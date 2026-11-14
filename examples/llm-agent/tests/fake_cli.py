"""A stand-in for the agent-desktop binary that replays envelopes captured from the real one."""

import base64
import copy
import json
import subprocess
from pathlib import Path

from agent.frames import frame_origin

try:
    from PIL import Image
except ImportError:
    Image = None

FIXTURES = Path(__file__).parent / "fixtures"
GLOBAL_VALUE_FLAGS = {"--session", "--trace"}
GLOBAL_SWITCHES = {"--headed"}
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="
)
MEASURED_SCREENSHOT_SIZE = (1896, 2060)
READ_COMMANDS = {"launch", "list-windows", "screenshot", "snapshot", "focus-window"}
OTHER_INSTANCE_WINDOW = {
    "accessible": True,
    "app_name": "FootprintChart.exe",
    "bounds": {"height": 900.0, "width": 900.0, "x": 5000.0, "y": 0.0},
    "id": "w-999",
    "is_focused": False,
    "minimized": False,
    "pid": 49744,
    "process_instance": "windows-proc-v1:other",
    "title": "other instance",
    "visible": True,
}


def _load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def strip_globals(argv):
    """Returns the command words of a recorded call, without the binary and global flags."""
    words = []
    rest = iter(argv[1:])
    for word in rest:
        if word in GLOBAL_VALUE_FLAGS:
            next(rest, None)
        elif word not in GLOBAL_SWITCHES:
            words.append(word)
    return words


def scene_window(window_id, title, bounds, **extra):
    """A list-windows row of the launched process for FakeCli(scene=...)."""
    launched = _load("launch")["data"]
    return {"accessible": True, "app_name": "Scene.exe", "bounds": dict(bounds), "id": window_id,
            "is_focused": True, "minimized": False, "pid": launched["pid"],
            "process_instance": launched["process_instance"], "title": title, "visible": True, **extra}


def _is_element(node, is_window_root):
    has_text = any(node.get(key) for key in ("name", "description", "value"))
    return bool(node.get("ref_id") or (has_text and not is_window_root))


def _marked_nodes(tree):
    """The tree's nodes in the order agent.observe.render_marked numbers them."""
    found = []

    def walk(node, is_root):
        if _is_element(node, is_root and node.get("role") == "window"):
            found.append(node)
        for child in node.get("children", []):
            walk(child, False)

    walk(tree, True)
    return found


