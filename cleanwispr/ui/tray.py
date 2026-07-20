"""System tray icon: app status at a glance + main entry menu."""

from __future__ import annotations

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
        self._notes_action = QAction("Open Notes…")
        menu.addAction(self._notes_action)

        settings_action = QAction("Settings…")
        if on_open_settings is not None:
            settings_action.triggered.connect(on_open_settings)
        self._settings_action = settings_action
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("Quit")
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)

        # keep references — QMenu/QAction are not owned by the C++ tray object
        self._menu = menu
        self._actions = [settings_action, quit_action]
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_activated)
        controller.state_changed.connect(self._on_state_changed)

    def show(self) -> None:
        self._tray.show()

    def notify(self, message: str) -> None:
        self._tray.showMessage(APP_NAME, message)

    def set_open_settings(self, callback) -> None:
        self._settings_action.triggered.connect(callback)

    def set_open_notes(self, callback) -> None:
        self._notes_action.triggered.connect(callback)

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
