"""Making a workflow from a finished run (agent/workflow_author.py).

The list run (tests/workflow_support.py) numbers its actions 1 to 15: 2 the menu button,
3 to 6 a detour in the 기간 row, 7 the third nameless button of the Shop row, 8 a refused
mouse_click, 9 and 10 WA2 and WA3 in the list, 11 Select, 12 조회 (a busy window opens and
closes by itself), 13 엑셀 (a save dialog of its own), 14 the typed path, 15 저장.
"""

import json
import shutil
from datetime import date
from types import SimpleNamespace

import pytest
from workflow_support import LIST_GOAL, LIST_RUN_ID, LIST_SYSTEM, element, list_run, write_run

import agent.workflow_author as author
from agent.locator import find, in_scope
from agent.loop import RunResult
from agent.run_record import read_record
from agent.workflow_author import (
    PROPOSE_WORKFLOW,
    AuthorError,
    after_run,
    build,
    draft,
    make_workflow,
)
from agent.workflow_format import check_workflow
from agent.workflow_store import WorkflowStore

DAY = date(2026, 11, 19)
SHOP_LIST = 9
LIST_ALL, LIST_WA0, LIST_WA1, LIST_SHOP_LABEL = 2, 4, 5, 13


def list_draft():
    """The draft that gives the plan's nine steps for the list run."""
    return {
        "params": [{"name": "shop", "kind": "list"}, {"name": "save_path", "kind": "path"}],
        "steps": [
            act(2, "생산현황 화면이 열림"),
            act(7, "Shop 목록이 열림"),
            {"kind": "select_only", "goal": "Shop이 고른 값만 선택됨", "action": None, "actions": [9, 10],
             "observation": SHOP_LIST, "all_mark": LIST_ALL, "item_marks": [LIST_WA0, LIST_WA1], "param": "shop"},
            act(11, "목록이 닫힘"),
            act(12, "조회가 끝나 표가 채워짐"),
            act(13, "저장 창이 열림"),
            act(14, "경로를 넣음", "save_path"),
            act(15, "저장함"),
            {"kind": "check_file", "goal": "파일이 저장됨", "action": None, "actions": None, "observation": None,
             "all_mark": None, "item_marks": None, "param": "save_path"},
        ],
    }


def act(action, goal, param=None):
    return {"kind": "act", "goal": goal, "action": action, "actions": None, "observation": None,
            "all_mark": None, "item_marks": None, "param": param}


class DraftModel:
    """Answers once with a propose_workflow call holding `proposal`, or with no call."""

    def __init__(self, proposal=None, tokens=11, error=None):
        self.proposal = proposal
        self.tokens = tokens
        self.error = error
        self.calls = []

    def respond(self, instructions, items, tools, effort=None):
        self.calls.append({"instructions": instructions, "items": items, "tools": tools})
        if self.error is not None:
            raise self.error
        output = []
        if self.proposal is not None:
            output.append(SimpleNamespace(type="function_call", name="propose_workflow", call_id="c1",
                                          arguments=json.dumps(self.proposal, ensure_ascii=False)))
        return SimpleNamespace(output=output, usage=SimpleNamespace(total_tokens=self.tokens))


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    return list_run(tmp_path_factory.mktemp("list"))


@pytest.fixture
def run_dir(recorded, tmp_path):
    """A private copy of the list run, safe to edit."""
    return shutil.copytree(recorded, tmp_path / "runs" / recorded.name)


def events_of(run_dir):
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]


def rewrite(run_dir, change):
    events = events_of(run_dir)
    change(events)
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                                          encoding="utf-8")


def action_event(events, n):
    return [e for e in events if e.get("type") == "action"][n - 1]


def observation_event(events, observation_id):
    return next(e for e in events if e.get("type") == "observation" and e.get("id") == observation_id)


def built(run_dir, proposal=None):
    return build(read_record(run_dir), proposal or list_draft())


def step(workflow, n):
    return workflow["steps"][n - 1]


