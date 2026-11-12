"""Locates and runs VirtualMes.exe for the virtual MES tests."""

import json
import os
import subprocess
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

EXE = Path(__file__).resolve().parents[1] / "virtual-mes" / "build" / "VirtualMes.exe"

requires_exe = pytest.mark.skipif(
    not EXE.exists(), reason="VirtualMes.exe is not built; run virtual-mes/build.ps1"
)


def run_exe(*args, exe=EXE):
    return subprocess.run(
        [str(exe), *[str(arg) for arg in args]],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/worksheets/sheet1.xml",
}


def read_xlsx(path):
    """Returns the first sheet as rows of cell text, after checking the workbook parts exist."""
    with zipfile.ZipFile(path) as archive:
        missing = XLSX_PARTS - set(archive.namelist())
        assert not missing, f"not a workbook, missing {sorted(missing)}"
        for part in XLSX_PARTS:
            ElementTree.fromstring(archive.read(part))
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [
        ["".join(text.text or "" for text in cell.iter(f"{SHEET_NS}t")) for cell in row.iter(f"{SHEET_NS}c")]
        for row in sheet.iter(f"{SHEET_NS}row")
    ]


def export(screen, filters, out, exe=EXE):
    return run_exe("--export-headless", screen, json.dumps(filters, ensure_ascii=False), out, exe=exe)


live = pytest.mark.skipif(
    os.environ.get("VIRTUAL_MES_LIVE") != "1",
    reason="opens real windows; set VIRTUAL_MES_LIVE=1 when nobody is using the desktop",
)

REPO = Path(__file__).resolve().parents[3]


def find_agent_desktop():
    """Same order as run.py: AGENT_DESKTOP_BIN, then target/release, then target/debug."""
    configured = os.environ.get("AGENT_DESKTOP_BIN")
    candidates = [Path(configured)] if configured else []
    candidates += [REPO / "target" / kind / "agent-desktop.exe" for kind in ("release", "debug")]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise AssertionError("agent-desktop.exe not found; build it or set AGENT_DESKTOP_BIN")


def _quote(arg):
    text = str(arg)
    return f'"{text}"' if " " in text else text


