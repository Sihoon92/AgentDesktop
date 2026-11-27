"""Table cells in workflows: written with the find_rows conditions that found them, and found
again by those conditions on replay (agent/workflow_author.py, workflow_format.py, workflow_run.py)."""

import copy

import pytest
from fake_cli import FakeCli
from structure_support import EXE, OBSERVE, company, data_rows, scene, set_values

from agent.desktop import DesktopClient
from agent.loop import RunResult
from agent.recorder import RunRecorder
from agent.run_record import read_record
from agent.task import Task
from agent.tools import Toolbox
from agent.workflow_author import AuthorError, build
from agent.workflow_format import WorkflowError, check_workflow
from agent.workflow_run import run_workflow

COLUMNS = ["공정 row#", "구분 row#", "양품수 row#"]
AREA = {"role": "pane", "id": "pn_Main"}
SUBTOTAL = [{"column": "공정 row#", "value": "조립"}, {"column": "구분 row#", "value": "소계"}]
TWO_SUBTOTALS = [["극판", "소계", "980"], ["조립", "소계", "640"], ["극판", "소계", "120"]]


def recorded_run(tmp_path, where=SUBTOTAL, tree=None, gated=True, find=True, row=2, more=(), observe_again=False):
    """A confirmed run: observe, find_rows(where) unless not find, then find_rows of each `more`,
    observe once more when observe_again, and press 양품수 of `row`."""
    cli = FakeCli(scene=scene(tree or company()))
    desktop = DesktopClient("ad.exe", "run1", runner=cli)
    meta = {"goal": "양품수를 누른다", "exe": EXE, "permission_level": 2, "system": "practice"}
    recorder = RunRecorder(tmp_path / "runs", desktop, meta, heartbeat_s=60)
    try:
        toolbox = Toolbox(desktop, Task("goal", EXE, 2, []), confirm=lambda _p: False, sleep=lambda _s: None,
                          observer=recorder, gated=gated)
        toolbox.call("launch_target", {})
        toolbox.call("observe", OBSERVE)
        recorder.on_event({"type": "plan", "params": [], "stages": [{"n": 1, "goal": "누른다"}]})
        if find:
            toolbox.call("find_rows", {"table": "T1", "where": where, "columns": ["양품수 row#"]})
        for other in more:
            toolbox.call("find_rows", {"table": "T1", "where": other, "columns": ["양품수 row#"]})
        if observe_again:
            toolbox.call("observe", OBSERVE)
        mark = toolbox.structure.tables[0].rows[row].cells[2].mark
        result = toolbox.call("click_part", {"mark": mark, "part": "center", "action": "click",
                                             "intent": "누른다", "knowledge_id": None})
        assert result.get("ok") is not False, result
    finally:
        recorder.finish(RunResult("confirmed", 2, "done"))
    return recorder.path


def draft(param=None, kinds=("text",)):
    params = [{"name": param, "kind": kinds[0]}] if param else []
    return {"params": params, "steps": [{"kind": "act", "goal": "양품수 칸을 누름", "action": 2, "actions": None,
                                         "observation": None, "all_mark": None, "item_marks": None,
                                         "param": param}]}


def test_a_cell_found_by_find_rows_is_written_with_its_condition(tmp_path):
    workflow = build(read_record(recorded_run(tmp_path)), draft())
    step = workflow["steps"][0]
    assert step["tool"] == "click_part" and step["target"]["role"] == "cell"
    assert step["cell"] == {"table": {"role": "table", "in": AREA, "columns": COLUMNS}, "column": "양품수 row#",
                            "where": {"공정 row#": "조립", "구분 row#": "소계"}}


def test_with_stable_ids_the_table_is_told_by_its_id(tmp_path):
    workflow = build(read_record(recorded_run(tmp_path)), draft(), use_ids=True)
    assert workflow["steps"][0]["cell"]["table"] == {"role": "table", "id": "gc_Count", "in": AREA}


