import pytest

from agent.geometry import contains
from agent.table import find_cells

W, H = 80, 24

TOP = [("SHOP", 0, 1), ("공정", 1, 2), ("계획", 3, 3), ("생산", 6, 4), ("불량", 10, 6)]
LOWER = ["그룹공정", "공정", "계획", "실적", "달성률", "작업수", "양품수", "양품율", "수율(Loss제외)",
         "불량수", "불량율(PPM)", "LOSS수", "LOSS율(PPM)", "이탈수", "이탈율(PPM)"]
ROWS = [("믹싱", "바인더", "소계"), ("믹싱", "슬러리 믹싱", "소계"), ("극판", "소계", ""),
        ("극판", "코팅", "소계"), ("극판", "압연", "소계")]
GOOD_COLUMN = 7


def _box(column, row, columns=1, rows=1):
    return {"x": column * W, "y": row * H, "width": columns * W, "height": rows * H}


def _label(name, bounds):
    return {"kind": "text", "ref": None, "role": "statictext", "name": name, "description": None,
            "states": [], "value": None, "bounds": bounds}


def _cell(value, bounds):
    return {"kind": "control", "ref": "@s1:e0", "role": "cell", "name": None, "description": None,
            "states": [], "value": value, "bounds": bounds}


def _table(merge_shop=False):
    elements = []
    for name, column, span in TOP:
        elements.append(_label(name, _box(column, 0, span, 2 if name == "SHOP" else 1)))
    for offset, name in enumerate(LOWER, 1):
        elements.append(_label(name, _box(offset, 1)))
    for index, labels in enumerate(ROWS):
        row = index + 2
        for column, name in enumerate(labels):
            if not name or (merge_shop and column == 0 and name == "극판"):
                continue
            elements.append(_label(name, _box(column, row)))
        for column in range(3, 3 + 13):
            value = "12,480" if labels[:2] == ("극판", "소계") and column == GOOD_COLUMN else "0"
            elements.append(_cell(value, _box(column, row)))
    if merge_shop:
        elements.append(_label("극판", _box(0, 4, 1, 3)))
    return [dict(element, mark=n) for n, element in enumerate(elements, 1)]


TABLE = _table()
MERGED_TABLE = _table(merge_shop=True)


def center_x_of_lower(name):
    return (LOWER.index(name) + 1) * W + W // 2


def test_unique_row_and_grouped_column_give_one_cell():
    found = find_cells(TABLE, ["믹싱", "바인더"], ["생산", "양품수"])
    assert found["reason"] is None and len(found["candidates"]) == 1
    cell = found["candidates"][0]
    assert cell["value"] == "0" and cell["mark"] is not None
    assert contains(cell["bounds"], *cell["point"])
    assert cell["point"] == [center_x_of_lower("양품수"), 2 * H + H // 2]


def test_candidate_names_the_label_marks():
    cell = find_cells(TABLE, ["믹싱", "바인더"], ["생산", "양품수"])["candidates"][0]
    names = {element["mark"]: element["name"] for element in TABLE}
    assert [names[mark] for mark in cell["row_marks"]] == ["믹싱", "바인더"]
    assert [names[mark] for mark in cell["column_marks"]] == ["생산", "양품수"]


def test_shop_and_subtotal_match_every_row_that_has_both():
    found = find_cells(TABLE, ["극판", "소계"], ["양품수"])
    assert [c["row_context"][:3] for c in found["candidates"]] == [
        ["극판", "소계"], ["극판", "코팅", "소계"], ["극판", "압연", "소계"]]
    assert found["candidates"][0]["value"] == "12,480"


def test_label_on_both_header_levels_is_one_column():
    found = find_cells(TABLE, ["믹싱", "바인더"], ["계획"])
    assert len(found["candidates"]) == 1
    assert found["candidates"][0]["point"][0] == center_x_of_lower("계획")


def test_merged_shop_cell_spanning_rows_matches_each_row():
    found = find_cells(MERGED_TABLE, ["극판"], ["양품수"])
    assert len(found["candidates"]) == 3
    assert [c["row_context"][:2] for c in found["candidates"]] == [
        ["극판", "소계"], ["극판", "코팅"], ["극판", "압연"]]
    assert found["candidates"][0]["value"] == "12,480"


def test_merged_shop_with_subtotal_still_finds_three_rows():
    assert len(find_cells(MERGED_TABLE, ["극판", "소계"], ["양품수"])["candidates"]) == 3


def test_names_match_after_trimming_and_collapsing_spaces():
    found = find_cells(TABLE, ["  믹싱 ", "슬러리   믹싱"], ["양품수"])
    assert len(found["candidates"]) == 1


def test_a_table_drawn_as_a_picture_still_gives_the_point_without_a_mark():
    labels_only = [element for element in TABLE if element["kind"] == "text"]
    cell = find_cells(labels_only, ["믹싱", "바인더"], ["양품수"])["candidates"][0]
    assert cell["mark"] is None and cell["value"] is None and cell["bounds"] is None
    assert cell["point"] == [center_x_of_lower("양품수"), 2 * H + H // 2]


def test_column_label_below_the_row_is_not_a_header():
    found = find_cells(TABLE, ["믹싱", "바인더"], ["소계"])
    assert found == {"candidates": [], "reason": "labels_not_aligned"}


@pytest.mark.parametrize("rows,columns,reason", [
    (["없는행"], ["양품수"], "row_label_not_found: 없는행"),
    (["극판"], ["없는열"], "column_label_not_found: 없는열"),
    (["믹싱", "코팅"], ["양품수"], "labels_not_aligned"),
    (["믹싱"], ["생산", "불량수"], "labels_not_aligned")])
def test_no_candidate_says_why(rows, columns, reason):
    assert find_cells(TABLE, rows, columns) == {"candidates": [], "reason": reason}


def test_table_without_text_elements():
    assert find_cells([], ["극판"], ["양품수"])["reason"] == "no_text_elements"
    nameless = [dict(TABLE[0], name=None, value=None)]
    assert find_cells(nameless, ["극판"], ["양품수"])["reason"] == "no_text_elements"


def test_candidates_are_capped_at_twenty():
    many_rows = []
    for row in range(30):
        many_rows.append(_label("합계", _box(0, row + 1)))
        many_rows.append(_cell("1", _box(1, row + 1)))
    many_rows.append(_label("값", _box(1, 0)))
    found = find_cells([dict(e, mark=n) for n, e in enumerate(many_rows, 1)], ["합계"], ["값"])
    assert len(found["candidates"]) == 20
    assert [c["point"][1] for c in found["candidates"]] == sorted(c["point"][1] for c in found["candidates"])


@pytest.mark.parametrize("rows,columns", [([], ["양품수"]), (["극판"], [])])
def test_empty_name_lists_are_rejected(rows, columns):
    with pytest.raises(ValueError):
        find_cells(TABLE, rows, columns)
