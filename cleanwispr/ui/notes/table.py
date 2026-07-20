"""Rich tables for the notes editor: an insert/properties dialog and a full set
of structural operations (add / remove / move rows & columns, merge / split,
delete). Everything drives Qt's native ``QTextTable`` — the same model the HTML
serialiser round-trips — so borders, padding, spacing, alignment and a styled
header row all persist with the note.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QTextCharFormat,
    QTextFrameFormat,
    QTextLength,
    QTextTable,
    QTextTableFormat,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
)

from cleanwispr.ui import theme

_ALIGN = [
    ("Left", Qt.AlignmentFlag.AlignLeft),
    ("Center", Qt.AlignmentFlag.AlignHCenter),
    ("Right", Qt.AlignmentFlag.AlignRight),
]


@dataclass
class TableConfig:
    rows: int = 3
    cols: int = 3
    header: bool = True
    border: int = 1
    padding: int = 6
    spacing: int = 0
    full_width: bool = True
    alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft


class TableDialog(QDialog):
    """Configure a new table, or edit an existing one's properties."""

    def __init__(self, parent=None, config: TableConfig | None = None, properties: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Table properties" if properties else "Insert table")
        self.setMinimumWidth(320)
        cfg = config or TableConfig()
        form = QFormLayout(self)

        self._rows = QSpinBox()
        self._rows.setRange(1, 100)
        self._rows.setValue(cfg.rows)
        self._cols = QSpinBox()
        self._cols.setRange(1, 40)
        self._cols.setValue(cfg.cols)
        if not properties:  # editing an existing table doesn't resize it here
            form.addRow("Rows:", self._rows)
            form.addRow("Columns:", self._cols)

        self._header = QCheckBox("First row is a header")
        self._header.setChecked(cfg.header)
        form.addRow(self._header)

        self._border = QSpinBox()
        self._border.setRange(0, 6)
        self._border.setValue(cfg.border)
        form.addRow("Border width:", self._border)

        self._padding = QSpinBox()
        self._padding.setRange(0, 40)
        self._padding.setValue(cfg.padding)
        self._padding.setSuffix(" px")
        form.addRow("Cell padding:", self._padding)

        self._spacing = QSpinBox()
        self._spacing.setRange(0, 40)
        self._spacing.setValue(cfg.spacing)
        self._spacing.setSuffix(" px")
        form.addRow("Cell spacing:", self._spacing)

        self._width = QComboBox()
        self._width.addItem("Full width", True)
        self._width.addItem("Fit contents", False)
        self._width.setCurrentIndex(0 if cfg.full_width else 1)
        form.addRow("Width:", self._width)

        self._align = QComboBox()
        for label, value in _ALIGN:
            self._align.addItem(label, value)
        self._align.setCurrentIndex(
            next((i for i, (_, v) in enumerate(_ALIGN) if v == cfg.alignment), 0)
        )
        form.addRow("Alignment:", self._align)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def config(self) -> TableConfig:
        return TableConfig(
            rows=self._rows.value(),
            cols=self._cols.value(),
            header=self._header.isChecked(),
            border=self._border.value(),
            padding=self._padding.value(),
            spacing=self._spacing.value(),
            full_width=self._width.currentData(),
            alignment=self._align.currentData(),
        )


# --- format building --------------------------------------------------------


def build_format(cfg: TableConfig) -> QTextTableFormat:
    fmt = QTextTableFormat()
    fmt.setBorder(cfg.border)
    fmt.setBorderStyle(
        QTextFrameFormat.BorderStyle.BorderStyle_Solid
        if cfg.border
        else QTextFrameFormat.BorderStyle.BorderStyle_None
    )
    fmt.setBorderBrush(QColor(theme.BORDER))
    fmt.setBorderCollapse(cfg.spacing == 0)
    fmt.setCellPadding(cfg.padding)
    fmt.setCellSpacing(cfg.spacing)
    fmt.setAlignment(cfg.alignment)
    fmt.setHeaderRowCount(1 if cfg.header else 0)
    if cfg.full_width:
        fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
    else:
        fmt.setWidth(QTextLength(QTextLength.Type.VariableLength, 0))
    return fmt


def read_config(table: QTextTable) -> TableConfig:
    fmt = table.format()
    width = fmt.width()
    return TableConfig(
        rows=table.rows(),
        cols=table.columns(),
        header=fmt.headerRowCount() > 0,
        border=int(fmt.border()),
        padding=int(fmt.cellPadding()),
        spacing=int(fmt.cellSpacing()),
        full_width=width.type() == QTextLength.Type.PercentageLength,
        alignment=fmt.alignment(),
    )


def style_header(table: QTextTable, header: bool) -> None:
    """Tint + embolden the first row when it's a header (or clear it)."""
    if table.rows() == 0:
        return
    for col in range(table.columns()):
        cell = table.cellAt(0, col)
        cell_fmt = cell.format()
        if header:
            cell_fmt.setBackground(QColor(theme.SURFACE_2))
        else:
            cell_fmt.clearBackground()
        cell.setFormat(cell_fmt)
        cursor = cell.firstCursorPosition()
        cursor.setPosition(cell.lastCursorPosition().position(), cursor.MoveMode.KeepAnchor)
        char = QTextCharFormat()
        char.setFontWeight(700 if header else 400)
        cursor.mergeCharFormat(char)


# --- structural operations --------------------------------------------------


def current_table(edit) -> QTextTable | None:
    return edit.textCursor().currentTable()


def insert_table(edit, cfg: TableConfig) -> None:
    cursor = edit.textCursor()
    table = cursor.insertTable(max(1, cfg.rows), max(1, cfg.cols), build_format(cfg))
    style_header(table, cfg.header)
    edit.setTextCursor(table.cellAt(0, 0).firstCursorPosition())


def apply_properties(edit, cfg: TableConfig) -> None:
    table = current_table(edit)
    if table is None:
        return
    table.setFormat(build_format(cfg))
    style_header(table, cfg.header)


def _cell_index(edit, table: QTextTable):
    cell = table.cellAt(edit.textCursor())
    return cell.row(), cell.column()


def insert_row(edit, below: bool) -> None:
    table = current_table(edit)
    if table is None:
        return
    row, _ = _cell_index(edit, table)
    table.insertRows(row + (1 if below else 0), 1)


def insert_column(edit, right: bool) -> None:
    table = current_table(edit)
    if table is None:
        return
    _, col = _cell_index(edit, table)
    table.insertColumns(col + (1 if right else 0), 1)


def delete_row(edit) -> None:
    table = current_table(edit)
    if table is None or table.rows() <= 1:
        return
    row, _ = _cell_index(edit, table)
    table.removeRows(row, 1)


def delete_column(edit) -> None:
    table = current_table(edit)
    if table is None or table.columns() <= 1:
        return
    _, col = _cell_index(edit, table)
    table.removeColumns(col, 1)


def delete_table(edit) -> None:
    table = current_table(edit)
    if table is None:
        return
    # select from just before the table frame to just after it, then delete
    sel = edit.textCursor()
    sel.beginEditBlock()
    sel.setPosition(max(0, table.firstPosition() - 1))
    sel.setPosition(table.lastPosition() + 1, sel.MoveMode.KeepAnchor)
    sel.removeSelectedText()
    sel.endEditBlock()


def merge_cells(edit) -> None:
    table = current_table(edit)
    if table is None:
        return
    cursor = edit.textCursor()
    if cursor.hasSelection():
        table.mergeCells(cursor)


def split_cell(edit) -> None:
    table = current_table(edit)
    if table is None:
        return
    cell = table.cellAt(edit.textCursor())
    if cell.rowSpan() > 1 or cell.columnSpan() > 1:
        table.splitCell(cell.row(), cell.column(), 1, 1)


def move_row(edit, delta: int) -> None:
    table = current_table(edit)
    if table is None:
        return
    row, _ = _cell_index(edit, table)
    other = row + delta
    if 0 <= other < table.rows():
        _swap_line(edit, table, row, other, axis="row")


def move_column(edit, delta: int) -> None:
    table = current_table(edit)
    if table is None:
        return
    _, col = _cell_index(edit, table)
    other = col + delta
    if 0 <= other < table.columns():
        _swap_line(edit, table, col, other, axis="col")


def _swap_line(edit, table: QTextTable, a: int, b: int, axis: str) -> None:
    """Swap the rich content of two rows (axis='row') or columns (axis='col')."""
    count = table.columns() if axis == "row" else table.rows()

    def cell(i: int, line: int):
        # i walks across the line; `line` is the row/column being addressed
        return table.cellAt(line, i) if axis == "row" else table.cellAt(i, line)

    cursor = edit.textCursor()
    cursor.beginEditBlock()
    frags_a = [_cell_fragment(cell(i, a)) for i in range(count)]
    frags_b = [_cell_fragment(cell(i, b)) for i in range(count)]
    for i in range(count):
        _set_cell_fragment(cell(i, a), frags_b[i])
    for i in range(count):
        _set_cell_fragment(cell(i, b), frags_a[i])
    cursor.endEditBlock()


def _cell_fragment(cell):
    cursor = cell.firstCursorPosition()
    cursor.setPosition(cell.lastCursorPosition().position(), cursor.MoveMode.KeepAnchor)
    return cursor.selection()


def _set_cell_fragment(cell, fragment) -> None:
    cursor = cell.firstCursorPosition()
    cursor.setPosition(cell.lastCursorPosition().position(), cursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    cursor.insertFragment(fragment)
