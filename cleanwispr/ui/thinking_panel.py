"""Thinking panel: a scrollable box above the overlay pill that streams the
model's reasoning during editor sessions, rendered as markdown.

Same window recipe as the pill (frameless, always-on-top, never takes
keyboard focus) but mouse-interactive so the scrollbar works. Auto-scrolls
while streaming unless the user has scrolled up to read.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.core.controller import Controller
from cleanwispr.ui.overlay import anchored_position

_WIDTH = 480
_HEIGHT = 240
_PILL_CLEARANCE = 40 + 12  # pill height + gap


class ThinkingPanel(QWidget):
    def __init__(self, controller: Controller, settings) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(_WIDTH, _HEIGHT)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        header_row = QHBoxLayout()
        header = QLabel("💭 Model reasoning")
        header.setStyleSheet("color: #b3a7f0; font-size: 11px; font-weight: bold;")
        header_row.addWidget(header)
        header_row.addStretch()
        close_button = QPushButton("✕")
        close_button.setFixedSize(20, 20)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.setStyleSheet(
            "QPushButton { background: transparent; color: #8a8f98; border: none;"
            " font-size: 12px; }"
            "QPushButton:hover { color: white; }"
        )
        close_button.clicked.connect(self.hide)
        header_row.addWidget(close_button)
        layout.addLayout(header_row)

        self._view = QTextEdit()
        self._view.setReadOnly(True)
        self._view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setStyleSheet(
            "QTextEdit { background: transparent; border: none; color: #d5d3e0;"
            " font-size: 12px; }"
            "QScrollBar:vertical { background: transparent; width: 8px; }"
            "QScrollBar::handle:vertical { background: #5a5670; border-radius: 4px;"
            " min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        layout.addWidget(self._view, 1)

        self._markdown = ""
        # streaming markdown: coalesce deltas, re-render at most every 80ms
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self._render)

        # the panel persists after the edit so the reasoning stays readable;
        # it clears away when the NEXT recording starts, or via its ✕ button
        controller.edit_thinking.connect(self._on_delta)
        controller.recording_starting.connect(self.hide)

    def _on_delta(self, text: str) -> None:
        if not self.isVisible():
            self._markdown = ""
            self._view.clear()
            self._show_positioned()
        self._markdown += text
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self) -> None:
        scrollbar = self._view.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 4  # user hasn't scrolled up
        previous_position = scrollbar.value()
        self._view.setMarkdown(self._markdown)
        scrollbar.setValue(scrollbar.maximum() if follow else previous_position)

    def paintEvent(self, event) -> None:  # Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 24, 27, 242))
        painter.drawRoundedRect(self.rect(), 14, 14)

    def _show_positioned(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            position = self._settings.ui.overlay_position
            x, y = anchored_position(position, area, self.width(), self.height())
            # sit above the pill when anchored to the bottom, below it when on top
            if position.startswith("top"):
                y += _PILL_CLEARANCE
            else:
                y -= _PILL_CLEARANCE
            self.move(x, y)
        self.show()
