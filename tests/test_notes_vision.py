"""Notes AI takes forward selected images to vision models only."""

import pytest

from cleanwispr.core.controller import NOTES_MODE_SELECTION, Controller
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from tests.test_controller import FakeEngine, FakeInjector, FakeRecorder
from tests.test_editor_flow import FakeProvider


class VisionProvider(FakeProvider):
    def __init__(self, vision: bool):
        super().__init__(reply="A photo of a cat.")
        self._vision = vision

    def supports_vision(self, model_id):
        return self._vision


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


def _controller(db, provider, text="what is in this image") -> Controller:
    settings = Settings()
    settings.llm.ollama.model = "llava:7b"
    return Controller(settings, db, FakeRecorder(), FakeEngine(text=text), FakeInjector())


def test_images_sent_to_vision_model(qtbot, db, monkeypatch):
    provider = VisionProvider(vision=True)
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    c = _controller(db, provider)

    c.start_notes_ai("some caption", NOTES_MODE_SELECTION, images=["QkFTRTY0"])
    with qtbot.waitSignal(c.notes_ai_ready, timeout=5000):
        c.notes_finish()

    messages, _ = provider.calls[0]
    user = next(m for m in messages if m.role == "user")
    assert user.images == ["QkFTRTY0"]  # forwarded to the model
    c.shutdown()


def test_images_skipped_for_text_only_model(qtbot, db, monkeypatch):
    provider = VisionProvider(vision=False)
    monkeypatch.setattr("cleanwispr.llm.factory.create_provider", lambda llm: provider)
    c = _controller(db, provider)
    notices = []
    c.notice.connect(notices.append)

    c.start_notes_ai("some caption", NOTES_MODE_SELECTION, images=["QkFTRTY0"])
    with qtbot.waitSignal(c.notes_ai_ready, timeout=5000):
        c.notes_finish()

    messages, _ = provider.calls[0]
    user = next(m for m in messages if m.role == "user")
    assert user.images is None  # not sent to a text-only model
    assert any("skipped" in n for n in notices)  # and the user is told why
    c.shutdown()
