#!/usr/bin/env bash
# CleanWispr one-click build (double-click in Finder): sets up the Python
# environment (with build tooling) if needed, then produces CleanWispr.app
# in dist/.
set -e
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11+ is required. Install it from https://www.python.org/downloads/"
    echo "or with Homebrew:  brew install python"
    open "https://www.python.org/downloads/"
    exit 1
fi

VENV=venv
[ -x .venv/bin/python ] && VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
    echo "First-time setup: creating the Python environment..."
    python3 -m venv "$VENV"
fi

# build tooling (includes runtime deps + PyInstaller); reinstall on change
if ! cmp -s requirements-build.txt "$VENV/.requirements-build.stamp"; then
    echo "Installing build dependencies — this can take a few minutes..."
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
    "$VENV/bin/python" -m pip install -r requirements-build.txt
    cp requirements-build.txt "$VENV/.requirements-build.stamp"
fi

echo "Building — this takes a few minutes..."
"$VENV/bin/python" scripts/build_macos.py
echo
echo "Done. Your app is in dist/CleanWispr.app (zip: dist/CleanWispr-macos.zip)"
