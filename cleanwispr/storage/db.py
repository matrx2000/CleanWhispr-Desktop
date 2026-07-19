"""Transcription/edit history — SQLite (WAL), schema from SPEC.md §5."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cleanwispr.storage import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  kind TEXT NOT NULL CHECK (kind IN ('dictation', 'edit')),
  text TEXT NOT NULL,
  instruction TEXT,
  source_text TEXT,
  language TEXT,
  engine TEXT,
  llm_model TEXT,
  duration_ms INTEGER,
  audio_path TEXT,
  edited_at TEXT
);
"""


@dataclass(slots=True)
class HistoryEntry:
    id: int
    created_at: str
    kind: str
    text: str
    instruction: str | None
    source_text: str | None
    language: str | None
    engine: str | None
    llm_model: str | None
    duration_ms: int | None
    audio_path: str | None
    edited_at: str | None = None  # set when the user manually edits the entry


class HistoryDb:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.db_file()
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(transcriptions)")}
        if "edited_at" not in columns:
            self._conn.execute("ALTER TABLE transcriptions ADD COLUMN edited_at TEXT")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add(
        self,
        kind: str,
        text: str,
        *,
        instruction: str | None = None,
        source_text: str | None = None,
        language: str | None = None,
        engine: str | None = None,
        llm_model: str | None = None,
        duration_ms: int | None = None,
        audio_path: str | None = None,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO transcriptions
               (kind, text, instruction, source_text, language, engine,
                llm_model, duration_ms, audio_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, text, instruction, source_text, language, engine,
             llm_model, duration_ms, audio_path),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list(
        self, *, limit: int = 100, offset: int = 0, search: str | None = None
    ) -> list[HistoryEntry]:
        if search:
            rows = self._conn.execute(
                """SELECT * FROM transcriptions
                   WHERE text LIKE ? OR instruction LIKE ?
                   ORDER BY id DESC LIMIT ? OFFSET ?""",
                (f"%{search}%", f"%{search}%", limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM transcriptions ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [HistoryEntry(**dict(row)) for row in rows]

    def update_text(self, entry_id: int, text: str) -> None:
        """User edit of an entry's text; stamps the edited flag."""
        self._conn.execute(
            "UPDATE transcriptions SET text = ?, edited_at = datetime('now') WHERE id = ?",
            (text, entry_id),
        )
        self._conn.commit()

    def delete(self, entry_id: int) -> None:
        self._conn.execute("DELETE FROM transcriptions WHERE id = ?", (entry_id,))
        self._conn.commit()

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM transcriptions").fetchone()[0])

    def clear(self) -> int:
        """Delete all history entries; returns how many were removed."""
        removed = self.count()
        self._conn.execute("DELETE FROM transcriptions")
        self._conn.commit()
        return removed
