"""What a run is doing right now: model calls, tool calls and CLI commands, each opened and closed.

ProgressLog writes one JSON line per start and per end to runs/<id>/progress.jsonl and hands each
record to its sinks (the terminal printer). Every record holds `id`, `phase` ("start" or "end"),
`kind` ("model", "tool", "cli", "ready"), `t_ms` (milliseconds since the log was made, on a
monotonic clock) and `parent` (the id of the open item it ran inside, left out at the top). A
start adds what names the item: a model call's `step` or `name`, a tool's `step`, `name`, `intent`
and `read_only`, a CLI command's `name` and `headed`. An end adds `ok`, a failure's `code`, a
model call's `tokens` and a delivered action's `mismatch`. A tool's start and end also hold
`events_seen`, the number of events.jsonl lines written so far, so the viewer can tell which tool
an event came from.

A CLI record carries the command's name alone, never its arguments: typed text, paths, points and
window ids stay out. The intent is the model's own sentence, so this file stays on the PC that
made the run, like events.jsonl; summaries and run.json take nothing from it.

Progress never stops a run. A sink that raises is dropped; a file that cannot be written is no
longer written.
"""

import contextlib
import json
import threading
import time

from agent.tool_schemas import READ_ONLY_TOOLS


class ProgressLog:
    def __init__(self, path=None, clock=time.monotonic, events_seen=None):
        self._path = path
        self._clock = clock
        self._events_seen = events_seen
        self._started = clock()
        self._sinks = []
        self._open = []
        self._starts = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def add_sink(self, sink):
        self._sinks.append(sink)

    def start(self, kind, **fields):
        """Opens an item inside the innermost open one and returns its id."""
        with self._lock:
            item_id = self._next_id
            self._next_id += 1
            record = {"id": item_id, "phase": "start", "kind": kind, "t_ms": self._elapsed_ms()}
            if self._open:
                record["parent"] = self._open[-1]
            record.update({key: value for key, value in fields.items() if value is not None})
            if kind == "tool" and self._events_seen is not None:
                record["events_seen"] = self._events_seen()
            self._open.append(item_id)
            self._starts[item_id] = record
        self._emit(record)
        return item_id

    def end(self, item_id, ok, **fields):
        """Closes `item_id`, first closing anything still open inside it as UNCLOSED. An id that
        is not open is ignored."""
        if item_id not in self._open:
            return
        while self._open and self._open[-1] != item_id:
            self._close(self._open[-1], False, {"code": "UNCLOSED"})
        self._close(item_id, ok, fields)

    def _close(self, item_id, ok, fields):
        with self._lock:
            self._open.remove(item_id)
            start = self._starts.pop(item_id)
            record = {"id": item_id, "phase": "end", "kind": start["kind"], "t_ms": self._elapsed_ms(), "ok": bool(ok)}
            record.update({key: value for key, value in fields.items() if value is not None})
            if start["kind"] == "tool" and self._events_seen is not None:
                record["events_seen"] = self._events_seen()
        self._emit(record)

    @contextlib.contextmanager
    def span(self, kind, **fields):
        """Opens an item for the block and closes it once: with what Span.finish set, ok when it
        was not called, or not ok with the exception's class name when the block raised."""
        span = Span(self.start(kind, **fields))
        try:
            yield span
        except BaseException as error:
            self.end(span.item_id, ok=False, code=type(error).__name__)
            raise
        self.end(span.item_id, **span.fields)

    def innermost(self):
        """A copy of the innermost open item's start record, or None."""
        with self._lock:
            return dict(self._starts[self._open[-1]]) if self._open else None

    def close_open(self, code):
        """Closes every open item, innermost first, as not ok with `code`."""
        while self._open:
            self._close(self._open[-1], False, {"code": code})

    def note_event(self, event):
        """Hands a run event (a plan, a stage) to the sinks that take events."""
        for sink in list(self._sinks):
            self._call(sink, "on_event", event)

    def flush_sinks(self):
        for sink in list(self._sinks):
            self._call(sink, "flush")

    def timed_model(self, model, name):
        """`model` with each respond() recorded as a model item named `name`."""
        return _TimedModel(model, self, name)

    def _elapsed_ms(self):
        return int(round((self._clock() - self._started) * 1000))

    def _emit(self, record):
        self._write(record)
        for sink in list(self._sinks):
            self._call(sink, "on_record", record)

    def _write(self, record):
        if self._path is None:
            return
        try:
            with open(self._path, "a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            self._path = None

    def _call(self, sink, method, *args):
        handler = getattr(sink, method, None)
        if handler is None:
            return
        try:
            handler(*args)
        except Exception:
            if sink in self._sinks:
                self._sinks.remove(sink)


class Span:
    """The open item of ProgressLog.span; finish() sets the fields its end will carry."""

    def __init__(self, item_id):
        self.item_id = item_id
        self.fields = {"ok": True}

    def finish(self, ok=True, **fields):
        self.fields = {"ok": ok, **fields}


class _TimedModel:
    def __init__(self, model, progress, name):
        self._model = model
        self._progress = progress
        self._name = name

    def respond(self, *args, **kwargs):
        with self._progress.span("model", name=self._name) as span:
            response = self._model.respond(*args, **kwargs)
            usage = getattr(response, "usage", None)
            span.finish(ok=True, tokens=getattr(usage, "total_tokens", None))
        return response

    def __getattr__(self, name):
        return getattr(self._model, name)


class _NullProgress(ProgressLog):
    """A progress log that records nothing, for clients made outside a recorded run."""

    def start(self, kind, **fields):
        return 0

    def end(self, item_id, ok, **fields):
        pass

    def innermost(self):
        return None

    def close_open(self, code):
        pass

    def add_sink(self, sink):
        pass


NULL_PROGRESS = _NullProgress()


def tool_item_fields(step, name, arguments):
    """The start fields of a tool item: its step, name, the intent on one line, and read_only
    for a tool that changes nothing in the app."""
    intent = arguments.get("intent") if isinstance(arguments, dict) else None
    if isinstance(intent, str):
        intent = " ".join(intent.split())
    else:
        intent = None
    return {"step": step, "name": name, "intent": intent or None,
            "read_only": True if name in READ_ONLY_TOOLS else None}


def result_fields(result):
    """The end fields of a tool call from its result: not ok when refused or failed (with the
    refused_code or code), ok with mismatch when delivered but not what was expected."""
    if "refused" in result or result.get("ok") is False:
        code = result.get("refused_code") or result.get("code")
        return {"ok": False, **({"code": code} if code else {})}
    return {"ok": True, **({"mismatch": True} if result.get("expected_met") is False else {})}


def envelope_fields(envelope):
    """The end fields of a CLI command from its envelope."""
    if envelope.get("ok"):
        return {"ok": True}
    code = (envelope.get("error") or {}).get("code")
    return {"ok": False, **({"code": code} if code else {})}
