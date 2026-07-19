"""Editor (LLM) settings: Ollama connection, auto-discovered model list,
context window and generation parameters."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.llm import server as ollama_server
from cleanwispr.llm.base import LlmModelInfo
from cleanwispr.llm.ollama import OllamaProvider, parse_pull_command
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.tasks import AsyncTask, DownloadTask
from cleanwispr.ui.widgets import intro_label


class EditorTab(QWidget):
    def __init__(self, settings: Settings, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._settings = settings
        self._on_change = on_change
        self._tasks: list[AsyncTask] = []
        self._pull_tasks: list[DownloadTask] = []

        layout = QVBoxLayout(self)
        layout.addWidget(intro_label(
            "The voice editor rewrites text by voice: select text in any app, press "
            "the editor hotkey, and speak a command like “make this formal” — a local "
            "AI model applies it and the result replaces your selection. With nothing "
            "selected, it writes new text from your command. Requires Ollama "
            "(free, ollama.com) running on this PC."
        ))
        layout.addWidget(self._connection_group())
        layout.addWidget(self._model_group())
        layout.addWidget(self._pull_group())
        layout.addWidget(self._params_group())
        layout.addStretch()
        self._refresh_models()

    def _ollama(self) -> OllamaProvider:
        return OllamaProvider(self._settings.llm.ollama.base_url)

    # --- connection ---

    def _connection_group(self) -> QGroupBox:
        group = QGroupBox("Ollama server")
        row = QHBoxLayout(group)
        self._url_edit = QLineEdit(self._settings.llm.ollama.base_url)
        self._url_edit.editingFinished.connect(self._url_changed)
        row.addWidget(self._url_edit, 1)
        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self._test_connection)
        row.addWidget(test_button)
        self._start_button = QPushButton("Start Ollama")
        self._start_button.setVisible(False)
        self._start_button.clicked.connect(self._start_ollama)
        row.addWidget(self._start_button)
        self._status = QLabel()
        self._status.setStyleSheet("color: gray;")
        row.addWidget(self._status)
        return group

    def _start_ollama(self) -> None:
        self._status.setText("Starting Ollama…")
        self._start_button.setEnabled(False)
        provider = self._ollama()

        def done(ok) -> None:
            self._start_button.setEnabled(True)
            if ok:
                self._start_button.setVisible(False)
                self._status.setText("Ollama started")
                self._refresh_models()
            else:
                self._status.setText("Could not start Ollama — is it installed? (ollama.com)")

        self._run_async(lambda: ollama_server.ensure_running(provider), done, done)

    def _url_changed(self) -> None:
        self._settings.llm.ollama.base_url = self._url_edit.text().strip()
        self._on_change()
        self._refresh_models()

    def _test_connection(self) -> None:
        provider = self._ollama()
        self._run_async(
            provider.server_version,
            lambda version: self._status.setText(f"Connected — Ollama {version}"),
            lambda msg: self._status.setText(f"Error: {msg}"),
        )

    # --- models ---

    def _model_group(self) -> QGroupBox:
        group = QGroupBox("Model (auto-detected from Ollama)")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self._model_combo = QComboBox()
        self._model_combo.currentIndexChanged.connect(self._model_changed)
        row.addWidget(self._model_combo, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_models)
        row.addWidget(refresh)
        layout.addLayout(row)
        self._model_details = QLabel(" ")
        self._model_details.setStyleSheet("color: gray;")
        layout.addWidget(self._model_details)
        return group

    def _refresh_models(self) -> None:
        provider = self._ollama()
        self._run_async(provider.list_models, self._models_loaded, self._models_failed)

    def _models_loaded(self, models: list[LlmModelInfo]) -> None:
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for model in models:
            extras = " · ".join(x for x in (model.parameter_size, model.quantization) if x)
            label = f"{model.id}  ({extras})" if extras else model.id
            self._model_combo.addItem(label, model.id)
        saved = self._settings.llm.ollama.model
        index = self._model_combo.findData(saved) if saved else -1
        self._model_combo.setCurrentIndex(index if index >= 0 else -1)
        self._model_combo.blockSignals(False)
        if not models:
            self._status.setText("No models installed — run: ollama pull <model>")
        elif index < 0 and saved:
            self._status.setText(f"Saved model '{saved}' is no longer installed")

    def _models_failed(self, message: str) -> None:
        self._status.setText(f"Error: {message}")
        if ollama_server.find_ollama_binary():
            self._start_button.setVisible(True)

    # --- install models from the app (no terminal needed) ---

    def _pull_group(self) -> QGroupBox:
        group = QGroupBox("Install a model")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self._pull_edit = QLineEdit()
        self._pull_edit.setPlaceholderText(
            "Paste an Ollama command or model name, e.g.  ollama pull qwen3:8b"
        )
        self._pull_edit.returnPressed.connect(self._start_pull)
        row.addWidget(self._pull_edit, 1)
        self._pull_button = QPushButton("Download")
        self._pull_button.clicked.connect(self._start_pull)
        row.addWidget(self._pull_button)
        layout.addLayout(row)

        self._run_as_pull_check = QCheckBox("Treat pasted 'ollama run …' commands as downloads")
        self._run_as_pull_check.setChecked(self._settings.llm.ollama.interpret_run_as_pull)
        self._run_as_pull_check.toggled.connect(self._run_as_pull_changed)
        layout.addWidget(self._run_as_pull_check)

        progress_row = QHBoxLayout()
        self._pull_progress = QProgressBar()
        self._pull_progress.setVisible(False)
        progress_row.addWidget(self._pull_progress, 1)
        layout.addLayout(progress_row)
        self._pull_status = QLabel(
            "Only the model name is extracted and downloaded via Ollama's API — "
            "nothing you paste is ever executed."
        )
        self._pull_status.setStyleSheet("color: gray;")
        self._pull_status.setWordWrap(True)
        layout.addWidget(self._pull_status)
        return group

    def _run_as_pull_changed(self, checked: bool) -> None:
        self._settings.llm.ollama.interpret_run_as_pull = checked
        self._on_change()

    def _start_pull(self) -> None:
        try:
            model, notice = parse_pull_command(
                self._pull_edit.text(),
                interpret_run_as_pull=self._settings.llm.ollama.interpret_run_as_pull,
            )
        except ValueError as exc:
            self._pull_status.setText(f"Error: {exc}")
            return
        self._pull_status.setText(notice or f"Downloading '{model}'…")
        self._pull_button.setEnabled(False)
        self._pull_progress.setVisible(True)
        self._pull_progress.setRange(0, 0)

        provider = self._ollama()
        task = DownloadTask(partial(provider.pull, model))
        self._pull_tasks.append(task)

        def on_progress(completed: int, total: object) -> None:
            if isinstance(total, int) and total > 0:
                self._pull_progress.setRange(0, 100)
                self._pull_progress.setValue(int(completed * 100 / total))

        def cleanup() -> None:
            if task in self._pull_tasks:
                self._pull_tasks.remove(task)
            self._pull_button.setEnabled(True)
            self._pull_progress.setVisible(False)

        def on_finished() -> None:
            cleanup()
            self._pull_status.setText(f"'{model}' installed")
            self._pull_edit.clear()
            self._refresh_models()

        def on_failed(message: str) -> None:
            cleanup()
            self._pull_status.setText(f"Error: {message}")

        task.progress.connect(on_progress)
        task.finished.connect(on_finished)
        task.failed.connect(on_failed)
        task.start()

    def _model_changed(self) -> None:
        model_id = self._model_combo.currentData()
        if not model_id:
            return
        self._settings.llm.ollama.model = model_id
        self._on_change()
        provider = self._ollama()
        self._run_async(
            lambda: provider.model_info(model_id), self._details_loaded, self._models_failed
        )

    def _details_loaded(self, info: LlmModelInfo) -> None:
        parts = []
        if info.context_length:
            parts.append(f"max context: {info.context_length:,} tokens")
        if info.parameter_size:
            parts.append(f"parameters: {info.parameter_size}")
        if info.quantization:
            parts.append(f"quantization: {info.quantization}")
        self._model_details.setText(" · ".join(parts) or " ")
        if info.context_length:
            self._ctx_spin.setMaximum(info.context_length)

    # --- generation parameters ---

    def _params_group(self) -> QGroupBox:
        group = QGroupBox("Generation")
        form = QFormLayout(group)
        ollama = self._settings.llm.ollama

        self._ctx_spin = QSpinBox()
        self._ctx_spin.setRange(512, 1_048_576)
        self._ctx_spin.setSingleStep(1024)
        self._ctx_spin.setValue(ollama.num_ctx)
        self._ctx_spin.setToolTip(
            "How much text (in tokens, ~4 characters each) the model can consider at "
            "once — your command + selection + answer must fit. Higher values support "
            "editing longer selections but use more memory."
        )
        self._ctx_spin.valueChanged.connect(self._params_changed)
        form.addRow("Context window (num_ctx):", self._ctx_spin)

        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.1)
        self._temp_spin.setValue(ollama.temperature)
        self._temp_spin.setToolTip(
            "Creativity dial: 0 = predictable and faithful, 1+ = more inventive. "
            "Keep it low (0.2) so edits change only what you asked for."
        )
        self._temp_spin.valueChanged.connect(self._params_changed)
        form.addRow("Temperature:", self._temp_spin)

        self._keep_edit = QLineEdit(ollama.keep_alive)
        self._keep_edit.setPlaceholderText("e.g. 10m, 1h, -1 (forever)")
        self._keep_edit.setToolTip(
            "How long Ollama keeps the model in (V)RAM after an edit. Longer = the "
            "next edit starts instantly; '-1' keeps it loaded forever. After it "
            "unloads, the next edit pays a few seconds of loading time."
        )
        self._keep_edit.editingFinished.connect(self._params_changed)
        form.addRow("Keep model loaded:", self._keep_edit)
        return group

    def _params_changed(self) -> None:
        ollama = self._settings.llm.ollama
        ollama.num_ctx = self._ctx_spin.value()
        ollama.temperature = round(self._temp_spin.value(), 2)
        ollama.keep_alive = self._keep_edit.text().strip() or "10m"
        self._on_change()

    # --- helpers ---

    def _run_async(self, work, on_done, on_failed) -> None:
        task = AsyncTask(work)
        self._tasks.append(task)
        task.done.connect(on_done)
        task.failed.connect(on_failed)
        cleanup = lambda *args: self._tasks.remove(task) if task in self._tasks else None  # noqa: E731
        task.done.connect(cleanup)
        task.failed.connect(cleanup)
        task.start()
