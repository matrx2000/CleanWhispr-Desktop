"""Build the Linux app bundle (PyInstaller onedir, no console window).

Run ON Linux — PyInstaller cannot cross-compile. For widest compatibility,
build on the oldest distro you want to support (the binary links against the
build machine's glibc).

Usage (from the venv): python scripts/build_linux.py
Output: dist/CleanWispr/CleanWispr (+ portable tar.gz in dist/)
"""

import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).parent.parent

DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=CleanWispr
Comment=Local voice-to-text and voice-driven text editing
Exec={exec_path}
Terminal=false
Categories=Utility;AudioVideo;
"""


def main() -> int:
    if not sys.platform.startswith("linux"):
        print("This script must run on Linux (PyInstaller cannot cross-compile).")
        return 1

    result = subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed",
            "--name", "CleanWispr",
            "--distpath", str(ROOT / "dist"),
            "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            str(ROOT / "packaging" / "entry.py"),
        ],
        cwd=ROOT,
    )
    if result.returncode != 0:
        return result.returncode

    bundle = ROOT / "dist" / "CleanWispr"
    binary = bundle / "CleanWispr"
    print(f"built: {binary} ({binary.stat().st_size // 1024} KB)")

    # sample .desktop launcher users can copy to ~/.local/share/applications
    # (Exec must be edited to the final install location)
    desktop = bundle / "CleanWispr.desktop"
    desktop.write_text(DESKTOP_ENTRY.format(exec_path=binary.name), encoding="utf-8")

    tar_path = ROOT / "dist" / "CleanWispr-portable-linux-x64.tar.gz"
    print("creating portable tar.gz (preserves executable permissions)...")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(bundle, arcname="CleanWispr")
    print(f"portable tarball: {tar_path} ({tar_path.stat().st_size // 1024 // 1024} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
