"""History browser: searchable list + full-detail pane (complete text,
instruction/original for edits, metadata, copy/delete)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.storage.db import HistoryDb, HistoryEntry
from cleanwispr.storage.settings import Settings
from cleanwispr.ui import theme
from cleanwispr.ui.widgets import ACCENT_SOFT, LabeledToggle, intro_label

_PREVIEW_LEN = 160  # characters; wraps over two lines inside the card

_CARD_QSS = f"""
QFrame#historyCard {{
    background: {theme.SURFACE_2};
    border: 1px solid {theme.BORDER};
    border-radius: 8px;
}}
QFrame#historyCard:hover {{ border-color: {theme.MUTED}; }}
QFrame#historyCard[selected="true"] {{
    border: 1px solid {theme.ACCENT};
    background: rgba(124, 102, 220, 0.10);
}}
QLabel {{ background: transparent; border: none; }}
QLabel#cardKindDictation {{
    font-size: 9px; font-weight: 700; color: #3dd68c;
    background: rgba(48, 164, 108, 0.16); border-radius: 8px; padding: 2px 8px;
}}
QLabel#cardKindEdit {{
    font-size: 9px; font-weight: 700; color: {ACCENT_SOFT};
    background: rgba(124, 102, 220, 0.18); border-radius: 8px; padding: 2px 8px;
}}
QLabel#cardEdited {{
    font-size: 9px; font-weight: 700; color: #f0b429;
    background: rgba(240, 180, 41, 0.12); border-radius: 8px; padding: 2px 8px;
}}
QLabel#cardTime {{ font-size: 10px; color: {theme.MUTED}; }}
QLabel#cardPreview {{ font-size: 12px; color: {theme.TEXT}; }}
"""


class _HistoryCard(QFrame):
    """One history entry in the list: kind badge + timestamp on top,
    a wrapped two-line text preview below."""

    HEIGHT = 68

    def __init__(self, entry: HistoryEntry) -> None:
        super().__init__()
        self.setObjectName("historyCard")
        self.setStyleSheet(_CARD_QSS)
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        is_edit = entry.kind == "edit"
        kind = QLabel("EDIT" if is_edit else "DICTATION")
        kind.setObjectName("cardKindEdit" if is_edit else "cardKindDictation")
        top.addWidget(kind)
        self.edited_badge = QLabel("EDITED")
        self.edited_badge.setObjectName("cardEdited")
        self.edited_badge.setVisible(entry.edited_at is not None)
        top.addWidget(self.edited_badge)
        time_label = QLabel(entry.created_at)
        time_label.setObjectName("cardTime")
        top.addWidget(time_label)
        top.addStretch()
        layout.addLayout(top)

        preview = " ".join(entry.text.split())  # collapse newlines/runs of spaces
        if len(preview) > _PREVIEW_LEN:
            preview = preview[:_PREVIEW_LEN] + "…"
        preview_label = QLabel(preview or "(empty)")
        preview_label.setObjectName("cardPreview")
        preview_label.setWordWrap(True)
        preview_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(preview_label, 1)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class HistoryTab(QWidget):
    def __init__(self, settings: Settings, db: HistoryDb, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._settings = settings
        self._db = db
        self._on_change = on_change
        self._entries: list[HistoryEntry] = []
        self._current: HistoryEntry | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(intro_label(
            "Every dictation and edit is saved to a local database on this PC only — "
            "nothing is uploaded, and the AI model never reads this history. Select "
            "an entry to read or change its text; manual changes are marked as edited."
        ))
        self._enabled_toggle = LabeledToggle("Save dictations and edits to history")
        self._enabled_toggle.setToolTip(
            "Off: nothing new is written to history after it's pasted. Existing "
            "entries are kept until you delete them."
        )
        self._enabled_toggle.setChecked(settings.history.enabled)
        self._enabled_toggle.toggled.connect(self._enabled_changed)
        layout.addWidget(self._enabled_toggle)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search history…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.refresh)
        search_row.addWidget(self._search, 1)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #8a8f98;")
        search_row.addWidget(self._count_label)
        clear_button = QPushButton("Clear all")
        clear_button.setObjectName("danger")
        clear_button.clicked.connect(self._clear_all)
        search_row.addWidget(clear_button)
        layout.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # left: entry list (card per entry)
        self._list = QListWidget()
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setSpacing(3)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setMinimumWidth(240)
        self._list.setStyleSheet(
            "QListWidget { background: transparent; }"
            "QListWidget::item { border: none; background: transparent; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self._cards: list[_HistoryCard] = []
        self._list.currentRowChanged.connect(self._selection_changed)
        splitter.addWidget(self._list)

        # right: detail pane
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(8, 0, 0, 0)

        self._meta = QLabel(" ")
        self._meta.setStyleSheet("color: gray;")
        self._meta.setWordWrap(True)
        detail_layout.addWidget(self._meta)

        self._instruction_label = QLabel()
        self._instruction_label.setWordWrap(True)
        self._instruction_label.setVisible(False)
        detail_layout.addWidget(self._instruction_label)

        self._source_view = QPlainTextEdit()
        self._source_view.setReadOnly(True)
        self._source_view.setPlaceholderText("Original text")
        self._source_view.setVisible(False)
        self._source_view.setMaximumHeight(120)
        detail_layout.addWidget(self._source_view)

        self._text_view = QPlainTextEdit()
        self._text_view.setPlaceholderText("Select an entry to see the full text")
        self._text_view.textChanged.connect(self._text_edited)
        detail_layout.addWidget(self._text_view, 1)

        button_row = QHBoxLayout()
        self._save_button = QPushButton("Save changes")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._save_current)
        button_row.addWidget(self._save_button)
        self._copy_button = QPushButton("Copy text")
        self._copy_button.clicked.connect(self._copy_current)
        self._copy_button.setEnabled(False)
        button_row.addWidget(self._copy_button)
        self._delete_button = QPushButton("Delete entry")
        self._delete_button.clicked.connect(self._delete_current)
        self._delete_button.setEnabled(False)
        button_row.addWidget(self._delete_button)
        button_row.addStretch()
        detail_layout.addLayout(button_row)

        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

    def showEvent(self, event) -> None:  # Qt override
        super().showEvent(event)
        self.refresh()

    def _enabled_changed(self, checked: bool) -> None:
        self._settings.history.enabled = checked
        self._on_change()

    def refresh(self) -> None:
        selected_id = self._current.id if self._current else None
        self._entries = self._db.list(limit=500, search=self._search.text() or None)
        self._list.blockSignals(True)
        self._list.clear()
        self._cards = []
        restore_row = -1
        for row, entry in enumerate(self._entries):
            card = _HistoryCard(entry)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, _HistoryCard.HEIGHT + 2))
            self._list.addItem(item)
            self._list.setItemWidget(item, card)
            self._cards.append(card)
            if entry.id == selected_id:
                restore_row = row
        total = self._db.count()
        shown = len(self._entries)
        self._count_label.setText(
            f"{shown} of {total}" if shown != total else f"{total} entries"
        )
        if restore_row >= 0:
            self._list.setCurrentRow(restore_row)
            self._cards[restore_row].set_selected(True)
            self._list.blockSignals(False)
            # setCurrentRow was muted — always refresh the detail pane from the
            # re-read entry
            self._show_entry(self._entries[restore_row])
        else:
            self._list.blockSignals(False)
            self._show_entry(None)

    def _selection_changed(self, row: int) -> None:
        for index, card in enumerate(self._cards):
            card.set_selected(index == row)
        entry = self._entries[row] if 0 <= row < len(self._entries) else None
        self._show_entry(entry)

    def _show_entry(self, entry: HistoryEntry | None) -> None:
        self._current = entry
        self._copy_button.setEnabled(entry is not None)
        self._delete_button.setEnabled(entry is not None)
        self._save_button.setEnabled(False)
        self._text_view.blockSignals(True)
        if entry is None:
            self._meta.setText(" ")
            self._instruction_label.setVisible(False)
            self._source_view.setVisible(False)
            self._text_view.setPlainText("")
            self._text_view.blockSignals(False)
            return

        parts = [entry.created_at, entry.kind]
        if entry.edited_at:
            parts.append(f"manually edited {entry.edited_at}")
        if entry.language:
            parts.append(f"language: {entry.language}")
        if entry.engine:
            parts.append(entry.engine)
        if entry.llm_model:
            parts.append(entry.llm_model)
        if entry.duration_ms:
            parts.append(f"{entry.duration_ms / 1000:.1f}s audio")
        self._meta.setText("  ·  ".join(parts))

        is_edit = entry.kind == "edit"
        self._instruction_label.setVisible(is_edit and bool(entry.instruction))
        if entry.instruction:
            self._instruction_label.setText(f"<b>Instruction:</b> {entry.instruction}")
        self._source_view.setVisible(is_edit and bool(entry.source_text))
        self._source_view.setPlainText(entry.source_text or "")
        self._text_view.setPlainText(entry.text)
        self._text_view.blockSignals(False)

    def _text_edited(self) -> None:
        self._save_button.setEnabled(
            self._current is not None and self._text_view.toPlainText() != self._current.text
        )

    def _save_current(self) -> None:
        if self._current is None:
            return
        self._db.update_text(self._current.id, self._text_view.toPlainText())
        self.refresh()  # re-reads the entry; meta gains the edited stamp

    def _copy_current(self) -> None:
        if self._current:
            QGuiApplication.clipboard().setText(self._text_view.toPlainText())

    def _delete_current(self) -> None:
        if self._current:
            self._db.delete(self._current.id)
            self._current = None
            self.refresh()

    def _clear_all(self) -> None:
        total = self._db.count()
        if not total:
            return
        answer = QMessageBox.question(
            self,
            "Clear all history",
            f"Permanently delete all {total} history entries?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._db.clear()
            self._current = None
            self.refresh()  # counter updates to "0 entries"
