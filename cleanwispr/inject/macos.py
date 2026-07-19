"""macOS text injection (best effort, untested on real hardware).

Clipboard via pbcopy/pbpaste; keystrokes via osascript System Events —
which requires the Accessibility permission (System Settings → Privacy &
Security → Accessibility) for the Python/app binary.
"""

from __future__ import annotations

import logging
import subprocess
import time

from cleanwispr.inject.base import InjectError, TextInjector

log = logging.getLogger(__name__)

_PERMISSION_HINT = (
    "If nothing was pasted, grant Accessibility permission: System Settings → "
    "Privacy & Security → Accessibility."
)


def _keystroke_script(letter: str, *, shift: bool = False) -> str:
    using = '{command down, shift down}' if shift else '{command down}'
    return f'tell application "System Events" to keystroke "{letter}" using {using}'


class MacInjector(TextInjector):
    def _get_clipboard(self) -> str | None:
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.decode(errors="replace") if result.returncode == 0 else None

    def _set_clipboard(self, text: str) -> None:
        try:
            subprocess.run(["pbcopy"], input=text.encode(), timeout=3, check=True)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            raise InjectError(f"Clipboard write failed: {exc}") from exc

    def _send_keystroke(self, letter: str) -> None:
        script = _keystroke_script(letter)
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, timeout=5
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InjectError(f"Keystroke failed: {exc}. {_PERMISSION_HINT}") from exc
        if result.returncode != 0:
            raise InjectError(
                f"Keystroke failed: {result.stderr.decode()[:150]}. {_PERMISSION_HINT}"
            )

    def inject(self, text: str, *, restore_clipboard: bool = True) -> None:
        previous = self._get_clipboard() if restore_clipboard else None
        self._set_clipboard(text)
        time.sleep(0.05)
        self._send_keystroke("v")
        if restore_clipboard and previous is not None:
            time.sleep(0.3)
            self._set_clipboard(previous)

    def capture_selection(self) -> str | None:
        previous = self._get_clipboard()
        self._set_clipboard("")
        self._send_keystroke("c")
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
