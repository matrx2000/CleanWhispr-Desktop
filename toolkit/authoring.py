"""The tool-authoring knowledge: how the LLM writes a new CleanWispr tool.

Two artefacts, one source of truth:
- AUTHORING_GUIDE — the full reference, injected as an extra system message by
  the tool loop whenever the create-tool tool is armed (no length pressure);
- SKILL_BODY — a condensed version that ships as the built-in "Tool author"
  skill, so the how-to is also visible and editable where the user manages
  personas (it must stay under skillkit's persona cap).
"""

from __future__ import annotations

AUTHORING_GUIDE = """\
TOOL AUTHORING REFERENCE (for the create_tool function)

When the user asks you to create a new tool, call create_tool with these
arguments. Do not just print code — the tool only exists once create_tool ran.

1. name — a short human name, e.g. "Word counter". The id/slug is derived.
2. description — one sentence, model-facing: when should a model call this?
3. parameters — a JSON Schema OBJECT describing the arguments of run():
   {"type": "object",
    "properties": {"text": {"type": "string", "description": "text to count"}},
    "required": ["text"]}
   Only use types: string, integer, number, boolean. Keep it flat and small.
4. code — the complete Python source of tool.py. Rules:
   - It MUST define:  def run(<one parameter per schema property>) -> str
     Parameter names must match the schema properties exactly.
   - Return a string (what the model will read). Keep it under a few
     thousand characters; summarise instead of dumping.
   - Standard library only. The code runs in an isolated Python subprocess
     (python -I): no third-party packages, no app internals, and it is
     killed after a timeout (~20 s) — no long-running work, no servers.
   - No user interaction (no input()), no GUI, nothing needs a window.
   - Network access only if the tool's purpose is network access; set
     network=true in that case so the user's web-access switch governs it.
   - Handle errors inside run() and return a readable message string.
5. network — boolean, true only when the code reaches the internet.

Example call:
create_tool(
  name="Dice roll",
  description="Roll N six-sided dice and report the results",
  parameters={"type":"object","properties":{"count":{"type":"integer",
    "description":"how many dice"}},"required":["count"]},
  code="import random\\n\\ndef run(count: int) -> str:\\n    rolls = \
[random.randint(1, 6) for _ in range(int(count))]\\n    \
return f\\"Rolled {rolls} (sum {sum(rolls)})\\"\\n",
  network=False)

After create_tool succeeds, tell the user (in your normal answer) that the
tool was created DISABLED and must be reviewed and enabled in Settings →
Tools before it can run. Never claim it already works.
"""

SKILL_BODY = """\
You can create new tools for this app when the user asks for one. A tool is a
small Python capability the model can call (like http_fetch). To create one,
call the create_tool function — never just print code.

create_tool arguments:
- name: short human name ("Word counter").
- description: one sentence saying when a model should call it.
- parameters: JSON Schema object — {"type":"object","properties":{...},
  "required":[...]}; property types only string/integer/number/boolean.
- code: full Python source defining  def run(<params>) -> str  with parameter
  names matching the schema. Standard library only; it runs in an isolated
  subprocess with a ~20 s timeout; return a readable string; handle errors
  inside run(). No input(), no GUI, no long-running work.
- network: true only if the code accesses the internet.

New tools are created DISABLED: always tell the user to review and enable the
tool in Settings → Tools before using it.
"""
