"""From a recorded run to a replay, on one scene: the list run is recorded (list_run), a workflow
is made from its record with a scripted draft (make_workflow), and that workflow replays with no
model on a fresh copy of the same app (run_workflow). The app's own state, not the replay's
report, says what the replay did."""

import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest
from test_workflow_author import DraftModel, list_draft
from workflow_support import (
    LIST_EXE,
    LIST_SYSTEM,
    MENU_AREA,
    MENU_EXE,
    MENU_SYSTEM,
    ListApp,
    MenuApp,
    list_run,
    menu_run,
)

from agent.ask import NoAsker
from agent.desktop import DesktopClient
from agent.profile import Profile
from agent.task import Task
from agent.tools import Toolbox
from agent.workflow_author import after_run, make_workflow
from agent.workflow_format import resolve_values
from agent.workflow_run import record_outcome, run_workflow
from agent.workflow_store import WorkflowStore

DAY = date(2026, 11, 19)


class Clock:
    """Toolbox time that moves only when the replay sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """The workflow made from the list run's record, saved in its store."""
    folder = tmp_path_factory.mktemp("end-to-end")
    run_dir = list_run(folder)
    store = WorkflowStore(folder / "workflows", LIST_SYSTEM)
    events = []
    outcome, _ = make_workflow(DraftModel(list_draft()), run_dir, store, events.append, today=DAY)
    return store, outcome, events


def replay(tmp_path, workflow, values):
    """Runs `workflow` on a fresh app; returns (result, app, step events)."""
    app = ListApp(tmp_path / "unused.xlsx", auto_settle=True)
    clock = Clock()
    task = Task(workflow["goal"], LIST_EXE, workflow["permission_level"], [], system=LIST_SYSTEM)
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=app.run), task, confirm=lambda _p: False,
                      sleep=clock.sleep, clock=clock, asker=NoAsker(), captures_dir=tmp_path / "captures",
                      gated=False)
    steps = []
    result = run_workflow(toolbox, task, workflow, values, log=steps.append, started_at=0.0)
    return result, app, steps


def test_the_list_run_makes_a_workflow(made):
    store, outcome, events = made

    assert outcome == {"created": True, "id": "w-20261119-001", "code": None, "steps": 9, "params": 2}
    assert events == [{"type": "workflow", **outcome}]
    assert [s["do"] for s in store.get(outcome["id"])["steps"]] == [
        "act", "act", "select_only", "act", "act", "act", "act", "act", "check_file"]


def test_the_workflow_replays_with_its_examples_on_a_fresh_app(made, tmp_path):
    store, outcome, _ = made
    workflow = store.get(outcome["id"])
    values = resolve_values(workflow, {}, "replay1")

    result, app, steps = replay(tmp_path, workflow, values)

    assert result.status == "verified", (result.reason, steps)
    assert [step["ok"] for step in steps] == [True] * 9
    assert app.applied == {"WA0", "WA1"}
    assert app.period == "전일"
    assert values["save_path"].endswith("-replay1.xlsx")
    assert Path(values["save_path"]).is_file()
    assert app.item_clicks == ["All", "WA2", "WA3"]
    record_outcome(store, workflow, values, result)
    assert store.get(outcome["id"])["status"] == "verified"


def test_the_workflow_replays_with_other_values(made, tmp_path):
    store, outcome, _ = made
    workflow = store.get(outcome["id"])
    target = tmp_path / "other" / "b.xlsx"
    target.parent.mkdir()
    values = resolve_values(workflow, {"shop": "WA1, WA3", "save_path": str(target)}, "replay2")

    result, app, steps = replay(tmp_path, workflow, values)

    assert result.status == "verified", (result.reason, steps)
    assert app.applied == {"WA1", "WA3"}
    assert app.item_clicks == ["All", "WA0", "WA2"]
    assert target.is_file()


def test_every_item_given_presses_only_all(made, tmp_path):
    store, outcome, _ = made
    workflow = store.get(outcome["id"])
    values = resolve_values(workflow, {"shop": "WA0, WA1, WA2, WA3"}, "replay4")

    result, app, steps = replay(tmp_path, workflow, values)

    assert result.status == "verified", (result.reason, steps)
    assert app.item_clicks == ["All"] and app.applied == {"WA0", "WA1", "WA2", "WA3"}


