"""Background task helper for the UI: run a blocking function on a thread,
report progress/result back to the Qt main thread via signals."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from PySide6.QtCore import QObject, Signal

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ui-task")


class DownloadTask(QObject):
    """One download with progress; cancellable."""

    progress = Signal(int, object)  # received_bytes, total_bytes|None
    finished = Signal()
    failed = Signal(str)

    def __init__(self, work: Callable[..., object]) -> None:
        """work(progress=fn, cancel=Event) — e.g. downloader.download_model partial."""
        super().__init__()
        self._work = work
        self._cancel = Event()

    def start(self) -> None:
        _executor.submit(self._run)

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        try:
            self._work(progress=self.progress.emit, cancel=self._cancel)
            self.finished.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class AsyncTask(QObject):
    """Run a blocking callable on a worker thread; deliver result via signals."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self._work = work

    def start(self) -> None:
        _executor.submit(self._run)

    def _run(self) -> None:
        try:
            self.done.emit(self._work())
        except Exception as exc:
            self.failed.emit(str(exc))
