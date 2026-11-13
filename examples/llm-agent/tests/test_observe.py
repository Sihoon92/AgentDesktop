import json

from agent.observe import render_marked, render_tree, visible_elements


def test_unnamed_wrapper_groups_disappear_and_children_move_up():
    tree = {
        "role": "window",
        "name": "Main",
        "available_actions": ["ScrollTo"],
        "children": [
            {
                "role": "group",
                "available_actions": ["ScrollTo"],
                "children": [
                    {
                        "role": "button",
                        "name": "Save",
                        "ref_id": "@s1:e1",
                        "available_actions": ["Click", "ScrollTo", "SetFocus"],
                    }
                ],
            },
            {"role": "group", "available_actions": ["ScrollTo"]},
        ],
    }

    text, refs = render_tree(tree)

    assert text == 'window "Main"\n  button "Save" @s1:e1 actions=Click'
    assert refs == {"@s1:e1": {"role": "button", "name": "Save", "description": None, "bounds": None}}


def test_refs_carry_bounds_for_drawing():
    bounds = {"x": 3705.0, "y": 118.0, "width": 108.0, "height": 45.0}
    tree = {"role": "switch", "name": "설정 ◂", "ref_id": "@s1:e1", "bounds": bounds}

    text, refs = render_tree(tree)

    assert refs["@s1:e1"]["bounds"] == bounds
    assert "bounds=" not in text


def test_description_states_value_and_truncation_are_visible_to_the_model():
    tree = {
        "role": "switch",
        "name": "icon",
        "description": "Magnet",
        "value": "on",
        "states": ["checked"],
        "ref_id": "@s1:e2",
        "available_actions": ["Toggle"],
        "children_count": 4,
        "subtree_truncated": True,
    }

    text, _ = render_tree(tree)

    assert text == (
        'switch "icon" @s1:e2 desc="Magnet" value="on" states=checked '
        "actions=Toggle children=4 truncated"
    )


def test_bounds_appear_only_when_requested():
    tree = {
        "role": "button",
        "name": "A",
        "ref_id": "@s1:e1",
        "bounds": {"x": 1.0, "y": 2.0, "width": 30.0, "height": 40.0},
    }

    assert "bounds=" not in render_tree(tree)[0]
    assert render_tree(tree, include_bounds=True)[0] == (
        'button "A" @s1:e1 bounds=1,2,30,40'
    )


def test_real_footprint_tree_keeps_settings_evidence_and_every_ref(fixture_json):
    snapshot = fixture_json("snapshot_settings_open")["data"]

    text, refs = render_tree(snapshot["tree"])

    assert len(refs) == 43
    assert 'switch "설정 ▸"' in text
    assert 'desc="오른쪽 지표 설정 패널 펼치기·접기"' in text
    assert 'statictext "지표"' in text
    assert "자석: 점을 가격 단위 칸과 봉 경계에 맞춘다" in text
    assert len(text.encode("utf-8")) < len(json.dumps(snapshot).encode("utf-8")) / 2


def test_visible_elements_follow_the_rendered_lines_in_tree_order():
    label_bounds = {"x": 10.0, "y": 20.0, "width": 40.0, "height": 24.0}
    tree = {
        "role": "window",
        "name": "Main",
        "children": [
            {"role": "group", "children": [
                {"role": "button", "name": "Save", "ref_id": "@s1:e1", "states": ["focused"]},
                {"role": "statictext", "name": "지표", "bounds": label_bounds},
            ]},
            {"role": "edit", "value": "15", "description": "기간"},
            {"role": "group", "subtree_truncated": True},
            {"role": "image"},
        ],
    }

    elements = visible_elements(tree)

    assert elements == [
        {"kind": "control", "ref": "@s1:e1", "role": "button", "name": "Save", "description": None,
         "states": ["focused"], "value": None, "bounds": None},
        {"kind": "text", "ref": None, "role": "statictext", "name": "지표", "description": None,
         "states": [], "value": None, "bounds": label_bounds},
        {"kind": "text", "ref": None, "role": "edit", "name": None, "description": "기간",
         "states": [], "value": "15", "bounds": None},
    ]
    assert render_tree(tree)[0] == (
        'window "Main"\n'
        '  button "Save" @s1:e1 states=focused\n'
        '  statictext "지표"\n'
        '  edit desc="기간" value="15"\n'
        "  group truncated"
    )


def test_a_subtree_root_that_is_not_a_window_is_an_element():
    tree = {"role": "list", "name": "지표 목록", "children": [{"role": "statictext", "name": "CVD"}]}

    assert [e["name"] for e in visible_elements(tree)] == ["지표 목록", "CVD"]


B = {"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0}


def test_marks_number_controls_and_text_in_tree_order():
    tree = {"role": "window", "name": "W", "children": [
        {"role": "statictext", "name": "라인", "bounds": B},
        {"role": "button", "name": "조회", "ref_id": "@s1:e1", "bounds": B},
        {"role": "group", "children": [{"role": "statictext", "name": "소계", "bounds": B}]}]}

    text, refs, marks = render_marked(tree)

    assert [marks[n]["name"] for n in sorted(marks)] == ["라인", "조회", "소계"]
    assert marks[1]["kind"] == "text" and marks[2]["ref"] == "@s1:e1"
    assert marks[3]["mark"] == 3 and marks[3]["bounds"] == B
    assert text.splitlines()[0] == 'window "W"'
    assert '[2] button "조회" @s1:e1' in text
    assert "@s1:e1" in refs


def test_marked_lines_keep_indentation_and_unmarked_lines():
    tree = {"role": "window", "name": "W", "children": [
        {"role": "group", "subtree_truncated": True, "children": [
            {"role": "statictext", "name": "가", "bounds": B}]}]}

    text, _, marks = render_marked(tree)

    assert text == 'window "W"\n  group truncated\n    [1] statictext "가"'
    assert list(marks) == [1]


def test_marks_are_the_visible_elements_with_a_number():
    tree = {"role": "list", "name": "목록", "children": [
        {"role": "statictext", "name": "CVD"}, {"role": "image"},
        {"role": "button", "name": "닫기", "ref_id": "@s1:e3"}]}

    _, _, marks = render_marked(tree)

    expected = [dict(element, mark=n) for n, element in enumerate(visible_elements(tree), 1)]
    assert [marks[n] for n in sorted(marks)] == expected


def test_render_tree_output_is_unchanged_by_marks():
    tree = {"role": "window", "name": "W", "children": [
        {"role": "statictext", "name": "라인", "bounds": B}]}

    assert "[1]" not in render_tree(tree)[0]
    assert render_marked(tree, include_bounds=True)[0].endswith('[1] statictext "라인" bounds=0,0,10,10')
