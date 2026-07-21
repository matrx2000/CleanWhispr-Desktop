"""NotesWindow — the notetaking view.

Multiple vaults (switchable), project folders, a WYSIWYG HTML editor with custom
text colours and rich tables, and a gated-shifter slider for voice input.
Left-slide dictates into the note; right-slide runs an AI take (edit the
selection / the whole note / generate); up-slide peeks the raw HTML; down-slide
undoes the last voice insert. Summoned by its own global hotkey; hides (never
quits) on close.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeySequence,
    QPixmap,
    QTextDocument,
    QTextDocumentFragment,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from cleanwispr import APP_NAME
from cleanwispr.core.controller import (
    NOTES_MODE_GENERATE,
    NOTES_MODE_SELECTION,
    AppState,
    Controller,
)
from cleanwispr.storage.settings import Settings
from cleanwispr.ui import theme
from cleanwispr.ui.notes import icons
from cleanwispr.ui.notes.editor import NoteEditor
from cleanwispr.ui.notes.slider import SlideMicToggle
from cleanwispr.ui.notes.table import TableDialog
from cleanwispr.ui.notes.vault import Note, VaultManager, reveal_in_file_manager

_ROLE = Qt.ItemDataRole.UserRole

_TOOLBAR_QSS = f"""
QToolBar#notesToolbar {{
    background: {theme.SURFACE};
    border: none;
    border-bottom: 1px solid {theme.BORDER};
    padding: 5px 8px;
    spacing: 2px;
}}
QToolBar#notesToolbar QToolButton {{
    color: {theme.TEXT};
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 5px 7px;
    margin: 0;
    font-weight: 600;
}}
QToolBar#notesToolbar QToolButton:hover {{ background: rgba(124, 102, 220, 0.16); }}
QToolBar#notesToolbar QToolButton:pressed {{ background: rgba(124, 102, 220, 0.30); }}
QToolBar#notesToolbar QToolButton::menu-indicator {{
    subcontrol-position: right center; subcontrol-origin: padding;
    right: 3px; width: 7px;
}}
QToolBar#notesToolbar::separator {{
    background: {theme.BORDER}; width: 1px; margin: 4px 7px;
}}
"""

# preset swatches for the text-colour menu
_PRESET_COLORS = [
    ("Default", None),
    ("Red", "#e5484d"),
    ("Orange", "#f5a524"),
    ("Yellow", "#f7d154"),
    ("Green", "#46a758"),
    ("Blue", "#3b82f6"),
    ("Purple", "#8b5cf6"),
    ("Gray", "#8a8f98"),
]


class NotesWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        controller: Controller,
        on_settings_changed: Callable[[], None],
    ) -> None:
        super().__init__()
        self._settings = settings
        self._controller = controller
        self._on_settings_changed = on_settings_changed
        self._vaults = VaultManager(settings, on_settings_changed)
        self._vault = self._vaults.active()
        self._current: Note | None = None
        self._loading = False
        self._undo_html: str | None = None
        self._ai_selection: tuple[int, int] | None = None

        self.setWindowTitle(f"{APP_NAME} — Notes")
        self.setMinimumSize(680, 460)
        self.resize(1040, 720)

        self._build_ui()
        self._connect_controller()
        self._reload_vault_combo()
        self._reload_tree()
        self._restore_last_note()

    # --- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_vault_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_editor_pane())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 780])
        outer.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_current)

    def _build_vault_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(QLabel("Vault:"))
        self._vault_combo = QComboBox()
        self._vault_combo.setMinimumWidth(200)
        self._vault_combo.currentIndexChanged.connect(self._on_vault_switched)
        layout.addWidget(self._vault_combo)

        add_vault = QPushButton("Add vault…")
        add_vault.clicked.connect(self._add_vault)
        layout.addWidget(add_vault)

        reveal = QPushButton("Open in file manager")
        reveal.clicked.connect(lambda: reveal_in_file_manager(self._vault.root))
        layout.addWidget(reveal)
        layout.addStretch()
        return bar

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 8, 8)
        layout.setSpacing(6)

        buttons = QHBoxLayout()
        new_note = QPushButton("+ Note")
        new_note.clicked.connect(self._new_note)
        new_project = QPushButton("+ Folder")
        new_project.clicked.connect(self._new_project)
        buttons.addWidget(new_note)
        buttons.addWidget(new_project)
        layout.addLayout(buttons)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter notes…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        layout.addWidget(self._tree, 1)
        return panel

    def _build_editor_pane(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._editor = NoteEditor(self._vault.root)
        self._editor.cursorPositionChanged.connect(self._sync_table_actions)
        self._raw = QPlainTextEdit()
        self._raw.setReadOnly(True)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._editor)
        self._stack.addWidget(self._raw)
        self._editor.textChanged.connect(self._on_text_changed)

        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._build_slider_bar())
        return panel

    def _build_toolbar(self) -> QToolBar:
        bar = QToolBar()
        bar.setObjectName("notesToolbar")
        bar.setMovable(False)
        bar.setIconSize(QSize(18, 18))
        bar.setStyleSheet(_TOOLBAR_QSS)
        e = self._editor

        def add(icon: QIcon, tip: str, slot) -> QAction:
            action = QAction(icon, "", self)
            action.setToolTip(tip)
            action.triggered.connect(slot)
            bar.addAction(action)
            return action

        add(icons.bold(), "Bold", e.toggle_bold)
        add(icons.italic(), "Italic", e.toggle_italic)
        add(icons.underline(), "Underline", e.toggle_underline)
        add(icons.code(), "Inline code", e.toggle_inline_code)
        bar.addSeparator()
        add(icons.heading(1), "Heading 1", lambda: e.set_heading(1))
        add(icons.heading(2), "Heading 2", lambda: e.set_heading(2))
        add(icons.heading(3), "Heading 3", lambda: e.set_heading(3))
        add(icons.paragraph(), "Body text", lambda: e.set_heading(0))
        bar.addSeparator()
        add(icons.bullet_list(), "Bullet list", e.insert_bullet_list)
        add(icons.numbered_list(), "Numbered list", e.insert_numbered_list)
        add(icons.checklist(), "Checklist item", e.insert_checklist)
        bar.addSeparator()
        bar.addWidget(self._build_color_button())
        add(icons.highlight(), "Highlight", self._pick_highlight)
        bar.addSeparator()
        bar.addWidget(self._build_table_button())

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent; border: none;")
        bar.addWidget(spacer)
        add(icons.export_markdown(), "Export note as Markdown…", self._export_markdown)
        return bar

    def _build_color_button(self) -> QToolButton:
        button = QToolButton()
        button.setIcon(icons.text_color())
        button.setToolTip("Text colour")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        for name, value in _PRESET_COLORS:
            action = QAction(name, self)
            if value is None:
                action.triggered.connect(self._editor.clear_text_color)
            else:
                action.setIcon(_swatch(value))
                action.triggered.connect(
                    lambda _c=False, v=value: self._editor.set_text_color(QColor(v))
                )
            menu.addAction(action)
        menu.addSeparator()
        custom = QAction("Custom…", self)
        custom.triggered.connect(self._pick_text_color)
        menu.addAction(custom)
        button.setMenu(menu)
        return button

    def _build_table_button(self) -> QToolButton:
        button = QToolButton()
        button.setIcon(icons.table())
        button.setText(" Table")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setToolTip("Insert or edit a table")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        menu.addAction(QAction("Insert table…", self, triggered=self._insert_table))
        menu.addSeparator()
        e = self._editor
        self._table_actions: list[QAction] = []

        def add(label: str, slot) -> None:
            action = QAction(label, self)
            action.triggered.connect(slot)
            menu.addAction(action)
            self._table_actions.append(action)

        add("Insert row above", e.table_insert_row_above)
        add("Insert row below", e.table_insert_row_below)
        add("Insert column left", e.table_insert_col_left)
        add("Insert column right", e.table_insert_col_right)
        menu.addSeparator()
        add("Move row up", e.table_move_row_up)
        add("Move row down", e.table_move_row_down)
        add("Move column left", e.table_move_col_left)
        add("Move column right", e.table_move_col_right)
        menu.addSeparator()
        add("Merge selected cells", e.table_merge)
        add("Split cell", e.table_split)
        menu.addSeparator()
        add("Delete row", e.table_delete_row)
        add("Delete column", e.table_delete_col)
        add("Delete table", e.table_delete)
        menu.addSeparator()
        add("Table properties…", self._table_properties)
        button.setMenu(menu)
        self._table_button = button
        return button

    def _build_slider_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 12)
        self._status = QLabel(
            "Slide left to dictate · right for AI (select text to edit it, "
            "or leave nothing selected to append)"
        )
        self._status.setStyleSheet(f"color: {theme.MUTED};")
        self._status.setWordWrap(True)

        self._slider = SlideMicToggle()
        self._slider.label_up = "Raw HTML"
        self._slider.label_down = "Undo"
        self._slider.slideLeft.connect(self._slide_dictate)
        self._slider.slideRight.connect(self._slide_ai)
        self._slider.slideUp.connect(self._toggle_raw)
        self._slider.slideDown.connect(self._undo_insert)
        self._slider.tapped.connect(self._slide_tap)

        layout.addWidget(self._status, 1)
        layout.addWidget(self._slider, 0, Qt.AlignmentFlag.AlignBottom)
        return bar

    def _connect_controller(self) -> None:
        c = self._controller
        c.notes_text_ready.connect(self._insert_dictation)
        c.notes_ai_ready.connect(self._apply_ai_result)
        c.state_changed.connect(self._on_state)
        c.edit_status.connect(self._status.setText)
        c.notice.connect(self._status.setText)
        c.error_occurred.connect(self._status.setText)

    # --- vaults ------------------------------------------------------------

    def _reload_vault_combo(self) -> None:
        self._vault_combo.blockSignals(True)
        self._vault_combo.clear()
        active = str(self._vaults.active_path())
        for path, label in self._vaults.display_names():
            self._vault_combo.addItem(label, path)
        index = self._vault_combo.findData(active)
        self._vault_combo.setCurrentIndex(max(0, index))
        self._vault_combo.blockSignals(False)

    def _on_vault_switched(self) -> None:
        path = self._vault_combo.currentData()
        if not path or path == str(self._vaults.active_path()):
            return
        self._save_current()
        self._vaults.set_active(path)
        self._vault = self._vaults.active()
        self._current = None
        self._editor.clear()
        self._reload_tree()

    def _add_vault(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a vault folder")
        if not folder:
            return
        self._save_current()
        self._vaults.add_vault(folder)
        self._vault = self._vaults.active()
        self._current = None
        self._editor.clear()
        self._reload_vault_combo()
        self._reload_tree()

    def reload_vault(self) -> None:
        """Called when vaults change in Settings → Notes."""
        self._vault = self._vaults.active()
        self._current = None
        self._editor.clear()
        self._reload_vault_combo()
        self._reload_tree()

    # --- tree / notes ------------------------------------------------------

    def _reload_tree(self, select: str | None = None) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()
        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        dir_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)

        for note in self._vault.list_notes(None):
            self._tree.addTopLevelItem(self._note_item(note, file_icon))
        for project in self._vault.projects():
            folder = QTreeWidgetItem([project])
            folder.setData(0, _ROLE, ("project", project))
            folder.setIcon(0, dir_icon)
            self._tree.addTopLevelItem(folder)
            for note in self._vault.list_notes(project):
                folder.addChild(self._note_item(note, file_icon))
            folder.setExpanded(True)
        self._tree.blockSignals(False)
        self._apply_filter(self._filter.text())
        if select:
            self._select_relpath(select)

    def _note_item(self, note: Note, icon: QIcon) -> QTreeWidgetItem:
        item = QTreeWidgetItem([note.title])
        item.setData(0, _ROLE, ("note", self._vault.relpath(note)))
        item.setIcon(0, icon)
        return item

    def _select_relpath(self, relpath: str) -> None:
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            item = it.value()
            data = item.data(0, _ROLE)
            if data and data[0] == "note" and data[1] == relpath:
                self._tree.setCurrentItem(item)
                return
            it += 1

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            item = it.value()
            data = item.data(0, _ROLE)
            if data and data[0] == "note":
                item.setHidden(needle not in item.text(0).lower())
            it += 1

    def _restore_last_note(self) -> None:
        last = self._settings.notes.last_note
        if last and self._vault.find(last):
            self._select_relpath(last)
        else:
            first = self._first_note_item()
            if first is not None:
                self._tree.setCurrentItem(first)
            elif not self._vault.list_notes(None) and not self._vault.projects():
                self._new_note()

    def _first_note_item(self) -> QTreeWidgetItem | None:
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            data = it.value().data(0, _ROLE)
            if data and data[0] == "note":
                return it.value()
            it += 1
        return None

    def _selected_project(self) -> str | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        role, value = item.data(0, _ROLE)
        if role == "project":
            return value
        return _project_of_relpath(value)

    def _new_note(self) -> None:
        note = self._vault.create("Untitled", project=self._selected_project())
        self._reload_tree(select=self._vault.relpath(note))
        self._editor.setFocus()

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name.strip():
            self._vault.create_project(name.strip())
            self._reload_tree()

    def _on_tree_selection(self, current: QTreeWidgetItem | None, _prev) -> None:
        self._save_current()
        if current is None:
            return
        role, value = current.data(0, _ROLE)
        if role != "note":
            return
        note = self._vault.find(value)
        if note is None:
            return
        self._load_note(note)

    def _load_note(self, note: Note) -> None:
        self._loading = True
        self._current = note
        self._editor.set_document_dir(self._vault.note_dir(note))
        text = self._vault.read(note)
        if note.is_markdown:
            self._editor.set_markdown(text)
        else:
            self._editor.set_html(text)
        self._undo_html = None
        self._show_editor()
        self._loading = False
        self._settings.notes.last_note = self._vault.relpath(note)
        self._on_settings_changed()

    def _on_text_changed(self) -> None:
        if not self._loading:
            self._save_timer.start()

    def _save_current(self) -> None:
        if self._current is None or self._loading:
            return
        saved = self._current
        with contextlib.suppress(OSError):
            saved = self._vault.save(self._current, self._editor.to_html())
        if saved.path != self._current.path:  # a legacy .md migrated to .html
            self._current = saved
            self._settings.notes.last_note = self._vault.relpath(saved)
            QTimer.singleShot(0, lambda: self._reload_tree(select=self._vault.relpath(saved)))

    # --- context menu ------------------------------------------------------

    def _tree_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        role, value = item.data(0, _ROLE)
        menu = QMenu(self)
        if role == "note":
            note = self._vault.find(value)
            if note is None:
                return
            menu.addAction("Rename…", lambda: self._rename_note(note))
            move_menu = menu.addMenu("Move to")
            move_menu.addAction("General (vault root)", lambda: self._move_note(note, None))
            for project in self._vault.projects():
                move_menu.addAction(project, lambda _c=False, p=project: self._move_note(note, p))
            menu.addAction("Reveal in file manager", lambda: reveal_in_file_manager(note.path))
            menu.addSeparator()
            menu.addAction("Delete", lambda: self._delete_note(note))
        else:  # project folder
            menu.addAction("New note here", lambda: self._new_note_in(value))
            menu.addAction("Rename…", lambda: self._rename_project(value))
            menu.addAction(
                "Reveal in file manager",
                lambda: reveal_in_file_manager(self._vault.root / value),
            )
            menu.addSeparator()
            menu.addAction("Delete folder", lambda: self._delete_project(value))
        menu.exec(self._tree.mapToGlobal(pos))

    def _new_note_in(self, project: str) -> None:
        note = self._vault.create("Untitled", project=project)
        self._reload_tree(select=self._vault.relpath(note))
        self._editor.setFocus()

    def _rename_note(self, note: Note) -> None:
        new_title, ok = QInputDialog.getText(
            self, "Rename note", "Title:", QLineEdit.EchoMode.Normal, note.title
        )
        if not ok or not new_title.strip():
            return
        self._save_current()
        renamed = self._vault.rename(note, new_title.strip())
        if self._current and self._current.path == note.path:
            self._current = renamed
        self._reload_tree(select=self._vault.relpath(renamed))

    def _move_note(self, note: Note, project: str | None) -> None:
        self._save_current()
        moved = self._vault.move(note, project)
        if self._current and self._current.path == note.path:
            self._current = moved
            self._editor.set_document_dir(self._vault.note_dir(moved))
        self._reload_tree(select=self._vault.relpath(moved))

    def _delete_note(self, note: Note) -> None:
        if QMessageBox.question(self, "Delete note", f"Delete “{note.title}”?") != (
            QMessageBox.StandardButton.Yes
        ):
            return
        was_current = self._current and self._current.path == note.path
        self._vault.delete(note)
        if was_current:
            self._current = None
            self._editor.clear()
        self._reload_tree()

    def _rename_project(self, project: str) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Rename folder", "Folder name:", QLineEdit.EchoMode.Normal, project
        )
        if ok and new_name.strip():
            self._save_current()
            self._current = None
            self._vault.rename_project(project, new_name.strip())
            self._reload_tree()

    def _delete_project(self, project: str) -> None:
        if QMessageBox.warning(
            self,
            "Delete folder",
            f"Delete the folder “{project}” and all notes inside it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._current = None
        self._editor.clear()
        self._vault.delete_project(project)
        self._reload_tree()

    # --- colours / tables / export ----------------------------------------

    def _pick_text_color(self) -> None:
        color = QColorDialog.getColor(parent=self, title="Text colour")
        if color.isValid():
            self._editor.set_text_color(color)

    def _pick_highlight(self) -> None:
        color = QColorDialog.getColor(parent=self, title="Highlight colour")
        if color.isValid():
            self._editor.set_highlight_color(color)

    def _insert_table(self) -> None:
        dialog = TableDialog(self)
        if dialog.exec():
            self._editor.insert_table(dialog.config())

    def _table_properties(self) -> None:
        config = self._editor.table_config()
        if config is None:
            return
        dialog = TableDialog(self, config=config, properties=True)
        if dialog.exec():
            new = dialog.config()
            new.rows, new.cols = config.rows, config.cols  # properties don't resize
            self._editor.apply_table_properties(new)

    def _sync_table_actions(self) -> None:
        in_table = self._editor.in_table()
        for action in getattr(self, "_table_actions", []):
            action.setEnabled(in_table)

    def _export_markdown(self) -> None:
        if self._current is None:
            return
        suggested = str(self._vault.root / f"{self._current.title}.md")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export as Markdown", suggested, "Markdown (*.md)"
        )
        if path:
            with contextlib.suppress(OSError), open(path, "w", encoding="utf-8") as fh:
                fh.write(self._editor.to_markdown())

    # --- slider actions ----------------------------------------------------

    def _slide_dictate(self) -> None:
        self._ensure_note()
        self._controller.toggle_notes_dictation()

    def _slide_ai(self) -> None:
        self._ensure_note()
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            # edit exactly the selected text \u2014 this is the only mode that replaces
            # existing content, and only within the selection (select-all to
            # transform the whole note on purpose)
            mode = NOTES_MODE_SELECTION
            # serialise the selection to Markdown so a selected TABLE reaches the
            # LLM as a real pipe table, not selectedText()'s U+FDD0-separated cells
            source = self._editor.selection_to_markdown()
            self._ai_selection = (cursor.selectionStart(), cursor.selectionEnd())
        else:
            # no selection: generate and INSERT at the cursor \u2014 never wipe the note
            mode = NOTES_MODE_GENERATE
            source = ""
            self._ai_selection = None
        self._controller.start_notes_ai(source, mode)

    def _slide_tap(self) -> None:
        if self._controller.state is AppState.RECORDING:
            self._controller.notes_finish()
        else:
            self._slide_dictate()

    def _toggle_raw(self) -> None:
        if self._stack.currentIndex() == 0:
            self._raw.setPlainText(self._editor.to_markdown())
            self._stack.setCurrentIndex(1)
            self._status.setText("Raw Markdown (read-only) — slide up again to return")
        else:
            self._show_editor()

    def _show_editor(self) -> None:
        self._stack.setCurrentIndex(0)

    def _undo_insert(self) -> None:
        if self._undo_html is None:
            self._status.setText("Nothing to undo")
            return
        self._editor.setHtml(self._undo_html)
        self._undo_html = None
        self._status.setText("Reverted the last voice insert")

    # --- controller results ------------------------------------------------

    def _ensure_note(self) -> None:
        if self._current is None:
            self._new_note()

    def _insert_dictation(self, text: str) -> None:
        self._show_editor()
        self._undo_html = self._editor.toHtml()
        cursor = self._editor.textCursor()
        cursor.insertText(text)
        self._editor.setTextCursor(cursor)
        self._editor.setFocus()

    def _apply_ai_result(self, payload) -> None:
        result, mode = payload
        self._show_editor()
        self._undo_html = self._editor.toHtml()
        if mode == NOTES_MODE_SELECTION and self._ai_selection is not None:
            # replace ONLY the selected range, nothing else
            start, end = self._ai_selection
            cursor = self._editor.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
            self._insert_markdown(cursor, result)
        else:
            # no selection: append the result at the end; existing notes untouched
            cursor = self._editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            if not self._editor.document().isEmpty():
                cursor.insertBlock()
            self._insert_markdown(cursor, result)
        self._ai_selection = None
        self._editor.setFocus()
        self._status.setText("Done — slide down to undo")

    def _insert_markdown(self, cursor, markdown: str) -> None:
        """Insert LLM output (Markdown) at `cursor`, rendered — replacing the
        cursor's selection if it has one, otherwise inserting in place."""
        doc = QTextDocument()
        doc.setMarkdown(markdown)
        cursor.insertFragment(QTextDocumentFragment(doc))
        self._editor.setTextCursor(cursor)

    _BUSY_STATES = (AppState.TRANSCRIBING, AppState.EDITING, AppState.INJECTING)

    def _on_state(self, state: AppState) -> None:
        self._slider.set_recording(state is AppState.RECORDING)
        self._slider.set_busy(state in self._BUSY_STATES)

    # --- window lifecycle --------------------------------------------------

    def keyPressEvent(self, event) -> None:  # Qt override
        if event.matches(QKeySequence.StandardKey.Close):
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # Qt override — hide, keep app in tray
        self._save_current()
        event.ignore()
        self.hide()


def _project_of_relpath(relpath: str) -> str | None:
    parts = relpath.split("/")
    return parts[0] if len(parts) > 1 else None


def _swatch(color: str) -> QIcon:
    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)
