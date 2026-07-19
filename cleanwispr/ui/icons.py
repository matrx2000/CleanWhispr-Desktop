"""Programmatically drawn tray icons — one per app state, no asset files needed."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap

from cleanwispr.core.controller import AppState

_STATE_COLORS = {
    AppState.IDLE: "#8a8f98",
    AppState.RECORDING: "#e5484d",
    AppState.TRANSCRIBING: "#f5a524",
    AppState.EDITING: "#7c66dc",
    AppState.INJECTING: "#30a46c",
    AppState.ERROR: "#e5484d",
}


def state_icon(state: AppState, size: int = 64) -> QIcon:
    """A microphone glyph in a colored circle."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    color = QColor(_STATE_COLORS[state])
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(2, 2, size - 4, size - 4))

    # mic: capsule body + arc stand + base line, in white
    pen = QPen(QColor("white"))
    pen.setWidthF(size * 0.06)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(QBrush(QColor("white")))
    body = QRectF(size * 0.40, size * 0.22, size * 0.20, size * 0.34)
    painter.drawRoundedRect(body, size * 0.10, size * 0.10)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    arc = QRectF(size * 0.32, size * 0.30, size * 0.36, size * 0.36)
    painter.drawArc(arc, 180 * 16, 180 * 16)
    painter.drawLine(
        QRectF(size * 0.5, size * 0.66, 0, size * 0.10).topLeft(),
        QRectF(size * 0.5, size * 0.66, 0, size * 0.10).bottomLeft(),
    )
    painter.end()
    return QIcon(pixmap)
