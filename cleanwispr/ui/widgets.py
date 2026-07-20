"""Small shared UI helpers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.ui import theme

ACCENT_SOFT = "#b3a7f0"  # lightened accent for text on dark chip backgrounds


class PathLink(QLabel):
    """A file-system path rendered as a clickable link. Clicking asks before
    opening the folder in the system file manager — nothing happens silently."""

    def __init__(
        self, path: str | Path, prefix: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._prefix = prefix
        self.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setWordWrap(True)
        self.linkActivated.connect(self._confirm_open)
        self._render()

    def set_path(self, path: str | Path) -> None:
        self._path = Path(path)
        self._render()

    def _render(self) -> None:
        self.setText(
            f'{self._prefix}<a href="open" style="color:{ACCENT_SOFT}; '
            f'text-decoration:none;">{self._path}</a>'
        )
        self.setToolTip("Click to open in your file manager")

    def _confirm_open(self) -> None:
        folder = self._path if self._path.is_dir() else self._path.parent
        reply = QMessageBox.question(
            self,
            "Open folder",
            f"Open this folder in your file manager?\n\n{folder}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def intro_label(text: str) -> QLabel:
    """Muted, wrapped explainer shown at the top of a settings tab."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #8a8f98; padding: 2px 2px 8px 2px;")
    return label


def _lerp_color(start: QColor, end: QColor, t: float) -> QColor:
    return QColor(
        int(start.red() + (end.red() - start.red()) * t),
        int(start.green() + (end.green() - start.green()) * t),
        int(start.blue() + (end.blue() - start.blue()) * t),
    )


_MODEL_ROW_QSS = f"""
QFrame#modelRow {{
    background: {theme.SURFACE_2};
    border: 1px solid {theme.BORDER};
    border-radius: 8px;
}}
QFrame#modelRow[active="true"] {{ border: 1px solid {theme.ACCENT}; }}
QLabel {{ background: transparent; border: none; }}
QLabel#rowTitle {{ font-size: 13px; font-weight: 600; color: {theme.TEXT}; }}
QLabel#rowSubtitle {{ font-size: 11px; color: {theme.MUTED}; }}
QLabel#rowTag {{
    font-size: 10px; font-weight: 600; color: {ACCENT_SOFT};
    background: rgba(124, 102, 220, 0.14); border-radius: 8px; padding: 2px 9px;
}}
QLabel#rowBadgeActive {{
    font-size: 10px; font-weight: 700; color: {ACCENT_SOFT};
    background: rgba(124, 102, 220, 0.22); border-radius: 10px; padding: 4px 12px;
}}
QLabel#rowBadgeInstalled {{
    font-size: 10px; font-weight: 600; color: {theme.MUTED};
    background: rgba(138, 143, 152, 0.14); border-radius: 10px; padding: 4px 12px;
}}
QPushButton {{
    font-size: 11px; font-weight: 600; border-radius: 6px; padding: 3px 14px;
}}
QPushButton#rowUse {{
    background: {theme.ACCENT}; color: white; border: 1px solid {theme.ACCENT};
}}
QPushButton#rowUse:hover {{ background: #8d78e6; border-color: #8d78e6; }}
QPushButton#rowDownload {{
    color: {ACCENT_SOFT}; border: 1px solid {theme.ACCENT}; background: transparent;
}}
QPushButton#rowDownload:hover {{ background: rgba(124, 102, 220, 0.15); }}
QPushButton#rowDelete {{
    color: {theme.MUTED}; border: 1px solid transparent; background: transparent;
}}
QPushButton#rowDelete:hover {{
    color: {theme.DANGER}; border-color: rgba(229, 72, 77, 0.45);
    background: rgba(229, 72, 77, 0.08);
}}
QProgressBar {{
    background: {theme.BG}; border: none; border-radius: 3px;
    min-height: 6px; max-height: 6px;
}}
QProgressBar::chunk {{ background: {theme.ACCENT}; border-radius: 3px; }}
"""


