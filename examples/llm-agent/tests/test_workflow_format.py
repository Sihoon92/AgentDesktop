"""The workflow file's shape and the values a replay runs with (agent/workflow_format.py)."""

import copy

import pytest

from agent.workflow_format import (
    ACT_TOOLS,
    EXPECTS,
    FORMAT,
    PARAM_KINDS,
    STEP_KINDS,
    VALUE_TOOLS,
    WorkflowError,
    check_workflow,
    other_values,
    param_name,
    resolve_values,
)


def sample(save_example="C:\\out\\front.xlsx"):
    """The design's example workflow, with every step kind."""
    return {
        "format": 1,
        "id": "w-20261119-001",
        "system": "virtualmes",
        "goal": "전공정 생산현황에서 Shop WA0, WA1을 고르고 조회해 엑셀로 저장",
        "source": {"run_id": "20261119-101500", "date": "2026-11-19"},
        "status": "unverified",
        "passes": 0,
        "failures": 0,
        "verified_with_other_values": False,
        "permission_level": 2,
        "params": {
            "shop": {"kind": "list", "example": ["WA0", "WA1"]},
            "save_path": {"kind": "path", "example": save_example},
        },
        "fixed": [{"name": "period", "value": "전일"}],
        "steps": [
            {"n": 1, "goal": "메뉴 목록이 열림", "do": "act", "tool": "click",
             "target": {"scope": "main", "role": "button", "name": "생산운영"}, "expect": "window_opened"},
            {"n": 2, "goal": "화면이 열림", "do": "act", "tool": "click",
             "target": {"scope": {"opened_by": 1}, "role": "button", "name": "전공정 생산현황"},
             "expect": "window_closed", "closes": 1},
            {"n": 3, "goal": "Shop 목록이 열림", "do": "act", "tool": "click",
             "target": {"scope": "main", "role": "button",
                        "near": {"name": "Shop", "relation": "same_row", "nth": 3}},
             "expect": "window_opened"},
            {"n": 4, "goal": "고른 값만 선택됨", "do": "select_only", "scope": {"opened_by": 3},
             "all": {"role": "statictext", "name": "All"}, "item_role": "statictext", "items": "$shop"},
            {"n": 5, "goal": "목록이 닫힘", "do": "act", "tool": "click_part",
             "args": {"part": "center", "action": "click"},
             "target": {"scope": {"opened_by": 3}, "role": "statictext", "name": "Select"},
             "expect": "window_closed", "closes": 3},
            {"n": 6, "goal": "조회가 끝남", "do": "act", "tool": "click",
             "target": {"scope": "main", "role": "button", "name": "조회(F5)"},
             "settle": True, "expect": "content_changed"},
            {"n": 7, "goal": "저장 창이 열림", "do": "act", "tool": "click", "args": {},
             "target": {"scope": "main", "role": "button", "description": "엑셀"}, "expect": "window_opened"},
            {"n": 8, "goal": "경로를 넣음", "do": "act", "tool": "type",
             "target": {"scope": {"opened_by": 7, "top": True}, "role": "edit", "name": "파일 이름:"},
             "value": "$save_path", "expect": "value_changed"},
            {"n": 9, "goal": "저장함", "do": "act", "tool": "press", "args": {"combo": "enter"},
             "target": {"scope": {"opened_by": 7, "top": True}}, "expect": "window_closed", "closes": 7},
            {"n": 10, "goal": "파일이 저장됨", "do": "check_file", "path": "$save_path"},
        ],
    }


def with_text_param():
    workflow = sample()
    workflow["params"]["screen"] = {"kind": "text", "example": "전공정 생산현황"}
    workflow["steps"][1]["target"]["name"] = "$screen"
    return workflow


def step(workflow, n):
    return workflow["steps"][n - 1]


