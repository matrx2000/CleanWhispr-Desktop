"""Ollama server lifecycle helper: detect and auto-start a local Ollama.

Started detached (its own process group, NOT job-guarded) — Ollama is a
shared system service that should outlive CleanWispr.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time

log = logging.getLogger(__name__)


def find_ollama_binary() -> str | None:
    return shutil.which("ollama")


def start_ollama_server() -> bool:
    """Launch `ollama serve` detached. Returns False if ollama isn't installed."""
    binary = find_ollama_binary()
    if binary is None:
        return False
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
        )
    else:
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    log.info("launched 'ollama serve' (%s)", binary)
    return True


def ensure_running(provider, timeout_s: float = 25.0) -> bool:
    """True if the provider's server is reachable, starting Ollama if needed."""
    if provider.is_available():
        return True
    if not start_ollama_server():
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if provider.is_available():
            log.info("ollama server is up")
            return True
        time.sleep(0.5)
    log.warning("ollama did not become reachable within %.0fs", timeout_s)
    return False
