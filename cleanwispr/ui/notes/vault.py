"""Note storage: multiple vaults, project folders, per-folder attachments.

A *vault* is a folder of notes. A vault may contain *project* subfolders that
group notes; notes living directly in the vault root are "global". Notes are
HTML files (portable, open in any browser, and — unlike Markdown — able to hold
custom text colours and styled tables); legacy `.md` notes are still read and
migrate to `.html` on first save.

Images pasted into a note are written to an ``attachments/`` folder that sits
*next to the note*: a project note uses ``<vault>/<project>/attachments``, a
global note uses ``<vault>/attachments``. The link stored in the note is always
the relative ``attachments/<file>``, so a note stays self-contained and moving
the whole vault never breaks it.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cleanwispr.storage import paths

log = logging.getLogger(__name__)

ATTACHMENTS_DIRNAME = "attachments"
NOTE_EXTS = (".html", ".md")
_SLUG_RE = re.compile(r"[^\w \-]", re.UNICODE)
_RESERVED_DIRS = {ATTACHMENTS_DIRNAME}


def _slugify(name: str, fallback: str = "Untitled") -> str:
    slug = _SLUG_RE.sub("", name).strip() or fallback
    return slug[:80]


@dataclass(slots=True)
class Note:
    """A note file inside a vault."""

    path: Path

    @property
    def title(self) -> str:
        return self.path.stem

    @property
    def is_markdown(self) -> bool:
        return self.path.suffix.lower() == ".md"

    @property
    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


class Vault:
    """CRUD over one folder of notes, organised into optional project folders."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    @property
    def name(self) -> str:
        return self._root.name or str(self._root)

    # --- projects ----------------------------------------------------------

    def projects(self) -> list[str]:
        return sorted(
            p.name
            for p in self.root.iterdir()
            if p.is_dir() and p.name not in _RESERVED_DIRS
        )

    def _project_dir(self, project: str | None) -> Path:
        if not project:
            return self.root
        path = self.root / project
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_project(self, name: str) -> str:
        base = _slugify(name, "Project")
        candidate = base
        counter = 2
        while (self.root / candidate).exists():
            candidate = f"{base} {counter}"
            counter += 1
        (self.root / candidate).mkdir(parents=True, exist_ok=True)
        return candidate

    def rename_project(self, old: str, new: str) -> str:
        target = _slugify(new, "Project")
        if target == old:
            return old
        counter = 2
        final = target
        while (self.root / final).exists():
            final = f"{target} {counter}"
            counter += 1
        (self.root / old).rename(self.root / final)
        return final

    def delete_project(self, name: str) -> None:
        import shutil

        shutil.rmtree(self.root / name, ignore_errors=True)

    # --- listing -----------------------------------------------------------

    def list_notes(self, project: str | None = None) -> list[Note]:
        folder = self._project_dir(project)
        notes: dict[str, Note] = {}
        for ext in NOTE_EXTS:
            for path in folder.glob(f"*{ext}"):
                if path.is_file():
                    # dedupe md/html twins by stem, preferring .html
                    existing = notes.get(path.stem)
                    if existing is None or path.suffix == ".html":
                        notes[path.stem] = Note(path)
        return sorted(notes.values(), key=lambda n: n.mtime, reverse=True)

    def project_of(self, note: Note) -> str | None:
        parent = note.path.parent
        return None if parent == self.root else parent.name

    def relpath(self, note: Note) -> str:
        return note.path.relative_to(self.root).as_posix()

    def find(self, relpath: str) -> Note | None:
        if not relpath:
            return None
        path = self.root / relpath
        return Note(path) if path.is_file() else None

    # --- note mutation -----------------------------------------------------

    def _unique_note_path(self, title: str, project: str | None, ext: str) -> Path:
        folder = self._project_dir(project)
        base = _slugify(title)
        candidate = folder / f"{base}{ext}"
        counter = 2
        while candidate.exists():
            candidate = folder / f"{base} {counter}{ext}"
            counter += 1
        return candidate

    def create(
        self, title: str = "Untitled", project: str | None = None, content: str = ""
    ) -> Note:
        path = self._unique_note_path(title, project, ".html")
        path.write_text(content, encoding="utf-8")
        return Note(path)

    def read(self, note: Note) -> str:
        try:
            return note.path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("could not read note %s: %s", note.path, exc)
            return ""

    def save(self, note: Note, html: str) -> Note:
        """Persist the note as HTML. A legacy `.md` note migrates to `.html`."""
        if note.is_markdown:
            target = note.path.with_suffix(".html")
            target.write_text(html, encoding="utf-8")
            if target != note.path:
                _silent_unlink(note.path)
            return Note(target)
        note.path.write_text(html, encoding="utf-8")
        return note

    def rename(self, note: Note, new_title: str) -> Note:
        target = self._unique_note_path(new_title, self.project_of(note), note.path.suffix)
        if target == note.path:
            return note
        note.path.rename(target)
        return Note(target)

    def move(self, note: Note, project: str | None) -> Note:
        target = self._unique_note_path(note.title, project, note.path.suffix)
        note.path.rename(target)
        return Note(target)

    def delete(self, note: Note) -> None:
        _silent_unlink(note.path)

    # --- attachments -------------------------------------------------------

    def note_dir(self, note: Note) -> Path:
        return note.path.parent


