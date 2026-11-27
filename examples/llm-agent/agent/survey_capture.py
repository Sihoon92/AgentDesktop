"""One capture: a fast phase at the signal and a slow phase on the worker thread.

The fast phase saves the whole screen, observes the windows once and picks the target windows:
the foreground window, the windows that appeared since the last capture, then the application's
visible top-level windows. The slow phase reads each target's tree through the CLI (only
`list-windows` and `snapshot`), counts element kinds and id keys, reads modules once per
application process and UIA once per application process, re-takes the main window's picture
at the moment its tree was read for the overlay, and writes `facts.json`.

`facts.json` holds numbers, family names and codes only. Trees, pictures and the window-number
map are company-PC-only files beside it.
"""

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from agent.deep_read import read_window
from agent.handles import window_number
from agent.overlay import draw_overlay
from agent.survey import id_keys, kinds
from agent.survey_codes import SURVEY_MAX_WINDOWS, SURVEY_QUEUE_MAX, SURVEY_SNAPSHOT_MS
from agent.survey_facts import (FACTS_JSON, CaptureFacts, FamilyCount, UiaCounts, WindowFact, capture_dir,
                                write_json)
from agent.toolkits import class_family, count_families, is_dialog_class, is_system_module, module_family
from agent.uia_facts import count_uia
from agent.watch import Observation, Watcher, size_class
from agent.win32_facts import WindowRow

BLANK_SPREAD = 8
TREE_READS = ("OK", "TRUNCATED")
GONE_CODES = ("WINDOW_NOT_FOUND", "STALE_REF", "ELEMENT_NOT_FOUND")
TIMEOUT_CODES = ("TIMEOUT", "HARNESS_TIMEOUT")
MDI_CLASS = "MDIClient"


def pick_targets(foreground: int | None, new_windows: list[int], app_windows: list[int],
                 limit: int = SURVEY_MAX_WINDOWS) -> list[int]:
    ordered = [foreground] if foreground else []
    ordered += list(new_windows) + list(app_windows)
    return [hwnd for hwnd in dict.fromkeys(ordered) if hwnd][:limit]


def is_blank(image) -> bool:
    """True when every RGB channel spans at most BLANK_SPREAD levels: a capture a security
    program painted over, or a locked screen."""
    extrema = image.convert("RGB").getextrema()
    return all(high - low <= BLANK_SPREAD for low, high in extrema)


def read_code(envelope: dict) -> str:
    if envelope.get("ok"):
        data = envelope.get("data") or {}
        return "TRUNCATED" if isinstance(data, dict) and data.get("complete") is False else "OK"
    code = (envelope.get("error") or {}).get("code")
    if code in TIMEOUT_CODES:
        return "TIMEOUT"
    if code == "APP_UNRESPONSIVE":
        return "UNRESPONSIVE"
    if code in GONE_CODES:
        return "GONE"
    return "ERROR"


def _area(row: WindowRow) -> int:
    left, top, right, bottom = row.rect
    return max(0, right - left) * max(0, bottom - top)


def window_kind(row: WindowRow, app_rows: list[WindowRow], is_app: bool) -> str:
    """MAIN for the largest unowned visible application window, then DIALOG, OWNED, TOP."""
    if is_app and not row.owner:
        unowned = [item for item in app_rows if not item.owner and item.visible and not item.minimized]
        if unowned and max(_area(item) for item in unowned) <= _area(row):
            return "MAIN"
    if is_dialog_class(row.class_name):
        return "DIALOG"
    if row.owner:
        return "OWNED"
    return "TOP"


class FastCapture(NamedTuple):
    id: str
    t: float
    shot: str
    foreground: int | None
    targets: tuple[int, ...]
    new_since_last: int
    hung: frozenset[int]
    rows: tuple[WindowRow, ...]


def _shoot(grab, path: Path) -> str:
    try:
        image = grab(all_screens=True)
        image.save(path)
    except Exception:
        return "SHOT_FAILED"
    return "SHOT_BLANK" if is_blank(image) else "OK"


def _shown(row: WindowRow) -> bool:
    return row.visible and not row.minimized


def fast_capture(cid: str, t: float, folder: Path, grab: Callable, watcher: Watcher,
                 observe: Callable[[], Observation], own_pids: frozenset[int]) -> FastCapture:
    target = capture_dir(folder, cid)
    target.mkdir(parents=True, exist_ok=True)
    shot = _shoot(grab, target / "screen.png")
    obs = observe()
    by_hwnd = {row.hwnd: row for row in obs.windows}
    foreground = obs.foreground
    front = by_hwnd.get(foreground) if foreground else None
    if not foreground or (front is not None and front.pid in own_pids):
        foreground = watcher.last_foreground
    new_windows = watcher.shown_since(watcher.capture_mark)
    watcher.capture_mark = watcher.mark()
    app_windows = [row.hwnd for row in obs.windows
                   if _shown(row) and row.pid not in own_pids and watcher.tracker.is_app(row.pid)]
    targets = pick_targets(foreground, new_windows, app_windows)
    return FastCapture(cid, t, shot, foreground, tuple(targets), len(new_windows), obs.hung, obs.windows)


