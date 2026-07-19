"""Windows text injection: clipboard write + simulated Ctrl(+Shift)+V.

ctypes only (no pywin32). Waits for the user's hotkey modifiers to be
physically released before sending the paste chord, otherwise the held
Ctrl/Win keys would corrupt the simulated keystroke.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from cleanwispr.inject.base import InjectError, TextInjector
from cleanwispr.inject.terminals import is_terminal

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, lwin, rwin

# Explicit signatures are mandatory: ctypes defaults every return type to a
# 32-bit int, which silently truncates 64-bit handles/pointers and crashes
# GlobalLock/memmove on 64-bit Windows.
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

# Registered formats that tell Windows Clipboard History / cloud sync to skip a
# write. Used for transient writes (paste + restore) so Win+V stays clean.
_FMT_EXCLUDE_MONITOR = user32.RegisterClipboardFormatW(
    "ExcludeClipboardContentFromMonitorProcessing"
)
_FMT_CAN_INCLUDE_HISTORY = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")


# --- clipboard (raw win32 — works from any thread, no Qt dependency) ---


def _open_clipboard(retries: int = 10) -> None:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.02)
    raise InjectError("Could not open the Windows clipboard")


def get_clipboard_text() -> str | None:
    _open_clipboard()
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _alloc_global(data: bytes) -> int:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise InjectError("Clipboard allocation failed")
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def set_clipboard_text(text: str, *, exclude_from_history: bool = False) -> None:
    """Write text to the clipboard. With exclude_from_history, the write carries
    the Windows formats that keep it out of Clipboard History (Win+V) and cloud
    sync — for transient writes the user never asked to keep."""
    text_handle = _alloc_global(bytes(ctypes.create_unicode_buffer(text)))
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, text_handle):
            kernel32.GlobalFree(text_handle)
            raise InjectError("Could not write to the clipboard")
        if exclude_from_history:
            zero = (0).to_bytes(4, "little")
            for fmt in (_FMT_EXCLUDE_MONITOR, _FMT_CAN_INCLUDE_HISTORY):
                if fmt:
                    user32.SetClipboardData(fmt, _alloc_global(zero))
    finally:
        user32.CloseClipboard()


# --- foreground window inspection ---


def _foreground_app() -> tuple[str | None, str | None]:
    """(exe_name, window_class) of the focused window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buffer, 256)

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe_name = None
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if process:
        try:
            path_buffer = ctypes.create_unicode_buffer(1024)
            length = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(
                process, 0, path_buffer, ctypes.byref(length)
            ):
                exe_name = path_buffer.value.rsplit("\\", 1)[-1]
        finally:
            kernel32.CloseHandle(process)
    return exe_name, class_buffer.value


def _wait_modifiers_released(timeout_s: float = 1.5) -> None:
    """Don't send the paste chord while the user still holds hotkey modifiers."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS):
            return
        time.sleep(0.02)
    log.warning("modifiers still held after %.1fs, pasting anyway", timeout_s)


def _send_paste(shift: bool) -> None:
    from pynput.keyboard import Controller, Key

    keyboard = Controller()
    with keyboard.pressed(Key.ctrl):
        if shift:
            with keyboard.pressed(Key.shift):
                keyboard.tap("v")
        else:
            keyboard.tap("v")


class WindowsInjector(TextInjector):
    def inject(self, text: str, *, restore_clipboard: bool = True) -> None:
        previous = get_clipboard_text() if restore_clipboard else None
        # transient write (will be restored) → keep it out of Win+V history;
        # a kept write (no restore) is a deliberate copy and stays visible
        set_clipboard_text(text, exclude_from_history=restore_clipboard)
        _wait_modifiers_released()

        exe_name, window_class = _foreground_app()
        shift = is_terminal(exe_name, window_class)
        log.info("pasting into %s (terminal=%s)", exe_name or "?", shift)
        _send_paste(shift)

        if restore_clipboard and previous is not None:
            # give the target app time to read the clipboard before restoring
            time.sleep(0.3)
            set_clipboard_text(previous, exclude_from_history=True)

    _COPY_WAIT_S = 1.2  # per attempt; modern apps publish the copy asynchronously

    def capture_selection(self) -> str | None:
        previous = get_clipboard_text()
        # sentinel: detect whether Ctrl+C produced anything
        set_clipboard_text("", exclude_from_history=True)
        _wait_modifiers_released()

        from pynput.keyboard import Controller, Key

        keyboard = Controller()
        selection = None
        for attempt in range(2):
            baseline = user32.GetClipboardSequenceNumber()
            with keyboard.pressed(Key.ctrl):
                keyboard.tap("c")
            # a fixed sleep is not enough (Win11 Notepad can take >0.5s):
            # poll the clipboard sequence number until the copy actually lands
            deadline = time.monotonic() + self._COPY_WAIT_S
            while time.monotonic() < deadline:
                if user32.GetClipboardSequenceNumber() != baseline:
                    time.sleep(0.05)  # let the writer finish publishing formats
                    selection = get_clipboard_text()
                    break
                time.sleep(0.03)
            if selection:
                break
            log.info("selection copy attempt %d produced nothing", attempt + 1)

        if previous is not None:
            set_clipboard_text(previous, exclude_from_history=True)
        return selection or None
