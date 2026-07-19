"""Synthesized audio feedback — no sample files needed.

Short sine chimes are rendered to WAV in the cache dir on first run:
- start: rising two-note (recording began)
- done:  falling two-note (text pasted)
- error: low double-buzz

Played via QSoundEffect (low latency). If QtMultimedia is unavailable
(minimal Linux installs), sound is silently disabled.
"""

from __future__ import annotations

import logging
import sys
import wave
from pathlib import Path

import numpy as np

from cleanwispr.core.controller import Controller
from cleanwispr.storage import paths
from cleanwispr.storage.settings import Settings

log = logging.getLogger(__name__)

_SAMPLE_RATE = 44_100


def _tone(freq: float, ms: int, volume: float = 0.4) -> np.ndarray:
    """One sine note with a soft attack/decay envelope."""
    samples = int(_SAMPLE_RATE * ms / 1000)
    t = np.arange(samples) / _SAMPLE_RATE
    wave_data = np.sin(2 * np.pi * freq * t)
    attack = int(samples * 0.15)
    envelope = np.ones(samples)
    envelope[:attack] = np.linspace(0, 1, attack)
    envelope[attack:] = np.exp(-3.5 * np.linspace(0, 1, samples - attack))
    return wave_data * envelope * volume


def _render(notes: list[tuple[float, int]], gap_ms: int = 20) -> np.ndarray:
    gap = np.zeros(int(_SAMPLE_RATE * gap_ms / 1000))
    parts: list[np.ndarray] = []
    for freq, ms in notes:
        parts.append(_tone(freq, ms))
        parts.append(gap)
    mixed = np.concatenate(parts)
    return (np.clip(mixed, -1, 1) * 32767).astype(np.int16)


_SOUNDS = {
    "start": [(587.0, 80), (880.0, 110)],  # D5 → A5, rising: "listening"
    "done": [(880.0, 80), (587.0, 130)],  # A5 → D5, falling: "finished"
    "error": [(196.0, 130), (185.0, 170)],  # low G3 → F#3: "nope"
}


def ensure_sound_files() -> dict[str, Path]:
    sound_dir = paths.cache_dir() / "sounds"
    sound_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, notes in _SOUNDS.items():
        path = sound_dir / f"{name}.wav"
        if not path.exists():
            pcm = _render(notes)
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(_SAMPLE_RATE)
                out.writeframes(pcm.tobytes())
        files[name] = path
    return files


class SoundPlayer:
    """Plays the chimes on controller events. Create after QApplication exists.

    Windows uses winsound.PlaySound, which resolves the CURRENT default output
    device on every call — critical with Bluetooth headsets, whose endpoint is
    torn down and replaced when recording flips them to the hands-free profile
    (a device-bound player like QSoundEffect goes silent when that happens).
    Other platforms use QSoundEffect."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._files = ensure_sound_files()
        self._effects = {}
        if sys.platform == "win32":
            return  # winsound needs no preloading
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
        except ImportError:
            log.warning("QtMultimedia unavailable — sound feedback disabled")
            return
        for name, path in self._files.items():
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(0.35)
            self._effects[name] = effect

    def attach(self, controller: Controller) -> None:
        # start chime fires before the mic opens — see Controller.recording_starting
        controller.recording_starting.connect(lambda: self.play("start"))
        controller.history_changed.connect(lambda: self.play("done"))
        controller.error_occurred.connect(lambda _msg: self.play("error"))

    def play(self, name: str) -> None:
        if not self._settings.ui.sounds_enabled:
            return
        if sys.platform == "win32":
            import winsound

            try:
                winsound.PlaySound(
                    str(self._files[name]),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
            except RuntimeError:
                log.warning("winsound playback failed for %s", name)
            return
        effect = self._effects.get(name)
        if effect is not None:
            effect.play()
