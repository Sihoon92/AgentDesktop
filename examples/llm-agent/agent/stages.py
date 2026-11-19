"""The stages of a run: the model's plan, the current stage, and what happened in each one.

A stage is one result a person can check on the screen. The model writes the plan; this
module only checks its form and limits, counts model calls per stage, and keeps per-stage
records for registration. It reads and writes nothing outside itself.

A record's actions hold the tool name, the model's intent, and numbers, never text read from
the screen: window titles and element names in a result's effects stay out.
"""

import re

from agent.tool_schemas import READ_ONLY_TOOLS

MIN_STAGES, MAX_STAGES, MAX_REVISIONS = 1, 12, 3
MAX_PARAMS = 12
PARAM_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
"""A parameter name as a command line takes it (`--param shop=...`)."""
SIGNALS = ("mismatch", "abandoned_window", "looked_twice", "limit_question")
EVIDENCE_KINDS = ("text", "picture")
LOOKING_TOOLS = frozenset({"observe", "look", "find_cells", "wait_for_text"})
"""Read-only calls that only look at the screen; two in one stage is the looked_twice signal."""

PENDING, ACTIVE, DONE, STOPPED = "pending", "active", "done", "stopped"


class StageError(ValueError):
    """A plan or stage call the harness refuses; `code` says why."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Stage:
    def __init__(self, goal, done_when, limit):
        self.goal = goal
        self.done_when = done_when
        self.status = PENDING
        self.calls = 0
        self.limit = limit
        self.kind = None
        self.note = None
        self.limit_questions = 0
        self.actions = []
        self.looks = 0
        self.mismatch = False
        self.abandoned = False
        self.opened = frozenset()

    def take_progress(self, other):
        """Carries an unfinished stage's counts over to the stage that replaces it."""
        for name in ("calls", "limit", "limit_questions", "looks", "mismatch", "abandoned", "opened"):
            setattr(self, name, getattr(other, name))
        self.actions = list(other.actions)

    def signals(self):
        present = {
            "mismatch": self.mismatch,
            "abandoned_window": self.abandoned,
            "looked_twice": self.looks >= 2,
            "limit_question": self.limit_questions > 0,
        }
        return [signal for signal in SIGNALS if present[signal]]


class StagePlan:
    """The model's plan and the per-stage call count, limit and record."""

    def __init__(self, call_limit):
        self._call_limit = call_limit
        self._stages = []
        self._revision = -1
        self._allowed_revisions = MAX_REVISIONS

    @property
    def exists(self):
        return bool(self._stages)

    def set(self, stages, params=None):
        """Sets the first plan or revises it, and returns the plan event.

        `stages` is the whole list. A revision must repeat every done stage word for word at
        its place; the stages after them are replaced. The calls, limit and record of the
        stage in progress carry over to the stage that takes its place, so a revision does
        not reset the call count.

        `params` are the goal's values that may differ in a later run, as
        [{"name", "value"}]; None is an empty list. A revision gives them anew, with no rule
        about earlier ones. The event carries a copy."""
        parsed = _parsed(stages)
        checked_params = _checked_params(params)
        if self.exists:
            if self._revision >= self._allowed_revisions:
                raise StageError("too_many_revisions",
                                 f"The plan was already revised {self._revision} times; ask the user")
            done = [stage for stage in self._stages if stage.status == DONE]
            if len(parsed) < len(done) or any(
                (stage.goal, stage.done_when) != pair for stage, pair in zip(done, parsed)
            ):
                raise StageError("changes_done_stage",
                                 f"Stages 1 to {len(done)} are done; repeat them unchanged and revise only the rest")
        else:
            done = []
        replaced = self._current_stage()
        rest = [_Stage(goal, done_when, self._call_limit) for goal, done_when in parsed[len(done):]]
        if rest:
            if replaced is not None:
                rest[0].take_progress(replaced)
            rest[0].status = ACTIVE
        self._stages = done + rest
        self._revision += 1
        return {
            "type": "plan",
            "revision": self._revision,
            "stages": [{"n": n, "goal": stage.goal, "done_when": stage.done_when, "status": stage.status}
                       for n, stage in enumerate(self._stages, 1)],
            "params": checked_params,
        }

    def allow_revisions(self):
        """After the user chose to go on at the revision limit: allows MAX_REVISIONS more
        revisions. It is not counted as a limit question."""
        self._allowed_revisions = max(self._revision, 0) + MAX_REVISIONS

    def current(self):
        """The stage in progress, or None before a plan, after the last stage, or after stop."""
        n, stage = self._current()
        if stage is None:
            return None
        return {"n": n, "goal": stage.goal, "done_when": stage.done_when, "calls": stage.calls,
                "limit": stage.limit}

    def count_call(self):
        stage = self._current_stage()
        if stage is not None:
            stage.calls += 1

    def note_action(self, tool, arguments, result):
        """Records one executed tool call on the current stage and updates its signals.

        A look counts toward looked_twice unless it was refused. An app-changing call that was
        not refused ends the watch on the windows the previous app-changing call opened: if it
        closed one of them, or moved the observed window from one of them to another, the stage
        has the abandoned_window signal. Window ids come from the result's effects only."""
        stage = self._current_stage()
        if stage is None:
            return
        arguments = arguments or {}
        effects = result.get("effects") or {}
        opened = _window_ids(effects.get("windows_opened"))
        closed = _window_ids(effects.get("windows_closed"))
        refused = "refused" in result
        stage.actions.append({
            "tool": tool,
            "intent": arguments.get("intent"),
            "expected_met": result.get("expected_met"),
            "refused_code": result.get("refused_code"),
            "opened": len(opened),
            "closed": len(closed),
        })
        if result.get("expected_met") is False:
            stage.mismatch = True
        if tool in LOOKING_TOOLS and not refused:
            stage.looks += 1
        if tool in READ_ONLY_TOOLS or refused:
            return
        if stage.opened and (closed & stage.opened or _moved_away(effects.get("observed_window"), stage.opened)):
            stage.abandoned = True
        stage.opened = opened

    def at_limit(self):
        stage = self._current_stage()
        return stage is not None and stage.calls >= stage.limit

    def extend(self):
        """After the user agreed at the limit: one more allowance and one more limit question."""
        n, stage = self._require_current()
        stage.limit += self._call_limit
        stage.limit_questions += 1
        return _stage_event(n, stage, "limit")

    def complete(self, kind, note):
        """Marks the current stage done with its evidence kind and note, and starts the next.
        Returns the stage events: done, then started when a stage follows."""
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence kind: {kind!r}")
        n, stage = self._require_current()
        stage.status = DONE
        stage.kind = kind
        stage.note = note
        events = [{**_stage_event(n, stage, "done"), "kind": kind, "note": note}]
        if n < len(self._stages):
            following = self._stages[n]
            following.status = ACTIVE
            events.append(_stage_event(n + 1, following, "started"))
        return events

    def stop(self):
        n, stage = self._current()
        if stage is None:
            return None
        stage.status = STOPPED
        return _stage_event(n, stage, "stopped")

    def all_done(self):
        return self.exists and all(stage.status == DONE for stage in self._stages)

    def unfinished(self):
        return [n for n, stage in enumerate(self._stages, 1) if stage.status != DONE]

    def records(self):
        """One record per stage for registration: the plan's texts, the model's note and
        intents, and numbers."""
        return [
            {
                "n": n,
                "goal": stage.goal,
                "done_when": stage.done_when,
                "status": stage.status,
                "calls": stage.calls,
                "kind": stage.kind,
                "note": stage.note,
                "limit_questions": stage.limit_questions,
                "actions": [dict(action) for action in stage.actions],
                "signals": stage.signals(),
            }
            for n, stage in enumerate(self._stages, 1)
        ]

    def stats(self):
        """Numbers only, for run.json and the run summary."""
        return {
            "stage_count": len(self._stages),
            "stages_done": sum(stage.status == DONE for stage in self._stages),
            "stage_calls": [stage.calls for stage in self._stages],
            "limit_questions": sum(stage.limit_questions for stage in self._stages),
            "plan_revisions": max(self._revision, 0),
        }

    def _current(self):
        for n, stage in enumerate(self._stages, 1):
            if stage.status == ACTIVE:
                return n, stage
        return None, None

    def _current_stage(self):
        return self._current()[1]

    def _require_current(self):
        if not self.exists:
            raise StageError("no_plan", "There is no plan yet; call plan first")
        n, stage = self._current()
        if n is None or stage is None:
            raise StageError("nothing_to_finish", "No stage is in progress")
        return n, stage


