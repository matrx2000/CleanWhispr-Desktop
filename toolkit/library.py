"""ToolLibrary — the on-disk collection of tools plus its persisted state.

Layout: every tool is a folder `<root>/<tool-id>/` holding `tool.json` and its
Python entry file; the library state (master switches, per-tool enable flags)
lives in one JSON file next to the root. Import/export moves tools around as
zip files of that folder, so tools can be shared exactly like skills.

Safety model:
- a tool runs only when the master switch, its own enable flag, and (for
  network tools) the allow_network switch all say yes;
- tools created by the LLM (via the built-in create-tool) land DISABLED —
  model-authored code never executes until the user reviews and enables it;
- zip import refuses entries that would escape the target folder (zip-slip).
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass, fields
from pathlib import Path
from threading import RLock

from toolkit.models import MANIFEST_NAME, ToolSpec, slugify
from toolkit.runner import ToolArgsError, run_packaged, validate_args

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_MAX_ZIP_MEMBERS = 200
_MAX_ZIP_BYTES = 5 * 1024 * 1024  # a tool is code, not data — 5 MB is generous


class ToolError(RuntimeError):
    """Library-level failure (bad zip, unknown tool); message is user-presentable."""


@dataclass
class ToolkitConfig:
    """Library-wide switches (everything that isn't an individual tool)."""

    enabled: bool = True  # master switch for the whole feature
    confirm_all: bool = False  # ask before EVERY call (per-tool confirm still applies)
    allow_network: bool = False  # web/network tools stay dead until opted in
    max_rounds: int = 5  # tool-call rounds per chat before forcing an answer

    @classmethod
    def from_dict(cls, data: object) -> ToolkitConfig:
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class ToolLibrary:
    def __init__(self, root: Path, state_path: Path | None = None) -> None:
        self.root = Path(root)
        self._state_path = state_path or self.root.parent / "tools.json"
        self._lock = RLock()
        self._specs: list[ToolSpec] = []
        self._enabled_overlay: dict[str, bool] = {}
        self.config = ToolkitConfig()
        self._natives = {"create_tool": self._native_create_tool}
        self._load_state()
        self.refresh()

    # --- persistence ---

    def _load_state(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self.config = ToolkitConfig.from_dict(raw.get("config"))
        tools = raw.get("tools")
        if isinstance(tools, dict):
            self._enabled_overlay = {
                str(tool_id): bool((entry or {}).get("enabled", True))
                for tool_id, entry in tools.items()
                if isinstance(entry, dict)
            }

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": SCHEMA_VERSION,
                "config": {
                    "enabled": self.config.enabled,
                    "confirm_all": self.config.confirm_all,
                    "allow_network": self.config.allow_network,
                    "max_rounds": self.config.max_rounds,
                },
                "tools": {
                    tool_id: {"enabled": value}
                    for tool_id, value in self._enabled_overlay.items()
                },
            }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._state_path)

    # --- scanning ---

    def refresh(self) -> None:
        """Re-read every tool folder; the enabled overlay survives rescans."""
        specs: list[ToolSpec] = []
        if self.root.is_dir():
            for folder in sorted(self.root.iterdir()):
                manifest = folder / MANIFEST_NAME
                if not folder.is_dir() or not manifest.is_file():
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    log.warning("skipping tool %s: unreadable manifest (%s)", folder.name, exc)
                    continue
                spec = ToolSpec.from_manifest(data, path=folder)
                if spec is None:
                    log.warning("skipping tool %s: manifest has no id/name", folder.name)
                    continue
                spec.id = folder.name  # the folder is the identity; manifest follows
                specs.append(spec)
        with self._lock:
            for spec in specs:
                if spec.id in self._enabled_overlay:
                    spec.enabled = self._enabled_overlay[spec.id]
            self._specs = specs

    def all(self) -> list[ToolSpec]:
        with self._lock:
            return list(self._specs)

    def get(self, tool_id: str) -> ToolSpec | None:
        with self._lock:
            return next((s for s in self._specs if s.id == tool_id), None)

    def resolve_call(self, wire_name: str) -> ToolSpec | None:
        """Find the armed tool a model-emitted function name refers to."""
        for spec in self.armed_specs():
            if spec.wire_name == wire_name or spec.id == wire_name:
                return spec
        return None

    def armed_specs(self) -> list[ToolSpec]:
        """Tools the model may actually call right now: feature on, tool on,
        and network tools only when web access is opted in."""
        with self._lock:
            if not self.config.enabled:
                return []
            return [
                s
                for s in self._specs
                if s.enabled and (not s.network or self.config.allow_network)
            ]

    # --- switches ---

    def set_enabled(self, tool_id: str, value: bool) -> None:
        with self._lock:
            spec = next((s for s in self._specs if s.id == tool_id), None)
            if spec is not None:
                spec.enabled = value
            self._enabled_overlay[tool_id] = value
        self.save()

    def set_config(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
        self.save()

    # --- execution (used by the LLM tool loop) ---

    def run(self, spec: ToolSpec, args: object) -> str:
        """Validate arguments and execute; always returns a model-readable string."""
        try:
            clean = validate_args(spec.parameters or {}, args)
        except ToolArgsError as exc:
            return f"Invalid arguments for {spec.wire_name}: {exc}"
        if spec.native:
            handler = self._natives.get(spec.native)
            if handler is None:
                return f"Tool '{spec.id}' needs native handler '{spec.native}' (not available)"
            try:
                return handler(clean)
            except Exception as exc:
                log.exception("native tool %s failed", spec.id)
                return f"Tool '{spec.id}' failed: {exc}"
        return run_packaged(spec, clean)

    # --- import / export (tool exchange as zip files) ---

    def export_zip(self, tool_id: str, dest: Path) -> Path:
        spec = self.get(tool_id)
        if spec is None or spec.path is None:
            raise ToolError(f"Tool '{tool_id}' not found")
        dest = Path(dest)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(spec.path.rglob("*")):
                if file.is_file() and "__pycache__" not in file.parts:
                    zf.write(file, f"{spec.id}/{file.relative_to(spec.path)}")
        return dest

    def import_zip(self, zip_path: Path) -> ToolSpec:
        """Install a tool from a zip (either the tool folder zipped, or its
        files at the archive root). The imported tool starts DISABLED — the
        user reviews and enables it in settings."""
        try:
            with zipfile.ZipFile(zip_path) as zf:
                members = zf.infolist()
                if len(members) > _MAX_ZIP_MEMBERS:
                    raise ToolError("Zip has too many files to be a tool")
                if sum(m.file_size for m in members) > _MAX_ZIP_BYTES:
                    raise ToolError("Zip is too large to be a tool")
                manifest_member = self._find_manifest(members)
                if manifest_member is None:
                    raise ToolError(f"No {MANIFEST_NAME} found in the zip")
                prefix = manifest_member.filename[: -len(MANIFEST_NAME)]
                data = json.loads(zf.read(manifest_member).decode("utf-8"))
                spec = ToolSpec.from_manifest(data)
                if spec is None:
                    raise ToolError(f"{MANIFEST_NAME} is missing a tool name")
                target = self._unique_dir(spec.id)
                extracted = False
                for member in members:
                    if member.is_dir() or not member.filename.startswith(prefix):
                        continue
                    relative = member.filename[len(prefix):]
                    if not relative or "__pycache__" in relative:
                        continue
                    out = (target / relative).resolve()
                    # zip-slip guard: every entry must stay inside the target
                    if target.resolve() != out and target.resolve() not in out.parents:
                        raise ToolError(f"Zip entry escapes the tool folder: {member.filename}")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(zf.read(member))
                    extracted = True
                if not extracted:
                    raise ToolError("Zip contained no tool files")
        except zipfile.BadZipFile as exc:
            raise ToolError(f"Not a valid zip file: {exc}") from exc
        except ToolError:
            with contextlib.suppress(OSError, UnboundLocalError):
                shutil.rmtree(target)
            raise
        with self._lock:
            self._enabled_overlay[target.name] = False  # review before first run
        self.save()
        self.refresh()
        imported = self.get(target.name)
        if imported is None:  # pragma: no cover — refresh just wrote it
            raise ToolError("Imported tool did not load")
        return imported

    @staticmethod
    def _find_manifest(members: list[zipfile.ZipInfo]) -> zipfile.ZipInfo | None:
        """Shallowest tool.json wins: root first, then inside one folder."""
        best = None
        for member in members:
            name = member.filename.replace("\\", "/")
            if not name.endswith(MANIFEST_NAME):
                continue
            depth = name.count("/")
            if depth <= 1 and (best is None or depth < best.filename.count("/")):
                best = member
        return best

    def remove(self, tool_id: str) -> None:
        spec = self.get(tool_id)
        if spec is None:
            return
        if spec.builtin:
            raise ToolError("Built-in tools can be disabled but not deleted")
        if spec.path is not None:
            shutil.rmtree(spec.path, ignore_errors=True)
        with self._lock:
            self._enabled_overlay.pop(tool_id, None)
        self.save()
        self.refresh()

    # --- creation (used by the built-in create-tool and the settings UI) ---

    def create_tool(
        self,
        name: str,
        description: str,
        parameters: dict | None,
        code: str,
        *,
        network: bool = False,
        timeout_s: float | None = None,
    ) -> ToolSpec:
        """Write a new tool package. It lands DISABLED so model-authored code
        never runs before the user reviewed it in Settings → Tools."""
        try:
            compile(code, "tool.py", "exec")
        except SyntaxError as exc:
            raise ToolError(f"tool.py has a syntax error: {exc}") from exc
        if "def run(" not in code:
            raise ToolError("tool.py must define the entry function: def run(...)")
        target = self._unique_dir(slugify(name))
        target.mkdir(parents=True, exist_ok=True)
        spec = ToolSpec(
            id=target.name,
            name=name.strip() or target.name,
            description=description.strip(),
            parameters=parameters if isinstance(parameters, dict) else None,  # type: ignore[arg-type]
            network=network,
            enabled=False,
            timeout_s=timeout_s or 20.0,
            path=target,
        )
        if spec.parameters is None:
            spec.parameters = {"type": "object", "properties": {}, "required": []}
        (target / MANIFEST_NAME).write_text(
            json.dumps(spec.to_manifest(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (target / "tool.py").write_text(code, encoding="utf-8")
        with self._lock:
            self._enabled_overlay[spec.id] = False
        self.save()
        self.refresh()
        return spec

    def _native_create_tool(self, args: dict) -> str:
        parameters = args.get("parameters")
        if isinstance(parameters, str):  # models sometimes double-encode the schema
            with contextlib.suppress(json.JSONDecodeError):
                parameters = json.loads(parameters)
        try:
            spec = self.create_tool(
                name=str(args.get("name") or ""),
                description=str(args.get("description") or ""),
                parameters=parameters if isinstance(parameters, dict) else None,
                code=str(args.get("code") or ""),
                network=bool(args.get("network", False)),
            )
        except ToolError as exc:
            return f"create_tool failed: {exc}"
        return (
            f"Tool '{spec.name}' created as '{spec.id}'. It is DISABLED for safety — "
            "tell the user to review and enable it in Settings → Tools before it can run."
        )

    def _unique_dir(self, base_id: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        candidate = self.root / (base_id or "tool")
        counter = 2
        while candidate.exists():
            candidate = self.root / f"{base_id}-{counter}"
            counter += 1
        return candidate

    # --- built-in seeding ---

    def seed_builtins(self) -> None:
        """Copy the packaged starter tools into the library on first run (or
        after the user deleted the folder). Existing folders are never touched,
        so user edits survive upgrades."""
        source_root = Path(__file__).resolve().parent / "builtin"
        if not source_root.is_dir():
            return
        seeded = False
        for source in sorted(source_root.iterdir()):
            if not (source / MANIFEST_NAME).is_file():
                continue
            target = self.root / source.name
            if target.exists():
                continue
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            seeded = True
            log.info("seeded built-in tool %s", source.name)
        if seeded:
            self.refresh()
