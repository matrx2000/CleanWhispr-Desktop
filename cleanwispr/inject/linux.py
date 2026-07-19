"""Linux text injection: clipboard tools + simulated paste keystroke.

Tool fallback chains ported from OpenWhispr's clipboard.js:
- X11/XWayland: xdotool (clipboard via xclip/xsel)
- Wayland: wl-copy/wl-paste for clipboard; wtype → ydotool → xdotool for keys
Pure command-builder helpers are separated from execution so they are
testable on any platform. Missing tools produce actionable install hints.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from cleanwispr.inject.base import InjectError, TextInjector

log = logging.getLogger(__name__)

_X11_TERMINAL_CLASSES = {
    "gnome-terminal-server", "gnome-terminal", "konsole", "xterm", "alacritty",
    "kitty", "terminator", "tilix", "xfce4-terminal", "urxvt", "st", "st-256color",
    "org.wezfurlong.wezterm", "foot", "termite", "lxterminal", "mate-terminal",
}

_INSTALL_HINT = (
    "Install a paste tool: X11 → 'sudo apt install xdotool xclip'; "
    "Wayland → 'sudo apt install wl-clipboard wtype'"
)


def detect_session() -> str:
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def is_terminal_class(window_class: str | None) -> bool:
    return bool(window_class) and window_class.lower() in _X11_TERMINAL_CLASSES


def clipboard_set_cmd(session: str, tools: set[str]) -> list[str] | None:
    if session == "wayland" and "wl-copy" in tools:
        return ["wl-copy"]
    if "xclip" in tools:
        return ["xclip", "-selection", "clipboard"]
    if "xsel" in tools:
        return ["xsel", "-b", "-i"]
    return None


def clipboard_get_cmd(session: str, tools: set[str]) -> list[str] | None:
    if session == "wayland" and "wl-paste" in tools:
        return ["wl-paste", "-n"]
    if "xclip" in tools:
        return ["xclip", "-selection", "clipboard", "-o"]
    if "xsel" in tools:
        return ["xsel", "-b", "-o"]
    return None


def key_cmds(session: str, tools: set[str], keys: str) -> list[list[str]]:
    """Candidate commands (tried in order) to send a chord like 'ctrl+v',
    'ctrl+shift+v', or 'ctrl+c'."""
    candidates: list[list[str]] = []
    parts = keys.split("+")
    letter = parts[-1]
    modifiers = parts[:-1]
    if session == "wayland":
        if "wtype" in tools:
            command = ["wtype"]
            for modifier in modifiers:
                command += ["-M", modifier]
            command.append(letter)
            candidates.append(command)
        if "ydotool" in tools:
            candidates.append(["ydotool", "key", keys])
    if "xdotool" in tools:  # X11 and XWayland apps
        candidates.append(["xdotool", "key", "--clearmodifiers", keys])
    return candidates


class LinuxInjector(TextInjector):
    def __init__(self) -> None:
        self._session = detect_session()
        self._tools = {
            name
            for name in ("wl-copy", "wl-paste", "xclip", "xsel", "xdotool", "wtype", "ydotool")
            if shutil.which(name)
        }
        log.info("linux injector: session=%s tools=%s", self._session, sorted(self._tools))

    # --- clipboard ---

    def _get_clipboard(self) -> str | None:
        command = clipboard_get_cmd(self._session, self._tools)
        if command is None:
            return None
        try:
            result = subprocess.run(command, capture_output=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.decode(errors="replace") if result.returncode == 0 else None

    def _set_clipboard(self, text: str) -> None:
        command = clipboard_set_cmd(self._session, self._tools)
        if command is None:
            raise InjectError(f"No clipboard tool found. {_INSTALL_HINT}")
        try:
            subprocess.run(command, input=text.encode(), timeout=3, check=True)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            raise InjectError(f"Clipboard write failed: {exc}") from exc

    # --- keystrokes ---

    def _send_keys(self, keys: str) -> None:
        candidates = key_cmds(self._session, self._tools, keys)
        if not candidates:
            raise InjectError(f"No keystroke tool found. {_INSTALL_HINT}")
        for command in candidates:
            try:
                result = subprocess.run(command, capture_output=True, timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                return
            log.info("keystroke tool failed (%s): %s", command[0], result.stderr[:120])
        raise InjectError(
            f"All keystroke tools failed for '{keys}'. Text is on the clipboard — "
            f"paste manually. {_INSTALL_HINT}"
        )

    def _active_window_class(self) -> str | None:
        if self._session != "x11" or "xdotool" not in self._tools:
            return None
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True, timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.decode().strip() if result.returncode == 0 else None

    # --- TextInjector ---

    def inject(self, text: str, *, restore_clipboard: bool = True) -> None:
        previous = self._get_clipboard() if restore_clipboard else None
        self._set_clipboard(text)
        time.sleep(0.05)
        shift = is_terminal_class(self._active_window_class())
        self._send_keys("ctrl+shift+v" if shift else "ctrl+v")
        if restore_clipboard and previous is not None:
            time.sleep(0.3)
            self._set_clipboard(previous)

    def capture_selection(self) -> str | None:
        previous = self._get_clipboard()
        self._set_clipboard("")  # sentinel
        self._send_keys("ctrl+c")
        selection = None
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            current = self._get_clipboard()
            if current:
                selection = current
                break
            time.sleep(0.05)
        if previous is not None:
            self._set_clipboard(previous)
        return selection or None
