import copy
import json

import pytest
from structure_support import company, data_rows, load, set_values, table_node

from agent.observe import numbered_nodes, render_marked, visible_elements
from agent.structure import Area, Cell, Column, Structure, analyze, record, table_identity

def bounds(x, y, width=40.0, height=20.0):
    return {"x": float(x), "y": float(y), "width": float(width), "height": float(height)}


def labels(table):
    return [column.label for column in table.columns]


def values(table):
    return [[cell.value if cell else "<none>" for cell in row.cells] for row in table.rows]


def test_numbering_is_the_same_as_render_marked():
    for tree in (company(), load("snapshot_settings_open.json"), load("snapshot_virtual_mes_easy.json")):
        _, _, marks = render_marked(tree)
        numbered = {mark: node for node, mark, _ in numbered_nodes(tree) if mark is not None}
        assert sorted(numbered) == sorted(marks) == [e["mark"] for e in visible_elements(tree)]
        for mark, node in numbered.items():
            assert node.get("name") == marks[mark]["name"] and node.get("role") == marks[mark]["role"]


def test_numbered_nodes_give_the_area_each_node_sits_in():
    tree = company()
    by_name = {node.get("name"): container for node, _, container in numbered_nodes(tree)}
    assert by_name["Data Panel"] == {"role": "table", "id": "gc_Count"}
    assert by_name["Row 1"] == {"role": "pane", "name": "Data Panel"}


def test_company_shaped_table_has_rows_columns_and_values():
    tree = company()
    structure = analyze(tree)
    assert len(structure.tables) == 1
    table = structure.tables[0]
    assert table.handle == "T1" and table.role == "table" and table.native_id == "gc_Count" and table.mark is None
    assert table.columns == (Column("공정 row#", "pattern"), Column("구분 row#", "pattern"), Column("양품수 row#", "pattern"))
    assert [row.index for row in table.rows] == [0, 1, 2]
    assert table.rows[1].cells[2].value == "980"
    assert values(table) == [["극판", "A", "1,200"], [None, "소계", "980"], ["조립", "소계", "640"]]
    cell = table.rows[0].cells[0]
    assert isinstance(cell, Cell) and not cell.offscreen
    _, _, marks = render_marked(tree)
    assert marks[cell.mark]["name"] == "공정 row0"
    assert structure.cell_marks == frozenset(c.mark for row in table.rows for c in row.cells)
    assert len(structure.cell_marks) == 9
    assert table.area == {"role": "pane", "id": "pn_Main"}


def test_a_table_without_a_table_role_is_the_innermost_node_holding_rows_of_cells():
    tree = company()
    table_node(tree)["role"] = "pane"
    table = analyze(tree).tables[0]
    assert table.role == "pane" and len(table.rows) == 3
    _, _, marks = render_marked(tree)
    assert marks[table.mark]["name"] == "Data Panel"


@pytest.mark.parametrize("row_role", ["unknown", "row"])
def test_leaf_row_elements_inside_a_table_role_are_cells(row_role):
    tree = company()
    for row in data_rows(tree):
        row["role"] = row_role
        for cell in row["children"]:
            cell["role"] = "row"
            cell.pop("ref_id")
            cell.pop("available_actions")
    structure = analyze(tree)
    expected = analyze(company())
    assert len(structure.tables) == 1
    table, before = structure.tables[0], expected.tables[0]
    assert table.columns == before.columns and values(table) == values(before)
    assert len(structure.cell_marks) == 9
    _, _, marks = render_marked(tree)
    assert {marks[mark]["role"] for mark in structure.cell_marks} == {"row"}
    assert all(marks[mark]["name"].startswith(("공정", "구분", "양품수")) for mark in structure.cell_marks)


def test_an_empty_leaf_row_inside_a_table_is_no_cell():
    tree = company()
    table_node(tree)["children"][0]["children"].append({"role": "row", "bounds": bounds(10, 290, 446, 22)})
    structure = analyze(tree)
    assert len(structure.tables[0].rows) == 3 and None not in structure.cell_marks
    assert len(structure.cell_marks) == 9


def test_leaf_rows_outside_a_table_role_are_no_table():
    items = [{"role": "row", "name": f"항목 {i}", "bounds": bounds(0, 20 * i, 120, 20)} for i in range(4)]
    for tree in ({"role": "window", "children": [{"role": "list", "children": items}]},
                 {"role": "window", "children": [{"role": "pane", "children": [
                     {"role": "group", "children": items[:2]}, {"role": "group", "children": items[2:]}]}]}):
        structure = analyze(tree)
        assert structure.tables == () and structure.cell_marks == frozenset()


