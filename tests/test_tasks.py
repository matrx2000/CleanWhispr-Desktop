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
