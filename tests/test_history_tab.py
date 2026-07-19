import pytest
from PySide6.QtWidgets import QMessageBox

from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.settings.history_tab import HistoryTab


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    db.add("dictation", "hello world")
    db.add("edit", "Edited.", instruction="fix it", source_text="orig")
    yield db
    db.close()


def _make_tab(qtbot, db, settings=None, on_change=None):
    tab = HistoryTab(settings or Settings(), db, on_change or (lambda: None))
    qtbot.addWidget(tab)
    return tab


def test_refresh_populates_and_counts(qtbot, db):
    tab = _make_tab(qtbot, db)
    tab.refresh()
    assert tab._list.count() == 2
    assert "2 entries" in tab._count_label.text()


def test_clear_all_confirmed(qtbot, db, monkeypatch):
    tab = _make_tab(qtbot, db)
    tab.refresh()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    tab._clear_all()  # must not raise (regression: referenced a removed label)
    assert db.count() == 0
    assert tab._list.count() == 0
    assert "0 entries" in tab._count_label.text()


def test_edit_entry_saves_with_flag(qtbot, db):
    tab = _make_tab(qtbot, db)
    tab.refresh()
    tab._list.setCurrentRow(0)  # newest entry
    assert not tab._save_button.isEnabled()

    tab._text_view.setPlainText("corrected text")
    assert tab._save_button.isEnabled()  # dirty -> save lights up
    tab._save_current()

    entry = db.list()[0]
    assert entry.text == "corrected text"
    assert entry.edited_at is not None
    assert not tab._cards[0].edited_badge.isHidden()  # edited marker in the list
    assert not tab._save_button.isEnabled()  # clean again after save


def test_clear_all_cancelled_keeps_entries(qtbot, db, monkeypatch):
    tab = _make_tab(qtbot, db)
    tab.refresh()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    tab._clear_all()
    assert db.count() == 2


def test_history_toggle_reflects_and_updates_setting(qtbot, db):
    settings = Settings()
    changes = []
    tab = _make_tab(qtbot, db, settings, lambda: changes.append(settings.history.enabled))

    assert tab._enabled_toggle.isChecked() is True  # on by default
    tab._enabled_toggle.setChecked(False)
    assert settings.history.enabled is False
    assert changes == [False]
