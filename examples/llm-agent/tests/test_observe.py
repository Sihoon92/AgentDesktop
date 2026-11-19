import json

from agent.observe import render_marked, render_tree, visible_elements
from agent.profile import Profile


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
         "states": ["focused"], "value": None, "bounds": None, "mark": 1, "scope": None},
        {"kind": "text", "ref": None, "role": "statictext", "name": "지표", "description": None,
         "states": [], "value": None, "bounds": label_bounds, "mark": 2, "scope": None},
        {"kind": "text", "ref": None, "role": "edit", "name": None, "description": "기간",
         "states": [], "value": "15", "bounds": None, "mark": 3, "scope": None},
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


def test_native_ids_reach_refs_marks_and_the_recorded_elements_and_only_the_value_reaches_the_text():
    native = {"kind": "automation_id", "value": "7003"}
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "button", "ref_id": "@s1:e1", "native_id": native, "available_actions": ["Click"]},
        {"role": "button", "name": "Save", "ref_id": "@s1:e2", "native_id": {"kind": "automation_id", "value": ""}},
    ]}

    text, refs, marks = render_marked(tree)

    assert refs["@s1:e1"]["native_id"] == native and "native_id" not in refs["@s1:e2"]
    assert marks[1]["native_id"] == native and "native_id" not in marks[2]
    assert '[1] button @s1:e1 id="7003" actions=Click' in text and "automation_id" not in text
    assert [element.get("native_id") for element in visible_elements(tree)] == [native, None]


def _list_popup_tree():
    return {"role": "window", "name": "Main", "children": [
        {"role": "window", "name": "목록", "children": [
            {"role": "statictext", "name": "All", "bounds": B},
            {"role": "statictext", "name": "WA0", "bounds": B}]},
        {"role": "button", "name": "조회", "ref_id": "@s1:e1", "bounds": B}]}


def test_marks_carry_their_number_and_the_nearest_window_ancestor():
    _, _, marks = render_marked(_list_popup_tree())

    assert [(marks[n]["name"], marks[n]["mark"], marks[n]["scope"]) for n in sorted(marks)] == [
        ("목록", 1, None), ("All", 2, 1), ("WA0", 3, 1), ("조회", 4, None)]


def test_a_window_inside_a_window_scopes_to_the_inner_one():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "window", "name": "바깥", "children": [
            {"role": "statictext", "name": "가"},
            {"role": "group", "children": [
                {"role": "window", "name": "안쪽", "children": [{"role": "statictext", "name": "나"}]}]},
            {"role": "statictext", "name": "다"}]}]}

    scopes = {e["name"]: e["scope"] for e in visible_elements(tree)}
    marks = {e["name"]: e["mark"] for e in visible_elements(tree)}

    assert scopes == {"바깥": None, "가": marks["바깥"], "안쪽": marks["바깥"], "나": marks["안쪽"],
                      "다": marks["바깥"]}


def test_a_window_without_text_or_ref_is_no_scope():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "window", "name": "바깥", "children": [
            {"role": "window", "children": [{"role": "statictext", "name": "가"}]}]}]}

    assert [(e["name"], e["scope"]) for e in visible_elements(tree)] == [("바깥", None), ("가", 1)]


def test_the_root_window_is_never_a_scope_even_with_a_ref():
    tree = {"role": "window", "name": "Main", "ref_id": "@s1:e1", "children": [
        {"role": "statictext", "name": "가"}]}

    assert [(e["name"], e["mark"], e["scope"]) for e in visible_elements(tree)] == [
        ("Main", 1, None), ("가", 2, None)]


def test_a_root_that_is_not_a_window_is_no_scope():
    tree = {"role": "list", "name": "목록", "children": [
        {"role": "window", "name": "창", "children": [{"role": "statictext", "name": "가"}]}]}

    assert [(e["name"], e["scope"]) for e in visible_elements(tree)] == [
        ("목록", None), ("창", None), ("가", 2)]


