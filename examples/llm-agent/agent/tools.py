"""The tools the model may call, confined to one process of the target app.

Toolbox keeps the target process, windows, observations and ref/point actions here. Mark and
capture tools with the before/after comparison live in agent/screen_tools.py; asking, the
question policy and learned knowledge live in agent/asking.py. Both are mixins of Toolbox.
"""

import copy
import time
from dataclasses import dataclass

from agent.asking import AskingTools
from agent.effects import EXPECTATIONS
from agent.guard import ActionGuard, signature
from agent.observe import render_marked
from agent.ready import ready_message, signals, wait_until_ready
from agent.results import refused as _refused
from agent.screen_tools import UNCONFIRMED, ScreenTools
from agent.tool_schemas import READ_ONLY_TOOLS, SCHEMAS, definitions_for
from agent.verify import first_unmet

OBSERVE_TIMEOUT_MS = "8000"
RESTART_WAIT_S = 15
POLL_S = 0.5
WINDOW_FIELDS = ("id", "title", "minimized", "is_focused", "bounds")


@dataclass(frozen=True)
class RefAction:
    command: str
    headed: bool = False
    positional: str | None = None
    options: tuple = ()


REF_ACTIONS = {
    "click": RefAction("click"),
    "toggle": RefAction("toggle"),
    "expand": RefAction("expand"),
    "collapse": RefAction("collapse"),
    "set_value": RefAction("set-value", positional="value"),
    "select": RefAction("select", positional="value"),
    "check": RefAction("check"),
    "uncheck": RefAction("uncheck"),
    "scroll": RefAction("scroll", options=("direction", "amount")),
    "type": RefAction("type", headed=True, positional="text"),
    "double_click": RefAction("double-click", headed=True),
    "right_click": RefAction("right-click", headed=True),
}
POINT_TOOLS = {
    "mouse_click": (("x", "y"),),
    "mouse_wheel": (("x", "y"),),
    "drag": (("from_x", "from_y"), ("to_x", "to_y")),
}
WINDOW_TOOLS = frozenset({"press", "restore_window"})
MARK_TOOLS = frozenset({"click_part", "click_point"})
REOBSERVING_TOOLS = frozenset(REF_ACTIONS) | frozenset(POINT_TOOLS) | WINDOW_TOOLS | MARK_TOOLS
NON_IDENTITY_ARGUMENTS = frozenset({"window_id", "intent", "expect", "knowledge_id"})
FULL_SURFACES = (None, "window")


class _NoObserver:
    def on_observation(self, window, envelope, text, source=None, wait=None):
        pass

    def on_action(self, tool, arguments, target, result):
        pass


def _summary(envelope):
    if envelope.get("ok"):
        data = envelope.get("data") or {}
        keys = ("disposition", "post_state", "surfaces")
        return {"ok": True, **{key: data[key] for key in keys if key in data}}
    error = envelope.get("error") or {}
    details = error.get("details") or {}
    summary = {
        "ok": False,
        "code": error.get("code"),
        "message": error.get("message"),
        "suggestion": error.get("suggestion"),
        "kind": details.get("kind"),
        "post_state": details.get("post_state"),
        "disposition": error.get("disposition"),
    }
    return {key: value for key, value in summary.items() if value is not None}


