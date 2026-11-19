"""Writes each run to runs/<run_id>/ so a person can replay what the agent saw and did.

run.json holds the run's summary, events.jsonl one event per line, frames/ one picture per
observation: the observed window with the other visible windows of the same process (popup
menus, dropdown lists) pasted where they sit on screen (agent/capture.py). Element boxes are
stored in image pixels, so the viewer only draws them. Recording must never end a run: every failure inside the recorder becomes a
warning on the event, or an entry in `errors` printed to stderr.

run.json holds numbers only about stages (`stages`, written at finish); the plan's texts and the
stage notes are kept in events.jsonl alone. Of a ready wait it holds the status, the seconds and
whether the target process changed (`ready`, written when the wait ends), never the signal. Of a
workflow it holds whether one was made, its id and its code (`workflow`, from the workflow event),
and of a replay the workflow's id, step counts and code (`workflow_run`, from the finish stats);
a value that is not a plain id, code or count keeps the whole entry out. Step goals, parameter
names and values and saved paths stay in events.jsonl.

run.json is format 2: it names the recording process (pid and its start time) and carries a
heartbeat_at that moves with every event and, from a daemon thread, at least every
heartbeat_s seconds until finish. A viewer that finds a running run whose process is gone or
whose heartbeat is old shows it as interrupted.
"""

import functools
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agent.capture import add_windows, stacked
from agent.frames import frame_origin, to_image_rect
from agent.observe import visible_elements
from agent.process_identity import process_started_at
from agent.ready import READY_STATUSES
from agent.summary import CODE, WORKFLOW_ID
from agent.survey import kinds

RUN_FORMAT = 2
HEARTBEAT_S = 10
REPLACE_ATTEMPTS = 5
REPLACE_PAUSE_S = 0.02
MAX_TEXT_ELEMENTS = 400
META_FIELDS = ("goal", "exe", "model", "permission_level", "started_by", "system", "max_steps", "code", "vision")
BOOLEAN_META = ("vision",)
"""Meta fields kept only as true or false, else null: `vision` says whether a vision model read the
pictures, never which one."""
WINDOW_FIELDS = ("id", "title", "bounds")
RESULT_FIELDS = (
    "ok", "disposition", "code", "message", "refused", "post_state",
    "expect", "effects", "expected_met", "verdict", "knowledge_id", "refused_code", "capture",
)
KEPT_EVENTS = ("knowledge_loaded", "knowledge_used", "knowledge_failed", "completion", "registration", "warning",
               "plan", "stage", "stop_requested", "finish_note", "workflow", "workflow_step")
MODEL_FIELDS = ("call_tokens", "stage", "stage_calls", "stage_limit")
"""Counts a model event may carry besides step and tokens; each is kept only when present."""
STAGE_COUNTS = ("stage_count", "stages_done", "limit_questions", "plan_revisions", "max_call_tokens")
STAGE_LISTS = ("stage_calls",)
"""The numbers of a run's stages that run.json keeps. Plan and stage texts stay in events.jsonl."""
VISION_COUNTS = ("vision_calls", "vision_tokens")
"""The vision counts of a split-mode run that run.json keeps at its top level. The vision
model's questions and answers stay in events.jsonl."""
WORKFLOW_RUN_FIELDS = ("id", "steps", "steps_done", "code")
"""The keys of a replay's workflow_run, all of which run.json keeps or none."""
STEP_FILLED_EVENTS = ("capture", "vision")
"""Toolbox events that get the current step when they come without one."""


def _never_raises(method):
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            method(self, *args, **kwargs)
        except Exception as error:
            self._note_error(method.__name__, error)

    return guarded


