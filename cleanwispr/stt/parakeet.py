"""NVIDIA Parakeet engine — sherpa-onnx OfflineRecognizer, fully in-process.

No server subprocess: the ONNX model loads into this process (a few seconds,
kept warm) and transcribes 16 kHz PCM directly. Parakeet v3 is multilingual
with automatic language detection, so the language setting is not needed.
"""

from __future__ import annotations

import logging
from threading import Lock

import numpy as np

from cleanwispr.stt import registry
from cleanwispr.stt.base import (
    SAMPLE_RATE,
    SttEngine,
    SttError,
    TranscriptionResult,
    normalize_transcript,
)

log = logging.getLogger(__name__)


def _find_file(directory, *patterns: str):
    for pattern in patterns:
        matches = sorted(directory.glob(pattern))
        if matches:
            return matches[0]
    return None


class ParakeetEngine(SttEngine):
    name = "parakeet"

    def __init__(self) -> None:
        self._recognizer = None
        self._model_id: str | None = None
        self._lock = Lock()

    def ensure(self, model_id: str, language: str = "auto", gpu: str = "auto") -> None:
        """language/gpu accepted for interface parity; Parakeet auto-detects
        language and sherpa-onnx wheels are CPU (int8 — fast even there)."""
        with self._lock:
            if self._recognizer is not None and self._model_id == model_id:
                return
            self._load_locked(model_id)

    def start(self, model_id: str) -> None:
        self.ensure(model_id)

    def stop(self) -> None:
        with self._lock:
            self._recognizer = None
            self._model_id = None

    def is_ready(self) -> bool:
        return self._recognizer is not None

    def _load_locked(self, model_id: str) -> None:
        if model_id not in registry.PARAKEET_MODELS:
            raise SttError(f"Unknown Parakeet model '{model_id}'")
        if not registry.is_parakeet_model_installed(model_id):
            raise SttError(
                f"Parakeet model '{model_id}' not installed — download it in "
                "Settings → Transcription."
            )
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise SttError(
                "sherpa-onnx is not installed — run: pip install -r requirements.txt"
            ) from exc

        model_dir = registry.parakeet_model_dir(model_id)
        encoder = _find_file(model_dir, "encoder*.int8.onnx", "encoder*.onnx")
        decoder = _find_file(model_dir, "decoder*.int8.onnx", "decoder*.onnx")
        joiner = _find_file(model_dir, "joiner*.int8.onnx", "joiner*.onnx")
        tokens = model_dir / "tokens.txt"
        if not all([encoder, decoder, joiner, tokens.exists()]):
            raise SttError(f"Parakeet model files incomplete in {model_dir}")

        log.info("loading parakeet model %s (in-process)", model_id)
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            tokens=str(tokens),
            num_threads=4,
            model_type="nemo_transducer",
        )
        self._model_id = model_id
        log.info("parakeet model %s ready", model_id)

    def transcribe(
        self,
        pcm: np.ndarray,
        *,
        language: str = "auto",
        initial_prompt: str | None = None,  # not supported by Parakeet — ignored
    ) -> TranscriptionResult:
        if len(pcm) == 0:
            raise SttError("Recording was empty — no audio reached the microphone")
        with self._lock:
            if self._recognizer is None:
                raise SttError("Parakeet engine not started")
            floats = pcm.astype(np.float32) / 32768.0
            stream = self._recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, floats)
            self._recognizer.decode_stream(stream)
            text = normalize_transcript(stream.result.text)
        return TranscriptionResult(
            text=text,
            language=language,
            duration_ms=int(len(pcm) / SAMPLE_RATE * 1000),
        )
