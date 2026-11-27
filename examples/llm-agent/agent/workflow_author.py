"""Makes a workflow from a finished, successful run folder.

The model is called once and answers with numbers only: which recorded actions to keep,
which ones to fold into a "select only these" step, and which parameter goes where. It has
no field in which to write a role or a name, so it cannot invent a target. The harness then
fills every target from the record (agent/locator.py), takes the expectations from the
recorded effects, checks each target against the observation it was pressed in, and checks
the result as the store would save it (agent/workflow_format.py).

Nothing here raises out of make_workflow or after_run: a workflow that cannot be made leaves
the run's result as it was and logs a code instead.
"""

import dataclasses
import json
import re
from datetime import date
from typing import NoReturn

from agent.locator import (
    LocatorError,
    describe,
    find,
    in_scope,
    new_windows,
    resolve_handle,
    window_elements,
    window_handle,
)
from agent.run_record import RecordError, read_record
from agent.workflow_format import (
    ACT_TOOLS,
    EXPECTS,
    FORMAT,
    PARAM_KINDS,
    PARAM_NAME,
    VALUE_TOOLS,
    WorkflowError,
    check_workflow,
)

SUCCESS_STATUSES = ("verified", "confirmed")
PICTURE_TOOLS = ("click_point", "mouse_click", "drag")
UNCONFIRMED = "unconfirmed"
MAX_OBSERVATION_CHARS = 6000
MAX_MATERIAL_CHARS = 60000
OMITTED = "(omitted to keep the material short)"
REF_TEXT = re.compile(r"\s@[A-Za-z0-9]+:e\d+")
BOUNDS_TEXT = re.compile(r"\s+bounds=\S+")
RUN_DATE = re.compile(r"(\d{4})(\d{2})(\d{2})-")
PLACEHOLDERS = {"id": "w-00000000-000", "status": "unverified", "passes": 0, "failures": 0,
                "verified_with_other_values": False}

DRAFT_INSTRUCTIONS = """\
You turn one successful desktop run into a workflow that a harness replays later without a model.
You only point at the record by number; the harness fills in every target and check itself.

You get the instruction, the parameter candidates from the run's plan, the plan's stages, the
delivered actions in order and the observation text each action was taken on ("seen_in" names
the observation id; its lines carry [n] marks). Answer with one propose_workflow call.

Steps, in the order of the record:
- act: repeat one action as it was done. "action" is its number. Use it for every action the
  task needs: opening screens and lists, pressing buttons, typing a path, confirming a dialog.
- select_only: replace the clicks that set which items of one open list are selected with
  "select exactly these". "actions" are those clicks, "observation" is an observation in which
  that list is open, "all_mark" is the mark of its select-all item and "item_marks" the marks of
  the items that were selected when the run finished, all in that one observation. The click
  that closes or applies the list stays a separate act step.
- check_file: confirm that the saved file exists. Use it last when the run saved a file.
Leave out detours: actions that were undone, lists opened and closed again without effect,
and anything the result does not need. Never point at a refused action or the same action twice.

Parameters are the values a later run may change. Use the plan's candidate names where they fit.
Kinds: "text" (one value: the name of the pressed item, or the typed text), "list" (the items of
a select_only), "path" (the saved file's path, on the act that typed it and on check_file).
Put a parameter only on an act whose target has a name or that typed text, or on a select_only.
An act on a table cell that find_rows found shows "found_by", the row's conditions; its parameter
is the value of a condition (with two or more conditions, the parameter must already be the
value of one of them from another step).
A candidate the steps do not use is left out; the harness keeps it as a fixed value.
Write one short goal per step: what is true on screen after it. Fields that do not apply are null.
"""