class Toolbox(AskingTools, ScreenTools):
    def __init__(self, desktop, task, confirm, sleep=time.sleep, clock=time.monotonic, observer=None,
                 asker=None, knowledge=None, captures_dir=None, wall=time.time, vision=None, gated=True,
                 profile=None):
        """observer gets on_observation(window, envelope, text, source=..., wait=...) for each tree
        the agent reads, and on_action(tool, arguments, target, result) for each other tool call,
        before the re-read is reported. source names what made the observation ("observe", "action",
        "question", "wait_for_text" or "ready"); wait is set only for wait_for_text, which reports one
        observation per call. An observer with on_event(event) also gets the capture, question,
        answer, knowledge, completion and ready events. Captures go to captures_dir, or a temporary folder
        when it is None. asker answers questions; knowledge is the system's KnowledgeStore.

        vision, when given, reads pictures for a text model that cannot (split mode): an object
        with read(capture, question, marks) returning an answer with as_result() and as_event(),
        as agent.vision.Vision does. look then asks it a question, captures are recorded but never
        attached for the model, image_mode is ignored, and each read is sent as a vision event.

        gated=False is for a run that follows a fixed list of steps no one answers (a workflow
        replay): no call is refused for a question, the policy hears no outcome, and the repeat
        guard is off, since pressing the same element again is part of such a list. The
        permission level, the target process and the before/after judgement stay as they are.

        profile, when given, is the system's agent.profile.Profile. Every read of the tree is
        numbered with it (agent.observe.render_marked), so an element registered there gets its
        mark and alias in the observation, after an action, in wait_for_text, in the ready window
        and on the question screen alike, and a RunRecorder given the same profile records the
        same marks."""
        self._desktop = desktop
        self._observer = observer or _NoObserver()
        self._task = task
        self._profile = profile
        self._confirm = confirm
        self.sleep = sleep
        self.clock = clock
        self._guard = ActionGuard()
        self._pid = None
        self._instance = None
        self._current_window = None
        self._ready = False
        self._ready_cancelled = None
        self._refs = {}
        self._view_origin = None
        self._last_read = None
        self._levels = {s["definition"]["name"]: s["level"] for s in SCHEMAS}
        self._init_screen(captures_dir, vision)
        self._init_asking(asker, knowledge, wall, task, gated)

    @property
    def current_window(self):
        """The id of the window the latest observation read or the latest action aimed at, or None."""
        return self._current_window

    def marked_elements(self):
        """Copies of the latest observation's elements in mark order, each carrying its "mark"."""
        return [copy.deepcopy(self._marks[number]) for number in sorted(self._marks)]

    def definitions(self):
        return definitions_for(self._task.permission_level, self.vision_enabled)

    def call(self, name, arguments):
        if name in READ_ONLY_TOOLS:
            return self._dispatch(name, arguments)
        expect = arguments.get("expect")
        if expect is not None and expect not in EXPECTATIONS:
            return _refused("expect must be one of " + ", ".join(EXPECTATIONS))
        target = self.preview(name, arguments)
        acting = name in REOBSERVING_TOOLS
        runnable = acting and self._may_run(name)
        if runnable:
            refusal = self._gate(name, arguments, consume=True)
            if refusal is not None:
                return self._refuse_for_question(name, arguments, target, refusal)
        before = self._before_state(name, arguments) if runnable else None
        result = self._dispatch(name, arguments)
        if not acting or "refused" in result:
            if runnable:
                self._note_delivery(result)
            self._observer.on_action(name, arguments, target, result)
            return result
        self._cite_knowledge(arguments)
        self.forget_last_read()
        after = self._observe_current(source=None)
        result = {**result, **self._judged(expect, before, after)}
        if arguments.get("knowledge_id") is not None:
            result["knowledge_id"] = arguments["knowledge_id"]
        self._after_action(name, arguments, target, result)
        reason = _capture_reason(result)
        taken = self._capture_after_action(reason, expect, target, before) if reason is not None else None
        if taken is not None:
            result["capture"] = taken[0].id
            if taken[2] is not None:
                result["vision"] = taken[2]
        self._observer.on_action(name, arguments, target, result)
        if self._last_read is not None:
            self._observer.on_observation(*self._last_read, source="action")
        if taken is not None:
            self._emit(taken[1])
            if taken[3] is not None:
                self._emit(taken[3])
        return {**result, **after}

    def _refuse_for_question(self, name, arguments, target, refusal):
        """Returns a policy refusal, with a capture of the window so the model can frame a question."""
        taken = self._capture_current("question") if refusal["refused_code"] != "unknown_knowledge" else None
        if taken is not None:
            refusal = {**refusal, "capture": taken[0].id}
        self._observer.on_action(name, arguments, target, refusal)
        if taken is not None:
            self._emit(taken[1])
        return refusal

    def preview(self, name, arguments):
        """Returns what the call would act on, without running anything.

        A ref tool gives the ref with its role, name, description and bounds from the latest
        observation; a point tool gives the window and the screen points; a window tool gives the
        window with no points; click_part gives the mark's element and its point; click_point the
        capture's window and the screen point. Anything else, or an unknown ref, mark or capture,
        gives None. A ref or mark whose element has a registered name also gives it as "alias",
        for the person who approves; the native id and the area stay with the harness.
        """
        if name in REF_ACTIONS:
            element = self._refs.get(arguments.get("ref"))
            if element is None:
                return None
            shown = {key: value for key, value in element.items() if key != "native_id"}
            alias = next((marked["alias"] for marked in self._marks.values()
                          if marked.get("ref") == arguments["ref"] and marked.get("alias")), None)
            if alias is not None:
                shown["alias"] = alias
            return {"ref": arguments["ref"], **shown}
        if name in POINT_TOOLS:
            points = [[arguments[x], arguments[y]] for x, y in POINT_TOOLS[name]]
            return {"window_id": arguments.get("window_id"), "points": points}
        if name in WINDOW_TOOLS:
            return {"window_id": arguments.get("window_id"), "points": []}
        if name == "click_part":
            return self._part_preview(arguments)
        if name == "click_point":
            return self._point_preview(arguments)
        return None

    def _dispatch(self, name, arguments):
        if self._levels.get(name, 99) > self._task.permission_level:
            return _refused(f"Tool '{name}' is not available at this permission level")
        if name == "launch_target":
            return self._launch()
        if self._pid is None:
            return _refused("Call launch_target first")
        if name in REF_ACTIONS:
            return self._ref_action(name, arguments)
        return getattr(self, f"_tool_{name}")(arguments)

    def _may_run(self, name):
        return self._pid is not None and self._levels.get(name, 99) <= self._task.permission_level

    def verify(self):
        """Reads every window of the target and reports whether one meets the task's requirements."""
        return self.verify_elements(self._task.require)

    def verify_elements(self, require):
        """Reads every window of the target and reports whether one meets all of `require`."""
        unmet = "no window of the target process is open"
        for window in self._rows() if self._pid is not None else []:
            envelope = self._snapshot(window["id"])
            if not envelope.get("ok") or not envelope["data"].get("complete"):
                unmet = f"could not fully read window {window['id']}"
                continue
            unmet = first_unmet(require, envelope["data"]["tree"])
            if unmet is None:
                return {"met": True, "window_id": window["id"]}
        return {"met": False, "unmet": unmet}

    def observed_trees(self, source="observe"):
        """Reads each target window in turn, leaving the yielded tree's refs current. A None
        source reads quietly; report_wait() then records the last tree read as one observation."""
        for window in self._rows():
            envelope = self._snapshot(window["id"])
            if envelope.get("ok"):
                self._current_window = window["id"]
                text, self._refs, self._marks = render_marked(envelope["data"]["tree"], profile=self._profile)
                self._marks_view = (window["id"], True)
                self._view_origin = _origin(window)
                self._last_read = (window, envelope, text)
                if source is not None:
                    self._observer.on_observation(window, envelope, text, source=source)
                yield envelope["data"]["tree"]

    def forget_last_read(self):
        """Starts a quiet wait: report_wait() records nothing until a tree is read."""
        self._last_read = None

    def report_wait(self, wait):
        """Records the last tree read as one wait_for_text observation carrying wait
        ({"text", "found", "polls", "waited_ms"}). Does nothing when no tree was read."""
        if self._last_read is not None:
            self._observer.on_observation(*self._last_read, source="wait_for_text", wait=wait)

    def launch_ready(self, cancelled=None):
        """Launches the target and waits until a window shows every ready signal of the task
        (agent.ready.signals, read the way its ready_read names), then makes that window's process
        the target and that window the current one. Sends a ready start and end event, the end
        carrying the counts of the wait and never a signal, and records the ready window as one
        observation with source "ready", from the very read that found the signals. Returns {"ok":
        True, "pid", "windows", "ready": {"waited_ms", "switched"}}, or {"ok": False, "code":
        "READY_<STATUS>", "message"} with the
        reason in the words of READY_MESSAGES. No approval is asked: starting the run asked for it.
        `cancelled()` true ends the wait as READY_CANCELLED; a restart_target waits with the same one."""
        if cancelled is not None:
            self._ready_cancelled = cancelled
        self._emit({"type": "ready", "phase": "start"})
        outcome = wait_until_ready(self._desktop, self._task.exe, signals(self._task), self._task.ready_timeout_s,
                                   self.clock, self.sleep, cancelled=self._ready_cancelled,
                                   read=self._task.ready_read)
        if outcome.status == "ready":
            self._pid = outcome.pid
            self._instance = outcome.instance
            self._ready = True
            self._record_read(outcome.window, outcome.tree_envelope, source="ready")
        self._emit({"type": "ready", "phase": "end", "status": outcome.status, "waited_ms": outcome.waited_ms,
                    "polls": outcome.polls, "switched": outcome.switched, "running_hint": outcome.running_hint,
                    "reads": outcome.reads, "failed_reads": outcome.failed_reads, "timeouts": outcome.timeouts,
                    "max_read_ms": outcome.max_read_ms, "candidates": outcome.candidates, "read": outcome.read})
        if outcome.status != "ready":
            return {"ok": False, "code": f"READY_{outcome.status.upper()}", "message": ready_message(outcome)}
        return {"ok": True, "pid": self._pid, "windows": self._window_summaries(),
                "ready": {"waited_ms": outcome.waited_ms, "switched": outcome.switched}}

    def _launch(self):
        """Launches the exe, or after a ready wait reports the target as it is: launching again
        would start the launcher a second time."""
        if self._ready:
            return {"ok": True, "pid": self._pid, "windows": self._window_summaries()}
        envelope = self._desktop.run("launch", self._task.exe)
        if not envelope.get("ok"):
            return _summary(envelope)
        data = envelope["data"]
        self._pid = data["pid"]
        self._instance = data.get("process_instance")
        self._current_window = (data.get("window") or {}).get("id")
        return {"ok": True, "pid": self._pid, "windows": self._window_summaries()}

    def _rows(self):
        envelope = self._desktop.run("list-windows")
        if not envelope.get("ok"):
            return []
        return [
            row
            for row in envelope.get("data") or []
            if row.get("pid") == self._pid and row.get("process_instance") == self._instance
        ]

    def _window_summaries(self):
        return [{key: row.get(key) for key in WINDOW_FIELDS} for row in self._rows()]

    def _owned_window(self, window_id):
        return next((row for row in self._rows() if row["id"] == window_id), None)

    def _snapshot(self, window_id, root_ref=None, surface=None):
        args = ["snapshot", "--window-id", window_id, "--compact", "--timeout-ms", OBSERVE_TIMEOUT_MS]
        if root_ref:
            args += ["--root", root_ref]
        if surface:
            args += ["--surface", surface]
        args.append("--include-bounds")
        return self._desktop.run(*args)

    def _observe(self, window, root_ref=None, surface=None, include_bounds=False, source="observe"):
        """Reads one window. A None source reads quietly: no observer call, only _last_read."""
        envelope = self._snapshot(window["id"], root_ref, surface)
        if not envelope.get("ok"):
            return {"observation_error": _summary(envelope)}
        return self._record_read(window, envelope, root_ref, surface, include_bounds, source)

    def _record_read(self, window, envelope, root_ref=None, surface=None, include_bounds=False, source="observe"):
        """Makes a successful snapshot of `window` the latest view (refs, marks, origin, current
        window) and reports it to the observer under `source`, unless `source` is None."""
        window_id = window["id"]
        data = envelope["data"]
        text, refs, self._marks = render_marked(data["tree"], include_bounds, profile=self._profile)
        self._refs = {**self._refs, **refs} if root_ref else refs
        self._marks_view = (window_id, not root_ref and surface in FULL_SURFACES)
        self._view_origin = _origin(window)
        self._current_window = window_id
        self._last_read = (window, envelope, text)
        if source is not None:
            self._observer.on_observation(window, envelope, text, source=source)
        return {"window_id": window_id, "complete": data.get("complete"), "observation": text}

    def _observe_current(self, source):
        rows = self._rows()
        ids = [row["id"] for row in rows]
        windows = [{key: row.get(key) for key in WINDOW_FIELDS} for row in rows]
        if not ids:
            self._refs = {}
            return {"windows": [], "observation_error": "The target has no open window"}
        window_id = self._current_window if self._current_window in ids else ids[0]
        window = next(row for row in rows if row["id"] == window_id)
        return {"windows": windows, **self._observe(window, source=source)}

    def _act(self, tool, element, identity, args, headed=False):
        """Runs one app-changing CLI command, under the guard unless the toolbox is ungated.
        Refs and marks come from the latest observation, so its window's top-left places a
        nameless element."""
        if not self._gated:
            return _summary(self._desktop.run(*args, headed=headed))
        sig = signature(tool, element, *identity, origin=self._view_origin)
        refusal = self._guard.check(sig)
        if refusal:
            return _refused(refusal)
        result = _summary(self._desktop.run(*args, headed=headed))
        self._guard.record(sig, result)
        return result

    def _ref_action(self, name, arguments):
        ref = arguments["ref"]
        element = self._refs.get(ref)
        if element is None:
            return _refused(f"{ref} is not in the latest observation. Observe again.")
        spec = REF_ACTIONS[name]
        args = [spec.command, ref]
        identity = []
        for option in spec.options:
            args.append(f"--{option}={arguments[option]}")
            identity.append(arguments[option])
        if spec.positional:
            args += ["--", arguments[spec.positional]]
            identity.append(arguments[spec.positional])
        return self._act(name, element, identity, args, headed=spec.headed)

    def _physical(self, tool, arguments, points, args, element=None, identity=None, observed=None):
        """Clicks or drags at screen points of an owned window after giving it focus. The guard
        identifies the action by `element` and `identity`, or by the window and the arguments.
        The re-read after the action reads `observed` (the window whose picture the point came
        from), or the clicked window when it is None, until that window is gone."""
        window_id = arguments["window_id"]
        row = self._owned_window(window_id)
        if row is None:
            return _refused(f"{window_id} is not a window of the target process")
        for point in points:
            if not _inside(row["bounds"], *point):
                return _refused(f"{point} is outside window {window_id}: {row['bounds']}")
        self._desktop.run("focus-window", "--window-id", window_id, headed=True)
        row = self._owned_window(window_id)
        if row is None or not row.get("is_focused"):
            return _refused(f"Window {window_id} could not take focus, so the point may hit another app")
        if identity is None:
            identity = sorted((k, v) for k, v in arguments.items() if k not in NON_IDENTITY_ARGUMENTS)
        if element is None:
            element = {"role": "window", "name": window_id}
        self._current_window = observed or window_id
        return self._act(tool, element, identity, args, headed=True)

    def _tool_list_windows(self, _arguments):
        return {"ok": True, "windows": self._window_summaries()}

    def _tool_observe(self, arguments):
        window_id = arguments["window_id"] or self._current_window
        window = None if window_id is None else self._owned_window(window_id)
        if window is None:
            return _refused(f"{window_id} is not a window of the target process")
        root_ref = arguments["root_ref"]
        if root_ref and root_ref not in self._refs:
            return _refused(f"{root_ref} is not in the latest observation")
        result = self._observe(window, root_ref, arguments["surface"], arguments["include_bounds"])
        return {"ok": "observation" in result, **result}

    def _tool_wait_for_text(self, arguments):
        text = arguments["text"]
        started = self.clock()
        deadline = started + arguments["timeout_ms"] / 1000
        self.forget_last_read()
        polls = 0
        while True:
            latest = self._observe_current(source=None)
            polls += 1
            found = text in latest.get("observation", "")
            if found or self.clock() >= deadline:
                break
            self.sleep(POLL_S)
        waited_ms = round((self.clock() - started) * 1000)
        self.report_wait({"text": text, "found": found, "polls": polls, "waited_ms": waited_ms})
        return {"ok": True, "found": found, **latest}

    def _tool_restore_window(self, arguments):
        window_id = arguments["window_id"]
        if self._owned_window(window_id) is None:
            return _refused(f"{window_id} is not a window of the target process")
        self._current_window = window_id
        return _summary(self._desktop.run("restore", "--window-id", window_id))

    def _tool_press(self, arguments):
        window_id = arguments["window_id"]
        if self._owned_window(window_id) is None:
            return _refused(f"{window_id} is not a window of the target process")
        combo = arguments["combo"]
        args = ["press", "--window-id", window_id, combo]
        headed = self._task.permission_level >= 2
        element = {"role": "window", "name": window_id}
        self._current_window = window_id
        return self._act("press", element, [combo], args, headed=headed)

    def _tool_mouse_click(self, arguments):
        x, y = arguments["x"], arguments["y"]
        args = ["mouse-click", f"--xy={x},{y}", f"--button={arguments['button']}"]
        return self._physical("mouse_click", arguments, [(x, y)], args)

    def _tool_mouse_wheel(self, arguments):
        x, y = arguments["x"], arguments["y"]
        args = ["mouse-wheel", f"--x={x}", f"--y={y}", f"--dy={arguments['dy']}"]
        return self._physical("mouse_wheel", arguments, [(x, y)], args)

    def _tool_drag(self, arguments):
        start = (arguments["from_x"], arguments["from_y"])
        end = (arguments["to_x"], arguments["to_y"])
        args = ["drag", f"--from-xy={start[0]},{start[1]}", f"--to-xy={end[0]},{end[1]}"]
        return self._physical("drag", arguments, [start, end], args)

    def _tool_restart_target(self, _arguments):
        if not self._rows():
            return _refused("Cannot prove the pid still belongs to the target, so nothing was closed")
        prompt = f"앱을 재시작합니다. 저장하지 않은 작업이 사라질 수 있습니다.\n{self._task.exe} (pid {self._pid})"
        if not self._confirm(prompt):
            return _refused("The user declined the restart")
        if not self._desktop.close_process(self._pid):
            return {"ok": False, "code": "RESTART_FAILED", "message": "The close request failed"}
        deadline = self.clock() + RESTART_WAIT_S
        while self._rows():
            if self.clock() >= deadline:
                return {"ok": False, "code": "RESTART_TIMEOUT", "message": "The app is still open; it may be prompting"}
            self.sleep(POLL_S)
        if signals(self._task):
            return self.launch_ready()
        return self._launch()


def _capture_reason(result):
    """Why the window is captured after an action, or None when it is not: a mismatch, or an
    unconfirmed verdict the model has to check with its own eyes."""
    if result.get("expected_met") is False:
        return "mismatch"
    if result.get("verdict") == UNCONFIRMED:
        return UNCONFIRMED
    return None


def _origin(window):
    """The (x, y) of a window row's top-left, or None without bounds."""
    bounds = window.get("bounds")
    return (bounds["x"], bounds["y"]) if bounds else None


def _inside(bounds, x, y):
    return (
        bounds["x"] <= x < bounds["x"] + bounds["width"]
        and bounds["y"] <= y < bounds["y"] + bounds["height"]
    )
