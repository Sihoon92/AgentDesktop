"""A fake desktop whose windows come and go on a fake clock, for the ready wait.

TimelineDesktop answers `launch`, `list-windows` and `snapshot --window-id` from a list of
FakeWindow, each present from `appear` until `gone` and showing the tree whose start time
was reached last. A window with `appear` of 0 or later belongs to the launch: it is not there
until `launch` has run. `sleep` moves the clock forward, so a test never really sleeps.
"""

import math

SIGNAL = "생산운영"


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def node(name=None, role="text", children=()):
    found = {"role": role, "children": list(children)}
    if name is not None:
        found["name"] = name
    return found


def window_tree(title, *names, deep=False):
    """A window root titled `title` whose children carry `names`. With deep, each name sits
    three levels down."""
    children = []
    for name in names:
        leaf = node(name, role="menuitem")
        children.append(node(role="pane", children=[node(role="group", children=[leaf])]) if deep else leaf)
    return node(title, role="window", children=children)


class FakeWindow:
    def __init__(self, window_id, pid, instance=None, title="", appear=-1.0, gone=math.inf, trees=None,
                 minimized=False):
        self.id = window_id
        self.pid = pid
        self.instance = instance if instance is not None else f"proc:{pid}"
        self.title = title
        self.appear = appear
        self.gone = gone
        self.minimized = minimized
        self.trees = sorted(trees or [(-math.inf, window_tree(title))], key=lambda item: item[0])

    def present(self, now, launched=True):
        return self.appear <= now < self.gone and (launched or self.appear < 0)

    def row(self):
        return {"accessible": True, "app_name": "Fake.exe", "bounds": {"x": 0.0, "y": 0.0, "width": 800.0,
                "height": 600.0}, "id": self.id, "is_focused": False, "minimized": self.minimized,
                "pid": self.pid, "process_instance": self.instance, "title": self.title, "visible": True}

    def tree(self, now):
        shown = None
        for start, tree in self.trees:
            if start <= now:
                shown = tree
        return shown


class TimelineDesktop:
    """`launch_pid` and `launch_instance` are what launch returns; `launch_error` makes it fail.
    `snapshot_s` is how long one snapshot takes on the fake clock. `failing` names windows whose
    snapshot fails. `on_launch(desktop, now)`, when given, runs at each launch before it answers,
    so a test can open the windows of a second launch."""

    def __init__(self, clock, windows, launch_pid=100, launch_instance=None, launch_error=None,
                 snapshot_s=0.0, failing=(), on_launch=None):
        self.clock = clock
        self.windows = list(windows)
        self.launch_pid = launch_pid
        self.launch_instance = launch_instance if launch_instance is not None else f"proc:{launch_pid}"
        self.launch_error = launch_error
        self.snapshot_s = snapshot_s
        self.failing = set(failing)
        self.on_launch = on_launch
        self.calls = []
        self.closed = []
        self.launched = False

    def commands(self, word):
        return [call for call in self.calls if call[0] == word]

    def snapshots_of(self, window_id):
        return [call for call in self.commands("snapshot") if window_id in call]

    def run(self, *args, headed=False):
        self.calls.append(args)
        now = self.clock()
        if args[0] == "launch":
            return self._launch(now)
        if args[0] == "list-windows":
            return {"ok": True, "command": "list-windows",
                    "data": [window.row() for window in self.windows if window.present(now, self.launched)]}
        if args[0] == "snapshot":
            return self._snapshot(args)
        return {"ok": False, "command": args[0], "error": {"code": "INVALID_ARGS", "message": "not faked"}}

    def close_process(self, pid):
        self.closed.append(pid)
        now = self.clock()
        for window in self.windows:
            if window.pid == pid and window.present(now):
                window.gone = now
        return True

    def _launch(self, now):
        if self.on_launch is not None:
            self.on_launch(self, now)
        if self.launch_error is not None:
            return {"ok": False, "command": "launch", "error": dict(self.launch_error)}
        self.launched = True
        mine = next((window for window in self.windows if window.pid == self.launch_pid
                     and window.instance == self.launch_instance and window.present(now)), None)
        data = {"app": "Fake.exe", "pid": self.launch_pid, "process_instance": self.launch_instance}
        if mine is not None:
            data["window"] = mine.row()
        return {"ok": True, "command": "launch", "data": data}

    def _snapshot(self, args):
        window_id = args[args.index("--window-id") + 1]
        self.clock.now += self.snapshot_s
        now = self.clock()
        window = next((window for window in self.windows if window.id == window_id), None)
        if window is None or not window.present(now, self.launched) or window_id in self.failing:
            return {"ok": False, "command": "snapshot",
                    "error": {"code": "WINDOW_NOT_FOUND", "message": "gone"}}
        return {"ok": True, "command": "snapshot",
                "data": {"complete": True, "ref_count": 0, "tree": window.tree(now)}}
