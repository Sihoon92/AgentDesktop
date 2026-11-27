"""Read-only Win32 facts for the capture session: processes, top-level windows, modules, monitors.

Everything here only reads, except `set_dpi_awareness` (this process's own DPI mode) and `beep`.
It never sends a message to another application and never reads a window's text: no call in
this module asks for a title. Every public function returns an empty value or None instead of
raising, and outside Windows returns those empty values at once. Each process handle is opened
by `_open_process` and closed before the function returns.
"""

import ctypes
import functools
import os
import platform
import sys
from typing import NamedTuple

from agent.process_identity import process_started_at

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
LIST_MODULES_ALL = 0x03
GW_OWNER = 4
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0
SM_CMONITORS = 80
MB_OK = 0
MAX_PATH_CHARS = 32768
CLASS_CHARS = 256
MAX_MODULES = 4096
INVALID_HANDLE = ctypes.c_void_p(-1).value
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2


class ProcessRow(NamedTuple):
    pid: int
    parent: int
    image: str | None
    started: str | None


class WindowRow(NamedTuple):
    """`rect` is (left, top, right, bottom) in physical pixels once this process is DPI aware.
    `owner` is the owner window or 0; `owner_enabled` is True without an owner."""

    hwnd: int
    pid: int
    class_name: str
    visible: bool
    minimized: bool
    owner: int
    owner_enabled: bool
    rect: tuple[int, int, int, int]


class MonitorInfo(NamedTuple):
    width: int
    height: int
    scale: float
    count: int


def _safe(default):
    def wrap(function):
        @functools.wraps(function)
        def guarded(*args, **kwargs):
            if sys.platform != "win32":
                return default
            try:
                return function(*args, **kwargs)
            except Exception:
                return default
        return guarded
    return wrap


