import json

import pytest
from workflow_support import LIST_ACTIONS, LIST_GOAL, LIST_PARAMS, LIST_RUN_ID, element, list_run, write_run

from agent.run_record import RecordError, read_record

WINDOW = {"id": "w-1", "title": "창", "bounds": None}


def observation(n, source="action", window=WINDOW, elements=None):
    return {"type": "observation", "id": n, "source": source, "window": dict(window), "frame": None, "image": None,
            "geometry_stale": False, "elements": [element(1, "button", "조회", ref="@s1:e1")] if elements is None
            else elements, "text_truncated": False, "text": "[1] button \"조회\"", "warning": None}


def action(tool="click", before=1, result=None, step=1):
    return {"type": "action", "step": step, "tool": tool, "intent": "누른다", "arguments": {"ref": "@s1:e1"},
            "target": {"ref": "@s1:e1", "role": "button", "name": "조회"}, "before": before,
            "result": {"ok": True} if result is None else result}


def plan(revision=0, params=None, goals=("가", "나")):
    stages = [{"n": n, "goal": goal, "done_when": f"{goal} 확인", "status": "pending"} for n, goal in enumerate(goals, 1)]
    return {"type": "plan", "revision": revision, "stages": stages, "params": [] if params is None else params, "step": 1}


def done(n, note):
    return {"type": "stage", "n": n, "status": "done", "calls": 1, "limit": 8, "kind": "text", "note": note, "step": 2}


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    folder = tmp_path_factory.mktemp("list")
    return folder, read_record(list_run(folder))


