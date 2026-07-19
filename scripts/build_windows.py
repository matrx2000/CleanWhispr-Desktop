"""Build the Windows app bundle (PyInstaller onedir, windowed).

Usage (from the venv): python scripts/build_windows.py
Output: dist/CleanWispr/CleanWispr.exe (+ portable zip in dist/)
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main() -> int:
    icon = ROOT / "resources" / "icon.ico"
    if not icon.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_icon.py")], check=True)

    result = subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed",
            "--name", "CleanWispr",
            "--icon", str(icon),
            "--distpath", str(ROOT / "dist"),
            "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            str(ROOT / "packaging" / "entry.py"),
        ],
        cwd=ROOT,
    )
    if result.returncode != 0:
        return result.returncode

    exe = ROOT / "dist" / "CleanWispr" / "CleanWispr.exe"
    print(f"built: {exe} ({exe.stat().st_size // 1024} KB)")

    zip_path = ROOT / "dist" / "CleanWispr-portable-win64.zip"
    print("zipping portable build...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in (ROOT / "dist" / "CleanWispr").rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(ROOT / "dist"))
    print(f"portable zip: {zip_path} ({zip_path.stat().st_size // 1024 // 1024} MB)")

    iscc = shutil.which("iscc")
    if iscc:
        print("compiling installer with Inno Setup...")
        subprocess.run([iscc, str(ROOT / "packaging" / "installer.iss")], check=True)
    else:
        print("Inno Setup (iscc) not found — skipped installer; portable zip is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
