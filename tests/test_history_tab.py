import pytest
from PySide6.QtWidgets import QMessageBox

from cleanwispr.storage.db import HistoryDb
from cleanwispr.ui.settings.history_tab import HistoryTab


@pytest.fixture
def db(tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    db.add("dictation", "hello world")
    db.add("edit", "Edited.", instruction="fix it", source_text="orig")
    yield db
    db.close()


def test_refresh_populates_and_counts(qtbot, db):
    tab = HistoryTab(db)
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab._table.rowCount() == 2
    assert "2 entries" in tab._count_label.text()


def test_clear_all_confirmed(qtbot, db, monkeypatch):
    tab = HistoryTab(db)
    qtbot.addWidget(tab)
    tab.refresh()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    tab._clear_all()  # must not raise (regression: referenced a removed label)
    assert db.count() == 0
    assert tab._table.rowCount() == 0
    assert "0 entries" in tab._count_label.text()


def test_edit_entry_saves_with_flag(qtbot, db):
    tab = HistoryTab(db)
    qtbot.addWidget(tab)
    tab.refresh()
    tab._table.selectRow(0)  # newest entry
    assert not tab._save_button.isEnabled()

    tab._text_view.setPlainText("corrected text")
    assert tab._save_button.isEnabled()  # dirty -> save lights up
    tab._save_current()

    entry = db.list()[0]
    assert entry.text == "corrected text"
    assert entry.edited_at is not None
    assert "(edited)" in tab._table.item(0, 0).text()  # edited marker in the list
    assert not tab._save_button.isEnabled()  # clean again after save


def test_clear_all_cancelled_keeps_entries(qtbot, db, monkeypatch):
    tab = HistoryTab(db)
    qtbot.addWidget(tab)
    tab.refresh()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    tab._clear_all()
    assert db.count() == 2