def broken(change):
    workflow = sample()
    if change == "unknown top key":
        workflow["owner"] = "me"
    elif change == "unknown step key":
        step(workflow, 1)["wait"] = 3
    elif change == "step with x":
        step(workflow, 1)["x"] = 10
    elif change == "target with bounds":
        step(workflow, 1)["target"]["bounds"] = {"x": 1, "y": 2, "width": 3, "height": 4}
    elif change == "step with mark":
        step(workflow, 4)["all"]["mark"] = 7
    elif change == "step with ref":
        step(workflow, 6)["target"]["ref"] = "@s1:e2"
    elif change == "step with window_id":
        step(workflow, 2)["target"]["scope"]["window_id"] = "w-1"
    elif change == "points in args":
        step(workflow, 5)["args"]["points"] = [[1, 2]]
    elif change == "rect on select_only":
        step(workflow, 4)["rect"] = {"x": 1}
    elif change == "undeclared $name":
        step(workflow, 6)["target"]["name"] = "$button"
    elif change == "declared but unused param":
        workflow["params"]["line"] = {"kind": "text", "example": "1"}
    elif change == "list param on an act":
        step(workflow, 6)["target"]["name"] = "$shop"
    elif change == "path param on a target name":
        step(workflow, 6)["target"]["name"] = "$save_path"
    elif change == "text param on select_only items":
        workflow["params"]["shop"] = {"kind": "text", "example": "WA0"}
    elif change == "list param on check_file":
        step(workflow, 10)["path"] = "$shop"
        step(workflow, 4)["items"] = "$shop"
        workflow["params"]["shop"] = {"kind": "list", "example": ["WA0"]}
        step(workflow, 8)["value"] = "C:\\out\\a.xlsx"
    elif change == "literal items":
        step(workflow, 4)["items"] = "WA0"
    elif change == "opened_by a later step":
        step(workflow, 2)["target"]["scope"] = {"opened_by": 3}
    elif change == "opened_by itself":
        step(workflow, 3)["target"]["scope"] = {"opened_by": 3}
    elif change == "opened_by a check_file":
        workflow["steps"].insert(9, {"n": 10, "goal": "다시", "do": "act", "tool": "click",
                                     "target": {"scope": {"opened_by": 10}, "role": "button", "name": "a"},
                                     "expect": None})
        workflow["steps"][10]["n"] = 11
        workflow["steps"][9]["target"]["scope"] = {"opened_by": 9}
        workflow["steps"][8]["do"] = "check_file"
        for key in ("tool", "args", "target", "expect", "closes"):
            workflow["steps"][8].pop(key)
        workflow["steps"][8]["path"] = "$save_path"
    elif change == "top false":
        step(workflow, 8)["target"]["scope"] = {"opened_by": 7, "top": False}
    elif change == "unknown scope word":
        step(workflow, 1)["target"]["scope"] = "popup"
    elif change == "select_only in main scope":
        step(workflow, 4)["scope"] = "main"
    elif change == "closes without window_closed":
        step(workflow, 3)["closes"] = 1
    elif change == "closes a later step":
        step(workflow, 2)["closes"] = 3
    elif change == "settle false":
        step(workflow, 6)["settle"] = False
    elif change == "unknown tool":
        step(workflow, 1)["tool"] = "click_point"
    elif change == "unknown expect":
        step(workflow, 1)["expect"] = "opened"
    elif change == "unknown step kind":
        step(workflow, 1)["do"] = "wait"
    elif change == "steps not numbered from 1":
        for item in workflow["steps"]:
            item["n"] += 1
    elif change == "steps out of order":
        workflow["steps"][0]["n"], workflow["steps"][1]["n"] = 2, 1
    elif change == "no steps":
        workflow["steps"] = []
        workflow["params"] = {}
    elif change == "press with a role":
        step(workflow, 9)["target"]["role"] = "edit"
    elif change == "press without a combo":
        step(workflow, 9)["args"] = {}
    elif change == "click_part with a bad part":
        step(workflow, 5)["args"]["part"] = "corner"
    elif change == "click_part without args":
        step(workflow, 5).pop("args")
    elif change == "args on a click":
        step(workflow, 7)["args"] = {"part": "center"}
    elif change == "value on a click":
        step(workflow, 6)["value"] = "a"
    elif change == "type without a value":
        step(workflow, 8).pop("value")
    elif change == "empty value":
        step(workflow, 8)["value"] = " "
    elif change == "target without a role":
        step(workflow, 6)["target"].pop("role")
    elif change == "target without a scope":
        step(workflow, 6)["target"].pop("scope")
    elif change == "near without nth":
        step(workflow, 3)["target"]["near"].pop("nth")
    elif change == "near with a bad relation":
        step(workflow, 3)["target"]["near"]["relation"] = "left_of"
    elif change == "near with nth 0":
        step(workflow, 3)["target"]["near"]["nth"] = 0
    elif change == "empty name":
        step(workflow, 6)["target"]["name"] = ""
    elif change == "empty goal":
        step(workflow, 6)["goal"] = " "
    elif change == "format 2":
        workflow["format"] = 2
    elif change == "bad id":
        workflow["id"] = "w-1"
    elif change == "bad system":
        workflow["system"] = "a/b"
    elif change == "bad status":
        workflow["status"] = "passed"
    elif change == "negative passes":
        workflow["passes"] = -1
    elif change == "passes as bool":
        workflow["passes"] = True
    elif change == "verified flag as text":
        workflow["verified_with_other_values"] = "no"
    elif change == "permission level 4":
        workflow["permission_level"] = 4
    elif change == "source without date":
        workflow["source"].pop("date")
    elif change == "bad param name":
        workflow["params"]["Shop"] = workflow["params"].pop("shop")
        step(workflow, 4)["items"] = "$Shop"
    elif change == "unknown param kind":
        workflow["params"]["shop"]["kind"] = "number"
    elif change == "empty list example":
        workflow["params"]["shop"]["example"] = []
    elif change == "relative path example":
        workflow["params"]["save_path"]["example"] = "out.xlsx"
    elif change == "fixed named like a param":
        workflow["fixed"].append({"name": "shop", "value": "x"})
    elif change == "fixed twice":
        workflow["fixed"].append({"name": "period", "value": "x"})
    elif change == "missing top key":
        workflow.pop("permission_level")
    elif change == "not a mapping":
        return ["steps"]
    else:
        raise AssertionError(change)
    return workflow


