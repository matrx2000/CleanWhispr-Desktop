"""LiveTypingSink — puts committed preview words on screen and keeps them honest.

Sits between the LiveTranscriber (worker thread pushing committed words) and a
TextInjector's live-typing primitives. Owns the three safety rules the SOTA
survey of injection failure modes dictates:

- never type while the user physically holds a modifier key (text would turn
  into app shortcuts) — pushes buffer up and flush retries later;
- capture the focused window's identity on the first keystroke and FREEZE the
  moment it changes (keystrokes must never land in a different app);
- never emit a newline (Enter submits things in chats and terminals).

`finalize` turns the on-screen preview into the authoritative final transcript
with the minimal backspace-and-retype delta (`stt.live.reconcile`).
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum
from threading import Lock

from cleanwispr.inject.base import TextInjector
from cleanwispr.stt.live import reconcile

log = logging.getLogger(__name__)

_MODIFIER_WAIT_S = 1.5  # finalize: how long to wait for hotkey keys to lift


class LiveResult(StrEnum):
    DONE = "done"  # screen now shows exactly the final text
    UNTOUCHED = "untouched"  # nothing was ever typed — use the classic paste
    FROZEN = "frozen"  # focus changed mid-preview — screen left alone


class LiveTypingSink:
    def __init__(self, injector: TextInjector) -> None:
        self._injector = injector
        self._lock = Lock()
        self._typed = ""  # exactly what we put on screen
        self._pending = ""  # committed words not yet typed (modifiers were held)
        self._token: object | None = None  # focus identity at first keystroke
        self._frozen = False

    @property
    def typed(self) -> str:
        with self._lock:
            return self._typed

    # --- called from the LiveTranscriber thread ---

    def push(self, words: str) -> None:
        words = words.replace("\n", " ").strip()
        if not words:
            return
        with self._lock:
            joiner = " " if (self._typed or self._pending) else ""
            self._pending += joiner + words
            self._flush_locked()

    def _flush_locked(self) -> None:
        if self._frozen or not self._pending:
            return
        if self._injector.modifiers_held():
            return  # keep buffering; the next push or finalize retries
        token = self._injector.focus_token()
        if self._token is None:
            self._token = token
        elif token != self._token:
            self._frozen = True
            log.info("live typing frozen: focus moved to another window")
            return
        try:
            self._injector.type_text(self._pending)
        except Exception:
            log.exception("live typing failed; freezing preview")
            self._frozen = True
            return
        self._typed += self._pending
        self._pending = ""

    # --- called from the pipeline worker after the preview thread finished ---

    def finalize(self, final_text: str) -> LiveResult:
        """Correct the on-screen preview so it reads exactly `final_text`."""
        final_text = final_text.replace("\n", " ").strip()
        with self._lock:
            self._pending = ""  # untyped words are covered by the reconcile
            if not self._typed:
                return LiveResult.UNTOUCHED
            if self._frozen or not self._wait_modifiers_locked():
                return LiveResult.FROZEN
            if self._token is not None and self._injector.focus_token() != self._token:
                self._frozen = True
                return LiveResult.FROZEN
            backspaces, addition = reconcile(self._typed, final_text)
            try:
                if backspaces:
                    self._injector.delete_chars(backspaces)
                if addition:
                    self._injector.type_text(addition)
            except Exception:
                log.exception("live correction failed")
                return LiveResult.FROZEN
            self._typed = final_text
            return LiveResult.DONE

    def rollback(self) -> None:
        """Erase the preview (cancelled take / nothing transcribed)."""
        with self._lock:
            self._pending = ""
            if not self._typed or self._frozen:
                return
            if self._injector.focus_token() != self._token:
                return
            try:
                self._injector.delete_chars(len(self._typed))
                self._typed = ""
            except Exception:
                log.exception("live rollback failed")

    def _wait_modifiers_locked(self) -> bool:
        deadline = time.monotonic() + _MODIFIER_WAIT_S
        while self._injector.modifiers_held():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True
