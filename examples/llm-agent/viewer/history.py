"""Indexes the runs folder for the viewer's run list: summaries, filters, pages and old logs.

Each run.json is read once and kept in memory until its modification time or size
changes, so listing hundreds of runs stats files instead of parsing them. A run.json
that is missing, half written or not an object still lists, as `unreadable`.
Records from before the recorder (`runs/<id>.jsonl`) list as `legacy` and open as
text steps. Nothing here writes to the runs folder.

Recorder run ids are UTC, but old log names are this PC's local time, so every entry
gets a UTC moment (`sort_at`) that orders the list and sets `date`, `since`, `latest`
and the `before` cursor. The cursor is a run id, or `legacy:<id>` for an old log.

`keep` (a `keep` file in the run folder) is read on every request, outside the cache,
and `?keep=1` lists only kept runs.

A run.json still saying `running` is judged on every request, never cached: format 2
is interrupted when its pid is missing, its process is gone or has another start
time (a reused pid), or its heartbeat is more than 60 seconds old; format 1 has none
of these, so it is interrupted once it started more than an hour ago.
"""

import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import PureWindowsPath

from agent.process_identity import process_started_at as live_process_started_at

RUN_ID = re.compile(r"\d{8}-\d{6}(-\d+)?")
CURSOR = re.compile(r"(legacy:)?(\d{8}-\d{6}(?:-\d+)?)")
LEGACY_NAME = re.compile(r"(\d{8}-\d{6}(?:-\d+)?)\.jsonl")
SINCE = re.compile(r"\d{4}-\d{2}-\d{2}")
KNOWN_FORMAT = 2
STATUSES = ("verified", "claimed", "gave_up", "stopped", "running", "interrupted", "unreadable", "legacy")
KEEP_FILE = "keep"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_QUERY_CHARS = 200
LEGACY_TEXT_CHARS = 300
LEGACY_OBSERVATION_CHARS = 4000
HEARTBEAT_LIMIT = timedelta(seconds=60)
FORMAT_1_RUNNING_LIMIT = timedelta(hours=1)


class BadQuery(ValueError):
    """A run-list parameter is invalid; the message says which, in Korean."""


def is_run_id(name):
    return RUN_ID.fullmatch(name) is not None


def run_order(run_id):
    """Orders run ids by the time they name, then by the -N suffix (no time zone applied)."""
    suffix = run_id[16:]
    return (run_id[:15], int(suffix) if suffix else 1)


def run_date(run_id):
    return f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"


def name_time(run_id):
    """The naive time written in a run id, or None when the digits are not a real time."""
    try:
        return datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_query(query):
    """Turns parse_qs output into checked filters, or raises BadQuery."""

    def first(name):
        values = query.get(name)
        return values[0] if values else None

    text = first("q")
    if text is not None and len(text) > MAX_QUERY_CHARS:
        raise BadQuery(f"q는 {MAX_QUERY_CHARS}자 이하여야 합니다")
    status = first("status")
    if status is not None and status not in STATUSES:
        raise BadQuery(f"status는 {', '.join(STATUSES)} 중 하나여야 합니다")
    since = first("since")
    if since is not None:
        try:
            if not SINCE.fullmatch(since):
                raise ValueError(since)
            date.fromisoformat(since)
        except ValueError:
            raise BadQuery("since는 YYYY-MM-DD 날짜여야 합니다") from None
    limit_text = first("limit")
    if limit_text is None:
        limit = DEFAULT_LIMIT
    elif limit_text.isdigit() and 1 <= int(limit_text) <= MAX_LIMIT:
        limit = int(limit_text)
    else:
        raise BadQuery(f"limit은 1부터 {MAX_LIMIT}까지의 정수여야 합니다")
    before = first("before")
    cursor = None
    if before is not None:
        match = CURSOR.fullmatch(before)
        if match is None:
            raise BadQuery("before는 실행 ID 또는 legacy:<실행 ID>여야 합니다")
        cursor = ("legacy" if match.group(1) else "run", match.group(2))
    keep = first("keep")
    if keep is not None and keep != "1":
        raise BadQuery("keep은 1만 쓸 수 있습니다")
    return {"q": text.casefold() if text else None, "status": status, "since": since, "limit": limit, "before": cursor,
            "keep": keep == "1"}