NULLABLE_INT = {"type": ["integer", "null"]}
NULLABLE_INTS = {"type": ["array", "null"], "items": {"type": "integer"}}
PROPOSE_WORKFLOW = {
    "type": "function",
    "name": "propose_workflow",
    "description": "Proposes the workflow's steps and parameters by pointing at the record's numbers.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "params": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "lowercase a-z, 0-9 and _, starting with a letter"},
                        "kind": {"type": "string", "enum": list(PARAM_KINDS)},
                    },
                    "required": ["name", "kind"],
                    "additionalProperties": False,
                },
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["act", "select_only", "check_file"]},
                        "goal": {"type": "string"},
                        "action": {**NULLABLE_INT, "description": "act: the action number"},
                        "actions": {**NULLABLE_INTS, "description": "select_only: the folded action numbers"},
                        "observation": {**NULLABLE_INT, "description": "select_only: an observation id"},
                        "all_mark": {**NULLABLE_INT, "description": "select_only: the select-all item's mark"},
                        "item_marks": {**NULLABLE_INTS, "description": "select_only: the selected items' marks"},
                        "param": {"type": ["string", "null"], "description": "the parameter name, or null"},
                    },
                    "required": ["kind", "goal", "action", "actions", "observation", "all_mark", "item_marks",
                                 "param"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["params", "steps"],
        "additionalProperties": False,
    },
}


