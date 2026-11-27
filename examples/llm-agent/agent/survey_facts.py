"""The facts a capture session writes and the readers read: types, folder layout, file I/O.

The collecting side (session, watcher, captures) and the reading side (hypotheses, labels,
block, report) meet only through these types and files. Every type converts to a plain dict
for JSON and back; `from_dict` ignores unknown keys and fills missing ones with defaults, so
an older or newer session folder still loads. No field holds a screen text, a window title,
an element name, a path, or a raw class or module name.
"""

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar, cast

SESSION_JSON = "session.json"
EVENTS_JSONL = "events.jsonl"
LABELS_YAML = "labels.yaml"
NAMES_JSON = "names.json"
SUMMARY_MD = "summary.md"
NOTES_MD = "notes.md"
BLOCK_TXT = "block.txt"
FACTS_JSON = "facts.json"


def _plain(value):
    if isinstance(value, _Record):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _convert(spec, value):
    if value is None or spec is None:
        return value
    shape, record = spec
    if shape == "tuple":
        return tuple(record.from_dict(item) if record else item for item in value)
    return record.from_dict(value)


class _Record:
    """`_NESTED` maps a field name to ("tuple", type-or-None) or ("one", type)."""

    _NESTED: ClassVar[dict] = {}

    def to_dict(self) -> dict:
        return {item.name: _plain(getattr(self, item.name)) for item in fields(cast(Any, self))}

    @classmethod
    def from_dict(cls, d: dict):
        known = {item.name for item in fields(cast(Any, cls))}
        values = {key: _convert(cls._NESTED.get(key), value) for key, value in d.items() if key in known}
        return cls(**values)


@dataclass(frozen=True)
class FamilyCount(_Record):
    families: dict = field(default_factory=dict)
    unknown: int = 0


@dataclass(frozen=True)
class UiaCounts(_Record):
    nodes: int = 0
    truncated: bool = False
    frameworks: dict = field(default_factory=dict)
    patterns: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WindowFact(_Record):
    """One target window of a capture. `family` is a family name or "?", `instance` is the
    CLI's `process_instance`, `kinds` the element counts of `agent.survey.kinds`."""

    win: str = ""
    proc: str | None = None
    instance: str | None = None
    rel: str = "UNKNOWN"
    family: str = "?"
    kind: str = "TOP"
    owned: str | None = None
    modal: bool = False
    hung: bool = False
    size: str = "M"
    read: str = "ERROR"
    read_s: float | None = None
    kinds: dict | None = None
    id_keys: tuple = ()

    _NESTED: ClassVar[dict] = {"id_keys": ("tuple", None)}


@dataclass(frozen=True)
class CaptureFacts(_Record):
    """One capture. `monitor` is f"{w}x{h}@{scale:g}" of the main window's monitor."""

    id: str = ""
    t: float = 0.0
    shot: str = "SHOT_FAILED"
    main: str | None = None
    windows: tuple = ()
    new_since_last: int = 0
    uia: UiaCounts | None = None
    uia_code: str | None = None
    monitor: str | None = None
    monitor_count: int | None = None
    slow: str = "QUEUE_ABANDONED"

    _NESTED: ClassVar[dict] = {"windows": ("tuple", WindowFact), "uia": ("one", UiaCounts)}


@dataclass(frozen=True)
class ProcessFact(_Record):
    proc: str = ""
    rel: str = "UNKNOWN"
    target_exe: bool = False
    started_in_session: bool = False
    bits: int | None = None
    modules: FamilyCount | None = None
    classes: FamilyCount = field(default_factory=FamilyCount)
    mdi: bool = False
    max_visible_top: int = 0
    first_window_t: float | None = None

    _NESTED: ClassVar[dict] = {"modules": ("one", FamilyCount), "classes": ("one", FamilyCount)}


@dataclass(frozen=True)
class CheckFact(_Record):
    """Name, status and code of one `checks.Result`."""

    name: str = ""
    status: str = ""
    code: str = ""


