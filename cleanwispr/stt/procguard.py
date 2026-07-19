"""Child-process lifetime guard.

On Windows, spawned inference servers are assigned to a Job Object with
KILL_ON_JOB_CLOSE: when this process exits — even force-killed — the OS
terminates every child in the job. No more orphaned whisper-servers.
On Linux, PR_SET_PDEATHSIG (via popen_kwargs) does the same at spawn time.
macOS relies on the graceful stop() path.
"""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    _job_handle = None

    def _job() -> int | None:
        global _job_handle
        if _job_handle is not None:
            return _job_handle
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            log.warning("CreateJobObject failed (%d)", ctypes.get_last_error())
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            handle, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            log.warning("SetInformationJobObject failed (%d)", ctypes.get_last_error())
            _kernel32.CloseHandle(handle)
            return None
        _job_handle = handle
        return handle

    def guard_child(process: subprocess.Popen) -> None:
        """Tie the child's lifetime to ours."""
        job = _job()
        if job is None:
            return
        if not _kernel32.AssignProcessToJobObject(job, int(process._handle)):
            log.warning("AssignProcessToJobObject failed (%d)", ctypes.get_last_error())

else:

    def guard_child(process: subprocess.Popen) -> None:
        """Non-Windows: lifetime is tied at spawn time via popen_kwargs()."""


def popen_kwargs() -> dict:
    """Extra Popen kwargs that tie the child's lifetime to ours at spawn time.

    Linux: PR_SET_PDEATHSIG delivers SIGKILL to the child when this process
    dies — the counterpart of the Windows job object. macOS has no equivalent;
    the graceful stop() path covers it."""
    if sys.platform.startswith("linux"):
        import ctypes as _ctypes
        import signal as _signal

        def _pdeathsig() -> None:
            libc = _ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(1, _signal.SIGKILL)  # PR_SET_PDEATHSIG = 1

        return {"preexec_fn": _pdeathsig}
    return {}
