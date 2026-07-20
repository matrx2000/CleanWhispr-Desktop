"""Notes settings: manage the note vaults (folders of notes)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.storage.settings import Settings
from cleanwispr.ui.notes.vault import VaultManager, reveal_in_file_manager
from cleanwispr.ui.widgets import intro_label


class NotesTab(QWidget):
    def __init__(
        self,
        settings: Settings,
        on_settings_changed: Callable[[], None],
        on_notes_dir_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._on_change = on_settings_changed
        self._on_vaults_changed = on_notes_dir_changed
        self._vaults = VaultManager(settings, on_settings_changed)

        layout = QVBoxLayout(self)
        layout.addWidget(
            intro_label(
                "A vault is a folder of notes. Add as many as you like and switch "
                "between them from the Notes window. Notes are portable HTML files; "
                "images and per-folder attachments live beside them, so a whole vault "
                "can be moved, synced, or backed up as one folder."
            )
        )
        layout.addWidget(QLabel("Vaults (the active one is shown in bold):"))

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _i: self._make_active())
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        add = QPushButton("Add vault…")
        add.clicked.connect(self._add)
        self._active_btn = QPushButton("Make active")
        self._active_btn.clicked.connect(self._make_active)
        self._open_btn = QPushButton("Open in file manager")
        self._open_btn.clicked.connect(self._open)
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setObjectName("danger")
        self._remove_btn.clicked.connect(self._remove)
        for widget in (add, self._active_btn, self._open_btn, self._remove_btn):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)

        self._refresh()

    def _refresh(self) -> None:
        self._list.clear()
        active = str(self._vaults.active_path())
        for path, label in self._vaults.display_names():
            text = f"★  {label}" if path == active else f"    {label}"
            item = QListWidgetItem(text)
            item.setData(1000, path)
            font = item.font()
            font.setBold(path == active)
            item.setFont(font)
            self._list.addItem(item)
        self._remove_btn.setEnabled(self._list.count() > 1)

    def _selected_path(self) -> str | None:
        item = self._list.currentItem()
        return item.data(1000) if item else None

    def _notify_window(self) -> None:
        if self._on_vaults_changed:
            self._on_vaults_changed()

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a vault folder")
        if not folder:
            return
        self._vaults.add_vault(folder)
        self._refresh()
        self._notify_window()

    def _make_active(self) -> None:
        path = self._selected_path()
        if path:
            self._vaults.set_active(path)
            self._refresh()
            self._notify_window()

    def _open(self) -> None:
        path = self._selected_path()
        if path:
            reveal_in_file_manager(path)

    def _remove(self) -> None:
        path = self._selected_path()
        if path:
            self._vaults.remove_vault(path)
            self._refresh()
            self._notify_window()
