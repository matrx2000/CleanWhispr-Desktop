"""HotkeyBackend — the seam for global hotkey capture.

Slots are named ("dictation", "editor"); each binds one combo string in our
canonical format: lowercase, '+'-joined, e.g. "ctrl+super", "ctrl+alt+e", "f8".

Activation-mode semantics are the *controller's* job — the backend only
reports key events. For toggle mode the controller uses on_press only; for
hold mode it uses press/release pairs. Backends that cannot deliver release
events (Wayland D-Bus shortcuts) set supports_hold = False, and the UI
hides the hold option.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class HotkeyError(RuntimeError):
    """Registration failed (combo taken / unsupported). Message is user-presentable."""


class HotkeyBackend(ABC):
    supports_hold: bool = True

    @abstractmethod
    def register(
        self,
        slot: str,
        combo: str,
        *,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ) -> None:
        """Bind combo to slot, replacing any previous binding for that slot.
        Callbacks fire on a backend thread — marshal to Qt via signals."""

    @abstractmethod
    def unregister(self, slot: str) -> None: ...

    @abstractmethod
    def start(self) -> None:
        """Begin listening (install hooks)."""

    @abstractmethod
    def stop(self) -> None:
        """Remove hooks. Must leave no dangling OS state."""