def test_the_list_run_becomes_the_expected_workflow(run_dir):
    record = read_record(run_dir)
    workflow = build(record, list_draft())
    assert [s["do"] for s in workflow["steps"]] == ["act", "act", "select_only", "act", "act", "act", "act", "act",
                                                   "check_file"]
    assert [s["n"] for s in workflow["steps"]] == list(range(1, 10))
    assert step(workflow, 1)["tool"] == "click"
    assert step(workflow, 1)["target"] == {"scope": "main", "role": "button", "name": "전공정 생산현황"}
    assert step(workflow, 2)["target"] == {"scope": "main", "role": "button",
                                           "near": {"name": "Shop", "relation": "same_row", "nth": 3}}
    select = step(workflow, 3)
    assert select == {"n": 3, "goal": "Shop이 고른 값만 선택됨", "do": "select_only", "scope": {"opened_by": 2},
                      "all": {"role": "statictext", "name": "All"}, "item_role": "statictext", "items": "$shop"}
    assert step(workflow, 4)["tool"] == "click_part"
    assert step(workflow, 4)["args"] == {"part": "center", "action": "click"}
    assert step(workflow, 4)["target"] == {"scope": {"opened_by": 2}, "role": "statictext", "name": "Select"}
    assert step(workflow, 7)["target"] == {"scope": {"opened_by": 6, "top": True}, "role": "edit",
                                           "name": "파일 이름"}
    assert step(workflow, 7)["value"] == "$save_path"
    assert step(workflow, 8)["target"] == {"scope": {"opened_by": 6, "top": True}, "role": "button", "name": "저장"}
    assert step(workflow, 9) == {"n": 9, "goal": "파일이 저장됨", "do": "check_file", "path": "$save_path"}
    assert workflow["params"] == {"shop": {"kind": "list", "example": ["WA0", "WA1"]},
                                  "save_path": {"kind": "path", "example": record.saved_path}}
    assert workflow["fixed"] == [{"name": "period", "value": "전일"}]
    assert (workflow["format"], workflow["system"], workflow["goal"], workflow["permission_level"]) == (
        1, LIST_SYSTEM, LIST_GOAL, 2)
    assert workflow["source"] == {"run_id": LIST_RUN_ID, "date": "2026-11-19"}
    for key in ("id", "status", "passes", "failures", "verified_with_other_values"):
        assert key not in workflow


def test_the_model_sees_no_coordinates_and_gives_no_target_text(run_dir, tmp_path):
    model = DraftModel(list_draft())
    proposal, tokens = draft(model, read_record(run_dir))
    assert proposal == list_draft() and tokens == 11
    sent = json.dumps(model.calls[0]["items"], ensure_ascii=False)
    for word in ("rect", "bounds", "points", '\\"x\\"', '\\"y\\"', "@m:e", "@d:e", "w-main", "w-save"):
        assert word not in sent, word
    assert "Shop" in sent and "[1]" in sent
    assert model.calls[0]["tools"] == [PROPOSE_WORKFLOW]
    schema = PROPOSE_WORKFLOW["parameters"]
    step_fields = set(schema["properties"]["steps"]["items"]["properties"])
    assert step_fields == {"kind", "goal", "action", "actions", "observation", "all_mark", "item_marks", "param"}
    assert set(schema["properties"]["params"]["items"]["properties"]) == {"name", "kind"}
    assert PROPOSE_WORKFLOW["strict"] is True and PROPOSE_WORKFLOW["name"] == "propose_workflow"


def test_the_model_sees_the_plan_the_stages_and_the_delivered_actions(run_dir):
    model = DraftModel(list_draft())
    draft(model, read_record(run_dir))
    material = json.loads(model.calls[0]["items"][0]["content"])
    assert material["goal"] == LIST_GOAL
    assert material["params"] == [{"name": "shop", "value": "WA0, WA1"}, {"name": "period", "value": "전일"}]
    assert material["stages"][0]["note"] == "기간과 Shop 줄이 보인다"
    numbers = [item["n"] for item in material["actions"]]
    assert 8 not in numbers and {2, 7, 9, 10, 14, 15} <= set(numbers)
    by_n = {item["n"]: item for item in material["actions"]}
    assert by_n[2]["target"] == {"role": "button", "name": "전공정 생산현황"}
    assert by_n[14]["value"] == read_record(run_dir).saved_path
    assert by_n[7]["windows_opened"] == 1 and by_n[11]["windows_closed"] == 1
    assert by_n[9]["seen_in"] in material["observations"] or str(by_n[9]["seen_in"]) in material["observations"]
    shown = material["observations"]
    assert any("All" in text and "WA0" in text for text in shown.values())


