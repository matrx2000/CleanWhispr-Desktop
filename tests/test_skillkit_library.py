"""SkillLibrary: CRUD, stackable active set, override resolution, persistence."""

import json

from skillkit import JsonSkillStore, MemorySkillStore, Skill, SkillLibrary, default_skills


def test_seed_only_when_empty():
    store = MemorySkillStore()
    lib = SkillLibrary(store, seed=default_skills())
    assert {s.id for s in lib.all()} == {
        "markdown-tables", "formal", "concise", "friendly", "poet"
    }
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
    assert len(lib.all()) == len(default_skills())


def test_subscribe_fires_on_change():
    lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
    hits = []
    unsubscribe = lib.subscribe(lambda: hits.append(1))
    lib.activate("poet")
    assert hits == [1]
    unsubscribe()
    lib.activate("concise")
    assert hits == [1]  # no longer notified


def test_seed_active_and_enabled_first_run():
    lib = SkillLibrary(
        MemorySkillStore(),
        seed=default_skills(),
        seed_active=["markdown-tables"],
        seed_enabled=True,
    )
    assert lib.enabled is True
    assert [s.id for s in lib.active_skills()] == ["markdown-tables"]
    # Tables is Notes-scoped: active for the notes leg, not the editor
    assert [s.id for s in lib.active_skills("notes")] == ["markdown-tables"]
    assert lib.active_skills("editor") == []


def test_seed_params_only_apply_on_first_run():
    store = MemorySkillStore()
    SkillLibrary(store, seed=default_skills(), seed_active=["poet"], seed_enabled=True)
    lib = SkillLibrary(store)  # user turns it off + clears
    lib.set_enabled(False)
    lib.clear_active()
    # a later launch with the same seed args must NOT re-enable / re-activate
    lib2 = SkillLibrary(store, seed=default_skills(), seed_active=["poet"], seed_enabled=True)
    assert lib2.enabled is False
    assert lib2.active_skills() == []


def test_tables_skill_is_a_notes_scoped_builtin():
    tables = next(s for s in default_skills() if s.id == "markdown-tables")
    assert tables.builtin is True
    assert tables.scope == "notes"
    assert "pipe table" in tables.body


def test_export_import_roundtrip():
    src = SkillLibrary(MemorySkillStore(), seed=default_skills())
    bundle = src.export_dict(["poet"])
    assert bundle["skills"][0]["id"] == "poet"

    dst = SkillLibrary(MemorySkillStore())
    added = dst.import_skills(bundle)
    assert len(added) == 1 and added[0].name == "Poet"
    assert added[0].builtin is False  # imported skills are editable
    assert dst.get(added[0].id) is not None


def test_import_assigns_fresh_id_no_clobber():
    lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
    added = lib.import_skills({"id": "poet", "name": "Poet", "body": "x"})
    assert added[0].id != "poet"  # collision → fresh id
    assert lib.get("poet").name == "Poet"  # the original is untouched


def test_import_tolerates_list_single_and_junk():
    lib = SkillLibrary(MemorySkillStore())
    assert len(lib.import_skills([{"name": "A", "body": "a"}, {"name": "B"}])) == 2
    assert len(lib.import_skills({"name": "C", "body": "c"})) == 1
    assert lib.import_skills({"garbage": True}) == []  # nothing identifiable
