"""A stand-in for the agent-desktop binary that replays envelopes captured from the real one."""

import base64
import json
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
GLOBAL_VALUE_FLAGS = {"--session", "--trace"}
GLOBAL_SWITCHES = {"--headed"}
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="
)
MEASURED_SCREENSHOT_SIZE = (1896, 2060)
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


class FakeCli:
    def __init__(self, panel_open=False):
        self.calls = []
        self.panel_open = panel_open
        self.focused = True
        self.process_alive = True
        self.instance = _load("launch")["data"]["process_instance"]
        self.action_results = {}
        self.stdout_override = None
        self.screenshot_result = None
        self.move_window_on_screenshot = False
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
        if name == "launch":
            self.process_alive = True
            return _load("launch")
        if name == "list-windows":
            return self._windows()
        if name == "screenshot":
            return self._screenshot(words)
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

    def _screenshot(self, words):
        if self.move_window_on_screenshot:
            self.window_shift += 100
        if self.screenshot_result is not None:
            return self.screenshot_result
        path = Path(words[-1])
        path.write_bytes(ONE_PIXEL_PNG)
        width, height = MEASURED_SCREENSHOT_SIZE
        data = {"path": str(path), "width": width, "height": height, "scale_factor": 1.75}
        return {"version": "2.6", "ok": True, "command": "screenshot", "data": data}

    def _windows(self):
        envelope = _load("windows")
        rows = envelope["data"] if self.process_alive else []
        for row in rows:
            row["process_instance"] = self.instance
            row["is_focused"] = self.focused
            row["bounds"]["x"] += self.window_shift
        envelope["data"] = rows + [OTHER_INSTANCE_WINDOW]
        return envelope