def test_the_instructions_are_english_and_name_no_application():
    instructions = author.DRAFT_INSTRUCTIONS
    assert instructions.isascii()
    assert "propose_workflow" in instructions


def test_expect_comes_from_the_recorded_effects(run_dir):
    workflow = built(run_dir)
    assert step(workflow, 2)["expect"] == "window_opened"
    assert (step(workflow, 4)["expect"], step(workflow, 4)["closes"]) == ("window_closed", 2)
    assert step(workflow, 6)["expect"] == "window_opened"
    assert step(workflow, 7)["expect"] == "value_changed"
    assert (step(workflow, 8)["expect"], step(workflow, 8)["closes"]) == ("window_closed", 6)
    assert step(workflow, 1)["expect"] == "content_changed"
    for n in (1, 2, 4, 6, 7, 8):
        assert "settle" not in step(workflow, n)


def hand_run(tmp_path, actions, observations, saved_path=None):
    """A run of hand-written observations (id → elements, window) and actions."""
    events = [{"type": "plan", "stages": [{"goal": "g", "done_when": "d"}], "params": []}]
    order = sorted(set(observations))
    for observation_id in order:
        elements, window, source = observations[observation_id]
        events.append({"type": "observation", "id": observation_id, "source": source, "elements": elements,
                       "window": {"id": window, "title": window}, "text": f"[1] {window}"})
        for item in actions:
            if item["before"] == observation_id:
                events.append({"type": "action", **item})
    if saved_path:
        events.append({"type": "finish_note", "by": "human", "saved_path": saved_path})
    return write_run(tmp_path, events)


def effects(opened=(), closed=(), added=0, none=False, picture=None):
    found = {"windows_opened": [{"id": w, "title": w} for w in opened],
             "windows_closed": [{"id": w, "title": w} for w in closed],
             "added": {"count": added, "names": []}, "removed": {"count": 0, "names": []},
             "changed": [], "focus": None, "none": none}
    if picture is not None:
        found["picture"] = picture
    return found


def click(ref, before, result, name=None, tool="click", **arguments):
    return {"step": 1, "tool": tool, "intent": "i", "arguments": {"ref": ref, **arguments},
            "target": {"ref": ref, "role": "button", "name": name}, "result": result, "before": before}


def box(x, y, w=40, h=30):
    return {"x": x, "y": y, "width": w, "height": h}


def buttons(*names):
    return [element(n, "button", name, ref=f"@h:e{n}", rect=box(10 + 60 * n, 10)) for n, name in enumerate(names, 1)]


def test_a_picture_only_verdict_gives_picture_changed_and_an_unmet_one_none(tmp_path):
    main = buttons("확인", "다음")
    run = hand_run(tmp_path, [
        click("@h:e1", 1, {"ok": True, "effects": effects(none=True, picture={"changed": True, "pixels": 9}),
                           "expect": "value_changed", "expected_met": None, "verdict": "unconfirmed"}, "확인"),
        click("@h:e2", 2, {"ok": True, "effects": effects(none=True), "expect": "content_changed",
                           "expected_met": False}, "다음"),
    ], {1: (main, "w-1", "observe"), 2: (main, "w-1", "action"), 3: (main, "w-1", "action")})
    workflow = build(read_record(run), {"params": [], "steps": [act(1, "a"), act(2, "b")]})
    assert step(workflow, 1)["expect"] == "picture_changed"
    assert step(workflow, 2)["expect"] is None


def test_a_window_that_closed_by_itself_gives_settle_and_content_changed(run_dir):
    workflow = built(run_dir)
    assert (step(workflow, 5)["expect"], step(workflow, 5).get("settle")) == ("content_changed", True)


