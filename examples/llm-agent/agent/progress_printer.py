"""Prints a run's progress records to the terminal as they come, one line at a time.

A model call, a tool call and the ready wait print a ▶ line when they start and a ○ (done),
△ (delivered, not what was expected) or × (failed, with its code) line when they end. A CLI
command prints one line when it ends, indented under what it ran inside. Commands of the same
name that end one after another under the same parent fold into one line (`○ snapshot ×24 ·
12.0초`), and so do tools that only read (observe, list_windows), whose commands are not printed.
A stage that starts prints `[단계 n/m]`. Every mark is in cp949, so a Korean Windows console
prints them.
"""

import functools

INTENT_CHARS = 60
INDENT = "   "


class ProgressPrinter:
    def __init__(self, write=None):
        self._write = write or functools.partial(print, flush=True)
        self._starts = {}
        self._group = None
        self._stage_total = None

    def on_record(self, record):
        if record["phase"] == "start":
            self._starts[record["id"]] = record
            if self._shows_start(record):
                self._line(self._depth(record) * INDENT + _start_text(record))
            return
        start = self._starts.pop(record["id"], None)
        if start is None or self._hidden(start):
            return
        if start["kind"] == "cli" or start.get("read_only"):
            self._add_to_group(start, record)
            return
        self._line(self._depth(start) * INDENT + _end_text(start, record))

    def on_event(self, event):
        if event.get("type") == "plan" and isinstance(event.get("stages"), list):
            self._stage_total = len(event["stages"])
        if event.get("type") == "stage" and event.get("status") == "started":
            total = f"/{self._stage_total}" if self._stage_total else ""
            self._line(f"[단계 {event.get('n')}{total}]")

    def flush(self):
        """Prints the folded reads waiting under one parent, one line per name in first-seen order."""
        if self._group is None:
            return
        groups, self._group = self._group, None
        for group in groups["by_name"].values():
            self._write(group["indent"] + _group_text(group))

    def _line(self, text):
        self.flush()
        self._write(text)

    def _add_to_group(self, start, end):
        """Folds a finished read with the others under the same parent, by name, so reads that
        alternate (list-windows, snapshot, list-windows, ...) still fold into one line each."""
        parent = start.get("parent")
        if self._group is not None and self._group["parent"] != parent:
            self.flush()
        if self._group is None:
            self._group = {"parent": parent, "by_name": {}}
        key = (start["kind"], start.get("name"))
        group = self._group["by_name"].setdefault(key, {
            "name": start.get("name"), "indent": self._depth(start) * INDENT,
            "count": 0, "ms": 0, "failures": 0, "code": None})
        group["count"] += 1
        group["ms"] += end["t_ms"] - start["t_ms"]
        if not end.get("ok"):
            group["failures"] += 1
            group["code"] = end.get("code")

    def _shows_start(self, record):
        return record["kind"] in ("model", "ready", "tool") and not record.get("read_only") and not self._hidden(record)

    def _hidden(self, record):
        """True inside a tool that only reads: its commands fold into the tool's own line."""
        parent = self._starts.get(record.get("parent"))
        while parent is not None:
            if parent.get("read_only"):
                return True
            parent = self._starts.get(parent.get("parent"))
        return False

    def _depth(self, record):
        depth = 0
        parent = self._starts.get(record.get("parent"))
        while parent is not None:
            if parent["kind"] != "cli" and not parent.get("read_only"):
                depth += 1
            parent = self._starts.get(parent.get("parent"))
        return depth


def _seconds(ms):
    return f"{ms / 1000:.1f}초"


def _model_label(record):
    return f"모델({record['name']})" if record.get("name") else "모델"


def _start_text(record):
    kind = record["kind"]
    if kind == "model":
        step = f" · 스텝 {record['step']}" if record.get("step") is not None else ""
        return f"▶ {_model_label(record)} 생각 중{step}"
    if kind == "ready":
        return "▶ 대상 앱 준비 중"
    intent = record.get("intent")
    if intent:
        intent = intent if len(intent) <= INTENT_CHARS else intent[:INTENT_CHARS - 1] + "…"
        return f"▶ {record.get('name')} · {intent}"
    return f"▶ {record.get('name')}"


def _end_text(start, end):
    kind = start["kind"]
    label = {"model": f"{_model_label(start)} 응답", "ready": "대상 앱 준비"}.get(kind, start.get("name"))
    text = f"{label} · {_seconds(end['t_ms'] - start['t_ms'])}"
    if not end.get("ok"):
        return f"× {text}" + (f" · {end['code']}" if end.get("code") else "")
    if end.get("mismatch"):
        return f"△ {text} · 기대와 다름"
    if kind == "model" and end.get("tokens"):
        return f"○ {text} · 토큰 {end['tokens']:,}"
    return f"○ {text}"


def _group_text(group):
    count, failures = group["count"], group["failures"]
    mark = "×" if failures == count else "○"
    if count == 1:
        text = f"{mark} {group['name']} · {_seconds(group['ms'])}"
        return text + (f" · {group['code']}" if failures and group["code"] else "")
    text = f"{mark} {group['name']} ×{count} · {_seconds(group['ms'])}"
    return text + (f" · 실패 {failures}" if failures else "")