def test_actions_are_numbered_and_tied_to_their_observations(recorded):
    folder, record = recorded
    events = [json.loads(line) for line in (folder / "runs" / LIST_RUN_ID / "events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    written = [event for event in events if event["type"] == "action"]

    assert [a["n"] for a in record.actions] == list(range(1, len(LIST_ACTIONS) + 1))
    assert tuple(a["tool"] for a in record.actions) == LIST_ACTIONS
    assert [a["before"] for a in record.actions] == [event["before"] for event in written]
    assert [(a["step"], a["intent"], a["arguments"], a["target"], a["result"]) for a in record.actions] == [
        (e["step"], e["intent"], e["arguments"], e["target"], e["result"]) for e in written]
    after = {a["n"]: a["after"] for a in record.actions}
    assert record.observations[after[2]]["source"] == "action" and after[2] == record.actions[2]["before"]
    assert after[12] == 11 and record.observations[12]["source"] == "observe"
    assert after[13] == 13 and record.actions[13]["before"] == 14
    assert after[14] == 15 and after[15] == 16


def test_an_action_without_a_following_observation_has_no_after(tmp_path, recorded):
    _, listed = recorded
    path = write_run(tmp_path, [observation(1, "observe"), plan(), action(before=1), observation(2), action(before=2)])

    record = read_record(path)

    assert [a["after"] for a in record.actions] == [2, None]
    assert listed.actions[0]["tool"] == "launch_target" and listed.actions[0]["after"] is None
    assert listed.actions[7]["tool"] == "mouse_click" and listed.actions[7]["after"] is None


def test_an_observation_that_is_not_made_by_an_action_is_never_an_after(tmp_path):
    events = [observation(1, "observe"), plan(), action(before=1), observation(2, "observe"),
              observation(3, "question"), observation(4, "wait_for_text"), action(before=4), observation(5)]

    record = read_record(write_run(tmp_path, events))

    assert [a["after"] for a in record.actions] == [None, 5]


def test_refused_and_undelivered_actions_are_marked(tmp_path, recorded):
    _, listed = recorded
    results = [
        {"ok": True, "disposition": {"delivery": "delivered_unverified", "retry": "unsafe"}},
        {"ok": True},
        {"ok": False, "refused": "막힘", "refused_code": "needs_confirmation"},
        {"refused": "막힘"},
        {"ok": False, "code": "ACTION_FAILED"},
        {"ok": True, "disposition": {"delivery": "not_delivered", "retry": "safe"}},
    ]
    events = [observation(1, "observe"), plan()] + [action(result=result) for result in results]

    record = read_record(write_run(tmp_path, events))

    assert [a["delivered"] for a in record.actions] == [True, True, False, False, False, False]
    assert [a["delivered"] for a in listed.actions].count(False) == 1
    assert listed.actions[7]["delivered"] is False


def test_the_last_plan_wins_and_stage_notes_are_attached(tmp_path):
    first = plan(0, [{"name": "shop", "value": "WA0"}], ("가", "나"))
    second = plan(1, [{"name": "shop", "value": "WA0, WA1"}, {"name": "day", "value": "어제"}], ("가", "다", "라"))
    events = [observation(1, "observe"), first, done(1, "가 메모"), second, done(2, "다 메모 하나"),
              done(2, "다 메모 둘")]

    record = read_record(write_run(tmp_path, events))

    assert record.params == second["params"]
    assert [(s["n"], s["goal"], s["note"]) for s in record.stages] == [
        (1, "가", "가 메모"), (2, "다", "다 메모 둘"), (3, "라", None)]
    assert record.stages[0]["done_when"] == "가 확인"


def test_a_plan_without_params_gives_an_empty_list(tmp_path):
    old_plan = {key: value for key, value in plan().items() if key != "params"}

    assert read_record(write_run(tmp_path, [observation(1, "observe"), old_plan])).params == []


def test_saved_path_and_main_window(recorded):
    folder, record = recorded

    assert record.saved_path == str(folder / "out" / "front.xlsx")
    assert record.main_window == "w-main"
    assert (record.run_id, record.goal, record.system, record.permission_level) == (LIST_RUN_ID, LIST_GOAL, "mes", 2)
    assert record.params == LIST_PARAMS
    assert [s["note"] for s in record.stages] == ["기간과 Shop 줄이 보인다", "기간 칸에 전일이 보인다",
                                                  "목록이 닫히고 선택이 적용되었다", "표에 결과가 있다", "저장 창이 닫혔다"]


def test_the_last_finish_note_gives_the_saved_path_and_none_gives_none(tmp_path):
    notes = [{"type": "finish_note", "by": "human", "saved_path": "C:\\a.xlsx"},
             {"type": "finish_note", "by": "harness", "saved_path": "C:\\b.xlsx"}]
    base = [observation(1, "observe"), plan()]

    assert read_record(write_run(tmp_path, base + notes)).saved_path == "C:\\b.xlsx"
    assert read_record(write_run(tmp_path, base)).saved_path is None


def test_observations_are_kept_by_id_as_written(recorded):
    folder, record = recorded
    events = [json.loads(line) for line in (folder / "runs" / LIST_RUN_ID / "events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    written = {event["id"]: event for event in events if event["type"] == "observation"}

    assert record.observations == written
    assert sorted(record.observations) == list(range(1, 17))
    assert record.observations[14]["window"]["id"] == "w-save"


def test_the_list_run_has_a_value_that_comes_and_goes_beside_a_steady_label(recorded):
    _, record = recorded
    main = [o for o in record.observations.values() if o["window"]["id"] == "w-main" and o["id"] > 1]

    def main_names(o):
        return {e["name"] for e in o["elements"] if e["scope"] is None}

    assert all("기간" in main_names(o) for o in main)
    assert any("전일" not in main_names(o) for o in main) and any("전주" in main_names(o) for o in main)
    assert record.actions[2]["target"]["name"] is None and record.actions[4]["target"]["ref"] == "@m:e2"


def test_the_list_run_popups_are_window_elements_that_scope_their_items(recorded):
    _, record = recorded
    shop_list = record.observations[record.actions[6]["after"]]
    window = next(e for e in shop_list["elements"] if e["role"] == "window")
    items = [e["name"] for e in shop_list["elements"] if e["scope"] == window["mark"]]

    assert window["name"] == "목록" and window["scope"] is None
    assert items == ["All", "Select", "WA0", "WA1", "WA2", "WA3"]
    assert [w["title"] for w in record.actions[6]["result"]["effects"]["windows_opened"]] == ["목록"]


@pytest.mark.parametrize("case, code", [("no folder", "RUN_NOT_FOUND"), ("no run.json", "RUN_NOT_FOUND"),
                                        ("status stopped", "RUN_NOT_SUCCESSFUL"),
                                        ("status claimed", "RUN_NOT_SUCCESSFUL"),
                                        ("no plan event", "NO_PLAN"), ("elements without mark", "RECORD_TOO_OLD")])
def test_unusable_records_raise_their_code(tmp_path, case, code):
    events = [observation(1, "observe"), plan(), action()]
    if case == "no folder":
        path = tmp_path / "runs" / "nope"
    elif case == "no run.json":
        path = write_run(tmp_path, events)
        (path / "run.json").unlink()
    elif case.startswith("status"):
        path = write_run(tmp_path, events, status=case.split()[1])
    elif case == "no plan event":
        path = write_run(tmp_path, [observation(1, "observe"), action()])
    else:
        old = observation(2, elements=[{k: v for k, v in element(1, "button", "조회").items() if k not in ("mark", "scope")}])
        path = write_run(tmp_path, events + [old])

    with pytest.raises(RecordError) as raised:
        read_record(path)

    assert raised.value.code == code


@pytest.mark.parametrize("status", ["verified", "confirmed"])
def test_verified_and_confirmed_runs_are_read(tmp_path, status):
    assert read_record(write_run(tmp_path, [observation(1, "observe"), plan()], status=status)).run_id


def test_a_half_written_last_line_is_ignored(tmp_path):
    path = write_run(tmp_path, [observation(1, "observe"), plan(), action(), observation(2)])
    with (path / "events.jsonl").open("a", encoding="utf-8") as file:
        file.write('{"type": "action", "step": 9, "tool": "cli')

    record = read_record(path)

    assert len(record.actions) == 1 and record.actions[0]["after"] == 2


def test_reading_changes_nothing_in_the_folder(tmp_path):
    path = write_run(tmp_path, [observation(1, "observe"), plan(), action(), observation(2)])
    before = {p.name: p.read_bytes() for p in path.iterdir()}

    read_record(path)

    assert {p.name: p.read_bytes() for p in path.iterdir()} == before


def test_the_first_observation_made_by_the_action_is_its_after(tmp_path):
    events = [observation(1, "observe"), plan(), action(before=1), observation(2), observation(3)]

    assert read_record(write_run(tmp_path, events)).actions[0]["after"] == 2


def test_the_main_window_is_the_window_of_the_first_observation(tmp_path):
    other = {"id": "w-2", "title": "대화상자", "bounds": None}
    events = [observation(1, "observe"), plan(), action(), observation(2, window=other)]

    assert read_record(write_run(tmp_path, events)).main_window == "w-1"


def test_a_given_status_decides_success_in_place_of_run_json(tmp_path):
    running = write_run(tmp_path, [observation(1, "observe"), plan()], status="running")
    confirmed = write_run(tmp_path, [observation(1, "observe"), plan()], status="confirmed")

    assert read_record(running, status="confirmed").run_id == running.name
    with pytest.raises(RecordError) as raised:
        read_record(confirmed, status="stopped")
    assert raised.value.code == "RUN_NOT_SUCCESSFUL"
    with pytest.raises(RecordError):
        read_record(running)