def test_a_window_still_open_at_the_end_is_not_settled(tmp_path):
    main = buttons("알림 열기")
    popup = [element(1, "window", "알림", rect=box(300, 300, 200, 100)), *[
        {**e, "mark": e["mark"] + 1} for e in main]]
    run = hand_run(tmp_path, [
        click("@h:e1", 1, {"ok": True, "effects": effects(opened=["알림"], added=1), "expect": "content_changed",
                           "expected_met": True}, "알림 열기"),
    ], {1: (main, "w-1", "observe"), 2: (popup, "w-1", "action")})
    workflow = build(read_record(run), {"params": [], "steps": [act(1, "a")]})
    assert step(workflow, 1)["expect"] == "window_opened"
    assert "settle" not in step(workflow, 1)


def test_a_window_closed_by_a_later_action_is_not_settled(tmp_path):
    main = buttons("열기", "닫기")
    run = hand_run(tmp_path, [
        click("@h:e1", 1, {"ok": True, "effects": effects(opened=["알림"], added=1), "expect": "content_changed",
                           "expected_met": True}, "열기"),
        click("@h:e2", 2, {"ok": True, "effects": effects(closed=["알림"]), "expect": "content_changed",
                           "expected_met": True}, "닫기"),
    ], {1: (main, "w-1", "observe"), 2: (main, "w-1", "action"), 3: (main, "w-1", "action")})
    workflow = build(read_record(run), {"params": [], "steps": [act(1, "a"), act(2, "b")]})
    assert step(workflow, 1)["expect"] == "window_opened" and "settle" not in step(workflow, 1)
    assert (step(workflow, 2)["expect"], step(workflow, 2)["closes"]) == ("window_closed", 1)


def test_a_window_pressed_inside_is_not_settled_even_when_no_close_was_recorded(tmp_path):
    main = buttons("열기")
    popup = [element(1, "window", "알림", rect=box(300, 300, 200, 100)),
             element(2, "button", "확인", scope=1, ref="@h:e9", rect=box(320, 350)),
             {**main[0], "mark": 3}]
    run = hand_run(tmp_path, [
        click("@h:e1", 1, {"ok": True, "effects": effects(opened=["알림"], added=2), "expect": "content_changed",
                           "expected_met": True}, "열기"),
        click("@h:e9", 2, {"ok": True, "effects": effects(), "expect": "content_changed", "expected_met": False},
              "확인"),
    ], {1: (main, "w-1", "observe"), 2: (popup, "w-1", "action"), 3: (main, "w-1", "action")})
    workflow = build(read_record(run), {"params": [], "steps": [act(1, "a"), act(2, "b")]})
    assert step(workflow, 1)["expect"] == "window_opened" and "settle" not in step(workflow, 1)
    assert step(workflow, 2)["target"]["scope"] == {"opened_by": 1}


def test_a_parameter_example_is_never_the_anchor(tmp_path):
    rows = [
        element(1, "edit", "검색", ref="@h:e1", rect=box(10, 10, 200, 30)),
        element(2, "statictext", "라인", rect=box(10, 100, 60, 30)),
        element(3, "statictext", "L1", rect=box(80, 100, 60, 30)),
        element(4, "button", None, ref="@h:e4", rect=box(150, 100, 30, 30)),
        element(5, "statictext", "공정", rect=box(10, 150, 60, 30)),
        element(6, "statictext", "P1", rect=box(80, 150, 60, 30)),
        element(7, "button", None, ref="@h:e7", rect=box(150, 150, 30, 30)),
    ]
    typed = {"step": 1, "tool": "type", "intent": "i", "arguments": {"ref": "@h:e1", "text": "L1"},
             "target": {"ref": "@h:e1", "role": "edit", "name": "검색"}, "before": 1,
             "result": {"ok": True, "effects": effects(), "expect": "value_changed", "expected_met": False}}
    run = hand_run(tmp_path, [
        typed,
        click("@h:e4", 2, {"ok": True, "effects": effects(added=1), "expect": "content_changed",
                           "expected_met": True}),
    ], {1: (rows, "w-1", "observe"), 2: (rows, "w-1", "action"), 3: (rows, "w-1", "action")})
    proposal = {"params": [{"name": "line", "kind": "text"}], "steps": [act(1, "a", "line"), act(2, "b")]}
    workflow = build(read_record(run), proposal)
    assert step(workflow, 1)["value"] == "$line"
    assert step(workflow, 2)["target"]["near"] == {"name": "라인", "relation": "same_row", "nth": 1}
    plain = build(read_record(run), {"params": [], "steps": [act(1, "a"), act(2, "b")]})
    assert step(plain, 2)["target"]["near"]["name"] == "L1"