BROKEN = [
    "unknown top key", "unknown step key", "step with x", "target with bounds", "step with mark", "step with ref",
    "step with window_id", "points in args", "rect on select_only", "undeclared $name", "declared but unused param",
    "list param on an act", "path param on a target name", "text param on select_only items", "list param on check_file",
    "literal items", "opened_by a later step", "opened_by itself", "opened_by a check_file", "top false",
    "unknown scope word", "select_only in main scope", "closes without window_closed", "closes a later step",
    "settle false", "unknown tool", "unknown expect", "unknown step kind", "steps not numbered from 1",
    "steps out of order", "no steps", "press with a role", "press without a combo", "click_part with a bad part",
    "click_part without args", "args on a click", "value on a click", "type without a value", "empty value",
    "target without a role", "target without a scope", "near without nth", "near with a bad relation",
    "near with nth 0", "empty name", "empty goal", "format 2", "bad id", "bad system", "bad status",
    "negative passes", "passes as bool", "verified flag as text", "permission level 4", "source without date",
    "bad param name", "unknown param kind", "empty list example", "relative path example",
    "fixed named like a param", "fixed twice", "missing top key", "not a mapping",
]


def test_the_constants():
    assert FORMAT == 1
    assert STEP_KINDS == ("act", "select_only", "check_file")
    assert "click_part" in ACT_TOOLS and "press" in ACT_TOOLS and "click_point" not in ACT_TOOLS
    assert VALUE_TOOLS == {"set_value": "value", "select": "value", "type": "text"}
    assert set(EXPECTS) == {"window_opened", "window_closed", "content_changed", "value_changed", "any",
                            "picture_changed"}
    assert PARAM_KINDS == ("text", "list", "path")


