import copy

from structure_support import company, load, set_values, table_node, toolbox_for

from agent.observe import render_marked
from agent.structure import analyze, render_summary


def bounds(x, y, width=60.0, height=20.0):
    return {"x": float(x), "y": float(y), "width": float(width), "height": float(height)}


def screen_with_filter_and_table():
    tree = company()
    table_node(tree)["name"] = "생산집계"
    combos = [{"role": "combobox", "ref_id": f"@s:f{i}", "bounds": bounds(x, 12)} for i, x in ((1, 90), (2, 160), (3, 230))]
    tree["children"].insert(0, {"role": "pane", "native_id": "pn_Filter", "children": [
        {"role": "statictext", "name": "Shop", "bounds": bounds(10, 10, 60, 24)}, *combos]})
    return tree


def test_summary_comes_first_and_matches_the_spec_shape():
    tree = screen_with_filter_and_table()
    structure = analyze(tree)
    text, _, marks = render_marked(tree, structure=structure)
    by_name = {element["name"]: mark for mark, element in marks.items() if element["name"]}
    combos = sorted(mark for mark, element in marks.items() if element["role"] == "combobox")
    expected = "\n".join([
        "구조",
        '  A1 영역 id="pn_Filter"',
        f'    줄1: [{by_name["Shop"]}] "Shop" → [{combos[0]}] · [{combos[1]}] · [{combos[2]}]',
        '  A2 영역 id="pn_Main"',
        f"    T1 표 [{by_name['생산집계']}]  열 3개 · 행 3개 · 값 있음 · 행을 가르는 열: 없음",
        "      열: 공정 row# | 구분 row# | 양품수 row#",
    ])
    assert text.startswith(expected + "\n")
    assert render_summary(structure) == expected


def test_key_candidates_and_many_columns_are_shown_short():
    tree = set_values(company(), [["극판", "A", "1"], ["극판", "B", "2"], ["조립", "C", "3"]])
    summary = render_summary(analyze(tree))
    assert "행을 가르는 열: 구분 row#\n" in summary
    for row in table_node(tree)["children"][0]["children"]:
        for k in range(9):
            row["children"].append({"role": "cell", "name": f"추가{'ABCDEFGHI'[k]} row0", "ref_id": f"@s:x{k}",
                                    "bounds": bounds(500 + 40 * k, row["bounds"]["y"], 38, 22)})
    summary = render_summary(analyze(tree))
    assert "열 12개" in summary
    assert summary.splitlines()[-1].endswith("| 추가E row# | …") and summary.count(" | ") == 8


def test_a_table_outside_every_area_is_listed_on_its_own():
    tree = {"role": "window", "children": [{"role": "grid", "children": [
        {"role": "row", "children": [{"role": "cell", "name": "a", "ref_id": "@s:e1", "bounds": bounds(0, 0)}]},
        {"role": "row", "children": [{"role": "cell", "name": "b", "ref_id": "@s:e2", "bounds": bounds(0, 30)}]}]}]}
    assert render_summary(analyze(tree)) == "\n".join([
        "구조", "  T1 표  열 1개 · 행 2개 · 값 있음 · 행을 가르는 열: 열1", "    열: 열1"])


def test_a_table_cut_by_the_depth_shows_no_columns_line():
    tree = company()
    node = table_node(tree)
    node["children"], node["children_count"] = [], 1
    assert render_summary(analyze(tree)).splitlines()[-1] == \
        "    T1 표  열 0개 · 행 0개 · 값 없음 · 행을 가르는 열: 없음"


def test_cells_of_a_table_have_no_lines_but_keep_their_marks():
    tree = company()
    structure = analyze(tree)
    plain, plain_refs, plain_marks = render_marked(tree)
    text, refs, marks = render_marked(tree, structure=structure)
    assert marks == plain_marks and refs == plain_refs
    assert structure.cell_marks <= set(marks)
    assert "row0" in plain and "row0" not in text and "980" not in text
    for mark in structure.cell_marks:
        assert f"[{mark}] " not in text
    assert '"Data Panel"' in text and '"Row 1"' in text