def test_a_parameter_on_a_named_target_replaces_the_name(run_dir):
    proposal = list_draft()
    proposal["params"].append({"name": "period", "kind": "text"})
    proposal["steps"][1:1] = [act(5, "기간 목록이 열림"), act(6, "기간을 고름", "period")]
    workflow = built(run_dir, proposal)
    chosen = step(workflow, 3)
    assert chosen["target"] == {"scope": {"opened_by": 2}, "role": "statictext", "name": "$period"}
    assert workflow["params"]["period"] == {"kind": "text", "example": "전일"}
    assert workflow["fixed"] == []
    assert step(workflow, 5)["scope"] == {"opened_by": 4}


def test_a_parameter_on_typed_text_replaces_the_value(run_dir):
    proposal = list_draft()
    proposal["params"] = [{"name": "shop", "kind": "list"}, {"name": "file_name", "kind": "text"}]
    proposal["steps"][6] = act(14, "경로를 넣음", "file_name")
    proposal["steps"].pop()
    workflow = built(run_dir, proposal)
    assert step(workflow, 7)["value"] == "$file_name"
    assert workflow["params"]["file_name"] == {"kind": "text", "example": read_record(run_dir).saved_path}


def test_typed_text_without_a_parameter_stays_as_recorded(run_dir):
    proposal = list_draft()
    proposal["params"] = [{"name": "shop", "kind": "list"}]
    proposal["steps"][6] = act(14, "경로를 넣음")
    proposal["steps"].pop()
    workflow = built(run_dir, proposal)
    assert step(workflow, 7)["value"] == read_record(run_dir).saved_path


def test_unused_plan_params_become_fixed(run_dir):
    proposal = list_draft()
    proposal["params"].append({"name": "unused", "kind": "text"})
    workflow = built(run_dir, proposal)
    assert set(workflow["params"]) == {"shop", "save_path"}
    assert workflow["fixed"] == [{"name": "period", "value": "전일"}]


def test_every_locator_is_checked_against_its_own_observation(run_dir):
    record = read_record(run_dir)
    workflow = build(record, list_draft())
    kept = {1: 2, 2: 7, 4: 11, 5: 12, 6: 13, 7: 14, 8: 15}
    for n, action_n in kept.items():
        action = record.actions[action_n - 1]
        observation = record.observations[action["before"]]
        target = workflow["steps"][n - 1]["target"]
        locator = {key: value for key, value in target.items() if key != "scope"}
        recorded = next(e for e in observation["elements"]
                        if (action["arguments"].get("ref") and e.get("ref") == action["arguments"]["ref"])
                        or e.get("mark") == action["arguments"].get("mark"))
        found = find(in_scope(observation["elements"], recorded["scope"]), locator)
        assert [e["mark"] for e in found] == [recorded["mark"]], n


def test_a_locator_that_finds_more_than_its_target_is_refused(run_dir, monkeypatch):
    monkeypatch.setattr(author, "describe", lambda scoped, mark, avoid=(): {"role": "button"})
    with pytest.raises(AuthorError) as caught:
        built(run_dir)
    assert caught.value.code == "LOCATOR_NOT_UNIQUE"


def test_a_value_that_changed_during_the_run_is_never_the_anchor(run_dir):
    proposal = list_draft()
    proposal["steps"].insert(1, act(5, "기간 목록이 열림"))
    workflow = built(run_dir, proposal)
    assert step(workflow, 2)["target"]["near"]["name"] == "기간"


def retool(n, tool, **arguments):
    def change(events):
        action = action_event(events, n)
        action["tool"] = tool
        action["arguments"] = {**arguments, "intent": "i"}
        action["target"] = None
    return change


def refuse(n):
    def change(events):
        action_event(events, n)["result"] = {"ok": False, "refused": True, "refused_code": "x"}
    return change


def rename(observation_id, mark, name):
    def change(events):
        for item in observation_event(events, observation_id)["elements"]:
            if item["mark"] == mark:
                item["name"] = name
    return change


