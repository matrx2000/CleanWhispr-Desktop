"""Config/cache/data directory resolution.

All app files live under the platform-conventional user directories:
- Windows: %APPDATA%/CleanWispr (config+data), %LOCALAPPDATA%/CleanWispr/Cache (cache)
- Linux:   ~/.config/cleanwispr, ~/.local/share/cleanwispr, ~/.cache/cleanwispr
"""

from pathlib import Path

import platformdirs

from cleanwispr import APP_NAME

_dirs = platformdirs.PlatformDirs(APP_NAME, appauthor=False)

# user-chosen models folder (settings.stt.models_dir); None = default cache location
_models_override: Path | None = None


def set_models_override(path: str | Path | None) -> None:
    """Route model downloads to a custom folder (e.g. another disk). Called at
    startup and whenever the user changes the location in settings."""
    global _models_override
    _models_override = Path(path) if path else None


def config_dir() -> Path:
    path = Path(_dirs.user_config_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    path = Path(_dirs.user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = Path(_dirs.user_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_root() -> Path:
    """Folder that holds all downloaded models (per-engine subfolders)."""
    root = _models_override if _models_override else cache_dir() / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def models_dir(engine: str) -> Path:
    """Model storage per STT engine, e.g. models/whisper, models/parakeet."""
    path = models_root() / engine
    path.mkdir(parents=True, exist_ok=True)
    return path


def recordings_dir() -> Path:
    """Only used when audio retention is enabled (off by default)."""
    path = data_dir() / "recordings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_notes_dir() -> Path:
    """Default vault folder for the Notes view (settings.notes.notes_dir empty)."""
    path = data_dir() / "notes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return config_dir() / "config.json"


def db_file() -> Path:
    return data_dir() / "history.db"