class AuthorError(Exception):
    """A draft the harness cannot turn into a workflow; `code` is MODEL_NO_DRAFT, DRAFT_INVALID,
    LOCATOR_NOT_UNIQUE or PICTURE_TARGET. Messages name step and action numbers, never screen text."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def draft(model, record):
    """Asks the model once. Returns (draft, tokens); draft is None when no readable proposal came."""
    message = {"role": "user", "content": json.dumps(_material(record), ensure_ascii=False)}
    response = model.respond(DRAFT_INSTRUCTIONS, [message], [PROPOSE_WORKFLOW])
    tokens = response.usage.total_tokens if getattr(response, "usage", None) else 0
    calls = [item for item in response.output
             if getattr(item, "type", None) == "function_call" and getattr(item, "name", None) == PROPOSE_WORKFLOW["name"]]
    if not calls:
        return None, tokens
    try:
        proposal = json.loads(calls[0].arguments)
    except (TypeError, ValueError):
        return None, tokens
    return (proposal if isinstance(proposal, dict) else None), tokens


def build(record, draft, use_ids=False):
    """The workflow without id, status and counts, or AuthorError. With use_ids (the system's
    profile says its ids stay the same), targets carry their native id and area on top of what
    names them, except a target whose name is a parameter: another value must find another element."""
    return _Builder(record, use_ids).build(draft)


def make_workflow(model, run_dir, store, log, today=None, status=None, use_ids=False):
    """Reads the run, drafts, builds and saves. Never raises. Returns (outcome, tokens) and logs
    the outcome as a "workflow" event: {"created", "id", "code", "steps", "params"}. `status`
    is the run's own result status while it is still ending (see read_record); a past run's
    record is judged by its run.json."""
    tokens = 0
    try:
        record = read_record(run_dir, status=status)
        try:
            proposal, tokens = draft(model, record)
        except Exception as error:
            raise AuthorError("MODEL_NO_DRAFT", f"the model call failed: {type(error).__name__}") from error
        if proposal is None:
            raise AuthorError("MODEL_NO_DRAFT", "the model proposed no workflow")
        workflow = build(record, proposal, use_ids=use_ids)
        if workflow.get("system") is None:
            workflow.pop("system", None)
        saved = store.add(workflow, today=today)
        outcome = {"created": True, "id": saved["id"], "code": None, "steps": len(saved["steps"]),
                   "params": len(saved["params"])}
    except (RecordError, AuthorError, WorkflowError) as error:
        outcome = _failed(error.code)
    except Exception:
        outcome = _failed("DRAFT_INVALID")
    try:
        log({"type": "workflow", **outcome})
    except Exception:
        pass
    return outcome, tokens


def after_run(model, result, run_dir, store, log, use_ids=False):
    """Makes a workflow after a verified or confirmed run and adds its tokens to `result`."""
    if result.status not in SUCCESS_STATUSES:
        return result
    try:
        _outcome, tokens = make_workflow(model, run_dir, store, log, status=result.status, use_ids=use_ids)
    except Exception:
        return result
    return dataclasses.replace(result, tokens=(result.tokens or 0) + tokens)


def _failed(code):
    return {"created": False, "id": None, "code": code, "steps": 0, "params": 0}


def _invalid(message) -> NoReturn:
    raise AuthorError("DRAFT_INVALID", message)


class _Builder:
    def __init__(self, record, use_ids=False):
        self.record = record
        self.use_ids = use_ids
        self.kinds = {}
        self.examples = {}
        self.kept = []
        self.used = set()
        self.position = 0

    def build(self, proposal):
        if not isinstance(proposal, dict):
            _invalid("the draft is not an object")
        self.kinds = _declared(proposal.get("params"))
        raw_steps = proposal.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            _invalid("the draft has no steps")
        planned = [self._plan(n, raw) for n, raw in enumerate(raw_steps, 1)]
        avoid = self._example_texts()
        steps = [self._finish(item, avoid) for item in planned]
        params = {name: {"kind": self.kinds[name], "example": self.examples[name]}
                  for name in self.kinds if name in self.examples}
        workflow = {
            "format": FORMAT,
            "system": self.record.system,
            "goal": self.record.goal,
            "source": {"run_id": self.record.run_id, "date": _run_date(self.record.run_id)},
            "permission_level": self.record.permission_level,
            "params": params,
            "fixed": self._fixed(params),
            "steps": steps,
        }
        try:
            check_workflow({**workflow, **PLACEHOLDERS, "system": workflow["system"] or "unknown"})
        except WorkflowError as error:
            raise AuthorError("DRAFT_INVALID", f"the built workflow fails the format check: {error}") from error
        return workflow

    def _plan(self, n, raw):
        if not isinstance(raw, dict):
            _invalid(f"step {n} is not an object")
        goal = raw.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            _invalid(f"step {n} has no goal")
        kind = raw.get("kind")
        base = {"n": n, "goal": goal.strip()}
        if kind == "act":
            return self._plan_act(base, raw)
        if kind == "select_only":
            return self._plan_select(base, raw)
        if kind == "check_file":
            return self._plan_check_file(base, raw)
        return _invalid(f"step {n} has an unknown kind")

    def _plan_act(self, base, raw):
        n = base["n"]
        action = self._action(raw.get("action"), n)
        if action["n"] <= self.position:
            _invalid(f"step {n} is out of the record's order")
        self.position = action["n"]
        observation = self._before(action, n)
        element = None if action["tool"] == "press" else self._element(action, observation, n)
        scope = self._scope(observation, element, n)
        cell = self._cell(action, observation)
        item = {**base, "kind": "act", "action": action, "observation": observation, "element": element,
                "scope": scope, "param": raw.get("param"), "cell": cell}
        item.update(self._act_value(action, element, raw.get("param"), n, cell))
        item.update(self._expect(action))
        self.kept.append({"n": n, "action": action})
        return item

    def _plan_select(self, base, raw):
        n = base["n"]
        numbers = raw.get("actions")
        if not isinstance(numbers, list) or not numbers:
            _invalid(f"step {n} folds no actions")
        actions = [self._action(number, n) for number in numbers]
        if min(a["n"] for a in actions) <= self.position:
            _invalid(f"step {n} is out of the record's order")
        self.position = max(a["n"] for a in actions)
        observation = self.record.observations.get(raw.get("observation")) if isinstance(
            raw.get("observation"), int) else None
        if observation is None:
            _invalid(f"step {n} points at no recorded observation")
        elements = observation.get("elements") or []
        everything = _by_mark(elements, raw.get("all_mark"))
        marks = raw.get("item_marks")
        if everything is None or not isinstance(marks, list) or not marks:
            _invalid(f"step {n} points at marks the observation does not have")
        items = [_by_mark(elements, mark) for mark in marks]
        if any(item is None for item in items) or raw.get("all_mark") in marks or len(set(marks)) != len(marks):
            _invalid(f"step {n} points at marks the observation does not have")
        scope_mark = everything.get("scope")
        if scope_mark is None or any(item.get("scope") != scope_mark for item in items):
            _invalid(f"step {n}: the items and the select-all item are not in one list window")
        scope = self._scope(observation, everything, n)
        scoped = in_scope(elements, scope_mark)
        roles = {item.get("role") for item in items}
        names = [_name(item) for item in items]
        if len(roles) != 1 or any(name is None for name in names):
            _invalid(f"step {n}: the items do not share a role or have no name")
        if any(sum(1 for e in scoped if _name(e) == name) != 1 for name in names):
            _invalid(f"step {n}: an item's name is not unique in its list window")
        for action in actions:
            folded = self._element(action, self._before(action, n), n)
            if self._scope(self._before(action, n), folded, n) != scope:
                _invalid(f"step {n}: action {action['n']} is not in the step's list window")
        param = raw.get("param")
        if self.kinds.get(param) != "list":
            _invalid(f"step {n} needs a list parameter")
        self._example(param, names, n)
        return {**base, "kind": "select_only", "scope": scope, "observation": observation, "element": everything,
                "param": param, "item_role": roles.pop()}

    def _plan_check_file(self, base, raw):
        n = base["n"]
        param = raw.get("param")
        if self.kinds.get(param) != "path":
            _invalid(f"step {n} needs a path parameter")
        if not self.record.saved_path:
            _invalid(f"step {n}: the record has no saved path")
        self._example(param, self.record.saved_path, n)
        return {**base, "kind": "check_file", "param": param}

    def _action(self, number, n):
        if type(number) is not int or not 1 <= number <= len(self.record.actions):
            _invalid(f"step {n} points at no recorded action")
        action = self.record.actions[number - 1]
        if action["tool"] in PICTURE_TOOLS:
            raise AuthorError("PICTURE_TARGET", f"step {n}: action {number} pressed a point of the picture")
        if not action["delivered"]:
            _invalid(f"step {n}: action {number} was not delivered")
        if action["tool"] not in ACT_TOOLS:
            _invalid(f"step {n}: action {number} uses a tool a workflow cannot repeat")
        if number in self.used:
            _invalid(f"step {n}: action {number} is used twice")
        self.used.add(number)
        return action

    def _before(self, action, n):
        observation = self.record.observations.get(action.get("before"))
        if observation is None:
            _invalid(f"step {n}: action {action['n']} has no recorded observation before it")
        return observation

    def _element(self, action, observation, n):
        arguments = action.get("arguments") or {}
        elements = observation.get("elements") or []
        if action["tool"] == "click_part":
            element = _by_mark(elements, arguments.get("mark"))
        else:
            ref = arguments.get("ref") or (action.get("target") or {}).get("ref")
            element = next((e for e in elements if ref and e.get("ref") == ref), None)
        if element is None:
            _invalid(f"step {n}: action {action['n']}'s target is not in its observation")
        return element

    def _scope(self, observation, element, n):
        window_id = (observation.get("window") or {}).get("id")
        scope_mark = None if element is None else element.get("scope")
        if window_id != self.record.main_window:
            if scope_mark is not None:
                _invalid(f"step {n}: a list window inside a separate window is not supported")
            opener = self._opener_of_window(window_id)
            if opener is None:
                _invalid(f"step {n}: no kept step opened its window")
            return {"opened_by": opener, "top": True}
        if scope_mark is None:
            return "main"
        elements = observation.get("elements") or []
        window = _by_mark(elements, scope_mark)
        if window is None:
            _invalid(f"step {n}: its list window is not in the observation")
        opener = self._opener_of_element(window_handle(elements, window))
        if opener is None:
            _invalid(f"step {n}: no kept step opened its list window")
        return {"opened_by": opener}

    def _opener_of_element(self, handle):
        for kept in reversed(self.kept):
            action = kept["action"]
            before = self.record.observations.get(action.get("before"))
            after = self.record.observations.get(action.get("after"))
            if before is None or after is None:
                continue
            after_elements = after.get("elements") or []
            for window in new_windows(before.get("elements") or [], after_elements):
                if window_handle(after_elements, window) == handle:
                    return kept["n"]
        return None

    def _opener_of_window(self, window_id):
        for kept in reversed(self.kept):
            opened = ((kept["action"].get("result") or {}).get("effects") or {}).get("windows_opened") or []
            if any(window.get("id") == window_id for window in opened):
                return kept["n"]
        return None

    def _cell(self, action, observation):
        """The cell step of a click_part that pressed a cell of a find_rows lookup with one row on
        the same observation: {"table", "column", "where"}, else None (the step is written as
        before). The table is told by its id when ids are stable and it has one."""
        lookup = _lookup_of(action)
        if lookup is None:
            return None
        found, column = lookup
        tables = ((observation.get("structure") or {}).get("tables")) or []
        table = next((t for t in tables if isinstance(t, dict) and t.get("handle") == found.get("table")), None)
        if table is None or not isinstance(table.get("identity"), dict):
            return None
        identity = dict(table["identity"])
        if self.use_ids and isinstance(table.get("id"), str) and table["id"]:
            identity = {"role": identity.get("role"), "id": table["id"],
                        **({"in": identity["in"]} if "in" in identity else {})}
        return {"table": identity, "column": column, "where": dict(found.get("where") or {})}

    def _act_value(self, action, element, param, n, cell=None):
        tool = action["tool"]
        if tool in VALUE_TOOLS:
            recorded = (action.get("arguments") or {}).get(VALUE_TOOLS[tool])
            if not isinstance(recorded, str) or not recorded.strip():
                _invalid(f"step {n}: action {action['n']} has no recorded value")
            if param is None:
                return {"value": recorded}
            kind = self.kinds.get(param)
            if kind == "path":
                if not self.record.saved_path or recorded.strip() != self.record.saved_path.strip():
                    _invalid(f"step {n}: a path parameter must sit on the typed saved path")
                self._example(param, self.record.saved_path, n)
            elif kind == "text":
                self._example(param, recorded, n)
            else:
                _invalid(f"step {n} cannot take parameter {param}")
            return {"value": f"${param}"}
        if param is None:
            return {}
        if cell is not None:
            return self._cell_param(param, cell, n)
        if self.kinds.get(param) != "text" or element is None or _name(element) is None:
            _invalid(f"step {n}: only a named target or typed text takes a parameter")
        self._example(param, _name(element), n)
        return {"name_param": param}

    def _cell_param(self, param, cell, n):
        """A text parameter on a cell step is the value of one of its conditions: the only one, or
        the one that equals the parameter's example from another step."""
        values = list(cell["where"].values())
        if self.kinds.get(param) != "text":
            _invalid(f"step {n}: a cell step takes only a text parameter")
        if param in self.examples:
            if self.examples[param] not in values:
                _invalid(f"step {n}: parameter {param} is not the value of a condition")
        elif len(values) == 1:
            self._example(param, values[0], n)
        else:
            _invalid(f"step {n}: with two or more conditions, name the parameter on another step first")
        return {}

    def _cell_step(self, cell):
        """The cell with each condition value that equals a text parameter's example as "$name"."""
        texts = {example: name for name, example in self.examples.items() if self.kinds.get(name) == "text"}
        where = {column: f"${texts[value]}" if value in texts else value for column, value in cell["where"].items()}
        return {**cell, "where": where}

    def _expect(self, action):
        result = action.get("result") or {}
        effects = result.get("effects") or {}
        opened = effects.get("windows_opened") or []
        closed = effects.get("windows_closed") or []
        if opened:
            if all(self._closes_by_itself(window, action) for window in opened):
                return {"expect": "content_changed", "settle": True}
            return {"expect": "window_opened"}
        if closed:
            found = {"expect": "window_closed"}
            for window in closed:
                opener = self._opener_of_window(window.get("id"))
                if opener is not None:
                    found["closes"] = opener
                    break
            return found
        if result.get("verdict") == UNCONFIRMED and (effects.get("picture") or {}).get("changed"):
            return {"expect": "picture_changed"}
        if result.get("expected_met") is True and result.get("expect") in EXPECTS:
            return {"expect": result["expect"]}
        return {"expect": None}

    def _closes_by_itself(self, window, action):
        window_id, title = window.get("id"), window.get("title")
        for later in self.record.actions[action["n"]:]:
            if not later["delivered"]:
                continue
            closed = ((later.get("result") or {}).get("effects") or {}).get("windows_closed") or []
            if any(item.get("id") == window_id for item in closed):
                return False
            before = self.record.observations.get(later.get("before"))
            if before is None:
                continue
            if (before.get("window") or {}).get("id") == window_id:
                return False
            if self._pressed_inside(later, before, title):
                return False
        last = self.record.observations[max(self.record.observations)] if self.record.observations else None
        if last is None:
            return True
        if (last.get("window") or {}).get("id") == window_id:
            return False
        return not any(_name(w) == _clean(title) for w in window_elements(last.get("elements") or []))

    @staticmethod
    def _pressed_inside(action, observation, title):
        elements = observation.get("elements") or []
        arguments = action.get("arguments") or {}
        if action["tool"] == "click_part":
            target = _by_mark(elements, arguments.get("mark"))
        else:
            ref = arguments.get("ref")
            target = next((e for e in elements if ref and e.get("ref") == ref), None)
        if target is None or target.get("scope") is None:
            return False
        window = _by_mark(elements, target["scope"])
        return window is not None and _name(window) == _clean(title)

    def _example(self, param, value, n):
        if param in self.examples and self.examples[param] != value:
            _invalid(f"step {n}: parameter {param} has two different recorded values")
        self.examples[param] = value

    def _example_texts(self):
        texts = set()
        for name, example in self.examples.items():
            if self.kinds[name] == "list":
                texts.update(_clean(item) for item in example)
            elif self.kinds[name] == "text":
                texts.add(_clean(example))
        return texts - {None}

    def _finish(self, item, avoid):
        if item["kind"] == "check_file":
            return {"n": item["n"], "goal": item["goal"], "do": "check_file", "path": f"${item['param']}"}
        if item["kind"] == "select_only":
            return {"n": item["n"], "goal": item["goal"], "do": "select_only", "scope": item["scope"],
                    "all": self._locator(item, avoid, None), "item_role": item["item_role"],
                    "items": f"${item['param']}"}
        action = item["action"]
        step = {"n": item["n"], "goal": item["goal"], "do": "act", "tool": action["tool"]}
        arguments = action.get("arguments") or {}
        if action["tool"] == "click_part":
            step["args"] = {"part": arguments.get("part"), "action": arguments.get("action")}
        elif action["tool"] == "press":
            step["args"] = {"combo": arguments.get("combo")}
        if item["element"] is None:
            step["target"] = {"scope": item["scope"]}
        else:
            step["target"] = {"scope": item["scope"], **self._locator(item, avoid, item.get("name_param"))}
        if "value" in item:
            step["value"] = item["value"]
        if item.get("cell") is not None:
            step["cell"] = self._cell_step(item["cell"])
        step["expect"] = item["expect"]
        for key in ("closes", "settle"):
            if key in item:
                step[key] = item[key]
        return step

    def _locator(self, item, avoid, name_param):
        element = item["element"]
        scope_mark = element.get("scope") if item["scope"] != "main" and "top" not in item["scope"] else None
        scoped = in_scope(item["observation"].get("elements") or [], scope_mark)
        avoided = avoid | self._changed_names(item["observation"], scope_mark, scoped)
        try:
            locator = describe(scoped, element.get("mark"), avoided, use_ids=self.use_ids and name_param is None)
        except LocatorError as error:
            raise AuthorError("LOCATOR_NOT_UNIQUE", f"step {item['n']}: its target cannot be told apart") from error
        example = None
        if name_param is not None:
            locator = {**locator, "name": f"${name_param}"}
            example = self.examples[name_param]
        found = find(scoped, locator, name=example)
        if len(found) != 1 or found[0] is not element:
            raise AuthorError("LOCATOR_NOT_UNIQUE", f"step {item['n']}: its target does not find exactly itself")
        return locator

    def _changed_names(self, observation, scope_mark, scoped):
        """Names of `scoped` that some other observation of the same scope lacks, counting only
        observations that still show at least half of these names (the same screen, not
        another screen or the moment before it existed). Such text is a value, not a label."""
        names = {_name(e) for e in scoped} - {None}
        window_id = (observation.get("window") or {}).get("id")
        handle = None
        if scope_mark is not None:
            elements = observation.get("elements") or []
            handle = window_handle(elements, _by_mark(elements, scope_mark))
        changed = set()
        for other in self.record.observations.values():
            if (other.get("window") or {}).get("id") != window_id:
                continue
            elements = other.get("elements") or []
            if handle is None:
                other_scoped = in_scope(elements, None)
            else:
                window = resolve_handle(elements, handle)
                if window is None:
                    continue
                other_scoped = in_scope(elements, window.get("mark"))
            present = {_name(e) for e in other_scoped} - {None}
            if 2 * len(names & present) < len(names):
                continue
            changed |= names - present
        return changed

    def _fixed(self, params):
        fixed = []
        for candidate in self.record.params:
            name, value = candidate.get("name"), candidate.get("value")
            if name in params or not isinstance(name, str) or not PARAM_NAME.fullmatch(name):
                continue
            if isinstance(value, str) and value.strip() and all(item["name"] != name for item in fixed):
                fixed.append({"name": name, "value": value.strip()})
        return fixed


