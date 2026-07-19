"""Controller toggle vs push-to-hold semantics (hotkey_pressed/hotkey_released)."""

import pytest

from cleanwispr.core import controller as controller_module
from cleanwispr.core.controller import AppState, Controller
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import ActivationMode, Settings
from tests.test_controller import FakeEngine, FakeInjector, FakeRecorder


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


def make_controller(db, mode: ActivationMode):
    settings = Settings()
    settings.hotkeys.dictation.mode = mode
    return Controller(settings, db, FakeRecorder(), FakeEngine(), FakeInjector())


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(controller_module.time, "monotonic", clock)
    return clock


def test_toggle_mode_ignores_release(qtbot, db, clock):
    c = make_controller(db, ActivationMode.TOGGLE)
    c.hotkey_pressed("dictation")
    assert c.state is AppState.RECORDING
    clock.now += 5
    c.hotkey_released("dictation")
    assert c.state is AppState.RECORDING  # toggle: release does nothing
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.hotkey_pressed("dictation")
    assert c.state is AppState.IDLE
    c.shutdown()


def test_hold_mode_stops_on_release(qtbot, db, clock):
    c = make_controller(db, ActivationMode.HOLD)
    c.hotkey_pressed("dictation")
    assert c.state is AppState.RECORDING
    clock.now += 2.0  # held well past the tap latch
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.hotkey_released("dictation")
    assert c.state is AppState.IDLE
    c.shutdown()


def test_hold_mode_quick_tap_latches(qtbot, db, clock):
    c = make_controller(db, ActivationMode.HOLD)
    c.hotkey_pressed("dictation")
    clock.now += 0.1  # released before the 0.3s latch threshold
    c.hotkey_released("dictation")
    assert c.state is AppState.RECORDING  # tap latched recording on
    with qtbot.waitSignal(c.history_changed, timeout=5000):
        c.hotkey_pressed("dictation")  # second press stops
    assert c.state is AppState.IDLE
    c.shutdown()
