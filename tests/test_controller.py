import numpy as np
import pytest

from cleanwispr.audio.gate import GateDecision
from cleanwispr.core.controller import AppState, Controller
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from cleanwispr.stt.base import TranscriptionResult


class FakeRecorder:
    def __init__(self, decision=None):
        self.decision = decision or GateDecision(skip=False, reason="speech_detected")
        self.started = False
        self.aborted = False

    deliver_frames = True  # mimic a mic that comes alive immediately

    def start(self, device_name=None, on_level=None, on_first_frame=None):
        self.started = True
        if self.deliver_frames and on_first_frame:
            on_first_frame()

    def stop(self):
        self.started = False
        return np.ones(16000, dtype=np.int16), self.decision

    def abort(self):
        self.started = False
        self.aborted = True


class FakeEngine:
    def __init__(self, text="hello world"):
        self.text = text

    def ensure(self, model_id, language="auto", gpu="auto"):
        pass

    def transcribe(self, pcm, *, language="auto", initial_prompt=None):
        return TranscriptionResult(text=self.text, language=language, duration_ms=1000)

    def stop(self):
        pass


class FakeInjector:
    def __init__(self):
        self.injected = []

    def inject(self, text, *, restore_clipboard=True):
        self.injected.append(text)

    def capture_selection(self):
        return None


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


def make_controller(db, recorder=None, engine=None, injector=None, settings=None):
    return Controller(
        settings or Settings(),
        db,
        recorder or FakeRecorder(),
        engine or FakeEngine(),
        injector or FakeInjector(),
    )


def test_full_dictation_pipeline(qtbot, db):
    injector = FakeInjector()
    c = make_controller(db, injector=injector)

    c.toggle_dictation()
    assert c.state is AppState.RECORDING

    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.toggle_dictation()

    assert c.state is AppState.IDLE
    assert injector.injected == ["hello world"]
    entries = db.list()
    assert len(entries) == 1
    assert entries[0].text == "hello world"
    assert entries[0].kind == "dictation"
    c.shutdown()


def test_dictation_skips_history_when_disabled(qtbot, db):
    settings = Settings()
    settings.history.enabled = False
    injector = FakeInjector()
    c = make_controller(db, injector=injector, settings=settings)

    c.toggle_dictation()
    c.toggle_dictation()
    qtbot.waitUntil(lambda: c.state is AppState.IDLE, timeout=5000)

    assert injector.injected == ["hello world"]  # injection still happens
    assert db.list() == []  # nothing persisted
    c.shutdown()


def test_silence_gate_skips_transcription(qtbot, db):
    recorder = FakeRecorder(decision=GateDecision(skip=True, reason="silence"))
    injector = FakeInjector()
    c = make_controller(db, recorder=recorder, injector=injector)

    c.toggle_dictation()
    with qtbot.waitSignal(c.notice, timeout=1000):
        c.toggle_dictation()

    assert c.state is AppState.IDLE
    assert injector.injected == []
    assert db.count() == 0
    c.shutdown()


def test_empty_recording_is_dropped_with_notice(qtbot, db):
    class EmptyRecorder(FakeRecorder):
        def stop(self):
            from cleanwispr.audio.gate import GateDecision

            return np.zeros(0, dtype=np.int16), GateDecision(skip=False, reason="unavailable")

    injector = FakeInjector()
    c = make_controller(db, recorder=EmptyRecorder(), injector=injector)
    c.toggle_dictation()
    with qtbot.waitSignal(c.notice, timeout=1000) as blocker:
        c.toggle_dictation()
    assert "microphone" in blocker.args[0].lower()
    assert c.state is AppState.IDLE
    assert injector.injected == []
    assert db.count() == 0
    c.shutdown()


def test_dead_mic_watchdog_aborts(qtbot, db):
    recorder = FakeRecorder()
    recorder.deliver_frames = False  # mic never produces audio
    c = make_controller(db, recorder=recorder)
    c._mic_watchdog.setInterval(50)
    c.toggle_dictation()
    assert c.state is AppState.RECORDING
    with qtbot.waitSignal(c.notice, timeout=2000) as blocker:
        pass  # watchdog fires on its own
    assert "Microphone produced no audio" in blocker.args[0]
    assert c.state is AppState.IDLE
    assert recorder.aborted
    c.shutdown()


def test_cancel_aborts_recording(qtbot, db):
    recorder = FakeRecorder()
    c = make_controller(db, recorder=recorder)
    c.toggle_dictation()
    c.cancel()
    assert c.state is AppState.IDLE
    assert recorder.aborted
    assert db.count() == 0
    c.shutdown()


def test_engine_dispatch_by_setting(qtbot, db):
    class NamedEngine(FakeEngine):
        def __init__(self, text):
            super().__init__(text=text)
            self.ensured = []

        def ensure(self, model_id, language="auto", gpu="auto"):
            self.ensured.append(model_id)

    whisper = NamedEngine("from whisper")
    parakeet = NamedEngine("from parakeet")
    settings = Settings()
    settings.stt.engine = "parakeet"
    c = Controller(
        settings, db, FakeRecorder(), {"whisper": whisper, "parakeet": parakeet}, FakeInjector()
    )
    c.toggle_dictation()
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.toggle_dictation()
    entry = db.list()[0]
    assert entry.text == "from parakeet"
    assert entry.engine == "parakeet:parakeet-tdt-0.6b-v3"
    assert parakeet.ensured == ["parakeet-tdt-0.6b-v3"]
    assert whisper.ensured == []
    c.shutdown()


def test_engine_failure_surfaces_error(qtbot, db):
    class BrokenEngine(FakeEngine):
        def ensure(self, model_id, language="auto", gpu="auto"):
            from cleanwispr.stt.base import SttError

            raise SttError("model not installed")

    c = make_controller(db, engine=BrokenEngine())
    c.toggle_dictation()
    with qtbot.waitSignal(c.error_occurred, timeout=5000) as blocker:
        c.toggle_dictation()
    assert "model not installed" in blocker.args[0]
    assert c.state is AppState.IDLE
    c.shutdown()
