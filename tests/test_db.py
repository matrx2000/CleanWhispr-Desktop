import pytest

from cleanwispr.storage.db import HistoryDb


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    yield db
    db.close()


def test_add_and_list_dictation(db):
    entry_id = db.add(
        "dictation", "hello world", language="en", engine="whisper:small", duration_ms=1200
    )
    entries = db.list()
    assert len(entries) == 1
    assert entries[0].id == entry_id
    assert entries[0].kind == "dictation"
    assert entries[0].text == "hello world"
    assert entries[0].audio_path is None  # no audio kept by default


def test_add_edit_entry(db):
    db.add(
        "edit",
        "Hello, World!",
        instruction="capitalize it",
        source_text="hello world",
        llm_model="ollama:qwen2.5:7b",
    )
    entry = db.list()[0]
    assert entry.kind == "edit"
    assert entry.instruction == "capitalize it"
    assert entry.source_text == "hello world"


def test_invalid_kind_rejected(db):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.add("banana", "text")


def test_search_and_delete(db):
    db.add("dictation", "the quick brown fox")
    db.add("dictation", "an unrelated note")
    hits = db.list(search="quick")
    assert len(hits) == 1
    db.delete(hits[0].id)
    assert db.count() == 1


def test_update_text_sets_edited_flag(db):
    entry_id = db.add("dictation", "orginal txet")
    assert db.list()[0].edited_at is None
    db.update_text(entry_id, "original text")
    entry = db.list()[0]
    assert entry.text == "original text"
    assert entry.edited_at is not None


def test_migration_adds_edited_at_to_old_schema(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE transcriptions (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             kind TEXT NOT NULL CHECK (kind IN ('dictation', 'edit')),
             text TEXT NOT NULL, instruction TEXT, source_text TEXT,
             language TEXT, engine TEXT, llm_model TEXT,
             duration_ms INTEGER, audio_path TEXT)"""
    )
    conn.execute("INSERT INTO transcriptions (kind, text) VALUES ('dictation', 'legacy')")
    conn.commit()
    conn.close()

    migrated = HistoryDb(path)  # must add the edited_at column
    entry = migrated.list()[0]
    assert entry.text == "legacy"
    assert entry.edited_at is None
    migrated.update_text(entry.id, "legacy fixed")
    assert migrated.list()[0].edited_at is not None
    migrated.close()


def test_clear_all(db):
    db.add("dictation", "one")
    db.add("edit", "two", instruction="x")
    assert db.clear() == 2
    assert db.count() == 0
    assert db.clear() == 0  # idempotent


def test_list_order_newest_first(db):
    first = db.add("dictation", "first")
    second = db.add("dictation", "second")
    ids = [e.id for e in db.list()]
    assert ids == [second, first]
