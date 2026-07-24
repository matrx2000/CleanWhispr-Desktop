"""Controller + live typing: preview streams during recording, the final
transcript corrects it in place, and classic paste remains the fallback."""

from __future__ import annotations

import numpy as np
import pytest

import cleanwispr.core.controller as controller_module
from cleanwispr.audio.gate import GateDecision
from cleanwispr.core.controller import AppState, Controller
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from cleanwispr.stt.base import TranscriptionResult
from cleanwispr.stt.live import LiveTranscriber

SPEECH = np.full(16000, 2000, dtype=np.int16)  # loud enough for the speech gate


class LiveRecorder:
    def __init__(self):
        self.recording = False

    def start(self, device_name=None, on_level=None, on_first_frame=None):
        self.recording = True
        if on_first_frame:
            on_first_frame()

    def snapshot(self):
        return SPEECH

    def stop(self):
        self.recording = False
        return SPEECH, GateDecision(skip=False, reason="speech_detected")

    def abort(self):
        self.recording = False


class LiveEngine:
    """Same hypothesis every preview cycle; a distinct final transcript."""

    def __init__(self, preview="hello world", final="Hello, world!"):
        self.preview = preview
        self.final = final
        self.calls = 0

    def ensure(self, model_id, language="auto", gpu="auto"):
        pass

    def transcribe(self, pcm, *, language="auto", initial_prompt=None):
        self.calls += 1
        return TranscriptionResult(text=self.preview, language=language, duration_ms=1000)

    def stop(self):
        pass


class LiveInjector:
    supports_live_typing = True

    def __init__(self):
        self.screen = ""
        self.injected = []
        self.copied = None
        self.terminal = False

    def inject(self, text, *, restore_clipboard=True):
        self.injected.append(text)

    def capture_selection(self):
        return None

    def copy_text(self, text):
        self.copied = text

    def focus_token(self):
        return 42

    def focus_is_terminal(self):
        return self.terminal

    def modifiers_held(self):
        return False

    def type_text(self, text):
        self.screen += text

    def delete_chars(self, count):
        self.screen = self.screen[: len(self.screen) - count]


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


@pytest.fixture(autouse=True)
def fast_preview(monkeypatch):
    """Preview cycles every 20 ms so tests don't wait on the real cadence."""
    real = LiveTranscriber

    def fast(snapshot, transcribe, sink):
        return real(snapshot, transcribe, sink, min_interval_s=0.02)

    monkeypatch.setattr(controller_module, "LiveTranscriber", fast)


def make(db, engine=None, injector=None):
    injector = injector or LiveInjector()
    controller = Controller(Settings(), db, LiveRecorder(), engine or LiveEngine(), injector)
    return controller, injector


def test_live_preview_types_and_final_corrects(qtbot, db):
    engine = LiveEngine(preview="hello world", final="hello world")
    # the engine returns the same text for previews and the final pass here;
    # the reconcile-difference case is covered below and in test_live.py
    controller, injector = make(db, engine=engine)

    controller.toggle_dictation()
    assert controller.state is AppState.RECORDING
    # "world" stays hot (last word of the hypothesis) during the preview
    qtbot.waitUntil(lambda: injector.screen == "hello", timeout=5000)

    with qtbot.waitSignal(controller.history_changed, timeout=5000):
        controller.toggle_dictation()

    assert injector.screen == "hello world"  # reconcile delivered the tail
    assert injector.injected == []  # live path used — no clipboard paste
    assert db.list()[0].text == "hello world"
    controller.shutdown()


def test_live_preview_reconciles_final_revision(qtbot, db):
    class TwoPhaseEngine(LiveEngine):
        def transcribe(self, pcm, *, language="auto", initial_prompt=None):
            self.calls += 1
            # previews hear "hello wold"; the full-take pass fixes it
            text = "hello wold" if len(pcm) and self.calls < 100 else self.final
            return TranscriptionResult(text=text, language=language, duration_ms=1000)

    engine = TwoPhaseEngine(final="hello world")
    controller, injector = make(db, engine=engine)
    controller.toggle_dictation()
    qtbot.waitUntil(lambda: injector.screen == "hello", timeout=5000)
    engine.calls = 1000  # flip the engine to its final answer
    with qtbot.waitSignal(controller.history_changed, timeout=5000):
        controller.toggle_dictation()
    assert injector.screen == "hello world"
    assert injector.injected == []
    assert db.list()[0].text == "hello world"
    controller.shutdown()


def test_live_preview_falls_back_to_paste_when_nothing_typed(qtbot, db):
    class SilentPreview(LiveEngine):
        def transcribe(self, pcm, *, language="auto", initial_prompt=None):
            # previews return nothing; the final pass hears the text
            done = not controller._recorder.recording
            return TranscriptionResult(
                text="hello world" if done else "", language=language, duration_ms=1000
            )

    engine = SilentPreview()
    controller, injector = make(db, engine=engine)
    controller.toggle_dictation()
    qtbot.wait(100)  # a few preview cycles that commit nothing
    with qtbot.waitSignal(controller.history_changed, timeout=5000):
        controller.toggle_dictation()
    assert injector.injected == ["hello world"]  # classic paste fallback
    assert injector.screen == ""
    controller.shutdown()


def test_live_typing_skipped_in_terminals(qtbot, db):
    injector = LiveInjector()
    injector.terminal = True
    controller, injector = make(db, injector=injector)
    controller.toggle_dictation()
    qtbot.wait(120)
    assert injector.screen == ""  # no preview typed into a terminal
    with qtbot.waitSignal(controller.history_changed, timeout=5000):
        controller.toggle_dictation()
    assert injector.injected == ["hello world"]  # classic paste instead
    controller.shutdown()


def test_live_typing_disabled_by_setting(qtbot, db):
    controller, injector = make(db)
    controller.settings.stt.live_typing = False
    controller.toggle_dictation()
    qtbot.wait(120)
    assert injector.screen == ""
    with qtbot.waitSignal(controller.history_changed, timeout=5000):
        controller.toggle_dictation()
    assert injector.injected == ["hello world"]
    controller.shutdown()


def test_cancel_rolls_back_preview(qtbot, db):
    controller, injector = make(db)
    controller.toggle_dictation()
    qtbot.waitUntil(lambda: injector.screen == "hello", timeout=5000)
    controller.cancel()
    qtbot.waitUntil(lambda: injector.screen == "", timeout=5000)
    assert controller.state is AppState.IDLE
    controller.shutdown()
