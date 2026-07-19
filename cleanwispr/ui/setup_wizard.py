"""First-run guided setup.

Shown when the app starts with no existing config (fresh install); walks the
user through choosing and downloading a transcription engine + model, picking
a language, and optionally setting up Ollama for the voice editor. Re-runnable
any time from Settings → General → "Run setup guide"."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.llm import factory, hardware
from cleanwispr.storage.settings import Settings
from cleanwispr.stt import downloader, registry
from cleanwispr.stt.languages import LANGUAGES
from cleanwispr.ui import theme
from cleanwispr.ui.tasks import AsyncTask, DownloadTask
from cleanwispr.ui.widgets import ACCENT_SOFT

_WHISPER_MODEL = "base"  # the recommended starter model
_PARAKEET_MODEL = "parakeet-tdt-0.6b-v3"

_CARD_QSS = f"""
QFrame#wizardCard {{
    background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER};
    border-radius: 8px;
}}
QFrame#wizardCard[chosen="true"] {{ border-color: {theme.ACCENT}; }}
QLabel {{ background: transparent; }}
"""


def _pretty_combo(combo: str) -> str:
    names = {"super": "Win", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
    return " + ".join(names.get(part, part.upper() if len(part) < 4 else part.title())
                      for part in combo.split("+"))


class _OptionCard(QFrame):
    """A selectable card: radio + title + description."""

    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("wizardCard")
        self.setStyleSheet(_CARD_QSS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        self.radio = QRadioButton()
        layout.addWidget(self.radio)
        text = QVBoxLayout()
        text.setSpacing(2)
        title_label = QLabel(f"<b>{title}</b>")
        text.addWidget(title_label)
        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        text.addWidget(desc)
        layout.addLayout(text, 1)
        self.radio.toggled.connect(self._repaint_border)

    def mousePressEvent(self, event) -> None:  # Qt override
        self.radio.setChecked(True)
        super().mousePressEvent(event)

    def _repaint_border(self, checked: bool) -> None:
        self.setProperty("chosen", "true" if checked else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class SetupWizard(QDialog):
    """Step-by-step first-run setup dialog."""

    _TITLES = (
        "Welcome to CleanWispr",
        "Choose your transcription engine",
        "Which language do you speak?",
        "Voice editor (optional)",
        "You're all set!",
    )

    def __init__(
        self,
        settings: Settings,
        on_change: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_change = on_change
        self._tasks: list[DownloadTask | AsyncTask] = []

        self.setWindowTitle("CleanWispr setup")
        self.setModal(True)
        self.setMinimumSize(560, 460)
        self.resize(620, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        self._step_label = QLabel()
        self._step_label.setStyleSheet(
            f"color: {ACCENT_SOFT}; font-size: 10px; font-weight: 700;"
        )
        layout.addWidget(self._step_label)
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 19px; font-weight: 700;")
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._welcome_page())
        self._pages.addWidget(self._engine_page())
        self._pages.addWidget(self._language_page())
        self._pages.addWidget(self._editor_page())
        self._pages.addWidget(self._done_page())
        layout.addWidget(self._pages, 1)

        footer = QHBoxLayout()
        self._back_button = QPushButton("Back")
        self._back_button.clicked.connect(partial(self._go, -1))
        footer.addWidget(self._back_button)
        footer.addStretch()
        skip = QPushButton("Skip setup")
        skip.setToolTip("Everything here can also be done later in Settings.")
        skip.clicked.connect(self.reject)
        footer.addWidget(skip)
        self._next_button = QPushButton("Next")
        self._next_button.clicked.connect(partial(self._go, +1))
        footer.addWidget(self._next_button)
        layout.addLayout(footer)

        self._show_page(0)

    # --- navigation ---

    def _go(self, delta: int) -> None:
        index = self._pages.currentIndex() + delta
        if index >= self._pages.count():
            self._on_change()
            self.accept()
            return
        self._show_page(max(0, index))

    def _show_page(self, index: int) -> None:
        self._pages.setCurrentIndex(index)
        self._step_label.setText(f"STEP {index + 1} OF {self._pages.count()}")
        self._title_label.setText(self._TITLES[index])
        self._back_button.setVisible(index > 0)
        self._next_button.setText("Finish" if index == self._pages.count() - 1 else "Next")
        self._next_button.setEnabled(True)  # page 1 may re-disable below
        if index == 1:
            self._update_engine_state()
        if index == 3:
            self._check_ollama()
            self._detect_hardware()
        if index == 4:
            self._refresh_done_text()

    @staticmethod
    def _body(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        return label

    # --- page 0: welcome ---

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.addWidget(self._body(
            "CleanWispr turns your voice into text in <b>any</b> application — "
            "100% locally. No cloud, no accounts: audio and text never leave "
            "this PC.<br><br>"
            "This short guide sets up the two things the app needs:"
            "<ul>"
            "<li><b>A transcription engine + model</b> (a one-time download) "
            "so dictation works.</li>"
            "<li>Optionally, <b>Ollama</b> — a free local AI runner that powers "
            "the voice editor (\"make this formal\", \"translate to English\", …).</li>"
            "</ul>"
            "You can change every choice later in Settings."
        ))
        layout.addStretch()
        return page

    # --- page 1: engine + model download ---

    def _engine_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        self._whisper_card = _OptionCard(
            "Whisper (recommended)",
            "The most widely used speech recognition. 60+ languages, custom "
            f"dictionary, GPU acceleration available. Downloads the engine + "
            f"'{_WHISPER_MODEL.title()}' starter model (≈220 MB).",
        )
        self._parakeet_card = _OptionCard(
            "NVIDIA Parakeet",
            "Very fast even without a GPU. 25 languages with automatic "
            "detection (≈465 MB download). No custom dictionary.",
        )
        # radios sit in different parent widgets, so Qt's automatic
        # exclusivity doesn't apply — group them explicitly
        self._engine_group = QButtonGroup(page)
        self._engine_group.addButton(self._whisper_card.radio)
        self._engine_group.addButton(self._parakeet_card.radio)
        self._whisper_card.radio.setChecked(self._settings.stt.engine != "parakeet")
        self._parakeet_card.radio.setChecked(self._settings.stt.engine == "parakeet")
        self._whisper_card.radio.toggled.connect(self._update_engine_state)
        layout.addWidget(self._whisper_card)
        layout.addWidget(self._parakeet_card)

        hint = self._body(
            "Tip: have an NVIDIA graphics card? After setup, grab the CUDA "
            "engine build in Settings → Transcription — it's ~25x faster."
        )
        hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        layout.addWidget(hint)
        layout.addStretch()

        self._engine_status = QLabel(" ")
        self._engine_status.setWordWrap(True)
        layout.addWidget(self._engine_status)
        self._engine_progress = QProgressBar()
        self._engine_progress.setTextVisible(False)
        self._engine_progress.setVisible(False)
        layout.addWidget(self._engine_progress)
        self._download_button = QPushButton("Download")
        self._download_button.clicked.connect(self._start_download)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._download_button)
        layout.addLayout(row)
        return page

    def _chosen_whisper(self) -> bool:
        return self._whisper_card.radio.isChecked()

    def _chosen_ready(self) -> bool:
        if self._chosen_whisper():
            return registry.is_server_installed("cpu") and registry.is_model_installed(
                _WHISPER_MODEL
            )
        return registry.is_parakeet_model_installed(_PARAKEET_MODEL)

    def _update_engine_state(self) -> None:
        ready = self._chosen_ready()
        self._download_button.setVisible(not ready)
        self._next_button.setEnabled(ready or self._pages.currentIndex() != 1)
        if ready:
            self._engine_status.setText("✓ Ready — everything needed is installed.")
            self._engine_status.setStyleSheet("color: #3dd68c;")
        else:
            self._engine_status.setText(
                "Click Download to fetch what's needed — you can keep using "
                "your PC meanwhile."
            )
            self._engine_status.setStyleSheet(f"color: {theme.MUTED};")

    def _start_download(self) -> None:
        """Download the missing pieces for the chosen engine, one after another."""
        self._apply_engine_choice()
        if self._chosen_whisper():
            steps = []
            if not registry.is_server_installed("cpu"):
                steps.append(("engine", partial(downloader.download_server_binary, "cpu")))
            if not registry.is_model_installed(_WHISPER_MODEL):
                steps.append(("model", partial(downloader.download_model, _WHISPER_MODEL)))
        else:
            steps = [("model", partial(downloader.download_parakeet_model, _PARAKEET_MODEL))]
        self._run_steps(steps, total=len(steps))

    def _apply_engine_choice(self) -> None:
        if self._chosen_whisper():
            self._settings.stt.engine = "whisper"
            self._settings.stt.whisper_model = _WHISPER_MODEL
        else:
            self._settings.stt.engine = "parakeet"
            self._settings.stt.parakeet_model = _PARAKEET_MODEL
        self._on_change()

    def _run_steps(self, steps: list, total: int) -> None:
        if not steps:
            self._engine_progress.setVisible(False)
            self._update_engine_state()
            return
        name, work = steps[0]
        step_no = total - len(steps) + 1
        self._download_button.setEnabled(False)
        self._whisper_card.setEnabled(False)
        self._parakeet_card.setEnabled(False)
        self._engine_status.setText(f"Downloading {name} ({step_no}/{total})…")
        self._engine_status.setStyleSheet(f"color: {theme.MUTED};")
        self._engine_progress.setVisible(True)
        self._engine_progress.setRange(0, 0)

        task = DownloadTask(work)
        self._tasks.append(task)

        def on_progress(received: int, size: object) -> None:
            if isinstance(size, int) and size > 0:
                self._engine_progress.setRange(0, 100)
                self._engine_progress.setValue(int(received * 100 / size))

        def on_finished() -> None:
            self._tasks.remove(task)
            self._run_steps(steps[1:], total)

        def on_failed(message: str) -> None:
            self._tasks.remove(task)
            self._engine_progress.setVisible(False)
            self._download_button.setEnabled(True)
            self._whisper_card.setEnabled(True)
            self._parakeet_card.setEnabled(True)
            self._engine_status.setText(f"Download failed: {message}")
            self._engine_status.setStyleSheet(f"color: {theme.DANGER};")

        task.progress.connect(on_progress)
        task.finished.connect(on_finished)
        task.failed.connect(on_failed)
        task.start()
        if len(steps) == 1:

            def re_enable() -> None:
                self._download_button.setEnabled(True)
                self._whisper_card.setEnabled(True)
                self._parakeet_card.setEnabled(True)
                self._update_engine_state()

            task.finished.connect(re_enable)

    # --- page 2: language ---

    def _language_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.addWidget(self._body(
            "Pick the language you'll dictate in — or leave it on "
            "<b>Auto-detect</b> if you switch languages. (Parakeet always "
            "auto-detects; this choice applies to Whisper.)"
        ))
        combo = QComboBox()
        for code, label in LANGUAGES:
            combo.addItem(label, code)
        combo.setCurrentIndex(max(0, combo.findData(self._settings.stt.language)))

        def changed() -> None:
            self._settings.stt.language = combo.currentData()
            self._on_change()

        combo.currentIndexChanged.connect(changed)
        layout.addWidget(combo)
        layout.addStretch()
        return page

    # --- page 3: voice editor / Ollama ---

    def _editor_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.addWidget(self._body(
            "The voice editor lets you <b>select text anywhere</b>, press the "
            "editor hotkey, and speak a change: <i>\"make this formal\"</i>, "
            "<i>\"translate to English\"</i>, <i>\"remove the second "
            "sentence\"</i>.<br><br>"
            "It needs <b>Ollama</b>, a free app that runs AI models locally:"
            "<ol>"
            "<li>Install Ollama from the website below.</li>"
            "<li>After this guide, open Settings → Voice Editor and paste the "
            "recommended command shown below.</li>"
            "</ol>"
            "This step is optional — dictation works without it."
        ))

        self._hw_label = QLabel("Checking your hardware for a model recommendation…")
        self._hw_label.setWordWrap(True)
        self._hw_label.setTextFormat(Qt.TextFormat.RichText)
        self._hw_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._hw_label.setStyleSheet(
            f"background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER}; "
            "border-radius: 8px; padding: 10px;"
        )
        layout.addWidget(self._hw_label)

        row = QHBoxLayout()
        website = QPushButton("Open ollama.com")
        website.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://ollama.com"))
        )
        row.addWidget(website)
        check = QPushButton("Check again")
        check.clicked.connect(self._check_ollama)
        row.addWidget(check)
        row.addStretch()
        layout.addLayout(row)

        self._ollama_status = QLabel(" ")
        self._ollama_status.setWordWrap(True)
        layout.addWidget(self._ollama_status)
        layout.addStretch()
        return page

    def _detect_hardware(self) -> None:
        if getattr(self, "_hw_done", False):
            return
        self._hw_done = True

        task = AsyncTask(hardware.detect)
        self._tasks.append(task)

        def done(result: object) -> None:
            self._tasks.remove(task)
            if not isinstance(result, hardware.Hardware):
                result = hardware.Hardware("cpu", "unknown hardware", None, None)
            model, reason = hardware.recommended_ollama_model(result)
            self._hw_label.setText(
                f"<b>Detected:</b> {result.name}<br>"
                f"<b>Recommended model:</b> <code>{model}</code> — {reason}.<br>"
                f"Paste this in Settings → Voice Editor after installing Ollama:"
                f"<br><code>ollama pull {model}</code>"
            )

        def failed(_message: str) -> None:
            self._tasks.remove(task)
            self._hw_label.setText(
                "Couldn't inspect your hardware — <code>gemma3:4b</code> is a "
                "safe starting model on most machines:<br>"
                "<code>ollama pull gemma3:4b</code>"
            )

        task.done.connect(done)
        task.failed.connect(failed)
        task.start()

    def _check_ollama(self) -> None:
        self._ollama_status.setText("Checking for Ollama…")
        self._ollama_status.setStyleSheet(f"color: {theme.MUTED};")
        settings = self._settings

        task = AsyncTask(lambda: factory.create_provider(settings.llm).is_available())
        self._tasks.append(task)

        def done(available: object) -> None:
            self._tasks.remove(task)
            if available:
                self._ollama_status.setText("✓ Ollama is installed and running.")
                self._ollama_status.setStyleSheet("color: #3dd68c;")
            else:
                self._ollama_status.setText(
                    "Ollama was not found (it may not be installed or running). "
                    "You can set it up any time later."
                )
                self._ollama_status.setStyleSheet(f"color: {theme.MUTED};")

        task.done.connect(done)
        task.failed.connect(lambda _msg: done(False))
        task.start()

    # --- page 4: done ---

    def _done_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._done_label = self._body("")
        layout.addWidget(self._done_label)
        layout.addStretch()
        return page

    def _refresh_done_text(self) -> None:
        dictation = _pretty_combo(self._settings.hotkeys.dictation.combo)
        editor = _pretty_combo(self._settings.hotkeys.editor.combo)
        self._done_label.setText(
            f"<b>Dictate:</b> click into any text field, press "
            f"<b>{dictation}</b>, speak, press it again — your words appear at "
            f"the cursor.<br><br>"
            f"<b>Edit by voice:</b> select some text, press <b>{editor}</b>, "
            f"and say what to change.<br><br>"
            f"CleanWispr lives in the <b>system tray</b> (microphone icon) — "
            f"right-click it for Settings, where every choice from this guide "
            f"can be adjusted."
        )
