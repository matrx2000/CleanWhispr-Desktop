#!/usr/bin/env bash
# CleanWispr one-click launcher: creates the Python environment on first run,
# installs/updates dependencies when needed, then starts the app.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11+ is required. Install it with your package manager, e.g.:"
    echo "  sudo apt install python3 python3-venv"
    exit 1
fi

# reuse an existing environment (either name), create venv/ otherwise
VENV=venv
[ -x .venv/bin/python ] && VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
    echo "First-time setup: creating the Python environment..."
    python3 -m venv "$VENV" || {
        echo "Could not create the environment. On Debian/Ubuntu run:"
        echo "  sudo apt install python3-venv"
        exit 1
    }
fi

# install dependencies only when requirements.txt changed since last install
if ! cmp -s requirements.txt "$VENV/.requirements.stamp"; then
    echo "Installing dependencies — this can take a few minutes on first run..."
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
    "$VENV/bin/python" -m pip install -r requirements.txt
    cp requirements.txt "$VENV/.requirements.stamp"
fi

# PortAudio is a system library on Linux (not bundled in the Python wheel)
if ! "$VENV/bin/python" -c "import sounddevice" >/dev/null 2>&1; then
    echo "Missing system libraries. On Debian/Ubuntu run:"
    echo "  sudo apt install libportaudio2 xdotool xclip   # Wayland: + wl-clipboard wtype"
    echo "then start CleanWispr again."
    exit 1
fi

exec "$VENV/bin/python" main.py
