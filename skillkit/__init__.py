"""skillkit — a portable "skills" layer for LLM apps.

A Skill is a named, reusable persona/role ("You are a witty poet…") that
flavours an LLM call's tone without touching the app's output contract. This
package is a self-contained module you can drop into any LLM app:

    core (stdlib only)   models · library · store · compose · voice
    ui   (PySide6 only)  skillkit.qt.SkillPalette · SkillsManager

Nothing in the core imports Qt or any host app. See README.md for a drop-in
integration guide and examples.

Quick start:

    from skillkit import SkillLibrary, JsonSkillStore, default_skills
    from skillkit.compose import PromptSpec, compose_messages
    from skillkit import voice

    lib = SkillLibrary(JsonSkillStore("skills.json"), seed=default_skills())
    lib.set_enabled(True)
    lib.activate("poet")

    spec = PromptSpec(role_framing="You rewrite text.", instruction="make it grand",
                      data="hello", data_noun="TEXT")
    messages = compose_messages(spec, lib.active_skills("editor"))
"""

from __future__ import annotations

from skillkit.compose import (
    DEFAULT_TRAILER,
    Message,
    PromptSpec,
    build_persona_block,
    compose_messages,
)
from skillkit.library import LibraryConfig, SkillLibrary, parse_import
from skillkit.models import (
    SCOPE_BOTH,
    SCOPE_EDITOR,
    SCOPE_NOTES,
    Skill,
    default_skills,
    slugify,
)
from skillkit.store import JsonSkillStore, MemorySkillStore, SkillStore
from skillkit.voice import SwitchVerdict, match_skill, parse_switch

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_TRAILER",
    "SCOPE_BOTH",
    "SCOPE_EDITOR",
    "SCOPE_NOTES",
    "JsonSkillStore",
    "LibraryConfig",
    "MemorySkillStore",
    "Message",
    "PromptSpec",
    "Skill",
    "SkillLibrary",
    "SkillStore",
    "SwitchVerdict",
    "__version__",
    "build_persona_block",
    "compose_messages",
    "default_skills",
    "match_skill",
    "parse_import",
    "parse_switch",
    "slugify",
]
