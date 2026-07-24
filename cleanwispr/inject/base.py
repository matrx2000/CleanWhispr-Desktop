"""TextInjector — the seam for getting text into (and out of) the active app.

Strategy everywhere: clipboard write + simulated paste keystroke, with
optional clipboard restore. Terminal windows get Ctrl+Shift+V.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class InjectError(RuntimeError):
    """Injection failed; the text is still on the clipboard for manual paste."""


class TextInjector(ABC):
    @abstractmethod
    def inject(self, text: str, *, restore_clipboard: bool = True) -> None:
        """Paste `text` into the currently focused application."""

    @abstractmethod
    def capture_selection(self) -> str | None:
        """Copy the current selection (simulated Ctrl+C) and return it, restoring
        the previous clipboard. Returns None if nothing was selected."""

    # --- live typing (optional capability, used by the dictation preview) ---
    # Backends that can simulate plain keystrokes set supports_live_typing and
    # implement the four primitives; the defaults keep every other backend a
    # pure paste injector with zero changes.

    supports_live_typing: bool = False

    def copy_text(self, text: str) -> None:
        """Put `text` on the clipboard WITHOUT pasting — the live-typing
        fallback when the target window disappeared mid-preview."""
        raise InjectError("Clipboard-only copy not supported on this backend")

    def focus_token(self) -> object | None:
        """Opaque identity of the focused window (None = can't tell). Live
        typing freezes when this changes so keystrokes never land in the
        wrong app."""
        return None

    def focus_is_terminal(self) -> bool:
        """Is the focused window a terminal? Live typing skips terminals — a
        half-typed word and its backspaces would go through the shell's line
        editor (history search, submitted commands) instead of plain text."""
        return False

    def modifiers_held(self) -> bool:
        """Is the user physically holding a modifier key right now? Typing
        while Ctrl/Alt/Win is down would trigger shortcuts instead of text."""
        return False

    def type_text(self, text: str) -> None:
        """Send `text` as plain keystrokes to the focused window."""
        raise InjectError("Live typing not supported on this backend")

    def delete_chars(self, count: int) -> None:
        """Send `count` backspaces to the focused window."""
        raise InjectError("Live typing not supported on this backend")