class FakeCli:
    def __init__(self, panel_open=False, scene=None):
        """scene, when given, replaces the captured FootprintChart replies with
        {"tree": tree, "windows": [rows]}: snapshots return that tree (or scene["trees"][id] for
        the window snapshotted, when given), list-windows those rows top first,
        screenshots a real PNG of each window's visible size (in screenshot_colors[id], or light
        gray, with screenshot_patches[id] = [(screen rect, color)] painted on it;
        failing_screenshots ids fail), and mouse-click calls on_click(x, y). Any command that
        is not a read (READ_COMMANDS) first calls on_command(name) when it is set."""
        self.scene = copy.deepcopy(scene) if scene is not None else None
        self.on_click = None
        self.on_command = None
        self.calls = []
        self.panel_open = panel_open
        self.focused = True
        self.process_alive = True
        self.instance = _load("launch")["data"]["process_instance"]
        self.action_results = {}
        self.stdout_override = None
        self.screenshot_result = None
        self.move_window_on_screenshot = False
        self.screenshot_colors = {}
        self.screenshot_patches = {}
        self.failing_screenshots = set()
        self.window_shift = 0
        self.snapshots = 0
        self.open_panel_on_snapshot = None

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[0] == "taskkill":
            self.process_alive = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if self.stdout_override is not None:
            return subprocess.CompletedProcess(argv, 1, self.stdout_override, "")
        envelope = self._respond(strip_globals(argv))
        code = 0 if envelope["ok"] else 1
        return subprocess.CompletedProcess(argv, code, json.dumps(envelope), "")

    def commands(self):
        return [strip_globals(call) for call in self.calls if call[0] != "taskkill"]

    def command_names(self):
        return [words[0] for words in self.commands()]

    def full_call(self, name):
        return next(c for c in self.calls if c[0] != "taskkill" and strip_globals(c)[0] == name)

    def _respond(self, words):
        name = words[0]
        if self.on_command is not None and name not in READ_COMMANDS:
            self.on_command(name)
        if name == "launch":
            self.process_alive = True
            launched = _load("launch")
            if self.scene is not None:
                launched["data"]["window"] = dict(self.scene["windows"][0])
            return launched
        if name == "list-windows":
            return self._windows()
        if name == "screenshot":
            return self._screenshot(words)
        if self.scene is not None and name == "snapshot":
            self.snapshots += 1
            window_id = words[words.index("--window-id") + 1] if "--window-id" in words else None
            tree = self.scene.get("trees", {}).get(window_id, self.scene["tree"])
            return {"version": "2.6", "ok": True, "command": "snapshot",
                    "data": {"snapshot_id": f"s{self.snapshots}", "complete": True,
                             "tree": copy.deepcopy(tree)}}
        if self.scene is not None and name == "mouse-click":
            xy = next(word for word in words if word.startswith("--xy="))[len("--xy="):]
            x, y = (int(value) for value in xy.split(","))
            if self.on_click is not None:
                self.on_click(x, y)
            return _load("toggle")
        if name == "snapshot":
            self.snapshots += 1
            if self.snapshots == self.open_panel_on_snapshot:
                self.panel_open = True
            if self.panel_open:
                return _load("snapshot_settings_open")
            if "--include-bounds" in words:
                return _load("snapshot_settings_closed_bounds")
            return _load("snapshot_settings_closed")
        if name in ("toggle", "check"):
            self.panel_open = True
        if name == "uncheck":
            self.panel_open = False
        if name in self.action_results:
            return self.action_results[name]
        return _load("toggle")

    def open_window(self, row):
        """Scene mode: a new window of the process appears on top, so it is listed first."""
        self.scene["windows"].insert(0, dict(row))

    def close_window(self, window_id):
        """Scene mode: a window of the process goes away."""
        self.scene["windows"] = [row for row in self.scene["windows"] if row["id"] != window_id]

    def focus(self, mark):
        """Scene mode: the element numbered `mark` takes focus, as a caret placed in it would."""
        for node in _marked_nodes(self.scene["tree"]):
            node["states"] = [s for s in node.get("states", []) if s != "focused"]
        node = _marked_nodes(self.scene["tree"])[mark - 1]
        node["states"] = node.get("states", []) + ["focused"]

    def _scene_screenshot(self, words):
        window_id = words[words.index("--window-id") + 1]
        if window_id in self.failing_screenshots:
            return {"version": "2.6", "ok": False, "command": "screenshot",
                    "error": {"code": "WINDOW_NOT_FOUND", "message": "gone"}}
        row = next(r for r in self.scene["windows"] if r["id"] == window_id)
        width, height = int(row["bounds"]["width"]) - 16, int(row["bounds"]["height"]) - 8
        path = Path(words[-1])
        image = Image.new("RGB", (width, height), self.screenshot_colors.get(window_id, (240, 240, 240)))
        origin = frame_origin(row["bounds"], width, height)
        for rect, color in self.screenshot_patches.get(window_id, []):
            left, top = int(rect["x"] - origin[0]), int(rect["y"] - origin[1])
            image.paste(color, (left, top, left + rect["width"], top + rect["height"]))
        image.save(path)
        data = {"path": str(path), "width": width, "height": height, "scale_factor": 1.0}
        return {"version": "2.6", "ok": True, "command": "screenshot", "data": data}

    def _screenshot(self, words):
        if self.move_window_on_screenshot:
            self.window_shift += 100
        if self.screenshot_result is not None:
            return self.screenshot_result
        if self.scene is not None:
            return self._scene_screenshot(words)
        path = Path(words[-1])
        path.write_bytes(ONE_PIXEL_PNG)
        width, height = MEASURED_SCREENSHOT_SIZE
        data = {"path": str(path), "width": width, "height": height, "scale_factor": 1.75}
        return {"version": "2.6", "ok": True, "command": "screenshot", "data": data}

    def _windows(self):
        envelope = _load("windows")
        if self.scene is not None:
            envelope["data"] = copy.deepcopy(self.scene["windows"])
        rows = envelope["data"] if self.process_alive else []
        for row in rows:
            row["process_instance"] = self.instance
            row["is_focused"] = self.focused
            row["bounds"]["x"] += self.window_shift
        envelope["data"] = rows + [OTHER_INSTANCE_WINDOW]
        return envelope
