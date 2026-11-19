"""A native id that is only a window handle is not an id.

Some toolkits give a control with no id of its own its window handle, in decimal, as its
automation id. The handle is new every time the app starts, so such an id never finds the same
control again. A snapshot's node whose native id is, letter for letter, the decimal text of a
window handle of the snapshot window's process (top-level and child windows alike) is treated as
having no native id.

without_handle_ids is the pure part; process_window_handles asks Win32 and gives an empty set
elsewhere or when it cannot tell. window_number reads the handle from a CLI window id
(`w-<decimal>`)."""

import re
import sys

WINDOW_ID = re.compile(r"w-([0-9]+)", re.ASCII)


def window_number(window_id):
    """The handle a CLI window id names, or None when it is not of the form `w-<decimal>`."""
    if not isinstance(window_id, str):
        return None
    match = WINDOW_ID.fullmatch(window_id)
    return int(match.group(1)) if match else None


def without_handle_ids(tree, handles):
    """A copy of `tree` in which every node whose native id is the decimal text of one of
    `handles` has no `native_id`. Nothing else changes; `tree` itself is left as it was."""
    texts = {str(handle) for handle in handles}
    if not texts or not isinstance(tree, dict):
        return tree
    root = dict(tree)
    stack = [root]
    while stack:
        node = stack.pop()
        if _native_text(node) in texts:
            del node["native_id"]
        children = node.get("children")
        if isinstance(children, list):
            copies = [dict(child) if isinstance(child, dict) else child for child in children]
            node["children"] = copies
            stack.extend(child for child in copies if isinstance(child, dict))
    return root


def _native_text(node):
    native = node.get("native_id")
    value = native.get("value") if isinstance(native, dict) else native
    return value if isinstance(value, str) else None


def process_window_handles(hwnd):
    """Every window handle, top-level and child, of the process that owns window `hwnd`. Empty off
    Windows and when `hwnd` is no window."""
    if sys.platform != "win32":
        return frozenset()
    return _win32_handles(hwnd)


def _win32_handles(hwnd):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    each = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [each, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, each, wintypes.LPARAM]

    def owner(handle):
        pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(handle, ctypes.byref(pid)):
            return 0
        return pid.value

    process = owner(hwnd) if hwnd else 0
    if not process:
        return frozenset()
    found = set()

    def keep(handle, _param):
        if handle and owner(handle) == process:
            found.add(int(handle))
        return True

    callback = each(keep)
    user32.EnumWindows(callback, 0)
    for top in list(found):
        user32.EnumChildWindows(top, callback, 0)
    return frozenset(found)
