# skillkit

A small, portable **"skills" layer for LLM apps**. A *skill* is a named, reusable
role/persona — *"a formal editor"*, *"a witty poet"* — that flavours an LLM
call's **tone and voice** without ever touching your app's **output contract**.

skillkit gives you four things, each usable on its own:

1. **A data model + library** — define skills, stack several active at once, persist them.
2. **A prompt composer** — weave the active personas into *your* base prompt using a
   guardrail-hardened "sandwich" so a user-authored persona can shape voice but
   **can't** override your formatting/safety rules or break out of a data fence.
3. **A deterministic voice parser** — turn a (mis-)transcribed utterance like
   *"switch to poet"* / *"plain"* into a switch intent, with fuzzy name matching. No LLM call.
4. **Optional Qt (PySide6) widgets** — a `/`-style quick-switch palette and a full
   add/edit/delete manager.

## Why it's portable

```
skillkit/
├── models.py     Skill dataclass + default_skills()          ── stdlib only
├── library.py    SkillLibrary: CRUD, stackable active set     ── stdlib only
├── store.py      SkillStore protocol · JsonSkillStore          ── stdlib only
├── compose.py    PromptSpec · compose_messages()               ── stdlib only
├── voice.py      parse_switch() · match_skill()                ── stdlib only
└── qt/           SkillsBridge · SkillPalette · SkillsManager   ── PySide6 only
```

- **The core has zero third-party dependencies** — just the standard library. Drop the
  folder into any project and `import skillkit`.
- **Provider-agnostic** — `compose_messages()` returns plain `[{"role", "content"}]`
  dicts. Map them to your SDK's message type in two lines.
- **Storage-agnostic** — the library reads/writes an opaque dict through a `SkillStore`.
  Use the bundled `JsonSkillStore`, or implement the two-method protocol to keep skills
  inside your own config.
- **Qt is optional** — nothing in the core imports it. Skip `skillkit.qt` and build your
  own UI, or use none at all.

