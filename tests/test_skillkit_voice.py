"""Deterministic voice switch parsing + fuzzy name matching."""

import pytest

from skillkit import MemorySkillStore, SkillLibrary, default_skills, match_skill, voice


@pytest.fixture
def lib():
    library = SkillLibrary(MemorySkillStore(), seed=default_skills())
    library.set_enabled(True)
    return library


def test_switch_to_replaces(lib):
    v = voice.parse_switch("switch to poet", lib)
    assert v.outcome == voice.APPLIED
    assert v.action == voice.REPLACE
    assert v.skill_id == "poet"
    lib.apply_verdict(v)
    assert [s.id for s in lib.active_skills()] == ["poet"]


def test_use_with_role_noun_adds(lib):
    lib.set_active(["poet"])
    v = voice.parse_switch("use the concise skill", lib)
    assert v.outcome == voice.APPLIED
    assert v.action == voice.ADD
    assert v.skill_id == "concise"
    lib.apply_verdict(v)
    assert {s.id for s in lib.active_skills()} == {"poet", "concise"}


def test_activate_is_strong_no_noun_needed(lib):
    v = voice.parse_switch("activate friendly", lib)
    assert v.outcome == voice.APPLIED and v.action == voice.ADD and v.skill_id == "friendly"


def test_mishear_matched_via_trigger(lib):
    # "poyet" is a seeded trigger for the Poet skill
    v = voice.parse_switch("switch to poyet", lib)
    assert v.outcome == voice.APPLIED and v.skill_id == "poet"


def test_normal_instruction_passes_through(lib):
    v = voice.parse_switch("make the poet reference more formal", lib)
    assert v.outcome == voice.PASSTHROUGH


def test_noun_gated_verb_without_noun_is_not_a_command(lib):
    # "use bullet points" is an edit instruction, not "use <skill>"
    v = voice.parse_switch("use bullet points", lib)
    assert v.outcome == voice.PASSTHROUGH


def test_remove_the_last_sentence_is_not_a_command(lib):
    lib.set_active(["poet"])
    v = voice.parse_switch("remove the last sentence", lib)
    assert v.outcome == voice.PASSTHROUGH


def test_deactivate_removes_active_skill(lib):
    lib.set_active(["poet", "concise"])
    v = voice.parse_switch("deactivate poet", lib)
    assert v.outcome == voice.APPLIED and v.action == voice.REMOVE and v.skill_id == "poet"
    lib.apply_verdict(v)
    assert [s.id for s in lib.active_skills()] == ["concise"]


def test_plain_clears_all(lib):
    lib.set_active(["poet", "concise"])
    v = voice.parse_switch("plain", lib)
    assert v.outcome == voice.APPLIED and v.action == voice.CLEAR
    lib.apply_verdict(v)
    assert lib.active_skills() == []


def test_clear_when_nothing_active_passes_through(lib):
    v = voice.parse_switch("stop", lib)
    assert v.outcome == voice.PASSTHROUGH  # nothing to clear


def test_unknown_skill_is_rejected_not_edited(lib):
    v = voice.parse_switch("switch to accountant", lib)
    assert v.outcome == voice.REJECTED
    assert v.notice  # tells the user why


def test_long_utterance_never_a_command(lib):
    v = voice.parse_switch("switch to poet and then also do a lot of other things now", lib)
    assert v.outcome == voice.PASSTHROUGH


def test_match_skill_margin_blocks_ties():
    from skillkit.models import Skill

    a = Skill(id="report", name="Report")
    b = Skill(id="reporter", name="Reporter")
    skill, reason, _ = match_skill("report", [a, b], accept=0.86, margin=0.2)
    # near-tie between two similar names → refuse rather than guess wrong
    assert reason in {"ambiguous", "matched"}
    if reason == "matched":
        assert skill.id == "report"  # exact wins


def test_disabled_skill_not_matched(lib):
    lib.set_skill_enabled("poet", False)
    v = voice.parse_switch("switch to poet", lib)
    assert v.outcome == voice.REJECTED  # disabled skills are not candidates
