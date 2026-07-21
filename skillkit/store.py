"""Persistence adapters for a SkillLibrary.

The library never touches the filesystem directly — it reads and writes an
opaque dict through a `SkillStore`. Swap the store to change *where* skills
live (a JSON file, a row in your app's config, an in-memory blob for tests)
without touching any other module. `JsonSkillStore` is a good default:
atomic writes, and a corrupt file is set aside as `.bak` rather than lost.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class SkillStore(Protocol):
    """The seam every persistence backend implements."""

    def load(self) -> dict | None:
        """Return the stored blob, or None if nothing has been saved yet."""

    def save(self, data: dict) -> None:
        """Persist the blob. Should be atomic where the medium allows."""


class MemorySkillStore:
    """Non-persistent store — handy for tests and for a host that wants to keep
    skills inside its own config object (pass the dict in, read it back out)."""

    def __init__(self, data: dict | None = None) -> None:
        self._data = data

    def load(self) -> dict | None:
        return self._data

    def save(self, data: dict) -> None:
        self._data = data

    @property
    def data(self) -> dict | None:
        return self._data


class JsonSkillStore:
    """A JSON file with atomic writes and corrupt-file quarantine."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("skills file unreadable (%s); keeping a .bak and starting fresh", exc)
            with contextlib.suppress(OSError):
                self.path.replace(self.path.with_suffix(self.path.suffix + ".bak"))
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
