"""History browser: searchable list + full-detail pane (complete text,
instruction/original for edits, metadata, copy/delete)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.storage.db import HistoryDb, HistoryEntry
from cleanwispr.ui.widgets import intro_label

_PREVIEW_LEN = 80


class HistoryTab(QWidget):
    def __init__(self, db: HistoryDb) -> None:
        super().__init__()
        self._db = db
        self._entries: list[HistoryEntry] = []
        self._current: HistoryEntry | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(intro_label(
            "Every dictation and edit is saved to a local database on this PC only — "
            "nothing is uploaded, and the AI model never reads this history. Select "
            "an entry to read or change its text; manual changes are marked as edited."
        ))
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

        # left: entry list
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Time", "Kind", "Text"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnWidth(0, 130)
        self._table.setColumnWidth(1, 74)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.itemSelectionChanged.connect(self._selection_changed)
        splitter.addWidget(self._table)

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

    def refresh(self) -> None:
        selected_id = self._current.id if self._current else None
        self._entries = self._db.list(limit=500, search=self._search.text() or None)
        self._table.setRowCount(len(self._entries))
        restore_row = -1
        for row, entry in enumerate(self._entries):
            preview = entry.text.replace("\n", " ")
            if len(preview) > _PREVIEW_LEN:
                preview = preview[:_PREVIEW_LEN] + "…"
            time_text = f"{entry.created_at} (edited)" if entry.edited_at else entry.created_at
            self._table.setItem(row, 0, QTableWidgetItem(time_text))
            kind_item = QTableWidgetItem("edit" if entry.kind == "edit" else "dictate")
            kind_item.setForeground(
                QColor("#b3a7f0") if entry.kind == "edit" else QColor("#30a46c")
            )
            self._table.setItem(row, 1, kind_item)
            self._table.setItem(row, 2, QTableWidgetItem(preview))
            if entry.id == selected_id:
                restore_row = row
        total = self._db.count()
        shown = len(self._entries)
        self._count_label.setText(
            f"{shown} of {total}" if shown != total else f"{total} entries"
        )
        if restore_row >= 0:
            self._table.selectRow(restore_row)
            # selectRow is a no-op signal-wise if the row was already selected —
            # always refresh the detail pane from the re-read entry
            self._show_entry(self._entries[restore_row])
        else:
            self._show_entry(None)

    def _selection_changed(self) -> None:
        rows = {index.row() for index in self._table.selectedIndexes()}
        entry = self._entries[next(iter(rows))] if rows else None
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
