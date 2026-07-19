"""Audio settings: input device picker, live level meter (mic test),
recording retention (off by default) with purge."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cleanwispr.audio.capture import AudioError, Recorder, list_input_devices
from cleanwispr.storage import paths
from cleanwispr.storage.settings import Settings
from cleanwispr.ui.widgets import LabeledToggle, PathLink, intro_label


class _LevelBridge(QObject):
    """Marshals level callbacks (PortAudio thread) onto the Qt main thread."""

    level = Signal(float)


class AudioTab(QWidget):
    def __init__(self, settings: Settings, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._settings = settings
        self._on_change = on_change
        self._test_recorder: Recorder | None = None
        self._bridge = _LevelBridge()
        self._bridge.level.connect(self._on_level)

        layout = QVBoxLayout(self)
        layout.addWidget(intro_label(
            "Which microphone recordings use. Tip: Bluetooth headset mics take about "
            "a second to wake up and record in lower quality — a wired or USB mic "
            "gives faster and more accurate dictation. Use “Test microphone” to "
            "check that audio is arriving."
        ))
        layout.addWidget(QLabel("Microphone:"))

        self._device_combo = QComboBox()
        self._populate_devices()
        self._device_combo.currentIndexChanged.connect(self._device_changed)
        layout.addWidget(self._device_combo)

        buttons = QHBoxLayout()
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._populate_devices)
        buttons.addWidget(refresh)
        self._test_button = QPushButton("Test microphone")
        self._test_button.setCheckable(True)
        self._test_button.toggled.connect(self._toggle_test)
        buttons.addWidget(self._test_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._meter = QProgressBar()
        self._meter.setRange(0, 100)
        self._meter.setTextVisible(False)
        self._meter.setFixedHeight(12)
        layout.addWidget(self._meter)
        self._meter_hint = QLabel("Speak while testing — the bar should move.")
        self._meter_hint.setStyleSheet("color: gray;")
        self._meter_hint.setVisible(False)
        layout.addWidget(self._meter_hint)

        self._keep_check = LabeledToggle("Keep audio recordings (saved as WAV files)")
        self._keep_check.setToolTip(
            "Off (default): audio is transcribed in memory and immediately discarded. "
            "On: every recording is also saved as a WAV file in the folder below."
        )
        self._keep_check.setChecked(settings.audio.keep_recordings)
        self._keep_check.toggled.connect(self._keep_changed)
        layout.addWidget(self._keep_check)

        purge_row = QHBoxLayout()
        purge_button = QPushButton("Delete all saved recordings")
        purge_button.clicked.connect(self._purge_recordings)
        purge_row.addWidget(purge_button)
        self._purge_hint = PathLink(paths.recordings_dir(), prefix="Folder: ")
        self._purge_hint.setStyleSheet("color: gray;")
        purge_row.addWidget(self._purge_hint, 1)
        layout.addLayout(purge_row)
        layout.addStretch()

    # --- devices ---

    def _populate_devices(self) -> None:
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem("System default", None)
        for device in list_input_devices():
            self._device_combo.addItem(device.name, device.name)
        saved = self._settings.audio.input_device
        if saved:
            index = self._device_combo.findData(saved)
            self._device_combo.setCurrentIndex(index if index >= 0 else 0)
        self._device_combo.blockSignals(False)

    def _device_changed(self) -> None:
        self._settings.audio.input_device = self._device_combo.currentData()
        self._on_change()
        if self._test_button.isChecked():  # restart the test on the new device
            self._stop_test()
            self._test_button.setChecked(False)

    # --- live level meter ---

    def _toggle_test(self, active: bool) -> None:
        if active:
            self._test_recorder = Recorder()
            try:
                self._test_recorder.start(
                    device_name=self._settings.audio.input_device,
                    on_level=self._bridge.level.emit,
                )
            except AudioError as exc:
                self._meter_hint.setText(str(exc))
                self._meter_hint.setVisible(True)
                self._test_recorder = None
                self._test_button.setChecked(False)
                return
            self._test_button.setText("Stop test")
            self._meter_hint.setText("Speak while testing — the bar should move.")
            self._meter_hint.setVisible(True)
        else:
            self._stop_test()

    def _stop_test(self) -> None:
        if self._test_recorder is not None:
            self._test_recorder.abort()
            self._test_recorder = None
        self._test_button.setText("Test microphone")
        self._meter.setValue(0)
        self._meter_hint.setVisible(False)

    def _on_level(self, rms: float) -> None:
        self._meter.setValue(min(100, int(rms * 500)))

    def hideEvent(self, event) -> None:  # Qt override — never leave a test stream running
        super().hideEvent(event)
        if self._test_button.isChecked():
            self._test_button.setChecked(False)

    # --- retention ---

    def _keep_changed(self, checked: bool) -> None:
        self._settings.audio.keep_recordings = checked
        self._on_change()

    def _purge_recordings(self) -> None:
        count = 0
        for file in paths.recordings_dir().glob("*.wav"):
            file.unlink(missing_ok=True)
            count += 1
        self._purge_hint.setText(f"Deleted {count} recording(s).")