def _declared(raw_params):
    if not isinstance(raw_params, list):
        _invalid("the draft's parameters are not a list")
    kinds = {}
    for raw in raw_params:
        if not isinstance(raw, dict):
            _invalid("a draft parameter is not an object")
        name, kind = raw.get("name"), raw.get("kind")
        if not isinstance(name, str) or not PARAM_NAME.fullmatch(name) or kind not in PARAM_KINDS or name in kinds:
            _invalid("a draft parameter has a bad or repeated name, or an unknown kind")
        kinds[name] = kind
    return kinds


def _material(record):
    seen_in = _representatives(record)
    actions = [_action_view(record, action, seen_in.get(action["n"]))
               for action in record.actions if action["delivered"]]
    shown = []
    for observation_id in dict.fromkeys(seen_in.values()):
        text = _shown_text(record.observations[observation_id].get("text") or "")
        shown.append([observation_id, text])
    total = sum(len(text) for _, text in shown)
    for item in shown:
        if total <= MAX_MATERIAL_CHARS:
            break
        total -= len(item[1]) - len(OMITTED)
        item[1] = OMITTED
    return {
        "goal": record.goal,
        "params": record.params,
        "stages": [{"n": stage.get("n"), "goal": stage.get("goal"), "done_when": stage.get("done_when"),
                    "note": stage.get("note")} for stage in record.stages],
        "actions": actions,
        "observations": {str(observation_id): text for observation_id, text in shown},
    }