class RunIndex:
    def __init__(self, runs_dir, clock=None, process_started_at=None, local_timezone=None):
        """`clock` returns an aware UTC now; `process_started_at(pid)` is the live start time or None;
        `local_timezone` reads old log names (None: this PC's time zone)."""
        self.runs_dir = runs_dir
        self._local_timezone = local_timezone
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._process_started_at = process_started_at or live_process_started_at
        self._lock = threading.Lock()
        self._cache = {}

    def summaries(self):
        """Every run and old log, newest run id first."""
        with self._lock:
            found = {}
            if self.runs_dir.is_dir():
                for entry in self.runs_dir.iterdir():
                    if is_run_id(entry.name) and entry.is_dir():
                        found[("run", entry.name)] = self._run_summary(entry.name, entry / "run.json")
                    else:
                        match = LEGACY_NAME.fullmatch(entry.name)
                        if match and entry.is_file():
                            found[("legacy", match.group(1))] = self._legacy_summary(match.group(1), entry)
            for key in list(self._cache):
                if key not in found:
                    del self._cache[key]
        ordered = sorted(found.items(), key=lambda item: self.order_key(*item[0]), reverse=True)
        now = self._clock()
        return [self._public(kind, summary, now) for (kind, _), summary in ordered]

    def page(self, filters):
        """Returns (runs, next_before) for checked filters from parse_query."""
        matches = [run for run in self.summaries() if _matches(run, filters)]
        if filters["before"] is not None:
            cursor = self.order_key(*filters["before"])
            matches = [run for run in matches if self.order_key(run["kind"], run["run_id"]) < cursor]
        shown = matches[: filters["limit"]]
        if len(matches) <= len(shown) or not shown:
            return shown, None
        last = shown[-1]
        return shown, (f"legacy:{last['run_id']}" if last["kind"] == "legacy" else last["run_id"])

    def moment(self, kind, run_id):
        """The UTC moment a run id names: recorder ids are UTC, old log names are local time."""
        written = name_time(run_id) or datetime(1970, 1, 1)
        if kind != "legacy":
            return written.replace(tzinfo=timezone.utc)
        if self._local_timezone is not None:
            return written.replace(tzinfo=self._local_timezone).astimezone(timezone.utc)
        try:
            return written.astimezone(timezone.utc)
        except (OverflowError, OSError, ValueError):
            return written.replace(tzinfo=timezone.utc)

    def order_key(self, kind, run_id):
        suffix = run_id[16:]
        return (self.moment(kind, run_id), int(suffix) if suffix else 1, kind == "run")

    def run(self, run_id):
        """The summary of the run folder `run_id`, or None."""
        if not is_run_id(run_id) or not (self.runs_dir / run_id).is_dir():
            return None
        with self._lock:
            summary = self._run_summary(run_id, self.runs_dir / run_id / "run.json")
        return self._public("run", summary, self._clock())

    def legacy(self, run_id):
        """{"run": summary, "steps": [...]} for the old log `run_id`, or None."""
        if not is_run_id(run_id):
            return None
        path = self.runs_dir / f"{run_id}.jsonl"
        if not path.is_file():
            return None
        try:
            lines = _legacy_lines(path)
        except OSError:
            return None
        with self._lock:
            summary = self._legacy_summary(run_id, path)
        return {"run": self._public("legacy", summary, self._clock()), "steps": [_legacy_step(line) for line in lines]}

    def latest(self):
        runs = self.summaries()
        return runs[0] if runs else None

    def _public(self, kind, summary, now):
        """The summary as served: its UTC moment and date, and a dead `running` run as interrupted."""
        public = {key: value for key, value in summary.items() if key != "liveness"}
        moment = self.moment(kind, summary["run_id"])
        public.update(sort_at=_iso(moment), date=moment.date().isoformat())
        public["keep"] = kind == "run" and (self.runs_dir / summary["run_id"] / KEEP_FILE).is_file()
        if kind == "legacy":
            public["started_at"] = public["sort_at"]
        if summary.get("status") != "running":
            return public
        reason = interrupted_reason(summary.get("liveness") or {}, now, self._process_started_at)
        if reason is not None:
            public.update(status="interrupted", interrupted_reason=reason)
        return public

    def _cached(self, key, path, build):
        try:
            info = path.stat()
        except OSError:
            self._cache.pop(key, None)
            return None
        stamp = (info.st_mtime_ns, info.st_size)
        cached = self._cache.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        summary = build()
        self._cache[key] = (stamp, summary)
        return summary

    def _run_summary(self, run_id, path):
        summary = self._cached(("run", run_id), path, lambda: summarize_run(run_id, _read_run_json(path)))
        return summary if summary is not None else summarize_run(run_id, None)

    def _legacy_summary(self, run_id, path):
        summary = self._cached(("legacy", run_id), path, lambda: summarize_legacy(run_id, path))
        return summary if summary is not None else summarize_legacy(run_id, None)


def _read_run_json(path):
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _matches(run, filters):
    if filters.get("keep") and not run["keep"]:
        return False
    if filters["status"] is not None and run["status"] != filters["status"]:
        return False
    if filters["since"] is not None and run["date"] < filters["since"]:
        return False
    if filters["q"] is not None:
        haystack = " ".join(part for part in (run.get("goal"), run.get("exe_name")) if part).casefold()
        if filters["q"] not in haystack:
            return False
    return True


def _text(value):
    return value if isinstance(value, str) else None


def _count(value):
    return value if type(value) is int else None


