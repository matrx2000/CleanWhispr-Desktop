"""Start-on-login: Windows registry Run key (Linux XDG autostart lands in M5)."""

from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path

from cleanwispr import APP_NAME

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    """Command the OS should run at login."""
    if getattr(sys, "frozen", False):  # packaged exe
        return f'"{sys.executable}"'
    # dev install: pythonw avoids a console window
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    # prefer the root main.py: it works from any working directory even
    # without `pip install -e .` (script dir lands on the import path)
    root_main = Path(__file__).resolve().parent.parent / "main.py"
    if root_main.exists():
        return f'"{interpreter}" "{root_main}"'
    return f'"{interpreter}" -m cleanwispr'


def launch_argv() -> list[str]:
    """Same as launch_command, but as an argv list (for LaunchAgent plists)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    root_main = Path(__file__).resolve().parent.parent / "main.py"
    if root_main.exists():
        return [sys.executable, str(root_main)]
    return [sys.executable, "-m", "cleanwispr"]


def desktop_entry() -> str:
    """XDG autostart .desktop content (Linux)."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={launch_command()}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Comment=Local voice-to-text and voice editing\n"
    )


def launch_agent_plist() -> str:
    """macOS LaunchAgent plist content."""
    args = "\n".join(f"        <string>{a}</string>" for a in launch_argv())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        "    <key>Label</key>\n    <string>com.cleanwispr.app</string>\n"
        "    <key>ProgramArguments</key>\n    <array>\n"
        f"{args}\n"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n    <true/>\n"
        "</dict>\n</plist>\n"
    )


def _autostart_file() -> Path | None:
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / "autostart" / "cleanwispr.desktop"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.cleanwispr.app.plist"
    return None


def set_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        target = _autostart_file()
        if target is None:
            log.info("autostart not supported on this platform")
            return
        if enabled:
            target.parent.mkdir(parents=True, exist_ok=True)
            content = (
                desktop_entry() if sys.platform.startswith("linux") else launch_agent_plist()
            )
            target.write_text(content, encoding="utf-8")
            log.info("autostart enabled: %s", target)
        else:
            target.unlink(missing_ok=True)
            log.info("autostart disabled")
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
            log.info("autostart enabled: %s", launch_command())
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, APP_NAME)
            log.info("autostart disabled")


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        target = _autostart_file()
        return target is not None and target.exists()
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
