import io
import wave
import zipfile

import numpy as np
import pytest

from cleanwispr.stt import registry
from cleanwispr.stt.downloader import DownloadError, extract_binary_from_zip
from cleanwispr.stt.languages import LANGUAGES
from cleanwispr.stt.whisper_cpp import pcm_to_wav_bytes


def test_registry_models():
    assert set(registry.WHISPER_MODELS) == {"tiny", "base", "small", "medium", "large", "turbo"}
    base = registry.WHISPER_MODELS["base"]
    assert base.recommended
    assert base.download_url.startswith("https://huggingface.co/ggerganov/whisper.cpp/")
    assert base.download_url.endswith("ggml-base.bin")


def test_languages_auto_first_and_unique():
    codes = [code for code, _ in LANGUAGES]
    assert codes[0] == "auto"
    assert len(codes) == len(set(codes))
    assert "hr" in codes and "en" in codes


def test_pcm_to_wav_roundtrip():
    pcm = (np.sin(np.linspace(0, 100, 16000)) * 10000).astype(np.int16)
    wav_bytes = pcm_to_wav_bytes(pcm)
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == len(pcm)


def test_pcm_to_wav_rejects_wrong_dtype():
    with pytest.raises(ValueError):
        pcm_to_wav_bytes(np.zeros(10, dtype=np.float32))


def test_parakeet_registry():
    from cleanwispr.stt.registry import PARAKEET_MODELS

    assert "parakeet-tdt-0.6b-v3" in PARAKEET_MODELS
    model = PARAKEET_MODELS["parakeet-tdt-0.6b-v3"]
    assert model.download_url.startswith("https://github.com/k2-fsa/sherpa-onnx/")
    assert model.dir_name == "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"


def test_extract_tar_bz2(tmp_path):
    import tarfile

    from cleanwispr.stt.downloader import extract_tar_bz2

    src = tmp_path / "model-dir"
    src.mkdir()
    (src / "tokens.txt").write_text("a\nb\n")
    (src / "encoder.int8.onnx").write_bytes(b"onnx")
    archive = tmp_path / "model.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        tar.add(src, arcname="model-dir")

    dest = tmp_path / "out"
    extract_tar_bz2(archive, dest)
    assert (dest / "model-dir" / "tokens.txt").read_text() == "a\nb\n"


def test_extract_binary_from_zip(tmp_path):
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("nested/dir/my-server.exe", b"binarydata")
        zf.writestr("readme.txt", b"docs")
    dest = tmp_path / "out" / "server.exe"
    extract_binary_from_zip(zip_path, "my-server.exe", dest)
    assert dest.read_bytes() == b"binarydata"


def test_extract_binary_missing_raises(tmp_path):
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", b"docs")
    with pytest.raises(DownloadError):
        extract_binary_from_zip(zip_path, "my-server.exe", tmp_path / "server.exe")