def _timestamp(text):
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def interrupted_reason(liveness, now, process_started_at):
    """Why a run.json saying running is dead, in Korean, or None while it may still be running."""
    if liveness.get("format") == 1:
        started = _timestamp(liveness.get("started_at"))
        if started is not None and started.tzinfo is not None and now - started > FORMAT_1_RUNNING_LIMIT:
            return "형식 1 기록이 1시간 넘게 실행 중으로 남아 있음"
        return None
    pid = liveness.get("pid")
    if type(pid) is not int:
        return "기록한 프로세스 정보(pid)가 없음"
    live = process_started_at(pid)
    if live is None:
        return "기록하던 프로세스가 없음"
    recorded = liveness.get("process_started_at")
    if isinstance(recorded, str) and live != recorded:
        return "같은 pid가 다른 프로세스에 쓰이고 있음"
    heartbeat = _timestamp(liveness.get("heartbeat_at"))
    if heartbeat is not None and heartbeat.tzinfo is not None and now - heartbeat > HEARTBEAT_LIMIT:
        return "heartbeat가 60초 넘게 갱신되지 않음"
    return None


def summarize_run(run_id, data):
    """The list entry for a run folder; `data` is its run.json object or None."""
    base = {"run_id": run_id, "kind": "run", "date": run_date(run_id)}
    if data is None or not isinstance(data.get("status"), str):
        return {**base, "status": "unreadable", "format": None, "newer_format": False}
    record_format = data.get("format", 1)
    started = _timestamp(data.get("started_at"))
    finished = _timestamp(data.get("finished_at"))
    duration = None
    if started is not None and finished is not None and started.tzinfo is not None and finished.tzinfo is not None:
        duration = max(0, round((finished - started).total_seconds()))
    exe = _text(data.get("exe"))
    return {
        **base,
        "format": record_format if type(record_format) is int else None,
        "newer_format": not (type(record_format) is int and record_format <= KNOWN_FORMAT),
        "status": data["status"],
        "goal": _text(data.get("goal")),
        "reason": _text(data.get("reason")),
        "exe": exe,
        "exe_name": PureWindowsPath(exe).name if exe else None,
        "model": _text(data.get("model")),
        "permission_level": _count(data.get("permission_level")),
        "started_by": _text(data.get("started_by")),
        "started_at": _text(data.get("started_at")),
        "finished_at": _text(data.get("finished_at")),
        "duration_s": duration,
        "steps": _count(data.get("steps")),
        "tokens": _count(data.get("tokens")),
        "liveness": {
            "format": 1 if "format" not in data else 2,
            "started_at": data.get("started_at"),
            "pid": data.get("pid"),
            "process_started_at": data.get("process_started_at"),
            "heartbeat_at": data.get("heartbeat_at"),
        },
    }


def summarize_legacy(run_id, path):
    """The list entry for an old log; its step count, and how it ended when a finish line says."""
    summary = {"run_id": run_id, "kind": "legacy", "date": run_date(run_id), "status": "legacy",
               "format": None, "newer_format": False, "goal": None, "steps": None, "result": None}
    if path is None:
        return summary
    try:
        lines = _legacy_lines(path)
    except OSError:
        return summary
    summary["steps"] = len(lines)
    for line in lines:
        result = line.get("result")
        if line.get("tool") == "finish" and isinstance(result, dict) and isinstance(result.get("status"), str):
            summary["result"] = result["status"]
    return summary


def _legacy_lines(path):
    lines = []
    for raw in path.read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict):
            lines.append(line)
    return lines


def _clip(text, limit=LEGACY_TEXT_CHARS):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def _short(value):
    if isinstance(value, str):
        return _clip(value)
    return _clip(json.dumps(value, ensure_ascii=False))


def _legacy_step(line):
    arguments = line.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    if isinstance(arguments, dict):
        arguments = {key: (_clip(value) if isinstance(value, str) else value) for key, value in arguments.items()}
    elif arguments is not None:
        arguments = _short(arguments)
    result = line.get("result")
    observation = result.get("observation") if isinstance(result, dict) else None
    return {
        "step": line.get("step") if type(line.get("step")) is int else None,
        "tool": _text(line.get("tool")) or "?",
        "arguments": arguments,
        "ok": result.get("ok") if isinstance(result, dict) and type(result.get("ok")) is bool else None,
        "summary": summarize_legacy_result(result),
        "observation": _clip(observation, LEGACY_OBSERVATION_CHARS) if isinstance(observation, str) else None,
    }


def summarize_legacy_result(result):
    """One line for a logged tool result: failure code, delivery, wait outcome, end status, sizes."""
    if not isinstance(result, dict):
        return _short(result)
    parts = ["실패"] if result.get("ok") is False else []
    for key in ("code", "message", "observation_error", "status", "reason", "found", "pid"):
        if result.get(key) is not None:
            parts.append(f"{key}={_short(result[key])}")
    disposition = result.get("disposition")
    if isinstance(disposition, dict) and disposition.get("delivery"):
        parts.append(f"delivery={disposition['delivery']}")
    if isinstance(result.get("windows"), list):
        parts.append(f"창 {len(result['windows'])}개")
    if isinstance(result.get("observation"), str):
        parts.append(f"관찰 {len(result['observation'].splitlines())}줄")
    if not parts:
        parts.append("ok" if result.get("ok") is True else _short(result))
    return " · ".join(parts)
