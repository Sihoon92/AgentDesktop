"""Which processes are the application's, and how every other process relates to it.

A process is known by its (pid, start time) pair, so a reused pid is a new process. The
application's processes are those whose executable lies in the target executable's folder or
an extra executable's folder (a launcher's real application in the same folder is found even
after the launcher exits). A process an application process starts outside those folders is
CHILD_OTHER, one under the Windows folder is SYSTEM, one whose executable cannot be read is
UNKNOWN, and every other is OTHER. Numbers (`P1`, ...) go only to APP and CHILD_OTHER
processes; nothing here keeps a path anywhere but in memory.
"""

import os
from collections.abc import Iterable
from typing import NamedTuple

from agent.survey_facts import Event
from agent.win32_facts import ProcessRow

NUMBERED_RELS = ("APP", "CHILD_OTHER")
IDLE_PIDS = (0, 4)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _under(path: str, folder: str) -> bool:
    if not path or not folder:
        return False
    return _norm(path).startswith(_norm(folder).rstrip("\\/") + os.sep)


def classify(image: str | None, parent_is_app: bool, app_dirs: tuple[str, ...], windows_dir: str) -> str:
    if image is None:
        return "UNKNOWN"
    if _under(image, windows_dir):
        return "SYSTEM"
    if any(_under(image, folder) for folder in app_dirs):
        return "APP"
    if parent_is_app:
        return "CHILD_OTHER"
    return "OTHER"


class TrackedProcess(NamedTuple):
    """A numbered process. `image` stays in memory on this PC only."""

    proc: str
    pid: int
    started: str | None
    image: str | None
    rel: str
    target: bool
    started_in_session: bool
    alive: bool


class _Record:
    def __init__(self, row, rel, in_session):
        self.pid, self.parent, self.image, self.started = row.pid, row.parent, row.image, row.started
        self.rel = rel
        self.in_session = in_session
        self.proc: str | None = None
        self.alive = True


class AppTracker:
    def __init__(self, target_exe: str, extra_exes: tuple[str, ...], windows_dir: str):
        self.target_exe = target_exe or ""
        self.windows_dir = windows_dir
        folders = [os.path.dirname(path) for path in (self.target_exe, *extra_exes) if path]
        self.app_dirs: tuple[str, ...] = tuple(dict.fromkeys(folders))
        self.already_running = False
        self.picked = False
        self._records: dict[tuple, _Record] = {}
        self._numbered: list[_Record] = []
        self._first = True
        self._unknown: set[tuple] = set()
        self._by_pid: dict[int, _Record] = {}

    @property
    def unknown_paths(self) -> int:
        """Distinct processes whose executable could not be read, among those that appeared
        after the first update (the ones present at start are the PC's protected services)."""
        return len(self._unknown)

    def update(self, rows: Iterable[ProcessRow], t: float) -> list[Event]:
        rows = list(rows)
        events: list[Event] = []
        seen = {(row.pid, row.started) for row in rows}
        for key, record in self._records.items():
            if record.alive and key not in seen:
                record.alive = False
                if self._by_pid.get(record.pid) is record:
                    del self._by_pid[record.pid]
                if record.proc:
                    events.append(Event(t, "process_exited", proc=record.proc, rel=record.rel))
        fresh = [row for row in rows if (row.pid, row.started) not in self._records]
        added = [self._add(row) for row in fresh]
        self._settle(added)
        for record in added:
            if record.image is None and not self._first and record.pid not in IDLE_PIDS:
                self._unknown.add((record.pid, record.started))
            if record.rel in NUMBERED_RELS:
                self._number(record)
                if self._first:
                    self.already_running = self.already_running or record.rel == "APP"
                else:
                    events.append(Event(t, "process_started", proc=record.proc, rel=record.rel))
        self._first = False
        return events

    def _add(self, row) -> _Record:
        record = _Record(row, "OTHER", not self._first)
        self._records[(row.pid, row.started)] = record
        self._by_pid[row.pid] = record
        return record

    def _settle(self, records: list[_Record]) -> None:
        changed = True
        while changed:
            changed = False
            for record in records:
                rel = classify(record.image, self._is_app_pid(record.parent), self.app_dirs, self.windows_dir)
                if record.rel == "APP" and rel != "APP":
                    continue
                if rel != record.rel:
                    record.rel = rel
                    changed = True

    def _is_app_pid(self, pid: int) -> bool:
        record = self._alive(pid)
        return record is not None and record.rel == "APP"

    def _number(self, record: _Record) -> None:
        if record.proc is None:
            self._numbered.append(record)
            record.proc = f"P{len(self._numbered)}"

    def _alive(self, pid: int) -> _Record | None:
        return self._by_pid.get(pid)

    def rel(self, pid: int) -> str:
        record = self._alive(pid)
        return record.rel if record else "OTHER"

    def proc(self, pid: int) -> str | None:
        record = self._alive(pid)
        return record.proc if record else None

    def is_app(self, pid: int) -> bool:
        return self.rel(pid) == "APP"

    def is_target(self, pid: int) -> bool:
        record = self._alive(pid)
        return bool(record and record.image and self.target_exe and _norm(record.image) == _norm(self.target_exe))

    def started_in_session(self, pid: int) -> bool:
        record = self._alive(pid)
        return bool(record and record.in_session)

    def pick(self, pid: int) -> None:
        """Makes this process the application: its folder joins `app_dirs` and every live
        process in that folder becomes APP."""
        record = self._alive(pid)
        if record is None:
            return
        self.picked = True
        if record.image:
            folder = os.path.dirname(record.image)
            if folder and folder not in self.app_dirs:
                self.app_dirs = (*self.app_dirs, folder)
        record.rel = "APP"
        self._number(record)
        live = [item for item in self._records.values() if item.alive and item is not record]
        self._settle(live)
        for item in live:
            if item.rel in NUMBERED_RELS:
                self._number(item)

    def numbered(self) -> tuple[TrackedProcess, ...]:
        """Every numbered process in number order, alive or not."""
        return tuple(TrackedProcess(record.proc or "", record.pid, record.started, record.image, record.rel,
                                    bool(record.image and self.target_exe
                                         and _norm(record.image) == _norm(self.target_exe)),
                                    record.in_session, record.alive)
                     for record in self._numbered)

