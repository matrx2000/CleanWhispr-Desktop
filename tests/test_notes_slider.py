"""SlideMicToggle gesture logic: a drag past a zone threshold fires its signal.

Synthesises mouse press/move/release on the widget and asserts the gated-shifter
rules (axis lock, thresholds, sub-threshold = no fire, tap = tapped).
"""

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from cleanwispr.ui.notes.slider import SlideMicToggle


def _make(qtbot):
    widget = SlideMicToggle()
    widget.label_up = "Up"
    widget.label_down = "Down"
    qtbot.addWidget(widget)
    fired: list[str] = []
    widget.slideLeft.connect(lambda: fired.append("left"))
    widget.slideRight.connect(lambda: fired.append("right"))
    widget.slideUp.connect(lambda: fired.append("up"))
    widget.slideDown.connect(lambda: fired.append("down"))
    widget.tapped.connect(lambda: fired.append("tap"))
    return widget, fired


def _evt(kind, x, y):
    return QMouseEvent(
        kind,
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drag(widget, dx, dy):
    start = QPointF(widget.width() / 2, widget.height() - 32)
    widget.mousePressEvent(_evt(QEvent.Type.MouseButtonPress, start.x(), start.y()))
    widget.mouseMoveEvent(_evt(QEvent.Type.MouseMove, start.x() + dx, start.y() + dy))
    widget.mouseReleaseEvent(_evt(QEvent.Type.MouseButtonRelease, start.x() + dx, start.y() + dy))


def test_drag_right_fires_right(qtbot):
    widget, fired = _make(qtbot)
    _drag(widget, 60, 0)
    assert fired == ["right"]


def test_drag_left_fires_left(qtbot):
    widget, fired = _make(qtbot)
    _drag(widget, -60, 0)
    assert fired == ["left"]


def test_drag_up_fires_up(qtbot):
    widget, fired = _make(qtbot)
    _drag(widget, 0, -50)
    assert fired == ["up"]


def test_drag_down_fires_down(qtbot):
    widget, fired = _make(qtbot)
    _drag(widget, 0, 50)
    assert fired == ["down"]


def test_subthreshold_drag_fires_nothing(qtbot):
    widget, fired = _make(qtbot)
    _drag(widget, 20, 0)  # past the gate but short of the half-track fire point
    assert fired == []


def test_tap_fires_tapped(qtbot):
    widget, fired = _make(qtbot)
    start = QPointF(widget.width() / 2, widget.height() - 32)
    widget.mousePressEvent(_evt(QEvent.Type.MouseButtonPress, start.x(), start.y()))
    widget.mouseReleaseEvent(_evt(QEvent.Type.MouseButtonRelease, start.x(), start.y()))
    assert fired == ["tap"]


def test_axis_lock_prevents_diagonal(qtbot):
    """A drag that starts horizontal then veers vertical stays horizontal."""
    widget, fired = _make(qtbot)
    start = QPointF(widget.width() / 2, widget.height() - 32)
    widget.mousePressEvent(_evt(QEvent.Type.MouseButtonPress, start.x(), start.y()))
    widget.mouseMoveEvent(_evt(QEvent.Type.MouseMove, start.x() + 30, start.y()))  # lock H
    widget.mouseMoveEvent(_evt(QEvent.Type.MouseMove, start.x() + 60, start.y() - 60))
    widget.mouseReleaseEvent(_evt(QEvent.Type.MouseButtonRelease, start.x() + 60, start.y() - 60))
    assert fired == ["right"]  # never "up", despite the vertical component


def test_disabled_while_busy(qtbot):
    widget, fired = _make(qtbot)
    widget.set_busy(True)
    _drag(widget, 60, 0)
    assert fired == []  # dragging is inert while a take is in flight