To move it to another app: copy the `skillkit/` folder and write a ~30-line adapter
(see [Integration](#integration-a-drop-in-checklist)).

---

## Install

There's no PyPI package — it's designed to be **vendored**: copy the `skillkit/`
directory into your project. The core needs only Python 3.11+. The `skillkit.qt`
subpackage additionally needs `PySide6>=6.8`.

---

## The data model

```python
@dataclass
class Skill:
    id: str                    # stable slug, e.g. "formal-email"
    name: str                  # "Formal email"
    description: str = ""       # one line, shown in pickers
    body: str = ""             # the persona instruction (UNTRUSTED, tone-only)
    enabled: bool = True
    builtin: bool = False       # shipped default; make it read-only in your UI
    scope: str = "both"         # "editor" | "notes" | "both" — which legs it flavours
    triggers: list[str] = []    # spoken aliases incl. known mishears ("poyet")
    temperature: float | None = None   # None → inherit your default
    model: str | None = None           # None → inherit your default
```

The **library** also holds library-wide state (persisted alongside the skills):
`enabled` (master switch), `active_ids` (the ordered, *stackable* active set),
`voice_switching`, and the fuzzy-match thresholds.

---

## Integration: a drop-in checklist

1. **Create a library** (once, at startup) and seed starter skills on first run:

   ```python
   from skillkit import SkillLibrary, JsonSkillStore, default_skills
   library = SkillLibrary(JsonSkillStore("skills.json"), seed=default_skills())
   ```

2. **Compose prompts** with the active skills wherever you build LLM messages:

   ```python
   from skillkit.compose import PromptSpec, compose_messages
   spec = PromptSpec(role_framing=..., output_rules=..., instruction=cmd, data=selection)
   messages = compose_messages(spec, library.active_skills("editor"))
   ```

3. **(Optional) voice switching** — before treating a transcript as an instruction:

   ```python
   from skillkit import voice
   verdict = voice.parse_switch(transcript, library)
   if verdict.outcome != voice.PASSTHROUGH:
       if verdict.outcome == voice.APPLIED:
           library.apply_verdict(verdict)   # arm/clear the skill(s)
       notify(verdict.notice)               # "Skill: Poet" / "Skills off (plain)"
       return                               # a switch is not an edit
   ```

4. **(Optional) UI** — drop in the palette + manager (see [Qt UI](#qt-ui-optional)).

That's it. When `library.enabled` is `False`, `active_skills()` returns `[]` and the
whole feature is a pure no-op — your prompts are byte-for-byte what they were before.

---

## Examples

### 1. Minimal — no persistence, no Qt, any LLM

```python
from skillkit import SkillLibrary, MemorySkillStore, Skill
from skillkit.compose import PromptSpec, compose_messages

lib = SkillLibrary(MemorySkillStore())
lib.set_enabled(True)
lib.add(Skill(id="poet", name="Poet", body="Write with vivid imagery and rhythm."))
lib.activate("poet")

spec = PromptSpec(
    role_framing="You rewrite the user's text as instructed.",
    output_rules="Output ONLY the rewritten text. No preamble, no code fences.",
    instruction="make it inviting",
    data="Meeting moved to 3pm.",
    data_noun="TEXT",
)
messages = compose_messages(spec, lib.active_skills())

# messages == [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
# hand them to your provider (mapping the dicts to its message type):
#   openai.chat.completions.create(model=..., messages=messages)
#   ollama.chat(model=..., messages=messages)
#   anthropic: pull the system dict out into the `system` param, pass the rest as messages
```

### 2. Composer only — no library, just harden one persona onto your prompt

```python
from skillkit import Skill
from skillkit.compose import PromptSpec, compose_messages

persona = Skill(id="p", name="Pirate", body="Talk like a friendly pirate.")
spec = PromptSpec(
    role_framing="You are a text editor.",
    output_rules="Output ONLY the edited text.",
    instruction="make it a greeting",
    data="hello team",           # wrapped in a per-request nonce fence + escaped
)
messages = compose_messages(spec, [persona])
# The persona shapes tone; your output_rules are restated AFTER it (system) and
# again in a user-message trailer, so they win the tie. The data can't forge the
# closing fence marker, and the persona can't close the <style> block early.
```

### 3. Custom storage adapter — keep skills inside your own config

```python
class MyConfigStore:                       # satisfies skillkit.SkillStore (a Protocol)
    def __init__(self, app_config): self._cfg = app_config
    def load(self):  return self._cfg.get("skills")          # dict | None
    def save(self, data): self._cfg["skills"] = data; self._cfg.persist()

library = SkillLibrary(MyConfigStore(my_app_config), seed=default_skills())
```

Any object with `load() -> dict | None` and `save(dict)` works. `MemorySkillStore`
(non-persistent) and `JsonSkillStore` (atomic file, corrupt→`.bak`) ship built in.

### 4. Voice control

```python
from skillkit import voice, SkillLibrary, MemorySkillStore, default_skills

lib = SkillLibrary(MemorySkillStore(), seed=default_skills())
lib.set_enabled(True)

for utterance in ["switch to poet", "use the concise skill", "make it formal", "plain"]:
    v = voice.parse_switch(utterance, lib)
    if v.outcome == voice.APPLIED:
        lib.apply_verdict(v)
        print(utterance, "→", v.notice, "| active:", [s.id for s in lib.active_skills()])
    elif v.outcome == voice.REJECTED:
        print(utterance, "→ (looked like a switch but no match)", v.notice)
    else:
        print(utterance, "→ passthrough (a normal instruction)")
```

Grammar (short utterances only, so dictated prose never trips it):

| Say | Effect |
|-----|--------|
| `switch to <name>` | make `<name>` the only active skill |
| `use / activate / add <name>` (+ a role-noun for common verbs) | add `<name>` to the active set |
| `deactivate <name>` / `remove the <name> skill` | drop `<name>` |
| `plain` · `stop` · `clear` · `normal` · `default` | clear all active skills |

`<name>` is fuzzy-matched (stdlib `difflib`) against each skill's name **and**
`triggers`. Seed `triggers` with known transcription mishears (`"poyet"` → Poet) for
robustness. An accept-floor + runner-up margin means a weak or tied match never
switches silently — it returns `REJECTED` instead of guessing.

### 5. Qt UI (optional)

```python
from skillkit.qt import SkillsBridge, SkillPalette, SkillsManager

bridge = SkillsBridge(library)         # fans library changes out as a Qt signal,
                                       # thread-safe (a voice switch may fire off-thread)

# a "/"-style quick switcher (frameless, theme-aware, keyboard-driven)
palette = SkillPalette(library, changed_signal=bridge.changed)
palette.create_requested.connect(open_my_manager)   # footer "+ Create" row
palette.popup()                        # show it centred, focused, ready to type

# a full manager to embed in a settings tab or window
manager = SkillsManager(
    library,
    changed_signal=bridge.changed,
    model_choices=lambda: my_installed_models(),      # optional: per-skill model combo
    on_test=lambda skill: run_a_sample(skill),        # optional: "Test skill" button
)
my_settings_tab.layout().addWidget(manager)
```

Both widgets take colours from the active `QPalette`, so they inherit the host's
theme. Wire **one** `SkillsBridge` and share it, so the palette, manager, tray, and
your pipeline all stay in sync from a single source of truth.

---

## Prompt safety, in detail

`compose_messages` builds:

```
system = role framing
       + data-fence rule           (per-request nonce markers, only when there is DATA)
       + task rules
       + <style> …personas… </style>
       + STYLE SCOPE note          "the style customises tone only; rules above win"
       + output rules              (your immutable contract, stated last in system)
user   = <<<NOUN:nonce>>> DATA <<<END:nonce>>>   (only when there is DATA)
       + Instruction: …
       + trailer                   (restates the output contract one final time)
```

- **Guardrail sandwich.** Frontier-model guidance is that later instructions win ties,
  so the contract is stated *after* the persona (in system) and *again* at the very end
  (the user trailer). A persona saying *"explain your reasoning"* is bracketed as
  tone-only and out-voted twice.
- **Nonce fences.** Each request wraps DATA in `<<<TEXT:ab12cd>>> … <<<END:ab12cd>>>`
  with a random nonce. The closing marker is scrubbed out of both the DATA and the
  persona text, so neither can forge a break-out. (This also fixes the classic bug where
  a document literally containing `<<<END>>>` escapes a static fence.)
- **Trusted vs untrusted.** The persona is *user-authored* (trusted-ish) but still
  scoped to tone; the real threat is the DATA, which is fully fenced and inert.

The composer is the reusable engine; **your app supplies the wording** (`role_framing`,
`task_rules`, `output_rules`) via `PromptSpec`, so the guardrails travel with skillkit
while the voice stays yours.

---

## Public API

| Import | What |
|--------|------|
| `SkillLibrary`, `LibraryConfig` | the collection + its state |
| `Skill`, `default_skills`, `slugify` | the model + starter skills |
| `SkillStore`, `JsonSkillStore`, `MemorySkillStore` | persistence |
| `PromptSpec`, `compose_messages`, `build_persona_block`, `DEFAULT_TRAILER` | prompt layering |
| `voice.parse_switch`, `voice.match_skill`, `SwitchVerdict` | voice control |
| `SCOPE_EDITOR`, `SCOPE_NOTES`, `SCOPE_BOTH` | scope constants |
| `skillkit.qt.SkillsBridge / SkillPalette / SkillsManager` | optional PySide6 UI |

Key `SkillLibrary` methods: `set_enabled`, `enabled`, `all`, `enabled_skills`,
`active_skills(scope=None)`, `activate` / `deactivate` / `toggle` / `replace_active` /
`set_active` / `clear_active`, `add` / `create` / `update` / `duplicate` / `remove` /
`set_skill_enabled`, `resolved_temperature(scope)` / `resolved_model(scope)`,
`apply_verdict(verdict)`, `subscribe(callback)`.

---

## Reference integration

CleanWispr (the app this was built for) wires skillkit in ~30 lines across three files —
a good worked example:

- `cleanwispr/llm/prompts.py` — builds a `PromptSpec` and calls `compose_messages` when
  skills are active (and stays byte-identical to the old prompt when they're not).
- `cleanwispr/core/controller.py` — runs `voice.parse_switch` on the editor leg, resolves
  `active_skills("editor")`, and applies per-skill temperature/model overrides.
- `cleanwispr/app.py` — creates the `JsonSkillStore`/`SkillLibrary`/`SkillsBridge`,
  the `SkillPalette`, and the tray submenu; `cleanwispr/ui/settings/skills_tab.py`
  embeds `SkillsManager`.
