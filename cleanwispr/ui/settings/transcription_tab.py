"""Transcription settings: engine binary + model manager (download from the app,
like OpenWhispr's model picker), storage location, language, custom dictionary."""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from typing import ClassVar

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.llm import hardware
from cleanwispr.storage import paths
from cleanwispr.storage.settings import Settings
from cleanwispr.stt import downloader, registry
from cleanwispr.stt.languages import LANGUAGES
from cleanwispr.ui import theme
from cleanwispr.ui.tasks import AsyncTask, DownloadTask
from cleanwispr.ui.widgets import ModelRow, intro_label

# which whisper-server build best matches each detected accelerator
_RECOMMENDED_VARIANT: dict[str, str] = {
    "nvidia": "cuda",
    "amd": "vulkan",
    "apple": "cpu",  # macOS build has Metal baked in
    "cpu": "cpu",
}


class TranscriptionTab(QWidget):
    def __init__(self, settings: Settings, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._settings = settings
        self._on_change = on_change
        self._tasks: dict[str, DownloadTask] = {}  # keep refs while running
        self._cancelled: set[str] = set()  # keys the user aborted mid-download

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(intro_label(
            "Your speech is transcribed 100% locally — audio never leaves this PC. "
            "One-time setup: download an engine build below, download at least one "
            "model, then set the language you dictate in."
        ))
        layout.addWidget(self._engine_choice_group())
        layout.addWidget(self._engine_group())
        layout.addWidget(self._models_group())
        layout.addWidget(self._parakeet_group())
        layout.addWidget(self._storage_group())
        layout.addWidget(self._language_group())
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # --- engine choice (whisper vs parakeet) ---

    def _engine_choice_group(self) -> QGroupBox:
        group = QGroupBox("Speech recognition engine")
        row = QHBoxLayout(group)
        self._engine_combo = QComboBox()
        self._engine_combo.addItem("Whisper (whisper.cpp — 60+ languages, GPU builds)", "whisper")
        self._engine_combo.addItem(
            "NVIDIA Parakeet (sherpa-onnx — very fast, auto language)", "parakeet"
        )
        index = self._engine_combo.findData(self._settings.stt.engine)
        self._engine_combo.setCurrentIndex(max(0, index))
        self._engine_combo.setToolTip(
            "Whisper: widest language support, custom dictionary, GPU acceleration. "
            "Parakeet: NVIDIA's ASR — excellent speed and accuracy even on CPU, "
            "25 languages with automatic detection, no dictionary support."
        )
        self._engine_combo.currentIndexChanged.connect(self._engine_changed)
        row.addWidget(self._engine_combo, 1)
        return group

    def _engine_changed(self) -> None:
        self._settings.stt.engine = self._engine_combo.currentData()
        self._on_change()
        self._refresh_all_model_rows()
        self._update_language_notice()

    def _refresh_all_model_rows(self) -> None:
        """The ACTIVE badge depends on both the selected model and the selected
        engine, so any engine change must repaint every model row."""
        for model_id in self._model_rows:
            self._refresh_model_row(model_id)
        for model_id in self._parakeet_rows:
            self._refresh_parakeet_row(model_id)

    def refresh(self) -> None:
        """Re-read install state and settings from disk. The Settings window is
        built once at startup, so this must run every time it's shown — otherwise
        downloads/choices made elsewhere (e.g. the setup wizard) don't appear
        and rows keep saying 'download' for things already installed."""
        self._sync_combo(self._engine_combo, self._settings.stt.engine)
        self._sync_combo(self._gpu_combo, self._settings.stt.gpu)
        self._sync_combo(self._language_combo, self._settings.stt.language)
        self._models_dir_edit.setText(str(paths.models_root()))
        for variant in self._engine_rows:
            self._refresh_engine_row(variant)
        self._refresh_all_model_rows()
        self._update_language_notice()

    @staticmethod
    def _sync_combo(combo: QComboBox, value: str) -> None:
        """Point a combo at `value` without firing its change handler (which would
        re-save settings and repaint)."""
        combo.blockSignals(True)
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    # --- engine binaries (cpu / cuda / vulkan) ---

    _VARIANT_LABELS: ClassVar[dict[str, tuple[str, str]]] = {
        "cpu": ("CPU", "works everywhere"),
        "cuda": ("CUDA", "NVIDIA GPUs — fastest by far"),
        "vulkan": ("Vulkan", "AMD/Intel/NVIDIA GPUs"),
    }
    if sys.platform == "darwin":
        _VARIANT_LABELS["cpu"] = ("Standard", "includes Metal GPU acceleration")

    def _engine_group(self) -> QGroupBox:
        group = QGroupBox("Transcription engine (whisper.cpp server)")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        self._hw_hint = QLabel("Checking your hardware for the best engine build…")
        self._hw_hint.setWordWrap(True)
        self._hw_hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        layout.addWidget(self._hw_hint)

        gpu_row = QHBoxLayout()
        gpu_row.addWidget(QLabel("Acceleration:"))
        self._gpu_combo = QComboBox()
        for value, label in [
            ("auto", "Auto (best installed: CUDA, then Vulkan, then CPU)"),
            ("cuda", "CUDA"),
            ("vulkan", "Vulkan"),
            ("cpu", "CPU only"),
        ]:
            self._gpu_combo.addItem(label, value)
        index = self._gpu_combo.findData(self._settings.stt.gpu)
        self._gpu_combo.setCurrentIndex(max(0, index))
        self._gpu_combo.setToolTip(
            "Which engine build transcribes your speech. CUDA (NVIDIA GPUs) is ~25x "
            "faster than CPU; Auto simply picks the best one you have installed."
        )
        self._gpu_combo.currentIndexChanged.connect(self._gpu_changed)
        gpu_row.addWidget(self._gpu_combo, 1)
        layout.addLayout(gpu_row)

        self._engine_rows: dict[str, ModelRow] = {}
        for variant in registry.server_variants():
            name, description = self._VARIANT_LABELS[variant]
            row = ModelRow(name, description, usable=False)
            row.download_clicked.connect(partial(self._download_engine, variant))
            row.cancel_clicked.connect(partial(self._cancel_download, f"engine:{variant}"))
            layout.addWidget(row)
            self._engine_rows[variant] = row
            self._refresh_engine_row(variant)
        self._detect_hardware()
        return group

    def _detect_hardware(self) -> None:
        """Detect the local accelerator (worker thread) and mark the matching
        whisper build as recommended so the user knows which one to download."""
        task = AsyncTask(hardware.detect)
        self._hw_task = task  # keep a ref while it runs
        task.done.connect(self._hardware_detected)
        task.failed.connect(lambda _msg: self._hw_hint.setText(" "))
        task.start()

    def _hardware_detected(self, result: object) -> None:
        if not isinstance(result, hardware.Hardware):
            self._hw_hint.setText(" ")
            return
        variant = _RECOMMENDED_VARIANT.get(result.kind, "cpu")
        if variant not in self._engine_rows:  # e.g. macOS: only the cpu build exists
            variant = "cpu"
        label = self._VARIANT_LABELS[variant][0]
        self._hw_hint.setText(
            f"Detected <b>{result.name}</b> — recommended build: <b>{label}</b>. "
            "Download it below for the best speed; CPU always works as a fallback."
        )
        for name, row in self._engine_rows.items():
            row.set_tag("Recommended" if name == variant else None)

    def _refresh_engine_row(self, variant: str) -> None:
        self._engine_rows[variant].set_state(registry.is_server_installed(variant))

    def _download_engine(self, variant: str) -> None:
        row = self._engine_rows[variant]
        task = DownloadTask(partial(downloader.download_server_binary, variant))
        self._wire_task(task, f"engine:{variant}", row)
        task.finished.connect(partial(self._refresh_engine_row, variant))
        task.start()

    def _gpu_changed(self) -> None:
        self._settings.stt.gpu = self._gpu_combo.currentData()
        self._on_change()

    # --- whisper models ---

    def _models_group(self) -> QGroupBox:
        group = QGroupBox("Whisper models")
        group.setToolTip(
            "Bigger models are more accurate but slower. With a GPU, Turbo or Large "
            "give the best quality; on CPU, Base or Small keep things responsive."
        )
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        self._model_rows: dict[str, ModelRow] = {}
        for model in registry.WHISPER_MODELS.values():
            row = ModelRow(
                model.label,
                f"{model.description} · {model.size_mb} MB",
                tag="Recommended" if model.recommended else None,
            )
            row.download_clicked.connect(partial(self._download_model, model.id))
            row.delete_clicked.connect(partial(self._delete_model, model.id))
            row.use_clicked.connect(partial(self._select_model, model.id))
            row.cancel_clicked.connect(partial(self._cancel_download, f"model:{model.id}"))
            layout.addWidget(row)
            self._model_rows[model.id] = row
            self._refresh_model_row(model.id)
        return group

    def _refresh_model_row(self, model_id: str) -> None:
        installed = registry.is_model_installed(model_id)
        active = (
            installed
            and self._settings.stt.engine == "whisper"
            and self._settings.stt.whisper_model == model_id
        )
        self._model_rows[model_id].set_state(installed, active)

    def _download_model(self, model_id: str) -> None:
        row = self._model_rows[model_id]
        task = DownloadTask(partial(downloader.download_model, model_id))
        self._wire_task(task, f"model:{model_id}", row)
        task.finished.connect(partial(self._refresh_model_row, model_id))
        task.start()

    def _delete_model(self, model_id: str) -> None:
        if (
            self._settings.stt.engine == "whisper"
            and self._settings.stt.whisper_model == model_id
        ):
            QMessageBox.information(
                self, "Model in use", "Select another model before deleting the active one."
            )
            return
        downloader.delete_model(model_id)
        self._refresh_model_row(model_id)

    def _select_model(self, model_id: str) -> None:
        self._settings.stt.whisper_model = model_id
        self._switch_engine("whisper")

    def _switch_engine(self, engine: str) -> None:
        """Using a model also activates its engine; route through the combo so
        the change is saved and every row repaints exactly once."""
        self._settings.stt.engine = engine
        index = self._engine_combo.findData(engine)
        if self._engine_combo.currentIndex() != index:
            self._engine_combo.setCurrentIndex(index)  # triggers _engine_changed
        else:
            self._on_change()
            self._refresh_all_model_rows()

    # --- parakeet models ---

    def _parakeet_group(self) -> QGroupBox:
        group = QGroupBox("NVIDIA Parakeet models")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        self._parakeet_rows: dict[str, ModelRow] = {}
        for model in registry.PARAKEET_MODELS.values():
            row = ModelRow(model.label, f"{model.description} · {model.size_mb} MB")
            row.download_clicked.connect(partial(self._download_parakeet, model.id))
            row.delete_clicked.connect(partial(self._delete_parakeet, model.id))
            row.use_clicked.connect(partial(self._select_parakeet, model.id))
            row.cancel_clicked.connect(partial(self._cancel_download, f"parakeet:{model.id}"))
            layout.addWidget(row)
            self._parakeet_rows[model.id] = row
            self._refresh_parakeet_row(model.id)
        return group

    def _refresh_parakeet_row(self, model_id: str) -> None:
        installed = registry.is_parakeet_model_installed(model_id)
        active = (
            installed
            and self._settings.stt.engine == "parakeet"
            and self._settings.stt.parakeet_model == model_id
        )
        self._parakeet_rows[model_id].set_state(installed, active)

    def _download_parakeet(self, model_id: str) -> None:
        row = self._parakeet_rows[model_id]
        task = DownloadTask(partial(downloader.download_parakeet_model, model_id))
        self._wire_task(task, f"parakeet:{model_id}", row)
        task.finished.connect(partial(self._refresh_parakeet_row, model_id))
        task.start()

    def _delete_parakeet(self, model_id: str) -> None:
        if (
            self._settings.stt.engine == "parakeet"
            and self._settings.stt.parakeet_model == model_id
        ):
            QMessageBox.information(
                self, "Model in use", "Select another model before deleting the active one."
            )
            return
        downloader.delete_parakeet_model(model_id)
        self._refresh_parakeet_row(model_id)

    def _select_parakeet(self, model_id: str) -> None:
        self._settings.stt.parakeet_model = model_id
        self._switch_engine("parakeet")

    # --- model storage location ---

    def _storage_group(self) -> QGroupBox:
        group = QGroupBox("Model storage location")
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self._models_dir_edit = QLineEdit(str(paths.models_root()))
        self._models_dir_edit.setReadOnly(True)
        row.addWidget(self._models_dir_edit, 1)
        browse = QPushButton("Change…")
        browse.clicked.connect(self._browse_models_dir)
        row.addWidget(browse)
        reset = QPushButton("Reset to default")
        reset.clicked.connect(partial(self._set_models_dir, ""))
        row.addWidget(reset)
        layout.addLayout(row)

        hint = QLabel(
            "Models download into this folder — point it at another disk if you're "
            "short on space. Existing downloads are not moved automatically: move "
            "the 'whisper' and 'parakeet' subfolders there yourself, or re-download."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.MUTED};")
        layout.addWidget(hint)
        return group

    def _browse_models_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose model storage folder", str(paths.models_root())
        )
        if chosen:
            self._set_models_dir(chosen)

    def _set_models_dir(self, value: str) -> None:
        self._settings.stt.models_dir = value
        paths.set_models_override(value or None)
        self._models_dir_edit.setText(str(paths.models_root()))
        self._on_change()
        self._refresh_all_model_rows()  # installed states depend on the folder

    # --- language + dictionary ---

    def _language_group(self) -> QGroupBox:
        group = QGroupBox("Language and custom dictionary")
        layout = QVBoxLayout(group)

        language_hint = QLabel(
            "<b>Whisper</b> transcribes in the language you pick here — or figures "
            "it out per recording on <i>Auto-detect</i>. Picking a fixed language "
            "makes recognition faster and more accurate if you always dictate in "
            "one language.<br>"
            "<b>NVIDIA Parakeet</b> ignores this dropdown: the 0.6B v3 model "
            "always auto-detects among its 25 languages, and the 110M model is "
            "English-only."
        )
        language_hint.setWordWrap(True)
        language_hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        layout.addWidget(language_hint)

        self._language_combo = QComboBox()
        for code, label in LANGUAGES:
            self._language_combo.addItem(label, code)
        index = self._language_combo.findData(self._settings.stt.language)
        self._language_combo.setCurrentIndex(max(0, index))
        self._language_combo.currentIndexChanged.connect(self._language_changed)
        layout.addWidget(self._language_combo)

        layout.addSpacing(6)
        layout.addWidget(QLabel("Words/names to bias recognition (one per line):"))
        dictionary_hint = QLabel(
            "<b>Whisper only:</b> these words are shown to the model as context "
            "before each recording, so unusual names, brands, and jargon come out "
            "spelled right (e.g. your company or product names). "
            "<b>Parakeet</b> does not support a custom dictionary and skips this "
            "list."
        )
        dictionary_hint.setWordWrap(True)
        dictionary_hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        layout.addWidget(dictionary_hint)
        self._dictionary_edit = QPlainTextEdit("\n".join(self._settings.stt.custom_dictionary))
        self._dictionary_edit.setFixedHeight(70)
        self._dictionary_edit.textChanged.connect(self._dictionary_changed)
        layout.addWidget(self._dictionary_edit)

        # live notice when the current engine won't use these settings
        self._parakeet_notice = QLabel(
            "Parakeet is currently your active engine — it auto-detects the "
            "language and ignores the dictionary. These settings apply when you "
            "switch back to Whisper."
        )
        self._parakeet_notice.setWordWrap(True)
        self._parakeet_notice.setStyleSheet(
            "color: #f0b429; font-size: 11px; padding-top: 4px;"
        )
        layout.addWidget(self._parakeet_notice)
        self._update_language_notice()
        return group

    def _update_language_notice(self) -> None:
        self._parakeet_notice.setVisible(self._settings.stt.engine == "parakeet")

    def _language_changed(self) -> None:
        self._settings.stt.language = self._language_combo.currentData()
        self._on_change()

    def _dictionary_changed(self) -> None:
        words = [w.strip() for w in self._dictionary_edit.toPlainText().splitlines() if w.strip()]
        self._settings.stt.custom_dictionary = words
        self._on_change()

    # --- shared task wiring ---

    def _cancel_download(self, key: str) -> None:
        """Abort the in-flight download for a row (its Cancel button, wired once
        at row creation, routes here)."""
        task = self._tasks.get(key)
        if task is not None:
            self._cancelled.add(key)
            task.cancel()

    def _wire_task(self, task: DownloadTask, key: str, row: ModelRow) -> None:
        self._tasks[key] = task
        self._cancelled.discard(key)
        row.set_busy(True)

        def cleanup() -> None:
            self._tasks.pop(key, None)
            row.set_busy(False)

        def on_failed(msg: str) -> None:
            was_cancelled = key in self._cancelled
            self._cancelled.discard(key)
            cleanup()
            if not was_cancelled:  # a user-initiated cancel isn't a failure to report
                QMessageBox.warning(self, "Download failed", msg)

        task.progress.connect(row.set_progress)
        task.finished.connect(cleanup)
        task.failed.connect(on_failed)