def test_the_sample_passes():
    workflow = sample()
    assert check_workflow(workflow) is workflow
    assert check_workflow(with_text_param())


def test_an_act_without_expect_or_args_passes():
    workflow = sample()
    step(workflow, 1)["expect"] = None
    check_workflow(workflow)
    step(workflow, 1).pop("expect")
    check_workflow(workflow)


def test_a_window_closed_without_closes_passes():
    workflow = sample()
    step(workflow, 2).pop("closes")
    check_workflow(workflow)


@pytest.mark.parametrize("change", BROKEN)
def test_broken_workflows_are_refused(change):
    with pytest.raises(WorkflowError) as caught:
        check_workflow(broken(change))
    assert caught.value.code == "WORKFLOW_INVALID"


def test_the_message_names_the_step_and_key_but_not_the_value():
    workflow = sample()
    step(workflow, 6)["target"]["name"] = "$secret_screen_word"
    with pytest.raises(WorkflowError) as caught:
        check_workflow(workflow)
    message = str(caught.value)
    assert "6" in message and "secret_screen_word" not in message
    workflow = sample()
    step(workflow, 6)["target"]["bounds"] = {"x": 1}
    with pytest.raises(WorkflowError) as caught:
        check_workflow(workflow)
    assert "6" in str(caught.value) and "bounds" in str(caught.value) and "조회" not in str(caught.value)
    assert "좌표" in str(caught.value)


def test_messages_carry_no_screen_text():
    for change in BROKEN:
        try:
            check_workflow(broken(change))
        except WorkflowError as error:
            text = str(error)
            for word in ("생산운영", "전공정", "조회", "Shop", "WA0", "front.xlsx", "전일", "엑셀", "C:\\out"):
                assert word not in text, (change, text)


def test_the_checked_workflow_is_not_changed():
    workflow = sample()
    before = copy.deepcopy(workflow)
    check_workflow(workflow)
    assert workflow == before


def test_param_name():
    assert param_name("$shop") == "shop"
    assert param_name("$save_path2") == "save_path2"
    assert param_name("shop") is None
    assert param_name("$Shop") is None
    assert param_name("$") is None
    assert param_name(None) is None
    assert param_name(3) is None


def test_omitted_values_fall_back_to_the_example(tmp_path):
    workflow = with_text_param()
    workflow["params"]["save_path"]["example"] = str(tmp_path / "front.xlsx")
    values = resolve_values(workflow, {}, "20261120-090000")
    assert values["shop"] == ["WA0", "WA1"]
    assert values["screen"] == "전공정 생산현황"


def test_an_omitted_path_gets_a_new_name_in_the_example_folder(tmp_path):
    (tmp_path / "front.xlsx").write_text("old", encoding="utf-8")
    workflow = sample(save_example=str(tmp_path / "front.xlsx"))
    values = resolve_values(workflow, {}, "20261120-090000")
    assert values["save_path"] == str(tmp_path / "front-20261120-090000.xlsx")


def test_an_omitted_path_whose_new_name_exists_is_refused(tmp_path):
    (tmp_path / "front-20261120-090000.xlsx").write_text("x", encoding="utf-8")
    workflow = sample(save_example=str(tmp_path / "front.xlsx"))
    with pytest.raises(WorkflowError) as caught:
        resolve_values(workflow, {}, "20261120-090000")
    assert caught.value.code == "FILE_EXISTS"


def test_an_omitted_path_in_a_missing_folder_is_refused(tmp_path):
    workflow = sample(save_example=str(tmp_path / "gone" / "front.xlsx"))
    with pytest.raises(WorkflowError) as caught:
        resolve_values(workflow, {}, "20261120-090000")
    assert caught.value.code == "PARAM_INVALID"


