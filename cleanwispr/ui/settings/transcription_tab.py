"""Transcription settings: engine binary + model manager (download from the app,
like OpenWhispr's model picker), language, custom dictionary."""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from typing import ClassVar

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.storage.settings import Settings
from cleanwispr.stt import downloader, registry
from cleanwispr.stt.languages import LANGUAGES
from cleanwispr.ui.tasks import DownloadTask
from cleanwispr.ui.widgets import intro_label


class TranscriptionTab(QWidget):
    def __init__(self, settings: Settings, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._settings = settings
        self._on_change = on_change
        self._tasks: dict[str, DownloadTask] = {}  # keep refs while running

        layout = QVBoxLayout(self)
        layout.addWidget(intro_label(
            "Your speech is transcribed 100% locally by whisper.cpp — audio never "
            "leaves this PC. One-time setup: download an engine build below, download "
            "at least one model, then set the language you dictate in."
        ))
        layout.addWidget(self._engine_group())
        layout.addWidget(self._models_group())
        layout.addWidget(self._language_group())
        layout.addStretch()

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

        grid = QGridLayout()
        self._engine_rows: dict[str, dict] = {}
        for row_index, variant in enumerate(registry.server_variants()):
            name, description = self._VARIANT_LABELS[variant]
            grid.addWidget(
                QLabel(f"<b>{name}</b> <span style='color:gray'>({description})</span>"),
                row_index, 0,
            )
            status = QLabel()
            grid.addWidget(status, row_index, 1)
            progress = QProgressBar()
            progress.setVisible(False)
            progress.setFixedWidth(140)
            grid.addWidget(progress, row_index, 2)
            button = QPushButton()
            button.clicked.connect(partial(self._download_engine, variant))
            grid.addWidget(button, row_index, 3)
            self._engine_rows[variant] = {
                "status": status, "progress": progress, "button": button,
            }
            self._refresh_engine_row(variant)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return group

    def _refresh_engine_row(self, variant: str) -> None:
        widgets = self._engine_rows[variant]
        installed = registry.is_server_installed(variant)
        widgets["status"].setText("Installed" if installed else "not installed")
        widgets["button"].setText("Reinstall" if installed else "Download")

    def _download_engine(self, variant: str) -> None:
        widgets = self._engine_rows[variant]
        task = DownloadTask(partial(downloader.download_server_binary, variant))
        self._wire_task(task, f"engine:{variant}", widgets["progress"], widgets["button"])
        task.finished.connect(partial(self._refresh_engine_row, variant))
        task.start()

    def _gpu_changed(self) -> None:
        self._settings.stt.gpu = self._gpu_combo.currentData()
        self._on_change()

    # --- models ---

    def _models_group(self) -> QGroupBox:
        group = QGroupBox("Whisper models")
        group.setToolTip(
            "Bigger models are more accurate but slower. With a GPU, Turbo or Large "
            "give the best quality; on CPU, Base or Small keep things responsive."
        )
        grid = QGridLayout(group)
        self._model_rows: dict[str, dict] = {}
        for row_index, model in enumerate(registry.WHISPER_MODELS.values()):
            recommended = (
                " <span style='color:#b3a7f0'>(recommended)</span>" if model.recommended else ""
            )
            label = QLabel(
                f"<b>{model.label}</b>{recommended}"
                + f"<br><span style='color:gray'>{model.description} · {model.size_mb} MB</span>"
            )
            grid.addWidget(label, row_index, 0)

            active = QLabel()
            grid.addWidget(active, row_index, 1)

            progress = QProgressBar()
            progress.setVisible(False)
            progress.setFixedWidth(140)
            grid.addWidget(progress, row_index, 2)

            action = QPushButton()
            action.clicked.connect(partial(self._model_action, model.id))
            grid.addWidget(action, row_index, 3)

            use_button = QPushButton("Use")
            use_button.clicked.connect(partial(self._select_model, model.id))
            grid.addWidget(use_button, row_index, 4)

            self._model_rows[model.id] = {
                "active": active, "progress": progress, "action": action, "use": use_button,
            }
            self._refresh_model_row(model.id)
        return group

    def _refresh_model_row(self, model_id: str) -> None:
        widgets = self._model_rows[model_id]
        installed = registry.is_model_installed(model_id)
        selected = self._settings.stt.whisper_model == model_id
        widgets["active"].setText("ACTIVE" if selected and installed else "")
        widgets["use"].setEnabled(installed and not selected)
        # single stable connection; the label tells _model_action what to do
        widgets["action"].setText("Delete" if installed else "Download")

    def _model_action(self, model_id: str) -> None:
        if registry.is_model_installed(model_id):
            self._delete_model(model_id)
        else:
            self._download_model(model_id)

    def _download_model(self, model_id: str) -> None:
        widgets = self._model_rows[model_id]
        task = DownloadTask(partial(downloader.download_model, model_id))
        self._wire_task(task, f"model:{model_id}", widgets["progress"], widgets["action"])
        task.finished.connect(partial(self._refresh_model_row, model_id))
        task.start()

    def _delete_model(self, model_id: str) -> None:
        if self._settings.stt.whisper_model == model_id:
            QMessageBox.information(
                self, "Model in use", "Select another model before deleting the active one."
            )
            return
        downloader.delete_model(model_id)
        self._refresh_model_row(model_id)

    def _select_model(self, model_id: str) -> None:
        self._settings.stt.whisper_model = model_id
        self._on_change()
        for mid in self._model_rows:
            self._refresh_model_row(mid)

    # --- language + dictionary ---

    def _language_group(self) -> QGroupBox:
        group = QGroupBox("Language and custom dictionary")
        layout = QVBoxLayout(group)

        self._language_combo = QComboBox()
        for code, label in LANGUAGES:
            self._language_combo.addItem(label, code)
        index = self._language_combo.findData(self._settings.stt.language)
        self._language_combo.setCurrentIndex(max(0, index))
        self._language_combo.currentIndexChanged.connect(self._language_changed)
        layout.addWidget(self._language_combo)

        layout.addWidget(QLabel("Words/names to bias recognition (one per line):"))
        self._dictionary_edit = QPlainTextEdit("\n".join(self._settings.stt.custom_dictionary))
        self._dictionary_edit.setFixedHeight(70)
        self._dictionary_edit.textChanged.connect(self._dictionary_changed)
        layout.addWidget(self._dictionary_edit)
        return group

    def _language_changed(self) -> None:
        self._settings.stt.language = self._language_combo.currentData()
        self._on_change()

    def _dictionary_changed(self) -> None:
        words = [w.strip() for w in self._dictionary_edit.toPlainText().splitlines() if w.strip()]
        self._settings.stt.custom_dictionary = words
        self._on_change()

    # --- shared task wiring ---

    def _wire_task(
        self, task: DownloadTask, key: str, progress: QProgressBar, button: QPushButton
    ) -> None:
        self._tasks[key] = task
        button.setEnabled(False)
        progress.setVisible(True)
        progress.setRange(0, 0)  # indeterminate until we know the total

        def on_progress(received: int, total: object) -> None:
            if isinstance(total, int) and total > 0:
                progress.setRange(0, 100)
                progress.setValue(int(received * 100 / total))

        def cleanup() -> None:
            self._tasks.pop(key, None)
            progress.setVisible(False)
            button.setEnabled(True)

        def on_failed(msg: str) -> None:
            cleanup()
            QMessageBox.warning(self, "Download failed", msg)

        task.progress.connect(on_progress)
        task.finished.connect(cleanup)
        task.failed.connect(on_failed)
