"""Global hotkeys via a raw pynput Listener (Windows + Linux/X11).

Unlike pynput's GlobalHotKeys (press-only), a raw Listener reports key-down
AND key-up, which push-to-hold needs. Keys are canonicalized (ctrl_l → ctrl)
and fed to the pure HotkeyStateMachine.

Callbacks fire on the pynput listener thread; registrants must marshal to
Qt themselves (the app uses a signal bridge).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock

from cleanwispr.hotkeys.base import HotkeyBackend, HotkeyError
from cleanwispr.hotkeys.combos import ComboError, to_pynput
from cleanwispr.hotkeys.state import HotkeyStateMachine

log = logging.getLogger(__name__)


class PynputBackend(HotkeyBackend):
    supports_hold = True

    def __init__(self) -> None:
        self._combos: dict[str, str] = {}  # slot -> pynput combo string
        self._callbacks: dict[str, tuple[Callable, Callable | None]] = {}
        self._machine = HotkeyStateMachine()
        self._listener = None
        self._running = False
        self._lock = Lock()

    def register(
        self,
        slot: str,
        combo: str,
        *,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ) -> None:
        try:
            pynput_combo = to_pynput(combo)
        except ComboError as exc:
            raise HotkeyError(str(exc)) from exc
        with self._lock:
            self._combos[slot] = pynput_combo
            self._callbacks[slot] = (on_press, on_release)
            if self._running:
                self._rebuild_bindings_locked()
        log.info("hotkey registered: %s = %s", slot, combo)

    def unregister(self, slot: str) -> None:
        with self._lock:
            self._combos.pop(slot, None)
            self._callbacks.pop(slot, None)
            self._machine.remove_binding(slot)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._start_listener_locked()
            self._rebuild_bindings_locked()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            listener, self._listener = self._listener, None
            if listener is not None:
                listener.stop()
            self._machine.reset()

    # --- internals (call with lock held) ---

    def _rebuild_bindings_locked(self) -> None:
        from pynput import keyboard

        self._machine.reset()
        for slot in list(self._machine.slots()):
            self._machine.remove_binding(slot)
        for slot, combo in self._combos.items():
            try:
                keys = frozenset(keyboard.HotKey.parse(combo))
            except ValueError as exc:
                raise HotkeyError(f"Unsupported combo for {slot}: {exc}") from exc
            on_press, on_release = self._callbacks[slot]
            self._machine.set_binding(slot, keys, on_press, on_release)

    def _start_listener_locked(self) -> None:
        from pynput import keyboard

        machine = self._machine
        lock = self._lock
        listener: keyboard.Listener | None = None

        def on_press(key) -> None:
            with lock:
                if listener is not None:
                    machine.key_down(listener.canonical(key))

        def on_release(key) -> None:
            with lock:
                if listener is not None:
                    machine.key_up(listener.canonical(key))

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        self._listener = listener