def test_a_where_value_equal_to_a_param_example_becomes_the_param(tmp_path):
    record = read_record(recorded_run(tmp_path, where=[{"column": "공정 row#", "value": "조립"}]))
    workflow = build(record, draft("process"))
    assert workflow["params"] == {"process": {"kind": "text", "example": "조립"}}
    assert workflow["steps"][0]["cell"]["where"] == {"공정 row#": "$process"}
    assert workflow["steps"][0]["target"]["name"] == "양품수 row2"


def test_a_param_on_a_cell_with_two_conditions_needs_its_example_from_elsewhere(tmp_path):
    with pytest.raises(AuthorError) as raised:
        build(read_record(recorded_run(tmp_path)), draft("process"))
    assert raised.value.code == "DRAFT_INVALID"


def test_the_latest_lookup_of_the_same_observation_gives_the_condition(tmp_path):
    first = [{"column": "공정 row#", "value": "조립"}]
    record = read_record(recorded_run(tmp_path, where=first, more=[SUBTOTAL]))
    assert build(record, draft())["steps"][0]["cell"]["where"] == {"공정 row#": "조립", "구분 row#": "소계"}


def test_a_lookup_on_an_earlier_observation_gives_no_condition(tmp_path):
    workflow = build(read_record(recorded_run(tmp_path, observe_again=True)), draft())
    assert "cell" not in workflow["steps"][0]


def test_a_cell_clicked_without_find_rows_is_written_as_before(tmp_path):
    workflow = build(read_record(recorded_run(tmp_path, find=False)), draft())
    assert "cell" not in workflow["steps"][0] and workflow["steps"][0]["target"]["name"] == "양품수 row2"


def test_a_cell_of_a_lookup_that_matched_two_rows_is_written_as_before(tmp_path):
    tree = set_values(company(), TWO_SUBTOTALS)
    where = [{"column": "공정 row#", "value": "극판"}, {"column": "구분 row#", "value": "소계"}]
    workflow = build(read_record(recorded_run(tmp_path, where=where, tree=tree, gated=False, row=0)), draft())
    assert "cell" not in workflow["steps"][0]


def test_the_action_view_shows_the_condition_to_the_author_model(tmp_path):
    from agent.workflow_author import _action_view

    record = read_record(recorded_run(tmp_path))
    assert _action_view(record, record.actions[1], 1)["found_by"] == {"공정 row#": "조립", "구분 row#": "소계"}


def cell_workflow(cell=None, name="양품수 row1"):
    cell = cell or {"table": {"role": "table", "in": AREA, "columns": COLUMNS}, "column": "양품수 row#",
                    "where": {"공정 row#": "조립", "구분 row#": "소계"}}
    return {"format": 1, "id": "w-20261127-001", "system": "practice", "goal": "g",
            "source": {"run_id": "20261127-100000", "date": "2026-11-27"}, "status": "unverified", "passes": 0,
            "failures": 0, "verified_with_other_values": False, "permission_level": 2, "params": {}, "fixed": [],
            "steps": [{"n": 1, "goal": "양품수 칸을 누름", "do": "act", "tool": "click_part",
                       "args": {"part": "center", "action": "click"},
                       "target": {"scope": "main", "role": "cell", "name": name}, "cell": cell, "expect": None}]}


def replay(tmp_path, tree, workflow, values=None):
    cli = FakeCli(scene=scene(tree))
    clock = iter(range(0, 100_000))
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), Task("goal", EXE, 2, []),
                      confirm=lambda _p: False, sleep=lambda _s: None, clock=lambda: next(clock), gated=False)
    result = run_workflow(toolbox, Task("goal", EXE, 2, []), workflow, values or {})
    clicks = [words for words in cli.commands() if words[0] == "mouse-click"]
    replay.snapshots = sum(1 for words in cli.commands() if words[0] == "snapshot")
    return result, clicks


def code(result):
    return result.stats["workflow_run"]["code"]


def center(row, column):
    return f"--xy={10 + 150 * column + 73},{200 + 24 * row + 11}"


