"""Deterministic voice control for skills — no LLM round-trip.

Turns a (possibly mis-transcribed) spoken utterance into an intent to switch
skills on/off. It is intentionally conservative: an utterance is only treated
as a command when it is short AND matches a switch grammar, so ordinary
dictated instructions ("make the poet reference formal") pass straight through.

Grammar (after normalising):
    replace   ::= "switch to" <name>                 → only <name> is active
    add       ::= ("use"|"add"|"activate"|…) <name>  → <name> added to the set
    remove    ::= ("remove"|"disable"|"drop"|…) <name>→ <name> dropped
    clear     ::= "plain" | "stop" | "clear" | …      → all skills off (exact match)

Common-word verbs (use/add/set/…) only count when a role-noun is present
("use the poet ROLE"), so "use bullet points" stays a normal instruction.
Names are fuzzy-matched (stdlib difflib) against each skill's name + triggers,
with an accept floor and a runner-up margin so a weak or tied match never
switches silently. Seed a skill's `triggers` with known mishears for robustness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from skillkit.models import Skill

# actions the controller applies to the library
ADD = "add"
REPLACE = "replace"
REMOVE = "remove"
CLEAR = "clear"
NONE = "none"

# outcomes the controller branches on
APPLIED = "applied"  # a switch was recognised → apply it, do not run an edit
REJECTED = "rejected"  # looked like a switch but couldn't resolve → notice, no edit
PASSTHROUGH = "passthrough"  # not a command → run the edit normally

# verbs that always signal a switch attempt (reject, don't edit, on failure)
_STRONG = {
    "switch to": REPLACE,
    "switch role to": REPLACE,
    "switch skill to": REPLACE,
    "change to": REPLACE,
    "activate": ADD,
    "reactivate": ADD,
    "deactivate": REMOVE,
}
# verbs that only count as a switch when a role-noun is present in the utterance
_NOUN_GATED = {
    "use": ADD,
    "add": ADD,
    "enable": ADD,
    "load": ADD,
    "apply": ADD,
    "become": ADD,
    "set": ADD,
    "turn on": ADD,
    "remove": REMOVE,
    "disable": REMOVE,
    "drop": REMOVE,
    "turn off": REMOVE,
}
# exact full utterances that clear every active skill
_CLEAR_PHRASES = {
    "plain", "plain mode", "plain text", "plain english", "no style",
    "stop", "clear", "clear all", "clear role", "clear roles", "clear skill",
    "clear skills", "no role", "no roles", "no skill", "no skills", "none",
    "default", "normal", "reset", "reset skills", "off", "skills off",
}
_ROLE_NOUNS = {"role", "roles", "skill", "skills", "mode", "persona", "personas", "style", "voice"}
_ARTICLES = {"the", "a", "an", "my", "to", "into"}

# longest verb phrases first so "switch to" beats "switch", "turn off" beats "turn"
_VERBS: list[tuple[str, str, bool]] = sorted(
    [(v, a, True) for v, a in _STRONG.items()]
    + [(v, a, False) for v, a in _NOUN_GATED.items()],
    key=lambda x: len(x[0].split()),
    reverse=True,
)


@dataclass
class SwitchVerdict:
    outcome: str = PASSTHROUGH  # APPLIED | REJECTED | PASSTHROUGH
    action: str = NONE  # ADD | REPLACE | REMOVE | CLEAR | NONE
    skill_id: str | None = None
    skill_name: str | None = None
    notice: str = ""  # user-facing message (shown for APPLIED and REJECTED)


def normalize(text: str) -> str:
    """Lowercase, drop surrounding punctuation, collapse whitespace."""
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    score = SequenceMatcher(None, a, b).ratio()
    a_words, b_words = a.split(), b.split()
    if b in a_words or a in b_words:  # the name appears as a whole word
        score = max(score, 0.93)
    if a.startswith(b) or b.startswith(a):
        score = max(score, 0.9)
    return score


def match_skill(
    spoken: str,
    candidates: list[Skill],
    *,
    accept: float = 0.86,
    margin: float = 0.08,
) -> tuple[Skill | None, str, list[tuple[float, Skill]]]:
    """Best skill for a spoken name. Returns (skill|None, reason, scored).
    reason ∈ {matched, ambiguous, no-match, no-skills}."""
    spoken = normalize(spoken)
    if not candidates:
        return None, "no-skills", []
    if not spoken:
        return None, "no-match", []
    scored = sorted(
        (
            (max((_similarity(spoken, normalize(n)) for n in s.spoken_names()), default=0.0), s)
            for s in candidates
        ),
        key=lambda x: x[0],
        reverse=True,
    )
    top_score, top = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0.0
    if top_score >= accept and (top_score - runner) >= margin:
        return top, "matched", scored
    if top_score >= 0.6:
        return None, "ambiguous", scored
    return None, "no-match", scored


def _strip_affixes(tail: str) -> tuple[str, bool]:
    """Remove leading/trailing articles and role-nouns from the name tail.
    Returns (clean_tail, had_role_noun)."""
    words = tail.split()
    had_noun = any(w in _ROLE_NOUNS for w in words)
    while words and words[0] in _ARTICLES | _ROLE_NOUNS:
        words.pop(0)
    while words and words[-1] in _ARTICLES | _ROLE_NOUNS:
        words.pop()
    return " ".join(words), had_noun


def parse_switch(transcript: str, library) -> SwitchVerdict:
    """Interpret an utterance against a SkillLibrary. Duck-typed on `library`:
    needs `.config` (accept_threshold, margin, max_words), `.enabled_skills()`,
    and `.active_skills()`."""
    text = normalize(transcript)
    if not text:
        return SwitchVerdict()
    words = text.split()
    if len(words) > library.config.max_words:
        return SwitchVerdict()  # too long to be a command

    if text in _CLEAR_PHRASES:
        if not library.active_skills():
            return SwitchVerdict()  # nothing to clear → treat as a normal (odd) instruction
        return SwitchVerdict(outcome=APPLIED, action=CLEAR, notice="Skills off (plain)")

    for verb, action, strong in _VERBS:
        if text != verb and not text.startswith(verb + " "):
            continue
        raw_tail = text[len(verb):].strip()
        tail, had_noun = _strip_affixes(raw_tail)
        if not strong and not had_noun:
            return SwitchVerdict()  # e.g. "use bullet points" — not a switch
        if not tail:
            return SwitchVerdict(
                outcome=REJECTED,
                notice="Say the skill name after “" + verb + "”.",
            )
        candidates = library.active_skills() if action == REMOVE else library.enabled_skills()
        skill, reason, _ = match_skill(
            tail,
            candidates,
            accept=library.config.accept_threshold,
            margin=library.config.margin,
        )
        if reason == "matched" and skill is not None:
            return SwitchVerdict(
                outcome=APPLIED,
                action=action,
                skill_id=skill.id,
                skill_name=skill.name,
                notice=_applied_notice(action, skill.name),
            )
        if reason == "no-skills":
            return SwitchVerdict(
                outcome=REJECTED,
                notice=(
                    "No skills to remove." if action == REMOVE else "No skills defined yet."
                ),
            )
        if reason == "ambiguous":
            return SwitchVerdict(
                outcome=REJECTED, notice=f"Not sure which skill — say “{tail}” again."
            )
        return SwitchVerdict(outcome=REJECTED, notice=f"No skill matches “{tail}”.")

    return SwitchVerdict()


def _applied_notice(action: str, name: str) -> str:
    if action == REPLACE:
        return f"Skill: {name}"
    if action == ADD:
        return f"Added: {name}"
    if action == REMOVE:
        return f"Removed: {name}"
    return name
