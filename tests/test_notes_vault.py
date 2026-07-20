"""Vault + VaultManager: notes, projects, attachments, multiple vaults."""


from cleanwispr.storage.settings import Settings
from cleanwispr.ui.notes import vault as vault_mod
from cleanwispr.ui.notes.vault import ATTACHMENTS_DIRNAME, Vault, VaultManager


def test_create_saves_html_and_reads_back(tmp_path):
    vault = Vault(tmp_path)
    note = vault.create("My Note")
    assert note.path.suffix == ".html"
    saved = vault.save(note, "<p>hello</p>")
    assert vault.read(saved) == "<p>hello</p>"


def test_titles_slugged_and_unique(tmp_path):
    vault = Vault(tmp_path)
    a = vault.create("Weird/Name:*?")
    assert "/" not in a.path.name and ":" not in a.path.name
    b = vault.create("Untitled")
    c = vault.create("Untitled")
    assert b.path != c.path


def test_projects_group_notes(tmp_path):
    vault = Vault(tmp_path)
    proj = vault.create_project("Work")
    assert proj in vault.projects()
    root_note = vault.create("Global")
    work_note = vault.create("Task", project=proj)

    assert vault.project_of(root_note) is None
    assert vault.project_of(work_note) == "Work"
    assert vault.relpath(work_note) == "Work/Task.html"
    assert [n.title for n in vault.list_notes("Work")] == ["Task"]
    assert [n.title for n in vault.list_notes(None)] == ["Global"]


def test_rename_move_delete(tmp_path):
    vault = Vault(tmp_path)
    proj = vault.create_project("Ideas")
    note = vault.create("Draft")
    renamed = vault.rename(note, "Final")
    assert renamed.title == "Final"
    assert not note.path.exists()

    moved = vault.move(renamed, proj)
    assert vault.project_of(moved) == "Ideas"
    assert not renamed.path.exists()

    vault.delete(moved)
    assert vault.list_notes("Ideas") == []


def test_legacy_md_migrates_to_html_on_save(tmp_path):
    vault = Vault(tmp_path)
    md = tmp_path / "Old.md"
    md.write_text("# Old", encoding="utf-8")
    note = vault.find("Old.md")
    assert note is not None and note.is_markdown
    saved = vault.save(note, "<h1>Old</h1>")
    assert saved.path.suffix == ".html"
    assert not md.exists()  # the .md twin is removed


def test_html_preferred_over_md_twin(tmp_path):
    vault = Vault(tmp_path)
    (tmp_path / "Dup.md").write_text("md", encoding="utf-8")
    (tmp_path / "Dup.html").write_text("html", encoding="utf-8")
    notes = [n for n in vault.list_notes(None) if n.title == "Dup"]
    assert len(notes) == 1
    assert notes[0].path.suffix == ".html"


def test_attachments_are_folder_local(tmp_path):
    root_note_dir = tmp_path
    project_dir = tmp_path / "Proj"
    project_dir.mkdir()

    rel_root = vault_mod.save_image(root_note_dir, b"png", "png")
    rel_proj = vault_mod.save_image(project_dir, b"png", "png")

    assert rel_root.startswith(f"{ATTACHMENTS_DIRNAME}/")
    assert (tmp_path / rel_root).exists()  # global note → vault-root attachments
    assert (project_dir / rel_proj).exists()  # project note → project attachments


def test_vault_manager_migrates_and_switches(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cleanwispr.ui.notes.vault.paths.default_notes_dir", lambda: tmp_path / "default"
    )
    settings = Settings()
    changes = []
    mgr = VaultManager(settings, lambda: changes.append(1))

    # migration seeded one default vault and made it active
    assert len(mgr.vaults()) == 1
    assert mgr.active_path() == tmp_path / "default"

    second = tmp_path / "second"
    mgr.add_vault(second)
    assert mgr.active_path() == second
    assert len(mgr.vaults()) == 2

    mgr.remove_vault(second)
    assert mgr.active_path() == tmp_path / "default"
    assert len(mgr.vaults()) == 1
    # a lone vault cannot be removed
    mgr.remove_vault(tmp_path / "default")
    assert len(mgr.vaults()) == 1


def test_vault_manager_uses_legacy_notes_dir(tmp_path):
    settings = Settings()
    settings.notes.notes_dir = str(tmp_path / "legacy")
    mgr = VaultManager(settings, lambda: None)
    assert mgr.active_path() == tmp_path / "legacy"
