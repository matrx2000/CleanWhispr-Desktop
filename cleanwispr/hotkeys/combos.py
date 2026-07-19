"""Combo string parsing — canonical "ctrl+super" style → pynput syntax,
plus overlap detection for conflict validation.

Pure logic (no pynput import) so it's testable on any platform.

Overlapping combos (one's key set contained in the other's) are forbidden at
assignment time: with subset matching, pressing the larger chord would also
trigger the smaller one — the bug that silently killed editor sessions.
"""

from __future__ import annotations

_MODIFIER_MAP = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "super": "super",
    "win": "super",
    "cmd": "super",
}

_NAMED_KEYS = {
    "esc": "esc",
    "escape": "esc",
    "space": "space",
    "tab": "tab",
    "enter": "enter",
    "backspace": "backspace",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "pagedown": "page_down",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "capslock": "caps_lock",
    "pause": "pause",
}

_MODIFIER_TO_PYNPUT = {"ctrl": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "super": "<cmd>"}


class ComboError(ValueError):
    pass


def _tokens(combo: str) -> list[str]:
    """Canonical tokens ("ctrl", "super", "e", "f8", "space"...), validated."""
    if not combo or not combo.strip():
        raise ComboError("Empty hotkey combo")
    tokens = []
    for raw in combo.lower().split("+"):
        token = raw.strip()
        if not token:
            raise ComboError(f"Malformed combo: {combo!r}")
        if token in _MODIFIER_MAP:
            tokens.append(_MODIFIER_MAP[token])
        elif token in _NAMED_KEYS:
            tokens.append(_NAMED_KEYS[token])
        elif (len(token) >= 2 and token[0] == "f" and token[1:].isdigit()) or len(token) == 1:
            tokens.append(token)
        else:
            raise ComboError(f"Unknown key {token!r} in combo {combo!r}")
    return tokens


def to_pynput(combo: str) -> str:
    """"ctrl+alt+e" → "<ctrl>+<alt>+e"; "f8" → "<f8>"."""
    parts = []
    for token in _tokens(combo):
        if token in _MODIFIER_TO_PYNPUT:
            parts.append(_MODIFIER_TO_PYNPUT[token])
        elif len(token) == 1:
            parts.append(token)
        else:
            parts.append(f"<{token}>")
    return "+".join(parts)


def combo_keys(combo: str) -> frozenset[str]:
    """Canonical key set, for overlap comparison."""
    return frozenset(_tokens(combo))


def combos_overlap(a: str, b: str) -> bool:
    """True if one combo's keys are contained in the other's (incl. equal)."""
    keys_a, keys_b = combo_keys(a), combo_keys(b)
    return keys_a <= keys_b or keys_b <= keys_a
