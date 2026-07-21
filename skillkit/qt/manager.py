"""SkillsManager — an embeddable editor for a SkillLibrary.

Drop it into a settings tab or show it in its own window. Provides: the master
on/off + voice toggle, a list with add / duplicate / delete, and a full editor
(name, description, persona body, voice triggers, scope, per-skill temperature
and model overrides, enable, activate, and an optional Test button).

Built-in skills are read-only — the user duplicates one to edit it. Persistence
is automatic (the library saves through its store); this widget never touches a
config file. Standard Qt widgets only, so it inherits the host's theme.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from skillkit.library import SkillLibrary
from skillkit.models import SCOPE_BOTH, SCOPE_EDITOR, SCOPE_NOTES, Skill

_SCOPE_LABELS = [
    ("Voice editor & Notes", SCOPE_BOTH),
    ("Voice editor only", SCOPE_EDITOR),
    ("Notes only", SCOPE_NOTES),
]


class SkillsManager(QWidget):
    """`model_choices` (optional) supplies model ids for the override combo.
    `on_test` (optional) receives the working Skill to run a quick trial; hide
    the Test button by leaving it None."""

    create_new_clicked = Signal()

    def __init__(
        self,
        library: SkillLibrary,
        *,
        changed_signal=None,
        model_choices: Callable[[], list[str]] | None = None,
        on_test: Callable[[Skill], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._model_choices = model_choices
        self._on_test = on_test
        self._current: Skill | None = None
        self._loading = False

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(self._master_group())

        split = QHBoxLayout()
        split.addWidget(self._list_panel(), 0)
        split.addWidget(self._editor_panel(), 1)
        root.addLayout(split, 1)

        if changed_signal is not None:
            changed_signal.connect(self._on_external_change)
        self._reload_list()
        self._sync_master()

    # --- master group ---

    def _master_group(self) -> QGroupBox:
        group = QGroupBox("Skills")
        layout = QVBoxLayout(group)
        hint = QLabel(
            "A skill is a reusable role that flavours the voice editor's output "
            "(tone and voice only — it can't override the app's formatting rules). "
            "Activate one or more; they stack."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._enable_check = QCheckBox("Enable skills")
        self._enable_check.toggled.connect(self._toggle_master)
        layout.addWidget(self._enable_check)

        self._voice_check = QCheckBox(
            "Allow voice switching (say “switch to <skill>”, “plain” to clear)"
        )
        self._voice_check.toggled.connect(self._toggle_voice)
        layout.addWidget(self._voice_check)
        return group

    def _toggle_master(self, checked: bool) -> None:
        if not self._loading:
            self._library.set_enabled(checked)
        self._sync_master()

    def _toggle_voice(self, checked: bool) -> None:
        if not self._loading:
            self._library.set_voice_switching(checked)

    def _sync_master(self) -> None:
        self._loading = True
        self._enable_check.setChecked(self._library.enabled)
        self._voice_check.setChecked(self._library.config.voice_switching)
        self._loading = False
        enabled = self._library.enabled
        self._voice_check.setEnabled(enabled)
        self._list.setEnabled(enabled)
        self._editor.setEnabled(enabled and self._current is not None)

    # --- list panel ---

    def _list_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(210)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        buttons = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        buttons.addWidget(add)
        self._dup_button = QPushButton("Duplicate")
        self._dup_button.clicked.connect(self._duplicate)
        buttons.addWidget(self._dup_button)
        self._del_button = QPushButton("Delete")
        self._del_button.clicked.connect(self._delete)
        buttons.addWidget(self._del_button)
        layout.addLayout(buttons)
        return panel

    def _reload_list(self) -> None:
        selected = self._current.id if self._current else None
        self._loading = True
        self._list.clear()
        active_ids = {s.id for s in self._library.active_skills()}
        target_row = 0
        for i, skill in enumerate(self._library.all()):
            tags = []
            if skill.id in active_ids:
                tags.append("active")
            if not skill.enabled:
                tags.append("off")
            if skill.builtin:
                tags.append("built-in")
            suffix = f"   ({', '.join(tags)})" if tags else ""
            item = QListWidgetItem(f"{skill.name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, skill.id)
            self._list.addItem(item)
            if skill.id == selected:
                target_row = i
        self._loading = False
        if self._list.count():
            self._list.setCurrentRow(target_row)
        else:
            self._current = None
            self._load_editor(None)

    def _on_selection_changed(self, item: QListWidgetItem | None) -> None:
        if self._loading:
            return
        self._commit()
        skill = self._library.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self._current = skill.copy() if skill else None
        self._load_editor(self._current)

    # --- editor panel ---

    def _editor_panel(self) -> QWidget:
        self._editor = QGroupBox("Edit skill")
        form = QFormLayout(self._editor)

        self._builtin_note = QLabel("Built-in skill — Duplicate it to make an editable copy.")
        self._builtin_note.setWordWrap(True)
        self._builtin_note.setVisible(False)
        form.addRow(self._builtin_note)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._commit)
        form.addRow("Name:", self._name)

        self._description = QLineEdit()
        self._description.editingFinished.connect(self._commit)
        form.addRow("Description:", self._description)

        self._body = QPlainTextEdit()
        self._body.setPlaceholderText(
            "You are a witty poet who favours vivid imagery…  (tone & voice only)"
        )
        self._body.setMinimumHeight(120)
        form.addRow("Instruction:", self._body)

        self._triggers = QLineEdit()
        self._triggers.setPlaceholderText("poet, poetic, poyet   (spoken names & known mishears)")
        self._triggers.editingFinished.connect(self._commit)
        form.addRow("Voice triggers:", self._triggers)

        self._scope = QComboBox()
        for label, value in _SCOPE_LABELS:
            self._scope.addItem(label, value)
        self._scope.currentIndexChanged.connect(self._commit_if_ready)
        form.addRow("Applies to:", self._scope)

        temp_row = QHBoxLayout()
        self._temp_override = QCheckBox("Override")
        self._temp_override.toggled.connect(self._on_temp_toggle)
        temp_row.addWidget(self._temp_override)
        self._temp = QDoubleSpinBox()
        self._temp.setRange(0.0, 2.0)
        self._temp.setSingleStep(0.1)
        self._temp.setValue(0.2)
        self._temp.valueChanged.connect(self._commit_if_ready)
        temp_row.addWidget(self._temp, 1)
        form.addRow("Temperature:", temp_row)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._model.lineEdit().setPlaceholderText("(inherit the app's model)")
        self._model.currentTextChanged.connect(self._commit_if_ready)
        form.addRow("Model override:", self._model)

        toggles = QHBoxLayout()
        self._enabled = QCheckBox("Enabled")
        self._enabled.toggled.connect(self._on_enabled_toggle)
        toggles.addWidget(self._enabled)
        self._active = QCheckBox("Active now")
        self._active.toggled.connect(self._on_active_toggle)
        toggles.addWidget(self._active)
        toggles.addStretch()
        form.addRow(toggles)

        self._test_button = QPushButton("Test skill")
        self._test_button.clicked.connect(self._test)
        self._test_button.setVisible(self._on_test is not None)
        form.addRow(self._test_button)
        return self._editor

    def _load_editor(self, skill: Skill | None) -> None:
        self._loading = True
        editable = skill is not None and not skill.builtin
        for widget in (
            self._name, self._description, self._body, self._triggers, self._scope,
            self._temp_override, self._temp, self._model, self._enabled,
        ):
            widget.setEnabled(editable)
        self._active.setEnabled(skill is not None and skill.enabled)
        self._builtin_note.setVisible(bool(skill and skill.builtin))
        self._dup_button.setEnabled(skill is not None)
        self._del_button.setEnabled(skill is not None and not skill.builtin)
        self._test_button.setEnabled(skill is not None and self._on_test is not None)

        if skill is None:
            self._name.clear()
            self._description.clear()
            self._body.setPlainText("")
            self._triggers.clear()
            self._loading = False
            self._editor.setEnabled(self._library.enabled)
            return

        self._name.setText(skill.name)
        self._description.setText(skill.description)
        self._body.setPlainText(skill.body)
        self._triggers.setText(", ".join(skill.triggers))
        self._scope.setCurrentIndex(max(0, self._scope.findData(skill.scope)))
        self._temp_override.setChecked(skill.temperature is not None)
        self._temp.setValue(skill.temperature if skill.temperature is not None else 0.2)
        self._temp.setEnabled(editable and skill.temperature is not None)
        self._populate_models(skill.model or "")
        self._enabled.setChecked(skill.enabled)
        self._active.setChecked(self._library.is_active(skill.id))
        self._loading = False
        self._editor.setEnabled(self._library.enabled)

    def _populate_models(self, current: str) -> None:
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItem("")  # inherit
        if self._model_choices is not None:
            try:
                for model_id in self._model_choices():
                    self._model.addItem(model_id)
            except Exception:  # a slow/broken provider must not break the editor
                pass
        if current and self._model.findText(current) < 0:
            self._model.addItem(current)
        self._model.setCurrentText(current)
        self._model.blockSignals(False)

    # --- committing edits back to the library ---

    def _commit_if_ready(self, *_args) -> None:
        if not self._loading:
            self._commit()

    def _pull(self) -> None:
        if self._current is None:
            return
        self._current.name = self._name.text().strip() or self._current.name
        self._current.description = self._description.text().strip()
        self._current.body = self._body.toPlainText().strip()
        self._current.triggers = [
            t.strip() for t in self._triggers.text().split(",") if t.strip()
        ]
        self._current.scope = self._scope.currentData() or SCOPE_BOTH
        self._current.temperature = (
            round(self._temp.value(), 2) if self._temp_override.isChecked() else None
        )
        self._current.model = self._model.currentText().strip() or None

    def _commit(self) -> None:
        if self._loading or self._current is None or self._current.builtin:
            return
        before = self._current.to_dict()
        self._pull()
        if self._current.to_dict() != before:
            self._library.update(self._current)
            self._refresh_current_row_label()

    def _refresh_current_row_label(self) -> None:
        # keep the list name in sync without a full rebuild (avoids selection churn)
        item = self._list.currentItem()
        if item and self._current:
            active = self._library.is_active(self._current.id)
            tags = []
            if active:
                tags.append("active")
            if not self._current.enabled:
                tags.append("off")
            suffix = f"   ({', '.join(tags)})" if tags else ""
            self._loading = True
            item.setText(f"{self._current.name}{suffix}")
            self._loading = False

    def _on_temp_toggle(self, checked: bool) -> None:
        self._temp.setEnabled(checked and not self._loading)
        self._commit_if_ready()

    def _on_enabled_toggle(self, checked: bool) -> None:
        if self._loading or self._current is None:
            return
        self._current.enabled = checked
        self._library.set_skill_enabled(self._current.id, checked)
        self._active.setEnabled(checked)
        self._refresh_current_row_label()

    def _on_active_toggle(self, checked: bool) -> None:
        if self._loading or self._current is None:
            return
        if checked:
            self._library.activate(self._current.id)
        else:
            self._library.deactivate(self._current.id)
        self._refresh_current_row_label()

    # --- list actions ---

    def _add(self) -> None:
        self._commit()
        skill = self._library.create(name="New skill", description="", body="")
        self._select_id(skill.id)

    def _duplicate(self) -> None:
        if self._current is None:
            return
        self._commit()
        clone = self._library.duplicate(self._current.id)
        if clone is not None:
            self._select_id(clone.id)

    def _delete(self) -> None:
        if self._current is None or self._current.builtin:
            return
        self._library.remove(self._current.id)
        self._current = None
        self._reload_list()

    def _select_id(self, skill_id: str) -> None:
        self._reload_list()
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == skill_id:
                self._list.setCurrentRow(row)
                break

    def _test(self) -> None:
        if self._on_test is None or self._current is None:
            return
        self._commit()
        self._on_test(self._current.copy())

    # --- external changes (voice switch, palette toggles) ---

    def _on_external_change(self) -> None:
        # refresh markers/active state without disturbing in-progress typing
        self._sync_master()
        if self._current is not None and self._library.get(self._current.id) is None:
            self._current = None
            self._reload_list()
            return
        self._loading = True
        active_ids = {s.id for s in self._library.active_skills()}
        for row in range(self._list.count()):
            item = self._list.item(row)
            skill = self._library.get(item.data(Qt.ItemDataRole.UserRole))
            if skill is None:
                continue
            tags = []
            if skill.id in active_ids:
                tags.append("active")
            if not skill.enabled:
                tags.append("off")
            if skill.builtin:
                tags.append("built-in")
            suffix = f"   ({', '.join(tags)})" if tags else ""
            item.setText(f"{skill.name}{suffix}")
        self._loading = False
        if self._current is not None:
            self._active.blockSignals(True)
            self._active.setChecked(self._library.is_active(self._current.id))
            self._active.blockSignals(False)

    def hideEvent(self, event) -> None:  # Qt override
        self._commit()
        super().hideEvent(event)
