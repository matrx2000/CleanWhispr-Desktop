# CleanWispr — Project Specification

A lightweight, fully local voice-to-text and voice-driven text-editing desktop app for Windows 10/11 and Linux, written in **Python + PySide6**. Inspired by (and reusing ideas/assets from) the MIT-licensed [OpenWhispr](openwhispr-main/) project, with all cloud/account/subscription functionality removed.

---

## 1. Goals

- **Dictation as a primary PC input method**: press a global hotkey anywhere, speak, and the transcribed text is typed/pasted at the cursor of the active application. Performance must match OpenWhispr (persistent local inference servers, no per-utterance model load).
- **Voice editor**: a second, separate hotkey records a spoken *instruction*; a locally running LLM (Ollama first) applies that instruction to the selected text (add/remove/rewrite) and replaces it in place.
- **Notes**: a built-in notetaking view (its own hotkey) — a WYSIWYG Markdown editor with multi-vault storage, image attachments, and the same local dictation/LLM engines driving voice input from an on-screen slider (no injection into a foreign app).
- **100% local**: no accounts, no cloud APIs, no telemetry, no network calls except model downloads (HuggingFace/GitHub) and localhost inference servers.
- **Modular by design**: STT engines and LLM providers sit behind small interfaces so new backends can be added without touching the app core.
- **Cross-platform**: Windows 10/11 first-class; Linux (X11 first, Wayland desktops incrementally).

## 2. Non-Goals (explicitly out of scope)

- Cloud transcription or cloud LLM providers, auth, accounts, usage quotas, referral/upgrade UI.
- Meeting detection/transcription, speaker diarization, calendar integration, semantic/vector search, chat agent overlay. (A local notetaking view — F3 — *is* in scope; the excluded items are the heavier "meeting assistant" features.)
- Automatic "AI cleanup" of dictated text. Dictation output is the **raw transcript**. (LLM editing happens only when explicitly triggered via the editor hotkey.)
- macOS as a first-class target (experimental support exists — injector, Metal engine build, `scripts/build_macos.py` — but it is untested and gets no dedicated effort).
- JavaScript/Electron anything.

## 3. The Core Features

### F1 — Dictation (voice → text at cursor)

Flow: hotkey → capture mic audio (16 kHz mono PCM) → local STT engine → inject text into active app.

- **Activation modes** (per-hotkey, user-selectable, same as OpenWhispr):
  - **Toggle (tap-to-talk)**: press once to start, press again to stop.
  - **Push-to-hold**: recording lasts while the key is held (key-down starts, key-up stops; minimum-hold debounce ~150 ms to reject accidental taps).
