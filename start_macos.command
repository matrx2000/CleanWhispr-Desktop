#!/usr/bin/env bash
# CleanWispr one-click launcher (double-click in Finder): creates the Python
# environment on first run, installs dependencies when needed, starts the app.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11+ is required. Install it from https://www.python.org/downloads/"
    echo "or with Homebrew:  brew install python"
    open "https://www.python.org/downloads/"
    exit 1
fi

# reuse an existing environment (either name), create venv/ otherwise
VENV=venv
[ -x .venv/bin/python ] && VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
    echo "First-time setup: creating the Python environment..."
    python3 -m venv "$VENV"
fi

# install dependencies only when requirements.txt changed since last install
if ! cmp -s requirements.txt "$VENV/.requirements.stamp"; then
    echo "Installing dependencies — this can take a few minutes on first run..."
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
    "$VENV/bin/python" -m pip install -r requirements.txt
    cp requirements.txt "$VENV/.requirements.stamp"
fi

# PortAudio comes from Homebrew on macOS
if ! "$VENV/bin/python" -c "import sounddevice" >/dev/null 2>&1; then
    echo "Missing the PortAudio system library. Install it with:"
    echo "  brew install portaudio"
    echo "then start CleanWispr again."
    exit 1
fi

exec "$VENV/bin/python" main.py
