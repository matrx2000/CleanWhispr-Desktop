"""Optional Qt (PySide6) UI for skillkit.

Import this only if you want the ready-made widgets — the core package has no
Qt dependency. Everything here talks to a `SkillLibrary`; wire one `SkillsBridge`
to fan library changes out to every widget on the Qt thread.
"""

from __future__ import annotations

from skillkit.qt.bridge import SkillsBridge
from skillkit.qt.manager import SkillsManager
from skillkit.qt.palette import SkillPalette

__all__ = ["SkillPalette", "SkillsBridge", "SkillsManager"]
