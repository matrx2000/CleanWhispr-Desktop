from cleanwispr.hotkeys.state import HotkeyStateMachine


def make(slot="dictation", keys=("ctrl", "super")):
    sm = HotkeyStateMachine()
    events = []
    sm.set_binding(
        slot,
        frozenset(keys),
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
    )
    return sm, events


def test_press_fires_when_all_keys_down():
    sm, events = make()
    sm.key_down("ctrl")
    assert events == []
    sm.key_down("super")
    assert events == ["press"]


def test_release_fires_when_any_key_up():
    sm, events = make()
    sm.key_down("ctrl")
    sm.key_down("super")
    sm.key_up("ctrl")
    assert events == ["press", "release"]
    sm.key_up("super")
    assert events == ["press", "release"]  # no double release


def test_autorepeat_is_ignored():
    sm, events = make()
    sm.key_down("ctrl")
    sm.key_down("super")
    sm.key_down("super")  # OS auto-repeat
    sm.key_down("ctrl")
    assert events == ["press"]


def test_repress_after_release_fires_again():
    sm, events = make()
    for _ in range(2):
        sm.key_down("ctrl")
        sm.key_down("super")
        sm.key_up("super")
        sm.key_up("ctrl")
    assert events == ["press", "release", "press", "release"]


def test_two_disjoint_bindings_independent():
    sm = HotkeyStateMachine()
    hits = []
    sm.set_binding("a", frozenset(["f8"]), on_press=lambda: hits.append("a"))
    sm.set_binding("b", frozenset(["ctrl", "e"]), on_press=lambda: hits.append("b"))
    sm.key_down("f8")
    sm.key_down("ctrl")
    sm.key_down("e")
    assert hits == ["a", "b"]


def test_reset_clears_pressed_but_keeps_bindings():
    sm, events = make()
    sm.key_down("ctrl")
    sm.reset()
    sm.key_down("ctrl")
    sm.key_down("super")
    assert events == ["press"]
