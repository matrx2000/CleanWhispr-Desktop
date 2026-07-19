"""Config/cache/data directory resolution.

All app files live under the platform-conventional user directories:
- Windows: %APPDATA%/CleanWispr (config+data), %LOCALAPPDATA%/CleanWispr/Cache (cache)
- Linux:   ~/.config/cleanwispr, ~/.local/share/cleanwispr, ~/.cache/cleanwispr
"""

from pathlib import Path

import platformdirs

from cleanwispr import APP_NAME

_dirs = platformdirs.PlatformDirs(APP_NAME, appauthor=False)


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


def models_dir(engine: str) -> Path:
    """Model storage per STT engine, e.g. models/whisper, models/parakeet."""
    path = cache_dir() / "models" / engine
    path.mkdir(parents=True, exist_ok=True)
    return path


def recordings_dir() -> Path:
    """Only used when audio retention is enabled (off by default)."""
    path = data_dir() / "recordings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return config_dir() / "config.json"


def db_file() -> Path:
    return data_dir() / "history.db"
