"""A fake desktop and screens for the registration screen's server side (viewer/profile_editor.py).

EditorDesktop is the ready wait's TimelineDesktop (tests/ready_support.py) that also answers
`screenshot --window-id W <path>` by writing a PNG of the window's size (one colour per window,
see PlacedWindow), as the CLI does, and
logs every command and every sleep in one list, so a test sees what came before what. A window
listed in `unpictured` cannot be pictured (minimized or hidden), and the screenshot fails.

The screens are what the design shows: a main window whose toolbar (id 12) holds picture-only
menu items (ids 56 and 60), a second toolbar (id 13) that reuses id 56, and a group (id 71) with
children. Names of apps and screen texts are fine here; they never go into agent/ or viewer/.
"""

from pathlib import Path

from PIL import Image

from tests.ready_support import FakeClock, FakeWindow, TimelineDesktop, node

SYSTEM = "MES"
EXE = r"C:\Apps\MES\Mes.exe"
SYSTEMS = {SYSTEM: EXE, "GSCM": r"C:\Apps\GSCM\Gscm.exe"}
ORDER = "Order Management"
TITLE = "MES Main"
OTHER_TITLE = "Private Mail - Inbox"
WINDOW_BOUNDS = {"x": 100.0, "y": 50.0, "width": 800.0, "height": 600.0}
READING_COMMANDS = {"launch", "list-windows", "snapshot", "find", "screenshot"}
MAIN_COLOR = (200, 200, 200)


def box(x, y, width=40.0, height=20.0):
    """Screen bounds inside the main window (which starts at 100, 50)."""
    return {"x": WINDOW_BOUNDS["x"] + x, "y": WINDOW_BOUNDS["y"] + y, "width": width, "height": height}


def item(native, role="image", children=(), at=(10.0, 10.0), **extra):
    return node(role=role, native=native, children=list(children), bounds=box(*at), **extra)


def toolbar(native, *children, at=(0.0, 0.0)):
    return node(role="toolbar", native=native, children=list(children), bounds=box(*at, width=400.0, height=30.0))


def main_screen(*extra):
    """The main window: a named button (the name signal), toolbar 12 with images 56 and 60,
    toolbar 13 with another image 56, and group 71 that holds a text."""
    return node(TITLE, role="window", bounds=dict(WINDOW_BOUNDS), children=[
        node(ORDER, role="button", ref_id="@s1:e1", bounds=box(500.0, 10.0)),
        toolbar("12", item("56", at=(10.0, 5.0)), item("60", at=(60.0, 5.0))),
        toolbar("13", item("56", at=(10.0, 45.0)), at=(0.0, 40.0)),
        node(role="group", native="71", bounds=box(0.0, 100.0, 200.0, 100.0),
             children=[node("상태: 준비", role="text", bounds=box(10.0, 110.0))]),
        *extra,
    ])


class EditorDesktop(TimelineDesktop):
    """`log` holds ("run", args) for each command and ("sleep", seconds) for each sleep of `sleep`."""

    def __init__(self, clock, windows, unpictured=(), **options):
        super().__init__(clock, windows, **options)
        self.unpictured = set(unpictured)
        self.log = []
        self.shots = []

    def sleep(self, seconds):
        self.log.append(("sleep", seconds))
        self.clock.sleep(seconds)

    def run(self, *args, headed=False):
        self.log.append(("run", args))
        if args and args[0] == "screenshot":
            self.calls.append(args)
            return self._screenshot(args)
        return super().run(*args, headed=headed)

    def words(self):
        return {call[0] for call in self.calls}

    def _screenshot(self, args):
        window_id = args[args.index("--window-id") + 1]
        path = Path(args[-1])
        now = self.clock()
        window = next((window for window in self.windows if window.id == window_id), None)
        if window is None or not window.present(now, self.launched) or window_id in self.unpictured:
            return {"ok": False, "command": "screenshot",
                    "error": {"code": "ACTION_FAILED", "message": "the window cannot be captured"}}
        bounds = window.row()["bounds"]
        width, height = int(bounds["width"]), int(bounds["height"])
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, height), getattr(window, "color", MAIN_COLOR)).save(path)
        self.shots.append(path)
        return {"ok": True, "command": "screenshot", "data": {"path": str(path), "width": width, "height": height}}


class MainWindow(FakeWindow):
    """A fake window whose row carries the main window's screen bounds."""

    def row(self):
        return {**super().row(), "bounds": dict(WINDOW_BOUNDS)}


class PlacedWindow(FakeWindow):
    """A fake window at `bounds` on screen whose screenshot is all `color`: a popup menu list is
    a top-level window of its own, listed before the window it covers."""

    def __init__(self, window_id, pid, bounds, color, **options):
        super().__init__(window_id, pid, **options)
        self.bounds = dict(bounds)
        self.color = color

    def row(self):
        return {**super().row(), "bounds": dict(self.bounds)}


def main_window(window_id="w-main", pid=100, appear=0.0, tree=None, title=TITLE, **options):
    return MainWindow(window_id, pid, title=title, appear=appear, trees=[(appear, tree or main_screen())], **options)


def other_app_window():
    """A window of another app, open before the launch: its title must never reach the screen."""
    return MainWindow("w-other", 50, title=OTHER_TITLE, appear=-100.0,
                      trees=[(-100.0, node(OTHER_TITLE, role="window", children=[node("secret", role="text")]))])


def editor_setup(tmp_path, windows=None, editor_class=None, **options):
    """(editor, desktop, clock) over an EditorDesktop of `windows` (the other app and the main
    window by default), with the profile folder `tmp_path / "profiles"`."""
    clock = FakeClock()
    desktop = EditorDesktop(clock, windows if windows is not None else [other_app_window(), main_window()],
                            **options)
    if editor_class is None:
        from viewer.profile_editor import ProfileEditor as editor_class
    editor = editor_class(lambda: desktop, SYSTEMS, tmp_path / "profiles", sleep=desktop.sleep, clock=clock)
    return editor, desktop, clock
