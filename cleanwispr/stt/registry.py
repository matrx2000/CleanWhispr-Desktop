"""Whisper model catalog + whisper-server binary locations.

Model URLs and sizes ported from OpenWhispr's modelRegistryData.json
(GGML models hosted by ggerganov on HuggingFace). Server binaries come from
the OpenWhispr/whisper.cpp fork's GitHub releases (MIT), which publish
prebuilt `whisper-server` for win32/linux/darwin.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from cleanwispr.storage import paths

_HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

WHISPER_SERVER_REPO = "OpenWhispr/whisper.cpp"


@dataclass(frozen=True, slots=True)
class WhisperModel:
    id: str
    label: str
    description: str
    size_mb: int
    expected_size_bytes: int
    file_name: str
    recommended: bool = False

    @property
    def download_url(self) -> str:
        return f"{_HF_BASE}/{self.file_name}"


WHISPER_MODELS: dict[str, WhisperModel] = {
    m.id: m
    for m in [
        WhisperModel("tiny", "Tiny", "Fastest, lower quality", 75, 78_000_000, "ggml-tiny.bin"),
        WhisperModel(
            "base", "Base", "Good balance", 142, 148_000_000, "ggml-base.bin", recommended=True
        ),
        WhisperModel(
            "small", "Small", "Better quality, slower", 466, 488_000_000, "ggml-small.bin"
        ),
        WhisperModel("medium", "Medium", "High quality", 1500, 1_570_000_000, "ggml-medium.bin"),
        WhisperModel(
            "large", "Large", "Best quality, slowest", 3000, 3_140_000_000, "ggml-large-v3.bin"
        ),
        WhisperModel(
            "turbo", "Turbo", "Fast with good quality", 1600, 1_670_000_000,
            "ggml-large-v3-turbo.bin",
        ),
    ]
}


# --- NVIDIA Parakeet models (sherpa-onnx, k2-fsa GitHub releases) ---

_SHERPA_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"


@dataclass(frozen=True, slots=True)
class ParakeetModel:
    id: str
    label: str
    description: str
    size_mb: int
    archive: str  # tar.bz2 asset name; extracts to a directory of the same stem

    @property
    def download_url(self) -> str:
        return f"{_SHERPA_RELEASE}/{self.archive}"

    @property
    def dir_name(self) -> str:
        return self.archive.removesuffix(".tar.bz2")


PARAKEET_MODELS: dict[str, ParakeetModel] = {
    m.id: m
    for m in [
        ParakeetModel(
            "parakeet-tdt-0.6b-v3", "Parakeet 0.6B v3",
            "Multilingual (25 languages, auto-detect), NVIDIA's fast ASR", 465,
            "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
        ),
        ParakeetModel(
            "parakeet-110m-en", "Parakeet 110M (English)",
            "Small and very fast, English only", 103,
            "sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8.tar.bz2",
        ),
    ]
}


def parakeet_model_dir(model_id: str) -> Path:
    return paths.models_dir("parakeet") / PARAKEET_MODELS[model_id].dir_name


def is_parakeet_model_installed(model_id: str) -> bool:
    return (parakeet_model_dir(model_id) / "tokens.txt").exists()


def model_path(model_id: str) -> Path:
    return paths.models_dir("whisper") / WHISPER_MODELS[model_id].file_name


def is_model_installed(model_id: str) -> bool:
    """Present and not an aborted partial download (>90% of expected size)."""
    model = WHISPER_MODELS[model_id]
    path = model_path(model_id)
    return path.exists() and path.stat().st_size >= model.expected_size_bytes * 0.9


def installed_models() -> list[str]:
    return [mid for mid in WHISPER_MODELS if is_model_installed(mid)]


# --- whisper-server binary (variants: cpu, cuda, vulkan; darwin: single Metal build) ---


def server_variants() -> tuple[str, ...]:
    """Engine builds available for this platform. macOS ships one binary with
    Metal GPU support built in, so 'cpu' is its only (and best) variant."""
    if sys.platform == "darwin":
        return ("cpu",)
    return ("cpu", "cuda", "vulkan")


def _platform_tag() -> str:
    if sys.platform == "win32":
        return "win32-x64"
    if sys.platform.startswith("linux"):
        return "linux-x64"
    if sys.platform == "darwin":
        import platform

        return "darwin-arm64" if platform.machine() == "arm64" else "darwin-x64"
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def server_binary_asset_name(variant: str = "cpu") -> str:
    """Zip asset name in the OpenWhispr/whisper.cpp GitHub release."""
    if sys.platform == "darwin":
        return f"whisper-server-{_platform_tag()}.zip"  # no variant suffix
    return f"whisper-server-{_platform_tag()}-{variant}.zip"


def server_binary_name_in_zip(variant: str = "cpu") -> str:
    if sys.platform == "darwin":
        return f"whisper-server-{_platform_tag()}"
    suffix = ".exe" if sys.platform == "win32" else ""
    return f"whisper-server-{_platform_tag()}-{variant}{suffix}"


def server_binary_path(variant: str = "cpu") -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    return paths.cache_dir() / "bin" / f"whisper-server-{variant}{suffix}"


def is_server_installed(variant: str = "cpu") -> bool:
    return server_binary_path(variant).exists()


def migrate_legacy_binaries() -> None:
    """Pre-variant installs used bin/whisper-server.exe — rename to the cpu slot."""
    suffix = ".exe" if sys.platform == "win32" else ""
    legacy = paths.cache_dir() / "bin" / f"whisper-server{suffix}"
    if legacy.exists() and not server_binary_path("cpu").exists():
        legacy.rename(server_binary_path("cpu"))


def resolve_server_variants(gpu_pref: str) -> list[str]:
    """Ordered list of *installed* variants to try for a GPU preference.
    "auto" prefers cuda → vulkan → cpu among what's installed; an explicit
    preference tries that variant first with cpu as the safety fallback."""
    if sys.platform == "darwin":
        order = ["cpu"]  # Metal is inside the single macOS build
    elif gpu_pref == "auto":
        order = ["cuda", "vulkan", "cpu"]
    elif gpu_pref in ("cuda", "vulkan"):
        order = [gpu_pref, "cpu"]
    else:
        order = ["cpu"]
    return [v for v in order if is_server_installed(v)]
