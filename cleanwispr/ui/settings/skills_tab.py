"""Skills settings tab — a thin CleanWispr host around skillkit's manager.

The editor UI, list, and persistence all live in `skillkit.qt.SkillsManager`;
this wrapper only supplies the two host-specific hooks: the installed Ollama
model list (for the per-skill model override) and a "Test skill" action that
runs the skill through the local model so the user can see it work.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from cleanwispr.llm import factory as llm_factory
from cleanwispr.llm.base import LlmModelInfo
from cleanwispr.llm.ollama import OllamaProvider
from cleanwispr.llm.prompts import build_edit_messages, clean_llm_output
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.tasks import AsyncTask
from cleanwispr.ui.widgets import intro_label
from skillkit.library import SkillLibrary
from skillkit.qt import SkillsBridge, SkillsManager

_SAMPLE_TEXT = "the meeting is at 3pm tomorrow, let me know if that works for you"
_SAMPLE_INSTRUCTION = "rewrite this"


class SkillsTab(QWidget):
    def __init__(
        self, settings: Settings, library: SkillLibrary, bridge: SkillsBridge
    ) -> None:
        super().__init__()
        self._settings = settings
        self._models: list[str] = []
        self._tasks: list[AsyncTask] = []

        layout = QVBoxLayout(self)
        layout.addWidget(
            intro_label(
                "Skills are reusable roles for the voice editor — “a formal editor”, "
                "“a witty poet”. Turn skills on, activate one or more, and speak your "
                "command as usual; the active skills shape the tone. Switch by voice "
                "(“switch to poet”, “plain” to clear) or from the tray menu."
            )
        )
        self._manager = SkillsManager(
            library,
            changed_signal=bridge.changed,
            model_choices=lambda: self._models,
            on_test=self._test_skill,
        )
        layout.addWidget(self._manager)
        self._refresh_models()

    def refresh(self) -> None:
        """Re-query installed models when the window is (re)opened."""
        self._refresh_models()

    def _ollama(self) -> OllamaProvider:
        return OllamaProvider(self._settings.llm.ollama.base_url)

    def _refresh_models(self) -> None:
        task = AsyncTask(self._ollama().list_models)
        self._tasks.append(task)

        def done(models: object) -> None:
            if isinstance(models, list):
                self._models = [m.id for m in models if isinstance(m, LlmModelInfo)]
            self._tasks.remove(task) if task in self._tasks else None

        def failed(_message: str) -> None:
            self._tasks.remove(task) if task in self._tasks else None

        task.done.connect(done)
        task.failed.connect(failed)
        task.start()

    def _test_skill(self, skill) -> None:
        """Run a sample edit through the local model with just this skill active,
        so the user sees the persona take effect (and that formatting holds)."""
        model = skill.model or self._settings.llm.ollama.model
        if not model:
            QMessageBox.information(
                self,
                "No model selected",
                "Pick a model in the Voice Editor tab (or set one on this skill) first.",
            )
            return
        options = llm_factory.chat_options(self._settings.llm)
        options.model = model
        if skill.temperature is not None:
            options.temperature = skill.temperature
        messages = build_edit_messages(_SAMPLE_INSTRUCTION, _SAMPLE_TEXT, [skill])

        def work() -> str:
            provider = self._ollama()
            chunks = list(provider.chat(messages, options))
            return clean_llm_output("".join(chunks))

        task = AsyncTask(work)
        self._tasks.append(task)
        box = QMessageBox(self)
        box.setWindowTitle(f"Testing “{skill.name}”")
        box.setText(f"Running the sample through {model}…")
        box.setStandardButtons(QMessageBox.StandardButton.Close)

        def done(result: object) -> None:
            box.setText(
                f"Sample: “{_SAMPLE_TEXT}”\n\nInstruction: “{_SAMPLE_INSTRUCTION}”\n\n"
                f"Result with “{skill.name}”:\n\n{result}"
            )
            self._tasks.remove(task) if task in self._tasks else None

        def failed(message: str) -> None:
            box.setText(f"Test failed: {message}")
            self._tasks.remove(task) if task in self._tasks else None

        task.done.connect(done)
        task.failed.connect(failed)
        task.start()
        box.exec()
