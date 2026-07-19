"""Settings window — tabs per SPEC.md §6.

Tabs: Transcription, Voice Editor, Hotkeys, Microphone, History, General, About.
Every tab scrolls, and the window shrinks down to small laptop screens.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cleanwispr import APP_NAME, __version__, autostart, logging_setup
from cleanwispr.storage import paths
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from cleanwispr.ui import theme
from cleanwispr.ui.settings.audio_tab import AudioTab
from cleanwispr.ui.settings.editor_tab import EditorTab
from cleanwispr.ui.settings.history_tab import HistoryTab
from cleanwispr.ui.settings.hotkeys_tab import HotkeysTab
from cleanwispr.ui.settings.transcription_tab import TranscriptionTab
from cleanwispr.ui.widgets import ACCENT_SOFT, LabeledToggle, PathLink

AUTHOR = "matrx2000"

# name, license, url — every entry verified against the live source
# (GitHub API license field / HTTP 200 on 2026-07-19)
_CREDITS: list[tuple[str, str, str]] = [
    ("OpenWhispr (reference project)", "MIT", "https://github.com/OpenWhispr/openwhispr"),
    ("whisper.cpp", "MIT", "https://github.com/ggml-org/whisper.cpp"),
    ("sherpa-onnx", "Apache-2.0", "https://github.com/k2-fsa/sherpa-onnx"),
    (
        "NVIDIA Parakeet models", "CC-BY-4.0",
        "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3",
    ),
    ("Ollama", "MIT", "https://github.com/ollama/ollama"),
    ("Qt for Python (PySide6)", "LGPL-3.0", "https://doc.qt.io/qtforpython-6/"),
    ("qt-material", "BSD-2-Clause", "https://github.com/UN-GCPDS/qt-material"),
    ("pydantic", "MIT", "https://github.com/pydantic/pydantic"),
    ("platformdirs", "MIT", "https://github.com/tox-dev/platformdirs"),
    ("HTTPX", "BSD-3-Clause", "https://github.com/encode/httpx"),
    ("python-sounddevice", "MIT", "https://github.com/spatialaudio/python-sounddevice"),
    ("NumPy", "BSD-3-Clause", "https://numpy.org"),
    ("pynput", "LGPL-3.0", "https://github.com/moses-palmer/pynput"),
]


class _ReadmeWindow(QMainWindow):
    """The project README rendered as rich text (GitHub-style) in its own
    resizable/maximizable window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — README")
        self.setMinimumSize(420, 320)
        self.resize(860, 680)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.document().setDocumentMargin(18)
        readme = Path(__file__).resolve().parents[3] / "README.md"
        if readme.exists():
            # base URL makes relative links/images (docs/images/…) resolve
            browser.document().setBaseUrl(
                QUrl.fromLocalFile(str(readme.parent) + "/")
            )
            browser.setMarkdown(readme.read_text(encoding="utf-8"))
        else:
            browser.setPlainText(
                "README.md was not found next to the app (it is not bundled "
                "into packaged builds). You can read it in the project "
                "repository instead."
            )
        self.setCentralWidget(browser)


