"""Real win32 clipboard round-trip — guards the ctypes signatures against the
64-bit handle-truncation bug that crashed injection. Windows only."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")


def test_clipboard_roundtrip():
    from cleanwispr.inject.windows import get_clipboard_text, set_clipboard_text

    previous = get_clipboard_text()
    try:
        payloads = ["hello world", "čćžšđ — Croatian chars", "multi\nline\ntext", "x" * 100_000]
        for payload in payloads:
            set_clipboard_text(payload)
            assert get_clipboard_text() == payload
    finally:
        if previous is not None:
            set_clipboard_text(previous)


def test_excluded_write_is_still_readable():
    """History-excluded writes must not affect normal clipboard text retrieval."""
    from cleanwispr.inject.windows import get_clipboard_text, set_clipboard_text

    previous = get_clipboard_text()
    try:
        set_clipboard_text("transient payload", exclude_from_history=True)
        assert get_clipboard_text() == "transient payload"
    finally:
        if previous is not None:
            set_clipboard_text(previous)


def test_foreground_app_returns_without_crashing():
    from cleanwispr.inject.windows import _foreground_app

    exe_name, window_class = _foreground_app()
    # values depend on the desktop; the point is: no OSError from bad handles
    assert exe_name is None or isinstance(exe_name, str)
    assert window_class is None or isinstance(window_class, str)
