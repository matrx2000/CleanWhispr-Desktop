"""Polish-batch units: overlay anchoring math and autostart command."""

import sys
from dataclasses import dataclass

import pytest

from cleanwispr.autostart import launch_command
from cleanwispr.ui.overlay import anchored_position


@dataclass
class Area:
    _left: int = 0
    _top: int = 0
    _width: int = 1920
    _height: int = 1040  # available area (minus taskbar)

    def left(self):
        return self._left

    def top(self):
        return self._top

    def width(self):
        return self._width

    def right(self):
        return self._left + self._width - 1

    def bottom(self):
        return self._top + self._height - 1


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("bottom-right", (1919 - 240 - 24, 1039 - 40 - 24)),
        ("bottom-left", (24, 1039 - 40 - 24)),
        ("bottom-center", ((1920 - 240) // 2, 1039 - 40 - 24)),
        ("top-right", (1919 - 240 - 24, 24)),
        ("top-left", (24, 24)),
    ],
)
def test_anchored_position(position, expected):
    assert anchored_position(position, Area(), 240, 40) == expected


def test_launch_command_dev_mode():
    command = launch_command()
    # repo has a root main.py, so autostart must use it (works without pip install -e)
    assert "main.py" in command
    assert command.startswith('"')
    if sys.platform == "win32":
        assert "pythonw" in command.lower() or "python" in command.lower()
