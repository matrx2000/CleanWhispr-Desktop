from cleanwispr.llm.hardware import (
    Hardware,
    detect,
    recommend_from_catalog,
    usable_memory_gb,
)
from cleanwispr.llm.ollama import OllamaProvider

CATALOG = OllamaProvider().catalog()


def _best(kind, vram=None, ram=None):
    model, reason = recommend_from_catalog(CATALOG, Hardware(kind, "test", vram, ram))
    assert reason  # every pick explains itself
    return model.id


def _small(kind, vram=None, ram=None):
    model, reason = recommend_from_catalog(
        CATALOG, Hardware(kind, "test", vram, ram), prefer="small"
    )
    assert reason
    return model.id


def test_nvidia_quality_scales_with_vram():
    assert _best("nvidia", vram=24) == "gemma4:31b"
    assert _best("nvidia", vram=16) == "gemma4:26b"
    assert _best("nvidia", vram=12) == "gemma4:12b"
    assert _best("nvidia", vram=8) == "qwen3:8b"
    assert _best("nvidia", vram=6) == "gemma3:4b"
    assert _best("nvidia", vram=2) == "gemma3:1b"


def test_small_tier_prefers_a_usable_floor_not_the_tiniest():
    # a big GPU could run 31B, but "smallest usable" targets the ~4B floor
    assert _small("nvidia", vram=24) == "gemma3:4b"
    # when even the floor doesn't fit, fall back to the absolute smallest
    assert _small("nvidia", vram=2) == "gemma3:1b"


def test_apple_uses_discounted_unified_memory():
    # 64 GB unified → 0.7 discount = 44.8 GB budget → the top model still fits
    assert _best("apple", ram=64) == "gemma4:31b"
    assert _best("apple", ram=8) in {"gemma3:1b", "gemma3:4b"}


def test_cpu_is_capped_so_it_never_recommends_a_crawling_giant():
    # 64 GB of RAM but no GPU: capped at the CPU ceiling, not a 31B model
    assert _best("cpu", ram=64) == "gemma3:4b"
    assert _best("cpu", ram=8) == "gemma3:1b"
    assert _best("cpu") == "gemma3:1b"  # unknown RAM stays safe


def test_amd_is_conservative():
    assert _best("amd", ram=64) == "gemma3:4b"


def test_usable_memory_prefers_vram_over_ram_on_nvidia():
    assert usable_memory_gb(Hardware("nvidia", "x", 12.0, 64.0)) == 12.0


def test_recommend_returns_a_catalog_model_across_profiles():
    profiles = [
        Hardware("nvidia", "x", 5.0, 32.0),
        Hardware("cpu", "x", None, 16.0),
        Hardware("apple", "x", None, 24.0),
        Hardware("cpu", "x", None, None),  # nothing known
    ]
    for hw in profiles:
        for prefer in ("quality", "small"):
            model, reason = recommend_from_catalog(CATALOG, hw, prefer=prefer)
            assert model in CATALOG
            assert reason


def test_catalog_is_well_formed():
    ids = [m.id for m in CATALOG]
    assert len(ids) == len(set(ids))  # no duplicate ids
    for model in CATALOG:
        assert model.size_gb > 0
        assert model.min_memory_gb > 0
        assert model.label and model.description


def test_detect_never_raises():
    hardware = detect()  # runs real probes; must degrade gracefully anywhere
    assert hardware.kind in ("nvidia", "amd", "apple", "cpu")
