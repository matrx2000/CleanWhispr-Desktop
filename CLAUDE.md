# CleanWispr — Developer & AI Agent Guide

Local voice-to-text + voice-driven text editing. Python 3.11+ / PySide6. Windows 10/11 and Linux. No cloud, no accounts, no telemetry. Full specification: **[SPEC.md](SPEC.md)** — read it before large changes; it is the source of truth for scope and architecture.

## Commands

```powershell
.venv\Scripts\Activate.ps1        # Linux: source .venv/bin/activate
python -m cleanwispr              # run the app
pytest                            # run tests (pytest-qt for Qt pieces)
ruff check .                      # lint (also: ruff format)
pip install -r requirements-dev.txt   # after dependency changes
python scripts/build_windows.py       # PyInstaller bundle + portable zip (+ installer if iscc on PATH)
```

Dependencies are managed with **plain venv + requirements.txt** (runtime) and requirements-dev.txt (tooling). Do NOT introduce uv/poetry/pdm. When adding a dependency, update requirements.txt with a comment saying what it's for.

## Architecture

Single Qt process. `core.controller.Controller` is the state machine (`idle → recording → transcribing → [editing] → injecting`) and the ONLY component that touches every subsystem. UI observes it via Qt signals. Heavy work (audio, inference HTTP calls, DB) runs on worker threads — **never block the Qt main thread**; hotkey callbacks arrive on backend threads and must be marshalled to Qt via signals.

```
cleanwispr/
├── app.py              # entry point/wiring
├── core/controller.py  # state machine (AppState, SessionKind)
├── audio/              # sounddevice capture (16 kHz mono int16) + silence gate  [M1]
├── stt/base.py         # SttEngine contract → whisper_cpp.py [M1], parakeet.py [M4]
├── llm/base.py         # LlmProvider contract → ollama.py [M3], openai_compat.py [later]
├── hotkeys/base.py     # HotkeyBackend contract → pynput_backend.py [M2], wayland/ [M5]
├── inject/base.py      # TextInjector contract → windows.py [M1], linux.py [M5]
├── ui/                 # tray.py, icons.py, settings/window.py, overlay [M1], history [M1]
└── storage/            # settings.py (pydantic→JSON), db.py (sqlite WAL), paths.py
```

### The four contracts (interface seams)

New backends implement these ABCs; nothing else in the app may import a concrete backend directly (only `app.py` wiring and factories may):

- **`stt.base.SttEngine`** — `start(model_id)` pre-warms (server spawn/model load), `transcribe(pcm, language, initial_prompt)` blocking on worker thread. Audio is always 16 kHz mono int16 numpy.
- **`llm.base.LlmProvider`** — `is_available()`, `list_models()`, `model_info()`, `chat(messages, options) -> Iterator[str]` (streaming). Raise `LlmProviderError` with user-presentable messages.
- **`hotkeys.base.HotkeyBackend`** — `register(slot, combo, on_press, on_release)`. Slots: `"dictation"`, `"editor"`. Combo format: lowercase `+`-joined (`"ctrl+super"`, `"f8"`). Backends without key-release events (Wayland) set `supports_hold = False`.
- **`inject.base.TextInjector`** — `inject(text)` = clipboard write + simulated paste (Ctrl+Shift+V in terminals); `capture_selection()` = simulated Ctrl+C with clipboard restore.

### Settings & data

- Config: pydantic `Settings` → JSON at platformdirs config dir (`%LOCALAPPDATA%/CleanWispr/config.json` on Windows, `~/.config/cleanwispr/` on Linux). Atomic writes; corrupt files back up to `.bak` and fall back to defaults; unknown keys ignored. **Any new setting goes into the schema in `storage/settings.py` + a UI control in the matching settings tab.**
- History: sqlite `history.db` (WAL) in the data dir — `transcriptions` table, kinds `dictation` | `edit`. Audio retention is OFF by default (`audio.keep_recordings`); recordings stay in memory and are discarded unless the user opts in.
- Model/binary cache: platformdirs cache dir under `models/<engine>/`.

## The reference project: openwhispr-main/

`openwhispr-main/` is the MIT-licensed Electron app CleanWispr is modeled on. It is **read-only reference material** — excluded from git and packaging; never modify it, never import from it. Its root `CLAUDE.md` is a detailed technical reference. Mine it for:

| Need | Where in openwhispr-main/ |
|---|---|
| whisper-server spawn args, GPU→CPU fallback | `src/helpers/whisperServer.js`, `gpuBinaryManager.js` |
| Parakeet/sherpa-onnx model registry, URLs | `src/models/modelRegistryData.json`, `src/helpers/parakeet.js` |
| Model download URLs (whisper GGML, HF) | `src/models/modelRegistryData.json` |
| Paste fallback chains (esp. Linux), terminal detection | `src/helpers/clipboard.js`, `resources/windows-fast-paste.c` |
| Wayland hotkeys (GNOME/KDE/Hyprland D-Bus recipes) | `src/helpers/gnomeShortcut.js`, `kdeShortcut.js`, `hyprlandShortcut.js` |
| Overlay window flags/positioning | `src/helpers/windowConfig.js` |
| Silence gate thresholds | `src/helpers/localSpeechGate.js` |
| Editor LLM prompt (injection-hardened) | `src/locales/en/prompts.json` (`fullPrompt`) |
| Whisper language list (58 langs) | `src/utils/languages.ts` |

## Conventions

- Type hints everywhere; `from __future__ import annotations` in modules using modern syntax.
- Qt naming stays Qt-style in overrides (`closeEvent`); everything else is snake_case.
- User-facing strings: keep them in UI modules for now; a light i18n layer is planned — don't scatter strings through core/.
- Errors that reach the user must be actionable ("Ollama is not running at …"), surfaced via tray notification or overlay flash — never silent, never a stack trace.
- Every subsystem grows behind its `base.py` contract. If a change requires the controller to know a backend's concrete type, the design is wrong.
- Tests: pure-logic tests plain pytest; anything touching Qt uses `pytest-qt` (`qtbot`). Mock localhost servers (Ollama/whisper-server) with httpx transports or a local test server — tests must pass with no Ollama installed.
- Subprocess rule (from OpenWhispr's hard-won experience): every spawned inference server must be tracked and reliably killed on quit — no orphan processes.

## Milestones

M0 scaffolding (done) → M1 dictation MVP Windows (audio→whisper→paste) → M2 hotkeys/modes → M3 voice editor via Ollama → M4 Parakeet + model manager UI + GPU → M5 Linux + packaging. Details in SPEC.md §10.
