"""Silence/speech gate — drops empty recordings before they hit the engine.

Thresholds ported from OpenWhispr's localSpeechGate.js; RMS/peak are computed
on float samples normalized to [-1, 1] over ~100 ms windows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SILENCE_RMS_THRESHOLD = 0.002
SPEECH_WINDOW_RMS_THRESHOLD = 0.003
SPEECH_WINDOW_PEAK_THRESHOLD = 0.02
STRONG_SPEECH_RMS_THRESHOLD = 0.006


@dataclass(slots=True)
class GateDecision:
    skip: bool
    reason: str  # "silence" | "insufficient_speech" | "speech_detected" | "unavailable"


class SpeechGate:
    def __init__(self) -> None:
        self.peak_rms = 0.0
        self.peak_amplitude = 0.0
        self.window_count = 0
        self.speech_window_count = 0

    def record_window(self, samples: np.ndarray) -> float:
        """Feed one block of int16 samples; returns the window RMS (for level UI)."""
        floats = samples.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(floats**2))) if len(floats) else 0.0
        peak = float(np.max(np.abs(floats))) if len(floats) else 0.0

        self.window_count += 1
        self.peak_rms = max(self.peak_rms, rms)
        self.peak_amplitude = max(self.peak_amplitude, peak)
        if rms >= SPEECH_WINDOW_RMS_THRESHOLD and peak >= SPEECH_WINDOW_PEAK_THRESHOLD:
            self.speech_window_count += 1
        return rms

    def decision(self) -> GateDecision:
        if not self.window_count:
            return GateDecision(skip=False, reason="unavailable")
        if self.peak_rms < SILENCE_RMS_THRESHOLD:
            return GateDecision(skip=True, reason="silence")
        has_speech = self.speech_window_count >= 1 or self.peak_rms >= STRONG_SPEECH_RMS_THRESHOLD
        if not has_speech:
            return GateDecision(skip=True, reason="insufficient_speech")
        return GateDecision(skip=False, reason="speech_detected")
