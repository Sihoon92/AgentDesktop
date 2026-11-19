"""Replays a stored workflow without a model: find each step's target, act, and check the result.

The replay drives the same Toolbox a model run uses (ungated: no question policy), so every
action and observation is recorded the same way. It never guesses: a target that matches no
element is read again until STEP_TIMEOUT_S, two or more matches stop the run at once, and a
check that is not met by STEP_TIMEOUT_S stops it too. A stop leaves the target app as it is.

Scopes follow the tree the model run read. "main" is what sits directly in the main window;
{"opened_by": N} is the inside of the window element step N opened in the main window's tree,
named by its window handle (agent/locator.py); {"opened_by": N, "top": True} is the window
list row step N opened, read on its own. Step N "opened" a window when its expectation was
window_opened and exactly one new row of the window list appeared.

Time comes only from toolbox.clock and toolbox.sleep. The codes are the ones of the design
(section 7); no screen text goes into them.
"""

import time

from agent import locator
from agent.completion import check_file
from agent.effects import diff, met, state
from agent.guard import AgentStopped
from agent.loop import RunResult
from agent.ready import signals
from agent.tools import REF_ACTIONS
from agent.workflow_format import VALUE_TOOLS, other_values, param_name

STEP_TIMEOUT_S = 15
SETTLE_TIMEOUT_S = 180
POLL_S = 0.5
PASSED_REASON = "workflow 통과"
UNCONFIRMED = "unconfirmed"
CHECKED = "checked"
ROLE_WITH_STATE = "checkbox"