def attachments_dir(folder: str | Path) -> Path:
    """The ``attachments/`` folder beside a note (created on demand)."""
    path = Path(folder) / ATTACHMENTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(folder: str | Path, data: bytes, ext: str = "png") -> str:
    """Write image bytes into ``<folder>/attachments`` and return the relative
    link (``attachments/<name>.<ext>``) for embedding in the note."""
    ext = (ext or "png").lstrip(".").lower() or "png"
    name = f"{uuid.uuid4().hex[:12]}.{ext}"
    (attachments_dir(folder) / name).write_bytes(data)
    return f"{ATTACHMENTS_DIRNAME}/{name}"


def reveal_in_file_manager(path: str | Path) -> None:
    """Open the OS file manager at `path` (selecting the file when possible)."""
    path = Path(path)
    try:
        if sys.platform == "win32":
            if path.is_dir():
                os.startfile(path)
            else:
                subprocess.run(["explorer", f"/select,{path}"], check=False)
        elif sys.platform == "darwin":
            args = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
            subprocess.run(args, check=False)
        else:
            target = path if path.is_dir() else path.parent
            subprocess.run(["xdg-open", str(target)], check=False)
    except OSError as exc:
        log.warning("could not reveal %s: %s", path, exc)


def _silent_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        log.warning("could not delete %s: %s", path, exc)


class VaultManager:
    """The set of configured vaults and which one is active, backed by settings."""

    def __init__(self, settings, on_change: Callable[[], None]) -> None:
        self._settings = settings
        self._on_change = on_change
        self._migrate()

    def _notes(self):
        return self._settings.notes

    def _migrate(self) -> None:
        n = self._notes()
        vaults = [v for v in n.vaults if v]
        if not vaults:
            legacy = n.notes_dir or str(paths.default_notes_dir())
            vaults = [legacy]
            n.vaults = vaults
            self._on_change()
        if not n.active_vault or n.active_vault not in vaults:
            n.active_vault = vaults[0]
            self._on_change()

    def vaults(self) -> list[Path]:
        return [Path(v) for v in self._notes().vaults]

    def active_path(self) -> Path:
        return Path(self._notes().active_vault)

    def active(self) -> Vault:
        return Vault(self.active_path())

    def set_active(self, path: str | Path) -> None:
        self._notes().active_vault = str(path)
        self._notes().last_note = ""
        self._on_change()

    def add_vault(self, path: str | Path) -> Path:
        path = str(Path(path))
        if path not in self._notes().vaults:
            self._notes().vaults.append(path)
        self.set_active(path)
        return Path(path)

    def remove_vault(self, path: str | Path) -> None:
        path = str(Path(path))
        vaults = self._notes().vaults
        if path in vaults and len(vaults) > 1:
            vaults.remove(path)
            if self._notes().active_vault == path:
                self._notes().active_vault = vaults[0]
                self._notes().last_note = ""
            self._on_change()

    def display_names(self) -> list[tuple[str, str]]:
        """(path, label) for each vault — label disambiguates duplicate basenames."""
        vaults = self.vaults()
        names = [v.name for v in vaults]
        out: list[tuple[str, str]] = []
        for v in vaults:
            label = v.name if names.count(v.name) == 1 else f"{v.name}  ({v.parent})"
            out.append((str(v), label))
        return out