def test_one_row_of_cells_is_no_table():
    tree = company()
    node = table_node(tree)
    node["role"] = "pane"
    panel = node["children"][0]
    panel["children"] = panel["children"][:1]
    assert analyze(tree).tables == ()


def test_header_labels_win_per_column():
    tree = company()
    node = table_node(tree)
    node["children"].insert(0, {"role": "column", "name": "공정", "bounds": bounds(10, 180, 146, 18)})
    node["children"].insert(1, {"role": "column", "name": "양품수", "bounds": bounds(310, 180, 146, 18)})
    table = analyze(tree).tables[0]
    assert table.columns == (Column("공정", "header"), Column("구분 row#", "pattern"), Column("양품수", "header"))
    assert values(table)[1] == [None, "소계", "980"]


def test_the_header_nearest_the_cells_wins_over_a_band_above_it():
    tree = company()
    node = table_node(tree)
    node["children"].insert(0, {"role": "column", "name": "생산", "bounds": bounds(10, 150, 446, 18)})
    node["children"].insert(1, {"role": "column", "name": "공정", "bounds": bounds(10, 175, 146, 18)})
    assert labels(analyze(tree).tables[0]) == ["공정", "생산", "생산 (2)"]


def test_a_table_with_no_cell_on_screen_or_no_cell_at_all_still_reads():
    tree = company()
    for row in data_rows(tree):
        for cell in row["children"]:
            cell.pop("bounds")
    table = analyze(tree).tables[0]
    assert labels(table) == ["공정 row#", "구분 row#", "양품수 row#"] and len(table.rows) == 3
    node = table_node(tree)
    node["children"] = []
    node["children_count"] = 200
    table = analyze(tree).tables[0]
    assert table.columns == () and table.rows == () and table.key_candidates == ()


def test_a_header_below_the_cells_is_no_header():
    tree = company()
    table_node(tree)["children"].append({"role": "column", "name": "합계", "bounds": bounds(10, 520, 146, 18)})
    assert labels(analyze(tree).tables[0])[0] == "공정 row#"


def test_columns_without_a_header_or_a_shared_shape_are_numbered():
    tree = {"role": "window", "children": [{"role": "grid", "children": [
        {"role": "row", "children": [
            {"role": "cell", "name": "극판", "ref_id": "@s:e1", "bounds": bounds(0, 0)},
            {"role": "cell", "name": "206", "ref_id": "@s:e2", "bounds": bounds(50, 0)}]},
        {"role": "row", "children": [
            {"role": "cell", "name": "조립", "ref_id": "@s:e3", "bounds": bounds(0, 30)},
            {"role": "cell", "name": "311", "ref_id": "@s:e4", "bounds": bounds(50, 30)}]}]}]}
    table = analyze(tree).tables[0]
    assert table.columns == (Column("열1", "index"), Column("열2", "index"))
    assert values(table) == [["극판", "206"], ["조립", "311"]]
    assert table.key_candidates == ("열1",)


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_blank_cells_stay_blank(blank):
    tree = set_values(company(), [["극판", "A", "1"], [blank, "B", "2"], [blank, "C", "3"]])
    table = analyze(tree).tables[0]
    assert [row.cells[0].value for row in table.rows] == ["극판", None, None]
    assert all(row.cells[0] is not None for row in table.rows)


def test_numeric_columns_are_never_key_candidates():
    tree = set_values(company(), [["극판", "A", "1,200"], ["극판", "B", "980"], ["극판", "C", "640"]])
    assert analyze(tree).tables[0].key_candidates == ("구분 row#",)
    tree = set_values(company(), [["극판", "A", "1,200"], ["극판", "A", "980"], ["극판", "A", "640"]])
    assert analyze(tree).tables[0].key_candidates == ()


def test_key_candidates_fall_back_to_a_pair():
    tree = set_values(company(), [["극판", "A", "1"], ["극판", "소계", "2"], ["조립", "소계", "3"]])
    assert analyze(tree).tables[0].key_candidates == ("공정 row#+구분 row#",)


def test_columns_with_a_blank_are_no_key_candidates():
    tree = set_values(company(), [["극판", "A", "1"], [None, "B", "2"], ["조립", "C", "3"]])
    assert analyze(tree).tables[0].key_candidates == ("구분 row#",)
    tree = set_values(company(), [["극판", "A", "1"], [None, "소계", "2"], ["조립", "소계", "3"]])
    assert analyze(tree).tables[0].key_candidates == ()