@dataclass
class SlowContext:
    desktop: Any
    win32: Any
    tracker: Any
    watcher: Watcher
    grab: Callable
    uia_runner: Callable
    uia_done: set = field(default_factory=set)
    module_cache: dict = field(default_factory=dict)
    class_tally: dict = field(default_factory=dict)
    unknown_modules: set = field(default_factory=set)
    mdi: set = field(default_factory=set)
    windows_dir: str = ""


def _listed(desktop) -> dict[int, dict] | None:
    """hwnd -> list-windows row, or None when the list itself could not be read."""
    envelope = desktop.run("list-windows")
    rows = envelope.get("data") if envelope.get("ok") else None
    if not isinstance(rows, list):
        return None
    listed = {}
    for row in rows:
        number = window_number(row.get("id")) if isinstance(row, dict) else None
        if number is not None:
            listed[number] = row
    return listed


def _modules(ctx: SlowContext, proc: str, pid: int) -> None:
    if proc in ctx.module_cache:
        return
    paths = ctx.win32.modules_of(pid)
    bits = ctx.win32.process_bits(pid)
    if paths is None:
        ctx.module_cache[proc] = (None, bits)
        return
    families: dict[str, int] = {}
    own = []
    for path in paths:
        name = Path(str(path).replace("\\", "/")).name
        if is_system_module(path, ctx.windows_dir):
            family = module_family(name)
            if family:
                families[family] = families.get(family, 0) + 1
        else:
            own.append(name)
    counted, unknown = count_families(own, module_family)
    for family, count in counted.families.items():
        families[family] = families.get(family, 0) + count
    ctx.unknown_modules.update(unknown)
    ctx.module_cache[proc] = (FamilyCount(families, counted.unknown), bits)


def _tally(ctx: SlowContext, proc: str, row: WindowRow, is_app: bool) -> None:
    children = ctx.win32.child_classes(row.hwnd)
    seen = ctx.class_tally.setdefault(proc, [])
    for name in (row.class_name, *children):
        if name and name not in seen:
            seen.append(name)
    if is_app and MDI_CLASS in children:
        ctx.mdi.add(proc)


def _bbox(listed_row: dict | None, row: WindowRow | None):
    bounds = (listed_row or {}).get("bounds")
    keys = ("x", "y", "width", "height")
    if isinstance(bounds, dict) and all(isinstance(bounds.get(key), (int, float)) for key in keys):
        x, y = int(bounds["x"]), int(bounds["y"])
        return (x, y, x + int(bounds["width"]), y + int(bounds["height"]))
    return row.rect if row is not None else None


def _window_pictures(ctx: SlowContext, target: Path, bbox, tree: dict | None) -> None:
    if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return
    try:
        image = ctx.grab(bbox=bbox, all_screens=True)
        image.save(target / "window.png")
        if tree is not None:
            draw_overlay(image, (bbox[0], bbox[1]), tree, target / "overlay.png")
    except Exception:
        return


def _read_window(ctx: SlowContext, hwnd: int, row: WindowRow | None, listed_row: dict | None, target: Path):
    """(read code, seconds, tree or None, depth facts). The window is read deep with the harness's
    fallback (agent.deep_read); depth facts are {"depth", "cut", "shallow"} for WindowFact, empty
    when the window was gone before it could be read."""
    if listed_row is None or row is None:
        return "GONE", None, None, {}
    started = time.monotonic()
    read = read_window(ctx.desktop, listed_row["id"], SURVEY_SNAPSHOT_MS)
    seconds = round(time.monotonic() - started, 1)
    depth = {"depth": read.depth, "cut": read.cut, "shallow": read.fallback}
    code = read_code(read.envelope)
    tree = (read.envelope.get("data") or {}).get("tree") if code in TREE_READS else None
    if not isinstance(tree, dict):
        return (code if code not in TREE_READS else "ERROR"), seconds, None, depth
    write_json(target / "trees" / f"{ctx.watcher.number(hwnd)}.json", tree)
    return code, seconds, tree, depth


