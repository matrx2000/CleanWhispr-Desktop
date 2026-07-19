from cleanwispr.llm.hardware import Hardware, detect, recommended_ollama_model


def _rec(kind, vram=None, ram=None):
    model, reason = recommended_ollama_model(Hardware(kind, "test", vram, ram))
    assert reason  # every tier explains itself
    return model


def test_nvidia_tiers():
    assert _rec("nvidia", vram=24) == "gemma4:31b"
    assert _rec("nvidia", vram=16) == "gemma4:26b"
    assert _rec("nvidia", vram=12) == "gemma4:12b"
    assert _rec("nvidia", vram=8) == "gemma3:4b"
    assert _rec("nvidia", vram=4) == "gemma3:1b"


def test_apple_tiers():
    assert _rec("apple", ram=64) == "gemma4:31b"
    assert _rec("apple", ram=48) == "gemma4:26b"
    assert _rec("apple", ram=32) == "gemma4:12b"
    assert _rec("apple", ram=16) == "gemma3:4b"
    assert _rec("apple", ram=8) == "gemma3:1b"


def test_amd_is_conservative():
    assert _rec("amd", ram=64) == "gemma3:4b"


def test_cpu_by_ram():
    assert _rec("cpu", ram=32) == "gemma3:4b"
    assert _rec("cpu", ram=8) == "gemma3:1b"
    assert _rec("cpu") == "gemma3:1b"  # unknown RAM stays safe


def test_detect_never_raises():
    hardware = detect()  # runs real probes; must degrade gracefully anywhere
    assert hardware.kind in ("nvidia", "amd", "apple", "cpu")
