"""Background task helper for the UI: run a blocking function on a thread,
report progress/result back to the Qt main thread via signals.

Thread-safety note: signals connected to plain Python callables (closures,
functools.partial) are invoked in the EMITTING thread — Qt can only queue the
call across threads when the receiver is a QObject. Emitting the public
signals directly from the worker would therefore run widget-touching slots on
the worker thread (symptom: the UI silently stops repainting, e.g. a model row
never showing its "Use" button after a download). To make every connection
safe, workers emit internal signals whose only receivers are bound methods of
the task itself — a QObject living on the main thread, so delivery is queued —
and those methods re-emit the public signals from the main thread.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread

from PySide6.QtCore import QObject, Signal

# Plain daemon threads, NOT a ThreadPoolExecutor: the interpreter joins
# executor workers at exit, so one task stuck in a slow network call would
# keep the whole process (and any frozen window) alive after Quit. A UI task
# dying with the process is always acceptable.


def _spawn(target: Callable[[], None], name: str) -> None:
    Thread(target=target, daemon=True, name=name).start()


class DownloadTask(QObject):
    """One download with progress; cancellable. Public signals fire on the
    Qt main thread."""

    # object (not int) for the byte counts: a Qt `int` is C++ int32 and overflows
    # once a download passes ~2.14 GB (libshiboken OverflowError); Python ints don't
    progress = Signal(object, object)  # received_bytes, total_bytes|None
    finished = Signal()
    failed = Signal(str)

    _worker_progress = Signal(object, object)
    _worker_finished = Signal()
    _worker_failed = Signal(str)

    def __init__(self, work: Callable[..., object]) -> None:
        """work(progress=fn, cancel=Event) — e.g. downloader.download_model partial."""
        super().__init__()
        self._work = work
        self._cancel = Event()
        self._worker_progress.connect(self._relay_progress)
        self._worker_finished.connect(self._relay_finished)
        self._worker_failed.connect(self._relay_failed)

    def start(self) -> None:
        _spawn(self._run, "ui-download")

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:  # worker thread
        try:
            self._work(progress=self._worker_progress.emit, cancel=self._cancel)
            self._worker_finished.emit()
        except Exception as exc:
            self._worker_failed.emit(str(exc))

    # --- main-thread relays ---

    def _relay_progress(self, received: int, total: object) -> None:
        self.progress.emit(received, total)

    def _relay_finished(self) -> None:
        self.finished.emit()

    def _relay_failed(self, message: str) -> None:
        self.failed.emit(message)


class AsyncTask(QObject):
    """Run a blocking callable on a worker thread; deliver result via signals
    on the Qt main thread."""

    done = Signal(object)
    failed = Signal(str)

    _worker_done = Signal(object)
    _worker_failed = Signal(str)

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self._work = work
        self._worker_done.connect(self._relay_done)
        self._worker_failed.connect(self._relay_failed)

    def start(self) -> None:
        _spawn(self._run, "ui-task")

    def _run(self) -> None:  # worker thread
        try:
            self._worker_done.emit(self._work())
        except Exception as exc:
            self._worker_failed.emit(str(exc))

    # --- main-thread relays ---

    def _relay_done(self, result: object) -> None:
        self.done.emit(result)

    def _relay_failed(self, message: str) -> None:
        self.failed.emit(message)
