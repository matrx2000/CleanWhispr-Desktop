"""SkillLibrary — the in-memory collection plus its persisted state.

Holds the skills, the master on/off flag, and the *stackable* active set
(an ordered list of ids — several skills can be active at once and are merged
into one prompt). Every mutation persists through the injected `SkillStore`
and notifies subscribers, so UI and the pipeline stay in sync from one source
of truth. Thread-safe: the pipeline may switch skills from a worker thread
while the UI reads on the main thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from threading import RLock

from skillkit.models import Skill, slugify
from skillkit.store import MemorySkillStore, SkillStore

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class LibraryConfig:
    """Library-wide state (everything that isn't an individual skill)."""

    enabled: bool = False  # master switch — feature is a pure no-op when False
    active_ids: list[str] = field(default_factory=list)  # stackable, ordered
    voice_switching: bool = True  # allow spoken "switch to <skill>"
    accept_threshold: float = 0.86  # fuzzy-match accept floor
    margin: float = 0.08  # winner must beat runner-up by this
    max_words: int = 6  # utterances longer than this are never a command

    @classmethod
    def from_dict(cls, data: dict | None) -> LibraryConfig:
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if not isinstance(kwargs.get("active_ids"), list):
            kwargs["active_ids"] = []
        else:
            kwargs["active_ids"] = [str(i) for i in kwargs["active_ids"]]
        return cls(**kwargs)


class SkillLibrary:
    def __init__(
        self,
        store: SkillStore | None = None,
        *,
        autosave: bool = True,
        seed: Iterable[Skill] | None = None,
    ) -> None:
        """`store` defaults to an in-memory store (nothing persisted). `seed` is
        used only when the store is empty — pass `skillkit.default_skills()` to
        ship starter skills, or None/[] for a blank library."""
        self._store: SkillStore = store or MemorySkillStore()
        self._autosave = autosave
        self._lock = RLock()
        self._subscribers: list[Callable[[], None]] = []
        self._items: list[Skill] = []
        self.config = LibraryConfig()

        raw = self._store.load()
        if raw is None:
            if seed is not None:
                self._items = [s.copy() for s in seed]
                self._save()
        else:
            self._load_from(raw)

    # --- (de)serialisation ---

    def _load_from(self, raw: dict) -> None:
        self.config = LibraryConfig.from_dict(raw.get("config"))
        items = []
        for record in raw.get("items", []) or []:
            skill = Skill.from_dict(record)
            if skill is not None:
                items.append(skill)
        self._items = items
        # drop any active id that no longer resolves
        self.config.active_ids = [i for i in self.config.active_ids if self._get(i)]

    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "config": {
                "enabled": self.config.enabled,
                "active_ids": list(self.config.active_ids),
                "voice_switching": self.config.voice_switching,
                "accept_threshold": self.config.accept_threshold,
                "margin": self.config.margin,
                "max_words": self.config.max_words,
            },
            "items": [s.to_dict() for s in self._items],
        }

    def _save(self) -> None:
        if self._autosave:
            self._store.save(self.to_dict())

    def save(self) -> None:
        """Force a persist (useful when autosave is off)."""
        with self._lock:
            self._store.save(self.to_dict())

    # --- change notification ---

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a zero-arg callback fired after any change. Returns an
        unsubscribe function. Callbacks may fire from a worker thread — marshal
        to your UI thread yourself (a Qt signal does this)."""
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback) if callback in self._subscribers else None

    def _changed(self) -> None:
        self._save()
        for callback in list(self._subscribers):
            try:
                callback()
            except Exception:  # a bad subscriber must not break a switch
                log.exception("skills subscriber raised")

    # --- master switch / voice config ---

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def set_enabled(self, value: bool) -> None:
        with self._lock:
            if self.config.enabled == value:
                return
            self.config.enabled = value
        self._changed()

    def set_voice_switching(self, value: bool) -> None:
        with self._lock:
            self.config.voice_switching = value
        self._changed()

    # --- reads ---

    def _get(self, skill_id: str) -> Skill | None:
        return next((s for s in self._items if s.id == skill_id), None)

    def get(self, skill_id: str) -> Skill | None:
        with self._lock:
            return self._get(skill_id)

    def all(self) -> list[Skill]:
        with self._lock:
            return list(self._items)

    def enabled_skills(self) -> list[Skill]:
        with self._lock:
            return [s for s in self._items if s.enabled]

    def active_skills(self, scope: str | None = None) -> list[Skill]:
        """Resolved active skills, in activation order — enabled, existing, and
        (when `scope` given) applicable to that leg. Dangling ids are skipped.
        Returns [] when the master switch is off, so the whole feature is a pure
        no-op with a single flag (the active set is remembered for when it's
        turned back on)."""
        with self._lock:
            if not self.config.enabled:
                return []
            out = []
            for skill_id in self.config.active_ids:
                skill = self._get(skill_id)
                if skill is None or not skill.enabled:
                    continue
                if scope is not None and not skill.applies_to(scope):
                    continue
                out.append(skill)
            return out

    def is_active(self, skill_id: str) -> bool:
        with self._lock:
            return skill_id in self.config.active_ids

    def resolved_temperature(self, scope: str | None = None) -> float | None:
        """Last active skill (in order) that pins a temperature wins."""
        value = None
        for skill in self.active_skills(scope):
            if skill.temperature is not None:
                value = skill.temperature
        return value

    def resolved_model(self, scope: str | None = None) -> str | None:
        value = None
        for skill in self.active_skills(scope):
            if skill.model:
                value = skill.model
        return value

    # --- activation (stackable) ---

    def activate(self, skill_id: str) -> None:
        with self._lock:
            if not self._get(skill_id) or skill_id in self.config.active_ids:
                return
            self.config.active_ids.append(skill_id)
        self._changed()

    def deactivate(self, skill_id: str) -> None:
        with self._lock:
            if skill_id not in self.config.active_ids:
                return
            self.config.active_ids.remove(skill_id)
        self._changed()

    def toggle(self, skill_id: str) -> None:
        if self.is_active(skill_id):
            self.deactivate(skill_id)
        else:
            self.activate(skill_id)

    def replace_active(self, skill_id: str) -> None:
        """Make `skill_id` the only active skill."""
        with self._lock:
            if not self._get(skill_id):
                return
            self.config.active_ids = [skill_id]
        self._changed()

    def set_active(self, ids: Iterable[str]) -> None:
        with self._lock:
            self.config.active_ids = [i for i in dict.fromkeys(ids) if self._get(i)]
        self._changed()

    def clear_active(self) -> None:
        with self._lock:
            if not self.config.active_ids:
                return
            self.config.active_ids = []
        self._changed()

    def apply_verdict(self, verdict) -> None:
        """Apply a skillkit.voice.SwitchVerdict (action + skill_id)."""
        from skillkit.voice import ADD, CLEAR, REMOVE, REPLACE

        if verdict.action == CLEAR:
            self.clear_active()
        elif verdict.action == REPLACE and verdict.skill_id:
            self.replace_active(verdict.skill_id)
        elif verdict.action == ADD and verdict.skill_id:
            self.activate(verdict.skill_id)
        elif verdict.action == REMOVE and verdict.skill_id:
            self.deactivate(verdict.skill_id)

    # --- CRUD ---

    def _unique_id(self, base: str, ignore: str | None = None) -> str:
        base = slugify(base)
        existing = {s.id for s in self._items if s.id != ignore}
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    def add(self, skill: Skill) -> Skill:
        """Add a skill, assigning a unique id derived from its name/id."""
        with self._lock:
            skill = skill.copy()
            skill.id = self._unique_id(skill.id or skill.name)
            self._items.append(skill)
        self._changed()
        return skill

    def create(self, name: str = "New skill", **kwargs) -> Skill:
        return self.add(Skill(id=slugify(name), name=name, **kwargs))

    def update(self, skill: Skill) -> None:
        """Replace an existing skill (matched by id) in place."""
        with self._lock:
            for i, existing in enumerate(self._items):
                if existing.id == skill.id:
                    self._items[i] = skill.copy()
                    break
            else:
                return
        self._changed()

    def duplicate(self, skill_id: str) -> Skill | None:
        with self._lock:
            src = self._get(skill_id)
            if src is None:
                return None
            clone = src.copy()
            clone.builtin = False
            clone.name = f"{src.name} copy"
            clone.id = self._unique_id(clone.name)
            self._items.append(clone)
        self._changed()
        return clone

    def remove(self, skill_id: str) -> None:
        with self._lock:
            skill = self._get(skill_id)
            if skill is None:
                return
            self._items = [s for s in self._items if s.id != skill_id]
            self.config.active_ids = [i for i in self.config.active_ids if i != skill_id]
        self._changed()

    def set_skill_enabled(self, skill_id: str, value: bool) -> None:
        with self._lock:
            skill = self._get(skill_id)
            if skill is None or skill.enabled == value:
                return
            skill.enabled = value
            if not value:
                self.config.active_ids = [i for i in self.config.active_ids if i != skill_id]
        self._changed()