class RunRecorder:
    def __init__(self, runs_dir, desktop, meta, run_id=None, now=None, heartbeat_s=HEARTBEAT_S):
        """Creates runs_dir/<run_id>/ and starts the heartbeat thread; finish() or close() stops it.
        A generated id is YYYYMMDD-HHMMSS in UTC, with -2, -3, ... when that second is taken; a
        given run_id must be free."""
        self._desktop = desktop
        self._now = now or (lambda: datetime.now(timezone.utc))
        started = self._now().astimezone(timezone.utc)
        self.run_id, self.path = _claim_run_dir(Path(runs_dir), run_id, started.strftime("%Y%m%d-%H%M%S"))
        (self.path / "frames").mkdir()
        self.errors = []
        self._observations = 0
        self._step = None
        self._last_frame = None
        self._run_lock = threading.Lock()
        self._finished = False
        self._stop = threading.Event()
        pid = os.getpid()
        self._run = {
            "format": RUN_FORMAT,
            "run_id": self.run_id,
            **{key: _meta_value(meta, key) for key in META_FIELDS},
            "started_at": _stamp(started),
            "finished_at": None,
            "heartbeat_at": _stamp(started),
            "pid": pid,
            "process_started_at": process_started_at(pid),
            "status": "running",
            "reason": None,
            "tokens": 0,
            "steps": 0,
        }
        self._write_run()
        self._heartbeat_s = heartbeat_s
        self._heartbeat_thread = threading.Thread(
            target=self._beat_until_stopped, name=f"heartbeat-{self.run_id}", daemon=True
        )
        self._heartbeat_thread.start()

    @_never_raises
    def on_observation(self, window, envelope, text, source=None, wait=None):
        self._observations += 1
        event = {
            "type": "observation",
            "id": self._observations,
            "source": source,
            **({"wait": wait} if wait is not None else {}),
            "window": {key: window.get(key) for key in WINDOW_FIELDS},
            "frame": None,
            "image": None,
            "geometry_stale": False,
            "elements": [],
            "text_truncated": False,
            "text": text,
            "warning": None,
        }
        frame, window_ids = self._capture(window, event)
        self._last_frame = {"id": event["id"], "window_ids": window_ids, "geometry": frame}
        tree = envelope["data"]["tree"]
        event["elements"], event["text_truncated"] = _elements(tree, frame)
        event["kinds"] = kinds(tree)
        event["complete"] = envelope["data"].get("complete")
        self._append(event)

    @_never_raises
    def on_action(self, tool, arguments, target, result):
        self._append(
            {
                "type": "action",
                "step": self._step,
                "tool": tool,
                "intent": arguments.get("intent"),
                "arguments": arguments,
                "target": self._image_target(target),
                "before": self._last_frame["id"] if self._last_frame else None,
                "result": {key: result[key] for key in RESULT_FIELDS if key in result},
            }
        )

    @_never_raises
    def on_event(self, event):
        """Takes the loop's log events and the toolbox's own events. Model turns, proposals,
        decisions, captures, vision reads, questions, answers, knowledge, completion and registration
        events are recorded; tool calls are not, because on_action already recorded each one. A
        capture, a vision read or a question without a step gets the current one. A question is tied to the latest observation
        and its screen boxes and points get image coordinates; so does an answer's pin."""
        kind = event.get("type")
        if kind == "question":
            self._append(self._mapped_question(event))
            return
        if kind == "answer":
            self._append(self._mapped_answer(event))
            return
        if kind == "stop_requested":
            self._note_stop_requested(event)
            return
        if kind == "workflow":
            self._note_workflow(event)
        if kind in KEPT_EVENTS:
            self._append(event)
            return
        if kind == "ready":
            self._note_ready(event)
            self._append(event)
            return
        if kind in STEP_FILLED_EVENTS:
            filled = dict(event)
            if filled.get("step") is None:
                filled["step"] = self._step
            if kind == "capture" and filled.get("observation") is None:
                filled["observation"] = self._observations or None
            self._append(filled)
            return
        if event.get("type") == "proposal":
            self._append({**event, "target": self._image_target(event.get("target"))})
            return
        if event.get("type") == "decision":
            self._append(event)
            return
        if event.get("type") != "model":
            return
        self._step = event["step"]
        with self._run_lock:
            self._run.update(steps=event["step"], tokens=event["tokens"])
        kept = {key: event[key] for key in MODEL_FIELDS if key in event}
        self._append({"type": "model", "step": event["step"], "tokens": event["tokens"], **kept})

    @_never_raises
    def finish(self, result):
        """Writes the final status and finished_at, then stops the heartbeat for good. A result
        with stats (the loop's stage counts) adds their numbers to run.json as `stages`, and the
        whole-number vision counts of a split-mode run (VISION_COUNTS) at its top level; a result
        without them, such as one from a run that made no plan, adds nothing."""
        self.close()
        stats = getattr(result, "stats", None)
        with self._run_lock:
            self._finished = True
            stamp = self._stamp_now()
            self._run.update(status=result.status, reason=result.reason, tokens=result.tokens,
                             steps=result.steps, finished_at=stamp, heartbeat_at=stamp)
            if isinstance(stats, dict):
                self._run["stages"] = _stage_numbers(stats)
                self._run.update({key: stats[key] for key in VISION_COUNTS if _whole(stats.get(key))})
                replay = _workflow_run(stats.get("workflow_run"))
                if replay is not None:
                    self._run["workflow_run"] = replay
            self._write_run_locked()
        self._append(
            {
                "type": "finish",
                "status": result.status,
                "steps": result.steps,
                "reason": result.reason,
                "tokens": result.tokens,
            }
        )

    def _note_ready(self, event):
        """Writes the end of a ready wait into run.json at once, so the run list sees it while the
        run goes on: {"status", "waited_s", "switched"}, numbers and a code only. An end whose
        status is not in READY_STATUSES, or whose time or switch is not a plain value, is kept in
        events.jsonl alone. A later end (a restart) replaces an earlier one."""
        status, waited_ms, switched = event.get("status"), event.get("waited_ms"), event.get("switched")
        if event.get("phase") != "end" or status not in READY_STATUSES or not _whole(waited_ms):
            return
        if not isinstance(switched, bool):
            return
        with self._run_lock:
            self._run["ready"] = {"status": status, "waited_s": round(waited_ms / 1000), "switched": switched}
            self._write_run_locked()

    def _note_workflow(self, event):
        """Writes {"created", "id", "code"} of a workflow event into run.json at once. An event
        whose created is not true or false, whose id is not a workflow id or null, or whose code is
        not a code or null, is kept in events.jsonl alone."""
        created, workflow_id, code = event.get("created"), event.get("id"), event.get("code")
        if not isinstance(created, bool) or not (workflow_id is None or _workflow_id(workflow_id)):
            return
        if not (code is None or _code_shaped(code)):
            return
        with self._run_lock:
            self._run["workflow"] = {"created": created, "id": workflow_id, "code": code}
            self._write_run_locked()

    def _note_stop_requested(self, event):
        """Keeps the stop request with the current step, unless the run already finished: a stop
        that comes too late is never written after the finish event."""
        with self._run_lock:
            if self._finished:
                return
            self._write_line({**event, "step": event.get("step", self._step)})
        self._heartbeat()

    def close(self):
        """Stops the heartbeat thread without finishing the run. Safe to call more than once."""
        self._stop.set()
        if self._heartbeat_thread is not threading.current_thread():
            self._heartbeat_thread.join(timeout=5)

    def _beat_until_stopped(self):
        while not self._stop.wait(self._heartbeat_s):
            self._heartbeat()

    @_never_raises
    def _heartbeat(self):
        """Moves heartbeat_at to now and rewrites run.json. Does nothing after finish."""
        with self._run_lock:
            if self._finished:
                return
            self._run["heartbeat_at"] = self._stamp_now()
            self._write_run_locked()

    def _stamp_now(self):
        return _stamp(self._now())

    def _capture(self, window, event):
        """Takes the picture. Returns ((origin, size) for mapping boxes, or None, and the ids of
        the windows in it). The window list read after the screenshot both checks that the
        window kept its place and names the other windows to paste over it."""
        name = f"{event['id']:04d}.png"
        path = self.path / "frames" / name
        alone = [window.get("id")]
        shot = self._desktop.run("screenshot", "--window-id", window["id"], str(path))
        if not shot.get("ok"):
            error = shot.get("error") or {}
            event["warning"] = f"스크린샷 실패: {error.get('code')}: {error.get('message')}"
            return None, alone
        size = (shot["data"]["width"], shot["data"]["height"])
        event["frame"] = name
        event["image"] = {"width": size[0], "height": size[1]}
        rows = self._listed_windows()
        after = next((row.get("bounds") for row in rows if row.get("id") == window["id"]), None)
        event["geometry_stale"] = after != window.get("bounds")
        if after is None:
            event["warning"] = "스크린샷 뒤에 창을 찾지 못해 박스 위치를 확인할 수 없습니다"
        if not window.get("bounds"):
            event["warning"] = "창 영역을 몰라 박스를 그릴 수 없습니다"
            return None, alone
        origin = frame_origin(window["bounds"], *size)
        layout = add_windows(self._desktop, path, window, origin, stacked(window, rows))
        if layout.size is not None:
            size = layout.size
            event["image"] = {"width": size[0], "height": size[1]}
        if layout.missing:
            missed = f"다른 창 {layout.missing}개를 찍지 못해 그림에서 뺐습니다"
            event["warning"] = f"{event['warning']}; {missed}" if event["warning"] else missed
        origin = (origin[0] + layout.shift[0], origin[1] + layout.shift[1])
        return (origin, size), [window_id for window_id, _ in layout.windows]

    def _listed_windows(self):
        listing = self._desktop.run("list-windows")
        return listing.get("data") or [] if listing.get("ok") else []

    def _image_target(self, target):
        if target is None:
            return None
        frame = self._last_frame["geometry"] if self._last_frame else None
        mapped = dict(target)
        if "bounds" in target:
            mapped["rect"] = _rect(target["bounds"], frame)
        if "points" in target:
            same_window = self._last_frame and target.get("window_id") in self._last_frame["window_ids"]
            points_frame = frame if same_window else None
            mapped["image_points"] = [_point(point, points_frame) for point in target["points"]]
        return mapped

    def _frame_geometry(self):
        return self._last_frame["geometry"] if self._last_frame else None

    def _mapped_question(self, event):
        frame = self._frame_geometry()
        mapped = dict(event)
        if mapped.get("step") is None:
            mapped["step"] = self._step
        mapped["observation"] = self._observations or None
        mapped["candidates"] = [
            {**candidate, "rect": _rect(candidate.get("bounds"), frame),
             "image_point": _point(candidate["point"], frame) if candidate.get("point") else None}
            for candidate in event.get("candidates") or []
        ]
        mapped["basis"] = [{**item, "rect": _rect(item.get("bounds"), frame)} for item in event.get("basis") or []]
        return mapped

    def _mapped_answer(self, event):
        pin = event.get("pin")
        if not pin:
            return event
        return {**event, "pin": {**pin, "image_point": _point(pin["point"], self._frame_geometry())}}

    def _append(self, event):
        self._write_line(event)
        self._heartbeat()

    def _write_line(self, event):
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as file:
            file.write(line)

    def _write_run(self):
        with self._run_lock:
            self._write_run_locked()

    def _write_run_locked(self):
        temporary = self.path / "run.json.tmp"
        temporary.write_text(json.dumps(self._run, ensure_ascii=False, default=str), encoding="utf-8")
        _replace_retrying(temporary, self.path / "run.json")

    def _note_error(self, where, error):
        message = f"{where}: {type(error).__name__}: {error}"
        self.errors.append(message)
        print(f"기록 오류 ({self.run_id}) {message}", file=sys.stderr)


