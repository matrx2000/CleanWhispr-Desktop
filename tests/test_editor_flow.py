"""Editor session end-to-end with fake STT/LLM/injector."""

import pytest

from cleanwispr.core.controller import AppState, Controller
from cleanwispr.llm.base import LlmProviderError
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from tests.test_controller import FakeEngine, FakeInjector, FakeRecorder


class FakeProvider:
    def __init__(self, reply="Edited result.", loaded=True):
        self.reply = reply
        self.loaded = loaded
        self.calls = []
        self.load_calls = []

    def is_model_loaded(self, model_id):
        return self.loaded

    def load_model(self, model_id, keep_alive="10m"):
        self.load_calls.append(model_id)
        self.loaded = True

    thinking = None  # set to a string to simulate a reasoning model

    def is_available(self):
        return True

    def chat(self, messages, options, on_thinking=None):
        self.calls.append((messages, options))
        if self.thinking and on_thinking:
            on_thinking(self.thinking)
        yield self.reply


class SelectionInjector(FakeInjector):
    def __init__(self, selection="original text"):
        super().__init__()
        self.selection = selection

    def capture_selection(self):
        return self.selection


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


def make_editor_controller(db, provider, injector):
    settings = Settings()
    settings.llm.ollama.model = "qwen2.5:7b"
    controller = Controller(
        settings, db, FakeRecorder(), FakeEngine(text="make it formal"), injector
    )
    return controller


def run_editor_session(qtbot, controller, expect_signal):
    controller.toggle_editor()
    assert controller.state is AppState.RECORDING
    with qtbot.waitSignal(expect_signal(controller), timeout=5000):
        controller.toggle_editor()


def test_editor_narrates_progress(qtbot, db, monkeypatch):
    provider = FakeProvider()
    injector = SelectionInjector("some text")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)

    c = make_editor_controller(db, provider, injector)
    statuses = []
    c.edit_status.connect(statuses.append)
    run_editor_session(qtbot, c, lambda c: c.history_changed)

    joined = " | ".join(statuses)
    assert "Transcribing your command" in joined
    assert "“make it formal”" in joined  # the recognized command is shown
    assert "ready — thinking" in joined  # warm model: no load phase
    assert "writing" in joined  # generation progress
    assert "Pasting result" in joined
    c.shutdown()


def test_cold_model_shows_loading_and_waits(qtbot, db, monkeypatch):
    provider = FakeProvider(loaded=False)
    injector = SelectionInjector("some text")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)

    c = make_editor_controller(db, provider, injector)
    statuses = []
    c.edit_status.connect(statuses.append)
    run_editor_session(qtbot, c, lambda c: c.history_changed)

    joined = " | ".join(statuses)
    assert "Loading qwen2.5:7b into memory" in joined  # explicit load feedback
    assert "loaded — thinking" in joined  # the app waited for the load
    assert provider.load_calls == ["qwen2.5:7b"]  # load happened before chat
    assert injector.injected == ["Edited result."]
    c.shutdown()


def test_dictation_does_not_narrate(qtbot, db):
    c = make_editor_controller(db, FakeProvider(), SelectionInjector())
    statuses = []
    c.edit_status.connect(statuses.append)
    c.toggle_dictation()
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.toggle_dictation()
    assert statuses == []  # narration is editor-only
    c.shutdown()


def test_thinking_model_narrates_reasoning(qtbot, db, monkeypatch):
    provider = FakeProvider()
    provider.thinking = "The user wants a formal tone, so I will rephrase the greeting"
    injector = SelectionInjector("some text")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)

    c = make_editor_controller(db, provider, injector)
    statuses = []
    thoughts = []
    c.edit_status.connect(statuses.append)
    c.edit_thinking.connect(thoughts.append)
    run_editor_session(qtbot, c, lambda c: c.history_changed)

    assert any(s.startswith("💭") for s in statuses)  # compact pill status
    joined_thoughts = "".join(thoughts)
    assert "**Command:** make it formal" in joined_thoughts  # what was sent...
    assert "> some text" in joined_thoughts  # ...including the captured selection
    assert "rephrase the greeting" in joined_thoughts  # full reasoning to the panel
    assert injector.injected == ["Edited result."]  # thinking never leaks into the paste
    c.shutdown()


def test_edit_with_selection(qtbot, db, monkeypatch):
    provider = FakeProvider()
    injector = SelectionInjector("dear sir stuff")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)

    c = make_editor_controller(db, provider, injector)
    run_editor_session(qtbot, c, lambda c: c.history_changed)

    assert injector.injected == ["Edited result."]
    messages, options = provider.calls[0]
    assert "dear sir stuff" in messages[1].content  # selection went to the LLM
    assert "make it formal" in messages[1].content  # instruction went to the LLM
    assert options.model == "qwen2.5:7b"

    entry = db.list()[0]
    assert entry.kind == "edit"
    assert entry.instruction == "make it formal"
    assert entry.source_text == "dear sir stuff"
    assert entry.text == "Edited result."
    assert entry.llm_model == "ollama:qwen2.5:7b"
    c.shutdown()


def test_edit_without_selection_generates(qtbot, db, monkeypatch):
    provider = FakeProvider(reply="Generated text.")
    injector = SelectionInjector(selection=None)
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)

    c = make_editor_controller(db, provider, injector)
    run_editor_session(qtbot, c, lambda c: c.history_changed)

    assert injector.injected == ["Generated text."]
    messages, _ = provider.calls[0]
    assert "<<<TEXT>>>" not in messages[1].content  # generate mode, no data block
    entry = db.list()[0]
    assert entry.kind == "edit"
    assert entry.source_text is None
    c.shutdown()


def test_provider_error_surfaces(qtbot, db, monkeypatch):
    def broken_factory(llm):
        raise LlmProviderError("Ollama is not reachable")

    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", broken_factory)
    injector = SelectionInjector()
    c = make_editor_controller(db, FakeProvider(), injector)

    c.toggle_editor()
    with qtbot.waitSignal(c.error_occurred, timeout=5000) as blocker:
        c.toggle_editor()
    assert "not reachable" in blocker.args[0]
    assert c.state is AppState.IDLE
    assert injector.injected == []
    assert db.count() == 0
    c.shutdown()
