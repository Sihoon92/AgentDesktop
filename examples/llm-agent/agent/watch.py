"""The watcher: what changed among the visible top-level windows between two observations.

`Watcher.step` is pure over the observations it is given, so tests drive it with made-up
window lists. Events carry only window and process numbers and codes: the family of the class
name, the kind, the owner's number, modality and a size class, never a title or a rectangle.
The session's own windows (its button, its console) make no event and are never remembered as
the foreground.

The first observation is the baseline: the windows already open then get no `window_shown`, so
a later event always means the window appeared during the session. Its foreground and its hung
application windows are still recorded.
"""

import threading
from collections.abc import Callable
from typing import NamedTuple

from agent.app_processes import AppTracker
from agent.survey_codes import SURVEY_FOREGROUND_S, SURVEY_POLL_S
from agent.survey_facts import Event
from agent.toolkits import class_family, is_dialog_class
from agent.win32_facts import WindowRow

SMALL_SHARE = 0.1
LARGE_SHARE = 0.6
BEFORE_SESSION = float("-inf")


class Observation(NamedTuple):
    t: float
    windows: tuple[WindowRow, ...]
    foreground: int | None
    hung: frozenset[int]


def size_class(rect, screen: tuple[int, int]) -> str:
    """S under a tenth of the screen's area, L from six tenths, M between."""
    left, top, right, bottom = rect
    screen_area = screen[0] * screen[1]
    if screen_area <= 0:
        return "M"
    share = max(0, right - left) * max(0, bottom - top) / screen_area
    if share < SMALL_SHARE:
        return "S"
    if share >= LARGE_SHARE:
        return "L"
    return "M"


def window_kind_code(row: WindowRow) -> str:
    if is_dialog_class(row.class_name):
        return "DIALOG"
    if row.owner:
        return "OWNED"
    return "TOP"


class Watcher:
    def __init__(self, tracker: AppTracker, own_pids: frozenset[int], screen: tuple[int, int]):
        self.tracker = tracker
        self.own_pids = frozenset(own_pids)
        self.screen = screen
        self.last_foreground: int | None = None
        self.first_app_window_t: float | None = None
        self.first_window_t: dict[str, float] = {}
        self.max_visible_top: dict[str, int] = {}
        self.capture_mark = 0
        self._lock = threading.RLock()
        self._numbers: dict[int, str] = {}
        self._hwnds: dict[str, int] = {}
        self._visible: dict[int, float] = {}
        self._hung: set[int] = set()
        self._foreground_rel: str | None = None
        self._shown: list[int] = []
        self._started = False

    def number(self, hwnd: int) -> str:
        with self._lock:
            if hwnd not in self._numbers:
                win = f"W{len(self._numbers) + 1}"
                self._numbers[hwnd] = win
                self._hwnds[win] = hwnd
            return self._numbers[hwnd]

    def hwnd_of(self, win: str) -> int | None:
        with self._lock:
            return self._hwnds.get(win)

    def mark(self) -> int:
        with self._lock:
            return len(self._shown)

    def shown_since(self, mark: int) -> list[int]:
        """Windows that appeared after `mark` and are still visible, in the order they appeared."""
        with self._lock:
            return [hwnd for hwnd in dict.fromkeys(self._shown[mark:]) if hwnd in self._visible]

    def step(self, obs: Observation) -> list[Event]:
        with self._lock:
            events: list[Event] = []
            rows = {row.hwnd: row for row in obs.windows
                    if row.visible and not row.minimized and row.pid not in self.own_pids}
            for hwnd in rows:
                self.number(hwnd)
            for hwnd in [hwnd for hwnd in self._visible if hwnd not in rows]:
                if hwnd in self._hung:
                    self._hung.discard(hwnd)
                    events.append(Event(obs.t, "hung_end", win=self.number(hwnd)))
                del self._visible[hwnd]
                events.append(Event(obs.t, "window_hidden", win=self.number(hwnd)))
            for hwnd, row in rows.items():
                if hwnd not in self._visible:
                    self._visible[hwnd] = obs.t if self._started else BEFORE_SESSION
                    if self._started:
                        self._shown.append(hwnd)
                        events.append(self._shown_event(obs.t, row))
                self._note_first_window(obs.t, row)
            events.extend(self._hung_events(obs, rows))
            events.extend(self._foreground_events(obs, rows))
            self._count_tops(rows)
            self._started = True
            return events

    def _shown_event(self, t: float, row: WindowRow) -> Event:
        return Event(t, "window_shown", win=self.number(row.hwnd), proc=self.tracker.proc(row.pid),
                     rel=self.tracker.rel(row.pid), family=class_family(row.class_name) or "?",
                     kind=window_kind_code(row), owned=self.number(row.owner) if row.owner else None,
                     modal=not row.owner_enabled, size=size_class(row.rect, self.screen))

    def _note_first_window(self, t: float, row: WindowRow) -> None:
        proc = self.tracker.proc(row.pid)
        if proc and proc not in self.first_window_t:
            self.first_window_t[proc] = t
        if self.first_app_window_t is None and self.tracker.is_app(row.pid):
            self.first_app_window_t = t

    def _hung_events(self, obs: Observation, rows: dict[int, WindowRow]) -> list[Event]:
        events = []
        for hwnd, row in rows.items():
            if not self.tracker.is_app(row.pid):
                continue
            hung = hwnd in obs.hung
            if hung and hwnd not in self._hung:
                self._hung.add(hwnd)
                events.append(Event(obs.t, "hung_start", win=self.number(hwnd)))
            elif not hung and hwnd in self._hung:
                self._hung.discard(hwnd)
                events.append(Event(obs.t, "hung_end", win=self.number(hwnd)))
        return events

    def _foreground_events(self, obs: Observation, rows: dict[int, WindowRow]) -> list[Event]:
        hwnd = obs.foreground
        if not hwnd or hwnd == self.last_foreground:
            return []
        row = rows.get(hwnd) or next((item for item in obs.windows if item.hwnd == hwnd), None)
        if row is not None and row.pid in self.own_pids:
            return []
        rel = self.tracker.rel(row.pid) if row is not None else "OTHER"
        shown_t = self._visible.get(hwnd, BEFORE_SESSION)
        within = self._foreground_rel if obs.t - shown_t <= SURVEY_FOREGROUND_S else None
        self.last_foreground = hwnd
        self._foreground_rel = rel
        return [Event(obs.t, "foreground", win=self.number(hwnd), rel=rel, fg_within=within)]

    def _count_tops(self, rows: dict[int, WindowRow]) -> None:
        counts: dict[str, int] = {}
        for row in rows.values():
            proc = self.tracker.proc(row.pid)
            if proc and not row.owner and self.tracker.is_app(row.pid):
                counts[proc] = counts.get(proc, 0) + 1
        for proc, count in counts.items():
            self.max_visible_top[proc] = max(self.max_visible_top.get(proc, 0), count)


class WatchThread:
    """Calls `observe`, steps the watcher and hands non-empty event lists to `on_events`, every
    `poll_s` seconds until stopped. An exception in one round is dropped; the next round tries again."""

    def __init__(self, watcher: Watcher, observe: Callable[[], Observation],
                 on_events: Callable[[list[Event]], None], poll_s: float = SURVEY_POLL_S):
        self.watcher = watcher
        self.observe = observe
        self.on_events = on_events
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="survey-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                events = self.watcher.step(self.observe())
                if events:
                    self.on_events(events)
            except Exception:
                pass
            self._stop.wait(self.poll_s)