def _replace_retrying(source, target):
    """os.replace, retried briefly: on Windows it fails while another process (the viewer)
    has the target open for reading."""
    for _ in range(REPLACE_ATTEMPTS - 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(REPLACE_PAUSE_S)
    os.replace(source, target)


def _stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claim_run_dir(runs_dir, run_id, base):
    runs_dir.mkdir(parents=True, exist_ok=True)
    if run_id is not None:
        (runs_dir / run_id).mkdir()
        return run_id, runs_dir / run_id
    suffix = 1
    while True:
        candidate = base if suffix == 1 else f"{base}-{suffix}"
        try:
            (runs_dir / candidate).mkdir()
            return candidate, runs_dir / candidate
        except FileExistsError:
            suffix += 1


def _stage_numbers(stats):
    """The counts of `stats` that are whole numbers, and the lists made only of them."""
    numbers = {key: stats[key] for key in STAGE_COUNTS if _whole(stats.get(key))}
    for key in STAGE_LISTS:
        value = stats.get(key)
        if isinstance(value, list) and all(_whole(item) for item in value):
            numbers[key] = list(value)
    return numbers


def _workflow_run(replay):
    """A replay's {"id", "steps", "steps_done", "code"} when it holds exactly those keys with a
    workflow id, whole-number counts and a code or null; otherwise None."""
    if not isinstance(replay, dict) or set(replay) != set(WORKFLOW_RUN_FIELDS):
        return None
    code = replay["code"]
    if not _workflow_id(replay["id"]) or not (_whole(replay["steps"]) and _whole(replay["steps_done"])):
        return None
    if not (code is None or _code_shaped(code)):
        return None
    return {key: replay[key] for key in WORKFLOW_RUN_FIELDS}


def _workflow_id(value):
    return isinstance(value, str) and WORKFLOW_ID.fullmatch(value) is not None


def _code_shaped(value):
    return isinstance(value, str) and CODE.fullmatch(value) is not None


def _meta_value(meta, key):
    value = meta.get(key)
    if key in BOOLEAN_META and not isinstance(value, bool):
        return None
    return value


def _whole(value):
    return type(value) is int and value >= 0


def _elements(tree, frame):
    """Returns every control and at most MAX_TEXT_ELEMENTS text elements, and whether text was cut.
    Each element keeps its observation mark and scope, so a cut never renumbers the rest."""
    elements = []
    texts = 0
    for element in visible_elements(tree):
        if element["kind"] == "text":
            texts += 1
            if texts > MAX_TEXT_ELEMENTS:
                continue
        bounds = element.pop("bounds")
        elements.append({**element, "rect": _rect(bounds, frame)})
    return elements, texts > MAX_TEXT_ELEMENTS


def _rect(bounds, frame):
    if not bounds or frame is None:
        return None
    origin, size = frame
    return to_image_rect(bounds, origin, size)


def _point(point, frame):
    if frame is None:
        return None
    (left, top), (width, height) = frame
    x, y = point[0] - left, point[1] - top
    return [x, y] if 0 <= x < width and 0 <= y < height else None
