"""Editor (LLM) settings: Ollama connection, auto-discovered model list,
context window and generation parameters."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.llm import hardware
from cleanwispr.llm import server as ollama_server
from cleanwispr.llm.base import LlmModelInfo
from cleanwispr.llm.ollama import OllamaProvider, parse_pull_command
from cleanwispr.storage.settings import Settings
from cleanwispr.ui import theme
from cleanwispr.ui.tasks import AsyncTask, DownloadTask
from cleanwispr.ui.widgets import LabeledToggle, ModelRow, intro_label


class EditorTab(QWidget):
    def __init__(self, settings: Settings, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._settings = settings
        self._on_change = on_change
        self._tasks: list[AsyncTask] = []
        self._pull_tasks: list[DownloadTask] = []
        self._catalog_rows: dict[str, ModelRow] = {}
        self._catalog_by_id: dict[str, object] = {}
        self._catalog_dl: dict[str, DownloadTask] = {}  # active catalog downloads by id
        self._catalog_cancelled: set[str] = set()
        self._manual_task: DownloadTask | None = None  # active manual (by-name) pull
        self._manual_cancelled = False
        self._installed_ids: set[str] = set()

        layout = QVBoxLayout(self)
        layout.addWidget(intro_label(
            "The voice editor rewrites text by voice: select text in any app, press "
            "the editor hotkey, and speak a command like “make this formal” — a local "
            "AI model applies it and the result replaces your selection. With nothing "
            "selected, it writes new text from your command. Requires Ollama "
            "(free, ollama.com) running on this PC."
        ))
        layout.addWidget(self._connection_group())
        layout.addWidget(self._recommend_group())
        layout.addWidget(self._catalog_group())
        layout.addWidget(self._model_group())
        layout.addWidget(self._pull_group())
        layout.addWidget(self._params_group())
        layout.addStretch()
        self._refresh_models()
        self._detect_hardware()

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

    # --- hardware-aware recommendation ---

    def _recommend_group(self) -> QGroupBox:
        group = QGroupBox("Recommended for your PC")
        layout = QVBoxLayout(group)
        self._recommend_label = QLabel("Checking your hardware…")
        self._recommend_label.setWordWrap(True)
        self._recommend_label.setStyleSheet(f"color: {theme.MUTED};")
        layout.addWidget(self._recommend_label)

        row = QHBoxLayout()
        self._best_button = QPushButton("Best quality")
        self._best_button.setToolTip("The most capable model that fits your hardware.")
        self._best_button.setEnabled(False)
        self._best_button.clicked.connect(lambda: self._install_recommended("quality"))
        row.addWidget(self._best_button)
        self._small_button = QPushButton("Smallest usable")
        self._small_button.setToolTip("A lighter, faster model that still edits well.")
        self._small_button.setEnabled(False)
        self._small_button.clicked.connect(lambda: self._install_recommended("small"))
        row.addWidget(self._small_button)
        row.addStretch()
        layout.addLayout(row)
        return group

    def _detect_hardware(self) -> None:
        provider = self._ollama()
        if not provider.supports_install:
            self._recommend_label.setText(
                "This provider doesn't support in-app downloads — install models "
                "in the tool itself."
            )
            return
        self._run_async(hardware.detect, self._hardware_detected, self._hardware_failed)

    def _hardware_detected(self, result: object) -> None:
        self._hardware = result if isinstance(result, hardware.Hardware) else None
        catalog = self._ollama().catalog()
        if not self._hardware or not catalog:
            self._hardware_failed("hardware unknown")
            return
        best, best_reason = hardware.recommend_from_catalog(catalog, self._hardware, "quality")
        small, _ = hardware.recommend_from_catalog(catalog, self._hardware, "small")
        self._best_id, self._small_id = best.id, small.id
        self._recommend_label.setText(
            f"Detected <b>{self._hardware.name}</b>.<br>"
            f"<b>Best quality:</b> {best.label} — {best_reason}.<br>"
            f"<b>Smallest usable:</b> {small.label} (≈{small.size_gb:.1f} GB) for speed."
        )
        self._best_button.setText(f"Best quality · {best.label}")
        self._small_button.setText(f"Smallest usable · {small.label}")
        self._best_button.setEnabled(True)
        self._small_button.setEnabled(True)

    def _hardware_failed(self, _message: str) -> None:
        self._hardware = None
        self._best_id = "gemma3:4b"
        self._small_id = "gemma3:1b"
        self._recommend_label.setText(
            "Couldn't inspect your hardware — <b>Gemma 3 4B</b> is a safe pick on "
            "most machines. Or choose one from the list below."
        )
        self._best_button.setText("Install Gemma 3 4B")
        self._best_button.setEnabled(True)
        self._small_button.setText("Install Gemma 3 1B")
        self._small_button.setEnabled(True)

    def _install_recommended(self, prefer: str) -> None:
        model_id = self._best_id if prefer == "quality" else self._small_id
        if model_id in self._installed_ids:
            self._select_model_id(model_id)
            self._pull_status.setText(f"'{model_id}' is already installed — now selected.")
            return
        row = self._catalog_rows.get(model_id)
        if row is not None:
            self._download_catalog_model(model_id)
        else:  # not in the catalog list for some reason — fall back to a direct pull
            self._pull_model(model_id)

    # --- catalog (guided install without the terminal) ---

    def _catalog_group(self) -> QGroupBox:
        group = QGroupBox("Find & install a model")
        group.setToolTip(
            "Search the model library and download any of them through Ollama — "
            "no terminal, no commands to remember."
        )
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(
            "Search models — e.g. llama, qwen, mistral, phi, deepseek…"
        )
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._filter_catalog)
        layout.addWidget(self._search_edit)

        self._catalog_hint = QLabel()
        self._catalog_hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._catalog_hint)

        for model in self._ollama().catalog():
            self._catalog_by_id[model.id] = model
            tag = "Recommended" if model.recommended else None
            row = ModelRow(
                model.label, f"{model.description} · {model.size_gb:.1f} GB", tag=tag
            )
            row.download_clicked.connect(partial(self._download_catalog_model, model.id))
            row.use_clicked.connect(partial(self._select_model_id, model.id))
            row.delete_clicked.connect(partial(self._delete_catalog_model, model.id))
            row.cancel_clicked.connect(partial(self._cancel_catalog_download, model.id))
            layout.addWidget(row)
            self._catalog_rows[model.id] = row
        self._filter_catalog("")
        return group

    def _filter_catalog(self, query: str) -> None:
        """Show catalog rows matching the search; with no query, show just the
        vetted recommendations to keep the list short."""
        query = query.strip()
        shown = 0
        for model_id, row in self._catalog_rows.items():
            model = self._catalog_by_id[model_id]
            visible = model.matches(query) if query else model.recommended
            row.setVisible(visible)
            shown += int(visible)
        total = len(self._catalog_rows)
        if query:
            self._catalog_hint.setText(
                f"{shown} of {total} models match — or type an exact name below to "
                "install anything in the Ollama library."
            )
        else:
            self._catalog_hint.setText(
                f"Showing {shown} recommended models. Search to browse all {total} — "
                "or install any other by name below."
            )

    def _refresh_catalog_rows(self) -> None:
        active = self._settings.llm.ollama.model
        for model_id, row in self._catalog_rows.items():
            installed = model_id in self._installed_ids
            row.set_state(installed, installed and model_id == active)

    def _download_catalog_model(self, model_id: str) -> None:
        row = self._catalog_rows[model_id]
        provider = self._ollama()
        task = DownloadTask(partial(provider.pull, model_id))
        self._pull_tasks.append(task)
        self._catalog_dl[model_id] = task
        self._catalog_cancelled.discard(model_id)
        row.set_busy(True)

        def cleanup() -> None:
            if task in self._pull_tasks:
                self._pull_tasks.remove(task)
            self._catalog_dl.pop(model_id, None)
            row.set_busy(False)

        def on_finished() -> None:
            cleanup()
            self._pull_status.setText(f"'{model_id}' installed")
            self._select_model_id(model_id)
            self._refresh_models()

        def on_failed(message: str) -> None:
            was_cancelled = model_id in self._catalog_cancelled
            self._catalog_cancelled.discard(model_id)
            cleanup()
            self._pull_status.setText(
                f"Cancelled '{model_id}'" if was_cancelled else f"Error: {message}"
            )

        task.progress.connect(row.set_progress)
        task.finished.connect(on_finished)
        task.failed.connect(on_failed)
        self._pull_status.setText(f"Downloading '{model_id}'…")
        task.start()

    def _cancel_catalog_download(self, model_id: str) -> None:
        """Row Cancel buttons are wired here once at creation (avoids reconnecting
        a signal on every download, which warns when nothing is connected)."""
        task = self._catalog_dl.get(model_id)
        if task is not None:
            self._catalog_cancelled.add(model_id)
            task.cancel()

    def _delete_catalog_model(self, model_id: str) -> None:
        if model_id == self._settings.llm.ollama.model:
            QMessageBox.information(
                self, "Model in use", "Select another model before deleting the active one."
            )
            return
        provider = self._ollama()
        self._run_async(
            lambda: provider.delete_model(model_id),
            lambda _r: self._refresh_models(),
            lambda msg: self._pull_status.setText(f"Error: {msg}"),
        )

    def _select_model_id(self, model_id: str) -> None:
        """Select a model by id, whether or not the combo has loaded it yet."""
        self._settings.llm.ollama.model = model_id
        self._on_change()
        index = self._model_combo.findData(model_id)
        if index >= 0:
            self._model_combo.setCurrentIndex(index)
        self._refresh_catalog_rows()

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

    def refresh(self) -> None:
        """Re-sync with settings and re-query installed models — the Settings
        window is built once at startup, so this runs each time it's shown to
        pick up a model the setup wizard (or anything else) just installed."""
        self._url_edit.setText(self._settings.llm.ollama.base_url)
        self._refresh_models()

    def _refresh_models(self) -> None:
        provider = self._ollama()
        self._run_async(provider.list_models, self._models_loaded, self._models_failed)

    def _models_loaded(self, models: list[LlmModelInfo]) -> None:
        self._installed_ids = {model.id for model in models}
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
        self._refresh_catalog_rows()
        if not models:
            self._status.setText("No models installed — pick one under “Install a model”.")
        elif index < 0 and saved:
            self._status.setText(f"Saved model '{saved}' is no longer installed")

    def _models_failed(self, message: str) -> None:
        self._status.setText(f"Error: {message}")
        if ollama_server.find_ollama_binary():
            self._start_button.setVisible(True)

    # --- install models from the app (no terminal needed) ---

    def _pull_group(self) -> QGroupBox:
        group = QGroupBox("Install any other model by name")
        group.setToolTip(
            "Anything in the Ollama library works — paste a command or an exact "
            "model name, even one not shown in the search list above."
        )
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

        self._run_as_pull_check = LabeledToggle(
            "Treat pasted 'ollama run …' commands as downloads"
        )
        self._run_as_pull_check.setChecked(self._settings.llm.ollama.interpret_run_as_pull)
        self._run_as_pull_check.toggled.connect(self._run_as_pull_changed)
        layout.addWidget(self._run_as_pull_check)

        progress_row = QHBoxLayout()
        self._pull_progress = QProgressBar()
        self._pull_progress.setVisible(False)
        progress_row.addWidget(self._pull_progress, 1)
        self._pull_cancel = QPushButton("Cancel")
        self._pull_cancel.setVisible(False)
        self._pull_cancel.clicked.connect(self._cancel_manual_pull)  # wired once
        progress_row.addWidget(self._pull_cancel)
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
        if notice:
            self._pull_status.setText(notice)
        self._pull_model(model, clear_edit=True)

    def _pull_model(self, model: str, *, clear_edit: bool = False) -> None:
        """Download a specific model id, showing progress + cancel in the manual
        pull row."""
        self._pull_status.setText(f"Downloading '{model}'…")
        self._pull_button.setEnabled(False)
        self._pull_cancel.setVisible(True)
        self._pull_progress.setVisible(True)
        self._pull_progress.setRange(0, 0)

        provider = self._ollama()
        task = DownloadTask(partial(provider.pull, model))
        self._pull_tasks.append(task)
        self._manual_task = task
        self._manual_cancelled = False

        def on_progress(completed: int, total: object) -> None:
            if isinstance(total, int) and total > 0:
                self._pull_progress.setRange(0, 100)
                self._pull_progress.setValue(int(completed * 100 / total))

        def cleanup() -> None:
            if task in self._pull_tasks:
                self._pull_tasks.remove(task)
            if self._manual_task is task:
                self._manual_task = None
            self._pull_button.setEnabled(True)
            self._pull_cancel.setVisible(False)
            self._pull_progress.setVisible(False)

        def on_finished() -> None:
            cleanup()
            self._pull_status.setText(f"'{model}' installed")
            if clear_edit:
                self._pull_edit.clear()
            self._select_model_id(model)
            self._refresh_models()

        def on_failed(message: str) -> None:
            was_cancelled = self._manual_cancelled
            cleanup()
            self._pull_status.setText("Cancelled" if was_cancelled else f"Error: {message}")

        task.progress.connect(on_progress)
        task.finished.connect(on_finished)
        task.failed.connect(on_failed)
        task.start()

    def _cancel_manual_pull(self) -> None:
        task = getattr(self, "_manual_task", None)
        if task is not None:
            self._manual_cancelled = True
            task.cancel()

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

        keep_tip = (
            "How long Ollama keeps the model in (V)RAM after an edit. Longer = the "
            "next edit starts instantly; 'Forever' never unloads it. After it "
            "unloads, the next edit pays a few seconds of loading time."
        )
        self._keep_value = QSpinBox()
        self._keep_value.setRange(1, 999)
        self._keep_value.setToolTip(keep_tip)
        self._keep_unit = QComboBox()
        for label, suffix in [
            ("Seconds", "s"),
            ("Minutes", "m"),
            ("Hours", "h"),
            ("Forever (never unload)", "-1"),
        ]:
            self._keep_unit.addItem(label, suffix)
        self._keep_unit.setToolTip(keep_tip)
        value, unit = self._parse_keep_alive(ollama.keep_alive)
        self._keep_value.setValue(value)
        self._keep_unit.setCurrentIndex(max(0, self._keep_unit.findData(unit)))
        self._keep_value.setEnabled(unit != "-1")
        self._keep_value.valueChanged.connect(self._params_changed)
        self._keep_unit.currentIndexChanged.connect(self._params_changed)
        keep_row = QHBoxLayout()
        keep_row.addWidget(self._keep_value)
        keep_row.addWidget(self._keep_unit, 1)
        form.addRow("Keep model loaded:", keep_row)
        return group

    @staticmethod
    def _parse_keep_alive(text: str) -> tuple[int, str]:
        """'10m' → (10, 'm'); '-1' → forever; anything unparseable → the default."""
        text = text.strip()
        if text == "-1":
            return 10, "-1"
        if len(text) > 1 and text[:-1].isdigit() and text[-1] in "smh":
            return int(text[:-1]), text[-1]
        return 10, "m"

    def _params_changed(self) -> None:
        ollama = self._settings.llm.ollama
        ollama.num_ctx = self._ctx_spin.value()
        ollama.temperature = round(self._temp_spin.value(), 2)
        unit = self._keep_unit.currentData()
        self._keep_value.setEnabled(unit != "-1")
        ollama.keep_alive = "-1" if unit == "-1" else f"{self._keep_value.value()}{unit}"
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
