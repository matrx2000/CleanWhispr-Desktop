"""SttEngine — the seam every speech-to-text backend implements.

Engines own their runtime lifecycle (child server process or in-process
runtime). `start()` pre-warms the engine (loads the model) so transcription
latency is inference-only. Input audio is always 16 kHz mono int16 PCM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16_000


class SttError(RuntimeError):
    """Engine/transcription failure; message is user-presentable."""


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    language: str | None = None  # detected language, if the engine reports it
    duration_ms: int = 0  # audio duration


@dataclass(slots=True)
class SttModelInfo:
    """One installable model as described by the registry."""

    id: str  # e.g. "small", "parakeet-tdt-0.6b-v3"
    engine: str  # "whisper" | "parakeet"
    label: str
    size_mb: int
    languages: list[str] = field(default_factory=list)  # empty = full whisper set
    download_urls: list[str] = field(default_factory=list)
    installed: bool = False


class SttEngine(ABC):
    """Contract: start() may be slow (model load); transcribe() must be thread-safe
    to call from a worker thread; stop() must reliably kill any child process."""

    name: str

    @abstractmethod
    def start(self, model_id: str) -> None:
        """Load the model / spawn the inference server. Idempotent."""

    @abstractmethod
    def stop(self) -> None:
        """Release the model / terminate the server. Idempotent."""

    @abstractmethod
    def is_ready(self) -> bool: ...

    @abstractmethod
    def transcribe(
        self,
        pcm: np.ndarray,  # int16 mono at SAMPLE_RATE
        *,
        language: str = "auto",
        initial_prompt: str | None = None,  # custom-dictionary bias (whisper only)
    ) -> TranscriptionResult: ...
