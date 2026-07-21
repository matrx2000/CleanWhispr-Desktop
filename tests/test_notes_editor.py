"""NoteEditor: HTML/Markdown IO, theme-correct colours, image attachments."""

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument

from cleanwispr.ui.notes import vault as vault_mod
from cleanwispr.ui.notes.editor import NoteEditor, default_text_color


def _editor(qtbot, tmp_path):
    editor = NoteEditor(tmp_path)
    qtbot.addWidget(editor)
    return editor


def test_markdown_roundtrip_structure(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.set_markdown("# Title\n\nbody text\n\n- one\n- two\n")
    out = editor.to_markdown()
    assert "# Title" in out
    assert "- one" in out and "- two" in out
    assert "body text" in out


def test_html_roundtrip(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.set_html("<h1>Heading</h1><p>paragraph</p>")
    html = editor.to_html()
    assert "Heading" in html and "paragraph" in html
    # reloading the produced HTML keeps the text
    editor.set_html(html)
    assert "paragraph" in editor.toPlainText()


def test_default_text_color_is_high_contrast(qtbot, tmp_path):
    # the app theme is dark, so the default note text must be light (not purple)
    color = default_text_color()
    assert color.lightness() > 200  # near-white


def test_custom_text_color_persists_in_html(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.setPlainText("colour me")
    editor.selectAll()
    editor.set_text_color(QColor("#3b82f6"))
    html = editor.to_html().lower()
    assert "3b82f6" in html or "color" in html  # explicit colour survives to HTML


def test_image_paste_saves_attachment_and_links_it(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    image = QImage(6, 6, QImage.Format.Format_RGB32)
    image.fill(0xFF3366)
    mime = QMimeData()
    mime.setImageData(image)

    editor.insertFromMimeData(mime)

    attachments = list((tmp_path / "attachments").glob("*.png"))
    assert len(attachments) == 1
    assert "attachments/" in editor.to_html()


def test_selection_to_markdown_table_has_no_noncharacters(qtbot, tmp_path):
    # regression: a selected table used to reach the LLM as selectedText()'s
    # U+FDD0/U+FDD1 cell separators; it must now serialise to a real pipe table
    editor = _editor(qtbot, tmp_path)
    editor.set_markdown("| Name | Score |\n| --- | --- |\n| Xylos | 8 |\n| Aetheria | 6 |\n")
    editor.selectAll()

    md = editor.selection_to_markdown()

    assert chr(0xFDD0) not in md and chr(0xFDD1) not in md  # no Qt cell noncharacters
    assert "|" in md and "---" in md  # a real Markdown pipe table
    assert "Xylos" in md and "Aetheria" in md
    # and it re-renders back into a table (round-trips through setMarkdown)
    editor.set_markdown(md)
    assert editor.document().toPlainText().count("Xylos") == 1


def test_selection_to_markdown_empty_without_selection(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.setPlainText("hello")
    assert editor.selection_to_markdown() == ""


def test_image_link_renders_from_disk(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(0x00FF00)
    img.save(str(vault_mod.attachments_dir(tmp_path) / "pic.png"))

    editor.set_document_dir(tmp_path)
    resource = editor.loadResource(
        QTextDocument.ResourceType.ImageResource, QUrl("attachments/pic.png")
    )
    assert isinstance(resource, QImage)
    assert not resource.isNull()
