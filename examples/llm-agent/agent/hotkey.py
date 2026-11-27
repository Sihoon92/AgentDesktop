"""Global hotkeys for the capture session, by RegisterHotKey on one message-loop thread.

A hotkey arrives as WM_HOTKEY on the thread that registered it, so one thread registers every
binding, runs GetMessage and calls `on_signal` with the binding's name. No keyboard hook is
installed and no other key is seen. A binding another program already holds fails to register;
`start` returns its name and the session carries on without it.
"""

import ctypes
import sys
import threading
from collections.abc import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_F1 = 0x70
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
START_WAIT_S = 5.0


def _key_code(word: str) -> int | None:
    if len(word) == 1 and ("a" <= word <= "z" or "0" <= word <= "9"):
        return ord(word.upper())
    if word.startswith("f") and word[1:].isdigit() and 1 <= int(word[1:]) <= 24:
        return VK_F1 + int(word[1:]) - 1
    return None


def parse_hotkey(text: str) -> tuple[int, int]:
    """("ctrl+alt+f9") -> (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_F9). At least one modifier
    and exactly one key of f1-f24, a-z, 0-9; anything else is ValueError."""
    words = [word.strip().lower() for word in str(text or "").split("+")]
    modifiers = 0
    keys = []
    for word in words:
        if word in MODIFIERS:
            modifiers |= MODIFIERS[word]
        else:
            keys.append(word)
    if not modifiers or len(keys) != 1:
        raise ValueError("a hotkey needs modifiers and one key")
    code = _key_code(keys[0])
    if code is None:
        raise ValueError("unknown key")
    return modifiers | MOD_NOREPEAT, code


class _Msg(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_ulong), ("x", ctypes.c_long), ("y", ctypes.c_long)]


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = ctypes.c_int
    user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.UnregisterHotKey.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(_Msg), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostThreadMessageW.restype = ctypes.c_int
    return user32


class HotkeyThread:
    """`bindings` maps a signal name ("capture", "end") to its hotkey text."""

    def __init__(self, bindings: dict[str, str], on_signal: Callable[[str], None], user32=None):
        self.bindings = dict(bindings)
        self.on_signal = on_signal
        self._user32 = user32
        self._failed: tuple[str, ...] = tuple(self.bindings)
        self._ready = threading.Event()
        self._thread_id: int | None = None
        self._thread = threading.Thread(target=self._run, name="survey-hotkeys", daemon=True)

    def start(self) -> tuple[str, ...]:
        """Registers every binding and returns the names that failed to register."""
        if self._user32 is None and sys.platform != "win32":
            return self._failed
        self._thread.start()
        self._ready.wait(START_WAIT_S)
        return self._failed

    def _run(self) -> None:
        self._thread_id = threading.get_native_id()
        try:
            user32 = self._user32 if self._user32 is not None else _user32()
        except OSError:
            self._ready.set()
            return
        names: dict[int, str] = {}
        failed = []
        for number, (name, text) in enumerate(self.bindings.items(), start=1):
            try:
                modifiers, key = parse_hotkey(text)
            except ValueError:
                failed.append(name)
                continue
            if user32.RegisterHotKey(None, number, modifiers, key):
                names[number] = name
            else:
                failed.append(name)
        self._failed = tuple(failed)
        self._ready.set()
        message = _Msg()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and int(message.wParam) in names:
                    try:
                        self.on_signal(names[int(message.wParam)])
                    except Exception:
                        pass
        finally:
            for number in names:
                user32.UnregisterHotKey(None, number)

    def stop(self, timeout_s: float = 5.0) -> None:
        if not self._thread.is_alive() or self._thread_id is None:
            return
        user32 = self._user32 if self._user32 is not None else _user32()
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout_s)
