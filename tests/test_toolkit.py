"""toolkit: manifests, library state, zip exchange, and the subprocess runner."""

from __future__ import annotations

import json
import zipfile

import pytest

from toolkit.library import ToolError, ToolLibrary
from toolkit.models import ToolSpec
from toolkit.runner import ToolArgsError, run_packaged, validate_args

# --- helpers ---


def write_tool(root, tool_id, *, code=None, manifest_extra=None, params=None):
    folder = root / tool_id
    folder.mkdir(parents=True)
    manifest = {
        "id": tool_id,
        "name": tool_id.replace("-", " ").title(),
        "description": f"The {tool_id} tool",
        "parameters": params
        or {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
    manifest.update(manifest_extra or {})
    (folder / "tool.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "tool.py").write_text(
        code or "def run(text: str) -> str:\n    return text.upper()\n", encoding="utf-8"
    )
    return folder


@pytest.fixture
def library(tmp_path):
    return ToolLibrary(tmp_path / "tools", state_path=tmp_path / "tools.json")


# --- manifest / spec ---


def test_spec_wire_format_uses_underscores():
    spec = ToolSpec(id="http-fetch", name="HTTP fetch", description="fetch")
    wire = spec.to_wire()
    assert wire["type"] == "function"
    assert wire["function"]["name"] == "http_fetch"
    assert wire["function"]["parameters"]["type"] == "object"


def test_manifest_roundtrip_and_tolerance():
    spec = ToolSpec.from_manifest({"name": "My Tool", "unknown_key": 1, "timeout_s": "nope"})
    assert spec is not None
    assert spec.id == "my-tool"
    assert spec.timeout_s == 20.0  # bad value → default
    assert ToolSpec.from_manifest({"description": "no name"}) is None
    assert ToolSpec.from_manifest("not a dict") is None


# --- library scan / switches ---


def test_library_scans_and_arms_tools(library):
    write_tool(library.root, "shout")
    library.refresh()
    assert [s.id for s in library.all()] == ["shout"]
    assert [s.id for s in library.armed_specs()] == ["shout"]

    library.set_enabled("shout", False)
    assert library.armed_specs() == []
    library.set_config(enabled=False)
    library.set_enabled("shout", True)
    assert library.armed_specs() == []  # master switch off


def test_network_tools_gated_behind_allow_network(library):
    write_tool(library.root, "webby", manifest_extra={"network": True})
    library.refresh()
    assert library.armed_specs() == []  # allow_network defaults to OFF
    library.set_config(allow_network=True)
    assert [s.id for s in library.armed_specs()] == ["webby"]


def test_enabled_state_survives_reload(tmp_path):
    library = ToolLibrary(tmp_path / "tools", state_path=tmp_path / "tools.json")
    write_tool(library.root, "shout")
    library.refresh()
    library.set_enabled("shout", False)

    reloaded = ToolLibrary(tmp_path / "tools", state_path=tmp_path / "tools.json")
    assert reloaded.get("shout").enabled is False


def test_resolve_call_matches_wire_name(library):
    write_tool(library.root, "http-fetch")
    library.refresh()
    assert library.resolve_call("http_fetch").id == "http-fetch"
    assert library.resolve_call("nope") is None


# --- zip import / export ---


def test_zip_roundtrip(tmp_path, library):
    write_tool(library.root, "shout")
    library.refresh()
    bundle = library.export_zip("shout", tmp_path / "shout.zip")

    other = ToolLibrary(tmp_path / "other-tools", state_path=tmp_path / "other.json")
    imported = other.import_zip(bundle)
    assert imported.id == "shout"
    assert imported.enabled is False  # imported code needs review before running
    assert (other.root / "shout" / "tool.py").exists()


def test_zip_import_rejects_slip(tmp_path, library):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("tool.json", json.dumps({"name": "Evil"}))
        zf.writestr("../escape.py", "print('boo')")
    with pytest.raises(ToolError, match="escapes"):
        library.import_zip(evil)
    assert not (tmp_path / "escape.py").exists()


def test_zip_import_requires_manifest(tmp_path, library):
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("readme.txt", "not a tool")
    with pytest.raises(ToolError, match=r"tool\.json"):
        library.import_zip(plain)


def test_remove_refuses_builtin(library):
    write_tool(library.root, "core", manifest_extra={"builtin": True})
    library.refresh()
    with pytest.raises(ToolError, match="disabled but not deleted"):
        library.remove("core")
    write_tool(library.root, "extra")
    library.refresh()
    library.remove("extra")
    assert library.get("extra") is None


# --- create_tool (the LLM-facing factory) ---


def test_create_tool_lands_disabled(library):
    spec = library.create_tool(
        name="Word counter",
        description="Counts words",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        code="def run(text: str) -> str:\n    return str(len(text.split()))\n",
    )
    assert spec.enabled is False
    assert library.armed_specs() == []  # created but not armed until user enables
    library.set_enabled(spec.id, True)
    assert library.run(library.get(spec.id), {"text": "one two three"}) == "3"


def test_create_tool_rejects_bad_code(library):
    with pytest.raises(ToolError, match="syntax error"):
        library.create_tool("Broken", "x", None, "def run(:\n")
    with pytest.raises(ToolError, match="def run"):
        library.create_tool("NoEntry", "x", None, "x = 1\n")


def test_native_create_tool_via_run(library):
    write_tool(
        library.root,
        "create-tool",
        manifest_extra={"native": "create_tool"},
        params={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "code": {"type": "string"},
            },
            "required": ["name", "description", "code"],
        },
    )
    library.refresh()
    spec = library.get("create-tool")
    result = library.run(
        spec,
        {
            "name": "Greeter",
            "description": "greets",
            "code": "def run() -> str:\n    return 'hi'\n",
        },
    )
    assert "DISABLED" in result
    assert library.get("greeter") is not None


# --- argument validation ---


def test_validate_args_requires_and_coerces():
    params = {
        "type": "object",
        "properties": {"count": {"type": "integer"}, "flag": {"type": "boolean"}},
        "required": ["count"],
    }
    assert validate_args(params, {"count": "3", "flag": "true", "junk": 1}) == {
        "count": 3,
        "flag": True,
    }
    with pytest.raises(ToolArgsError, match="count"):
        validate_args(params, {})
    with pytest.raises(ToolArgsError, match="JSON object"):
        validate_args(params, "not a dict")


# --- subprocess runner ---


def test_runner_executes_tool(tmp_path):
    folder = write_tool(tmp_path, "shout")
    spec = ToolSpec(id="shout", name="Shout", path=folder)
    assert run_packaged(spec, {"text": "hello"}) == "HELLO"


def test_runner_captures_prints_when_no_return(tmp_path):
    folder = write_tool(
        tmp_path, "printer", code="def run(text: str):\n    print('got', text)\n"
    )
    spec = ToolSpec(id="printer", name="P", path=folder)
    assert run_packaged(spec, {"text": "x"}).strip() == "got x"


def test_runner_reports_tool_exceptions_as_text(tmp_path):
    folder = write_tool(
        tmp_path, "boom", code="def run(text: str) -> str:\n    raise ValueError('nope')\n"
    )
    spec = ToolSpec(id="boom", name="B", path=folder)
    result = run_packaged(spec, {"text": "x"})
    assert "failed" in result and "ValueError" in result


def test_runner_survives_non_ansi_unicode(tmp_path):
    # regression: a Windows child prints stdout in the ANSI code page, so a
    # result with characters outside it (emoji, non-Latin) used to crash the
    # verdict print with UnicodeEncodeError — web pages hit this constantly
    folder = write_tool(
        tmp_path,
        "unicode",
        code='def run(text: str) -> str:\n    return "héllo — ünïcode 😀 → done"\n',
    )
    spec = ToolSpec(id="unicode", name="U", path=folder)
    assert run_packaged(spec, {"text": "x"}) == "héllo — ünïcode 😀 → done"


def test_runner_kills_on_timeout(tmp_path):
    folder = write_tool(
        tmp_path,
        "sleepy",
        code="import time\n\ndef run(text: str) -> str:\n    time.sleep(60)\n    return 'late'\n",
    )
    spec = ToolSpec(id="sleepy", name="S", timeout_s=2.0, path=folder)
    assert "timed out" in run_packaged(spec, {"text": "x"})


# --- built-in tools ship and load ---


def test_seed_builtins_installs_starter_tools(tmp_path):
    library = ToolLibrary(tmp_path / "tools", state_path=tmp_path / "tools.json")
    library.seed_builtins()
    ids = {s.id for s in library.all()}
    assert {"http-fetch", "run-python", "create-tool"} <= ids
    fetch = library.get("http-fetch")
    assert fetch.network is True and fetch.builtin is True
    assert library.get("run-python").confirm is True
    # network gate: even enabled, http-fetch is not armed until web access is on
    assert "http-fetch" not in {s.id for s in library.armed_specs()}


def test_builtin_run_python_executes(tmp_path):
    library = ToolLibrary(tmp_path / "tools", state_path=tmp_path / "tools.json")
    library.seed_builtins()
    spec = library.get("run-python")
    result = library.run(spec, {"code": "print(2 + 2)"})
    assert result.strip() == "4"
