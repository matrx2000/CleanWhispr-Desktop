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
