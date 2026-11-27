"""Fakes for the capture-session tests: a CLI, a Win32 reader, a screen grabber and a UIA runner.

Every name a real screen would show carries a marker (SECRET_TITLE, SECRET_NAME,
TfrmSecretScreen, CompanySecret.dll) so a test can prove none reaches a name-free file.
"""

import subprocess

from PIL import Image

from agent.win32_facts import MonitorInfo, ProcessRow, WindowRow

MARKERS = ("SECRET_TITLE", "SECRET_NAME", "TfrmSecretScreen", "CompanySecret.dll")
APP_DIR = r"C:\Apps\Real"
APP_EXE = APP_DIR + r"\Real.exe"
APP, OTHER, OWN = 100, 200, 300
UIA_JSON = '{"nodes":5,"truncated":false,"frameworks":{"Win32":5},"patterns":{"Invoke":2}}'


def window(hwnd, pid=APP, cls="TfrmSecretScreen", owner=0, owner_enabled=True, rect=(0, 0, 900, 700),
           visible=True, minimized=False):
    return WindowRow(hwnd, pid, cls, visible, minimized, owner, owner_enabled, rect)


MAIN = window(11)
DIALOG = window(12, cls="#32770", owner=11, owner_enabled=False, rect=(100, 100, 400, 300))
FOREIGN = window(21, pid=OTHER, cls="SECRET_NAME", rect=(50, 50, 250, 150))
BUTTON = window(31, pid=OWN, cls="TkTopLevel", rect=(950, 10, 990, 40))


def tree(name="SECRET_NAME", x=0, y=0):
    return {"role": "window", "name": name, "bounds": {"x": x, "y": y, "width": 900, "height": 700},
            "children": [{"role": "button", "ref_id": "@e1", "name": name,
                          "bounds": {"x": x + 10, "y": y + 10, "width": 80, "height": 20}},
                         {"role": "pane", "native_id": {"kind": "automation_id", "value": "SECRET_NAME"},
                          "bounds": {"x": x + 10, "y": y + 50, "width": 80, "height": 20}}]}


class FakeDesktop:
    """`run(*argv)` like DesktopClient. `snapshot_errors` maps hwnd to an error code, `incomplete`
    holds hwnds read as truncated, `gone` holds hwnds list-windows no longer shows."""

    def __init__(self, windows=(MAIN, DIALOG, FOREIGN)):
        self.windows = list(windows)
        self.calls: list[tuple] = []
        self.snapshot_errors: dict[int, str] = {}
        self.incomplete: set[int] = set()
        self.gone: set[int] = set()
        self.before_snapshot = None

    def run(self, *argv, headed=False):
        argv = tuple(str(arg) for arg in argv)
        self.calls.append(argv)
        if argv[0] == "list-windows":
            return {"ok": True, "data": [
                {"id": f"w-{row.hwnd}", "pid": row.pid, "process_instance": f"{row.pid}:inst",
                 "title": "SECRET_TITLE", "app_name": "CompanySecret.dll",
                 "bounds": {"x": row.rect[0], "y": row.rect[1], "width": row.rect[2] - row.rect[0],
                            "height": row.rect[3] - row.rect[1]}}
                for row in self.windows if row.hwnd not in self.gone]}
        if argv[0] == "snapshot":
            hwnd = int(argv[argv.index("--window-id") + 1][2:])
            if self.before_snapshot:
                self.before_snapshot(hwnd)
            if hwnd in self.snapshot_errors:
                return {"ok": False, "error": {"code": self.snapshot_errors[hwnd], "message": "x"}}
            row = next(item for item in self.windows if item.hwnd == hwnd)
            return {"ok": True, "data": {"complete": hwnd not in self.incomplete,
                                         "tree": tree(x=row.rect[0], y=row.rect[1])}}
        return {"ok": True, "data": {}}


class FakeWin32:
    def __init__(self, windows=(MAIN, DIALOG, FOREIGN), processes=None):
        self.windows = list(windows)
        self.processes = list(processes if processes is not None else [
            ProcessRow(APP, 1, APP_EXE, "a"), ProcessRow(OTHER, 1, r"C:\Tools\Other.exe", "b"),
            ProcessRow(OWN, 1, r"C:\Python\python.exe", "c")])
        self.fg: int | None = 11
        self.hung: set[int] = set()
        self.module_calls: list[int] = []
        self.beeps = 0
        self.modules: tuple | None = (r"C:\Windows\System32\kernel32.dll", r"C:\Windows\System32\mshtml.dll",
                                      APP_DIR + r"\CompanySecret.dll", APP_DIR + r"\DevExpress.XtraGrid.v21.dll",
                                      APP_DIR + r"\vcl270.bpl")

    def process_rows(self):
        return tuple(self.processes)

    def top_windows(self):
        return tuple(self.windows)

    def foreground(self):
        return self.fg

    def is_hung(self, hwnd):
        return hwnd in self.hung

    def modules_of(self, pid):
        self.module_calls.append(pid)
        return self.modules

    def process_bits(self, pid):
        return 64

    def child_classes(self, hwnd):
        return ("TfrmSecretScreen", "MDIClient", "Edit")

    def monitor_of(self, hwnd):
        return MonitorInfo(1920, 1080, 1.5, 2)

    def windows_dir(self):
        return r"C:\Windows"

    def set_dpi_awareness(self):
        return "PER_MONITOR_V2"

    def beep(self):
        self.beeps += 1


def patterned(size=(64, 48)):
    image = Image.new("RGB", size, "white")
    for x in range(0, size[0], 4):
        image.putpixel((x, x % size[1]), (0, 0, 0))
    return image


class FakeGrab:
    """`grab(all_screens=True)` or `grab(bbox=..., all_screens=True)`; `blank` makes one-color pictures."""

    def __init__(self, blank=False, fail=False):
        self.blank, self.fail = blank, fail
        self.calls: list[dict] = []

    def __call__(self, bbox=None, all_screens=False):
        self.calls.append({"bbox": bbox, "all_screens": all_screens})
        if self.fail:
            raise OSError("screen grab blocked")
        if self.blank:
            return Image.new("RGB", (64, 48), (20, 20, 20))
        if bbox is not None:
            return patterned((bbox[2] - bbox[0], bbox[3] - bbox[1]))
        return patterned()


class FakeUia:
    def __init__(self):
        self.calls: list[list] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, UIA_JSON, "")