def no_saved_path(events):
    events[:] = [e for e in events if e.get("type") != "finish_note"]


def bad_draft(change, run_dir):
    proposal = list_draft()
    if change == "act → click_point 동작":
        rewrite(run_dir, retool(8, "click_point", mark=2, x=5, y=5))
        proposal["steps"].insert(2, act(8, "누름"))
    elif change == "act → mouse_click 동작":
        proposal["steps"].insert(2, act(8, "누름"))
    elif change == "delivered mouse_click":
        rewrite(run_dir, lambda events: action_event(events, 8).update(
            result={"ok": True, "effects": effects(none=True)}))
        proposal["steps"].insert(2, act(8, "누름"))
    elif change == "없는 동작 번호":
        proposal["steps"][0] = act(99, "없음")
    elif change == "거절된 동작":
        rewrite(run_dir, refuse(11))
    elif change == "같은 동작을 두 번":
        proposal["steps"].insert(1, act(2, "다시"))
    elif change == "select_only가 쓴 동작을 act로":
        proposal["steps"].insert(3, act(10, "다시"))
    elif change == "기록 순서와 다른 순서":
        proposal["steps"][0], proposal["steps"][1] = proposal["steps"][1], proposal["steps"][0]
    elif change == "이름 없는 대상에 파라미터":
        proposal["params"].append({"name": "arrow", "kind": "text"})
        proposal["steps"][1] = act(7, "Shop 목록이 열림", "arrow")
    elif change == "list 파라미터를 act에":
        proposal["steps"][0] = act(2, "열림", "shop")
    elif change == "모르는 파라미터":
        proposal["steps"][0] = act(2, "열림", "nope")
    elif change == "select_only의 항목이 다른 범위":
        proposal["steps"][2]["item_marks"] = [LIST_WA0, LIST_SHOP_LABEL]
    elif change == "select_only의 항목 이름이 범위에서 둘":
        rewrite(run_dir, rename(SHOP_LIST, LIST_WA0 + 2, "WA0"))
    elif change == "select_only 항목이 다른 범위의 같은 이름":
        rewrite(run_dir, rename(SHOP_LIST, LIST_SHOP_LABEL, "WA2"))
        proposal["steps"][2]["item_marks"] = [LIST_WA0, LIST_SHOP_LABEL]
    elif change == "select_only에 같은 동작 둘":
        proposal["steps"][2]["actions"] = [9, 9, 10]
    elif change == "select_only가 앞 단계보다 앞선 동작":
        select = proposal["steps"].pop(2)
        proposal["steps"].insert(3, select)
    elif change == "같은 파라미터에 다른 값 둘":
        proposal["params"].append({"name": "screen", "kind": "text"})
        proposal["steps"][0] = act(2, "열림", "screen")
        proposal["steps"][3] = act(11, "닫힘", "screen")
    elif change == "지시가 빈 기록":
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        (run_dir / "run.json").write_text(json.dumps({**run, "goal": ""}, ensure_ascii=False), encoding="utf-8")
    elif change == "select_only의 동작이 다른 범위":
        rewrite(run_dir, lambda events: action_event(events, 10)["arguments"].update(mark=LIST_SHOP_LABEL))
    elif change == "select_only에 파라미터 없음":
        proposal["steps"][2]["param"] = None
    elif change == "select_only의 관찰이 없음":
        proposal["steps"][2]["observation"] = 999
    elif change == "path 파라미터인데 기록에 저장 경로 없음":
        rewrite(run_dir, no_saved_path)
    elif change == "path 파라미터가 저장 경로가 아닌 글에":
        rewrite(run_dir, lambda events: action_event(events, 14)["arguments"].update(text="C:\\other\\x.xlsx"))
    elif change == "scroll 동작":
        rewrite(run_dir, retool(3, "scroll", direction="down"))
        proposal["steps"].insert(1, act(3, "내림"))
    elif change == "launch_target":
        proposal["steps"].insert(0, act(1, "앱을 엶"))
    elif change == "빈 목표":
        proposal["steps"][0]["goal"] = " "
    elif change == "모르는 단계 종류":
        proposal["steps"][0]["kind"] = "wait"
    elif change == "기준을 못 찾는 대상":
        rewrite(run_dir, rename(6, 3, "Shop"))
    else:
        raise AssertionError(change)
    return proposal