def _parsed(stages):
    if not isinstance(stages, list) or len(stages) < MIN_STAGES:
        raise StageError("bad_plan", f"A plan needs {MIN_STAGES} to {MAX_STAGES} stages")
    if len(stages) > MAX_STAGES:
        raise StageError("too_many_stages", f"A plan has at most {MAX_STAGES} stages, not {len(stages)}")
    parsed = []
    for n, stage in enumerate(stages, 1):
        texts = [stage.get(key) if isinstance(stage, dict) else None for key in ("goal", "done_when")]
        if not all(isinstance(text, str) and text.strip() for text in texts):
            raise StageError("bad_plan", f"Stage {n} needs both goal and done_when")
        parsed.append(tuple(texts))
    return parsed


def _checked_params(params):
    """The params as a new list of {"name", "value"}, or StageError("bad_params"): each name
    matches PARAM_NAME and differs from the others, each value is non-blank text, and there are
    at most MAX_PARAMS."""
    if params is None:
        return []
    if not isinstance(params, list):
        raise StageError("bad_params", "params must be a list of {name, value}")
    if len(params) > MAX_PARAMS:
        raise StageError("bad_params", f"At most {MAX_PARAMS} params, not {len(params)}")
    checked = []
    for n, param in enumerate(params, 1):
        name = param.get("name") if isinstance(param, dict) else None
        value = param.get("value") if isinstance(param, dict) else None
        if not isinstance(name, str) or not PARAM_NAME.fullmatch(name):
            raise StageError("bad_params", f"Param {n} needs a name of lowercase letters, digits and _, "
                                           "starting with a letter, at most 32 characters")
        if not isinstance(value, str) or not value.strip():
            raise StageError("bad_params", f"Param {name} needs a value as the goal gives it")
        if any(item["name"] == name for item in checked):
            raise StageError("bad_params", f"Param name {name} is given twice")
        checked.append({"name": name, "value": value})
    return checked


def _window_ids(windows):
    return frozenset(window.get("id") for window in windows or [] if window.get("id") is not None)


def _moved_away(observed_window, opened):
    if not observed_window:
        return False
    return observed_window.get("before") in opened and observed_window.get("after") not in opened


def _stage_event(n, stage, status):
    return {"type": "stage", "n": n, "status": status, "calls": stage.calls, "limit": stage.limit}