@dataclass(frozen=True)
class SessionFacts(_Record):
    """`hotkey` is "OK" or "HOTKEY_TAKEN". `saved_ids` are the keys read at start from
    `.check/<system>.json`, `saved_ids_process` that record's process mark."""

    version: int = 0
    system: str = ""
    started: str = ""
    duration_s: float = 0.0
    head: str | None = None
    changed: int = 0
    hotkey: str = "OK"
    app_seen: str = "NOT_SEEN"
    first_app_window_t: float | None = None
    unknown_paths: int = 0
    checks: tuple = ()
    processes: tuple = ()
    captures: tuple = ()
    saved_ids: tuple | None = None
    saved_ids_process: str | None = None

    _NESTED: ClassVar[dict] = {"checks": ("tuple", CheckFact), "processes": ("tuple", ProcessFact),
                               "captures": ("tuple", None), "saved_ids": ("tuple", None)}


@dataclass(frozen=True)
class Event(_Record):
    """One watcher line. `to_dict` leaves out the fields that are None."""

    t: float = 0.0
    event: str = ""
    win: str | None = None
    proc: str | None = None
    rel: str | None = None
    family: str | None = None
    kind: str | None = None
    owned: str | None = None
    modal: bool | None = None
    size: str | None = None
    fg_within: str | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in super().to_dict().items() if value is not None}


@dataclass(frozen=True)
class Labels(_Record):
    """Scene codes by capture id and human answers by hypothesis id."""

    captures: dict = field(default_factory=dict)
    manual: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedSession:
    folder: Path
    session: SessionFacts
    captures: tuple
    events: tuple


def capture_id(n: int) -> str:
    return f"C{n:02d}"


def capture_dir(folder: Path, cid: str) -> Path:
    return Path(folder) / f"capture-{cid[1:]}"


def write_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def append_event(path: Path, event: Event) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def _abandoned(cid: str) -> CaptureFacts:
    return CaptureFacts(id=cid, t=0.0, shot="SHOT_FAILED", main=None, windows=(), new_since_last=0,
                        uia=None, uia_code=None, monitor=None, monitor_count=None, slow="QUEUE_ABANDONED")


def _read_events(path: Path) -> tuple:
    if not path.is_file():
        return ()
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(Event.from_dict(json.loads(line)))
        except (ValueError, TypeError):
            continue
    return tuple(events)


def load_session(folder: Path) -> LoadedSession:
    """Raises FileNotFoundError without `session.json`. A capture whose `facts.json` is
    missing (only its fast phase ran) loads as QUEUE_ABANDONED. A torn last event line,
    left by a session killed mid-write, is skipped."""
    folder = Path(folder)
    session = SessionFacts.from_dict(json.loads((folder / SESSION_JSON).read_text(encoding="utf-8")))
    captures = []
    for cid in session.captures:
        facts = capture_dir(folder, cid) / FACTS_JSON
        if facts.is_file():
            captures.append(CaptureFacts.from_dict(json.loads(facts.read_text(encoding="utf-8"))))
        else:
            captures.append(_abandoned(cid))
    return LoadedSession(folder, session, tuple(captures), _read_events(folder / EVENTS_JSONL))


def capture_codes(capture: CaptureFacts) -> tuple[str, str, str]:
    """(kind, rel, state) of the capture's main window; ("TOP", "UNKNOWN", "GONE") without one."""
    main = next((window for window in capture.windows if window.win == capture.main), None)
    if capture.main is None or main is None:
        return ("TOP", "UNKNOWN", "GONE")
    if capture.shot == "SHOT_BLANK":
        state = "BLANK"
    elif main.read == "GONE":
        state = "GONE"
    elif main.hung or main.read == "UNRESPONSIVE":
        state = "HUNG"
    elif main.read == "TIMEOUT":
        state = "TIMEOUT"
    else:
        state = "OK"
    return (main.kind, main.rel, state)
