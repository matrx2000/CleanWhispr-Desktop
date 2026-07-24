"""Build the macOS app bundle (PyInstaller onedir → CleanWispr.app).

Run ON macOS — PyInstaller cannot cross-compile. Builds for the CPU of the
build machine (Apple Silicon or Intel).

Usage (from the venv): python scripts/build_macos.py
Output: dist/CleanWispr.app (+ zip in dist/)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main() -> int:
    if sys.platform != "darwin":
        print("This script must run on macOS (PyInstaller cannot cross-compile).")
        return 1

    result = subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed",
            "--name", "CleanWispr",
            "--add-data", f"{ROOT / 'toolkit' / 'builtin'}:toolkit/builtin",
            "--osx-bundle-identifier", "com.cleanwispr.app",
            "--distpath", str(ROOT / "dist"),
            "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            str(ROOT / "packaging" / "entry.py"),
        ],
        cwd=ROOT,
    )
    if result.returncode != 0:
        return result.returncode

    app = ROOT / "dist" / "CleanWispr.app"
    print(f"built: {app}")

    # ditto preserves the bundle structure, symlinks, and permissions —
    # a plain zipfile.ZipFile would break the .app
    zip_path = ROOT / "dist" / "CleanWispr-macos.zip"
    print("zipping .app with ditto...")
    subprocess.run(
        ["ditto", "-c", "-k", "--keepParent", str(app), str(zip_path)], check=True
    )
    print(f"zip: {zip_path} ({zip_path.stat().st_size // 1024 // 1024} MB)")
    print(
        "\nNote: the app is unsigned. Testers must right-click → Open on first "
        "launch (or run: xattr -dr com.apple.quarantine CleanWispr.app) and grant "
        "Microphone, Accessibility, and Input Monitoring permissions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