def slow_capture(fast: FastCapture, folder: Path, ctx: SlowContext) -> CaptureFacts:
    target = capture_dir(folder, fast.id)
    listed = _listed(ctx.desktop)
    unlisted = listed is None
    listed = listed or {}
    by_hwnd = {row.hwnd: row for row in fast.rows}
    app_rows = [row for row in fast.rows if _shown(row) and ctx.tracker.is_app(row.pid)]
    windows, mapping = [], {}
    uia: UiaCounts | None = None
    uia_code = monitor = monitor_count = None
    for index, hwnd in enumerate(fast.targets):
        row, listed_row = by_hwnd.get(hwnd), listed.get(hwnd)
        win = ctx.watcher.number(hwnd)
        pid = row.pid if row is not None else int((listed_row or {}).get("pid") or 0)
        proc, is_app = ctx.tracker.proc(pid), ctx.tracker.is_app(pid)
        read = ("ERROR", None, None, {}) if unlisted else _read_window(ctx, hwnd, row, listed_row, target)
        code, seconds, tree, depth = read
        if index == 0 and code != "GONE":
            _window_pictures(ctx, target, _bbox(listed_row, row), tree)
        kind = window_kind(row, app_rows, is_app) if row is not None else "TOP"
        if proc and row is not None:
            _modules(ctx, proc, pid)
            _tally(ctx, proc, row, is_app)
        if index == 0:
            if kind == "MAIN" and proc and proc not in ctx.uia_done:
                ctx.uia_done.add(proc)
                uia, uia_code = count_uia(hwnd, ctx.uia_runner)
            screen = ctx.win32.monitor_of(hwnd)
            if screen is not None:
                monitor, monitor_count = f"{screen.width}x{screen.height}@{screen.scale:g}", screen.count
        mapping[win] = {"hwnd": hwnd, "pid": pid}
        windows.append(WindowFact(
            win=win, proc=proc, instance=(listed_row or {}).get("process_instance"), rel=ctx.tracker.rel(pid),
            family=(class_family(row.class_name) if row is not None else None) or "?", kind=kind,
            owned=ctx.watcher.number(row.owner) if row is not None and row.owner else None,
            modal=bool(row is not None and not row.owner_enabled), hung=hwnd in fast.hung,
            size=size_class(row.rect, ctx.watcher.screen) if row is not None else "M", read=code, read_s=seconds,
            kinds=kinds(tree) if tree is not None else None,
            id_keys=tuple(sorted(id_keys(tree))) if tree is not None else (), **depth))
    facts = CaptureFacts(id=fast.id, t=fast.t, shot=fast.shot, main=windows[0].win if windows else None,
                         windows=tuple(windows), new_since_last=fast.new_since_last, uia=uia, uia_code=uia_code,
                         monitor=monitor, monitor_count=monitor_count, slow="DONE")
    write_json(target / "trees" / "map.json", mapping)
    write_json(target / FACTS_JSON, facts.to_dict())
    return facts


def unread_facts(fast: FastCapture, slow: str) -> CaptureFacts:
    """The facts of a capture whose slow phase did not run: only what the fast phase saw."""
    return CaptureFacts(id=fast.id, t=fast.t, shot=fast.shot, main=None, windows=(),
                        new_since_last=fast.new_since_last, slow=slow)


class CaptureWorker:
    """Runs slow phases one at a time in capture order. At most `queue_max` captures wait."""

    def __init__(self, ctx: SlowContext, folder: Path, on_done: Callable[[CaptureFacts], None],
                 queue_max: int = SURVEY_QUEUE_MAX):
        self.ctx, self.folder, self.on_done, self.queue_max = ctx, Path(folder), on_done, queue_max
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="survey-capture", daemon=True)
        self._current: FastCapture | None = None

    def _write(self, facts: CaptureFacts) -> None:
        write_json(capture_dir(self.folder, facts.id) / FACTS_JSON, facts.to_dict())

    def submit(self, fast: FastCapture) -> bool:
        if self._queue.qsize() >= self.queue_max:
            facts = unread_facts(fast, "QUEUE_FULL")
            self._write(facts)
            self.on_done(facts)
            return False
        self._queue.put(fast)
        return True

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while True:
            fast = self._queue.get()
            if fast is None:
                return
            self._current = fast
            try:
                facts = slow_capture(fast, self.folder, self.ctx)
            except Exception:
                facts = unread_facts(fast, "QUEUE_ABANDONED")
                self._write(facts)
            self._current = None
            self.on_done(facts)

    def drain(self, timeout_s: float) -> list[str]:
        """Waits up to `timeout_s` for the queue to empty, then writes every capture still
        waiting or in progress as QUEUE_ABANDONED and returns their ids."""
        left: list[FastCapture] = []
        if self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout_s)
            if self._thread.is_alive() and self._current is not None:
                left.append(self._current)
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                left.append(item)
        for fast in left:
            self._write(unread_facts(fast, "QUEUE_ABANDONED"))
        return [fast.id for fast in left]
