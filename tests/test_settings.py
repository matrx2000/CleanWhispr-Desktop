import json

from cleanwispr.storage import settings as settings_store
from cleanwispr.storage.settings import ActivationMode, Settings


def test_defaults():
    s = Settings()
    assert s.hotkeys.dictation.mode is ActivationMode.TOGGLE
    assert s.hotkeys.editor.mode is ActivationMode.TOGGLE
    # never ctrl+alt+<letter>: AltGr on European layouts types into the target app
    assert s.hotkeys.dictation.combo == "ctrl+super"
    assert s.hotkeys.editor.combo == "alt+super"
    assert s.stt.engine == "whisper"
    assert s.stt.language == "auto"
    assert s.llm.provider == "ollama"
    assert s.llm.ollama.base_url == "http://127.0.0.1:11434"
    assert s.audio.keep_recordings is False  # audio retention off by default
    assert s.history.enabled is True  # history logging on by default


def test_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    s = Settings()
    s.stt.language = "hr"
    s.llm.ollama.num_ctx = 16384
    s.hotkeys.dictation.combo = "f8"
    settings_store.save(s, path)

    loaded = settings_store.load(path)
    assert loaded == s


def test_missing_file_gives_defaults(tmp_path):
    assert settings_store.load(tmp_path / "nope.json") == Settings()


def test_corrupt_file_falls_back_and_keeps_backup(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert settings_store.load(path) == Settings()
    assert (tmp_path / "config.json.bak").exists()


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "config.json"
    data = Settings().model_dump()
    data["future_feature"] = {"x": 1}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert settings_store.load(path) == Settings()
