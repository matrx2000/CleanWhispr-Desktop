"""Crisp vector toolbar icons drawn with QPainter — no icon-font dependency.

Each icon is stroked in the theme text colour at 2x for retina sharpness and
returned as a QIcon sized for the notes toolbar.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from cleanwispr.ui import theme

_SIZE = 18


def _base_font() -> QFont:
    """The resolved application font — the painter's own default is unresolved
    (family-less) on this Qt build and renders letters as tofu boxes."""
    app = QApplication.instance()
    return QFont(app.font()) if app is not None else QFont("Segoe UI")


def _icon(draw: Callable[[QPainter], None], *, fill: str | None = None) -> QIcon:
    dpr = 2
    pixmap = QPixmap(_SIZE * dpr, _SIZE * dpr)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # drawing on a devicePixelRatio pixmap already maps logical (0.._SIZE) coords
    # to device pixels — no extra painter.scale() (that would double-apply)
    pen = QPen(QColor(theme.TEXT))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter)
    painter.end()
    return QIcon(pixmap)


def _glyph(letter: str, *, bold=False, italic=False, underline=False) -> QIcon:
    def draw(p: QPainter) -> None:
        font = _base_font()
        font.setPixelSize(13)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        p.setFont(font)
        p.drawText(QRectF(0, 0, _SIZE, _SIZE), Qt.AlignmentFlag.AlignCenter, letter)

    return _icon(draw)


def bold() -> QIcon:
    return _glyph("B", bold=True)


def italic() -> QIcon:
    return _glyph("I", italic=True)


def underline() -> QIcon:
    return _glyph("U", underline=True)


def heading(level: int) -> QIcon:
    return _glyph(f"H{level}", bold=True)


def paragraph() -> QIcon:
    return _glyph("¶")


def code() -> QIcon:
    def draw(p: QPainter) -> None:
        p.drawPolyline([_pt(7, 5), _pt(3, 9), _pt(7, 13)])
        p.drawPolyline([_pt(11, 5), _pt(15, 9), _pt(11, 13)])

    return _icon(draw)


def bullet_list() -> QIcon:
    def draw(p: QPainter) -> None:
        for y in (5, 9, 13):
            p.setBrush(QColor(theme.TEXT))
            p.drawEllipse(_pt(4, y), 1.2, 1.2)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(_pt(7.5, y), _pt(15, y))

    return _icon(draw)


def numbered_list() -> QIcon:
    def draw(p: QPainter) -> None:
        font = _base_font()
        font.setPixelSize(7)
        font.setBold(True)
        for i, y in enumerate((5, 9, 13), start=1):
            p.setFont(font)
            p.drawText(QRectF(1, y - 4.5, 5, 9), Qt.AlignmentFlag.AlignCenter, str(i))
            p.drawLine(_pt(7.5, y), _pt(15, y))

    return _icon(draw)


def checklist() -> QIcon:
    def draw(p: QPainter) -> None:
        for y in (5, 12):
            p.drawPolyline([_pt(2.5, y), _pt(4, y + 1.6), _pt(6.5, y - 1.8)])
            p.drawLine(_pt(8.5, y), _pt(15, y))

    return _icon(draw)


def table() -> QIcon:
    def draw(p: QPainter) -> None:
        p.drawRoundedRect(QRectF(3, 4, 12, 10), 1.5, 1.5)
        p.drawLine(_pt(9, 4), _pt(9, 14))
        p.drawLine(_pt(3, 9), _pt(15, 9))

    return _icon(draw)


def highlight() -> QIcon:
    def draw(p: QPainter) -> None:
        pen = p.pen()
        pen.setWidthF(3.0)
        pen.setColor(QColor("#f7d154"))
        p.setPen(pen)
        p.drawLine(_pt(4, 12), _pt(12, 4))
        pen.setWidthF(1.4)
        pen.setColor(QColor(theme.TEXT))
        p.setPen(pen)
        p.drawLine(_pt(3, 15), _pt(9, 15))

    return _icon(draw)


def text_color(swatch: str = theme.ACCENT) -> QIcon:
    def draw(p: QPainter) -> None:
        path = QPainterPath()
        path.moveTo(*_xy(9, 4))
        path.lineTo(*_xy(5.5, 12))
        path.moveTo(*_xy(9, 4))
        path.lineTo(*_xy(12.5, 12))
        path.moveTo(*_xy(6.6, 9.5))
        path.lineTo(*_xy(11.4, 9.5))
        p.drawPath(path)
        bar = p.pen()
        bar.setWidthF(2.6)
        bar.setColor(QColor(swatch))
        p.setPen(bar)
        p.drawLine(_pt(3.5, 15.5), _pt(14.5, 15.5))

    return _icon(draw)


def export_markdown() -> QIcon:
    def draw(p: QPainter) -> None:
        p.drawLine(_pt(9, 3), _pt(9, 11))
        p.drawPolyline([_pt(6, 8), _pt(9, 11), _pt(12, 8)])
        p.drawLine(_pt(4, 14.5), _pt(14, 14.5))

    return _icon(draw)


def _pt(x: float, y: float):
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


def _xy(x: float, y: float):
    return (x, y)