def _representatives(record):
    """Action number → the observation shown for it: the last of each run of consecutive
    observations that read the same window with the same windows inside it."""
    groups = []
    for action in record.actions:
        observation = record.observations.get(action.get("before"))
        if not action["delivered"] or observation is None:
            continue
        key = ((observation.get("window") or {}).get("id"),
               tuple(_name(w) for w in window_elements(observation.get("elements") or [])))
        if groups and groups[-1]["key"] == key:
            groups[-1]["actions"].append(action["n"])
            groups[-1]["observation"] = observation["id"]
        else:
            groups.append({"key": key, "actions": [action["n"]], "observation": observation["id"]})
    return {number: group["observation"] for group in groups for number in group["actions"]}


def _action_view(record, action, seen_in):
    result = action.get("result") or {}
    effects = result.get("effects") or {}
    arguments = action.get("arguments") or {}
    target = action.get("target") or {}
    view = {"n": action["n"], "tool": action["tool"], "intent": action.get("intent") or arguments.get("intent"),
            "target": {"role": target.get("role"), "name": target.get("name")} if target else None}
    if action["tool"] in VALUE_TOOLS:
        view["value"] = arguments.get(VALUE_TOOLS[action["tool"]])
    for key in ("part", "action", "combo"):
        if key in arguments:
            view[key] = arguments[key]
    lookup = _lookup_of(action)
    if lookup is not None:
        view["found_by"] = dict(lookup[0].get("where") or {})
    view.update({
        "expect": result.get("expect"),
        "expected_met": result.get("expected_met"),
        "verdict": result.get("verdict"),
        "windows_opened": len(effects.get("windows_opened") or []),
        "windows_closed": len(effects.get("windows_closed") or []),
        "seen_in": seen_in,
    })
    return view


def _lookup_of(action):
    """(find_rows event, column) for a click_part that pressed a cell the latest such lookup on the
    same observation returned while it matched exactly one row, else None."""
    if action.get("tool") != "click_part":
        return None
    mark = (action.get("arguments") or {}).get("mark")
    for found in reversed(action.get("lookups") or []):
        for cell in found.get("cells") or []:
            if isinstance(cell, dict) and cell.get("mark") == mark and isinstance(cell.get("column"), str):
                return (found, cell["column"]) if found.get("matched") == 1 else None
    return None


def _shown_text(text):
    cleaned = BOUNDS_TEXT.sub("", REF_TEXT.sub("", text))
    return cleaned if len(cleaned) <= MAX_OBSERVATION_CHARS else cleaned[:MAX_OBSERVATION_CHARS] + "\n" + OMITTED


def _run_date(run_id):
    found = RUN_DATE.match(run_id or "")
    if found:
        return "-".join(found.groups())
    return date.today().isoformat()


def _by_mark(elements, mark):
    if type(mark) is not int:
        return None
    return next((e for e in elements if e.get("mark") == mark), None)


def _name(element):
    return _clean(element.get("name"))


def _clean(value):
    if not isinstance(value, str):
        return None
    return value.strip() or None
