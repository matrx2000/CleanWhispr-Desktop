"""AI-take application: no selection appends, selection replaces only itself.

Guards against the regression where an instruction with no selection wiped the
whole note (the LLM output replaced the entire document).
"""

import pytest

from cleanwispr.core.controller import NOTES_MODE_GENERATE, NOTES_MODE_SELECTION, Controller
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.notes.window import NotesWindow
from tests.test_controller import FakeEngine, FakeInjector, FakeRecorder


@pytest.fixture
def window(qtbot, tmp_path):
    settings = Settings()
    settings.notes.vaults = [str(tmp_path)]
    settings.notes.active_vault = str(tmp_path)
    db = HistoryDb(tmp_path / "h.db")
    controller = Controller(settings, db, FakeRecorder(), FakeEngine(), FakeInjector())
    win = NotesWindow(settings, controller, lambda: None)
    qtbot.addWidget(win)
    yield win
    db.close()


def test_generate_appends_and_keeps_existing_note(window):
    window._editor.set_markdown("My important existing notes.")
    window._apply_ai_result(("Freshly generated line", NOTES_MODE_GENERATE))

    text = window._editor.toPlainText()
    assert "My important existing notes." in text  # nothing was deleted
    assert "Freshly generated line" in text  # the result was appended
    # order: existing content comes before the appended result
    assert text.index("important") < text.index("Freshly")


def test_selection_replaces_only_the_selection(window):
    window._editor.setPlainText("keep OLD keep")
    start = window._editor.toPlainText().index("OLD")
    window._ai_selection = (start, start + 3)
    window._apply_ai_result(("NEW", NOTES_MODE_SELECTION))

    assert window._editor.toPlainText().strip() == "keep NEW keep"


def test_undo_restores_pre_ai_state(window):
    window._editor.set_markdown("original")
    window._apply_ai_result(("appended", NOTES_MODE_GENERATE))
    assert "appended" in window._editor.toPlainText()
    window._undo_insert()
    assert "appended" not in window._editor.toPlainText()
    assert "original" in window._editor.toPlainText()
