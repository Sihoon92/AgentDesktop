"""A replay finds a target written with a native id: role, id and area must all fit (plan task 5).

agent/workflow_run.py is not changed for this: it finds targets through agent.locator.find,
which reads the "native_id" and "in" the observation puts on each element (plan K3). The
FakeApp gives native ids to the labels in AppOptions.ids and, with id_area, puts those
elements in a toolbar that only an id names.
"""

import time

import pytest
from replay_support import AppOptions, full_steps, make_replay, make_values, make_workflow

from agent.observe import render_marked
from agent.workflow_format import check_workflow
from agent.workflow_run import run_workflow

AREA = {"role": "toolbar", "id": "tb"}
IDS = {"메뉴": "9001", "Shop 열기": "7300"}


def marks_carry_ids():
    """Whether the observation's numbered elements carry native_id and in (plan K3, task 3)."""
    native = {"kind": "automation_id", "value": "2"}
    tree = {"role": "window", "name": "w", "children": [
        {"role": "toolbar", "native_id": {"kind": "automation_id", "value": "1"}, "children": [
            {"role": "button", "name": "b", "ref_id": "@s1:e1", "native_id": native}]}]}
    marks = render_marked(tree)[2]
    return any(e.get("native_id") == native and e.get("in") == {"role": "toolbar", "id": "1"} for e in marks.values())


needs_ids = pytest.mark.skipif(not marks_carry_ids(), reason="observed elements carry no native_id yet (plan K3)")


def id_steps():
    """full_steps with the menu button and the nameless Shop arrow written by id and area."""
    steps = full_steps()
    steps[0]["target"] = {"scope": "main", "role": "button", "name": "메뉴", "id": "9001", "in": AREA}
    steps[2]["target"] = {"scope": "main", "role": "button", "id": "7300", "in": AREA}
    return steps


def replay(tmp_path, steps, options):
    replaying = make_replay(tmp_path, options)
    workflow = check_workflow(make_workflow(tmp_path, steps=steps))
    result = run_workflow(replaying.toolbox, replaying.task, workflow, make_values(tmp_path),
                          started_at=time.time() - 1)
    return result, replaying.app


def stopped_at(result, code, steps_done):
    stats = result.stats["workflow_run"]
    return result.status == "stopped" and (stats["code"], stats["steps_done"]) == (code, steps_done)


@needs_ids
def test_replay_finds_a_target_by_role_id_and_container(tmp_path):
    result, app = replay(tmp_path, id_steps(), AppOptions(ids=IDS, id_area="tb"))

    assert result.status == "verified"
    assert app.applied == {"WA0", "WA1"} and app.saved


@needs_ids
def test_replay_stops_when_the_id_changed(tmp_path):
    result, app = replay(tmp_path, id_steps(), AppOptions(ids={**IDS, "Shop 열기": "7301"}, id_area="tb"))

    assert stopped_at(result, "TARGET_NOT_FOUND", 2)
    assert app.applied is None and "list" not in app.open_popups


@needs_ids
def test_replay_stops_when_the_area_changed(tmp_path):
    result, app = replay(tmp_path, id_steps(), AppOptions(ids=IDS, id_area="tb2"))

    assert stopped_at(result, "TARGET_NOT_FOUND", 0)
    assert app.presses == []


@needs_ids
def test_replay_stops_when_the_named_target_lost_its_id(tmp_path):
    """An id is added to the name, never in place of it: the menu still named 메뉴 but without
    its id is not the target."""
    result, _ = replay(tmp_path, id_steps(), AppOptions(ids={"Shop 열기": "7300"}, id_area="tb"))

    assert stopped_at(result, "TARGET_NOT_FOUND", 0)


def test_a_workflow_without_ids_replays_the_same_on_an_app_with_ids(tmp_path):
    """A workflow saved before ids were written does not look at ids, wherever the app has them."""
    result, app = replay(tmp_path, full_steps(), AppOptions(ids=IDS, id_area="tb"))

    assert result.status == "verified"
    assert app.applied == {"WA0", "WA1"}
