"""SkillLibrary: CRUD, stackable active set, override resolution, persistence."""

import json

from skillkit import JsonSkillStore, MemorySkillStore, Skill, SkillLibrary, default_skills


def test_seed_only_when_empty():
    store = MemorySkillStore()
    lib = SkillLibrary(store, seed=default_skills())
    assert {s.id for s in lib.all()} == {"formal", "concise", "friendly", "poet"}
    # a second library over the now-populated store does NOT re-seed
    lib2 = SkillLibrary(store, seed=default_skills())
    assert len(lib2.all()) == len(lib.all())


def test_add_assigns_unique_ids():
    lib = SkillLibrary(MemorySkillStore())
    a = lib.create(name="Poet")
    b = lib.create(name="Poet")
    assert a.id == "poet" and b.id == "poet-2"


def test_stackable_activation_order_preserved():
    lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
    lib.set_enabled(True)
    lib.activate("poet")
    lib.activate("concise")
    lib.activate("poet")  # already active → no dupe
    assert [s.id for s in lib.active_skills()] == ["poet", "concise"]
    lib.deactivate("poet")
    assert [s.id for s in lib.active_skills()] == ["concise"]
    lib.replace_active("formal")
    assert [s.id for s in lib.active_skills()] == ["formal"]
    lib.clear_active()
    assert lib.active_skills() == []


def test_scope_filters_active():
    lib = SkillLibrary(MemorySkillStore())
    lib.set_enabled(True)
    lib.add(Skill(id="notes-only", name="N", scope="notes"))
    lib.add(Skill(id="editor-only", name="E", scope="editor"))
    lib.add(Skill(id="both", name="B", scope="both"))
    lib.set_active(["notes-only", "editor-only", "both"])
    assert {s.id for s in lib.active_skills("editor")} == {"editor-only", "both"}
    assert {s.id for s in lib.active_skills("notes")} == {"notes-only", "both"}


def test_override_resolution_last_active_wins():
    lib = SkillLibrary(MemorySkillStore())
    lib.set_enabled(True)
    lib.add(Skill(id="a", name="A", temperature=0.1, model="model-a"))
    lib.add(Skill(id="b", name="B", temperature=0.9))  # no model override
    lib.set_active(["a", "b"])
    assert lib.resolved_temperature("editor") == 0.9  # b is later
    assert lib.resolved_model("editor") == "model-a"  # b doesn't set one → a wins


def test_disabling_skill_deactivates_it():
    lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
    lib.set_enabled(True)
    lib.set_active(["poet"])
    lib.set_skill_enabled("poet", False)
    assert lib.active_skills() == []
    assert "poet" not in [s.id for s in lib.enabled_skills()]


def test_remove_drops_from_active():
    lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
    lib.set_enabled(True)
    lib.set_active(["poet", "concise"])
    lib.remove("poet")
    assert [s.id for s in lib.active_skills()] == ["concise"]
    assert lib.get("poet") is None


def test_duplicate_is_editable_copy():
    lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
    clone = lib.duplicate("poet")
    assert clone is not None
    assert clone.builtin is False
    assert clone.id != "poet"


def test_json_store_roundtrip_and_dangling_active(tmp_path):
    path = tmp_path / "skills.json"
    lib = SkillLibrary(JsonSkillStore(path), seed=default_skills())
    lib.set_enabled(True)
    lib.set_active(["poet", "concise"])

    # a fresh library over the same file restores state
    lib2 = SkillLibrary(JsonSkillStore(path))
    assert lib2.enabled is True
    assert [s.id for s in lib2.active_skills()] == ["poet", "concise"]

    # hand-corrupt the active_ids to include a ghost → dropped on load, no crash
    data = json.loads(path.read_text(encoding="utf-8"))
    data["config"]["active_ids"].append("ghost")
    path.write_text(json.dumps(data), encoding="utf-8")
    lib3 = SkillLibrary(JsonSkillStore(path))
    assert [s.id for s in lib3.active_skills()] == ["poet", "concise"]


def test_corrupt_json_falls_back(tmp_path):
    path = tmp_path / "skills.json"
    path.write_text("{ not json", encoding="utf-8")
    lib = SkillLibrary(JsonSkillStore(path), seed=default_skills())
    # unreadable file quarantined, seed applied
    assert (tmp_path / "skills.json.bak").exists()
    assert len(lib.all()) == 4


def test_subscribe_fires_on_change():
    lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
    hits = []
    unsubscribe = lib.subscribe(lambda: hits.append(1))
    lib.activate("poet")
    assert hits == [1]
    unsubscribe()
    lib.activate("concise")
    assert hits == [1]  # no longer notified
