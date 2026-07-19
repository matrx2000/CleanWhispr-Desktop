import wave

import numpy as np

from cleanwispr.ui.sounds import _SOUNDS, _render, ensure_sound_files


def test_render_produces_int16_audio():
    pcm = _render([(440.0, 100), (880.0, 100)])
    assert pcm.dtype == np.int16
    assert len(pcm) > 0
    assert np.abs(pcm).max() > 1000  # audible
    assert np.abs(pcm).max() <= 32767  # no clipping


def test_sound_files_generated(monkeypatch, tmp_path):
    monkeypatch.setattr("cleanwispr.storage.paths.cache_dir", lambda: tmp_path)
    files = ensure_sound_files()
    assert set(files) == set(_SOUNDS)
    for path in files.values():
        with wave.open(str(path)) as w:
            assert w.getframerate() == 44_100
            assert w.getnchannels() == 1
            assert w.getnframes() > 0
