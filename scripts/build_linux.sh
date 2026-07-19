#!/usr/bin/env bash
# CleanWispr one-click build: sets up the Python environment (with build
# tooling) if needed, then produces the standalone Linux app in dist/.
set -e
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11+ is required. Install it with your package manager, e.g.:"
    echo "  sudo apt install python3 python3-venv"
    exit 1
fi

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

# build tooling (includes runtime deps + PyInstaller); reinstall on change
if ! cmp -s requirements-build.txt "$VENV/.requirements-build.stamp"; then
    echo "Installing build dependencies — this can take a few minutes..."
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
    "$VENV/bin/python" -m pip install -r requirements-build.txt
    cp requirements-build.txt "$VENV/.requirements-build.stamp"
fi

echo "Building — this takes a few minutes..."
"$VENV/bin/python" scripts/build_linux.py
echo
echo "Done. Your app is in dist/CleanWispr/CleanWispr"
echo "(portable tarball: dist/CleanWispr-portable-linux-x64.tar.gz)"