def test_observation_text_of_a_tableless_window_is_unchanged_but_for_areas():
    menu = {"role": "window", "name": "연습 앱", "children": [{"role": "menu", "name": "파일", "children": [
        {"role": "menuitem", "name": name, "ref_id": f"@s:e{i}", "bounds": bounds(0, 20 * i, 120, 20)}
        for i, name in enumerate(["새로 만들기", "열기"], 1)]}]}
    dialog = {"role": "window", "name": "확인", "children": [{"role": "pane", "children": [
        {"role": "statictext", "name": "저장할까요?", "bounds": bounds(10, 10, 200, 20)},
        {"role": "button", "name": "예", "ref_id": "@s:e1", "bounds": bounds(10, 40)}]}]}
    for tree in (menu, dialog, load("snapshot_settings_open.json"), load("snapshot_virtual_mes_mes_like.json")):
        before = copy.deepcopy(tree)
        plain, _, plain_marks = render_marked(tree)
        structure = analyze(tree)
        text, _, marks = render_marked(tree, structure=structure)
        summary = render_summary(structure)
        assert text == (f"{summary}\n{plain}" if summary else plain) and marks == plain_marks
        assert "표" not in summary and "" not in summary.splitlines()
        assert tree == before
    assert render_summary(analyze(dialog)) == ""
    assert render_summary(analyze(menu)).splitlines() == [
        "구조", '  A1 영역 "파일"', '    줄1: [2] "새로 만들기"', '    줄2: [3] "열기"']


def test_an_area_without_a_line_or_a_table_is_left_out():
    tree = {"role": "window", "children": [{"role": "pane", "native_id": "outer", "children": [
        {"role": "pane", "native_id": "a", "children": [{"role": "button", "name": "x", "ref_id": "@s:e1",
                                                          "bounds": bounds(0, 0)}]},
        {"role": "pane", "native_id": "b", "children": [{"role": "button", "name": "y", "ref_id": "@s:e2"}]}]}]}
    assert render_summary(analyze(tree)).splitlines() == ["구조", '  A2 영역 id="a"', '    줄1: [1] "x"']


def test_wait_for_text_finds_a_value_only_a_hidden_cell_holds(tmp_path):
    toolbox, _ = toolbox_for(company(), tmp_path)
    assert "980" not in toolbox.call("observe", {"window_id": None, "root_ref": None, "surface": None,
                                                 "include_bounds": False})["observation"]
    assert toolbox.call("wait_for_text", {"text": "980", "timeout_ms": 1000})["found"] is True
    assert toolbox.call("wait_for_text", {"text": "1,2", "timeout_ms": 1000})["found"] is True
    assert toolbox.call("wait_for_text", {"text": "없는값", "timeout_ms": 1000})["found"] is False


def test_the_toolbox_observation_carries_the_summary_and_hides_cells(tmp_path):
    toolbox, _ = toolbox_for(company(), tmp_path)
    observation = toolbox.call("observe", {"window_id": None, "root_ref": None, "surface": None,
                                           "include_bounds": False})["observation"]
    assert observation.startswith("구조\n  A1 영역 id=\"pn_Main\"\n    T1 표")
    assert "양품수 row1" not in observation
    assert len(toolbox.marked_elements()) == 1 + 3 + 9


def test_only_a_named_label_gets_the_arrow():
    tree = {"role": "window", "children": [{"role": "toolbar", "native_id": "tb", "children": [
        {"role": "image", "native_id": "56", "bounds": bounds(0, 0)},
        {"role": "button", "name": "열기", "ref_id": "@s:e1", "bounds": bounds(70, 0)},
        {"role": "button", "name": "닫기", "ref_id": "@s:e2", "bounds": bounds(0, 40)},
        {"role": "button", "name": "저장", "ref_id": "@s:e3", "bounds": bounds(70, 40)}]}]}
    assert render_summary(analyze(tree)).splitlines()[2:] == ['    줄1: [1] · [2] "열기"', '    줄2: [3] "닫기" · [4] "저장"']
