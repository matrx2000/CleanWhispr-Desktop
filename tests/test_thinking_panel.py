from cleanwispr.core.controller import Controller
from cleanwispr.storage.db import HistoryDb
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.thinking_panel import ThinkingPanel
from tests.test_controller import FakeEngine, FakeInjector, FakeRecorder


def make_panel(qtbot, tmp_path):
    db = HistoryDb(tmp_path / "history.db")
    settings = Settings()
    controller = Controller(settings, db, FakeRecorder(), FakeEngine(), FakeInjector())
    panel = ThinkingPanel(controller, settings)
    qtbot.addWidget(panel)
    return panel, controller, db


def test_markdown_is_rendered_not_shown_raw(qtbot, tmp_path):
    panel, controller, db = make_panel(qtbot, tmp_path)
    controller.edit_thinking.emit("**Plan:** first I will\n\n1. analyze the *text*\n")
    controller.edit_thinking.emit("2. apply the `instruction`\n")
    qtbot.waitUntil(lambda: "Plan:" in panel._view.toPlainText(), timeout=2000)

    plain = panel._view.toPlainText()
    assert "**" not in plain and "*text*" not in plain  # markup rendered, not raw
    assert "Plan:" in plain and "instruction" in plain
    assert panel.isVisible()
    controller.shutdown()
    db.close()


def test_new_recording_hides_and_next_stream_starts_fresh(qtbot, tmp_path):
    panel, controller, db = make_panel(qtbot, tmp_path)
    controller.edit_thinking.emit("old reasoning")
    qtbot.waitUntil(lambda: "old reasoning" in panel._view.toPlainText(), timeout=2000)

    controller.recording_starting.emit()
    assert not panel.isVisible()

    controller.edit_thinking.emit("new reasoning")
    qtbot.waitUntil(lambda: "new reasoning" in panel._view.toPlainText(), timeout=2000)
    assert "old reasoning" not in panel._view.toPlainText()  # cleared between sessions
    controller.shutdown()
    db.close()