@functools.lru_cache(maxsize=1)
def _api():
    from ctypes import wintypes as w

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    each = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def declare(function, argtypes, restype):
        function.argtypes = argtypes
        function.restype = restype

    declare(kernel32.CreateToolhelp32Snapshot, [w.DWORD, w.DWORD], w.HANDLE)
    declare(kernel32.Process32FirstW, [w.HANDLE, ctypes.POINTER(_ProcessEntry)], w.BOOL)
    declare(kernel32.Process32NextW, [w.HANDLE, ctypes.POINTER(_ProcessEntry)], w.BOOL)
    declare(kernel32.OpenProcess, [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
    declare(kernel32.CloseHandle, [w.HANDLE], w.BOOL)
    declare(kernel32.QueryFullProcessImageNameW, [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL)
    declare(kernel32.IsWow64Process, [w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL)
    declare(kernel32.GetWindowsDirectoryW, [w.LPWSTR, w.UINT], w.UINT)
    declare(psapi.EnumProcessModulesEx, [w.HANDLE, ctypes.POINTER(w.HMODULE), w.DWORD,
                                         ctypes.POINTER(w.DWORD), w.DWORD], w.BOOL)
    declare(psapi.GetModuleFileNameExW, [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD], w.DWORD)
    declare(user32.EnumWindows, [each, w.LPARAM], w.BOOL)
    declare(user32.EnumChildWindows, [w.HWND, each, w.LPARAM], w.BOOL)
    declare(user32.IsWindow, [w.HWND], w.BOOL)
    declare(user32.GetWindowThreadProcessId, [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD)
    declare(user32.GetClassNameW, [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int)
    declare(user32.IsWindowVisible, [w.HWND], w.BOOL)
    declare(user32.IsIconic, [w.HWND], w.BOOL)
    declare(user32.GetWindow, [w.HWND, w.UINT], w.HWND)
    declare(user32.IsWindowEnabled, [w.HWND], w.BOOL)
    declare(user32.GetWindowRect, [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL)
    declare(user32.IsHungAppWindow, [w.HWND], w.BOOL)
    declare(user32.GetForegroundWindow, [], w.HWND)
    declare(user32.MonitorFromWindow, [w.HWND, w.DWORD], w.HMONITOR)
    declare(user32.GetMonitorInfoW, [w.HMONITOR, ctypes.POINTER(_MonitorInfoEx)], w.BOOL)
    declare(user32.GetSystemMetrics, [ctypes.c_int], ctypes.c_int)
    declare(user32.MessageBeep, [w.UINT], w.BOOL)
    return kernel32, user32, psapi, each


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260)]


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", _Rect), ("rcWork", _Rect), ("dwFlags", ctypes.c_uint32),
                ("szDevice", ctypes.c_wchar * 32)]


def _open_process(pid):
    """A process handle with the most read access granted, or None. The caller closes it."""
    kernel32 = _api()[0]
    for access in (PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
                   PROCESS_QUERY_LIMITED_INFORMATION):
        handle = kernel32.OpenProcess(access, False, pid)
        if handle:
            return handle
    return None


def _with_process(pid, read):
    if type(pid) is not int or pid <= 0:
        return None
    handle = _open_process(pid)
    if not handle:
        return None
    try:
        return read(handle)
    finally:
        _api()[0].CloseHandle(handle)


@_safe(())
def process_rows() -> tuple[ProcessRow, ...]:
    kernel32 = _api()[0]
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE:
        return ()
    pairs = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            pairs.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return tuple(ProcessRow(pid, parent, process_image(pid), process_started_at(pid)) for pid, parent in pairs)


@_safe(None)
def process_image(pid) -> str | None:
    from ctypes import wintypes

    def read(handle):
        buffer = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
        size = wintypes.DWORD(MAX_PATH_CHARS)
        if not _api()[0].QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value or None

    return _with_process(pid, read)


@_safe(None)
def process_bits(pid) -> int | None:
    from ctypes import wintypes

    def read(handle):
        wow = wintypes.BOOL()
        if not _api()[0].IsWow64Process(handle, ctypes.byref(wow)):
            return None
        if wow.value:
            return 32
        return 64 if platform.machine().endswith("64") else 32

    return _with_process(pid, read)


@_safe(None)
def modules_of(pid) -> tuple[str, ...] | None:
    from ctypes import wintypes

    def read(handle):
        psapi = _api()[2]
        modules = (wintypes.HMODULE * MAX_MODULES)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed),
                                          LIST_MODULES_ALL):
            return None
        count = min(needed.value // ctypes.sizeof(wintypes.HMODULE), MAX_MODULES)
        names = []
        buffer = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
        for index in range(count):
            if psapi.GetModuleFileNameExW(handle, modules[index], buffer, MAX_PATH_CHARS):
                names.append(buffer.value)
        return tuple(names)

    return _with_process(pid, read)


def _class_name(user32, hwnd):
    buffer = ctypes.create_unicode_buffer(CLASS_CHARS)
    length = user32.GetClassNameW(hwnd, buffer, CLASS_CHARS)
    return buffer.value[:length] if length > 0 else ""


def _row(user32, hwnd):
    from ctypes import wintypes

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    owner = user32.GetWindow(hwnd, GW_OWNER) or 0
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        rect = wintypes.RECT(0, 0, 0, 0)
    return WindowRow(hwnd=int(hwnd), pid=int(pid.value), class_name=_class_name(user32, hwnd),
                     visible=bool(user32.IsWindowVisible(hwnd)), minimized=bool(user32.IsIconic(hwnd)),
                     owner=int(owner), owner_enabled=bool(user32.IsWindowEnabled(owner)) if owner else True,
                     rect=(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)))


def _enumerate(enum_call):
    _kernel32, _user32, _psapi, each = _api()
    found = []

    def keep(hwnd, _param):
        if hwnd:
            found.append(int(hwnd))
        return True

    callback = each(keep)
    enum_call(callback)
    return found


@_safe(())
def top_windows() -> tuple[WindowRow, ...]:
    """Every top-level window, front to back as EnumWindows gives them."""
    user32 = _api()[1]
    handles = _enumerate(lambda callback: user32.EnumWindows(callback, 0))
    return tuple(_row(user32, hwnd) for hwnd in handles)


@_safe(())
def child_classes(hwnd) -> tuple[str, ...]:
    user32 = _api()[1]
    if not hwnd or not user32.IsWindow(hwnd):
        return ()
    handles = _enumerate(lambda callback: user32.EnumChildWindows(hwnd, callback, 0))
    return tuple(_class_name(user32, child) for child in handles)


@_safe(False)
def is_hung(hwnd) -> bool:
    return bool(_api()[1].IsHungAppWindow(hwnd))


@_safe(None)
def foreground() -> int | None:
    return int(_api()[1].GetForegroundWindow() or 0) or None


@_safe(None)
def monitor_of(hwnd) -> MonitorInfo | None:
    user32 = _api()[1]
    if not hwnd or not user32.IsWindow(hwnd):
        return None
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = _MonitorInfoEx()
    info.cbSize = ctypes.sizeof(_MonitorInfoEx)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    width = info.rcMonitor.right - info.rcMonitor.left
    height = info.rcMonitor.bottom - info.rcMonitor.top
    return MonitorInfo(int(width), int(height), _monitor_scale(monitor), int(user32.GetSystemMetrics(SM_CMONITORS)))


def _monitor_scale(monitor) -> float:
    from ctypes import wintypes

    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                            ctypes.POINTER(wintypes.UINT)]
        shcore.GetDpiForMonitor.restype = ctypes.c_long
        x, y = wintypes.UINT(), wintypes.UINT()
        if shcore.GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, ctypes.byref(x), ctypes.byref(y)) != 0 or not x.value:
            return 1.0
        return round(x.value / 96, 2)
    except (OSError, AttributeError):
        return 1.0


@_safe("")
def windows_dir() -> str:
    buffer = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
    if _api()[0].GetWindowsDirectoryW(buffer, MAX_PATH_CHARS):
        return buffer.value
    return os.environ.get("SystemRoot", "")


def _per_monitor_v2() -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
    return bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))


def _per_monitor() -> bool:
    shcore = ctypes.WinDLL("shcore")
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness.restype = ctypes.c_long
    return shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0


def _system_aware() -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetProcessDPIAware.restype = ctypes.c_int
    return bool(user32.SetProcessDPIAware())


_DPI_STEPS = (("PER_MONITOR_V2", _per_monitor_v2), ("PER_MONITOR", _per_monitor), ("SYSTEM", _system_aware))


def set_dpi_awareness() -> str:
    """Makes this process DPI aware at the best level the system grants and names it, so Win32
    rectangles, screen pictures and CLI bounds share physical pixels. "NONE" when none took."""
    if sys.platform != "win32":
        return "NONE"
    for name, step in _DPI_STEPS:
        try:
            if step():
                return name
        except (OSError, AttributeError):
            continue
    return "NONE"


@_safe(None)
def beep() -> None:
    _api()[1].MessageBeep(MB_OK)