class ModelRow(QFrame):
    """Card-style row for the model/engine manager: name + description on the
    left; a state badge, slim progress bar, and compact actions on the right.

    Two flavors: `usable=True` (models — Use/Download/Delete + ACTIVE badge,
    accent border when active) and `usable=False` (engine binaries —
    Download/Reinstall + INSTALLED badge).
    """

    download_clicked = Signal()
    delete_clicked = Signal()
    use_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        tag: str | None = None,
        usable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modelRow")
        self.setStyleSheet(_MODEL_ROW_QSS)
        self._usable = usable
        self._installed = False
        self._active = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 12, 9)
        layout.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("rowTitle")
        title_row.addWidget(title_label)
        self._tag_label = QLabel(tag or "")
        self._tag_label.setObjectName("rowTag")
        self._tag_label.setVisible(bool(tag))
        title_row.addWidget(self._tag_label)
        title_row.addStretch()
        text_col.addLayout(title_row)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("rowSubtitle")
        text_col.addWidget(subtitle_label)
        layout.addLayout(text_col, 1)

        self._progress = QProgressBar()
        self._progress.setFixedWidth(120)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._badge = QLabel("ACTIVE" if usable else "INSTALLED")
        self._badge.setObjectName("rowBadgeActive" if usable else "rowBadgeInstalled")
        self._badge.setVisible(False)
        layout.addWidget(self._badge)

        def _button(text: str, name: str, signal: Signal) -> QPushButton:
            button = QPushButton(text)
            button.setObjectName(name)
            button.setFixedHeight(26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(signal.emit)
            button.setVisible(False)
            layout.addWidget(button)
            return button

        self._use = _button("Use", "rowUse", self.use_clicked)
        self._download = _button("Download", "rowDownload", self.download_clicked)
        self._delete = _button("Delete", "rowDelete", self.delete_clicked)
        # shown only mid-download (see set_busy); lets the user abort a big fetch
        self._cancel = _button("Cancel", "rowDelete", self.cancel_clicked)

    def set_state(self, installed: bool, active: bool = False) -> None:
        self._installed, self._active = installed, active
        if self._usable:
            self._badge.setVisible(installed and active)
            self._use.setVisible(installed and not active)
            self._download.setVisible(not installed)
            self._delete.setVisible(installed)
        else:
            self._badge.setVisible(installed)
            self._download.setText("Reinstall" if installed else "Download")
            self._download.setVisible(True)
        highlight = self._usable and installed and active
        self.setProperty("active", "true" if highlight else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_busy(self, busy: bool) -> None:
        """While a download runs: hide the normal actions, show the slim progress
        bar and a Cancel button."""
        self._progress.setVisible(busy)
        self._cancel.setVisible(busy)
        if busy:
            self._progress.setRange(0, 0)  # indeterminate until the size is known
            for widget in (self._badge, self._use, self._download, self._delete):
                widget.setVisible(False)
        else:
            self.set_state(self._installed, self._active)

    def set_progress(self, received: int, total: object) -> None:
        if isinstance(total, int) and total > 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(int(received * 100 / total))

    def set_tag(self, tag: str | None) -> None:
        """Show/replace/clear the small pill next to the title (e.g. a runtime
        'Recommended for your GPU' marker)."""
        self._tag_label.setText(tag or "")
        self._tag_label.setVisible(bool(tag))


class ToggleSwitch(QWidget):
    """A sliding pill-shaped on/off switch — a drop-in for QCheckBox's boolean state."""

    toggled = Signal(bool)

    _WIDTH = 40
    _HEIGHT = 22
    _PADDING = 3

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._WIDTH, self._HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._knob_pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knobPosition", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._knob_pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        self.toggled.emit(checked)

    def mousePressEvent(self, event) -> None:  # Qt override
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mousePressEvent(event)

    def _get_knob_position(self) -> float:
        return self._knob_pos

    def _set_knob_position(self, value: float) -> None:
        self._knob_pos = value
        self.update()

    knobPosition = Property(float, _get_knob_position, _set_knob_position)

    def paintEvent(self, event) -> None:  # Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        off_color = QColor(theme.BORDER)
        on_color = QColor(theme.MUTED if not self.isEnabled() else theme.ACCENT)
        painter.setBrush(_lerp_color(off_color, on_color, self._knob_pos))
        painter.drawRoundedRect(
            QRectF(0, 0, self._WIDTH, self._HEIGHT), self._HEIGHT / 2, self._HEIGHT / 2
        )

        diameter = self._HEIGHT - 2 * self._PADDING
        travel = self._WIDTH - self._HEIGHT
        knob_x = self._PADDING + self._knob_pos * travel
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(knob_x, self._PADDING, diameter, diameter))


class LabeledToggle(QWidget):
    """A ToggleSwitch with a text label — drop-in replacement for a labeled QCheckBox."""

    toggled = Signal(bool)

    def __init__(self, text: str, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        self.switch = ToggleSwitch(checked)
        self.switch.toggled.connect(self.toggled)
        layout.addWidget(self.switch)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setChecked(self, checked: bool) -> None:
        self.switch.setChecked(checked)

    def setToolTip(self, tip: str) -> None:  # Qt override
        super().setToolTip(tip)
        self.switch.setToolTip(tip)
