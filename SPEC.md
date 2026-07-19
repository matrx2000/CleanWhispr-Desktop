# CleanWispr — Project Specification

A lightweight, fully local voice-to-text and voice-driven text-editing desktop app for Windows 10/11 and Linux, written in **Python + PySide6**. Inspired by (and reusing ideas/assets from) the MIT-licensed [OpenWhispr](openwhispr-main/) project, with all cloud/account/subscription functionality removed.

---

## 1. Goals

- **Dictation as a primary PC input method**: press a global hotkey anywhere, speak, and the transcribed text is typed/pasted at the cursor of the active application. Performance must match OpenWhispr (persistent local inference servers, no per-utterance model load).
- **Voice editor**: a second, separate hotkey records a spoken *instruction*; a locally running LLM (Ollama first) applies that instruction to the selected text (add/remove/rewrite) and replaces it in place.
- **100% local**: no accounts, no cloud APIs, no telemetry, no network calls except model downloads (HuggingFace/GitHub) and localhost inference servers.
- **Modular by design**: STT engines and LLM providers sit behind small interfaces so new backends can be added without touching the app core.
- **Cross-platform**: Windows 10/11 first-class; Linux (X11 first, Wayland desktops incrementally).

## 2. Non-Goals (explicitly out of scope)

- Cloud transcription or cloud LLM providers, auth, accounts, usage quotas, referral/upgrade UI.
- Notes system, meeting detection/transcription, speaker diarization, calendar integration, semantic/vector search, chat agent overlay.
- Automatic "AI cleanup" of dictated text. Dictation output is the **raw transcript**. (LLM editing happens only when explicitly triggered via the editor hotkey.)
- macOS as a first-class target (experimental support exists — injector, Metal engine build, `scripts/build_macos.py` — but it is untested and gets no dedicated effort).
- JavaScript/Electron anything.

## 3. The Two Core Features

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

### F2 — Voice Editor (voice instruction → LLM edit of selected text)

Flow: user selects text in any app → editor hotkey → speaks an instruction ("delete the second sentence", "add a bullet about X", "make this formal") → instruction is transcribed by the STT engine → selected text is captured (simulated `Ctrl+C`, clipboard restored after) → both are sent to the local LLM → the edited text replaces the selection (paste).

