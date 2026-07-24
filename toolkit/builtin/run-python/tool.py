"""Run Python — execute a model-written snippet and report its output.

The heavy lifting (isolated subprocess, timeout, output capture) is done by
the toolkit runner that launches this file; this entry only needs to exec the
snippet and collect something readable. Requires per-call confirmation via
its manifest — the user sees the code before it runs.
"""

from __future__ import annotations

import ast
import io
from contextlib import redirect_stdout


def run(code: str) -> str:
    namespace: dict = {"__name__": "__main__"}
    buffer = io.StringIO()
    try:
        tree = ast.parse(code, mode="exec")
        # if the snippet ends in a bare expression, report its value like a REPL
        trailing = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            trailing = ast.Expression(tree.body.pop().value)  # type: ignore[attr-defined]
        with redirect_stdout(buffer):
            exec(compile(tree, "<tool>", "exec"), namespace)
            value = eval(compile(trailing, "<tool>", "eval"), namespace) if trailing else None
    except Exception as exc:
        printed = buffer.getvalue()
        prefix = f"{printed}\n" if printed.strip() else ""
        return f"{prefix}Error: {type(exc).__name__}: {exc}"
    printed = buffer.getvalue().strip()
    parts = [p for p in (printed, repr(value) if value is not None else "") if p]
    return "\n".join(parts) or "(no output — use print() to report results)"
