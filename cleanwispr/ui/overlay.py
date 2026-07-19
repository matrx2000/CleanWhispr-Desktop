"""Floating overlay pill: recording/processing state near the screen edge.

Window recipe ported from OpenWhispr's windowConfig.js — frameless,
translucent, always-on-top, never steals focus from the app being dictated
into. Hidden while idle; flashes notices/errors briefly.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from cleanwispr.core.controller import AppState, Controller

_STATE_TEXT = {
    AppState.RECORDING: "● Recording — click to stop",
    AppState.TRANSCRIBING: "… Transcribing",
    AppState.EDITING: "… Applying edit",
    AppState.INJECTING: "✓ Pasting",
}

_MIC_WARMUP_TEXT = "Starting microphone…"

_STATE_COLOR = {
    AppState.RECORDING: "#e5484d",
    AppState.TRANSCRIBING: "#f5a524",
    AppState.EDITING: "#7c66dc",
    AppState.INJECTING: "#30a46c",
}

_FLASH_MS = 1800
_MIN_WIDTH = 240
_MAX_WIDTH = 480


def anchored_position(
    position: str, area, width: int, height: int, margin: int = 24
) -> tuple[int, int]:
    """Top-left point for a widget anchored per the overlay_position setting."""
    if "left" in position:
        x = area.left() + margin
    elif "center" in position:
        x = area.left() + (area.width() - width) // 2
    else:  # right
        x = area.right() - width - margin
    y = area.top() + margin if position.startswith("top") else area.bottom() - height - margin
    return x, y


class OverlayPill(QWidget):
    def __init__(self, controller: Controller, settings) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(_MIN_WIDTH, 40)

        self._controller = controller
        self._settings = settings
        self._text = ""
        self._color = QColor("#8a8f98")
        self._level = 0.0
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        controller.state_changed.connect(self._on_state)
        controller.notice.connect(lambda msg: self._flash(msg, "#8a8f98"))
        controller.error_occurred.connect(lambda msg: self._flash(msg, "#e5484d"))
        controller.edit_status.connect(self._on_edit_status)
        controller.level_changed.connect(self._on_level)
        controller.mic_ready.connect(self._on_mic_ready)

    # --- reactions ---

    def _on_state(self, state: AppState) -> None:
        if state is AppState.RECORDING:
            # amber until the mic actually delivers audio (slow Bluetooth startup);
            # mic_ready flips it to the red recording display
            self._flash_timer.stop()
            self._display(_MIC_WARMUP_TEXT, "#f5a524")
        elif state in _STATE_TEXT:
            self._flash_timer.stop()
            self._display(_STATE_TEXT[state], _STATE_COLOR[state])
        elif state is AppState.IDLE and not self._flash_timer.isActive():
            self.hide()

    def _on_mic_ready(self) -> None:
        if self._controller.state is AppState.RECORDING:
            self._display(_STATE_TEXT[AppState.RECORDING], _STATE_COLOR[AppState.RECORDING])

    def _on_edit_status(self, message: str) -> None:
        """Editor-session narration: sticky until the state moves on."""
        if self._controller.state is not AppState.IDLE:
            self._flash_timer.stop()
            self._display(message, _STATE_COLOR[AppState.EDITING])

    def _flash(self, message: str, color: str) -> None:
        self._display(message, color)
        self._flash_timer.start(_FLASH_MS)

    def _display(self, text: str, color: str) -> None:
        metrics = QFontMetrics(QFont(self.font().family(), 9))
        width = max(_MIN_WIDTH, min(_MAX_WIDTH, metrics.horizontalAdvance(text) + 64))
        self._text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, width - 54)
        self._color = QColor(color)
        self.setFixedSize(width, 40)
        self._show_positioned()
        self.update()

    def _end_flash(self) -> None:
        if self._controller.state is AppState.IDLE:
            self.hide()

    def _on_level(self, rms: float) -> None:
        self._level = min(1.0, rms * 25)
        if self.isVisible() and self._controller.state is AppState.RECORDING:
            self.update()

    # --- widget behavior ---

    def mousePressEvent(self, event) -> None:  # Qt override
        if self._controller.state is AppState.RECORDING:
            self._controller.toggle_dictation()

    def paintEvent(self, event) -> None:  # Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 24, 27, 235))
        painter.drawRoundedRect(self.rect(), 20, 20)

        # state dot, level-reactive while recording
        radius = 5 + (4 * self._level if self._controller.state is AppState.RECORDING else 0)
        painter.setBrush(self._color)
        painter.drawEllipse(
            int(20 - radius), int(self.height() / 2 - radius), int(radius * 2), int(radius * 2)
        )

        painter.setPen(QColor("white"))
        painter.setFont(QFont(self.font().family(), 9))
        painter.drawText(
            self.rect().adjusted(38, 0, -10, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )

    def _show_positioned(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x, y = anchored_position(
                self._settings.ui.overlay_position, area, self.width(), self.height()
            )
            self.move(x, y)
        self.show()
