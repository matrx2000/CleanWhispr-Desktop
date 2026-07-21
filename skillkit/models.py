"""The Skill data model — a named, reusable instruction that flavours an LLM
call (tone, voice, persona) without owning the app's output contract.

Pure stdlib: dataclasses + a tolerant dict (de)serialiser that mirrors the
"unknown keys ignored, missing keys defaulted" behaviour a good config store
wants. No third-party dependency, so this module drops into any project.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields

# scope: which LLM legs a skill is allowed to flavour
SCOPE_EDITOR = "editor"
SCOPE_NOTES = "notes"
SCOPE_BOTH = "both"
_SCOPES = {SCOPE_EDITOR, SCOPE_NOTES, SCOPE_BOTH}


def slugify(name: str) -> str:
    """A stable, filename-safe id from a display name ('Formal Email' → 'formal-email')."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "skill"


@dataclass
class Skill:
    """One reusable persona/role.

    `body` is the instruction text the user writes ("You are a witty poet…").
    It is treated as UNTRUSTED, tone-only input by the composer — it can shape
    voice and vocabulary but cannot override the host's output rules.
    """

    id: str
    name: str
    description: str = ""
    body: str = ""
    enabled: bool = True
    builtin: bool = False  # shipped default; UI makes it read-only (edit = duplicate)
    scope: str = SCOPE_BOTH  # "editor" | "notes" | "both"
    # spoken aliases used for voice matching — seed with known mishears
    # (e.g. ["poyet", "pull it"] so "switch to poet" survives a bad transcript)
    triggers: list[str] = field(default_factory=list)
    temperature: float | None = None  # None → inherit the host's default
    model: str | None = None  # None → inherit the host's default

    def applies_to(self, scope: str) -> bool:
        return self.scope == SCOPE_BOTH or self.scope == scope

    def spoken_names(self) -> list[str]:
        """Everything a user might say to mean this skill: its name + triggers."""
        names = [self.name, *self.triggers]
        return [n for n in names if n and n.strip()]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Skill | None:
        """Build a Skill from stored JSON, tolerating unknown/missing keys.
        Returns None when the record is too broken to use (no id and no name)."""
        if not isinstance(data, dict):
            return None
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        name = str(kwargs.get("name") or "").strip()
        raw_id = str(kwargs.get("id") or "").strip()
        if not name and not raw_id:
            return None  # nothing identifiable → not a usable skill record
        kwargs["id"] = raw_id or slugify(name)
        kwargs["name"] = name or kwargs["id"]
        if kwargs.get("scope") not in _SCOPES:
            kwargs["scope"] = SCOPE_BOTH
        triggers = kwargs.get("triggers")
        if not isinstance(triggers, list):
            kwargs["triggers"] = []
        else:
            kwargs["triggers"] = [str(t) for t in triggers if str(t).strip()]
        return cls(**kwargs)

    def copy(self) -> Skill:
        return Skill.from_dict(self.to_dict())  # type: ignore[return-value]


def default_skills() -> list[Skill]:
    """A handful of read-only starter skills, seeded on first run when the
    library is empty. Deliberately generic and safe — pure tone/voice, plus one
    formatting helper (Tables) for Markdown notes."""
    return [
        Skill(
            id="markdown-tables",
            name="Tables",
            description="Write tables as clean, correctly-rendered Markdown.",
            body=(
                "Format any tabular data as a GitHub-flavoured Markdown pipe table so it "
                "renders correctly in the note:\n"
                "- Leave one blank line before and one blank line after the table.\n"
                "- Start and end every row with a pipe: | Item | Qty |\n"
                "- Put a delimiter row directly under the header, one cell per column, each "
                "with at least three hyphens: | --- | --- |. Add colons for alignment when "
                "useful (:--- left, :---: centre, ---: right).\n"
                "- Give every row — header, delimiter and each body row — exactly the same "
                "number of columns.\n"
                "- Keep each cell on a single line; write <br> for an in-cell line break, and "
                "escape any literal pipe character inside a cell as \\|.\n"
                "- Never wrap the table in code fences or backticks.\n"
                "When adding to or editing a table, reproduce the whole table with these "
                "rules applied so it re-renders cleanly."
            ),
            builtin=True,
            scope=SCOPE_NOTES,
            triggers=["table", "tables", "markdown table", "spreadsheet"],
        ),
        Skill(
            id="formal",
            name="Formal",
            description="Polished, professional register.",
            body=(
                "Write in a formal, professional register. Prefer complete sentences and "
                "precise vocabulary; avoid slang, contractions, and emoji. Keep it courteous "
                "and measured."
            ),
            builtin=True,
            triggers=["formal", "professional"],
        ),
        Skill(
            id="concise",
            name="Concise",
            description="Tighten the wording; cut filler.",
            body=(
                "Be concise. Remove filler, redundancy, and hedging. Prefer short, direct "
                "sentences and strong verbs while keeping the original meaning intact."
            ),
            builtin=True,
            triggers=["concise", "brief", "tighten"],
        ),
        Skill(
            id="friendly",
            name="Friendly",
            description="Warm, approachable, conversational.",
            body=(
                "Use a warm, friendly, approachable tone. Contractions are welcome. Sound like "
                "a helpful person talking to a colleague — relaxed but still clear."
            ),
            builtin=True,
            triggers=["friendly", "casual", "warm"],
        ),
        Skill(
            id="poet",
            name="Poet",
            description="Lyrical, vivid, image-rich voice.",
            body=(
                "Write with a poet's ear: vivid imagery, rhythm, and carefully chosen words. "
                "Favour metaphor and concrete sensory detail over plain phrasing."
            ),
            builtin=True,
            triggers=["poet", "poetic", "poyet"],
        ),
    ]