BAD_DRAFTS = [
    ("act → click_point 동작", "PICTURE_TARGET"), ("act → mouse_click 동작", "PICTURE_TARGET"),
    ("delivered mouse_click", "PICTURE_TARGET"),
    ("없는 동작 번호", "DRAFT_INVALID"), ("거절된 동작", "DRAFT_INVALID"), ("같은 동작을 두 번", "DRAFT_INVALID"),
    ("select_only가 쓴 동작을 act로", "DRAFT_INVALID"),
    ("기록 순서와 다른 순서", "DRAFT_INVALID"), ("이름 없는 대상에 파라미터", "DRAFT_INVALID"),
    ("list 파라미터를 act에", "DRAFT_INVALID"), ("모르는 파라미터", "DRAFT_INVALID"),
    ("select_only의 항목이 다른 범위", "DRAFT_INVALID"), ("select_only의 항목 이름이 범위에서 둘", "DRAFT_INVALID"),
    ("select_only의 동작이 다른 범위", "DRAFT_INVALID"),
    ("select_only 항목이 다른 범위의 같은 이름", "DRAFT_INVALID"), ("select_only에 같은 동작 둘", "DRAFT_INVALID"),
    ("select_only가 앞 단계보다 앞선 동작", "DRAFT_INVALID"), ("같은 파라미터에 다른 값 둘", "DRAFT_INVALID"),
    ("지시가 빈 기록", "DRAFT_INVALID"), ("select_only에 파라미터 없음", "DRAFT_INVALID"),
    ("select_only의 관찰이 없음", "DRAFT_INVALID"),
    ("path 파라미터인데 기록에 저장 경로 없음", "DRAFT_INVALID"),
    ("path 파라미터가 저장 경로가 아닌 글에", "DRAFT_INVALID"), ("scroll 동작", "DRAFT_INVALID"),
    ("launch_target", "DRAFT_INVALID"), ("빈 목표", "DRAFT_INVALID"), ("모르는 단계 종류", "DRAFT_INVALID"),
    ("기준을 못 찾는 대상", "LOCATOR_NOT_UNIQUE"),
]


@pytest.mark.parametrize("change, code", BAD_DRAFTS)
def test_bad_drafts_raise_their_code(run_dir, change, code):
    proposal = bad_draft(change, run_dir)
    with pytest.raises(AuthorError) as caught:
        build(read_record(run_dir), proposal)
    assert caught.value.code == code


def test_author_errors_carry_no_screen_text(run_dir):
    for change, _code in BAD_DRAFTS:
        copy = run_dir.parent / f"copy-{BAD_DRAFTS.index((change, _code))}"
        shutil.copytree(run_dir, copy)
        try:
            build(read_record(copy), bad_draft(change, copy))
        except AuthorError as error:
            for word in ("전공정", "Shop", "WA0", "조회", "엑셀", "front.xlsx", "전일", "기간", "파일 이름"):
                assert word not in str(error), (change, str(error))


def test_no_tool_call_is_model_no_draft(run_dir, tmp_path):
    proposal, tokens = draft(DraftModel(None, tokens=5), read_record(run_dir))
    assert (proposal, tokens) == (None, 5)
    store = WorkflowStore(tmp_path / "wf", LIST_SYSTEM)
    logged = []
    outcome, tokens = make_workflow(DraftModel(None, tokens=5), run_dir, store, logged.append, today=DAY)
    assert outcome == {"created": False, "id": None, "code": "MODEL_NO_DRAFT", "steps": 0, "params": 0}
    assert tokens == 5 and store.entries() == []


def test_unreadable_arguments_are_model_no_draft(run_dir, tmp_path):
    class Garbled(DraftModel):
        def respond(self, instructions, items, tools, effort=None):
            response = super().respond(instructions, items, tools, effort)
            response.output[0].arguments = "{not json"
            return response

    assert draft(Garbled(list_draft(), tokens=3), read_record(run_dir)) == (None, 3)
    store = WorkflowStore(tmp_path / "wf", LIST_SYSTEM)
    outcome, _ = make_workflow(Garbled(list_draft()), run_dir, store, lambda _e: None, today=DAY)
    assert outcome["code"] == "MODEL_NO_DRAFT"


