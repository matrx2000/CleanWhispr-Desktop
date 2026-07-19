"""Generate resources/icon.ico from the app's drawn tray icon (no asset files)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication, QImageWriter

sys.path.insert(0, str(Path(__file__).parent.parent))

from cleanwispr.core.controller import AppState
from cleanwispr.ui.icons import state_icon

app = QGuiApplication([])
formats = [bytes(f).decode() for f in QImageWriter.supportedImageFormats()]
if "ico" not in formats:
    raise SystemExit(f"Qt cannot write .ico here (formats: {formats})")

out = Path(__file__).parent.parent / "resources"
out.mkdir(exist_ok=True)
icon = state_icon(AppState.IDLE, size=256)
pixmap = icon.pixmap(256, 256)
target = out / "icon.ico"
if not pixmap.save(str(target), "ICO"):
    raise SystemExit("icon save failed")
print(f"wrote {target} ({target.stat().st_size} bytes)")
