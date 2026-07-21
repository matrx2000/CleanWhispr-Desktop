"""A Qt signal fan-out for library changes.

A SkillLibrary can be mutated from any thread (e.g. a voice switch on a
pipeline worker). `SkillsBridge` subscribes to the library and re-emits every
change as a Qt signal, which Qt delivers on the receiving object's thread — so
UI widgets can connect to `changed` and refresh safely. Create one bridge and
share it across the palette, manager, tray, etc.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from skillkit.library import SkillLibrary


class SkillsBridge(QObject):
    changed = Signal()

    def __init__(self, library: SkillLibrary, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._library = library
        # Signal.emit is thread-safe; queued automatically for cross-thread receivers
        self._unsubscribe = library.subscribe(self.changed.emit)

    @property
    def library(self) -> SkillLibrary:
        return self._library

    def dispose(self) -> None:
        self._unsubscribe()