def test_a_given_path_is_trimmed_and_kept(tmp_path):
    workflow = sample(save_example=str(tmp_path / "front.xlsx"))
    target = tmp_path / "new.xlsx"
    assert resolve_values(workflow, {"save_path": f"  {target} "}, "r")["save_path"] == str(target)


def test_list_values_are_split_trimmed_and_deduplicated(tmp_path):
    workflow = sample(save_example=str(tmp_path / "front.xlsx"))
    assert resolve_values(workflow, {"shop": "WA0, WA1,WA0"}, "r")["shop"] == ["WA0", "WA1"]
    assert resolve_values(workflow, {"shop": " WA3 "}, "r")["shop"] == ["WA3"]


def test_a_text_value_is_trimmed(tmp_path):
    workflow = with_text_param()
    workflow["params"]["save_path"]["example"] = str(tmp_path / "front.xlsx")
    assert resolve_values(workflow, {"screen": "  후공정 생산현황 "}, "r")["screen"] == "후공정 생산현황"
    with pytest.raises(WorkflowError) as caught:
        resolve_values(workflow, {"screen": "   "}, "r")
    assert caught.value.code == "PARAM_INVALID"


@pytest.mark.parametrize("given, code", [
    ({"nope": "1"}, "PARAM_UNKNOWN"),
    ({"period": "전주"}, "PARAM_UNKNOWN"),
    ({"shop": "WA0,,WA1"}, "PARAM_INVALID"),
    ({"shop": "WA0, WA1,,WA0"}, "PARAM_INVALID"),
    ({"shop": " , "}, "PARAM_INVALID"),
    ({"shop": ""}, "PARAM_INVALID"),
    ({"save_path": "out.xlsx"}, "PARAM_INVALID"),
    ({"save_path": "\\out\\a.xlsx"}, "PARAM_INVALID"),
    ({"save_path": "MISSING"}, "PARAM_INVALID"),
    ({"save_path": " "}, "PARAM_INVALID"),
])
def test_bad_values_are_refused_before_the_run(tmp_path, given, code):
    if given.get("save_path") == "MISSING":
        given = {"save_path": str(tmp_path / "없는 폴더" / "a.xlsx")}
    workflow = sample(save_example=str(tmp_path / "front.xlsx"))
    with pytest.raises(WorkflowError) as caught:
        resolve_values(workflow, given, "20261120-090000")
    assert caught.value.code == code


def test_value_errors_carry_the_name_but_not_the_value(tmp_path):
    workflow = sample(save_example=str(tmp_path / "front.xlsx"))
    with pytest.raises(WorkflowError) as caught:
        resolve_values(workflow, {"shop": "SECRET1,,SECRET2"}, "r")
    assert "shop" in str(caught.value) and "SECRET" not in str(caught.value)
    with pytest.raises(WorkflowError) as caught:
        resolve_values(workflow, {"save_path": str(tmp_path / "hidden_dir" / "a.xlsx")}, "r")
    assert "hidden_dir" not in str(caught.value)


def test_an_existing_save_path_is_refused(tmp_path):
    existing = tmp_path / "a.xlsx"
    existing.write_text("x", encoding="utf-8")
    workflow = sample(save_example=str(tmp_path / "front.xlsx"))
    with pytest.raises(WorkflowError) as caught:
        resolve_values(workflow, {"save_path": str(existing)}, "r")
    assert caught.value.code == "FILE_EXISTS"


def test_other_values_ignores_the_path(tmp_path):
    workflow = with_text_param()
    example = {"shop": ["WA0", "WA1"], "save_path": str(tmp_path / "x.xlsx"), "screen": "전공정 생산현황"}
    assert other_values(workflow, example) is False
    assert other_values(workflow, {**example, "save_path": str(tmp_path / "other.xlsx")}) is False
    assert other_values(workflow, {**example, "shop": ["WA1", "WA0"]}) is False
    assert other_values(workflow, {**example, "shop": ["WA0"]}) is True
    assert other_values(workflow, {**example, "screen": "후공정 생산현황"}) is True
