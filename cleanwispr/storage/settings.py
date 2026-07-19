"""Application settings: pydantic-validated JSON config.

Schema mirrors SPEC.md §5. Unknown keys are ignored on load so older configs
survive upgrades; a corrupt config falls back to defaults (the broken file is
kept as config.json.bak for inspection).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from cleanwispr.storage import paths

log = logging.getLogger(__name__)


class ActivationMode(StrEnum):
    TOGGLE = "toggle"  # tap to start, tap to stop
    HOLD = "hold"  # record while key is held (Windows/X11 only)


class HotkeySlot(BaseModel):
    combo: str
    mode: ActivationMode = ActivationMode.TOGGLE


class HotkeySettings(BaseModel):
    dictation: HotkeySlot = HotkeySlot(combo="ctrl+super")
    # NOT ctrl+alt+<letter>: Ctrl+Alt is AltGr on many European layouts, so such
    # combos TYPE a character into the focused app (Croatian: Ctrl+Alt+E = €),
    # which destroys the very selection the editor is about to use
    editor: HotkeySlot = HotkeySlot(combo="alt+super")


class SttSettings(BaseModel):
    engine: str = "whisper"  # whisper | parakeet
    whisper_model: str = "small"
    parakeet_model: str = "parakeet-tdt-0.6b-v3"
    language: str = "auto"
    custom_dictionary: list[str] = Field(default_factory=list)
    gpu: str = "auto"  # auto | cuda | vulkan | cpu
    models_dir: str = ""  # custom model download folder; empty = default cache dir


class OllamaSettings(BaseModel):
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""  # empty until the user picks one in settings
    num_ctx: int = 8192
    temperature: float = 0.2
    keep_alive: str = "10m"
    interpret_run_as_pull: bool = True  # pasted 'ollama run x' treated as a pull


class LlmSettings(BaseModel):
    provider: str = "ollama"
    ollama: OllamaSettings = OllamaSettings()


class AudioSettings(BaseModel):
    input_device: str | None = None  # None = system default
    keep_recordings: bool = False


class InjectSettings(BaseModel):
    restore_clipboard: bool = True


class HistorySettings(BaseModel):
    enabled: bool = True  # off: dictations/edits are never written to history.db


class UiSettings(BaseModel):
    overlay_position: str = "bottom-right"
    start_on_login: bool = False
    ui_language: str = "en"
    sounds_enabled: bool = True
    verbose_logging: bool = False  # console INFO + file DEBUG when enabled


class Settings(BaseModel):
    hotkeys: HotkeySettings = HotkeySettings()
    stt: SttSettings = SttSettings()
    llm: LlmSettings = LlmSettings()
    audio: AudioSettings = AudioSettings()
    inject: InjectSettings = InjectSettings()
    history: HistorySettings = HistorySettings()
    ui: UiSettings = UiSettings()


def load(path: Path | None = None) -> Settings:
    path = path or paths.config_file()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("config unreadable (%s), falling back to defaults", exc)
        with contextlib.suppress(OSError):
            path.replace(path.with_suffix(".json.bak"))
        return Settings()


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump_json(indent=2)
    # atomic write: never leave a half-written config behind
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
