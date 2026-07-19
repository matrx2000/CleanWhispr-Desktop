"""Terminal detection: terminals need Ctrl+Shift+V, everything else Ctrl+V.

Exe and window-class lists ported from OpenWhispr's windows-fast-paste.c.
Pure logic (no OS calls) so it's testable everywhere.
"""

from __future__ import annotations

TERMINAL_EXES = {
    "windowsterminal.exe",
    "wt.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "openconsole.exe",
    "alacritty.exe",
    "wezterm-gui.exe",
    "hyper.exe",
    "tabby.exe",
    "termius.exe",
    "mintty.exe",
    "conemu64.exe",
    "conemu.exe",
    "putty.exe",
}

TERMINAL_WINDOW_CLASSES = {
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
    "ConsoleWindowClass",  # conhost
    "mintty",
    "VirtualConsoleClass",  # ConEmu
    "PuTTY",
}


def is_terminal(exe_name: str | None, window_class: str | None) -> bool:
    if exe_name and exe_name.lower() in TERMINAL_EXES:
        return True
    return bool(window_class) and window_class in TERMINAL_WINDOW_CLASSES
