"""Hotkey settings: key-capture widget, activation mode per slot, conflict
validation, and live re-registration."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.hotkeys.combos import ComboError, combo_keys, combos_overlap, to_pynput
from cleanwispr.storage.settings import ActivationMode, Settings
from cleanwispr.ui.widgets import intro_label

_MODIFIER_KEYS = {
    Qt.Key.Key_Control: "ctrl",
    Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_Shift: "shift",
    Qt.Key.Key_Meta: "super",
}

_NAMED_QT_KEYS = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "pageup",
    Qt.Key.Key_PageDown: "pagedown",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Pause: "pause",
}

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "super")


def combo_from_parts(modifiers: set[str], key: str | None) -> str | None:
    """Canonical combo string from captured parts, or None if not a valid combo."""
    ordered = [m for m in _MODIFIER_ORDER if m in modifiers]
    parts = ordered + ([key] if key else [])
    if not parts:
        return None
    combo = "+".join(parts)
    try:
        to_pynput(combo)
    except ComboError:
        return None
    return combo


def qt_key_token(key: int) -> str | None:
    """Non-modifier Qt key → canonical token ("e", "f8", "space"...)."""
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return f"f{key - Qt.Key.Key_F1 + 1}"
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return chr(key).lower()
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(key)
    try:
        return _NAMED_QT_KEYS.get(Qt.Key(key))
    except ValueError:
        return None


class HotkeyCaptureButton(QPushButton):
    """Click, then press the desired keys. Modifier-only combos are captured on
    release; Esc cancels."""

    combo_captured = Signal(str)

    def __init__(self, combo: str) -> None:
        super().__init__(combo)
        self._combo = combo
        self._capturing = False
        self._mods: set[str] = set()
        self.clicked.connect(self._begin_capture)

    def set_combo(self, combo: str) -> None:
        self._combo = combo
        self.setText(combo)

    def _begin_capture(self) -> None:
        self._capturing = True
        self._mods = set()
        self.setText("Press keys… (Esc cancels)")
        self.grabKeyboard()

    def _end_capture(self, combo: str | None) -> None:
        self.releaseKeyboard()
        self._capturing = False
        if combo:
            self._combo = combo
            self.combo_captured.emit(combo)
        self.setText(self._combo)

    def keyPressEvent(self, event) -> None:  # Qt override
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._end_capture(None)
            return
        if key in _MODIFIER_KEYS:
            self._mods.add(_MODIFIER_KEYS[Qt.Key(key)])
            self.setText("+".join(m for m in _MODIFIER_ORDER if m in self._mods) + "+…")
            return
        token = qt_key_token(key)
        if token:
            self._end_capture(combo_from_parts(self._mods, token))

    def keyReleaseEvent(self, event) -> None:  # Qt override
        if not self._capturing:
            super().keyReleaseEvent(event)
            return
        # modifier-only combo: finalized when the first captured modifier is released
        if event.key() in _MODIFIER_KEYS and self._mods:
            self._end_capture(combo_from_parts(self._mods, None))


class HotkeysTab(QWidget):
    def __init__(
        self,
        settings: Settings,
        on_change: Callable[[], None],
        on_hotkeys_changed: Callable[[], None],
    ) -> None:
        super().__init__()
        self._settings = settings
        self._on_change = on_change
        self._on_hotkeys_changed = on_hotkeys_changed

        layout = QVBoxLayout(self)
        layout.addWidget(intro_label(
            "These shortcuts work globally, in any application. Dictation types what "
            "you say at the cursor; the voice editor rewrites your selected text from "
            "a spoken command; Notes opens the notetaking window. Click a button, then "
            "press your desired keys. Esc always cancels a recording in progress."
        ))
        layout.addWidget(self._slot_group("Dictation", "dictation"))
        layout.addWidget(self._slot_group("Voice editor", "editor"))
        layout.addWidget(self._slot_group("Notes window", "notes", with_activation=False))

        hint = QLabel(
            "Toggle: press once to start, again to stop. Push-to-hold: record while "
            "the keys are held (a quick tap latches recording on). Avoid combos "
            "Windows already uses (e.g. Win+Space switches keyboard layouts), and "
            "avoid Ctrl+Alt+letter — on many European layouts that is AltGr and "
            "types a character into the app you're editing."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)
        layout.addStretch()

    # every configurable hotkey slot, in priority order (matches app.py)
    _SLOTS = ("dictation", "editor", "notes")

    def _slot_group(self, title: str, slot: str, with_activation: bool = True) -> QGroupBox:
        group = QGroupBox(title)
        grid = QGridLayout(group)
        slot_settings = getattr(self._settings.hotkeys, slot)

        grid.addWidget(QLabel("Hotkey:"), 0, 0)
        capture = HotkeyCaptureButton(slot_settings.combo)
        capture.combo_captured.connect(lambda combo: self._combo_changed(slot, capture, combo))
        grid.addWidget(capture, 0, 1)

        if with_activation:
            grid.addWidget(QLabel("Activation:"), 1, 0)
            mode_combo = QComboBox()
            mode_combo.addItem("Toggle (tap to start/stop)", ActivationMode.TOGGLE.value)
            mode_combo.addItem("Push-to-hold", ActivationMode.HOLD.value)
            mode_combo.setCurrentIndex(0 if slot_settings.mode is ActivationMode.TOGGLE else 1)
            mode_combo.currentIndexChanged.connect(
                lambda _index: self._mode_changed(slot, mode_combo)
            )
            grid.addWidget(mode_combo, 1, 1)
        grid.setColumnStretch(1, 1)
        return group

    def _combo_changed(self, slot: str, button: HotkeyCaptureButton, combo: str) -> None:
        for other_slot in self._SLOTS:
            if other_slot == slot:
                continue
            other_combo = getattr(self._settings.hotkeys, other_slot).combo
            if not combos_overlap(combo, other_combo):
                continue
            if combo == other_combo:
                reason = f"'{combo}' is already used by the {other_slot} hotkey."
            else:
                smaller, larger = sorted([combo, other_combo], key=lambda c: len(combo_keys(c)))
                reason = (
                    f"'{smaller}' is contained in '{larger}'. Pressing the larger "
                    f"combination would also press every key of the smaller one, so "
                    f"both hotkeys would trigger at once. Choose combinations that "
                    f"don't contain each other."
                )
            QMessageBox.warning(self, "Hotkey conflict", reason)
            button.set_combo(getattr(self._settings.hotkeys, slot).combo)
            return
        getattr(self._settings.hotkeys, slot).combo = combo
        self._on_change()
        self._on_hotkeys_changed()

    def _mode_changed(self, slot: str, mode_combo: QComboBox) -> None:
        getattr(self._settings.hotkeys, slot).mode = ActivationMode(mode_combo.currentData())
        self._on_change()
        self._on_hotkeys_changed()
