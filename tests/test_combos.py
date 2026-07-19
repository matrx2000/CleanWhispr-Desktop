import pytest

from cleanwispr.hotkeys.combos import ComboError, combos_overlap, to_pynput


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ("ctrl+super", "shift+ctrl+super", True),  # the real-world editor killer
        ("ctrl+super", "ctrl+super", True),  # identical
        ("f9", "ctrl+f9", True),  # subset via non-modifier key
        ("ctrl+super", "f9", False),
        ("ctrl+alt+d", "ctrl+shift+e", False),  # shared modifier only is fine
        ("Ctrl+Super", "shift+ctrl+win", True),  # case/alias insensitive (win == super)
    ],
)
def test_combos_overlap(a, b, overlap):
    assert combos_overlap(a, b) is overlap
    assert combos_overlap(b, a) is overlap


@pytest.mark.parametrize(
    ("combo", "expected"),
    [
        ("ctrl+super", "<ctrl>+<cmd>"),
        ("ctrl+alt+e", "<ctrl>+<alt>+e"),
        ("f8", "<f8>"),
        ("ctrl+shift+space", "<ctrl>+<shift>+<space>"),
        ("esc", "<esc>"),
        ("Ctrl+Alt+E", "<ctrl>+<alt>+e"),
    ],
)
def test_valid_combos(combo, expected):
    assert to_pynput(combo) == expected


@pytest.mark.parametrize("combo", ["", "  ", "ctrl+", "ctrl+bogus", "notakey"])
def test_invalid_combos(combo):
    with pytest.raises(ComboError):
        to_pynput(combo)