def _scrollable(widget: QWidget) -> QScrollArea:
    """Wrap a tab so it stays usable on small screens instead of clipping."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


class SettingsWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        db: HistoryDb,
        on_settings_changed: Callable[[], None],
        on_hotkeys_changed: Callable[[], None],
        on_clear_app_data: Callable[[], None] | None = None,
        on_run_setup: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.setMinimumSize(520, 400)  # fits small laptop screens; tabs scroll
        self.resize(780, 560)
        self._on_clear_app_data = on_clear_app_data
        self._on_run_setup = on_run_setup
        self._readme_window: _ReadmeWindow | None = None

        # ordered by how a new user sets things up: engine → editor → triggers → mic
        tabs = QTabWidget()
        # TranscriptionTab brings its own scroll area
        tabs.addTab(TranscriptionTab(settings, on_settings_changed), "Transcription")
        tabs.addTab(_scrollable(EditorTab(settings, on_settings_changed)), "Voice Editor")
        tabs.addTab(
            _scrollable(HotkeysTab(settings, on_settings_changed, on_hotkeys_changed)),
            "Hotkeys",
        )
        tabs.addTab(_scrollable(AudioTab(settings, on_settings_changed)), "Microphone")
        self.history_tab = HistoryTab(settings, db, on_settings_changed)
        tabs.addTab(self.history_tab, "History")
        tabs.addTab(
            _scrollable(self._general_tab(settings, on_settings_changed)), "General"
        )
        tabs.addTab(_scrollable(self._about_tab()), "About")
        self.setCentralWidget(tabs)

    def _general_tab(self, settings: Settings, on_change: Callable[[], None]) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        sounds_check = LabeledToggle("Play sounds when recording starts / text is pasted")
        sounds_check.setChecked(settings.ui.sounds_enabled)

        def toggle_sounds(checked: bool) -> None:
            settings.ui.sounds_enabled = checked
            on_change()

        sounds_check.toggled.connect(toggle_sounds)
        form.addRow(sounds_check)

        autostart_check = LabeledToggle("Start CleanWispr when Windows starts")
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

        verbose_check = LabeledToggle(
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

        folder_row = QHBoxLayout()
        settings_button = QPushButton("Open settings folder")
        settings_button.setToolTip(
            f"Opens {paths.config_dir()} in your file manager — config.json, "
            "history.db, logs, and downloaded models live here."
        )
        settings_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.config_dir())))
        )
        folder_row.addWidget(settings_button)
        logs_button = QPushButton("Open log folder")
        logs_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir() / "logs")))
        )
        folder_row.addWidget(logs_button)
        folder_row.addStretch()
        form.addRow(folder_row)

        form.addRow("Config file:", PathLink(paths.config_file()))
        form.addRow("Data directory:", PathLink(paths.data_dir()))
        form.addRow("Model storage:", PathLink(paths.models_root()))

        clear_row = QHBoxLayout()
        if self._on_run_setup:
            setup_button = QPushButton("Run setup guide")
            setup_button.setToolTip(
                "Re-opens the step-by-step first-run guide (engine + model "
                "download, language, voice editor)."
            )
            setup_button.clicked.connect(self._on_run_setup)
            clear_row.addWidget(setup_button)
        clear_button = QPushButton("Clear app data…")
        clear_button.setObjectName("danger")
        clear_button.setToolTip(
            "Deletes everything CleanWispr stored on this PC — like an uninstall."
        )
        clear_button.clicked.connect(self._confirm_clear_app_data)
        clear_row.addWidget(clear_button)
        clear_row.addStretch()
        form.addRow(clear_row)
        return widget

    def _confirm_clear_app_data(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Clear app data",
            "This permanently deletes ALL CleanWispr data from this PC:\n\n"
            "  • settings\n"
            "  • dictation/edit history\n"
            "  • logs\n"
            "  • downloaded models and engine binaries\n\n"
            "CleanWispr will quit afterwards. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes and self._on_clear_app_data:
            self._on_clear_app_data()

    def _about_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)

        title = QLabel(f"{APP_NAME} {__version__}")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Local voice-to-text and voice-driven text editing.\n"
            "No cloud, no accounts, no telemetry — audio and text never leave your PC."
        )
        subtitle.setStyleSheet(f"color: {theme.MUTED};")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        author = QLabel(f"Author: <b>{AUTHOR}</b>")
        layout.addWidget(author)
        layout.addSpacing(14)

        credits_title = QLabel("Built with")
        credits_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(credits_title)

        rows = "".join(
            f'<tr><td style="padding: 2px 14px 2px 0;">'
            f'<a href="{url}" style="color:{ACCENT_SOFT}; text-decoration:none;">'
            f"{name}</a></td>"
            f'<td style="color:{theme.MUTED};">{license_}</td></tr>'
            for name, license_, url in _CREDITS
        )
        credits_label = QLabel(f"<table>{rows}</table>")
        credits_label.setOpenExternalLinks(True)
        credits_label.setWordWrap(True)
        layout.addWidget(credits_label)

        note = QLabel(
            "CleanWispr reuses architectural patterns, model registry data, prompt "
            "engineering, and platform-integration recipes from the MIT-licensed "
            "OpenWhispr project."
        )
        note.setStyleSheet(f"color: {theme.MUTED};")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addSpacing(14)
        docs_title = QLabel("README.md")
        docs_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(docs_title)
        docs_hint = QLabel(
            "The full project documentation — features, setup, building, "
            "changelog — rendered like on GitHub."
        )
        docs_hint.setStyleSheet(f"color: {theme.MUTED};")
        docs_hint.setWordWrap(True)
        layout.addWidget(docs_hint)
        readme_row = QHBoxLayout()
        readme_button = QPushButton("Open README")
        readme_button.clicked.connect(self._open_readme)
        readme_row.addWidget(readme_button)
        readme_row.addStretch()
        layout.addLayout(readme_row)

        layout.addStretch()
        return widget

    def _open_readme(self) -> None:
        if self._readme_window is None:
            self._readme_window = _ReadmeWindow()
        self._readme_window.show()
        self._readme_window.raise_()
        self._readme_window.activateWindow()

    def closeEvent(self, event) -> None:  # Qt override, keeps Qt naming
        # closing the window hides it; the app lives in the tray
        event.ignore()
        self.hide()
