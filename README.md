# CleanWispr

**Version 0.2.2** · Local voice-to-text and voice-driven text editing for Windows 10/11 and Linux (experimental macOS). Python + PySide6. **No cloud, no accounts, no telemetry — audio and text never leave your PC.**

## What it does

**🎙 Dictation** — press a global hotkey in any application, speak, and the transcript is pasted at your cursor. Powered by [whisper.cpp](https://github.com/ggerganov/whisper.cpp) running locally as a warm background server, so transcription starts instantly.

**✏ Voice editor** — select text anywhere, press the editor hotkey, and speak a command: *"make this formal"*, *"remove the second sentence"*, *"translate this to English"*. A local LLM (via [Ollama](https://ollama.com)) applies the edit and the result replaces your selection. With nothing selected, it writes new text from your command.

## Feature overview

| Area | Highlights |
|---|---|
| **Transcription** | Two engines: **whisper.cpp** (CPU / **CUDA** / Vulkan builds, Metal on macOS; 6 model sizes Tiny → Large-v3 + Turbo; 60+ languages; custom dictionary) and **NVIDIA Parakeet** via sherpa-onnx (in-process, extremely fast even on CPU; multilingual v3 with auto language detection). All models downloaded in-app with progress |
| **Voice editor** | Ollama model auto-discovery with parameter/quantization/context info; install models by pasting `ollama pull …` commands (name-only extraction — nothing is executed); auto-starts Ollama if it isn't running; hardened prompts (selection is data, output-only) |
| **Live feedback** | Overlay pill narrates every stage: mic warm-up → recording (level-reactive) → transcribing → model loading (with seconds counter) → writing → pasting; **thinking panel** streams reasoning models' thoughts as markdown, with the exact command + selection that was sent; synthesized audio cues (toggleable) |
| **Hotkeys** | Two global shortcuts (dictation / editor), click-to-capture UI, toggle or push-to-hold per slot, Esc cancels, overlap-conflict validation with clear explanations |
| **History** | Searchable local SQLite log of every dictation and edit (with instruction + original text for edits); entries are editable with an "edited" audit flag; confirmed clear-all; audio recordings NOT kept unless you opt in |
| **Robustness** | Single-instance lock; inference servers die with the app (job object / PDEATHSIG) — no orphan processes; automatic engine fallback (CUDA → CPU); empty-mic and dead-mic guards with actionable messages |
| **UI** | Material Design dark theme; every setting explained in plain language with tooltips; rotating file log with an opt-in verbose mode |

## Quick start (no Python knowledge needed)

The launcher scripts do everything automatically: on first run they create a
private Python environment, install all dependencies, and start the app;
afterwards they just start it. The only prerequisite is
[Python 3.11+](https://www.python.org/downloads/) itself.

| OS | Do this |
|---|---|
| **Windows** | Install Python (**tick "Add python.exe to PATH"**), then double-click **`start_windows.bat`** |
| **Linux** | `sudo apt install python3-venv libportaudio2 xdotool xclip` (Wayland: + `wl-clipboard wtype`), then run **`./start_linux.sh`** |
| **macOS** | `brew install portaudio`, then double-click **`start_macos.command`** (first time: right-click → Open) |

The app appears as a microphone icon in the system tray, and on first start a
**guided setup** walks you through downloading a transcription engine + model
(~220 MB for the recommended Base model), picking your language, and optionally
setting up [Ollama](https://ollama.com) for the voice editor (paste
`ollama pull qwen3:8b` into Settings → Voice Editor to fetch an editing model).
On Windows you can then enable **Settings → General → Start CleanWispr when
Windows starts** and forget about the script entirely.

Platform notes: on Linux, global hotkeys need X11 (or XWayland) — on pure
Wayland desktops use the tray menu until native shortcuts land. On macOS
(experimental, untested), grant **Accessibility** and **Input Monitoring**
permissions when prompted — required for pasting and global hotkeys.

<details>
<summary><b>Manual setup</b> (if you prefer doing it yourself)</summary>

```powershell
# Windows (PowerShell)
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

```bash
# Linux / macOS
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

</details>

## Building standalone executables

CleanWispr ships as a [PyInstaller](https://pyinstaller.org) **onedir** bundle:
a folder containing the executable plus its libraries (deliberately *not* a
single-file exe — onedir starts faster and triggers far fewer antivirus false
positives). The build is windowed, so no console window appears.

Two rules apply to every platform:

1. **Build on the target OS.** PyInstaller cannot cross-compile — the Windows
   exe must be built on Windows, the Linux binary on Linux, the macOS app on
   macOS.
2. **Models and engine binaries are NOT bundled** — see
   [What's inside the bundle](#whats-inside-the-bundle-and-what-isnt) below.

Like the start scripts, there is a **one-click build script per OS** in
`scripts/` — each creates the Python environment with the build tooling if
needed, then builds. No manual setup required.

### Windows

Double-click **`scripts\build_windows.bat`** (or run
`python scripts/build_windows.py` from an activated venv with
`requirements-build.txt` installed).

Produces `dist/CleanWispr/CleanWispr.exe` and `dist/CleanWispr-portable-win64.zip`
(standalone — no Python required on the target machine). If
[Inno Setup](https://jrsoftware.org/isinfo.php) (`iscc`) is on PATH, a
`CleanWispr-setup-win64.exe` installer is compiled as well.

### Linux

Install the system dependencies first (same as running from source), then run
the build script:

```bash
sudo apt install python3-venv portaudio19-dev xdotool xclip   # Wayland: + wl-clipboard wtype
./scripts/build_linux.sh
```

Produces `dist/CleanWispr/CleanWispr` and
`dist/CleanWispr-portable-linux-x64.tar.gz`. The bundle includes a sample
`CleanWispr.desktop` launcher — copy it to `~/.local/share/applications/` and
set its `Exec=` line to the full path of the extracted binary.

Compatibility note: the binary links against the glibc of the **build**
machine, so build on the oldest distro you want to support (e.g. build on
Ubuntu 22.04 to also cover 24.04, not the other way around).

### macOS

```bash
brew install portaudio
```

…then double-click **`scripts/build_macos.command`** (first time:
right-click → Open).

Produces `dist/CleanWispr.app` and `dist/CleanWispr-macos.zip` (zipped with
`ditto` so the bundle structure survives). The app is built for the CPU of the
build machine (Apple Silicon or Intel). Because it is unsigned, first launch
requires right-click → **Open** (or `xattr -dr com.apple.quarantine
CleanWispr.app`), then grant **Microphone**, **Accessibility**, and **Input
Monitoring** permissions when prompted.

### What's inside the bundle (and what isn't)

The bundle contains Python, Qt, and the app code — including the sherpa-onnx
runtime used by the Parakeet engine. Everything else is downloaded **at
runtime** into the user's data folders, exactly like when running from source:

- whisper.cpp engine builds (CPU / **CUDA** / Vulkan) — downloaded from
  Settings → Transcription and spawned as a separate process. GPU support is
  unaffected by PyInstaller: the CUDA build ships its own runtime DLLs.
- Whisper and Parakeet **models** — downloaded in-app to the model storage
  folder (configurable in Settings → Transcription).
- **Ollama** — a separate application the user installs themselves.

This keeps the bundle small and means packaging never breaks GPU or model
downloads. The one packaged native dependency is sherpa-onnx; if Parakeet ever
fails *only* in a bundled build, add `--collect-all sherpa_onnx` to the
PyInstaller arguments in the build script.

### Antivirus false positives (Windows)

PyInstaller executables are sometimes flagged by antivirus software as
malware. This is a well-known **false positive**: PyInstaller's bootloader
(the stub that unpacks and starts Python) is also used by actual malware, so
heuristic scanners distrust anything built with it. If it happens:

- **Prefer the onedir build** (what `build_windows.py` already produces) —
  single-file `--onefile` exes self-extract at startup, which looks far more
  suspicious to scanners.
- **Don't compress with UPX** — the build scripts don't, and it's the single
  biggest false-positive trigger. Keep it that way.
- **Code-sign the exe** (`signtool` with an Authenticode certificate). Signed
  binaries build SmartScreen/Defender reputation and largely stop the flags —
  this is the only real long-term fix for distribution.
- **Report the false positive** to the vendor (for Defender:
  [Microsoft's submission portal](https://www.microsoft.com/en-us/wdsi/filesubmission)) —
  usually whitelisted within days.
- As a local workaround, users can add an exclusion for the install folder, or
  simply build from source themselves — a locally built exe with the exact
  same code often isn't flagged, since detection keys on the specific binary
  hash.

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
(`history.db`), logs, downloaded models and engine binaries. Models can
optionally live anywhere — e.g. on another disk — via **Settings →
Transcription → Model storage location**. **Settings → General → Clear app
data** deletes everything CleanWispr stored on your PC (a full factory reset /
pre-uninstall cleanup). The AI model receives only your spoken command and the
selected text — never your history.

## Changelog

### 0.2.2

**New**

- **Guided first-run setup**: on a fresh install (no app data yet) a
  step-by-step wizard walks you through choosing and downloading a
  transcription engine + model, picking your language, and setting up Ollama
  for the voice editor — re-runnable any time from Settings → General →
  "Run setup guide"
- **One-click launchers**: `start_windows.bat`, `start_linux.sh`, and
  `start_macos.command` (repo root) create the Python environment, install
  dependencies (re-installing automatically when `requirements.txt` changes),
  and start the app — no Python knowledge needed; Windows launches windowless
  via `pythonw`
- **One-click build scripts**: `scripts/build_windows.bat`,
  `scripts/build_linux.sh`, and `scripts/build_macos.command` bootstrap the
  environment with build tooling and produce the standalone executable —
  same zero-setup experience as the launchers

- **Redesigned model manager** (Settings → Transcription): engine builds and
  models are now clean card-style rows with an accent-highlighted ACTIVE
  badge, compact Use / Download / Delete actions, a slim inline progress bar,
  and a "Recommended" tag — replacing the old grid of oversized buttons; the
  tab scrolls smoothly
- **Custom model storage location**: point model downloads at any folder
  (e.g. another disk) in Settings → Transcription; the default stays in the
  user cache dir
- **Clear app data**: one button in Settings → General deletes settings,
  history, logs, models, and engine binaries — like an uninstall
- **About tab**: version, author, and all open-source projects CleanWispr is
  built on, with links and licenses
- **History on/off switch**: new toggle at the top of the History tab —
  when off, dictations and edits are still pasted but never written to
  `history.db`
- **Redesigned history browser**: the entry table is replaced with card-style
  rows — kind badges (DICTATION / EDIT), an EDITED marker, and a wrapped
  two-line text preview that no longer truncates after a few characters
- **Modern toggle switches**: every checkbox replaced with an animated
  sliding switch matching the app theme
- **Restyled buttons app-wide**: rounded, theme-consistent buttons replace
  qt-material's pink defaults
- **Keep-model-loaded picker**: the free-text Ollama keep-alive field is now
  a number + unit dropdown (seconds / minutes / hours / forever) — no more
  typos in the duration format
- **Clearer language & dictionary settings**: the Transcription tab now
  explains what the language choice and custom dictionary mean per engine
  (Whisper honors both; Parakeet auto-detects and has no dictionary), with a
  live notice when Parakeet is active and those settings don't apply
- **Clickable paths**: folder paths shown in Settings (recordings folder,
  config, data, model storage) open in your file manager after confirmation
- **Resizable settings window**: shrinks to small laptop screens; all tabs
  gained scroll support
- **Linux and macOS build scripts** (`scripts/build_linux.py`,
  `scripts/build_macos.py`) plus full build documentation, including the
  antivirus false-positive workarounds for Windows

**Fixed**

- Whisper model rows showed **ACTIVE** even when the Parakeet engine was
  selected — both engines appeared active at once; the badge now tracks the
  actually selected engine
- Selecting a Whisper model with "Use" while Parakeet was active silently did
  nothing; it now switches the engine back to Whisper (mirroring how Parakeet
  selection already worked)

### 0.2.0

- **NVIDIA Parakeet engine** (sherpa-onnx, in-process): multilingual 0.6B v3
  with automatic language detection and a small fast English 110M model —
  excellent speed even without a GPU; engine selector in Settings
- **Linux support** (X11/WSLg) and experimental macOS: platform injectors
  with tool fallback chains, per-platform engine builds (Metal on macOS),
  XDG / LaunchAgent autostart
- **GPU transcription**: CUDA and Vulkan whisper-server builds with automatic
  fallback to CPU
- **Voice editor upgrades**: live Ollama status in the overlay (model loading
  with a seconds counter), streaming **thinking panel** with markdown rendering
  and the exact command + selection sent to the model; install Ollama models by
  pasting `ollama pull` commands; Ollama auto-start when not running
- **UI overhaul**: Material Design dark theme (qt-material), logical tab order,
  plain-language explanations and tooltips everywhere, editable history with an
  edited-flag and confirmed clear-all, mic level meter, overlay positioning,
  synthesized sound cues, verbose-logging toggle, open-settings/logs buttons
- **Robustness**: single-instance lock, orphan-proof child processes (job
  object / PDEATHSIG), hotkey overlap validation, Bluetooth-mic warm-up
  handling, empty-recording guards, inference retry, clipboard-history-clean
  transient writes
- **Packaging**: PyInstaller Windows bundle + portable zip, Inno Setup
  installer script, beginner-friendly `main.py` launcher

### 0.1.0

- Initial release: local whisper.cpp dictation with global hotkeys
  (toggle / push-to-hold), Ollama-powered voice editor on selected text,
  SQLite history, tray + overlay UI

## Attribution

CleanWispr reuses architectural patterns, model registry data, prompt
engineering, and platform-integration recipes from
[OpenWhispr](https://github.com/OpenWhispr/openwhispr) (MIT License). Speech
recognition by [whisper.cpp](https://github.com/ggml-org/whisper.cpp);
local LLM serving by [Ollama](https://ollama.com); UI theme by
[qt-material](https://github.com/UN-GCPDS/qt-material).
