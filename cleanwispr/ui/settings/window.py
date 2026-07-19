"""Settings window — tabs per SPEC.md §6.

Implemented: Transcription (model manager), Audio, History, General.
Placeholders: Hotkeys (M2), Editor/LLM (M3).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QWidget,
)

from cleanwispr import APP_NAME, __version__, autostart, logging_setup
from cleanwispr.storage import paths
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.settings.audio_tab import AudioTab
from cleanwispr.ui.settings.editor_tab import EditorTab
from cleanwispr.ui.settings.history_tab import HistoryTab
from cleanwispr.ui.settings.hotkeys_tab import HotkeysTab
from cleanwispr.ui.settings.transcription_tab import TranscriptionTab


class SettingsWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        db: HistoryDb,
        on_settings_changed: Callable[[], None],
        on_hotkeys_changed: Callable[[], None],
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.resize(780, 560)

        # ordered by how a new user sets things up: engine → editor → triggers → mic
        tabs = QTabWidget()
        tabs.addTab(TranscriptionTab(settings, on_settings_changed), "Transcription")
        tabs.addTab(EditorTab(settings, on_settings_changed), "Voice Editor")
        tabs.addTab(
            HotkeysTab(settings, on_settings_changed, on_hotkeys_changed), "Hotkeys"
        )
        tabs.addTab(AudioTab(settings, on_settings_changed), "Microphone")
        self.history_tab = HistoryTab(db)
        tabs.addTab(self.history_tab, "History")
        tabs.addTab(self._general_tab(settings, on_settings_changed), "General")
        self.setCentralWidget(tabs)

    def _general_tab(self, settings: Settings, on_change: Callable[[], None]) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        sounds_check = QCheckBox("Play sounds when recording starts / text is pasted")
        sounds_check.setChecked(settings.ui.sounds_enabled)

        def toggle_sounds(checked: bool) -> None:
            settings.ui.sounds_enabled = checked
            on_change()

        sounds_check.toggled.connect(toggle_sounds)
        form.addRow(sounds_check)

        autostart_check = QCheckBox("Start CleanWispr when Windows starts")
        autostart_check.setChecked(settings.ui.start_on_login)

        def toggle_autostart(checked: bool) -> None:
            settings.ui.start_on_login = checked
            autostart.set_autostart(checked)
            on_change()

        autostart_check.toggled.connect(toggle_autostart)
        form.addRow(autostart_check)

        position_combo = QComboBox()
        for value, label in [
            ("bottom-right", "Bottom right"),
            ("bottom-left", "Bottom left"),
            ("bottom-center", "Bottom center"),
            ("top-right", "Top right"),
            ("top-left", "Top left"),
        ]:
            position_combo.addItem(label, value)
        index = position_combo.findData(settings.ui.overlay_position)
        position_combo.setCurrentIndex(max(0, index))

        def position_changed() -> None:
            settings.ui.overlay_position = position_combo.currentData()
            on_change()

        position_combo.currentIndexChanged.connect(position_changed)
        form.addRow("Overlay position:", position_combo)

        verbose_check = QCheckBox(
            "Verbose logging (show detailed activity in console and log file)"
        )
        verbose_check.setToolTip(
            "Off: only warnings and errors are shown. On: every step (recordings, "
            "server starts, requests) is logged — useful when reporting a problem."
        )
        verbose_check.setChecked(settings.ui.verbose_logging)

        def toggle_verbose(checked: bool) -> None:
            settings.ui.verbose_logging = checked
            logging_setup.set_verbose(checked)  # applies immediately, no restart
            on_change()

        verbose_check.toggled.connect(toggle_verbose)
        form.addRow(verbose_check)

        logs_button = QPushButton("Open log folder")
        logs_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir() / "logs")))
        )
        form.addRow(logs_button)

        form.addRow("Version:", QLabel(__version__))
        form.addRow("Config file:", QLabel(str(paths.config_file())))
        form.addRow("Data directory:", QLabel(str(paths.data_dir())))
        form.addRow("Model cache:", QLabel(str(paths.cache_dir() / "models")))
        attribution = QLabel(
            "CleanWispr builds on ideas and recipes from the MIT-licensed "
            '<a href="https://github.com/OpenWhispr/openwhispr">OpenWhispr</a> project.'
        )
        attribution.setOpenExternalLinks(True)
        attribution.setWordWrap(True)
        form.addRow(attribution)
        return widget

    def closeEvent(self, event) -> None:  # Qt override, keeps Qt naming
        # closing the window hides it; the app lives in the tray
        event.ignore()
        self.hide()
