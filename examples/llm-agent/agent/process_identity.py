"""Tells whether a process id still names the same process, by its start time.

The recorder writes its own pid and start time into run.json, and the viewer compares them
with the live process later: a missing process or a different start time means the pid was
reused or the run died. Standard library only; outside Windows the start time is unknown.
"""

import ctypes
import sys
from datetime import datetime, timedelta, timezone

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PID = 0xFFFFFFFF
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def process_started_at(pid):
    """Start time of process pid as UTC ISO with milliseconds and a trailing Z, such as
    '2026-11-08T00:59:58.123Z'. None when the process is gone, exited, or cannot be read.
    Never raises."""
    if sys.platform != "win32" or type(pid) is not int or not 0 < pid <= MAX_PID:
        return None
    try:
        return _windows_start(_kernel32(), pid)
    except Exception:
        return None


def _windows_start(api, pid):
    from ctypes import wintypes

    handle = api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not api.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        if _ticks(exited):
            return None
        return _stamp(_ticks(creation))
    finally:
        api.CloseHandle(handle)


def _ticks(filetime):
    return (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime


def _stamp(ticks):
    started = FILETIME_EPOCH + timedelta(microseconds=ticks // 10)
    return started.strftime("%Y-%m-%dT%H:%M:%S.") + f"{started.microsecond // 1000:03d}Z"


def _kernel32():
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
