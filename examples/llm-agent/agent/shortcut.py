"""Reads a Windows shortcut (.lnk) so the harness can launch what it names.

`agent-desktop launch` starts executables only. A shortcut is a file that says which exe to start,
with which arguments, in which folder, and it is the shell that reads it. read_link asks the
Windows shell (IShellLinkW and IPersistFile, through ctypes) for those three: the target, the
arguments split by the rules of CommandLineToArgvW, and the working folder with its environment
variables expanded. launch_problem, the pure part, says whether what was read can be launched.
resolve_shortcut puts the two together and is the function a client is given.

Every failure is one of four codes, and no message names a path or an argument."""

import contextlib
import ctypes
import os
import sys
import uuid
from dataclasses import dataclass

UNREADABLE = "SHORTCUT_UNREADABLE"
NO_TARGET = "SHORTCUT_NO_TARGET"
NOT_EXE = "SHORTCUT_NOT_EXE"
TARGET_MISSING = "SHORTCUT_TARGET_MISSING"
CODES = (UNREADABLE, NO_TARGET, NOT_EXE, TARGET_MISSING)
LINK_HEADER = b"\x4c\x00\x00\x00" + uuid.UUID("00021401-0000-0000-C000-000000000046").bytes_le

MESSAGES = {
    UNREADABLE: "바로가기 파일을 읽지 못했습니다",
    NO_TARGET: "바로가기에 대상 경로가 없습니다",
    NOT_EXE: "바로가기의 대상이 실행 파일(.exe)이 아닙니다",
    TARGET_MISSING: "바로가기의 대상 실행 파일이 없습니다",
}


@dataclass(frozen=True)
class Shortcut:
    """What a shortcut names: the target path, its arguments one by one, and the working folder
    ("" when the shortcut gives none)."""

    target: str
    arguments: tuple = ()
    working_dir: str = ""


class ShortcutError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

    @property
    def message(self):
        return MESSAGES[self.code]


def is_shortcut(path):
    """True when `path` names a shortcut file: it ends in `.lnk`, in any case."""
    return str(path).lower().endswith(".lnk")


def resolve_shortcut(path, read=None, is_file=os.path.isfile):
    """The launchable Shortcut at `path`, or ShortcutError with one of the four codes. Anything
    `read` (read_link by default) raises other than a ShortcutError is SHORTCUT_UNREADABLE."""
    shortcut, code = guarded(path, read or read_link)
    if code is not None:
        raise ShortcutError(code)
    problem = launch_problem(shortcut, is_file)
    if problem is not None:
        raise ShortcutError(problem)
    return shortcut


def guarded(path, resolve):
    """(shortcut, None) when `resolve` (resolve_shortcut, or a stand-in for it) gives a Shortcut
    for `path`, else (None, code). A ShortcutError keeps its code when it is one of CODES; any
    other code, any other exception and any other answer is SHORTCUT_UNREADABLE. Never raises."""
    try:
        found = resolve(path)
    except ShortcutError as error:
        return None, error.code if error.code in CODES else UNREADABLE
    except Exception:
        return None, UNREADABLE
    if not isinstance(found, Shortcut):
        return None, UNREADABLE
    return found, None


def launch_problem(shortcut, is_file=os.path.isfile):
    """The code that keeps `shortcut` from being launched, or None when it can be: it must name a
    target, the target must be an `.exe`, and that file must be there."""
    target = shortcut.target.strip() if isinstance(shortcut.target, str) else ""
    if not target:
        return NO_TARGET
    if not target.lower().endswith(".exe"):
        return NOT_EXE
    if not is_file(target):
        return TARGET_MISSING
    return None


def launch_args(shortcut):
    """The `launch` arguments that start `shortcut`: the target, each argument as one
    `--arg=<value>` token so a value may begin with `-`, then `--cwd <folder>` when there is one."""
    tokens = [shortcut.target]
    tokens += [f"--arg={argument}" for argument in shortcut.arguments]
    if shortcut.working_dir:
        tokens += ["--cwd", shortcut.working_dir]
    return tokens


def read_link(path):
    """The target, arguments and working folder the shortcut at `path` names, as the shell reads
    them. ShortcutError(SHORTCUT_UNREADABLE) off Windows and when the file is missing or is not a
    shortcut. A shortcut with no file path (a store app, a control panel item) has target ""."""
    if sys.platform != "win32":
        raise ShortcutError(UNREADABLE)
    try:
        full = os.path.abspath(os.fspath(path))
        with open(full, "rb") as file:
            if not has_link_header(file.read(len(LINK_HEADER))):
                raise ShortcutError(UNREADABLE)
        target, arguments, working_dir = _read_fields(full)
        return Shortcut(
            target=target,
            arguments=tuple(split_arguments(arguments)),
            working_dir=expand_variables(working_dir),
        )
    except ShortcutError:
        raise
    except Exception:
        raise ShortcutError(UNREADABLE) from None


