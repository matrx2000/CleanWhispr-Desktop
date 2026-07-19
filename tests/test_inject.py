from cleanwispr.inject.terminals import is_terminal


def test_terminal_by_exe():
    assert is_terminal("WindowsTerminal.exe", None)
    assert is_terminal("cmd.exe", "SomeClass")
    assert is_terminal("pwsh.exe", None)


def test_terminal_by_window_class():
    assert is_terminal(None, "CASCADIA_HOSTING_WINDOW_CLASS")
    assert is_terminal("unknown.exe", "ConsoleWindowClass")


def test_regular_app_is_not_terminal():
    assert not is_terminal("notepad.exe", "Notepad")
    assert not is_terminal(None, None)
    assert not is_terminal("code.exe", "Chrome_WidgetWin_1")
