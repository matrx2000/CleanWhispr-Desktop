import numpy as np

from cleanwispr.audio.gate import SpeechGate


def _feed(gate: SpeechGate, amplitude: float, windows: int = 5) -> None:
    rng = np.random.default_rng(42)
    for _ in range(windows):
        samples = (rng.uniform(-1, 1, 1600) * amplitude * 32767).astype(np.int16)
        gate.record_window(samples)


def test_silence_is_skipped():
    gate = SpeechGate()
    _feed(gate, amplitude=0.0005)
    decision = gate.decision()
    assert decision.skip
    assert decision.reason == "silence"


def test_speech_passes():
    gate = SpeechGate()
    _feed(gate, amplitude=0.3)
    decision = gate.decision()
    assert not decision.skip
    assert decision.reason == "speech_detected"


def test_empty_gate_does_not_block():
    assert not SpeechGate().decision().skip
