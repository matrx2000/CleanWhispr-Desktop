"""Microphone capture: sounddevice (PortAudio) → 16 kHz mono int16 PCM.

The engines' native input format — no resampling, no FFmpeg, no container.
Level/gate callbacks fire on the PortAudio thread; marshal to Qt via signals.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from cleanwispr.audio.gate import GateDecision, SpeechGate
from cleanwispr.stt.base import SAMPLE_RATE

log = logging.getLogger(__name__)

_BLOCK_SIZE = SAMPLE_RATE // 10  # 100 ms windows, matching the gate's tuning


class AudioError(RuntimeError):
    """Mic unavailable/failed; message is user-presentable."""


@dataclass(slots=True)
class InputDevice:
    index: int
    name: str
    is_default: bool


def list_input_devices() -> list[InputDevice]:
    default_index = -1
    with contextlib.suppress(TypeError, sd.PortAudioError):
        default_index = sd.default.device[0]
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0:
            devices.append(InputDevice(index, info["name"], index == default_index))
    return devices


class Recorder:
    """One recording at a time: start() → mic stream fills a buffer → stop() → PCM."""

    def __init__(self) -> None:
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._gate = SpeechGate()

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(
        self,
        device_name: str | None = None,
        on_level: Callable[[float], None] | None = None,
        on_first_frame: Callable[[], None] | None = None,
    ) -> None:
        if self._stream is not None:
            return
        self._chunks = []
        self._gate = SpeechGate()
        gate = self._gate
        first_frame_seen = False

        def callback(indata: np.ndarray, frames: int, time_info, status) -> None:
            nonlocal first_frame_seen
            if status:
                log.warning("audio stream status: %s", status)
            if not first_frame_seen:
                first_frame_seen = True
                # Bluetooth mics can take ~1s before delivering anything;
                # this tells the UI the mic is actually live
                if on_first_frame:
                    on_first_frame()
            mono = indata[:, 0].copy()
            self._chunks.append(mono)
            rms = gate.record_window(mono)
            if on_level:
                on_level(rms)

        device = self._resolve_device(device_name)
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=_BLOCK_SIZE,
                device=device,
                callback=callback,
                # deep buffering: we transcribe after the fact, so added input
                # latency is invisible — but it absorbs system hiccups that
                # would otherwise drop samples ("input overflow")
                latency="high",
            )
            self._stream.start()
        except (sd.PortAudioError, ValueError) as exc:
            self._stream = None
            raise AudioError(f"Could not open microphone: {exc}") from exc
        log.info("recording started (device=%s)", device_name or "default")

    def stop(self) -> tuple[np.ndarray, GateDecision]:
        stream, self._stream = self._stream, None
        if stream is None:
            return np.zeros(0, dtype=np.int16), GateDecision(skip=True, reason="unavailable")
        stream.stop()
        stream.close()
        pcm = (
            np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.int16)
        )
        self._chunks = []
        decision = self._gate.decision()
        log.info(
            "recording stopped: %.1fs, gate=%s", len(pcm) / SAMPLE_RATE, decision.reason
        )
        return pcm, decision

    def abort(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._chunks = []

    @staticmethod
    def _resolve_device(device_name: str | None) -> int | None:
        """Settings store the device by name; fall back to default if it's gone."""
        if not device_name:
            return None
        for device in list_input_devices():
            if device.name == device_name:
                return device.index
        log.warning("input device %r not found, using default", device_name)
        return None
