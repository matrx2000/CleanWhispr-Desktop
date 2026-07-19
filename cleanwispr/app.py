"""Application entry point: wires settings, controller, subsystems, and UI."""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from cleanwispr import APP_NAME, autostart, logging_setup
from cleanwispr.audio.capture import Recorder
from cleanwispr.core.controller import Controller
from cleanwispr.hotkeys.base import HotkeyError
from cleanwispr.hotkeys.combos import combos_overlap
from cleanwispr.hotkeys.pynput_backend import PynputBackend
from cleanwispr.storage import paths
from cleanwispr.storage import settings as settings_store
from cleanwispr.storage.db import HistoryDb
from cleanwispr.stt import registry
from cleanwispr.stt.parakeet import ParakeetEngine
from cleanwispr.stt.whisper_cpp import WhisperCppEngine
from cleanwispr.ui import theme
from cleanwispr.ui.overlay import OverlayPill
from cleanwispr.ui.settings.window import SettingsWindow
from cleanwispr.ui.sounds import SoundPlayer
from cleanwispr.ui.thinking_panel import ThinkingPanel
from cleanwispr.ui.tray import TrayManager

log = logging.getLogger(__name__)


class _HotkeyBridge(QObject):
    """Marshals hotkey callbacks (pynput thread) onto the Qt main thread."""

    pressed = Signal(str)  # slot name
    released = Signal(str)
    cancel = Signal()


def _make_injector():
    if sys.platform == "win32":
        from cleanwispr.inject.windows import WindowsInjector

        return WindowsInjector()
    if sys.platform == "darwin":
        from cleanwispr.inject.macos import MacInjector

        return MacInjector()
    from cleanwispr.inject.linux import LinuxInjector

    return LinuxInjector()


def main() -> int:
    first_run = not paths.config_file().exists()  # before load() materializes it
    early_settings = settings_store.load()
    logging_setup.setup(verbose=early_settings.ui.verbose_logging)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # closing windows must not kill the tray app
    theme.apply(app)

    # single instance: a second launch would double every hotkey, recording, and paste
    lock = QLockFile(str(Path(tempfile.gettempdir()) / "cleanwispr.lock"))
    lock.setStaleLockTime(0)  # a live lock is held by a live process; crashes clear it
    if not lock.tryLock(100):
        QMessageBox.information(
            None, APP_NAME, f"{APP_NAME} is already running — look for it in the system tray."
        )
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "No system tray available on this desktop.")
        return 1

    # NOTE: no hotkey "default migrations" here — rewriting a combo that
    # matches an old default silently clobbers users who chose it on purpose
    # (a saved ctrl+super kept resetting to f8 on every launch)
    settings = early_settings
    settings_store.save(settings)  # materialize defaults on first run
    paths.set_models_override(settings.stt.models_dir or None)
    registry.migrate_legacy_binaries()
    if settings.ui.start_on_login:
        autostart.set_autostart(True)  # keep the registry command current
    db = HistoryDb()

    engines = {"whisper": WhisperCppEngine(), "parakeet": ParakeetEngine()}
    controller = Controller(settings, db, Recorder(), engines, _make_injector())

    def on_settings_changed() -> None:
        settings_store.save(settings)

    bridge = _HotkeyBridge()
    bridge.pressed.connect(controller.hotkey_pressed)
    bridge.released.connect(controller.hotkey_released)
    bridge.cancel.connect(controller.cancel)
    hotkeys = PynputBackend()

    tray = TrayManager(controller, on_open_settings=None)  # wired below

    def apply_hotkeys() -> None:
        """(Re)register both slots from current settings — startup and live changes.
        Overlapping combos (one contained in the other) would both fire on the
        larger chord, so the editor slot is disabled until the conflict is fixed."""
        dictation = settings.hotkeys.dictation.combo
        editor = settings.hotkeys.editor.combo
        conflict = combos_overlap(dictation, editor)
        try:
            hotkeys.register(
                "dictation",
                dictation,
                on_press=lambda: bridge.pressed.emit("dictation"),
                on_release=lambda: bridge.released.emit("dictation"),
            )
            if conflict:
                hotkeys.unregister("editor")
                tray.notify(
                    f"Editor hotkey disabled: '{editor}' overlaps the dictation "
                    f"hotkey '{dictation}' — both would trigger together. Pick a "
                    f"different editor combo in Settings → Hotkeys."
                )
            else:
                hotkeys.register(
                    "editor",
                    editor,
                    on_press=lambda: bridge.pressed.emit("editor"),
                    on_release=lambda: bridge.released.emit("editor"),
                )
        except HotkeyError as exc:
            log.error("hotkey setup failed: %s", exc)
            tray.notify(f"Hotkeys unavailable: {exc}")

    cleaned_up = False

    def cleanup() -> None:
        """Stop everything exactly once — normal quit and clear-app-data share it."""
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        hotkeys.stop()
        controller.shutdown()
        db.close()

    def clear_app_data() -> None:
        """Factory reset: stop all subsystems (releases file locks on the DB and
        engine binaries), delete every app folder, then quit."""
        log.info("clearing all app data at user request")
        cleanup()
        roots = {paths.config_dir(), paths.data_dir(), paths.cache_dir(), paths.models_root()}
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)
        app.quit()

    wizard_ref: list = []  # keeps the non-modal-exec dialog alive

    def open_setup_guide() -> None:
        from cleanwispr.ui.setup_wizard import SetupWizard

        wizard = SetupWizard(settings, on_settings_changed)
        wizard_ref.append(wizard)

        def finished(_result: int) -> None:
            wizard_ref.clear()
            controller.prewarm()  # a model may have just been downloaded

        wizard.finished.connect(finished)
        wizard.show()
        wizard.raise_()
        wizard.activateWindow()

    settings_window = SettingsWindow(
        settings,
        db,
        on_settings_changed,
        apply_hotkeys,
        on_clear_app_data=clear_app_data,
        on_run_setup=open_setup_guide,
    )
    controller.history_changed.connect(settings_window.history_tab.refresh)
    tray.set_open_settings(_show(settings_window))

    overlay = OverlayPill(controller, settings)  # noqa: F841 — kept alive for app lifetime
    thinking = ThinkingPanel(controller, settings)  # noqa: F841 — kept alive for app lifetime
    sounds = SoundPlayer(settings)
    sounds.attach(controller)
    tray.show()

    apply_hotkeys()
    try:
        hotkeys.register("cancel", "esc", on_press=bridge.cancel.emit)
        hotkeys.start()
    except Exception as exc:  # e.g. no X display / missing permissions
        log.error("global hotkeys unavailable: %s", exc)
        tray.notify(f"Global hotkeys unavailable: {exc} — use the tray menu instead.")

    controller.prewarm()  # load the whisper model in the background
    if first_run:
        open_setup_guide()

    exit_code = app.exec()
    cleanup()
    return exit_code


def _show(window):
    def show() -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    return show


if __name__ == "__main__":
    raise SystemExit(main())