- **Cancel**: `Escape` while recording discards the take.
- **Silence gate**: RMS/peak analysis drops empty recordings before they reach the STT engine (port of OpenWhispr's `localSpeechGate` idea).
- **Text injection**: write transcript to clipboard, simulate paste keystroke, optionally restore prior clipboard contents.
- **Languages**: full Whisper set (58+ languages incl. `auto`), passed to the engine per-request. Parakeet models expose their own language sets (25 for `parakeet-tdt-0.6b-v3`).
- **Custom dictionary**: user word/phrase list passed as Whisper `initial_prompt` to bias recognition.
- **Transcript normalization**: engines return text split into timed segments joined by newlines that fall mid-sentence (whisper.cpp especially); a shared `stt.base.normalize_transcript` collapses whitespace runs so output is flowing prose, not stray line breaks. Applied by every engine before returning a result.
- **Live typing** (`stt.live` + `inject.live`, on by default, toggle in Settings → Transcription): while recording, the engine re-transcribes the growing take on a background thread and words are typed into the target app as soon as **two consecutive hypotheses agree** on them (the LocalAgreement-2 policy from UFAL's whisper_streaming — agreement filters both word flicker and silence hallucinations, so committed text almost never needs revising). When the take ends, the authoritative full-recording transcript corrects the preview **in place** with a minimal backspace-and-retype delta (`reconcile`, the nerd-dictation technique). Safety rules learned from the injection-failure survey: typing pauses while modifier keys are physically held; the focused window is fingerprinted on the first keystroke and typing **freezes** if focus changes (final text then lands on the clipboard instead); newlines are never typed; terminals are skipped entirely (synthetic backspaces go through the shell's line editor) and fall back to the classic paste; silence-only buffers never reach the engine. The preview loop is serial — a slow engine lowers the preview rate instead of piling up requests — and the final transcription waits for any in-flight preview request. Backends without keystroke primitives (`supports_live_typing = False`, e.g. Wayland) keep the classic paste-once behaviour unchanged.

### F2 — Voice Editor (voice instruction → LLM edit of selected text)

Flow: user selects text in any app → editor hotkey → speaks an instruction ("delete the second sentence", "add a bullet about X", "make this formal") → instruction is transcribed by the STT engine → selected text is captured (simulated `Ctrl+C`, clipboard restored after) → both are sent to the local LLM → the edited text replaces the selection (paste).

- Same toggle / push-to-hold activation modes and configurable hotkey as dictation.
- **Prompting**: a hardened system prompt (adapted from OpenWhispr's `fullPrompt` in `src/locales/en/prompts.json` — instruction-following, output-only-the-result, prompt-injection resistant). The LLM must return *only* the edited text.
- If no text is selected, the instruction is treated as a generation request ("write a short apology email") and the result is inserted at the cursor.
- Streaming responses are consumed but injected once complete (v1); live preview is a later enhancement.

### F3 — Notes (voice-driven notetaking view)

A standalone window (own global hotkey, or tray → *Open Notes…*) that hides to the tray on close and never quits the app. It reuses the STT and LLM engines but **injects nothing** into other apps — results flow back into its own editor via controller signals (`notes_text_ready`, `notes_ai_ready`).

- **Storage**: notes are **HTML files** (portable; hold colours and styled tables that Markdown can't). A *vault* is a folder of notes; a vault may contain *project* subfolders. Multiple vaults are configurable and switchable; legacy `.md` notes are read and migrate to `.html` on first save. Markdown import/export is available. Default vault: `<data_dir>/notes`.
- **Editor** (`NoteEditor`, a `QTextEdit`): headings, bullet/numbered/checklist lists, inline code, custom text and highlight colours, and rich tables (insert/move/merge/split rows and columns, table properties). **Pasted or dropped images** are written to an `attachments/` folder beside the note and linked by relative path, so a note stays self-contained and a whole vault can be moved/synced/backed up as one folder.
- **Voice input via a gated-shifter slider** (`SlideMicToggle`, ported from CleanWhispr-Flutter): drag the thumb **left** to dictate into the note at the cursor, **right** for an AI take, **up** to peek the raw Markdown, **down** to undo the last voice insert; tap to start/stop. The drag locks to one axis (never diagonal) with a live bubble naming the release action.
- **AI-take modes** (`core.controller` session kinds `NOTES_DICTATION` / `NOTES_AI`): a selection → **edit only that range**; no selection → **generate and append** at the end (existing note never wiped). A `whole_note` prompt (`llm.prompts.build_whole_note_messages`, injection-hardened, returns the full revised note) also exists for operating on the entire note.
- Notes dictation and AI takes are logged to the same history DB as F1/F2 (kinds `dictation` / `edit`).

### F4 — Skills (reusable LLM roles / personas)

A modular layer between the UI and the LLM that flavours the voice editor (F2) and Notes AI (F3) with a reusable *role* — "a formal editor", "a witty poet", or a **Tables** formatter. Implemented as a **standalone, portable package** `skillkit/` (repo root, sibling to `cleanwispr/`) so the whole persona layer can be reused in other LLM apps; CleanWispr wires it in through a thin adapter.

- **Data model** (`skillkit.Skill`): `id`, `name`, `description`, `body` (the persona instruction — untrusted, tone/formatting only), `enabled`, `builtin`, `scope` (`editor` / `notes` / `both`), voice `triggers`, and optional per-skill `temperature` / `model` overrides. A `SkillLibrary` holds the skills, a master on/off, and a **stackable** ordered active set; it persists through a `SkillStore` seam (default `JsonSkillStore` → `<config_dir>/skills.json`, atomic writes, corrupt→`.bak`), **not** inside `config.json`, so the whole feature travels as one portable file.
- **Prompt layering** (`skillkit.compose`): active personas are woven into the F2/F3 base prompts as a scoped `<style>` block using a *guardrail sandwich* — the app's output contract is stated after the persona and restated in a user-message trailer, and DATA is wrapped in **per-request nonce fences** with the close marker scrubbed from both data and persona. A persona can shape tone/formatting but cannot override the output rules or be hijacked by document text. With no active skill the prompt is byte-identical to F2/F3 without skills.
- **Voice switching** (`skillkit.voice`, voice editor only): a short utterance matching a switch grammar arms/clears skills instead of running an edit — `"switch to <name>"` (replace), `"use"/"activate <name>"` (add — stackable), `"deactivate <name>"` (remove), `"plain"`/`"stop"`/`"clear"` (clear all). The spoken name is fuzzy-matched (stdlib `difflib`) against each skill's name + triggers with an accept-floor and runner-up margin; a weak/tied match is rejected, never guessed. **No extra LLM call.** Anything not matching the grammar flows through as a normal instruction.
- **UI** (`skillkit.qt`, PySide6-only): a `SkillsManager` (add/edit/duplicate/delete, triggers, scope, per-skill temperature+model, **Test skill**, list-width slider, **JSON import/export** to exchange skills) embedded in Settings → Skills; a `SkillPalette` "/" quick-switcher; and a tray **Skills** submenu (checkable per skill). A `SkillsBridge` re-emits library changes as a Qt signal so voice and UI stay in sync (changes may originate off the UI thread).
- **Built-ins**, seeded on first run only: Formal, Concise, Friendly, Poet, and **Tables** (teaches GitHub-flavoured Markdown pipe-table syntax so tables render correctly in Notes via Qt's `setMarkdown`). The feature ships **enabled with Tables active** by default; the master switch off makes it a pure no-op.
- **Controller wiring**: `core.controller._run_edit` runs the voice switch, then resolves `active_skills("editor")`; `_run_notes_ai` resolves `active_skills("notes")` (no voice switch). Both apply per-skill temperature/model overrides. The controller imports only the pure `skillkit` core — never the Qt UI, preserving the seam discipline.

### F5 — Tools (Python capabilities the LLM can execute)

Skills say HOW the model writes; **tools are WHAT it can do**. A tool is a folder — `tool.json` (manifest with an OpenAI/Ollama-style JSON-Schema `parameters` object) plus a `tool.py` whose `run(**args) -> str` does the work — that the voice editor's LLM calls through **Ollama native function calling** (`/api/chat` `tools` array; `tool_calls` stream back with `arguments` already parsed; results return as `role:"tool"` messages with `tool_name`). Implemented as a **standalone package** `toolkit/` (repo root, sibling to `skillkit/`), stdlib-only, host-agnostic.

- **Library** (`toolkit.ToolLibrary`): tools live under `<config_dir>/tools/<tool-id>/`; state (master switch, per-call confirmation, web-access switch, per-tool enable flags, round budget) persists in `<config_dir>/tools.json`. **Import/export as .zip** (zip-slip-guarded, size-capped) — the same exchange story as skills. Imported tools land **disabled** until the user reviews and enables them.
- **Execution** (`toolkit.runner`): arguments are validated/coerced against the manifest schema, then the entry function runs in an isolated `python -I` **subprocess** with a hard timeout and an output cap (restricted-builtins tricks are not a boundary; a subprocess is — per the sandboxing survey; frozen builds fall back to a soft-timeout thread). Tool errors come back as strings the model can read and adapt to.
- **Tool loop** (`cleanwispr.llm.toolloop.run_tool_loop`): model turn → tool calls → confirm → execute → results fed back → repeat, with a hard round budget (then the model must answer without tools). Tool availability is **capability-gated per model** (`/api/show` capabilities contains `"tools"` — qwen3/llama3.1/mistral/gemma4 yes, gemma3/gemma3n no; a one-time notice explains when the selected model can't use tools). Results are fenced and labelled as DATA (spotlighting) so fetched content can't easily steer the model — the real boundaries are the switches below.
- **Safety gates**: master **enable** switch; per-tool enable switches; **confirm-before-run** (per-tool `confirm` flag — Run Python asks every time — plus an optional ask-for-everything mode; the worker blocks on a Qt dialog, timeout = denied); and a separate **web-access master switch, off by default**, gating every tool whose manifest declares `network: true`, with a prominent prompt-injection warning in Settings → Tools (a fetched page can carry hidden instructions that hijack a small local model — the "lethal trifecta" risk).
- **Built-ins** (seeded once, re-seeded only if deleted): **HTTP fetch** (urllib + html.parser page-to-text, network-gated), **Run Python** (executes a model-written snippet in the sandbox, confirm-gated), and **Create tool** (a *native* tool: the model can build new tools on request — manifest + code written into the library, **always created disabled** so model-authored code never runs before the user reviewed and enabled it in Settings → Tools).
- **Authoring knowledge** (`toolkit.authoring`): the full TOOL AUTHORING REFERENCE is injected as a system message whenever Create tool is armed, and a condensed version ships as the built-in **Tool author** skill (seeded for existing installs too), so the how-to is visible and editable where personas are managed.

## 4. Architecture

Single Python process + PySide6 event loop; heavy work (audio capture, inference calls, DB writes) on worker threads (`QThread`/`concurrent.futures`) so the UI and hotkey handling never block. Local inference servers run as managed child processes.

```
cleanwispr/
├── app.py                  # entry point, wires everything together
├── core/
│   ├── controller.py       # central state machine: idle → recording → transcribing → injecting
│   ├── events.py           # Qt signals bus between subsystems
│   └── modes.py            # dictation vs editor session logic
├── audio/
│   ├── capture.py          # sounddevice (PortAudio) 16 kHz mono PCM recorder, device selection
│   └── gate.py             # silence/speech gate (RMS/peak)
├── stt/
│   ├── base.py             # SttEngine interface: start(), stop(), transcribe(pcm, language, prompt)
│   ├── live.py             # live typing (F1): LocalAgreement-2 commits + reconcile + preview loop
│   ├── whisper_cpp.py      # manages bundled whisper-server binary (HTTP, CUDA/Vulkan/CPU fallback)
│   ├── parakeet.py         # sherpa-onnx Python bindings (NVIDIA Parakeet/Nemotron ONNX models)
│   ├── registry.py         # model catalog (ported from openwhispr modelRegistryData.json)
│   └── downloader.py       # model + binary downloads with progress, resume, checksums
├── llm/
│   ├── base.py             # LlmProvider interface: list_models(), model_info(), chat(messages, options)
│   ├── ollama.py           # Ollama REST: /api/tags, /api/show, /api/chat (streaming + function calling)
│   ├── toolloop.py         # tool loop (F5): calls → confirm → execute → results → next turn
│   ├── openai_compat.py    # (later) generic OpenAI-compatible endpoint → LM Studio, llama.cpp, vLLM
│   └── prompts.py          # editor system prompts (edit / generate / whole_note)
├── hotkeys/
│   ├── base.py             # HotkeyBackend interface: register(slot, combo, on_down, on_up)
│   ├── pynput_backend.py   # Windows + Linux/X11 (low-level hooks; true key-down/key-up for hold mode)
│   └── wayland/            # (later) GNOME gsettings+D-Bus, KDE KGlobalAccel, Hyprland hyprctl
├── inject/
│   ├── base.py             # TextInjector interface: inject(text), capture_selection(), live-typing primitives
│   ├── live.py             # LiveTypingSink (F1): commits → keystrokes, focus/modifier/terminal guards
│   ├── windows.py          # clipboard + SendInput Ctrl+V; terminal detection → Ctrl+Shift+V
│   └── linux.py            # clipboard (wl-copy/xclip) + xdotool → wtype → ydotool fallback chain
├── ui/
│   ├── tray.py             # QSystemTrayIcon: status, start/stop, settings, quit
│   ├── overlay.py          # frameless translucent always-on-top pill showing rec/processing state
│   ├── settings/           # settings window (see §6), incl. notes_tab.py (vault manager)
│   ├── notes/              # Notes view (F3): window, editor, vault storage, table ops, slide-mic control
│   └── history.py          # transcription history browser
├── storage/
│   ├── db.py               # SQLite (stdlib sqlite3, WAL) — history
│   ├── settings.py         # pydantic-validated JSON config in platformdirs user-config dir
│   └── paths.py            # cache/config/data dir resolution (platformdirs)
└── resources/              # icons, bundled binary manifests
```

> **Skills (F4)** live in a **separate top-level package `skillkit/`** (sibling to
> `cleanwispr/`): a stdlib-only core (`models`, `library`, `store`, `compose`,
> `voice`) plus an optional PySide6 UI (`skillkit/qt/`). It has **no dependency on
> `cleanwispr`**, so it is portable to other LLM apps; `cleanwispr` depends on it
> through a thin adapter — `llm/prompts.py` (composer), `core/controller.py`
> (voice switch + skill resolution), `ui/settings/skills_tab.py` (embeds the
> manager), `ui/tray.py` (submenu), and `app.py` (store/library/bridge/palette
> wiring). See `skillkit/README.md` for the public API and integration guide.

> **Tools (F5)** follow the same pattern in a **separate top-level package
> `toolkit/`** (sibling to `skillkit/`): stdlib-only `models` / `library` /
> `runner` / `authoring`, plus packaged built-in tools under `toolkit/builtin/`.
> It has no dependency on `cleanwispr`; the app wires it through
> `llm/toolloop.py` (the agent loop), `core/controller.py` (arming + confirm
> bridge), `ui/settings/tools_tab.py` (manager UI), and `app.py` (library +
> seeding + confirm dialog).

### Key design decisions (and what they inherit from OpenWhispr)

| Concern | Decision | Inherited from |
|---|---|---|
| Whisper inference | Bundled `whisper-server` (whisper.cpp) child process, persistent HTTP server on localhost; GPU (CUDA/Vulkan) build with CPU fallback | `whisperServer.js`, `gpuBinaryManager.js` |
| Parakeet inference | `sherpa-onnx` **Python package** (no subprocess needed), INT8 ONNX models | `parakeet.js` (simplified) |
| Audio capture | `sounddevice`, 16 kHz mono int16 — the exact input format the engines want. **No FFmpeg, no WebM.** | replaces MediaRecorder+FFmpeg pipeline |
| Model catalog | Static registry (Python module/JSON) with HF download URLs, sizes, language lists | `modelRegistryData.json` |
| Push-to-hold | `pynput` low-level hooks give key-down/key-up on Windows & X11 | replaces `windows-key-listener.exe` |
| Text injection | Clipboard + simulated paste, terminal-aware (`Ctrl+Shift+V`), clipboard restore | `clipboard.js`, `windows-fast-paste.c` |
| Overlay | Frameless, `Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool`, `WA_TranslucentBackground`, non-activating | `windowConfig.js` MAIN_WINDOW_CONFIG |
| LLM access | HTTP to localhost servers only; provider interface hides the wire protocol | `inferenceProviders/` registry idea |
| Editor prompt | Adapted from OpenWhispr's injection-hardened `fullPrompt` | `src/locales/en/prompts.json` |

### LLM provider modularity

`LlmProvider` is the seam. v1 ships `OllamaProvider`:

- `list_models()` → `GET /api/tags` (auto-discovery of installed models for the settings UI)
- `model_info(name)` → `GET /api/show` (context length, parameter size, quantization — shown in UI)
- `chat(messages, options)` → `POST /api/chat` with `options.num_ctx`, `temperature`, `keep_alive`, streaming
- Configurable base URL (default `http://127.0.0.1:11434`)

**In-app model installation (provider-agnostic capability).** The contract also
exposes an optional install capability so a non-technical user never touches a
terminal: `supports_install`, `catalog()` (a searchable list of
`InstallableModel`s spanning families — Gemma, Qwen, Llama, Mistral, Phi,
DeepSeek… — each carrying `size_gb` + `min_memory_gb` + a `recommended` flag and
a `matches(query)` search helper), `pull(model, progress, cancel)` (streaming
download, cancellable — Ollama uses `POST /api/pull`), and `delete_model()`.
Any model can also be installed by exact name. A shared, provider-neutral
recommender
(`hardware.recommend_from_catalog`) picks a **best-quality** or
**smallest-usable** model for the detected accelerator (VRAM / unified memory /
RAM budgeting, with a CPU cap). Providers without the capability set
`supports_install = False` and the UI falls back to "install it in that tool".

`OpenAICompatProvider` (planned) covers LM Studio, llama.cpp `llama-server`, vLLM, Jan — one implementation, many servers. Provider choice + per-provider settings live in the config schema from day one. Because install/recommendation lives in the contract (not in Ollama), any such provider that advertises a catalog gets the guided install + hardware recommendation UI for free.

### STT engine modularity

`SttEngine` is the seam: `whisper.cpp` and `parakeet` (sherpa-onnx) in v1. Engines own their server/runtime lifecycle (pre-warm on app start, configurable), the registry describes available models per engine, and the downloader fetches them to `~/.cache/cleanwispr/` (`%LOCALAPPDATA%` on Windows).

## 5. Data & Persistence

### History database (SQLite, WAL)

```sql
CREATE TABLE transcriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  kind TEXT NOT NULL CHECK (kind IN ('dictation', 'edit')),
  text TEXT NOT NULL,            -- transcript, or the edit result
  instruction TEXT,              -- editor mode: the spoken instruction
  source_text TEXT,              -- editor mode: the text that was edited
  language TEXT,
  engine TEXT,                   -- e.g. 'whisper:small', 'parakeet:tdt-0.6b-v3'
  llm_model TEXT,                -- editor mode: e.g. 'ollama:qwen2.5:7b'
  duration_ms INTEGER,           -- audio length
  audio_path TEXT                -- NULL unless audio retention is enabled
);
```

- **Audio retention is OFF by default** — recordings are held in memory and discarded after transcription. Settings expose an opt-in toggle (+ retention folder and a purge button).
- **History logging can be disabled entirely** (`history.enabled`, default on): text is still transcribed/edited and pasted, it just is never written to `history.db`.
- History window: reverse-chronological list, full-text filter, copy/delete entries, view dictation vs edit detail.

### Settings (JSON, pydantic-validated)

Location: `platformdirs` user config dir (`%LOCALAPPDATA%/CleanWispr/config.json` on Windows, `~/.config/cleanwispr/config.json` on Linux). Schema (abridged):

```jsonc
{
  "hotkeys": {
    "dictation": { "combo": "ctrl+super", "mode": "toggle" },   // mode: toggle | hold
    "editor":    { "combo": "alt+super", "mode": "toggle" },    // NOT ctrl+alt+<letter>: AltGr clash on EU layouts
    "notes":     { "combo": "f10", "mode": "toggle" }           // opens the Notes window (mode unused)
  },
  "stt": {
    "engine": "whisper",               // whisper | parakeet
    "whisper_model": "small",
    "parakeet_model": "parakeet-tdt-0.6b-v3",
    "language": "auto",
    "custom_dictionary": [],
    "gpu": "auto",                     // auto | cuda | vulkan | cpu
    "models_dir": "",                  // custom model download folder; empty = default cache dir
    "live_typing": true                // stream stable words into the target app while speaking (F1)
  },
  "llm": {
    "provider": "ollama",
    "ollama": { "base_url": "http://127.0.0.1:11434", "model": "", "num_ctx": 8192, "temperature": 0.2, "keep_alive": "10m", "interpret_run_as_pull": true }
  },
  "audio": { "input_device": null, "keep_recordings": false },
  "inject": { "restore_clipboard": true },
  "history": { "enabled": true },      // off: nothing is written to history.db
  "ui": { "overlay_position": "bottom-right", "start_on_login": false, "ui_language": "en", "sounds_enabled": true, "verbose_logging": false },
  "notes": { "vaults": [], "active_vault": "", "last_note": "", "notes_dir": "" }  // notes_dir: legacy single-folder, migrated into vaults on load
}
```

No secrets are stored (no API keys exist in a local-only app), so no keyring/encryption layer is needed.

**Skills store (F4).** The skills library persists **separately** in `<config_dir>/skills.json` — `{ "version", "config": { "enabled", "active_ids", "voice_switching", "accept_threshold", "margin", "max_words" }, "items": [ Skill… ] }` — deliberately *not* inside `config.json`, so the portable `skillkit` package owns its own storage (via a `SkillStore` seam) and a user's skills move/back up as a single file. Same atomic-write + corrupt→`.bak` safety as `config.json`.

**Tools store (F5).** Same pattern: tool folders live under `<config_dir>/tools/<tool-id>/` (`tool.json` + `tool.py`), and the library state persists in `<config_dir>/tools.json` — `{ "version", "config": { "enabled", "confirm_all", "allow_network", "max_rounds" }, "tools": { "<id>": { "enabled" } } }`. The `toolkit` package owns its own storage; a tool travels as a zip of its folder.

## 6. Settings UI (PySide6 window)

Tabs (ordered by how a new user sets things up; the window is resizable down to
small laptop screens and every tab scrolls):

1. **Transcription** — engine picker (whisper/parakeet); card-style model manager (download/delete/use, ACTIVE badge, inline progress, **cancel**); engine-build manager (CPU/CUDA/Vulkan) with GPU backend selector and **GPU auto-detection** that marks the recommended build for the detected accelerator; **model storage location** picker (any folder/disk, default = user cache dir); language dropdown; custom dictionary editor; **live typing** toggle (stream words into the target app while speaking, F1).
2. **Voice Editor (LLM)** — provider selector (Ollama; extensible); **auto-detected list of installed Ollama models** with parameter size/quantization/context info from `/api/show`; **hardware-aware recommendation** (Best-quality / Smallest-usable buttons) and a **searchable model library** (filter across families) installable in-app with progress + cancel (plus the paste-`ollama pull` box for installing anything by exact name); context window (`num_ctx`), temperature, keep-alive; base URL; "Test connection" / "Start Ollama" buttons; prompt preview/override (advanced).
3. **Skills** — reusable LLM roles (F4): master **Enable skills** + **Allow voice switching** toggles; a list with add / duplicate / delete and a **width slider** (for long names); an editor for name, description, persona body, voice triggers, scope (voice editor / Notes / both), per-skill temperature + model override, and enable/activate; a **Test skill** button (runs a sample through the model to preview the persona); and **import/export** skills as JSON to exchange them. Built-in skills (Formal / Concise / Friendly / Poet / **Tables** / **Tool author**) are read-only — duplicate to edit. Backed by the standalone `skillkit` manager widget.
4. **Tools** — LLM capabilities (F5): master **Let the model use tools** + **Ask before every tool call** toggles; a separate **Allow tools that access the internet** switch (off by default) with a prominent prompt-injection/exfiltration warning; a row per installed tool (enable toggle, built-in / 🌐 web / asks-first badges, export, delete); **Import tool (.zip)** (imported tools land disabled until reviewed) and **Open tools folder**.
5. **Hotkeys** — key-capture widget per slot (dictation, editor, notes), activation mode selector per slot (the notes slot just opens the window, so no mode), conflict validation across all slots in priority order (dictation > editor > notes).
6. **Microphone** — input device picker with live level meter; audio retention toggle + folder (clickable path) + purge.
7. **Notes** — vault manager: add/remove vaults, mark the active one, reveal in file manager. Changing vaults live-reloads the open Notes window.
8. **History** — history-logging on/off toggle + the browser from §5.
9. **General** — sounds toggle, start-on-login, overlay position, verbose logging, open settings/log folder, clickable data paths, **Clear app data** (confirmed full factory reset: settings, history, logs, models, binaries — then quit).
10. **About** — version, author, and every third-party project with verified links and licenses (incl. OpenWhispr MIT attribution).

Folder paths shown anywhere in Settings are clickable links that open the
folder in the system file manager after a confirmation prompt.

**First-run setup wizard**: when the app starts with no existing config, a
step-by-step guide (welcome → engine choice + download → language → optional
Ollama setup → hotkey recap) gets a non-technical user to a working install.
The engine step **detects the GPU** and, when a compatible accelerator is found,
offers (pre-checked) to download the matching whisper build (CUDA/Vulkan) next
to the CPU fallback, so users aren't left on slow CPU transcription unknowingly.
The Ollama step is self-contained: it detects whether Ollama is installed and
running (offering **Start Ollama** or an install link), recommends a right-sized
model for the machine, and downloads the chosen one (Best-quality /
Smallest-usable) in-place — no terminal, no manual `ollama pull`. Skippable at
any point; re-runnable from Settings → General.

## 7. Tray & Overlay

- **Tray** (`QSystemTrayIcon`): state-colored icon (idle/recording/processing/error), left-click toggles dictation, context menu: Start dictation, Start editor, **Skills** (submenu: enable + a checkable entry per skill + quick-switch/manage), Open Notes, Settings, History, Quit. App has no taskbar presence; closing the settings window minimizes to tray.
- **Overlay pill**: small frameless translucent always-on-top widget near a screen edge; shows recording (level-reactive), transcribing spinner, and brief result/error flashes; click stops/cancels; draggable; hidden when idle (configurable).

## 8. Platform Notes

| Area | Windows 10/11 | Linux |
|---|---|---|
| Hotkeys (toggle + hold) | `pynput` WH_KEYBOARD_LL hook | X11: `pynput`/XRecord. Wayland: desktop-specific registration (GNOME → gsettings + D-Bus; KDE → KGlobalAccel; Hyprland → `hyprctl bind`), **toggle-only** (Wayland can't deliver key-up), recipes ported from OpenWhispr's `gnomeShortcut.js` / `kdeShortcut.js` / `hyprlandShortcut.js` |
| Paste | `SendInput` Ctrl+V (via pynput/pywin32); detect terminal window class/exe → Ctrl+Shift+V | `xdotool` → `wtype` → `ydotool` fallback chain (port of `clipboard.js` order); both CLIPBOARD and PRIMARY set |
| Clipboard | Qt clipboard | Qt clipboard + `wl-copy`/`xclip` fallback |
| Tray | native | StatusNotifier (Qt handles); document AppIndicator extension for GNOME |
| Packaging | PyInstaller one-dir (windowed) + portable zip + Inno Setup installer (`scripts/build_windows.py`) | PyInstaller one-dir + portable tar.gz + sample .desktop (`scripts/build_linux.py`); AppImage/deb/rpm later. macOS: PyInstaller .app + ditto zip (`scripts/build_macos.py`, experimental). Models/engine binaries are never bundled — always downloaded at runtime |
| Autostart | Run registry key / Startup shortcut | XDG autostart .desktop |

## 9. Performance Requirements

- Hotkey-press → recording-started feedback: **< 100 ms** (mic stream pre-opened or warm-started).
- STT servers pre-warmed at app start (model loaded), so stop-speaking → text-injected is dominated by inference only; whisper.cpp `small` on GPU should land well under ~1.5 s for a 10 s utterance.
- LLM `keep_alive` keeps the Ollama model resident between edits.
- Idle CPU ≈ 0% (event-driven hooks, no polling loops); idle RAM dominated only by loaded STT model (user-controlled via model choice and optional lazy-load setting).

## 10. Milestones

1. **M0 — Skeleton**: repo scaffolding, `pyproject.toml` (uv), config load/save, tray icon, empty settings window, CI (ruff + pytest).
2. **M1 — Dictation MVP (Windows)**: audio capture → whisper.cpp server (CPU) → clipboard paste; fixed hotkey, toggle mode; overlay pill; history writes.
3. **M2 — Hotkeys & modes**: configurable combos, push-to-hold, Escape-cancel, silence gate, key-capture settings UI, conflict validation.
4. **M3 — Voice editor**: selection capture, Ollama provider (discovery + chat + options), hardened edit prompt, replace-selection flow, editor settings tab.
5. **M4 — Engines & models**: Parakeet via sherpa-onnx, model download manager UI, GPU whisper builds (CUDA/Vulkan) with fallback, language + custom dictionary plumbing.
6. **M5 — Linux**: X11 support end-to-end; Wayland (GNOME/KDE/Hyprland) hotkey + paste fallbacks; packaging (installer + AppImage); autostart.

## 11. Repository & Agentic Development Setup

- **Tooling**: plain `venv` + `requirements.txt` / `requirements-dev.txt` / `requirements-build.txt` for env/deps (no uv/poetry/pdm), `ruff` (lint + format), `pytest` (+ `pytest-qt`). Python ≥ 3.11.
- **`CLAUDE.md`** at repo root: architecture map, module responsibilities, interface contracts (`SttEngine`, `LlmProvider`, `HotkeyBackend`, `TextInjector`), how to run/test, platform gotchas (ported wisdom from OpenWhispr's CLAUDE.md), and pointers into `openwhispr-main/` as reference material.
- **Conventions**: every subsystem behind its `base.py` interface; UI never imports engines directly (goes through `core.controller`); all user-facing strings via a light i18n layer from day one; no blocking calls on the Qt main thread.
- **Tests**: unit tests for gate, registry, config schema, prompt building, injection command selection; integration tests with a mocked Ollama/whisper-server; manual test checklist per platform in `docs/TESTING.md`.
- `openwhispr-main/` stays in-tree as read-only reference (MIT, attributed) and is excluded from packaging.

## 12. Attribution

CleanWispr reuses architectural patterns, model registry data, prompt engineering, and platform-integration recipes from [OpenWhispr](https://github.com/OpenWhispr/openwhispr) (MIT License). A copy of the license and attribution ships in the About dialog and `THIRD_PARTY_NOTICES.md`.
