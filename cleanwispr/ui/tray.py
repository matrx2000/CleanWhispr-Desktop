"""System tray icon: app status at a glance + main entry menu."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from cleanwispr import APP_NAME
from cleanwispr.core.controller import AppState, Controller
from cleanwispr.ui.icons import state_icon

_STATE_LABELS = {
    AppState.IDLE: "Idle",
    AppState.RECORDING: "Recording…",
    AppState.TRANSCRIBING: "Transcribing…",
    AppState.EDITING: "Applying edit…",
    AppState.INJECTING: "Pasting…",
    AppState.ERROR: "Error",
}


class TrayManager:
    def __init__(self, controller: Controller, on_open_settings) -> None:
        self._controller = controller
        self._tray = QSystemTrayIcon(state_icon(AppState.IDLE))
        self._tray.setToolTip(f"{APP_NAME} — Idle")

        menu = QMenu()
        self._dictation_action = QAction("Start dictation")
        self._dictation_action.triggered.connect(controller.toggle_dictation)
        menu.addAction(self._dictation_action)

        self._editor_action = QAction("Start voice editor")
        self._editor_action.triggered.connect(controller.toggle_editor)
        menu.addAction(self._editor_action)

        menu.addSeparator()
        # Skills submenu is populated later via set_skills() (kept empty/hidden
        # until the host wires a library); insert it before Notes for a stable order
        self._skills_menu = QMenu("Skills")
        self._skills_menu.menuAction().setVisible(False)
        menu.addMenu(self._skills_menu)
        self._skills = None
        self._on_quick_switch: Callable[[], None] | None = None
        self._on_manage_skills: Callable[[], None] | None = None

        self._notes_action = QAction("Open Notes…")
        menu.addAction(self._notes_action)

        settings_action = QAction("Settings…")
        if on_open_settings is not None:
            settings_action.triggered.connect(on_open_settings)
        self._settings_action = settings_action
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("Quit")
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        self._on_quit: Callable[[], None] | None = None

        # keep references — QMenu/QAction are not owned by the C++ tray object
        self._menu = menu
        self._actions = [settings_action, quit_action]
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_activated)
        controller.state_changed.connect(self._on_state_changed)

    def set_on_quit(self, callback: Callable[[], None]) -> None:
        """Host-provided quit routine (hide windows, exit the loop). The
        fallback is plain QApplication.quit()."""
        self._on_quit = callback

    def _quit(self) -> None:
        if self._on_quit is not None:
            self._on_quit()
        else:
            QApplication.instance().quit()

    def show(self) -> None:
        self._tray.show()

    def notify(self, message: str) -> None:
        self._tray.showMessage(APP_NAME, message)

    def set_open_settings(self, callback) -> None:
        self._settings_action.triggered.connect(callback)

    def set_open_notes(self, callback) -> None:
        self._notes_action.triggered.connect(callback)

    def set_skills(
        self,
        library,
        bridge,
        on_quick_switch: Callable[[], None],
        on_manage_skills: Callable[[], None],
    ) -> None:
        """Wire the Skills submenu to a skillkit library. `bridge.changed` keeps
        the active checkmarks in sync when skills change from voice or the UI."""
        self._skills = library
        self._on_quick_switch = on_quick_switch
        self._on_manage_skills = on_manage_skills
        self._skills_menu.menuAction().setVisible(True)
        bridge.changed.connect(self._rebuild_skills_menu)
        self._rebuild_skills_menu()

    def _rebuild_skills_menu(self) -> None:
        menu = self._skills_menu
        menu.clear()
        library = self._skills
        if library is None:
            return

        enable = QAction("Enable skills", menu)
        enable.setCheckable(True)
        enable.setChecked(library.enabled)
        enable.toggled.connect(library.set_enabled)
        menu.addAction(enable)
        menu.addSeparator()

        if library.enabled:
            active_ids = {s.id for s in library.active_skills()}
            skills = library.enabled_skills()
            if skills:
                for skill in skills:
                    action = QAction(skill.name, menu)
                    action.setCheckable(True)
                    action.setChecked(skill.id in active_ids)
                    action.toggled.connect(partial(self._set_skill_active, skill.id))
                    menu.addAction(action)
            else:
                empty = QAction("No skills yet — add one below", menu)
                empty.setEnabled(False)
                menu.addAction(empty)
            menu.addSeparator()
            if self._on_quick_switch is not None:
                quick = QAction("Quick switch…", menu)
                quick.triggered.connect(lambda: self._on_quick_switch())
                menu.addAction(quick)

        if self._on_manage_skills is not None:
            manage = QAction("Manage skills…", menu)
            manage.triggered.connect(lambda: self._on_manage_skills())
            menu.addAction(manage)

    def _set_skill_active(self, skill_id: str, active: bool) -> None:
        if self._skills is None:
            return
        if active:
            self._skills.activate(skill_id)
        else:
            self._skills.deactivate(skill_id)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # left-click toggles dictation (context menu handles the rest)
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._controller.toggle_dictation()

    def _on_state_changed(self, state: AppState) -> None:
        self._tray.setIcon(state_icon(state))
        self._tray.setToolTip(f"{APP_NAME} — {_STATE_LABELS[state]}")
        recording = state is AppState.RECORDING
        self._dictation_action.setText("Stop recording" if recording else "Start dictation")
        self._editor_action.setEnabled(not recording)
