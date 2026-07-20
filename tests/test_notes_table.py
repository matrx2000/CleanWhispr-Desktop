"""Table operations: insert, add/move rows & columns, merge/split, delete."""

from PySide6.QtGui import QTextCursor, QTextTable

from cleanwispr.ui.notes import table as T
from cleanwispr.ui.notes.editor import NoteEditor
from cleanwispr.ui.notes.table import TableConfig


def _editor(qtbot, tmp_path):
    editor = NoteEditor(tmp_path)
    qtbot.addWidget(editor)
    return editor


def _table(editor) -> QTextTable | None:
    """Locate the note's table via the document, not the (possibly displaced) cursor."""
    root = editor.document().rootFrame()
    for child in root.childFrames():
        if isinstance(child, QTextTable):
            return child
    return None


def _set_cell(editor, row, col, text):
    cur = _table(editor).cellAt(row, col).firstCursorPosition()
    cur.insertText(text)


def _cell_text(editor, row, col):
    cell = _table(editor).cellAt(row, col)
    cur = cell.firstCursorPosition()
    cur.setPosition(cell.lastCursorPosition().position(), QTextCursor.MoveMode.KeepAnchor)
    return cur.selection().toPlainText()


def _put_cursor(editor, row, col):
    editor.setTextCursor(_table(editor).cellAt(row, col).firstCursorPosition())


def test_insert_table_sets_dimensions(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.insert_table(TableConfig(rows=3, cols=4, header=True))
    assert editor.in_table()
    table = _table(editor)
    assert table.rows() == 3 and table.columns() == 4
    assert table.format().headerRowCount() == 1


def test_add_and_delete_rows_columns(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.insert_table(TableConfig(rows=2, cols=2, header=False))
    _put_cursor(editor, 0, 0)
    editor.table_insert_row_below()
    assert _table(editor).rows() == 3
    _put_cursor(editor, 0, 0)
    editor.table_insert_col_right()
    assert _table(editor).columns() == 3
    _put_cursor(editor, 0, 0)
    editor.table_delete_row()
    assert _table(editor).rows() == 2
    _put_cursor(editor, 0, 0)
    editor.table_delete_col()
    assert _table(editor).columns() == 2


def test_move_row_swaps_content(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.insert_table(TableConfig(rows=2, cols=1, header=False))
    _set_cell(editor, 0, 0, "top")
    _set_cell(editor, 1, 0, "bottom")
    _put_cursor(editor, 1, 0)
    editor.table_move_row_up()
    assert _cell_text(editor, 0, 0) == "bottom"
    assert _cell_text(editor, 1, 0) == "top"


def test_move_column_swaps_content(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.insert_table(TableConfig(rows=1, cols=2, header=False))
    _set_cell(editor, 0, 0, "left")
    _set_cell(editor, 0, 1, "right")
    _put_cursor(editor, 0, 0)
    editor.table_move_col_right()
    assert _cell_text(editor, 0, 0) == "right"
    assert _cell_text(editor, 0, 1) == "left"


def test_merge_and_split_cells(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.insert_table(TableConfig(rows=1, cols=2, header=False))
    table = T.current_table(editor)
    cur = table.cellAt(0, 0).firstCursorPosition()
    end = table.cellAt(0, 1).lastCursorPosition().position()
    cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cur)
    editor.table_merge()
    assert T.current_table(editor).cellAt(0, 0).columnSpan() == 2

    _put_cursor(editor, 0, 0)
    editor.table_split()
    assert T.current_table(editor).cellAt(0, 0).columnSpan() == 1


def test_delete_table_removes_it(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.insert_table(TableConfig(rows=2, cols=2))
    assert editor.in_table()
    _put_cursor(editor, 0, 0)
    editor.table_delete()
    assert not editor.in_table()


def test_properties_reads_and_reapplies(qtbot, tmp_path):
    editor = _editor(qtbot, tmp_path)
    editor.insert_table(TableConfig(rows=2, cols=2, padding=6, spacing=0))
    _put_cursor(editor, 0, 0)
    cfg = editor.table_config()
    assert cfg is not None
    cfg.padding = 12
    editor.apply_table_properties(cfg)
    assert int(T.current_table(editor).format().cellPadding()) == 12