@pytest.mark.parametrize("shop", ["WA9", "wa0, WA1"])
def test_a_shop_value_that_is_not_listed_stops_before_pressing_any_item(made, tmp_path, shop):
    store, outcome, _ = made
    workflow = store.get(outcome["id"])
    values = resolve_values(workflow, {"shop": shop}, "replay3")

    result, app, steps = replay(tmp_path, workflow, values)

    assert result.status == "stopped" and result.reason == "PARAM_VALUE_NOT_FOUND"
    assert [step["ok"] for step in steps] == [True, True, False]
    assert app.item_clicks == []
    assert app.applied == {"WA0", "WA1", "WA2", "WA3"}


def test_a_real_model_run_makes_its_workflow_before_it_finishes(tmp_path):
    """The loop, the recorder and after_run in run.py's order, with nothing faked but the model
    and the app: the run is still running when after_run reads its record."""
    from test_loop import call

    store = WorkflowStore(tmp_path / "workflows", LIST_SYSTEM)

    def make_it(model, result, run_dir, log):
        return after_run(model, result, run_dir, store, log)

    run_dir = list_run(tmp_path, before_finish=make_it, later_turns=[[call("propose_workflow", list_draft())]])

    assert [path.name for path in store.folder.glob("w-*.yaml")] == ["w-" + date.today().strftime("%Y%m%d") + "-001.yaml"]
    kinds = [json.loads(line)["type"] for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kinds.count("workflow") == 1 and kinds.index("workflow") < kinds.index("finish")
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "confirmed"
    assert run["workflow"]["created"] is True and run["workflow"]["code"] is None


MENU_PROFILE = Profile(system=MENU_SYSTEM, stable_ids=True, elements=(
    {"id": "56", "role": "image", "in": MENU_AREA, "name": "Shipment Plan"},
    {"id": "60", "role": "image", "in": MENU_AREA, "name": "Execution Plan"},
))
MENU_DRAFT = {"params": [], "steps": [{"kind": "act", "goal": "실행 계획 화면이 열림", "action": 2, "actions": None,
                                       "observation": None, "all_mark": None, "item_marks": None, "param": None}]}


def menu_workflow(tmp_path, profile):
    """A model run on picture-only menus that makes its workflow the way run.py does: after_run with
    use_ids from the profile, before the recorder finishes. Returns (store, run folder)."""
    from test_loop import call

    store = WorkflowStore(tmp_path / "workflows", MENU_SYSTEM)

    def make_it(model, result, run_dir, log):
        return after_run(model, result, run_dir, store, log, use_ids=profile.stable_ids)

    run_dir = menu_run(tmp_path, profile, before_finish=make_it, later_turns=[[call("propose_workflow", MENU_DRAFT)]])
    return store, run_dir


def test_a_model_run_on_picture_only_menus_makes_a_workflow_that_replays(tmp_path):
    store, run_dir = menu_workflow(tmp_path, MENU_PROFILE)

    [workflow] = store.entries()
    assert workflow["steps"][0]["target"] == {"scope": "main", "role": "image", "id": "60", "in": MENU_AREA}
    observations = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                    if json.loads(line).get("type") == "observation"]
    assert any('image id="60" alias="Execution Plan"' in event.get("text", "") for event in observations)

    app = MenuApp()
    clock = Clock()
    task = Task(workflow["goal"], MENU_EXE, workflow["permission_level"], [], system=MENU_SYSTEM)
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=app.run), task, confirm=lambda _p: False,
                      sleep=clock.sleep, clock=clock, asker=NoAsker(), captures_dir=tmp_path / "captures",
                      gated=False, profile=MENU_PROFILE)
    steps = []
    result = run_workflow(toolbox, task, workflow, resolve_values(workflow, {}, "replay5"), log=steps.append,
                          started_at=0.0)

    assert result.status == "verified", (result.reason, steps)
    assert app.pressed == ["60"] and app.screen == "실행 계획 화면"


def test_picture_only_menus_make_no_workflow_without_stable_ids(tmp_path):
    store, run_dir = menu_workflow(tmp_path, dataclasses.replace(MENU_PROFILE, stable_ids=False))

    assert store.entries() == []
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "confirmed" and run["workflow"] == {"created": False, "id": None,
                                                                "code": "LOCATOR_NOT_UNIQUE"}
