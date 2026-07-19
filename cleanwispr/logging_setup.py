"""Logging configuration with a user-facing verbosity switch.

The console stays quiet (warnings/errors only) unless verbose logging is
enabled in Settings → General. The file log always records INFO+ so bug
reports work regardless of the console setting; verbose raises it to DEBUG.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from cleanwispr.storage import paths

_console: logging.StreamHandler | None = None
_file: RotatingFileHandler | None = None


def setup(verbose: bool) -> None:
    global _console, _file
    log_dir = paths.data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _console = logging.StreamHandler()
    _file = RotatingFileHandler(
        log_dir / "cleanwispr.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    _console.setFormatter(formatter)
    _file.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers decide what surfaces
    root.addHandler(_console)
    root.addHandler(_file)
    set_verbose(verbose)


def set_verbose(verbose: bool) -> None:
    """Live switch — wired to the Settings → General checkbox."""
    if _console is not None:
        _console.setLevel(logging.INFO if verbose else logging.WARNING)
    if _file is not None:
        _file.setLevel(logging.DEBUG if verbose else logging.INFO)
