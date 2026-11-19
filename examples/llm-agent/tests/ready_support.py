"""A fake desktop whose windows come and go on a fake clock, for the ready wait.

TimelineDesktop answers `launch`, `list-windows`, `snapshot --window-id` and `find --window-id`
from a list of FakeWindow, each present from `appear` until `gone` and showing the tree whose
start time was reached last. A window with `appear` of 0 or later belongs to the launch: it is
not there until `launch` has run. `sleep` moves the clock forward, so a test never really sleeps.

`find` answers in the CLI's shape (crates/core/src/commands/find_live.rs `single_match_response`,
crates/core/src/locator/live_locator/locator_match_data.rs and select.rs `match_data`):
`data.match` is null or {"ref_id", "role", "name" (left out when empty), "value", "states",
"interactive", "path"} with "bounds" when there is no ref. The match carries no native id, and
`path` holds the labels of the ancestors from the window root, so the root itself has an empty
path. Like the CLI, `--exact` ignores case and folds inner runs of spaces, `--native-id` is
compared letter for letter, and the root window is searched too.
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
        self.advance(seconds)

    def advance(self, seconds):
        """Moves the clock, rounded to a nanosecond so ten sleeps of 0.3 s end at 3.0 s."""
        self.now = round(self.now + seconds, 9)


def node(name=None, role="text", children=(), native=None, **extra):
    found = {"role": role, "children": list(children), **extra}
    if name is not None:
        found["name"] = name
    if native is not None:
        found["native_id"] = {"kind": "automation_id", "value": native}
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
    `snapshot_s`, `find_s` and `list_s` are how long one snapshot, find or window list takes on the
    fake clock.
    `failing` names windows whose snapshot and find fail; `snapshot_failing(desktop, window_id)`,
    when given, fails a snapshot it returns true for. `find_timeouts` names windows whose find
    answers TIMEOUT, as the CLI does when its traversal cannot finish. `on_launch(desktop, now)`,
    when given, runs at each launch before it answers, so a test can open the windows of a
    second launch."""

    def __init__(self, clock, windows, launch_pid=100, launch_instance=None, launch_error=None,
                 snapshot_s=0.0, failing=(), on_launch=None, find_s=0.0, find_timeouts=(),
                 snapshot_failing=None, list_s=0.0):
        self.clock = clock
        self.windows = list(windows)
        self.launch_pid = launch_pid
        self.launch_instance = launch_instance if launch_instance is not None else f"proc:{launch_pid}"
        self.launch_error = launch_error
        self.snapshot_s = snapshot_s
        self.failing = set(failing)
        self.on_launch = on_launch
        self.find_s = find_s
        self.find_timeouts = set(find_timeouts)
        self.snapshot_failing = snapshot_failing
        self.list_s = list_s
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
            self.clock.advance(self.list_s)
            now = self.clock()
            return {"ok": True, "command": "list-windows",
                    "data": [window.row() for window in self.windows if window.present(now, self.launched)]}
        if args[0] == "snapshot":
            return self._snapshot(args)
        if args[0] == "find":
            return self._find(args)
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
        self.clock.advance(self.snapshot_s)
        now = self.clock()
        window = next((window for window in self.windows if window.id == window_id), None)
        failing = self.snapshot_failing is not None and self.snapshot_failing(self, window_id)
        if window is None or not window.present(now, self.launched) or window_id in self.failing or failing:
            return {"ok": False, "command": "snapshot",
                    "error": {"code": "WINDOW_NOT_FOUND", "message": "gone"}}
        return {"ok": True, "command": "snapshot",
                "data": {"complete": True, "ref_count": 0, "tree": window.tree(now)}}

    def _find(self, args):
        window_id = args[args.index("--window-id") + 1]
        self.clock.advance(self.find_s)
        now = self.clock()
        window = next((window for window in self.windows if window.id == window_id), None)
        if window is None or not window.present(now, self.launched) or window_id in self.failing:
            return {"ok": False, "command": "find", "error": {"code": "WINDOW_NOT_FOUND", "message": "gone"}}
        if window_id in self.find_timeouts:
            return {"ok": False, "command": "find",
                    "error": {"code": "TIMEOUT", "message": "Locator traversal did not produce an authoritative result"}}
        wanted = {flag: args[args.index(flag) + 1] for flag in ("--name", "--native-id", "--role") if flag in args}
        found = _first_match(window.tree(now), wanted, "--exact" in args)
        data = {"match": found}
        if found is None and "--role" in wanted:
            data["roles_present"] = []
        return {"ok": True, "command": "find", "data": data}


def _folded(text):
    return " ".join(str(text).split()).casefold()


def _native(found):
    native = found.get("native_id")
    return native.get("value") if isinstance(native, dict) else native


def _fits(found, wanted, exact):
    if "--role" in wanted and found.get("role") != wanted["--role"]:
        return False
    if "--native-id" in wanted and _native(found) != wanted["--native-id"]:
        return False
    if "--name" in wanted:
        name = found.get("name")
        if not isinstance(name, str):
            return False
        if exact and _folded(name) != _folded(wanted["--name"]):
            return False
        if not exact and _folded(wanted["--name"]) not in _folded(name):
            return False
    return True


def _label(found):
    label = found.get("name") or found.get("value")
    return f"{found.get('role')}:{label}" if label else str(found.get("role"))


def _first_match(tree, wanted, exact):
    """The first node in depth-first order, the root included, as the CLI's `--first` match."""
    pending = [(tree, [])] if isinstance(tree, dict) else []
    while pending:
        found, ancestors = pending.pop(0)
        if _fits(found, wanted, exact):
            match = {"ref_id": found.get("ref_id"), "role": found.get("role"), "value": found.get("value"),
                     "states": list(found.get("states") or []), "interactive": found.get("ref_id") is not None,
                     "path": [_label(ancestor) for ancestor in ancestors]}
            if found.get("name"):
                match["name"] = found["name"]
            if found.get("ref_id") is None and found.get("bounds"):
                match["bounds"] = found["bounds"]
            return match
        pending[0:0] = [(child, ancestors + [found]) for child in found.get("children") or [] if isinstance(child, dict)]
    return None

