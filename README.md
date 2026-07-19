# CleanWispr

**Version 0.1.0** · Local voice-to-text and voice-driven text editing for Windows 10/11 and Linux (experimental macOS). Python + PySide6. **No cloud, no accounts, no telemetry — audio and text never leave your PC.**

## What it does

**🎙 Dictation** — press a global hotkey in any application, speak, and the transcript is pasted at your cursor. Powered by [whisper.cpp](https://github.com/ggerganov/whisper.cpp) running locally as a warm background server, so transcription starts instantly.

**✏ Voice editor** — select text anywhere, press the editor hotkey, and speak a command: *"make this formal"*, *"remove the second sentence"*, *"translate this to English"*. A local LLM (via [Ollama](https://ollama.com)) applies the edit and the result replaces your selection. With nothing selected, it writes new text from your command.

## Feature overview

| Area | Highlights |
|---|---|
| **Transcription** | whisper.cpp engine with CPU / **CUDA** / Vulkan builds (Metal on macOS); 6 model sizes (Tiny → Large-v3 + Turbo) downloaded in-app with progress; 60+ languages incl. auto-detect; custom dictionary to bias names/jargon |
| **Voice editor** | Ollama model auto-discovery with parameter/quantization/context info; install models by pasting `ollama pull …` commands (name-only extraction — nothing is executed); auto-starts Ollama if it isn't running; hardened prompts (selection is data, output-only) |
| **Live feedback** | Overlay pill narrates every stage: mic warm-up → recording (level-reactive) → transcribing → model loading (with seconds counter) → writing → pasting; **thinking panel** streams reasoning models' thoughts as markdown, with the exact command + selection that was sent; synthesized audio cues (toggleable) |
| **Hotkeys** | Two global shortcuts (dictation / editor), click-to-capture UI, toggle or push-to-hold per slot, Esc cancels, overlap-conflict validation with clear explanations |
| **History** | Searchable local SQLite log of every dictation and edit (with instruction + original text for edits); entries are editable with an "edited" audit flag; confirmed clear-all; audio recordings NOT kept unless you opt in |
| **Robustness** | Single-instance lock; inference servers die with the app (job object / PDEATHSIG) — no orphan processes; automatic engine fallback (CUDA → CPU); empty-mic and dead-mic guards with actionable messages |
| **UI** | Material Design dark theme; every setting explained in plain language with tooltips; rotating file log with an opt-in verbose mode |

## Running from source (Windows)

1. Install [Python 3.11+](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"** in the installer.
2. Open PowerShell in this folder and run, one line at a time:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

The app appears as a microphone icon in the system tray. First-time setup lives
in Settings → Transcription (download the engine and a model, ~220 MB for the
recommended Base model). For the voice editor, install [Ollama](https://ollama.com)
and pull a model (e.g. paste `ollama pull qwen3:8b` into Settings → Voice Editor).

Afterwards, starting the app is just:

```powershell
.venv\Scripts\Activate.ps1
python main.py
```

…or enable **Settings → General → Start CleanWispr when Windows starts**.

## Running on Linux (experimental — X11/WSLg recommended)

```bash
sudo apt install python3-venv portaudio19-dev xdotool xclip   # Wayland: + wl-clipboard wtype
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Notes: global hotkeys need X11 (or XWayland); on pure Wayland desktops use the
tray menu until native shortcuts land. GPU transcription uses the CUDA or
Vulkan engine build (Settings → Transcription).

## Running on macOS (experimental, untested)

Same as Linux (`brew install portaudio`), then grant **Accessibility** and
**Input Monitoring** permissions to your terminal/Python when prompted —
required for pasting and global hotkeys. The single engine build includes
Metal GPU acceleration.

## Building the Windows app

```powershell
pip install -r requirements-build.txt
python scripts/build_windows.py
```

Produces `dist/CleanWispr/CleanWispr.exe` and `dist/CleanWispr-portable-win64.zip`
(standalone — no Python required on the target machine). If
[Inno Setup](https://jrsoftware.org/isinfo.php) (`iscc`) is on PATH, a
`CleanWispr-setup-win64.exe` installer is compiled as well.

## Development setup

```powershell
pip install -r requirements-dev.txt   # adds pytest, ruff
pip install -e .                      # optional: enables `python -m cleanwispr`
ruff check .
pytest
```

See [CLAUDE.md](CLAUDE.md) for architecture and contribution conventions, and
[SPEC.md](SPEC.md) for the original project specification.

## Where your data lives

Everything is stored locally under your user profile
(`%LOCALAPPDATA%\CleanWispr` on Windows, `~/.local/share/cleanwispr` +
`~/.cache/cleanwispr` on Linux): settings (`config.json`), history
(`history.db`), logs, downloaded models and engine binaries. Deleting that
folder is a full factory reset. The AI model receives only your spoken
command and the selected text — never your history.

## Attribution

CleanWispr reuses architectural patterns, model registry data, prompt
engineering, and platform-integration recipes from
[OpenWhispr](https://github.com/OpenWhispr/openwhispr) (MIT License). Speech
recognition by [whisper.cpp](https://github.com/ggerganov/whisper.cpp);
local LLM serving by [Ollama](https://ollama.com); UI theme by
[qt-material](https://github.com/UN-GCPDS/qt-material).