def test_offscreen_cells_count_as_rows():
    tree = company()
    last = data_rows(tree)[2]
    for cell in last["children"]:
        cell.pop("bounds")
    last["children"][1]["states"] = ["offscreen"]
    data_rows(tree)[1]["children"][0]["states"] = ["offscreen"]
    table = analyze(tree).tables[0]
    assert len(table.rows) == 3
    assert [cell.offscreen for cell in table.rows[2].cells] == [True, True, True]
    assert [cell.offscreen for cell in table.rows[1].cells] == [True, False, False]
    assert values(table)[2] == ["조립", "소계", "640"]


def test_a_row_with_fewer_cells_fills_the_columns_it_overlaps():
    tree = company()
    data_rows(tree)[1]["children"].pop(0)
    table = analyze(tree).tables[0]
    assert values(table)[1] == ["<none>", "소계", "980"]
    assert table.key_candidates == ()


def test_cells_given_straight_under_a_table_are_grouped_into_rows_by_their_boxes():
    cells = [{"role": "cell", "name": name, "ref_id": f"@s:e{i}", "bounds": bounds(x, y)}
             for i, (name, x, y) in enumerate([("b2", 50, 31), ("a1", 0, 0), ("b1", 0, 30), ("a2", 50, 1)], 1)]
    tree = {"role": "window", "children": [{"role": "table", "children": cells}]}
    table = analyze(tree).tables[0]
    assert values(table) == [["a1", "a2"], ["b1", "b2"]]


def test_cells_without_a_box_straight_under_a_table_make_one_last_row():
    cells = [{"role": "cell", "name": name, "ref_id": f"@s:e{i}", "bounds": bounds(x, y)}
             for i, (name, x, y) in enumerate([("a1", 0, 0), ("a2", 50, 0), ("b1", 0, 30), ("b2", 50, 30)], 1)]
    cells += [{"role": "cell", "name": name, "ref_id": f"@s:e{i}", "states": ["offscreen"]}
              for i, name in ((5, "c1"), (6, "c2"))]
    tree = {"role": "window", "children": [{"role": "table", "children": cells}]}
    table = analyze(tree).tables[0]
    assert values(table) == [["a1", "a2"], ["b1", "b2"], ["c1", "c2"]]
    assert [cell.offscreen for cell in table.rows[2].cells] == [True, True]


def test_rows_inside_a_table_are_not_areas():
    structure = analyze(company())
    assert [area.container for area in structure.areas] == [{"role": "pane", "id": "pn_Main"}]


def test_wrapper_chain_collapses_to_one_area():
    structure = analyze(company())
    assert len(structure.areas) == 1
    area = structure.areas[0]
    assert isinstance(area, Area) and area.handle == "A1" and area.tables == ("T1",) and area.lines == ()
    assert structure.area("A1") is area and structure.area("A2") is None
    assert structure.table("T1") is structure.tables[0] and structure.table("T2") is None


def filter_tree():
    combos = [{"role": "combobox", "ref_id": f"@s:e{i}", "bounds": bounds(x, y, 60, 20)}
              for i, x, y in ((3, 230, 10), (2, 160, 11), (1, 90, 12))]
    return {"role": "window", "children": [
        {"role": "pane", "children": [
            {"role": "pane", "native_id": "pn_Filter", "children": [
                {"role": "statictext", "name": "Shop", "bounds": bounds(10, 13, 60, 24)}, *combos,
                {"role": "button", "name": "조회", "ref_id": "@s:e4", "bounds": bounds(10, 50, 60, 24)},
                {"role": "button", "name": "숨김", "ref_id": "@s:e5"}]},
            {"role": "pane", "native_id": "pn_Side", "children": [
                {"role": "button", "name": "닫기", "ref_id": "@s:e6", "bounds": bounds(400, 10)}]}]}]}


def test_lines_are_left_to_right():
    tree = filter_tree()
    structure = analyze(tree)
    _, _, marks = render_marked(tree)
    area = structure.areas[0]
    assert area.container == {"role": "pane", "id": "pn_Filter"}
    lines = [[marks[mark]["name"] or marks[mark]["ref"] for mark in line] for line in area.lines]
    assert lines == [["Shop", "@s:e1", "@s:e2", "@s:e3"], ["조회"]]
    assert [a.handle for a in structure.areas] == ["A1", "A2"]