def test_visible_elements_carry_mark_and_scope_in_the_same_numbering():
    tree = _list_popup_tree()
    tree["children"].insert(0, {"role": "group", "subtree_truncated": True, "children": [
        {"role": "image"}, {"role": "edit", "value": "15", "ref_id": "@s1:e2"}]})
    text, _, marks = render_marked(tree)
    elements = visible_elements(tree)

    assert [e["mark"] for e in elements] == sorted(marks)
    assert [e["scope"] for e in elements] == [marks[n]["scope"] for n in sorted(marks)]
    numbered = [line.strip().split(" ", 1)[0] for line in text.splitlines() if line.strip().startswith("[")]
    assert numbered == [f"[{e['mark']}]" for e in elements]


def nid(value):
    return {"kind": "automation_id", "value": value}


def profile_of(*elements):
    return Profile(system="sys", elements=tuple(elements))


def entry(native, role, name, area=None):
    written = {"id": native, "role": role, "name": name}
    if area is not None:
        written["in"] = area
    return written


TB1 = {"role": "toolbar", "id": "tb1"}
TB2 = {"role": "toolbar", "id": "tb2"}


def two_toolbars():
    return {"role": "window", "name": "Main", "children": [
        {"role": "toolbar", "native_id": nid("tb1"), "children": [
            {"role": "image", "native_id": nid("56"), "bounds": B},
            {"role": "image", "native_id": nid("60"), "bounds": B}]},
        {"role": "toolbar", "native_id": nid("tb2"), "children": [
            {"role": "image", "native_id": nid("56"), "bounds": B}]}]}


def numbered_lines(text):
    return [line.strip().split(" ", 1)[0] for line in text.splitlines() if line.strip().startswith("[")]


def test_an_id_only_leaf_is_an_element_with_a_number_and_a_line():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "group", "native_id": nid("71"), "bounds": B},
        {"role": "button", "name": "조회", "ref_id": "@s1:e1"}]}

    text, _, marks = render_marked(tree)

    assert text == 'window "Main"\n  [1] group id="71"\n  [2] button "조회" @s1:e1'
    assert marks[1]["kind"] == "id" and marks[1]["ref"] is None and marks[1]["bounds"] == B
    assert marks[1]["native_id"] == nid("71") and "in" not in marks[1] and "alias" not in marks[1]
    assert [e["kind"] for e in visible_elements(tree)] == ["id", "control"]


def test_an_id_only_node_with_children_is_no_element_unless_registered():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "group", "native_id": nid("g1"), "children": [{"role": "statictext", "name": "가"}]},
        {"role": "group", "native_id": nid("g2"), "children_count": 3}]}

    text, _, marks = render_marked(tree)

    assert text == 'window "Main"\n  [1] statictext "가"'
    assert [marks[n]["name"] for n in marks] == ["가"]
    assert visible_elements(tree) == [marks[1]]


def test_a_registered_node_with_children_is_an_element_with_its_alias():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "button", "native_id": nid("60"), "children": [
            {"role": "image"}, {"role": "statictext", "name": "가"}]}]}
    profile = profile_of(entry("60", "button", "Execution Plan"))

    text, _, marks = render_marked(tree, profile=profile)

    assert text == 'window "Main"\n  [1] button id="60" alias="Execution Plan"\n    [2] statictext "가"'
    assert marks[1]["kind"] == "id" and marks[1]["alias"] == "Execution Plan"
    assert [e["name"] for e in visible_elements(tree)] == ["가"]


def test_a_window_role_node_with_only_an_id_opens_no_scope():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "window", "native_id": nid("w9"), "children": [{"role": "statictext", "name": "가"}]},
        {"role": "window", "native_id": nid("w8")}]}
    profile = profile_of(entry("w8", "window", "창"))

    elements = visible_elements(tree, profile=profile)

    assert [(e["name"], e["mark"], e["scope"]) for e in elements] == [("가", 1, None)]


def test_a_named_element_shows_no_id():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "button", "name": "Save", "ref_id": "@s1:e1", "native_id": nid("7001")}]}

    text, _, marks = render_marked(tree)

    assert text == 'window "Main"\n  [1] button "Save" @s1:e1'
    assert marks[1]["native_id"] == nid("7001")


def test_an_unnamed_ref_shows_its_id_after_the_ref():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "button", "ref_id": "@s1:e4", "native_id": nid("7001"), "available_actions": ["Click"]}]}

    assert render_marked(tree)[0] == 'window "Main"\n  [1] button @s1:e4 id="7001" actions=Click'
    assert render_tree(tree)[0] == 'window "Main"\n  button @s1:e4 id="7001" actions=Click'


