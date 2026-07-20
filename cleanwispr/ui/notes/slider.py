"""SlideMicToggle — a mouse-draggable gated-shifter thumb.

A Qt port of the Flutter `SlideMicToggle` (CleanWhispr-Flutter,
lib/ui/dictation_screen.dart): drag the thumb **left** to dictate, **right**
for an AI take, **up**/**down** for the window's secondary actions. The drag is
gated like a manual shifter — once it leaves the centre detent it locks to one
axis (never diagonal), and a live bubble names the action a release would fire.
Geometry and thresholds mirror the Flutter original.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from cleanwispr.ui import theme

_TRACK_W = 216
_TRACK_H = 64
_THUMB = 56
_UP_THRESHOLD = 36.0
_GATE = 10.0  # centre detent radius: inside it the axis is undecided
_MAX_OFFSET = (_TRACK_W - _THUMB) / 2 - 4  # 76
_MAX_Y = 56.0


class SlideMicToggle(QWidget):
    """A gated-shifter voice control. Emits a directional signal on release."""

    slideLeft = Signal()
    slideRight = Signal()
    slideUp = Signal()
    slideDown = Signal()
    tapped = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_TRACK_W, _TRACK_H + 24)  # headroom for the bubble
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self.label_left = "Dictate"
        self.label_right = "AI take"
        self.label_up = ""
        self.label_down = ""

        self._recording = False
        self._busy = False
        self._offset = QPointF(0, 0)
        self._locked_axis: Qt.Orientation | None = None
        self._press_pos: QPointF | None = None
        self._moved = False

        self._home_anim = QVariantAnimation(self)
        self._home_anim.setDuration(80)
        self._home_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._home_anim.valueChanged.connect(self._on_home_step)

    # --- public state ------------------------------------------------------

    def set_recording(self, on: bool) -> None:
        if on != self._recording:
            self._recording = on
            self.update()

    def set_busy(self, on: bool) -> None:
        if on != self._busy:
            self._busy = on
            self.update()

    @property
    def _can_drag(self) -> bool:
        return not self._recording and not self._busy

    # --- geometry helpers --------------------------------------------------

    def _track_rect(self) -> QRectF:
        top = self.height() - _TRACK_H
        return QRectF(0, top, _TRACK_W, _TRACK_H)

    def _thumb_center(self) -> QPointF:
        r = self._track_rect()
        return QPointF(
            r.center().x() + self._offset.x(),
            r.center().y() + self._offset.y(),
        )

    # --- pending gesture (drives the bubble and the release action) --------

    def _pending(self) -> str | None:
        ox, oy = self._offset.x(), self._offset.y()
        vertical_dominant = abs(oy) > abs(ox)
        if oy <= -_UP_THRESHOLD and vertical_dominant:
            return "up" if self.label_up else None
        if oy >= _UP_THRESHOLD and vertical_dominant:
            return "down" if self.label_down else None
        if ox <= -_MAX_OFFSET / 2:
            return "left"
        if ox >= _MAX_OFFSET / 2:
            return "right"
        return None

    def _pending_label(self) -> str | None:
        return {
            "left": self.label_left,
            "right": self.label_right,
            "up": self.label_up,
            "down": self.label_down,
        }.get(self._pending() or "")

    # --- mouse -------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        # while a take is in flight the control is inert (drag AND tap); recording
        # still accepts a tap so it can be stopped
        if event.button() != Qt.MouseButton.LeftButton or self._busy:
            self._press_pos = None
            return
        self._press_pos = event.position()
        self._moved = False
        self._home_anim.stop()

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is None or not self._can_drag:
            return
        delta = event.position() - self._press_pos
        nx, ny = delta.x(), delta.y()
        if abs(nx) > 2 or abs(ny) > 2:
            self._moved = True
        if self._locked_axis is None and (abs(nx) > _GATE or abs(ny) > _GATE):
            self._locked_axis = (
                Qt.Orientation.Horizontal
                if abs(nx) >= abs(ny)
                else Qt.Orientation.Vertical
            )
        if self._locked_axis is Qt.Orientation.Horizontal:
            ny = 0
        elif self._locked_axis is Qt.Orientation.Vertical:
            nx = 0
        nx = max(-_MAX_OFFSET, min(_MAX_OFFSET, nx))
        ny = max(-_MAX_Y, min(_MAX_Y, ny))
        if abs(nx) < _GATE and abs(ny) < _GATE:
            self._locked_axis = None  # re-entering the detent releases the lock
        self._offset = QPointF(nx, ny)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        self._press_pos = None
        if not self._moved:
            self.tapped.emit()
            return
        pending = self._pending()
        if pending == "up" and self.label_up:
            self.slideUp.emit()
        elif pending == "down" and self.label_down:
            self.slideDown.emit()
        elif pending == "left":
            self.slideLeft.emit()
        elif pending == "right":
            self.slideRight.emit()
        self._animate_home()

    def _animate_home(self) -> None:
        self._locked_axis = None
        self._home_anim.stop()
        self._home_anim.setStartValue(self._offset)
        self._home_anim.setEndValue(QPointF(0, 0))
        self._home_anim.start()

    def _on_home_step(self, value: QPointF) -> None:
        self._offset = value
        self.update()

    # --- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track_rect()

        # pill track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.SURFACE_2))
        painter.drawRoundedRect(track, _TRACK_H / 2, _TRACK_H / 2)

        # end hints — mic (left) and a sparkle (right)
        self._draw_mic(painter, QPointF(track.left() + 22, track.center().y()))
        self._draw_sparkle(painter, QPointF(track.right() - 22, track.center().y()))

        # thumb
        center = self._thumb_center()
        thumb_rect = QRectF(0, 0, _THUMB, _THUMB)
        thumb_rect.moveCenter(center)
        color = QColor(theme.DANGER if self._recording else theme.ACCENT)
        painter.setBrush(color)
        painter.drawEllipse(thumb_rect)
        self._draw_thumb_glyph(painter, center)

        # live gesture bubble
        label = self._pending_label()
        if label and self._can_drag:
            self._draw_bubble(painter, label, center)

    def _draw_mic(self, painter: QPainter, c: QPointF) -> None:
        painter.save()
        pen = QPen(QColor(theme.MUTED))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        capsule = QRectF(c.x() - 4, c.y() - 9, 8, 13)
        painter.drawRoundedRect(capsule, 4, 4)
        painter.drawArc(QRectF(c.x() - 7, c.y() - 5, 14, 14), 200 * 16, 140 * 16)
        painter.drawLine(QPointF(c.x(), c.y() + 6), QPointF(c.x(), c.y() + 10))
        painter.restore()

    def _draw_sparkle(self, painter: QPainter, c: QPointF) -> None:
        painter.save()
        pen = QPen(QColor(theme.MUTED))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(c.x(), c.y() - 8), QPointF(c.x(), c.y() + 8))
        painter.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
        painter.drawLine(QPointF(c.x() - 5, c.y() - 5), QPointF(c.x() + 5, c.y() + 5))
        painter.drawLine(QPointF(c.x() - 5, c.y() + 5), QPointF(c.x() + 5, c.y() - 5))
        painter.restore()

    def _draw_thumb_glyph(self, painter: QPainter, c: QPointF) -> None:
        painter.save()
        pen = QPen(QColor("white"))
        pen.setWidthF(2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if self._recording:
            # stop square
            painter.setBrush(QColor("white"))
            r = QRectF(0, 0, 16, 16)
            r.moveCenter(c)
            painter.drawRoundedRect(r, 3, 3)
        elif self._busy:
            # a simple ring gap to read as "working"
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(c.x() - 9, c.y() - 9, 18, 18), 45 * 16, 270 * 16)
        else:
            # idle: a left/right double chevron advertising the drag axis
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(c.x() - 4, c.y() - 6)
            path.lineTo(c.x() - 10, c.y())
            path.lineTo(c.x() - 4, c.y() + 6)
            path.moveTo(c.x() + 4, c.y() - 6)
            path.lineTo(c.x() + 10, c.y())
            path.lineTo(c.x() + 4, c.y() + 6)
            painter.drawPath(path)
        painter.restore()

    def _draw_bubble(self, painter: QPainter, text: str, thumb_center: QPointF) -> None:
        painter.save()
        font = QFont(self.font())
        # the app font may be sized in pixels (pointSizeF() == -1); bump whichever
        # unit is actually in use rather than feeding a negative point size
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 0.5)
        elif font.pixelSize() > 0:
            font.setPixelSize(font.pixelSize() + 1)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        pad_x, pad_y = 12, 6
        w = metrics.horizontalAdvance(text) + pad_x * 2
        h = metrics.height() + pad_y * 2
        cx = max(w / 2, min(self.width() - w / 2, thumb_center.x()))
        rect = QRectF(cx - w / 2, 0, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(rect, h / 2, h / 2)
        painter.setPen(QColor("white"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