class LiveApp:
    """Runs a private copy of VirtualMes.exe and drives it through agent-desktop.

    The options go into VirtualMes.args beside the copy, because agent-desktop
    launches the exe without arguments, as the agent does."""

    def __init__(self, folder, *args):
        self.folder = Path(folder)
        self.args = args
        self.log = self.folder / "events.jsonl"
        self.pid = None
        self.instance = None
        self._binary = None
        self._env = None

    def __enter__(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        exe = self.folder / EXE.name
        exe.write_bytes(EXE.read_bytes())
        line = " ".join(_quote(arg) for arg in (*self.args, "--log", self.log))
        (self.folder / "VirtualMes.args").write_text(line, encoding="utf-8")
        self._binary = find_agent_desktop()
        self._env = {**os.environ, "AGENT_DESKTOP_HOME": str(self.folder / "state")}
        data = self.cli("launch", exe)
        self.pid = data["pid"]
        self.instance = data.get("process_instance")
        self.app_name = data.get("app")
        return self

    def __exit__(self, *exc_info):
        if self.pid is None:
            return
        subprocess.run(["taskkill", "/PID", str(self.pid)], capture_output=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self._alive():
            time.sleep(0.1)
        if self._alive():
            subprocess.run(["taskkill", "/F", "/PID", str(self.pid)], capture_output=True)

    def _alive(self):
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {self.pid}", "/NH"], capture_output=True, encoding="oem", errors="replace")
        return str(self.pid) in listed.stdout

    def cli(self, *args):
        """Runs one agent-desktop command and returns its data, failing the test on an error envelope."""
        done = subprocess.run(
            [str(self._binary), *[str(arg) for arg in args]],
            capture_output=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=90, env=self._env)
        envelope = json.loads(done.stdout)
        assert envelope.get("ok"), f"{args}: {json.dumps(envelope.get('error'), ensure_ascii=False)}"
        return envelope.get("data")

    def windows(self):
        return [
            {"id": row["id"], "title": row.get("title") or "", "bounds": row.get("bounds")}
            for row in self.cli("list-windows") or []
            if row.get("pid") == self.pid and row.get("process_instance") == self.instance
        ]

    def main(self):
        return next(window["id"] for window in self.windows() if window["title"] == "MES (가상)")

    def snapshot(self, window_id):
        return self.cli("snapshot", "--window-id", window_id, "--compact", "--include-bounds",
                        "--timeout-ms", "10000")

    def nodes(self, window_id):
        """The window's tree as a flat list with stable keys: role, name, ref, bounds, states, id, value."""
        flat = []

        def visit(node):
            native = node.get("native_id") or {}
            entry = {
                "role": node.get("role"), "name": node.get("name"), "ref": node.get("ref_id"),
                "bounds": node.get("bounds"), "states": node.get("states"), "id": native.get("value"),
                "value": node.get("value"),
            }
            flat.append({key: value for key, value in entry.items() if value not in (None, "", [])})
            for child in node.get("children") or []:
                visit(child)

        visit(self.snapshot(window_id)["tree"])
        return flat

    def all_nodes(self):
        return [node for window in self.windows() for node in self.nodes(window["id"])]

    def find(self, name, role=None):
        for node in self.all_nodes():
            if node.get("name") == name and (role is None or node.get("role") == role):
                return node
        raise AssertionError(f"no element named {name!r}" + (f" with role {role}" if role else ""))

    def click(self, ref):
        return self.cli("click", ref)

    def events(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def focus_elsewhere(self):
        """Moves OS focus to a second, throwaway copy of the app, then closes that copy."""
        self.focus(self.main())
        with LiveApp(self.folder / "elsewhere") as other:
            self.focus(other.main())
            time.sleep(0.3)

    def by_id(self, automation_id):
        for node in self.all_nodes():
            if node.get("id") == automation_id:
                return node
        raise AssertionError(f"no element with id {automation_id!r}")

    def click_outside(self):
        """Presses the real mouse on an empty part of the main window's data panel."""
        self.focus(self.main())
        bounds = next(window["bounds"] for window in self.windows() if window["id"] == self.main())
        x = int(bounds["x"] + bounds["width"] * 0.75)
        y = int(bounds["y"] + bounds["height"] * 0.8)
        self.cli("--headed", "mouse-click", f"--xy={x},{y}")

    def wait_for_event(self, name, count=1, timeout_s=15):
        """Waits until the event log holds `count` events of this name and returns the last of them."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            matches = [event for event in self.events() if event["event"] == name]
            if len(matches) >= count:
                return matches[count - 1]
            time.sleep(0.1)
        raise AssertionError(f"event {name!r} x{count} did not appear within {timeout_s}s")

    def press(self, key):
        """Sends a key to the app's process without focusing it."""
        self.cli("press", key, "--app", self.app_name)

    def wait_for_node(self, predicate, what, timeout_s=10):
        """Polls every window until a node satisfies the predicate."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for node in self.all_nodes():
                if predicate(node):
                    return node
            time.sleep(0.2)
        raise AssertionError(f"{what} did not appear within {timeout_s}s")

    def save_dialog(self, path, confirm_overwrite=False):
        """Types the full path into the standard save dialog and presses its save button.

        The file-name edit has automation id 1001 and the save button 1 in every UI language.
        The path is typed with real keystrokes: the dialog shows a value written with
        set-value but still saves under its default name."""
        def is_name_box(node):
            return node.get("id") == "1001" and node.get("role") == "textfield" and node.get("ref")

        time.sleep(1.0)
        edit = self.wait_for_node(is_name_box, "the save dialog's file name box")
        self.cli("--headed", "type", edit["ref"], str(path))
        typed = self.wait_for_node(is_name_box, "the save dialog's file name box").get("value")
        assert typed == str(path), f"the save dialog holds {typed!r}, not the typed path"
        save = self.wait_for_node(
            lambda node: node.get("id") == "1" and node.get("role") == "button" and node.get("ref"),
            "the save dialog's save button")
        self.click(save["ref"])
        if confirm_overwrite:
            yes = self.wait_for_node(
                lambda node: node.get("role") == "button" and (node.get("name") or "").startswith("예")
                and node.get("ref"), "the overwrite confirmation")
            self.click(yes["ref"])

    def right_click(self, ref):
        """Right-clicks the element with the real mouse; these elements have no semantic context menu."""
        self.focus(self.main())
        self.cli("--headed", "right-click", ref)

    def click_at_center(self, node):
        """Presses the real mouse at the centre of the node's bounds, for elements that have no ref."""
        self.focus(self.main())
        bounds = node["bounds"]
        x = int(bounds["x"] + bounds["width"] / 2)
        y = int(bounds["y"] + bounds["height"] / 2)
        self.cli("--headed", "mouse-click", f"--xy={x},{y}")

    def focus(self, window_id, attempts=4):
        """Brings a window to the foreground.

        Windows can refuse the request, so retry, and between tries minimize and restore
        the window, which hands it the foreground. A final failure names the window that
        kept the foreground."""
        for attempt in range(attempts):
            try:
                return self.cli("focus-window", "--window-id", window_id)
            except AssertionError as error:
                if attempt == attempts - 1:
                    holder = [row.get("title") for row in self.cli("list-windows") or [] if row.get("is_focused")]
                    raise AssertionError(f"{error}; foreground is held by {holder}") from None
                time.sleep(0.5)
                try:
                    self.cli("minimize", "--window-id", window_id)
                    self.cli("restore", "--window-id", window_id)
                except AssertionError:
                    pass

    def wait_for_windows(self, count, timeout_s=8):
        """Waits until the app has exactly this many windows and returns them."""
        deadline = time.monotonic() + timeout_s
        while True:
            windows = self.windows()
            if len(windows) == count:
                return windows
            assert time.monotonic() < deadline, f"expected {count} windows, have {[w['title'] for w in windows]}"
            time.sleep(0.2)

    def right_click_at_center(self, node):
        """Right-clicks the real mouse at the centre of the node's bounds."""
        self.focus(self.main())
        bounds = node["bounds"]
        x = int(bounds["x"] + bounds["width"] / 2)
        y = int(bounds["y"] + bounds["height"] / 2)
        self.cli("--headed", "mouse-click", "--button", "right", f"--xy={x},{y}")

    def request_close(self):
        """Asks the app to close the way the window's close button does."""
        subprocess.run(["taskkill", "/PID", str(self.pid)], capture_output=True)

    def is_running(self):
        return self._alive()