def test_alias_needs_the_written_container():
    profile = profile_of(entry("56", "image", "Shipment Plan", TB1))

    text, _, marks = render_marked(two_toolbars(), profile=profile)

    assert [marks[n].get("alias") for n in sorted(marks)] == ["Shipment Plan", None, None]
    assert [marks[n]["in"] for n in sorted(marks)] == [TB1, TB1, TB2]
    assert text.count('alias="Shipment Plan"') == 1


def test_two_elements_fitting_one_entry_get_no_alias():
    profile = profile_of(entry("56", "image", "Shipment Plan"))

    text, _, marks = render_marked(two_toolbars(), profile=profile)

    assert all("alias" not in marks[n] for n in marks)
    assert "alias=" not in text


def test_alias_is_counted_per_entry_not_per_name():
    profile = profile_of(entry("56", "image", "Plan", TB1), entry("56", "image", "Plan", TB2))

    marks = render_marked(two_toolbars(), profile=profile)[2]

    assert [marks[n].get("alias") for n in sorted(marks)] == ["Plan", None, "Plan"]


def test_an_alias_with_quotes_is_json_quoted_in_the_line():
    tree = {"role": "window", "name": "Main", "children": [{"role": "image", "native_id": nid("60")}]}
    alias = 'Plan "A" \\ B'
    profile = profile_of(entry("60", "image", alias))

    text = render_marked(tree, profile=profile)[0]

    assert text.splitlines()[1] == "  [1] image id=\"60\" alias=" + json.dumps(alias, ensure_ascii=False)


def test_elements_with_an_id_carry_native_id_and_in():
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "group", "name": "필터", "children": [
            {"role": "button", "ref_id": "@s1:e1", "native_id": nid("7001")},
            {"role": "statictext", "name": "전일"}]},
        {"role": "image", "native_id": nid("9")}]}

    group, button, label, image = visible_elements(tree)

    assert button["native_id"] == nid("7001") and button["in"] == {"role": "group", "name": "필터"}
    assert not {"native_id", "in", "alias"} & (set(group) | set(label))
    assert image["native_id"] == nid("9") and "in" not in image


def test_a_part_read_with_root_finds_areas_inside_the_part_only():
    part = {"role": "toolbar", "native_id": nid("tb1"), "children": [{"role": "image", "native_id": nid("56")}]}
    profile = profile_of(entry("56", "image", "Shipment Plan", TB1))

    elements = visible_elements(part, profile=profile)

    assert [(e["kind"], e["role"]) for e in elements] == [("id", "image")]
    assert elements[0]["in"] == TB1 and elements[0]["alias"] == "Shipment Plan"


def test_marks_and_visible_elements_agree_with_a_profile():
    tree = two_toolbars()
    tree["children"].append({"role": "group", "native_id": nid("71"), "children": [
        {"role": "button", "ref_id": "@s1:e1", "native_id": nid("72")}]})
    profile = profile_of(entry("60", "image", "Execution Plan"), entry("71", "group", "묶음"))

    text, _, marks = render_marked(tree, profile=profile)
    elements = visible_elements(tree, profile=profile)

    assert [marks[n] for n in sorted(marks)] == elements
    assert numbered_lines(text) == [f"[{e['mark']}]" for e in elements]
    assert [e.get("alias") for e in elements] == [None, "Execution Plan", None, "묶음", None]
    assert elements[4]["in"] == {"role": "group", "id": "71"}


def test_without_a_profile_only_the_leaf_rule_and_the_id_text_differ():
    tree = _list_popup_tree()
    tree["children"].insert(0, {"role": "group", "subtree_truncated": True, "children": [
        {"role": "image"}, {"role": "edit", "value": "15", "ref_id": "@s1:e2"}]})

    text, refs, marks = render_marked(tree)

    assert text == (
        'window "Main"\n'
        "  group truncated\n"
        '    [1] edit @s1:e2 value="15"\n'
        '  [2] window "목록"\n'
        '    [3] statictext "All"\n'
        '    [4] statictext "WA0"\n'
        '  [5] button "조회" @s1:e1'
    )
    assert render_marked(tree, profile=Profile(system="sys")) == (text, refs, marks)
    assert all(not {"native_id", "in", "alias"} & set(marks[n]) for n in marks)