- Same toggle / push-to-hold activation modes and configurable hotkey as dictation.
- **Prompting**: a hardened system prompt (adapted from OpenWhispr's `fullPrompt` in `src/locales/en/prompts.json` — instruction-following, output-only-the-result, prompt-injection resistant). The LLM must return *only* the edited text.
- If no text is selected, the instruction is treated as a generation request ("write a short apology email") and the result is inserted at the cursor.
- Streaming responses are consumed but injected once complete (v1); live preview is a later enhancement.

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
│   ├── whisper_cpp.py      # manages bundled whisper-server binary (HTTP, CUDA/Vulkan/CPU fallback)
│   ├── parakeet.py         # sherpa-onnx Python bindings (NVIDIA Parakeet/Nemotron ONNX models)
│   ├── registry.py         # model catalog (ported from openwhispr modelRegistryData.json)
│   └── downloader.py       # model + binary downloads with progress, resume, checksums
├── llm/
│   ├── base.py             # LlmProvider interface: list_models(), model_info(), chat(messages, options)
│   ├── ollama.py           # Ollama REST: /api/tags, /api/show, /api/chat (streaming)
│   ├── openai_compat.py    # (later) generic OpenAI-compatible endpoint → LM Studio, llama.cpp, vLLM
│   └── prompts.py          # editor system prompts
├── hotkeys/
│   ├── base.py             # HotkeyBackend interface: register(slot, combo, on_down, on_up)
│   ├── pynput_backend.py   # Windows + Linux/X11 (low-level hooks; true key-down/key-up for hold mode)
│   └── wayland/            # (later) GNOME gsettings+D-Bus, KDE KGlobalAccel, Hyprland hyprctl
├── inject/
│   ├── base.py             # TextInjector interface: inject(text), capture_selection()
│   ├── windows.py          # clipboard + SendInput Ctrl+V; terminal detection → Ctrl+Shift+V
│   └── linux.py            # clipboard (wl-copy/xclip) + xdotool → wtype → ydotool fallback chain
├── ui/
│   ├── tray.py             # QSystemTrayIcon: status, start/stop, settings, quit
│   ├── overlay.py          # frameless translucent always-on-top pill showing rec/processing state
│   ├── settings/           # settings window (see §6)
│   └── history.py          # transcription history browser
├── storage/
│   ├── db.py               # SQLite (stdlib sqlite3, WAL) — history
│   ├── settings.py         # pydantic-validated JSON config in platformdirs user-config dir
│   └── paths.py            # cache/config/data dir resolution (platformdirs)
└── resources/              # icons, bundled binary manifests
```

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

`OpenAICompatProvider` (planned) covers LM Studio, llama.cpp `llama-server`, vLLM, Jan — one implementation, many servers. Provider choice + per-provider settings live in the config schema from day one.

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
    "editor":    { "combo": "f9", "mode": "toggle" }            // NOT ctrl+alt+<letter>: AltGr clash on EU layouts
  },
  "stt": {
    "engine": "whisper",               // whisper | parakeet
    "whisper_model": "small",
    "parakeet_model": "parakeet-tdt-0.6b-v3",
    "language": "auto",
    "custom_dictionary": [],
    "gpu": "auto",                     // auto | cuda | vulkan | cpu
    "models_dir": ""                   // custom model download folder; empty = default cache dir
  },
  "llm": {
    "provider": "ollama",
    "ollama": { "base_url": "http://127.0.0.1:11434", "model": "", "num_ctx": 8192, "temperature": 0.2, "keep_alive": "10m", "interpret_run_as_pull": true }
  },
  "audio": { "input_device": null, "keep_recordings": false },
  "inject": { "restore_clipboard": true },
  "history": { "enabled": true },      // off: nothing is written to history.db
  "ui": { "overlay_position": "bottom-right", "start_on_login": false, "ui_language": "en", "sounds_enabled": true, "verbose_logging": false }
}
```

No secrets are stored (no API keys exist in a local-only app), so no keyring/encryption layer is needed.

## 6. Settings UI (PySide6 window)

Tabs (ordered by how a new user sets things up; the window is resizable down to
small laptop screens and every tab scrolls):

1. **Transcription** — engine picker (whisper/parakeet); card-style model manager (download/delete/use, ACTIVE badge, inline progress); engine-build manager (CPU/CUDA/Vulkan) with GPU backend selector; **model storage location** picker (any folder/disk, default = user cache dir); language dropdown; custom dictionary editor.
2. **Voice Editor (LLM)** — provider selector (Ollama; extensible); **auto-detected list of installed Ollama models** with parameter size/quantization/context info from `/api/show`; context window (`num_ctx`), temperature, keep-alive; base URL; "Test connection" button; prompt preview/override (advanced).
3. **Hotkeys** — key-capture widget per slot (dictation, editor), activation mode selector per slot, conflict validation between slots.
4. **Microphone** — input device picker with live level meter; audio retention toggle + folder (clickable path) + purge.
5. **History** — history-logging on/off toggle + the browser from §5.
6. **General** — sounds toggle, start-on-login, overlay position, verbose logging, open settings/log folder, clickable data paths, **Clear app data** (confirmed full factory reset: settings, history, logs, models, binaries — then quit).
7. **About** — version, author, and every third-party project with verified links and licenses (incl. OpenWhispr MIT attribution).

Folder paths shown anywhere in Settings are clickable links that open the
folder in the system file manager after a confirmation prompt.

**First-run setup wizard**: when the app starts with no existing config, a
step-by-step guide (welcome → engine choice + download → language → optional
Ollama setup → hotkey recap) gets a non-technical user to a working install.
Skippable at any point; re-runnable from Settings → General.

## 7. Tray & Overlay

- **Tray** (`QSystemTrayIcon`): state-colored icon (idle/recording/processing/error), left-click toggles dictation, context menu: Start dictation, Start editor, Settings, History, Quit. App has no taskbar presence; closing the settings window minimizes to tray.
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
