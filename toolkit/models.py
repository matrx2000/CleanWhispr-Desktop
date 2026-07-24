"""The ToolSpec data model — one installable capability the local LLM may call.

A tool is a folder: `tool.json` (this manifest) plus a `tool.py` whose entry
function does the work. The manifest carries an OpenAI/Ollama-style JSON-Schema
`parameters` object, so a spec converts 1:1 into the wire format Ollama's
/api/chat `tools` array expects (and into MCP's inputSchema shape, should a
bridge ever be wanted).

Pure stdlib, tolerant parsing (unknown keys ignored, missing keys defaulted) —
same philosophy as skillkit.models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENTRY = "tool.py:run"
DEFAULT_TIMEOUT_S = 20.0
MANIFEST_NAME = "tool.json"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "tool"


def _empty_schema() -> dict:
    return {"type": "object", "properties": {}, "required": []}


@dataclass
class ToolSpec:
    """One tool as described by its manifest.

    `parameters` is a JSON-Schema object (type/properties/required) describing
    the arguments of the entry function. `confirm` marks tools the user must
    approve per call; `native` names a host-implemented handler instead of a
    tool.py (used by the built-in create-tool). `needs_packages` runs the tool
    without Python's isolated -I flag so user-site packages import. `network`
    declares that the tool talks to the internet — such tools are additionally
    gated behind the library's master allow_network switch, because content
    fetched from the web is untrusted input to the model (prompt injection).
    """

    id: str
    name: str
    description: str = ""
    version: str = "1.0"
    parameters: dict = field(default_factory=_empty_schema)
    entry: str = DEFAULT_ENTRY
    timeout_s: float = DEFAULT_TIMEOUT_S
    confirm: bool = False
    enabled: bool = True  # effective value; the library overlays saved state
    builtin: bool = False
    native: str | None = None
    needs_packages: bool = False
    network: bool = False  # touches the internet → gated by allow_network
    path: Path | None = None  # tool folder on disk (None for pure-native specs)

    @property
    def wire_name(self) -> str:
        """Function name shown to the model — underscores, models handle those
        more reliably than hyphens in tool-call templates."""
        return self.id.replace("-", "_")

    def to_wire(self) -> dict:
        """Ollama/OpenAI function-calling tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.wire_name,
                "description": self.description or self.name,
                "parameters": self.parameters or _empty_schema(),
            },
        }

    def to_manifest(self) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": self.parameters,
            "entry": self.entry,
            "timeout_s": self.timeout_s,
            "confirm": self.confirm,
            "enabled": self.enabled,
        }
        if self.builtin:
            data["builtin"] = True
        if self.native:
            data["native"] = self.native
        if self.needs_packages:
            data["needs_packages"] = True
        if self.network:
            data["network"] = True
        return data

    @classmethod
    def from_manifest(cls, data: object, path: Path | None = None) -> ToolSpec | None:
        """Build a spec from manifest JSON; None when unusable (no id/name)."""
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "").strip()
        raw_id = str(data.get("id") or "").strip()
        if not name and not raw_id:
            return None
        parameters = data.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            parameters = _empty_schema()
        try:
            timeout_s = float(data.get("timeout_s", DEFAULT_TIMEOUT_S))
        except (TypeError, ValueError):
            timeout_s = DEFAULT_TIMEOUT_S
        return cls(
            id=raw_id or slugify(name),
            name=name or raw_id,
            description=str(data.get("description") or ""),
            version=str(data.get("version") or "1.0"),
            parameters=parameters,
            entry=str(data.get("entry") or DEFAULT_ENTRY),
            timeout_s=max(1.0, min(timeout_s, 300.0)),
            confirm=bool(data.get("confirm", False)),
            enabled=bool(data.get("enabled", True)),
            builtin=bool(data.get("builtin", False)),
            native=(str(data["native"]) if data.get("native") else None),
            needs_packages=bool(data.get("needs_packages", False)),
            network=bool(data.get("network", False)),
            path=path,
        )
