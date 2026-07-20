"""Notes sessions: dictation → notes_text_ready, AI take → notes_ai_ready.

Both terminate in a Qt signal to the Notes editor and NEVER inject into a
foreign app (unlike DICTATION/EDIT). Reuses the fakes from the other suites so
the tests pass with no Ollama/whisper installed.
"""

import pytest

from cleanwispr.core.controller import (
    NOTES_MODE_GENERATE,
    NOTES_MODE_SELECTION,
    NOTES_MODE_WHOLE,
    AppState,
    Controller,
)
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from tests.test_controller import FakeEngine, FakeInjector, FakeRecorder
from tests.test_editor_flow import FakeProvider


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


def make_controller(db, engine=None, injector=None):
    settings = Settings()
    settings.llm.ollama.model = "qwen2.5:7b"
    return Controller(
        settings, db, FakeRecorder(), engine or FakeEngine(), injector or FakeInjector()
    )


def test_notes_dictation_emits_signal_without_injecting(qtbot, db):
    injector = FakeInjector()
    c = make_controller(db, engine=FakeEngine(text="typed by voice"), injector=injector)
    texts: list[str] = []
    c.notes_text_ready.connect(texts.append)

    c.toggle_notes_dictation()
    assert c.state is AppState.RECORDING
    # history_changed is the final step (after the signal + state settle)
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.toggle_notes_dictation()

    assert texts == ["typed by voice"]
    assert injector.injected == []  # notes never inject into a foreign app
    assert c.state is AppState.IDLE
    entry = db.list()[0]
    assert entry.kind == "dictation"
    assert entry.text == "typed by voice"
    c.shutdown()


def _run_ai(qtbot, c, source, mode):
    results: list = []
    c.notes_ai_ready.connect(results.append)
    c.start_notes_ai(source, mode)
    assert c.state is AppState.RECORDING
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.notes_finish()
    assert c.state is AppState.IDLE
    return results[0]  # (result, mode)


def test_notes_ai_selection_edits_source(qtbot, db, monkeypatch):
    provider = FakeProvider(reply="Formal version.")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    injector = FakeInjector()
    c = make_controller(db, engine=FakeEngine(text="make it formal"), injector=injector)

    result, mode = _run_ai(qtbot, c, "hey there", NOTES_MODE_SELECTION)

    assert result == "Formal version."
    assert mode == NOTES_MODE_SELECTION
    assert injector.injected == []  # result goes to the editor via the signal
    messages, _ = provider.calls[0]
    assert "<<<TEXT>>>" in messages[1].content  # selection edit prompt
    assert "hey there" in messages[1].content
    entry = db.list()[0]
    assert entry.kind == "edit"
    assert entry.source_text == "hey there"
    c.shutdown()


def test_notes_ai_whole_note_uses_note_prompt(qtbot, db, monkeypatch):
    provider = FakeProvider(reply="# Note\n\nRevised.")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    c = make_controller(db, engine=FakeEngine(text="add a summary heading"))

    result, mode = _run_ai(qtbot, c, "# Note\n\nsome body", NOTES_MODE_WHOLE)

    assert mode == NOTES_MODE_WHOLE
    assert result == "# Note\n\nRevised."
    messages, _ = provider.calls[0]
    assert "<<<NOTE>>>" in messages[1].content  # whole-note prompt, not selection
    assert "some body" in messages[1].content
    c.shutdown()


def test_notes_ai_generate_on_empty_note(qtbot, db, monkeypatch):
    provider = FakeProvider(reply="Fresh content.")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    c = make_controller(db, engine=FakeEngine(text="write a haiku about rain"))

    result, mode = _run_ai(qtbot, c, "", NOTES_MODE_GENERATE)

    assert mode == NOTES_MODE_GENERATE
    assert result == "Fresh content."
    messages, _ = provider.calls[0]
    assert "<<<TEXT>>>" not in messages[1].content  # generation, no data block
    assert "<<<NOTE>>>" not in messages[1].content
    c.shutdown()