def test_the_cell_workflow_meets_the_format():
    assert check_workflow(cell_workflow()) == cell_workflow()


def test_replay_finds_the_same_row_at_another_index(tmp_path):
    result, clicks = replay(tmp_path, company(), cell_workflow(name="양품수 row1"))
    assert result.status == "verified"
    assert len(clicks) == 1 and clicks[0][1] == center(2, 2)


def test_replay_takes_a_condition_from_a_param(tmp_path):
    workflow = cell_workflow({"table": {"role": "table", "in": AREA, "columns": COLUMNS}, "column": "양품수 row#",
                              "where": {"공정 row#": "$process"}})
    workflow["params"] = {"process": {"kind": "text", "example": "조립"}}
    assert check_workflow(workflow) == workflow
    result, clicks = replay(tmp_path, company(), workflow, {"process": "극판"})
    assert result.status == "verified" and clicks[0][1] == center(0, 2)


def test_replay_finds_a_table_by_its_id(tmp_path):
    workflow = cell_workflow({"table": {"role": "table", "id": "gc_Count", "in": AREA}, "column": "양품수 row#",
                              "where": {"공정 row#": "조립"}})
    result, clicks = replay(tmp_path, company(), workflow)
    assert result.status == "verified" and clicks[0][1] == center(2, 2)


def two_tables():
    tree = company()
    pane = tree["children"][0]
    while pane.get("native_id", {}).get("value") != "pn_Main":
        pane = pane["children"][0]
    pane["children"].append(copy.deepcopy(pane["children"][0]))
    return tree


def offscreen_subtotal():
    tree = company()
    data_rows(tree)[2]["children"][2].pop("bounds")
    return tree


@pytest.mark.parametrize(("tree", "change", "expected"), [
    (company, lambda w: w["steps"][0]["cell"]["table"].update(columns=["공정", "구분", "양품수"]), "CELL_TABLE_NOT_FOUND"),
    (two_tables, None, "CELL_TABLE_AMBIGUOUS"),
    (company, lambda w: w["steps"][0]["cell"]["where"].update({"공정 row#": "도포"}), "CELL_NOT_FOUND"),
    (lambda: set_values(company(), TWO_SUBTOTALS),
     lambda w: w["steps"][0]["cell"]["where"].update({"공정 row#": "극판"}), "CELL_AMBIGUOUS"),
    (offscreen_subtotal, None, "CELL_OFFSCREEN"),
])
def test_replay_stops_with_cell_codes(tmp_path, tree, change, expected):
    workflow = cell_workflow()
    if change is not None:
        change(workflow)
    result, clicks = replay(tmp_path, tree(), workflow)
    assert result.status == "stopped" and code(result) == expected and clicks == []
    waited = expected in ("CELL_TABLE_NOT_FOUND", "CELL_NOT_FOUND")
    assert (replay.snapshots > 3) == waited


def test_a_missing_cell_in_the_column_is_not_found(tmp_path):
    tree = company()
    data_rows(tree)[2]["children"].pop(2)
    result, _ = replay(tmp_path, tree, cell_workflow())
    assert code(result) == "CELL_NOT_FOUND"


@pytest.mark.parametrize("change", [
    lambda s: s["cell"].update(extra=1),
    lambda s: s["cell"]["table"].update(name="표"),
    lambda s: s["cell"]["table"].update(id="gc"),
    lambda s: s["cell"]["table"].pop("columns"),
    lambda s: s["cell"].update(column="없는 열"),
    lambda s: s["cell"].update(where={}),
    lambda s: s["cell"].update(where={"공정 row#": 3}),
    lambda s: s["cell"]["table"].update({"in": {"role": "pane"}}),
    lambda s: s.update(tool="click", args={}),
    lambda s: s["cell"].update(where={"공정 row#": "$nobody"}),
])
def test_format_rejects_unknown_cell_keys(change):
    workflow = cell_workflow()
    change(workflow["steps"][0])
    with pytest.raises(WorkflowError):
        check_workflow(workflow)