def test_make_workflow_saves_and_logs(run_dir, tmp_path):
    store = WorkflowStore(tmp_path / "wf", LIST_SYSTEM)
    logged = []
    outcome, tokens = make_workflow(DraftModel(list_draft(), tokens=40), run_dir, store, logged.append, today=DAY)
    assert outcome == {"created": True, "id": "w-20261119-001", "code": None, "steps": 9, "params": 2}
    assert tokens == 40
    assert logged == [{"type": "workflow", **outcome}]
    saved = store.get("w-20261119-001")
    assert check_workflow(saved) is saved
    assert saved["status"] == "unverified" and saved["passes"] == 0


@pytest.mark.parametrize("case, code", [
    ("no run", "RUN_NOT_FOUND"), ("bad draft", "DRAFT_INVALID"), ("model raises", "MODEL_NO_DRAFT"),
    ("store refuses", "SYSTEM_MISMATCH"), ("build breaks", "DRAFT_INVALID"),
])
def test_make_workflow_never_raises(run_dir, tmp_path, monkeypatch, case, code):
    store = WorkflowStore(tmp_path / "wf", LIST_SYSTEM)
    model = DraftModel(list_draft())
    target = run_dir
    if case == "no run":
        target = tmp_path / "nowhere"
    elif case == "bad draft":
        model = DraftModel({"params": [], "steps": [act(99, "없음")]})
    elif case == "model raises":
        model = DraftModel(error=RuntimeError("connection reset"))
    elif case == "store refuses":
        store = WorkflowStore(tmp_path / "wf", "other")
    elif case == "build breaks":
        monkeypatch.setattr(author, "in_scope", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
    logged = []
    outcome, _ = make_workflow(model, target, store, logged.append, today=DAY)
    assert outcome == {"created": False, "id": None, "code": code, "steps": 0, "params": 0}
    assert logged == [{"type": "workflow", **outcome}]
    assert store.entries() == []


def test_after_run_only_for_success_and_adds_tokens(run_dir, tmp_path):
    store = WorkflowStore(tmp_path / "wf", LIST_SYSTEM)
    for status in ("stopped", "claimed", "error"):
        model = DraftModel(list_draft(), tokens=30)
        result = RunResult(status, 5, "r", tokens=100)
        assert after_run(model, result, run_dir, store, lambda _e: None) == result
        assert model.calls == []
    for status in ("verified", "confirmed"):
        logged = []
        result = RunResult(status, 5, "r", tokens=100, stats={"k": 1})
        updated = after_run(DraftModel(list_draft(), tokens=30), result, run_dir, store, logged.append)
        assert (updated.status, updated.steps, updated.reason, updated.tokens, updated.stats) == (
            status, 5, "r", 130, {"k": 1})
        assert logged[0]["created"] is True
    assert len(store.entries()) == 2


def test_after_run_keeps_the_result_when_making_fails(tmp_path):
    store = WorkflowStore(tmp_path / "wf", LIST_SYSTEM)
    result = RunResult("confirmed", 5, "r", tokens=100)
    logged = []
    updated = after_run(DraftModel(error=RuntimeError("x")), result, tmp_path / "nowhere", store, logged.append)
    assert updated.status == "confirmed" and updated.tokens == 100
    assert logged[0]["created"] is False


def set_run_status(run_dir, status):
    path = run_dir / "run.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**run, "status": status}, ensure_ascii=False), encoding="utf-8")


def test_after_run_judges_a_run_that_is_still_running_by_its_result(run_dir, tmp_path):
    set_run_status(run_dir, "running")
    store = WorkflowStore(tmp_path / "wf", LIST_SYSTEM)
    logged = []

    updated = after_run(DraftModel(list_draft()), RunResult("confirmed", 5, "r", tokens=1), run_dir, store,
                        logged.append)

    assert logged[0]["created"] is True and updated.tokens == 12
    outcome, _ = make_workflow(DraftModel(list_draft()), run_dir, store, lambda _e: None)
    assert outcome["code"] == "RUN_NOT_SUCCESSFUL"