def has_link_header(head):
    """True when `head` begins as a shell link file does: header size 0x4C, then the ShellLink
    class id. The shell itself takes an empty file for a link with no target."""
    return bytes(head[:len(LINK_HEADER)]) == LINK_HEADER


def split_arguments(text):
    """`text` split into arguments by the rules of CommandLineToArgvW (Windows only)."""
    if not text or not text.strip():
        return []
    shell32 = ctypes.WinDLL("shell32")
    kernel32 = ctypes.WinDLL("kernel32")
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(f"x {text}", ctypes.byref(count))
    if not argv:
        raise ShortcutError(UNREADABLE)
    try:
        return [argv[index] for index in range(1, count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def expand_variables(text):
    """`text` with its `%NAME%` environment variables expanded (Windows only)."""
    if not text or "%" not in text:
        return text or ""
    kernel32 = ctypes.WinDLL("kernel32")
    expand = kernel32.ExpandEnvironmentStringsW
    expand.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    expand.restype = ctypes.c_ulong
    size = expand(text, None, 0)
    if not size:
        return text
    buffer = ctypes.create_unicode_buffer(size)
    if not expand(text, buffer, size):
        return text
    return buffer.value


S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = 0x80010106
COINIT_APARTMENTTHREADED = 0x2
CLSCTX_INPROC_SERVER = 0x1
STGM_READ = 0x0
TEXT_MAX = 32768

QUERY_INTERFACE, RELEASE = 0, 2
LINK_GET_PATH, LINK_GET_WORKING_DIRECTORY, LINK_GET_ARGUMENTS = 3, 8, 10
PERSIST_LOAD = 5


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_ulong),
        ("data2", ctypes.c_ushort),
        ("data3", ctypes.c_ushort),
        ("data4", ctypes.c_ubyte * 8),
    ]


def _guid(text):
    return _Guid.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_SHELL_LINK = _guid("00021401-0000-0000-C000-000000000046")
IID_SHELL_LINK_W = _guid("000214F9-0000-0000-C000-000000000046")
IID_PERSIST_FILE = _guid("0000010B-0000-0000-C000-000000000046")


def _read_fields(path):
    with _shell_link() as (link, persist):
        _check(_method(persist, PERSIST_LOAD, ctypes.c_wchar_p, ctypes.c_ulong)(path, STGM_READ))
        return _target(link), _text(link, LINK_GET_ARGUMENTS), _text(link, LINK_GET_WORKING_DIRECTORY)


def _target(link):
    """The link's file path, "" when it has none (S_FALSE) or cannot give one. The shell gives it
    with its environment variables already expanded."""
    buffer = ctypes.create_unicode_buffer(TEXT_MAX)
    get_path = _method(link, LINK_GET_PATH, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong)
    if get_path(buffer, TEXT_MAX, None, 0) < 0:
        return ""
    return buffer.value


def _text(link, index):
    buffer = ctypes.create_unicode_buffer(TEXT_MAX)
    _check(_method(link, index, ctypes.c_wchar_p, ctypes.c_int)(buffer, TEXT_MAX))
    return buffer.value


@contextlib.contextmanager
def _shell_link():
    """A ShellLink object and its IPersistFile, on a thread whose COM is set up for the call. COM
    is initialized when this thread had none; a thread already initialized, in either model, is
    used as it is. Only what this call initialized is uninitialized."""
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_Guid), ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    started = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    initialized = started in (S_OK, S_FALSE)
    if not initialized and _unsigned(started) != RPC_E_CHANGED_MODE:
        raise ShortcutError(UNREADABLE)
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        _check(ole32.CoCreateInstance(
            ctypes.byref(CLSID_SHELL_LINK), None, CLSCTX_INPROC_SERVER, ctypes.byref(IID_SHELL_LINK_W), ctypes.byref(link),
        ))
        query = _method(link, QUERY_INTERFACE, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p))
        _check(query(ctypes.byref(IID_PERSIST_FILE), ctypes.byref(persist)))
        yield link, persist
    finally:
        for obj in (persist, link):
            if obj.value:
                _method(obj, RELEASE)()
        if initialized:
            ole32.CoUninitialize()


def _method(obj, index, *argtypes):
    """Method `index` of COM object `obj`'s vtable, bound to `obj`; it returns the HRESULT."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])
    return lambda *args: function(obj, *args)


def _check(result):
    if result < 0:
        raise ShortcutError(UNREADABLE)
    return result


def _unsigned(result):
    return result & 0xFFFFFFFF
