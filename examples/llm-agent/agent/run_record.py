"""Reads a finished run folder (runs/<id>/) into the pieces a workflow is made from.

It only reads: what the run did, in order, tied to the observations around each action. It
judges nothing about targets, scopes or expectations; agent.workflow_author does. Records
older than the observation marks (elements without "mark") cannot be read this way.
"""

from dataclasses import dataclass
from pathlib import Path

from agent.summary import read_records

SUCCESS_STATUSES = ("verified", "confirmed")
UNDELIVERED = "not_delivered"


class RecordError(Exception):
    """A run folder that cannot give a workflow; `code` says why: RUN_NOT_FOUND,
    RUN_NOT_SUCCESSFUL, NO_PLAN or RECORD_TOO_OLD."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    goal: str
    system: str | None
    permission_level: int
    params: list[dict]
    stages: list[dict]
    actions: list[dict]
    observations: dict[int, dict]
    saved_path: str | None
    main_window: str | None


def read_record(run_dir, status=None):
    """Reads run_dir into a RunRecord, or raises RecordError.

    `status`, when given, decides success in place of run.json's: a run that is ending reads its
    own record before the recorder writes its final status, so run.json still says running.

    params and stages come from the last plan event; each stage gets "note", the note of its
    last done stage event, or None. actions are the action events numbered from 1, each with
    "before" (the observation the action was taken on, as recorded), "after" (the first
    observation made by an action that follows it before the next action event, or None) and
    "delivered". observations maps each observation id to its event as written. saved_path is
    the last finish_note's; main_window the window of the first observation. A run.json
    without a goal or a permission level gives "" and 0, the level that allows no action."""
    run_dir = Path(run_dir)
    if not (run_dir / "run.json").is_file():
        raise RecordError("RUN_NOT_FOUND", f"No run record at {run_dir}")
    run, events = read_records(run_dir)
    ended = run.get("status") if status is None else status
    if ended not in SUCCESS_STATUSES:
        raise RecordError("RUN_NOT_SUCCESSFUL", f"The run ended {ended}, not verified or confirmed")
    plans = [event for event in events if event.get("type") == "plan"]
    if not plans:
        raise RecordError("NO_PLAN", "The run made no plan")
    observations = {event["id"]: event for event in events if event.get("type") == "observation" and "id" in event}
    if any("mark" not in element for event in observations.values() for element in event.get("elements") or []):
        raise RecordError("RECORD_TOO_OLD", "The record's observations carry no marks")
    notes = [event for event in events if event.get("type") == "finish_note"]
    first = next(iter(observations.values()), None)
    return RunRecord(
        run_id=run.get("run_id") or run_dir.name,
        goal=run.get("goal") or "",
        system=run.get("system"),
        permission_level=run.get("permission_level") or 0,
        params=list(plans[-1].get("params") or []),
        stages=_stages(plans[-1], events),
        actions=_actions(events),
        observations=observations,
        saved_path=notes[-1].get("saved_path") if notes else None,
        main_window=(first.get("window") or {}).get("id") if first else None,
    )


def _stages(plan, events):
    notes = {event.get("n"): event.get("note") for event in events
             if event.get("type") == "stage" and event.get("status") == "done"}
    return [{**stage, "note": notes.get(stage.get("n"))} for stage in plan.get("stages") or []]


def _actions(events):
    actions = []
    for event in events:
        kind = event.get("type")
        if kind == "action":
            actions.append({
                "n": len(actions) + 1,
                "step": event.get("step"),
                "tool": event.get("tool"),
                "intent": event.get("intent"),
                "arguments": event.get("arguments") or {},
                "target": event.get("target"),
                "result": event.get("result") or {},
                "before": event.get("before"),
                "after": None,
                "delivered": _delivered(event.get("result") or {}),
            })
        elif kind == "observation" and event.get("source") == "action" and actions and actions[-1]["after"] is None:
            actions[-1]["after"] = event.get("id")
    return actions


def _delivered(result):
    disposition = result.get("disposition") or {}
    return "refused" not in result and result.get("ok") is not False and disposition.get("delivery") != UNDELIVERED
