"""Cross-platform M5 units: Linux injector command builders, registry names,
autostart file contents."""

import pytest

from cleanwispr.inject.linux import (
    clipboard_get_cmd,
    clipboard_set_cmd,
    detect_session,
    is_terminal_class,
    key_cmds,
)


def test_detect_session(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert detect_session() == "unknown"
    monkeypatch.setenv("DISPLAY", ":0")
    assert detect_session() == "x11"
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert detect_session() == "wayland"


def test_clipboard_commands_wayland_prefers_wl_tools():
    tools = {"wl-copy", "wl-paste", "xclip"}
    assert clipboard_set_cmd("wayland", tools) == ["wl-copy"]
    assert clipboard_get_cmd("wayland", tools) == ["wl-paste", "-n"]
    # x11 session ignores wayland tools
    assert clipboard_set_cmd("x11", tools)[0] == "xclip"


def test_clipboard_commands_missing_tools():
    assert clipboard_set_cmd("x11", set()) is None
    assert clipboard_get_cmd("wayland", set()) is None


def test_key_cmds_order_and_syntax():
    tools = {"wtype", "ydotool", "xdotool"}
    wayland = key_cmds("wayland", tools, "ctrl+shift+v")
    assert wayland[0] == ["wtype", "-M", "ctrl", "-M", "shift", "v"]
    assert ["ydotool", "key", "ctrl+shift+v"] in wayland
    assert wayland[-1] == ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"]

    x11 = key_cmds("x11", {"xdotool"}, "ctrl+v")
    assert x11 == [["xdotool", "key", "--clearmodifiers", "ctrl+v"]]


def test_terminal_classes():
    assert is_terminal_class("konsole")
    assert is_terminal_class("Alacritty".lower())
    assert not is_terminal_class("firefox")
    assert not is_terminal_class(None)


@pytest.mark.parametrize(
    ("platform", "variant", "asset"),
    [
        ("win32", "cuda", "whisper-server-win32-x64-cuda.zip"),
        ("linux", "cpu", "whisper-server-linux-x64-cpu.zip"),
        ("linux", "vulkan", "whisper-server-linux-x64-vulkan.zip"),
    ],
)
def test_server_asset_names(monkeypatch, platform, variant, asset):
    import cleanwispr.stt.registry as registry

    monkeypatch.setattr(registry.sys, "platform", platform)
    assert registry.server_binary_asset_name(variant) == asset


def test_darwin_single_variant(monkeypatch):
    import cleanwispr.stt.registry as registry

    monkeypatch.setattr(registry.sys, "platform", "darwin")
    assert registry.server_variants() == ("cpu",)
    assert registry.server_binary_asset_name("cpu").startswith("whisper-server-darwin-")
    assert "-cpu" not in registry.server_binary_asset_name("cpu")


def test_desktop_entry_and_plist():
    from cleanwispr.autostart import desktop_entry, launch_agent_plist

    entry = desktop_entry()
    assert entry.startswith("[Desktop Entry]")
    assert "Exec=" in entry and "CleanWispr" in entry

    plist = launch_agent_plist()
    assert "com.cleanwispr.app" in plist
    assert "<key>RunAtLoad</key>" in plist
    assert "<string>" in plist  # program arguments present
