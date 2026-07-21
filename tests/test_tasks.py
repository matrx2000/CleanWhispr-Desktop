"""Task signals must be delivered on the Qt main thread — slots are often
closures/partials that touch widgets, and Qt only queues cross-thread calls
for QObject receivers (regression: model rows never refreshed after a
download because the finished-slot ran on the worker thread)."""

import threading

from cleanwispr.ui.tasks import AsyncTask, DownloadTask


def test_download_task_signals_arrive_on_main_thread(qtbot):
    main_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def work(progress, cancel):
        progress(1, 2)

    task = DownloadTask(work)
    task.progress.connect(lambda *_: seen.setdefault("progress", threading.get_ident()))
    task.finished.connect(lambda: seen.setdefault("finished", threading.get_ident()))
    task.start()

    qtbot.waitUntil(lambda: "finished" in seen, timeout=3000)
    assert seen["progress"] == main_thread
    assert seen["finished"] == main_thread


def test_download_task_handles_byte_counts_over_2gb(qtbot):
    # a Qt int is 32-bit and overflows past ~2.14 GB; model downloads are larger,
    # so the progress signal must carry the byte counts as Python ints (object)
    big = 3_336_156_352  # ~3.3 GB, well over int32 max (2_147_483_647)
    seen = []

    def work(progress, cancel):
        progress(big, big + 4096)

    task = DownloadTask(work)
    task.progress.connect(lambda received, total: seen.append((received, total)))
    with qtbot.waitSignal(task.finished, timeout=3000):
        task.start()

    assert seen == [(big, big + 4096)]  # delivered intact, no OverflowError


def test_download_task_failure_arrives_on_main_thread(qtbot):
    main_thread = threading.get_ident()
    seen: dict[str, object] = {}

    def work(progress, cancel):
        raise RuntimeError("boom")

    task = DownloadTask(work)
    task.failed.connect(
        lambda msg: seen.update(thread=threading.get_ident(), message=msg)
    )
    task.start()

    qtbot.waitUntil(lambda: "thread" in seen, timeout=3000)
    assert seen["thread"] == main_thread
    assert seen["message"] == "boom"


def test_async_task_result_arrives_on_main_thread(qtbot):
    main_thread = threading.get_ident()
    seen: dict[str, object] = {}

    task = AsyncTask(lambda: 42)
    task.done.connect(lambda result: seen.update(thread=threading.get_ident(), result=result))
    task.start()

    qtbot.waitUntil(lambda: "thread" in seen, timeout=3000)
    assert seen["thread"] == main_thread
    assert seen["result"] == 42
