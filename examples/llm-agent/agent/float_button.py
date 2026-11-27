"""A small always-on-top button that never takes the foreground.

The window carries WS_EX_NOACTIVATE, so a click on it leaves the foreground window (a popup that
closes when it loses focus, say) where it was. A left click captures, dragging moves it, a right
click opens a menu whose one item ends the session (that menu may take the focus). It shows the
capture count and turns orange while slow phases are pending. Other threads update it through a
queue the Tk loop polls; Tk itself runs on the main thread.
"""

import ctypes
import queue
from collections.abc import Callable

WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
NO_ACTIVATE_STYLE = WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
GWL_EXSTYLE = -20
POLL_MS = 100
DRAG_PIXELS = 4
IDLE_COLOR = "#2b6cb0"
BUSY_COLOR = "#dd6b20"
SIZE = 56


def apply_no_activate(hwnd: int, user32) -> None:
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | NO_ACTIVATE_STYLE)


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    return user32


class FloatButton:
    def __init__(self, on_capture: Callable[[], None], on_end: Callable[[], None]):
        self.on_capture = on_capture
        self.on_end = on_end
        self._updates: queue.Queue = queue.Queue()
        self._root = None
        self._label = None
        self._count = 0
        self._busy = False
        self._press = None

    @staticmethod
    def available() -> bool:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            return False
        return True

    def start(self) -> None:
        import tkinter

        root = tkinter.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry(f"{SIZE}x{SIZE}+{root.winfo_screenwidth() - SIZE - 24}+{root.winfo_screenheight() // 3}")
        label = tkinter.Label(root, text="0", fg="white", bg=IDLE_COLOR, font=("Segoe UI", 14, "bold"))
        label.pack(fill="both", expand=True)
        menu = tkinter.Menu(root, tearoff=0)
        menu.add_command(label="종료", command=self.on_end)
        label.bind("<ButtonPress-1>", self._pressed)
        label.bind("<B1-Motion>", self._dragged)
        label.bind("<ButtonRelease-1>", self._released)
        label.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))
        self._root, self._label = root, label
        root.update_idletasks()
        try:
            apply_no_activate(int(root.wm_frame(), 16), _user32())
        except (OSError, ValueError, AttributeError):
            pass
        root.after(POLL_MS, self._poll)

    def _pressed(self, event) -> None:
        self._press = (event.x_root, event.y_root, self._root.winfo_x(), self._root.winfo_y(), False)

    def _dragged(self, event) -> None:
        if self._press is None:
            return
        x0, y0, left, top, moved = self._press
        dx, dy = event.x_root - x0, event.y_root - y0
        if moved or abs(dx) > DRAG_PIXELS or abs(dy) > DRAG_PIXELS:
            self._press = (x0, y0, left, top, True)
            self._root.geometry(f"+{left + dx}+{top + dy}")

    def _released(self, _event) -> None:
        pressed, self._press = self._press, None
        if pressed is not None and not pressed[4]:
            self.on_capture()

    def _poll(self) -> None:
        while True:
            try:
                kind, value = self._updates.get_nowait()
            except queue.Empty:
                break
            if kind == "close":
                self._root.destroy()
                return
            if kind == "count":
                self._count = value
            elif kind == "busy":
                self._busy = value
        self._label.configure(text=str(self._count), bg=BUSY_COLOR if self._busy else IDLE_COLOR)
        self._root.after(POLL_MS, self._poll)

    def set_count(self, n: int) -> None:
        self._updates.put(("count", int(n)))

    def set_busy(self, busy: bool) -> None:
        self._updates.put(("busy", bool(busy)))

    def run(self) -> None:
        if self._root is not None:
            self._root.mainloop()

    def close(self) -> None:
        self._updates.put(("close", None))
