"""Editor sessions with the skills layer active (voice switch + persona)."""

import pytest

from cleanwispr.core.controller import AppState, Controller
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from skillkit import MemorySkillStore, Skill, SkillLibrary
from tests.test_controller import FakeEngine, FakeRecorder
from tests.test_editor_flow import FakeProvider, SelectionInjector


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


def _library(active: bool) -> SkillLibrary:
    lib = SkillLibrary(MemorySkillStore())
    lib.set_enabled(True)
    lib.add(Skill(id="poet", name="Poet", body="Write with vivid imagery.", triggers=["poet"]))
    if active:
        lib.activate("poet")
    return lib


def _controller(db, provider, injector, skills, text="make it formal") -> Controller:
    settings = Settings()
    settings.llm.ollama.model = "qwen2.5:7b"
    return Controller(
        settings, db, FakeRecorder(), FakeEngine(text=text), injector, skills=skills
    )


def test_active_skill_reaches_the_model(qtbot, db, monkeypatch):
    provider = FakeProvider()
    injector = SelectionInjector("some text")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    c = _controller(db, provider, injector, _library(active=True))

    c.toggle_editor()
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.toggle_editor()

    messages, _ = provider.calls[0]
    assert "Write with vivid imagery." in messages[0].content  # persona in the prompt
    assert "STYLE SCOPE" in messages[0].content  # …scoped by the guardrail
    assert injector.injected == ["Edited result."]
    c.shutdown()


def test_inactive_skill_leaves_prompt_legacy(qtbot, db, monkeypatch):
    provider = FakeProvider()
    injector = SelectionInjector("some text")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    c = _controller(db, provider, injector, _library(active=False))

    c.toggle_editor()
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.toggle_editor()

    messages, _ = provider.calls[0]
    assert "<style>" not in messages[0].content  # no active skill → legacy prompt
    assert "<<<TEXT>>>" in messages[1].content
    c.shutdown()


def test_voice_switch_arms_skill_without_editing(qtbot, db, monkeypatch):
    provider = FakeProvider()
    injector = SelectionInjector("some text")
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    lib = _library(active=False)
    c = _controller(db, provider, injector, lib, text="switch to poet")

    c.toggle_editor()
    with qtbot.waitSignal(c.notice, timeout=5000) as blocker:
        c.toggle_editor()

    assert "Poet" in blocker.args[0]  # the switch is announced
    assert [s.id for s in lib.active_skills()] == ["poet"]  # armed
    assert provider.calls == []  # no LLM call ran
    assert injector.injected == []  # nothing pasted
    assert db.count() == 0  # a switch is not a history entry
    assert c.state is AppState.IDLE
    c.shutdown()


def test_voice_switch_disabled_when_voice_switching_off(qtbot, db, monkeypatch):
    provider = FakeProvider()
    injector = SelectionInjector(None)
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    lib = _library(active=False)
    lib.set_voice_switching(False)
    c = _controller(db, provider, injector, lib, text="switch to poet")

    c.toggle_editor()
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.toggle_editor()

    # with voice switching off, "switch to poet" is just a generation instruction
    assert provider.calls  # the LLM ran
    assert lib.active_skills() == []  # nothing armed
    c.shutdown()
