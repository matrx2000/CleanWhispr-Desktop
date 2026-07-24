"""toolkit — user-installable tools the local LLM can execute.

Skills say HOW the model should write; tools are WHAT the model can do:
small Python packages (a folder with tool.json + tool.py) that the model
calls through Ollama function calling. Tools are exchanged as zip files,
gated by per-tool and master switches, and executed in an isolated
subprocess with a timeout.

Standalone by design (stdlib only), mirroring skillkit: the host app wires a
ToolLibrary and passes armed specs to its LLM tool loop.
"""

from __future__ import annotations

from toolkit.library import ToolError, ToolkitConfig, ToolLibrary
from toolkit.models import ToolSpec, slugify
from toolkit.runner import MAX_RESULT_CHARS, ToolArgsError, validate_args

__all__ = [
    "MAX_RESULT_CHARS",
    "ToolArgsError",
    "ToolError",
    "ToolLibrary",
    "ToolSpec",
    "ToolkitConfig",
    "slugify",
    "validate_args",
]
