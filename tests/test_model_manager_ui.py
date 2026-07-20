"""UI wiring for the model manager additions: catalog rows, the setup wizard's
Ollama-state gating, and the transcription tab's GPU-build recommendation.

Network/hardware probes are stubbed so these stay fast and hermetic.
"""

import pytest

from cleanwispr.llm import hardware
from cleanwispr.llm.base import LlmModelInfo
from cleanwispr.storage.settings import Settings


@pytest.fixture(autouse=True)
def _stub_probes(monkeypatch):
    # keep construction from spawning real nvidia-smi / localhost calls
    cpu = hardware.Hardware("cpu", "Test CPU", None, 16.0)
    monkeypatch.setattr(hardware, "detect", lambda: cpu)
    monkeypatch.setattr(
        "cleanwispr.llm.ollama.OllamaProvider.list_models", lambda self: []
    )


def _editor_tab(qtbot, settings=None):
    from cleanwispr.ui.settings.editor_tab import EditorTab

    tab = EditorTab(settings or Settings(), lambda: None)
    qtbot.addWidget(tab)
    return tab


def test_catalog_rows_reflect_installed_and_active(qtbot):
    settings = Settings()
    tab = _editor_tab(qtbot, settings)
    assert tab._catalog_rows  # the curated list rendered

    tab._models_loaded([LlmModelInfo("gemma3:4b", "gemma3:4b")])
    row = tab._catalog_rows["gemma3:4b"]
    assert row._installed is True
    assert row._active is False  # installed but not selected yet

    settings.llm.ollama.model = "gemma3:4b"
    tab._refresh_catalog_rows()
    assert tab._catalog_rows["gemma3:4b"]._active is True
    # a model that isn't installed stays not-installed
    assert tab._catalog_rows["gemma4:31b"]._installed is False


def test_search_filters_catalog_rows(qtbot):
    tab = _editor_tab(qtbot)
    # default view shows only recommended models
    visible_default = [mid for mid, r in tab._catalog_rows.items() if r.isVisibleTo(tab)]
    assert visible_default and all(
        tab._catalog_by_id[mid].recommended for mid in visible_default
    )

    tab._filter_catalog("qwen")
    visible = [mid for mid, r in tab._catalog_rows.items() if r.isVisibleTo(tab)]
    assert visible, "expected qwen matches"
    assert all("qwen" in mid for mid in visible)  # only qwen family shown

    tab._filter_catalog("mistral")
    assert any("mistral" in mid for mid, r in tab._catalog_rows.items() if r.isVisibleTo(tab))


def test_recommendation_buttons_populate_from_hardware(qtbot):
    tab = _editor_tab(qtbot)
    tab._hardware_detected(hardware.Hardware("nvidia", "RTX 4090", 24.0, 64.0))
    assert tab._best_button.isEnabled()
    assert tab._small_button.isEnabled()
    assert tab._best_id == "gemma4:31b"  # 24 GB VRAM → the top model
    assert tab._small_id == "gemma3:4b"  # smallest-usable floor


def test_wizard_gates_model_install_on_ollama_state(qtbot, monkeypatch):
    from cleanwispr.ui.setup_wizard import SetupWizard

    wizard = SetupWizard(Settings(), lambda: None)
    qtbot.addWidget(wizard)

    # the editor page isn't the current stacked page, so isVisibleTo() is always
    # False here — assert the explicit visibility flag via isHidden() instead
    monkeypatch.setattr(
        "cleanwispr.llm.server.find_ollama_binary", lambda: "/usr/bin/ollama"
    )
    wizard._set_ollama_ready(False)  # installed but stopped
    assert not wizard._best_button.isEnabled()
    assert not wizard._start_ollama_button.isHidden()  # offer to start it

    wizard._set_ollama_ready(True)  # running
    assert wizard._best_button.isEnabled()
    assert wizard._small_button.isEnabled()
    assert wizard._start_ollama_button.isHidden()

    monkeypatch.setattr("cleanwispr.llm.server.find_ollama_binary", lambda: None)
    wizard._set_ollama_ready(False)  # not installed at all
    assert not wizard._website_button.isHidden()  # offer the download link
    assert not wizard._best_button.isEnabled()


def test_wizard_offers_gpu_build_when_a_gpu_is_detected(qtbot, monkeypatch):
    import sys

    if sys.platform == "darwin":
        pytest.skip("macOS ships a single Metal build; no cuda/vulkan offer")
    from cleanwispr.ui.setup_wizard import SetupWizard

    wizard = SetupWizard(Settings(), lambda: None)
    qtbot.addWidget(wizard)
    wizard._whisper_card.radio.setChecked(True)

    # simulate detection completing with an NVIDIA GPU
    wizard._engine_hw = hardware.Hardware("nvidia", "RTX 4090", 24.0, 64.0)
    wizard._engine_hw_done = True
    wizard._gpu_variant = "cuda"
    wizard._refresh_gpu_section()
    assert not wizard._gpu_frame.isHidden()  # the GPU prompt is shown
    assert wizard._gpu_toggle.isChecked()  # pre-checked so users opt in by default

    # CPU-only machine: no GPU prompt, a note steers to a small model instead
    wizard._engine_hw = hardware.Hardware("cpu", "CPU", None, 16.0)
    wizard._gpu_variant = None
    wizard._refresh_gpu_section()
    assert wizard._gpu_frame.isHidden()
    assert not wizard._cpu_note.isHidden()


def test_transcription_tab_marks_recommended_gpu_build(qtbot):
    import sys

    if sys.platform == "darwin":
        pytest.skip("macOS ships a single build; no cuda/vulkan rows")
    from cleanwispr.ui.settings.transcription_tab import TranscriptionTab

    tab = TranscriptionTab(Settings(), lambda: None)
    qtbot.addWidget(tab)

    tab._hardware_detected(hardware.Hardware("nvidia", "RTX 4090", 24.0, 64.0))
    assert tab._engine_rows["cuda"]._tag_label.text() == "Recommended"
    assert not tab._engine_rows["cuda"]._tag_label.isHidden()  # shown
    assert tab._engine_rows["cpu"]._tag_label.text() == ""  # not the recommended one
