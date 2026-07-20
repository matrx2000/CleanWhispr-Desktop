"""whisper.cpp engine: drives the C++ `whisper-server` binary as a persistent
background child process (the OpenWhispr performance recipe).

Server lifecycle: spawn with the model pre-loaded, poll until it answers,
keep it warm between dictations, restart transparently when the model or
language changes, and always kill it on stop() — no orphan processes.
"""

from __future__ import annotations

import io
import logging
import socket
import subprocess
import sys
import time
import wave
from threading import Lock

import httpx
import numpy as np

from cleanwispr.storage import paths
from cleanwispr.stt import procguard, registry
from cleanwispr.stt.base import (
    SAMPLE_RATE,
    SttEngine,
    SttError,
    TranscriptionResult,
    normalize_transcript,
)

log = logging.getLogger(__name__)

_STARTUP_TIMEOUT_S = 180  # large models take a while to load
_REQUEST_TIMEOUT_S = 300


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    if pcm.dtype != np.int16:
        raise ValueError(f"expected int16 PCM, got {pcm.dtype}")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


class WhisperCppEngine(SttEngine):
    name = "whisper"

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._model_id: str | None = None
        self._language: str | None = None
        self._gpu_pref: str = "auto"
        self._variant: str | None = None  # variant actually running
        self._lock = Lock()  # serialize start/stop/transcribe across threads

    @property
    def active_variant(self) -> str | None:
        return self._variant if self._alive() else None

    # --- lifecycle ---

    def ensure(self, model_id: str, language: str = "auto", gpu: str = "auto") -> None:
        """Start the server, or restart it if model/language/gpu changed."""
        with self._lock:
            if (
                self._alive()
                and self._model_id == model_id
                and self._language == language
                and self._gpu_pref == gpu
            ):
                return
            self._stop_locked()
            self._start_with_fallback_locked(model_id, language, gpu)

    def start(self, model_id: str) -> None:
        self.ensure(model_id, self._language or "auto", self._gpu_pref)

    def _start_with_fallback_locked(self, model_id: str, language: str, gpu: str) -> None:
        """Try GPU variants in preference order; fall back on startup failure."""
        variants = registry.resolve_server_variants(gpu)
        if not variants:
            raise SttError(
                "Transcription engine not installed — download it in "
                "Settings → Transcription."
            )
        last_error: SttError | None = None
        for variant in variants:
            try:
                self._start_locked(model_id, language, variant)
                self._gpu_pref = gpu
                return
            except SttError as exc:
                log.warning("whisper-server (%s) failed to start: %s", variant, exc)
                last_error = exc
        raise last_error or SttError("whisper-server could not start")

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def is_ready(self) -> bool:
        return self._alive()

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _start_locked(self, model_id: str, language: str, variant: str) -> None:
        binary = registry.server_binary_path(variant)
        if not registry.is_model_installed(model_id):
            raise SttError(
                f"Whisper model '{model_id}' not installed — download it in "
                "Settings → Transcription."
            )

        port = _free_port()
        args = [
            str(binary),
            "--model", str(registry.model_path(model_id)),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--language", language or "auto",
        ]
        extras: dict = procguard.popen_kwargs()
        if sys.platform == "win32":
            extras["creationflags"] = subprocess.CREATE_NO_WINDOW
        log.info(
            "starting whisper-server: variant=%s model=%s language=%s port=%s",
            variant, model_id, language, port,
        )
        log_dir = paths.cache_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stderr_log = open(log_dir / "whisper-server.log", "wb")  # noqa: SIM115 — owned by child
        self._process = subprocess.Popen(
            args,
            cwd=binary.parent,  # GPU builds load companion DLLs from their own dir
            stdout=subprocess.DEVNULL,
            stderr=stderr_log,
            **extras,
        )
        stderr_log.close()  # child holds its own handle
        procguard.guard_child(self._process)  # dies with us, even on force-kill
        self._port = port
        self._model_id = model_id
        self._language = language
        self._variant = variant
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                code = self._process.returncode if self._process else "?"
                self._process = None
                raise SttError(f"whisper-server exited during startup (code {code})")
            try:
                httpx.get(f"http://127.0.0.1:{self._port}/", timeout=2)
                log.info("whisper-server ready on port %s", self._port)
                return
            except httpx.HTTPError:
                time.sleep(0.25)
        self._stop_locked()
        raise SttError("whisper-server did not become ready in time")

    def _stop_locked(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.info("whisper-server stopped")

    # --- transcription ---

    def transcribe(
        self,
        pcm: np.ndarray,
        *,
        language: str = "auto",
        initial_prompt: str | None = None,
    ) -> TranscriptionResult:
        if self._model_id is None:
            raise SttError("Engine not started")
        if len(pcm) == 0:
            raise SttError("Recording was empty — no audio reached the microphone")
        self.ensure(self._model_id, language, self._gpu_pref)

        duration_ms = int(len(pcm) / SAMPLE_RATE * 1000)
        wav = pcm_to_wav_bytes(pcm)
        data: dict[str, str] = {"response_format": "json"}
        if initial_prompt:
            data["prompt"] = initial_prompt

        try:
            text = self._post_inference(wav, data)
        except SttError as exc:
            # transient server weirdness (or a dead/hijacked port): restart once
            log.warning("inference failed (%s), restarting whisper-server and retrying", exc)
            with self._lock:
                self._stop_locked()
                self._start_with_fallback_locked(self._model_id, language, self._gpu_pref)
            text = self._post_inference(wav, data)
        return TranscriptionResult(text=text, language=language, duration_ms=duration_ms)

    def _post_inference(self, wav: bytes, data: dict[str, str]) -> str:
        try:
            response = httpx.post(
                f"http://127.0.0.1:{self._port}/inference",
                files={"file": ("audio.wav", wav, "audio/wav")},
                data=data,
                timeout=_REQUEST_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise SttError(f"Transcription request failed: {exc}") from exc
        if response.status_code != 200:
            raise SttError(f"whisper-server error {response.status_code}: {response.text[:200]}")
        return normalize_transcript(response.json().get("text") or "")
