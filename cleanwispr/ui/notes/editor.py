"""NoteEditor — a WYSIWYG rich-text surface stored as HTML.

Editing is visual (`QTextEdit` rich text): headings, lists, tables, inline
images, and custom text/highlight colours. The on-disk format is HTML (Qt's
``toHtml``/``setHtml`` round-trip everything the editor can express — colours,
styled tables, merged cells); Markdown import/export is available for
portability. Pasted images are written to an ``attachments/`` folder beside the
note and linked by the relative path, so a note stays self-contained.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontInfo,
    QImage,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextListFormat,
)
from PySide6.QtWidgets import QTextEdit

from cleanwispr.ui import theme
from cleanwispr.ui.notes import table as table_ops
from cleanwispr.ui.notes import vault as vault_mod
from cleanwispr.ui.widgets import ACCENT_SOFT

_MARKDOWN_FEATURES = (
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub
    if hasattr(QTextDocument, "MarkdownFeature")
    else QTextDocument.MarkdownDialectGitHub  # older PySide alias
)


def default_text_color() -> QColor:
    """Black on a light app background, white on a dark one."""
    bg = QColor(theme.BG)
    luminance = 0.2126 * bg.redF() + 0.7152 * bg.greenF() + 0.0722 * bg.blueF()
    return QColor("#111111") if luminance > 0.5 else QColor("#ffffff")


class NoteEditor(QTextEdit):
    """Visual editor whose document serialises to HTML + image attachments."""

    def __init__(self, document_dir: str | Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self._doc_dir = Path(document_dir) if document_dir else Path.cwd()
        self.setAcceptRichText(True)
        self.setObjectName("notesEditor")
        self.setPlaceholderText("Start writing, or slide the mic to dictate…")
        self.setTabChangesFocus(False)
        self._apply_theme()
        self.set_document_dir(self._doc_dir)

    # --- theming -----------------------------------------------------------

    def _apply_theme(self) -> None:
        txt = default_text_color().name()
        self.setStyleSheet(
            f"QTextEdit#notesEditor {{"
            f" background:{theme.SURFACE}; color:{txt}; border:none;"
            f" border-radius:8px; padding:6px 10px; font-size:14px;"
            f" selection-background-color:{theme.ACCENT}; selection-color:#ffffff; }}"
        )
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(txt))
        pal.setColor(QPalette.ColorRole.Base, QColor(theme.SURFACE))
        self.setPalette(pal)
        self.document().setDefaultStyleSheet(
            f"a {{ color:{ACCENT_SOFT}; }}"
            f"code {{ background:{theme.SURFACE_2}; }}"
            f"pre {{ background:{theme.SURFACE_2}; padding:8px; border-radius:6px; }}"
            f"blockquote {{ color:{theme.MUTED}; }}"
            f"th {{ background:{theme.SURFACE_2}; color:#ffffff; }}"
            f"th, td {{ border:1px solid {theme.BORDER}; padding:4px 9px; }}"
        )
        self.document().setDocumentMargin(16)

    # --- document location (attachments + relative image links) ------------

    def set_document_dir(self, path: str | Path) -> None:
        self._doc_dir = Path(path)
        self.document().setBaseUrl(QUrl.fromLocalFile(str(self._doc_dir) + "/"))

    # --- HTML / Markdown IO ------------------------------------------------

    def set_html(self, html: str) -> None:
        self.setHtml(html)

    def to_html(self) -> str:
        return self.toHtml()

    def set_markdown(self, text: str) -> None:
        self.setMarkdown(text)

    def to_markdown(self) -> str:
        return self.document().toMarkdown(_MARKDOWN_FEATURES)

    def selection_to_markdown(self) -> str:
        """Serialise the current selection to GitHub Markdown.

        Crucial for AI edits of a selected *table*: ``QTextCursor.selectedText()``
        encodes table cells with Qt's internal noncharacters (U+FDD0 between
        cells, U+FDD1 at the table end), which are meaningless to the LLM. Going
        via the fragment's HTML → Markdown yields a real pipe table instead.
        """
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return ""
        scratch = QTextDocument()
        scratch.setHtml(cursor.selection().toHtml())
        markdown = scratch.toMarkdown(_MARKDOWN_FEATURES).strip()
        # markdown keeps tables; plain text is a clean fallback that still
        # avoids the raw selectedText() table noncharacters (U+FDD0/U+FDD1)
        return markdown or scratch.toPlainText().strip()

    def selection_images(self, max_dim: int = 1024) -> list[str]:
        """Base64-encoded PNGs for the images inside the current selection, for a
        vision model. Empty when there is no selection or no images. Large images
        are downscaled so the request stays reasonable."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return []
        names = re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', cursor.selection().toHtml())
        out: list[str] = []
        for name in names:
            image = self.loadResource(QTextDocument.ResourceType.ImageResource, QUrl(name))
            if isinstance(image, QImage) and not image.isNull():
                out.append(_encode_image_base64(image, max_dim))
        return out

    # --- image paste / drop ------------------------------------------------

    def canInsertFromMimeData(self, source) -> bool:  # Qt override
        if source.hasImage() or self._image_urls(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # Qt override
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self._insert_image(_encode_png(image), "png")
                return
        for url in self._image_urls(source):
            path = url.toLocalFile()
            data = _read_bytes(path)
            if data:
                self._insert_image(data, path.rsplit(".", 1)[-1] or "png")
                return
        super().insertFromMimeData(source)

    @staticmethod
    def _image_urls(source) -> list[QUrl]:
        if not source.hasUrls():
            return []
        exts = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
        return [
            u
            for u in source.urls()
            if u.isLocalFile() and u.toLocalFile().lower().endswith(exts)
        ]

    def _insert_image(self, data: bytes, ext: str) -> None:
        rel = vault_mod.save_image(self._doc_dir, data, ext)
        cursor = self.textCursor()
        cursor.insertImage(rel)
        self.setTextCursor(cursor)

    def loadResource(self, type_: int, name: QUrl):  # Qt override
        if type_ == QTextDocument.ResourceType.ImageResource:
            url = name if isinstance(name, QUrl) else QUrl(str(name))
            local = self.document().baseUrl().resolved(url).toLocalFile()
            if local:
                image = QImage(local)
                if not image.isNull():
                    return image
        return super().loadResource(type_, name)

    # --- inline formatting -------------------------------------------------

    def toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        weight = self.fontWeight()
        fmt.setFontWeight(
            QFont.Weight.Normal if weight > QFont.Weight.Normal else QFont.Weight.Bold
        )
        self._merge_char_format(fmt)

    def toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.fontItalic())
        self._merge_char_format(fmt)

    def toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.fontUnderline())
        self._merge_char_format(fmt)

    def toggle_inline_code(self) -> None:
        fmt = QTextCharFormat()
        is_mono = self.currentCharFormat().fontFixedPitch()
        fmt.setFontFixedPitch(not is_mono)
        family = "monospace" if not is_mono else self.document().defaultFont().family()
        fmt.setFontFamilies([family])
        self._merge_char_format(fmt)

    def set_text_color(self, color: QColor) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._merge_char_format(fmt)

    def clear_text_color(self) -> None:
        self.set_text_color(default_text_color())

    def set_highlight_color(self, color: QColor) -> None:
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        self._merge_char_format(fmt)

    def clear_highlight(self) -> None:
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(0, 0, 0, 0))
        self._merge_char_format(fmt)

    def set_heading(self, level: int) -> None:
        """Make the current block a heading (level 1-3) or body text (level 0)."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        block_fmt = cursor.blockFormat()
        char_fmt = QTextCharFormat()
        block_fmt.setHeadingLevel(level)
        base_size = QFontInfo(self.document().defaultFont()).pointSizeF()
        if base_size <= 0:
            base_size = 11.0
        if level:
            sizes = {1: 1.8, 2: 1.5, 3: 1.25}
            char_fmt.setFontPointSize(base_size * sizes.get(level, 1.15))
            char_fmt.setFontWeight(QFont.Weight.Bold)
        else:
            char_fmt.setFontPointSize(base_size)
        cursor.setBlockFormat(block_fmt)
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.mergeCharFormat(char_fmt)
        cursor.endEditBlock()

    def insert_bullet_list(self) -> None:
        self.textCursor().createList(QTextListFormat.Style.ListDisc)

    def insert_numbered_list(self) -> None:
        self.textCursor().createList(QTextListFormat.Style.ListDecimal)

    def insert_checklist(self) -> None:
        cursor = self.textCursor()
        cursor.insertText("[ ] ")
        cursor.createList(QTextListFormat.Style.ListDisc)

    def _merge_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.mergeCurrentCharFormat(fmt)

    # --- tables (delegated to the table module) ----------------------------

    def in_table(self) -> bool:
        return table_ops.current_table(self) is not None

    def insert_table(self, config: table_ops.TableConfig) -> None:
        table_ops.insert_table(self, config)

    def table_config(self) -> table_ops.TableConfig | None:
        current = table_ops.current_table(self)
        return table_ops.read_config(current) if current is not None else None

    def apply_table_properties(self, config: table_ops.TableConfig) -> None:
        table_ops.apply_properties(self, config)

    def table_insert_row_above(self) -> None:
        table_ops.insert_row(self, below=False)

    def table_insert_row_below(self) -> None:
        table_ops.insert_row(self, below=True)

    def table_insert_col_left(self) -> None:
        table_ops.insert_column(self, right=False)

    def table_insert_col_right(self) -> None:
        table_ops.insert_column(self, right=True)

    def table_delete_row(self) -> None:
        table_ops.delete_row(self)

    def table_delete_col(self) -> None:
        table_ops.delete_column(self)

    def table_delete(self) -> None:
        table_ops.delete_table(self)

    def table_merge(self) -> None:
        table_ops.merge_cells(self)

    def table_split(self) -> None:
        table_ops.split_cell(self)

    def table_move_row_up(self) -> None:
        table_ops.move_row(self, -1)

    def table_move_row_down(self) -> None:
        table_ops.move_row(self, 1)

    def table_move_col_left(self) -> None:
        table_ops.move_column(self, -1)

    def table_move_col_right(self) -> None:
        table_ops.move_column(self, 1)


def _encode_png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _encode_image_base64(image: QImage, max_dim: int) -> str:
    """PNG → base64 for a vision LLM, downscaling to `max_dim` on the long edge."""
    if max_dim and (image.width() > max_dim or image.height() > max_dim):
        image = image.scaled(
            max_dim,
            max_dim,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return base64.b64encode(_encode_png(image)).decode("ascii")


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""