def test_an_element_that_is_an_area_itself_is_no_member_of_the_lines_around_it():
    tree = filter_tree()
    pane = tree["children"][0]["children"][0]
    pane["children"].append({"role": "group", "name": "기간", "bounds": bounds(10, 50, 300, 24), "children": [
        {"role": "button", "name": "전일", "ref_id": "@s:e7", "bounds": bounds(80, 52)}]})
    structure = analyze(tree)
    _, _, marks = render_marked(tree)
    assert [a.container for a in structure.areas][:2] == [{"role": "pane", "id": "pn_Filter"},
                                                          {"role": "group", "name": "기간"}]
    names = [[marks[mark]["name"] for mark in line] for line in structure.areas[0].lines]
    assert names[1] == ["조회"]
    assert [[marks[mark]["name"] for mark in line] for line in structure.areas[1].lines] == [["전일"]]


def test_an_area_holding_two_areas_is_kept_but_one_holding_one_is_not():
    tree = filter_tree()
    tree["children"][0]["native_id"] = "pn_Outer"
    structure = analyze(tree)
    assert [a.container.get("id") for a in structure.areas] == ["pn_Outer", "pn_Filter", "pn_Side"]
    assert structure.areas[0].lines == ()
    tree["children"][0]["children"].pop()
    assert [a.container.get("id") for a in analyze(tree).areas] == ["pn_Filter"]


def test_a_named_control_is_no_area():
    tree = {"role": "window", "children": [{"role": "button", "name": "열기", "ref_id": "@s:e1", "children": [
        {"role": "statictext", "name": "열기", "bounds": bounds(0, 0)}]}]}
    assert analyze(tree).areas == ()


def test_a_screen_without_tables_gets_no_table_lines():
    menu = {"role": "window", "name": "연습 앱", "children": [{"role": "menu", "name": "파일", "children": [
        {"role": "menuitem", "name": name, "ref_id": f"@s:e{i}", "bounds": bounds(0, 20 * i, 120, 20)}
        for i, name in enumerate(["새로 만들기", "열기", "저장"], 1)]}]}
    for tree in (menu, load("snapshot_settings_open.json")):
        structure = analyze(tree)
        assert structure.tables == () and structure.cell_marks == frozenset()
        assert all(area.tables == () for area in structure.areas)
    assert [len(line) for line in analyze(menu).areas[0].lines] == [1, 1, 1]


def test_an_empty_tree_has_no_structure():
    assert analyze({"role": "window"}) == Structure((), (), frozenset())


def test_table_identity_without_an_id_uses_area_role_and_columns():
    tree = company()
    del table_node(tree)["native_id"]
    table = analyze(tree).tables[0]
    expected = {"role": "table", "in": {"role": "pane", "id": "pn_Main"},
                "columns": ["공정 row#", "구분 row#", "양품수 row#"]}
    assert table_identity(table, use_ids=True) == expected
    assert table_identity(table, use_ids=False) == expected


def test_table_identity_with_an_id_uses_it_only_when_ids_are_stable():
    table = analyze(company()).tables[0]
    assert table_identity(table, use_ids=True) == {"role": "table", "id": "gc_Count",
                                                   "in": {"role": "pane", "id": "pn_Main"}}
    assert "columns" in table_identity(table, use_ids=False)


def test_table_identity_without_an_area_leaves_in_out():
    tree = {"role": "window", "children": [{"role": "grid", "children": [
        {"role": "row", "children": [{"role": "cell", "name": "a", "ref_id": "@s:e1", "bounds": bounds(0, 0)}]},
        {"role": "row", "children": [{"role": "cell", "name": "b", "ref_id": "@s:e2", "bounds": bounds(0, 30)}]}]}]}
    assert table_identity(analyze(tree).tables[0], use_ids=False) == {"role": "grid", "columns": ["열1"]}


def test_record_carries_handles_identities_and_counts_but_no_cell_values():
    tree = set_values(company(), [["극판", "A", "1"], ["극판", "B", "2"], ["조립", "C", "3"]])
    structure = analyze(tree)
    recorded = record(structure)
    assert recorded == {
        "areas": [{"handle": "A1", "area": {"role": "pane", "id": "pn_Main"}}],
        "tables": [{"handle": "T1", "identity": table_identity(structure.tables[0], use_ids=False), "id": "gc_Count",
                    "columns": ["공정 row#", "구분 row#", "양품수 row#"], "rows": 3,
                    "key_candidates": ["구분 row#"]}],
    }
    text = json.dumps(recorded, ensure_ascii=False)
    assert "980" not in text and "극판" not in text
    json.dumps(recorded)


def test_analyze_changes_nothing_in_the_tree():
    tree = company()
    before = copy.deepcopy(tree)
    analyze(tree)
    assert tree == before