class _Stop(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def run_workflow(toolbox, task, workflow, values, log=lambda _e: None, stop=None, started_at=None):
    """Runs every step of `workflow` with `values` (from workflow_format.resolve_values) on
    `toolbox`, sending one workflow_step event per step to `log`. `stop` is anything with
    is_set(), heard between steps. `started_at` is the wall time a saved file must be newer
    than; None takes the time of this call. Returns a RunResult: "verified" with every step
    done, or "stopped" with the steps done and the code as its reason."""
    replay = _Replay(toolbox, task, workflow, values, log, stop)
    return replay.run(time.time() if started_at is None else started_at)


def record_outcome(store, workflow, values, result):
    """Counts the replay in the store: a pass (noting values other than the examples) or a failure."""
    if result.status == "verified":
        store.note_pass(workflow["id"], other_values(workflow, values))
    else:
        store.note_failure(workflow["id"])


class _Replay:
    def __init__(self, toolbox, task, workflow, values, log, stop):
        self._toolbox = toolbox
        self._task = task
        self._workflow = workflow
        self._values = values
        self._log = log
        self._stop = stop
        self._main = None
        self._opened = {}
        self._started_at = None

    def run(self, started_at):
        self._started_at = started_at
        code = self._start()
        if code is not None:
            return self._result(0, code)
        done = 0
        for step in self._workflow["steps"]:
            if self._stopped():
                return self._result(done, "STOPPED_BY_USER")
            began = self._toolbox.clock()
            code = self._step(step)
            self._log({"type": "workflow_step", "n": step["n"], "kind": step["do"], "ok": code is None,
                       "code": code, "ms": max(0, round((self._toolbox.clock() - began) * 1000)),
                       "goal": step["goal"]})
            if code is not None:
                return self._result(done, code)
            done += 1
        return self._result(done, None)

    def _result(self, done, code):
        stats = {"workflow_run": {"id": self._workflow["id"], "steps": len(self._workflow["steps"]),
                                  "steps_done": done, "code": code}}
        if code is None:
            return RunResult("verified", done, PASSED_REASON, stats=stats)
        return RunResult("stopped", done, code, stats=stats)

    def _stopped(self):
        return self._stop is not None and self._stop.is_set()

    def _start(self):
        if signals(self._task):
            started = self._toolbox.launch_ready(cancelled=self._stopped)
        else:
            started = self._toolbox.call("launch_target", {})
        if not started.get("ok"):
            return "STOPPED_BY_USER" if self._stopped() else started.get("code") or "ACTION_FAILED"
        if not self._rows():
            return "APP_GONE"
        if not self._toolbox.call("observe", _observe_args(None)).get("ok"):
            return "APP_GONE" if not self._rows() else "ACTION_FAILED"
        self._main = self._toolbox.current_window
        return None

    def _step(self, step):
        try:
            if step["do"] == "act":
                self._act(step)
            elif step["do"] == "select_only":
                self._select_only(step)
            else:
                self._check_file(step)
        except _Stop as stopped:
            return stopped.code
        except AgentStopped:
            return "ACTION_FAILED"
        return None

    def _rows(self):
        return self._toolbox.call("list_windows", {}).get("windows") or []

    def _live_rows(self):
        rows = self._rows()
        if not rows:
            raise _Stop("APP_GONE")
        return rows

    def _read(self, window_id):
        """The marked elements of one window, or None when it cannot be read."""
        if not self._toolbox.call("observe", _observe_args(window_id)).get("ok"):
            return None
        return self._toolbox.marked_elements()

    def _view(self, scope):
        """(window id, scope mark, elements) for a scope as it is now, or None when the window it
        names is not there (yet)."""
        if scope == "main":
            elements = self._read(self._main)
            return None if elements is None else (self._main, None, elements)
        opened = self._opened.get(scope["opened_by"])
        if opened is None:
            return None
        if scope.get("top"):
            elements = self._read(opened["row"])
            return None if elements is None else (opened["row"], None, elements)
        if opened["handle"] is None and opened["base"] is None:
            return None
        elements = self._read(self._main)
        if elements is None:
            return None
        if opened["handle"] is None:
            fresh = locator.new_windows(opened["base"], elements)
            if len(fresh) != 1:
                return None
            opened["handle"] = locator.window_handle(elements, fresh[0])
        window = locator.resolve_handle(elements, opened["handle"])
        return None if window is None else (self._main, window["mark"], elements)

    def _find(self, scope, wanted):
        """Reads the scope until `wanted(scoped)` gives a list of one, by STEP_TIMEOUT_S.
        Returns (view, element); `wanted` None just waits for the scope."""
        deadline = self._toolbox.clock() + STEP_TIMEOUT_S
        while True:
            self._live_rows()
            view = self._view(scope)
            if view is not None:
                if wanted is None:
                    return view, None
                found = wanted(locator.in_scope(view[2], view[1]))
                if len(found) > 1:
                    raise _Stop("TARGET_AMBIGUOUS")
                if len(found) == 1:
                    if "offscreen" in (found[0].get("states") or []):
                        raise _Stop("TARGET_OFFSCREEN")
                    return view, found[0]
            if self._toolbox.clock() >= deadline:
                raise _Stop("TARGET_NOT_FOUND")
            self._toolbox.sleep(POLL_S)

    def _value(self, raw):
        name = param_name(raw)
        return self._values[name] if name is not None else raw

    def _act(self, step):
        tool, target = step["tool"], step["target"]
        if tool == "press":
            view, element = self._find(target["scope"], None)
        else:
            shape = {key: value for key, value in target.items() if key != "scope"}
            name = self._value(target["name"]) if "name" in target else None
            view, element = self._find(target["scope"], lambda scoped: locator.find(scoped, shape, name))
        window_id, _mark, elements = view
        arguments = self._arguments(step, window_id, element)
        rows_before = self._live_rows()
        result = self._toolbox.call(tool, arguments)
        self._delivered(result)
        _Check(self, step, rows_before, window_id, elements, result).wait()

    def _arguments(self, step, window_id, element):
        tool, args = step["tool"], step.get("args") or {}
        expect = step.get("expect")
        common = {"intent": step["goal"], "knowledge_id": None,
                  "expect": "any" if expect in (None, "picture_changed") else expect}
        if tool == "press":
            return {"window_id": window_id, "combo": args["combo"], **common}
        if tool == "click_part":
            return {"mark": element["mark"], "part": args["part"], "action": args["action"], **common}
        if tool not in REF_ACTIONS or not element.get("ref"):
            raise _Stop("ACTION_FAILED")
        arguments = {"ref": element["ref"], **common}
        if tool in VALUE_TOOLS:
            arguments[VALUE_TOOLS[tool]] = self._value(step["value"])
        return arguments

    def _delivered(self, result):
        if "refused" in result or result.get("ok") is False:
            self._live_rows()
            raise _Stop("ACTION_FAILED")

    def _select_only(self, step):
        """Makes exactly the given items checked in the list of the step's scope (design 5)."""
        rows_before = self._live_rows()
        role = step["item_role"]
        wanted = [value.strip() for value in self._value(step["items"])]
        view, all_element = self._find(step["scope"], lambda scoped: locator.find(scoped, step["all"]))
        scoped = locator.in_scope(view[2], view[1])
        chosen = []
        for value in wanted:
            found = locator.find(scoped, {"role": role}, value)
            if len(found) != 1:
                raise _Stop("PARAM_VALUE_NOT_FOUND")
            chosen.append(found[0])
        names = [row.get("name") for row in _list_rows(scoped, role, chosen, all_element)]
        if role == ROLE_WITH_STATE:
            self._set_states(step, role, names, wanted)
        else:
            self._press_all_then_clear(step, role, names, wanted)
        _refuse_new_rows(rows_before, self._live_rows())

    def _row(self, step, role, name):
        return self._find(step["scope"], lambda scoped: locator.find(scoped, {"role": role}, name))[1]

    def _set_states(self, step, role, names, wanted):
        for name in names:
            row = self._row(step, role, name)
            if not row.get("ref"):
                raise _Stop("ACTION_FAILED")
            checked = CHECKED in (row.get("states") or [])
            if checked == (name.strip() in wanted):
                continue
            tool = "uncheck" if checked else "check"
            self._delivered(self._toolbox.call(tool, {"ref": row["ref"], "intent": step["goal"],
                                                      "knowledge_id": None, "expect": "value_changed"}))
        deadline = self._toolbox.clock() + STEP_TIMEOUT_S
        while True:
            states = {name: CHECKED in (self._row(step, role, name).get("states") or []) for name in names}
            if all(states[name] == (name.strip() in wanted) for name in names):
                return
            if self._toolbox.clock() >= deadline:
                raise _Stop("EXPECT_UNMET")
            self._toolbox.sleep(POLL_S)

    def _press_all_then_clear(self, step, role, names, wanted):
        self._press_part(step, self._find(step["scope"], lambda scoped: locator.find(scoped, step["all"]))[1])
        for name in names:
            if name.strip() in wanted:
                continue
            result = self._press_part(step, self._row(step, role, name))
            if not (result.get("expected_met") is True or result.get("verdict") == UNCONFIRMED):
                raise _Stop("EXPECT_UNMET")

    def _press_part(self, step, element):
        result = self._toolbox.call("click_part", {"mark": element["mark"], "part": "center", "action": "click",
                                                   "intent": step["goal"], "knowledge_id": None, "expect": "any"})
        self._delivered(result)
        return result

    def _check_file(self, step):
        if not check_file(self._value(step["path"]), self._started_at)["met"]:
            raise _Stop("FILE_NOT_SAVED")


class _Check:
    """Waits until one act step's expectation holds (design 5).

    A window_opened step remembers the row it opened and the handle of the window element that
    appeared in the main window's tree with it. When the row came before the element, the main
    window's elements of that moment are kept as "base" and the handle is filled in by the first
    later read that shows exactly one new window element (_Replay._view)."""

    def __init__(self, replay, step, rows_before, window_id, elements, result):
        self._replay = replay
        self._toolbox = replay._toolbox
        self._step = step
        self._expect = step.get("expect")
        self._before_ids = [row["id"] for row in rows_before]
        self._before = state(rows_before, _marks(elements), window_id)
        self._window_id = window_id
        self._main_before = elements if window_id == replay._main else None
        self._result = result

    def wait(self):
        if self._step.get("settle"):
            self._wait_settled()
            return
        deadline = self._toolbox.clock() + STEP_TIMEOUT_S
        first = True
        while True:
            rows = self._replay._live_rows()
            new = [row["id"] for row in rows if row["id"] not in self._before_ids]
            done = self._met(rows, new, first)
            first = False
            opened = self._replay._opened.get(self._step["n"])
            unexpected = [row_id for row_id in new if opened is None or row_id != opened["row"]]
            if done or self._expect == "picture_changed" or self._toolbox.clock() >= deadline:
                if unexpected:
                    raise _Stop("UNEXPECTED_WINDOW")
                if not done:
                    raise _Stop("EXPECT_UNMET")
                return
            self._toolbox.sleep(POLL_S)

    def _wait_settled(self):
        """A settle step waits while any window that was not there before the action is open,
        whenever it opened: WAIT_TIMEOUT when one is still open SETTLE_TIMEOUT_S after the action.
        With none open the expectation is checked, and EXPECT_UNMET follows STEP_TIMEOUT_S without
        any. Windows that open meanwhile are the settle's own, never unexpected."""
        began = self._toolbox.clock()
        quiet_since = began
        first = True
        while True:
            rows = self._replay._live_rows()
            new = [row["id"] for row in rows if row["id"] not in self._before_ids]
            now = self._toolbox.clock()
            if new:
                quiet_since = None
                if now - began >= SETTLE_TIMEOUT_S:
                    raise _Stop("WAIT_TIMEOUT")
            else:
                quiet_since = now if quiet_since is None else quiet_since
                if self._met(rows, new, first):
                    return
                if now - quiet_since >= STEP_TIMEOUT_S:
                    raise _Stop("EXPECT_UNMET")
            first = False
            self._toolbox.sleep(POLL_S)

    def _met(self, rows, new, first):
        expect = self._expect
        if expect is None:
            return True
        if expect == "picture_changed":
            return self._result.get("expected_met") is True or self._result.get("verdict") == UNCONFIRMED
        if expect == "window_opened":
            return self._note_opened(new, first)
        if expect == "window_closed":
            return self._closed({row["id"] for row in rows})
        now = state(rows, _marks(self._elements_now(first)), self._window_id)
        return met(expect, diff(self._before, now))

    def _elements_now(self, first):
        """The elements of the step's window now: the action's own re-read when it read that window."""
        if first and "observation" in self._result and self._toolbox.current_window == self._window_id:
            return self._toolbox.marked_elements()
        return self._replay._read(self._window_id) or []

    def _note_opened(self, new, first):
        if len(new) != 1:
            return False
        handle = base = None
        if self._main_before is not None:
            now = self._elements_now(first)
            fresh = locator.new_windows(self._main_before, now)
            if len(fresh) == 1:
                handle = locator.window_handle(now, fresh[0])
            elif not fresh:
                base = now
        self._replay._opened[self._step["n"]] = {"row": new[0], "handle": handle, "base": base}
        return True

    def _closed(self, now_ids):
        closes = self._step.get("closes")
        if closes is None:
            return any(row_id not in now_ids for row_id in self._before_ids)
        opened = self._replay._opened.get(closes)
        return opened is not None and opened["row"] not in now_ids


def _observe_args(window_id):
    return {"window_id": window_id, "root_ref": None, "surface": None, "include_bounds": False}


def _marks(elements):
    return {element["mark"]: element for element in elements or []}


def _list_rows(scoped, role, chosen, all_element):
    """The list's item rows: elements of the items' role whose horizontal span overlaps a chosen
    item's, leaving out the "all" element and whatever shares its row (such as an apply button
    beside it)."""
    all_box = locator.box(all_element)
    rows = []
    for element in scoped:
        box = locator.box(element)
        if element is all_element or element.get("role") != role or box is None:
            continue
        if all_box is not None and locator.overlaps(all_box, box, "y"):
            continue
        if any(locator.box(item) is not None and locator.overlaps(locator.box(item), box, "x") for item in chosen):
            rows.append(element)
    return rows




def _refuse_new_rows(rows_before, rows_now):
    before = {row["id"] for row in rows_before}
    if any(row["id"] not in before for row in rows_now):
        raise _Stop("UNEXPECTED_WINDOW")
