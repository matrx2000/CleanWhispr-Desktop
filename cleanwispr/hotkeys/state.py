"""Pure hotkey state machine: tracks pressed keys, fires press/release per combo.

Backend-agnostic (keys are any hashables) so the logic is fully testable.
A binding activates when its key set becomes a subset of the pressed keys,
and releases when any of its keys goes up — that's what push-to-hold needs.

Subset matching means overlapping combos (one contained in the other) would
both fire on the larger chord. Overlaps are therefore REJECTED at assignment
time (see combos.combos_overlap and the Hotkeys tab / app wiring) instead of
being disambiguated with delays here.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Binding:
    keys: frozenset
    on_press: Callable[[], None]
    on_release: Callable[[], None] | None
    active: bool = False


@dataclass(slots=True)
class HotkeyStateMachine:
    _bindings: dict[str, _Binding] = field(default_factory=dict)
    _pressed: set = field(default_factory=set)

    def set_binding(
        self,
        slot: str,
        keys: frozenset,
        on_press: Callable[[], None],
        on_release: Callable[[], None] | None = None,
    ) -> None:
        self._bindings[slot] = _Binding(keys, on_press, on_release)

    def remove_binding(self, slot: str) -> None:
        self._bindings.pop(slot, None)

    def slots(self) -> list[str]:
        return list(self._bindings)

    def reset(self) -> None:
        """Forget pressed keys (listener restart) without dropping bindings."""
        self._pressed.clear()
        for binding in self._bindings.values():
            binding.active = False

    def key_down(self, key: Hashable) -> None:
        if key in self._pressed:
            return  # OS auto-repeat
        self._pressed.add(key)
        for binding in self._bindings.values():
            if not binding.active and binding.keys <= self._pressed:
                binding.active = True
                binding.on_press()

    def key_up(self, key: Hashable) -> None:
        self._pressed.discard(key)
        for binding in self._bindings.values():
            if binding.active and key in binding.keys:
                binding.active = False
                if binding.on_release is not None:
                    binding.on_release()
