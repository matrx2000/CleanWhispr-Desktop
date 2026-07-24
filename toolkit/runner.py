"""Tool execution — run a tool's entry function with OS-level isolation.

Security posture (per the sandboxing survey): restricted-builtins tricks are
not a boundary; a subprocess is. Packaged tools therefore run in a fresh
`python -I` child (isolated mode: no user site, no PYTHONPATH, no cwd on
sys.path) with a hard timeout and an output cap. When the app itself is a
frozen bundle (PyInstaller — sys.executable is the app, not a Python), tools
fall back to an in-process daemon thread with a soft timeout; the manifest's
confirm flag and the user's per-tool enable switch remain the real gate there.

Tool errors come back as *strings*, not exceptions — they are fed to the model,
which can read the message and adapt. Only argument-validation problems raise
(`ToolArgsError`), because those are caught and turned into a model-visible
message by the tool loop.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

from toolkit.models import ToolSpec

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8_000
_SENTINEL = "@@CW_TOOL@@"

# Runs inside `python -I -c` in the child. Reads {dir, file, func, args} as JSON
# from stdin, imports the tool module by path, calls func(**args), and prints a
# sentinel-prefixed JSON verdict as the last line (tool print() output is
# captured and used as the result when the function returns None).
#
# The verdict is serialised with ensure_ascii (the json default) ON PURPOSE:
# a Windows child writes stdout in the legacy ANSI code page, and any page/
# result character outside it (emoji, non-Latin text) would crash the print
# with UnicodeEncodeError. \uXXXX escapes survive any code page and json.loads
# on the parent side restores them losslessly.
_BOOTSTRAP = r"""
import io, json, sys, traceback
from contextlib import redirect_stdout
payload = json.load(sys.stdin)
sys.path.insert(0, payload["dir"])
import importlib.util
buffer = io.StringIO()
try:
    spec = importlib.util.spec_from_file_location("cleanwispr_tool", payload["file"])
    module = importlib.util.module_from_spec(spec)
    with redirect_stdout(buffer):
        spec.loader.exec_module(module)
        result = getattr(module, payload["func"])(**payload["args"])
    if isinstance(result, str):
        out = result
    elif result is None:
        out = buffer.getvalue()
    else:
        out = json.dumps(result, ensure_ascii=False, default=str)
    print(_S + json.dumps({"ok": True, "result": out}))
except Exception:
    print(_S + json.dumps({"ok": False, "error": traceback.format_exc(limit=4)}))
""".replace("_S", repr(_SENTINEL))


class ToolArgsError(ValueError):
    """The model sent arguments that don't fit the tool's schema; the message
    is fed back to the model so it can correct the call."""


def validate_args(parameters: dict, args: object) -> dict:
    """Check model-supplied arguments against the manifest's JSON schema:
    required keys present, unknown keys dropped, primitive types coerced
    (models frequently send numbers/bools as strings)."""
    if not isinstance(args, dict):
        raise ToolArgsError("arguments must be a JSON object")
    properties = parameters.get("properties") or {}
    required = parameters.get("required") or []
    missing = [key for key in required if key not in args]
    if missing:
        raise ToolArgsError(f"missing required argument(s): {', '.join(missing)}")
    clean: dict = {}
    for key, value in args.items():
        if key not in properties:
            continue  # unknown extras are dropped, not fatal
        clean[key] = _coerce(value, (properties[key] or {}).get("type"))
    return clean


def _coerce(value: object, schema_type: str | None) -> object:
    try:
        if schema_type == "string" and not isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if schema_type == "integer" and not isinstance(value, bool):
            return int(value)  # type: ignore[arg-type]
        if schema_type == "number" and not isinstance(value, bool):
            return float(value)  # type: ignore[arg-type]
        if schema_type == "boolean" and isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
    except (TypeError, ValueError):
        pass  # leave as-is; the tool sees the original and can complain
    return value


def _cap(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_RESULT_CHARS:
        return text[:MAX_RESULT_CHARS] + "\n… [result truncated]"
    return text


def run_packaged(spec: ToolSpec, args: dict) -> str:
    """Execute a folder tool's entry function; always returns a string."""
    if spec.path is None:
        return f"Tool '{spec.id}' has no folder on disk"
    file_name, _, func = spec.entry.partition(":")
    func = func or "run"
    entry_file = (spec.path / file_name).resolve()
    if not entry_file.is_file() or spec.path.resolve() not in entry_file.parents:
        return f"Tool '{spec.id}' entry file '{file_name}' not found"
    payload = json.dumps(
        {"dir": str(spec.path), "file": str(entry_file), "func": func, "args": args}
    )
    if getattr(sys, "frozen", False):
        return _run_in_process(spec, entry_file, func, args)
    return _run_subprocess(spec, payload)


def _run_subprocess(spec: ToolSpec, payload: str) -> str:
    # -X utf8 (not PYTHONIOENCODING — isolated mode's -E ignores PYTHON* env
    # vars) keeps the child's stdio UTF-8 even on ANSI-code-page Windows
    cmd = [sys.executable, "-X", "utf8"]
    if not spec.needs_packages:
        cmd.append("-I")
    cmd += ["-c", _BOOTSTRAP]
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.timeout_s,
            cwd=spec.path,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return f"Tool '{spec.id}' timed out after {spec.timeout_s:.0f}s"
    except OSError as exc:
        return f"Tool '{spec.id}' could not start: {exc}"
    verdict = _parse_verdict(completed.stdout)
    if verdict is not None:
        ok, text = verdict
        return _cap(text) if ok else f"Tool '{spec.id}' failed:\n{_cap(text)}"
    stderr = (completed.stderr or "").strip()
    return f"Tool '{spec.id}' produced no result" + (f"\n{_cap(stderr)}" if stderr else "")


def _parse_verdict(stdout: str) -> tuple[bool, str] | None:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(_SENTINEL):
            try:
                data = json.loads(line[len(_SENTINEL):])
            except json.JSONDecodeError:
                return None
            if data.get("ok"):
                return True, str(data.get("result") or "")
            return False, str(data.get("error") or "unknown error")
    return None


def _run_in_process(spec: ToolSpec, entry_file: Path, func: str, args: dict) -> str:
    """Frozen-bundle fallback: same import-and-call, on a daemon thread with a
    soft timeout (the thread cannot be killed; it is abandoned)."""
    import importlib.util
    import io
    from contextlib import redirect_stdout

    result: dict = {}

    def work() -> None:
        buffer = io.StringIO()
        try:
            module_spec = importlib.util.spec_from_file_location("cleanwispr_tool", entry_file)
            module = importlib.util.module_from_spec(module_spec)  # type: ignore[arg-type]
            sys.path.insert(0, str(spec.path))
            try:
                with redirect_stdout(buffer):
                    module_spec.loader.exec_module(module)  # type: ignore[union-attr]
                    value = getattr(module, func)(**args)
            finally:
                sys.path.remove(str(spec.path))
            if isinstance(value, str):
                result["ok"] = value
            elif value is None:
                result["ok"] = buffer.getvalue()
            else:
                result["ok"] = json.dumps(value, ensure_ascii=False, default=str)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=work, daemon=True, name=f"tool-{spec.id}")
    thread.start()
    thread.join(timeout=spec.timeout_s)
    if thread.is_alive():
        return f"Tool '{spec.id}' timed out after {spec.timeout_s:.0f}s"
    if "error" in result:
        return f"Tool '{spec.id}' failed:\n{_cap(result['error'])}"
    return _cap(str(result.get("ok", "")))
