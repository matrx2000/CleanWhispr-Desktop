import pytest

from cleanwispr.llm import hardware
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.setup_wizard import SetupWizard, _pretty_combo


@pytest.fixture(autouse=True)
def _stub_hardware(monkeypatch):
    # the engine page kicks off hardware detection on show; keep it instant and
    # deterministic instead of spawning real nvidia-smi/lspci probes on a thread
    cpu = hardware.Hardware("cpu", "Test CPU", None, 16.0)
    monkeypatch.setattr(hardware, "detect", lambda: cpu)


def _make(qtbot, settings=None, on_change=None):
    wizard = SetupWizard(settings or Settings(), on_change or (lambda: None))
    qtbot.addWidget(wizard)
    return wizard


def test_pages_and_navigation(qtbot):
    wizard = _make(qtbot)
    assert wizard._pages.count() == 5
    assert wizard._pages.currentIndex() == 0
    assert not wizard._back_button.isVisibleTo(wizard)

    wizard._go(+1)
    assert wizard._pages.currentIndex() == 1
    wizard._go(-1)
    assert wizard._pages.currentIndex() == 0
    wizard._go(-1)  # can't go below the first page
    assert wizard._pages.currentIndex() == 0


def test_engine_page_gates_next_until_installed(qtbot, monkeypatch):
    from cleanwispr.stt import registry

    monkeypatch.setattr(registry, "is_server_installed", lambda *_: False)
    monkeypatch.setattr(registry, "is_model_installed", lambda *_: False)
    wizard = _make(qtbot)
    wizard._go(+1)  # engine page
    assert not wizard._next_button.isEnabled()

    monkeypatch.setattr(registry, "is_server_installed", lambda *_: True)
    monkeypatch.setattr(registry, "is_model_installed", lambda *_: True)
    wizard._update_engine_state()
    assert wizard._next_button.isEnabled()


def test_engine_choice_applies_to_settings(qtbot):
    settings = Settings()
    changes = []
    wizard = _make(qtbot, settings, lambda: changes.append(True))
    wizard._parakeet_card.radio.setChecked(True)
    wizard._apply_engine_choice()
    assert settings.stt.engine == "parakeet"
    assert changes


def test_finish_accepts_and_saves(qtbot):
    changes = []
    wizard = _make(qtbot, on_change=lambda: changes.append(True))
    wizard._show_page(4)
    wizard._go(+1)  # Finish
    assert wizard.result() == SetupWizard.DialogCode.Accepted
    assert changes


def test_pretty_combo():
    assert _pretty_combo("ctrl+super") == "Ctrl + Win"
    assert _pretty_combo("f9") == "F9"
