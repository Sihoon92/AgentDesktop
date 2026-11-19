"""Telling one element apart without coordinates (agent/locator.py)."""

import pytest

from agent.locator import (
    LocatorError,
    box,
    describe,
    find,
    in_scope,
    new_windows,
    overlaps,
    resolve_handle,
    window_elements,
    window_handle,
)


def el(mark, role, name=None, box=(0, 0, 10, 10), scope=None, description=None, key="rect"):
    element = {"mark": mark, "scope": scope, "kind": "text", "ref": None, "role": role, "name": name,
               "description": description, "states": [], "value": None}
    element[key] = None if box is None else {"x": box[0], "y": box[1], "width": box[2], "height": box[3]}
    return element


def form():
    """A filter form with a popup list, the shape of one recorded observation."""
    return [
        el(1, "statictext", "기간", (10, 100, 60, 30)),
        el(2, "button", "조회(F5)", (500, 100, 80, 30)),
        el(3, "statictext", "Shop", (10, 200, 60, 30)),
        el(4, "button", None, (100, 200, 30, 30)),
        el(5, "window", "목록", (100, 240, 200, 200)),
        el(6, "statictext", "기간", (110, 250, 60, 20), scope=5),
        el(7, "statictext", "All", (110, 280, 60, 20), scope=5),
        el(8, "statictext", "WA0", (110, 310, 60, 20), scope=5),
        el(9, "statictext", "WA1", (110, 340, 60, 20), scope=5),
        el(10, "button", None, (150, 200, 30, 30)),
        el(11, "button", None, (200, 200, 30, 30)),
        el(12, "statictext", "공정", (10, 450, 60, 30)),
        el(13, "button", None, (100, 450, 30, 30)),
        el(14, "edit", None, (300, 450, 120, 30), description="저장 경로"),
    ]


def by_mark(elements, mark):
    return next(element for element in elements if element["mark"] == mark)


def marks(found):
    return [element["mark"] for element in found]


def test_in_scope_splits_main_and_popup():
    elements = form()
    main = in_scope(elements, None)
    popup = in_scope(elements, 5)
    assert 1 in marks(main) and 6 not in marks(main)
    assert marks(popup) == [6, 7, 8, 9]
    assert 5 not in marks(main) and 5 not in marks(popup)


def test_window_elements_are_the_window_roles():
    assert marks(window_elements(form())) == [5]


def test_a_unique_role_and_name_needs_no_anchor():
    main = in_scope(form(), None)
    assert describe(main, 2) == {"role": "button", "name": "조회(F5)"}


def test_a_description_is_kept_and_an_empty_name_is_left_out():
    main = in_scope(form(), None)
    assert describe(main, 14) == {"role": "edit", "description": "저장 경로"}


def test_the_same_name_in_another_scope_does_not_count():
    elements = form()
    assert describe(in_scope(elements, None), 1) == {"role": "statictext", "name": "기간"}
    assert describe(in_scope(elements, 5), 6) == {"role": "statictext", "name": "기간"}


def test_unnamed_buttons_are_told_apart_by_a_named_neighbour():
    main = in_scope(form(), None)
    locator = describe(main, 11)
    assert locator == {"role": "button", "near": {"name": "Shop", "relation": "same_row", "nth": 3}}
    assert marks(find(main, locator)) == [11]
    assert describe(main, 13)["near"] == {"name": "공정", "relation": "same_row", "nth": 1}


def headed_table(with_headers=True):
    cells = [
        el(4, "statictext", "전체", (100, 100, 60, 20)),
        el(5, "statictext", "전체", (200, 100, 60, 20)),
        el(6, "statictext", "전체", (300, 100, 60, 20)),
    ]
    headers = [
        el(1, "statictext", "Line", (100, 50, 60, 20)),
        el(2, "statictext", "Shop", (200, 50, 60, 20)),
        el(3, "statictext", "Model", (300, 50, 60, 20)),
    ]
    return headers + cells if with_headers else cells


def test_same_named_texts_get_an_anchor_or_fail():
    scoped = headed_table()
    locator = describe(scoped, 5)
    assert locator == {"role": "statictext", "name": "전체",
                       "near": {"name": "Shop", "relation": "same_column", "nth": 1}}
    assert marks(find(scoped, locator)) == [5]
    with pytest.raises(LocatorError):
        describe(headed_table(with_headers=False), 5)


def test_the_anchor_must_be_unique_in_scope():
    scoped = [
        el(1, "statictext", "Shop", (10, 200, 60, 30)),
        el(2, "button", None, (100, 200, 30, 30)),
        el(3, "statictext", "Shop", (10, 300, 60, 30)),
        el(4, "button", None, (100, 300, 30, 30)),
    ]
    with pytest.raises(LocatorError):
        describe(scoped, 2)
    scoped[2]["name"] = "Line"
    assert describe(scoped, 2)["near"] == {"name": "Shop", "relation": "same_row", "nth": 1}


def test_a_repeated_name_is_never_the_anchor_even_when_it_would_work():
    scoped = [
        el(1, "statictext", "Line", (0, 200, 40, 30)),
        el(2, "statictext", "Shop", (50, 200, 60, 30)),
        el(3, "button", None, (150, 200, 30, 30)),
        el(4, "statictext", "Shop", (50, 300, 60, 30)),
        el(5, "statictext", "공정", (0, 400, 40, 30)),
        el(6, "button", None, (150, 400, 30, 30)),
    ]
    assert describe(scoped, 3)["near"] == {"name": "Line", "relation": "same_row", "nth": 1}


def labelled_values():
    """Rows of [label][current value][unnamed button]: the value sits nearer the button than the label."""
    return [
        el(1, "statictext", "기간", (10, 100, 60, 30)),
        el(2, "statictext", "전일", (80, 100, 80, 30)),
        el(3, "button", None, (170, 100, 30, 30)),
        el(4, "statictext", "공정", (10, 150, 60, 30)),
        el(5, "statictext", "전체", (80, 150, 80, 30)),
        el(6, "button", None, (170, 150, 30, 30)),
    ]


def test_an_avoided_name_is_never_the_anchor():
    scoped = labelled_values()
    assert describe(scoped, 3)["near"]["name"] == "전일"
    locator = describe(scoped, 3, avoid=("전일",))
    assert locator["near"] == {"name": "기간", "relation": "same_row", "nth": 1}
    assert marks(find(scoped, locator)) == [3]
    assert describe(scoped, 6, avoid=[" 전체 ", "전일"])["near"]["name"] == "공정"


def test_avoiding_every_anchor_fails():
    with pytest.raises(LocatorError):
        describe(labelled_values(), 3, avoid={"전일", "기간", "공정", "전체"})


def test_avoid_does_not_change_a_target_that_needs_no_anchor():
    assert describe(labelled_values(), 2, avoid=("전일",)) == {"role": "statictext", "name": "전일"}


def test_a_repeated_label_stays_unusable_when_the_value_is_avoided():
    scoped = labelled_values() + [el(7, "statictext", "기간", (10, 300, 60, 30))]
    with pytest.raises(LocatorError):
        describe(scoped, 3, avoid=("전일",))


def test_a_tied_row_falls_back_to_the_column():
    scoped = [
        el(1, "statictext", "Shop", (10, 200, 60, 60)),
        el(2, "button", None, (100, 200, 30, 25)),
        el(3, "button", None, (100, 235, 30, 25)),
        el(4, "statictext", "Top", (100, 150, 30, 20)),
    ]
    assert describe(scoped, 3)["near"] == {"name": "Top", "relation": "same_column", "nth": 2}


def test_the_description_tells_elements_apart():
    scoped = [el(1, "edit", None, (10, 10, 50, 20), description="시작"),
              el(2, "edit", None, (10, 40, 50, 20), description="끝")]
    assert describe(scoped, 2) == {"role": "edit", "description": "끝"}
    assert marks(find(scoped, {"role": "edit", "description": "시작"})) == [1]


def test_the_nearest_anchor_on_the_left_is_used():
    scoped = [
        el(1, "statictext", "Shop", (10, 200, 60, 30)),
        el(2, "button", None, (100, 200, 30, 30)),
        el(3, "statictext", "Line", (150, 200, 60, 30)),
        el(4, "button", None, (230, 200, 30, 30)),
    ]
    assert describe(scoped, 4)["near"] == {"name": "Line", "relation": "same_row", "nth": 1}
    assert describe(scoped, 2)["near"] == {"name": "Shop", "relation": "same_row", "nth": 1}


def test_a_row_is_at_least_half_of_the_shorter_box_overlapping():
    scoped = [
        el(1, "statictext", "Shop", (10, 200, 60, 30)),
        el(2, "button", None, (100, 208, 30, 30)),
        el(3, "button", None, (150, 223, 30, 30)),
        el(4, "button", None, (200, 214, 30, 30)),
    ]
    row = {"role": "button", "near": {"name": "Shop", "relation": "same_row", "nth": 2}}
    assert marks(find(scoped, row)) == [4]


def test_an_enclosing_named_group_is_not_an_anchor():
    scoped = [
        el(1, "group", "필터", (0, 190, 600, 50)),
        el(2, "statictext", "Shop", (10, 200, 60, 30)),
        el(3, "button", None, (100, 200, 30, 30)),
        el(4, "button", None, (150, 200, 30, 30)),
    ]
    assert describe(scoped, 4)["near"] == {"name": "Shop", "relation": "same_row", "nth": 2}


def test_an_element_without_a_position_can_be_neither_anchor_nor_told_apart():
    scoped = [
        el(1, "statictext", "Shop", None),
        el(2, "button", None, (100, 200, 30, 30)),
        el(3, "button", None, (150, 200, 30, 30)),
        el(4, "button", "확인", None),
    ]
    with pytest.raises(LocatorError):
        describe(scoped, 2)
    assert describe(scoped, 4) == {"role": "button", "name": "확인"}
    assert marks(find(scoped, {"role": "button", "name": "확인"})) == [4]


def test_an_unnamed_target_without_a_position_fails():
    scoped = [
        el(1, "statictext", "Shop", (10, 200, 60, 30)),
        el(2, "button", None, None),
        el(3, "button", None, (150, 200, 30, 30)),
    ]
    with pytest.raises(LocatorError):
        describe(scoped, 2)


def test_a_mark_outside_the_scope_fails():
    with pytest.raises(LocatorError):
        describe(in_scope(form(), None), 7)


def test_describe_then_find_round_trips_for_every_element():
    elements = form()
    for scope in (None, 5):
        scoped = in_scope(elements, scope)
        for element in scoped:
            assert marks(find(scoped, describe(scoped, element["mark"]))) == [element["mark"]]
    table = headed_table()
    for element in table:
        assert marks(find(table, describe(table, element["mark"]))) == [element["mark"]]


def moved(elements, key="rect"):
    shifted = []
    for element in elements:
        copy = dict(element)
        box = copy.pop("rect")
        copy[key] = None if box is None else {
            "x": box["x"] * 1.75 + 300, "y": box["y"] * 1.75 + 120,
            "width": box["width"] * 1.75, "height": box["height"] * 1.75,
        }
        shifted.append(copy)
    return shifted


@pytest.mark.parametrize("key", ["rect", "bounds"])
def test_find_survives_scaling_and_shifting(key):
    recorded = in_scope(form(), None)
    live = in_scope(moved(form(), key), None)
    for element in recorded:
        locator = describe(recorded, element["mark"])
        assert marks(find(live, locator)) == [element["mark"]]
    table = headed_table()
    assert marks(find(moved(table, key), describe(table, 6))) == [6]


def test_live_bounds_are_read_like_recorded_rects():
    live = [el(1, "statictext", "Shop", (10, 200, 60, 30), key="bounds"),
            el(2, "button", None, (100, 200, 30, 30), key="bounds"),
            el(3, "button", None, (150, 200, 30, 30), key="bounds")]
    assert describe(live, 3)["near"] == {"name": "Shop", "relation": "same_row", "nth": 2}


def test_find_with_a_parameter_name():
    scoped = [el(1, "statictext", "전일", (10, 10, 50, 20)), el(2, "statictext", "전주", (10, 40, 50, 20))]
    assert marks(find(scoped, {"role": "statictext", "name": "$p"}, name="전주")) == [2]
    assert find(scoped, {"role": "statictext", "name": "$p"}, name="전월") == []


def test_find_reports_none_and_many():
    scoped = headed_table(with_headers=False)
    assert find(scoped, {"role": "statictext", "name": "없음"}) == []
    assert find(scoped, {"role": "button", "name": "전체"}) == []
    assert marks(find(scoped, {"role": "statictext", "name": "전체"})) == [4, 5, 6]


def test_find_with_an_anchor_reports_none_and_many():
    main = in_scope(form(), None)
    beyond = {"role": "button", "near": {"name": "Shop", "relation": "same_row", "nth": 4}}
    assert find(main, beyond) == []
    assert find(main, {"role": "button", "near": {"name": "없음", "relation": "same_row", "nth": 1}}) == []
    doubled = main + [el(30, "statictext", "Shop", (10, 600, 60, 30)), el(31, "button", None, (100, 600, 30, 30))]
    found = find(doubled, {"role": "button", "near": {"name": "Shop", "relation": "same_row", "nth": 1}})
    assert sorted(marks(found)) == [4, 31]


def test_a_tie_in_the_row_is_reported_as_many():
    scoped = [
        el(1, "statictext", "Shop", (10, 200, 60, 60)),
        el(2, "button", None, (100, 200, 30, 25)),
        el(3, "button", None, (100, 235, 30, 25)),
    ]
    assert sorted(marks(find(scoped, {"role": "button",
                                      "near": {"name": "Shop", "relation": "same_row", "nth": 1}}))) == [2, 3]


def test_name_match_ignores_only_outer_spaces():
    scoped = [el(1, "statictext", " WA0 ", (10, 10, 50, 20)), el(2, "statictext", "WA1", (10, 40, 50, 20))]
    assert marks(find(scoped, {"role": "statictext", "name": "WA0"})) == [1]
    assert find(scoped, {"role": "statictext", "name": "wa0"}) == []
    assert find(scoped, {"role": "statictext", "name": "W A0"}) == []
    assert describe(scoped, 1) == {"role": "statictext", "name": "WA0"}


def test_a_locator_without_a_name_matches_only_unnamed_elements():
    scoped = [el(1, "button", None, (10, 10, 30, 30)), el(2, "button", "확인", (60, 10, 30, 30))]
    assert describe(scoped, 1) == {"role": "button"}
    assert marks(find(scoped, {"role": "button"})) == [1]


def test_new_windows_and_handles():
    list_one = el(5, "window", "목록", (100, 240, 200, 200))
    after = [el(1, "button", "Shop", (10, 10, 30, 30)), list_one]
    opened = new_windows([], after)
    assert marks(opened) == [5]
    assert window_handle(after, list_one) == {"name": "목록", "nth": 1}

    list_two = el(9, "window", "목록", (400, 240, 200, 200))
    both = after + [list_two]
    assert marks(new_windows(after, both)) == [9]
    handle = window_handle(both, list_two)
    assert handle == {"name": "목록", "nth": 2}
    assert resolve_handle(both, handle)["mark"] == 9
    assert resolve_handle(after, handle) is None
    assert resolve_handle(both, {"name": "목록", "nth": 1})["mark"] == 5


def test_an_unnamed_window_has_a_handle_too():
    popup = el(3, "window", None, (0, 0, 50, 50))
    elements = [el(1, "button", "a", (0, 0, 10, 10)), popup]
    assert window_handle(elements, popup) == {"name": None, "nth": 1}
    assert resolve_handle(elements, {"name": None, "nth": 1})["mark"] == 3
    assert marks(new_windows([], elements)) == [3]


def test_a_window_already_open_is_not_new():
    before = [el(5, "window", "목록", (0, 0, 50, 50))]
    after = [el(7, "window", "목록", (0, 0, 50, 50))]
    assert new_windows(before, after) == []


def test_overlaps_is_at_least_half_of_the_shorter_span():
    label = {"x": 10, "y": 200, "width": 60, "height": 30}
    assert overlaps(label, {"x": 100, "y": 214, "width": 30, "height": 30}, "y") is True
    assert overlaps(label, {"x": 100, "y": 223, "width": 30, "height": 30}, "y") is False
    assert overlaps(label, {"x": 40, "y": 500, "width": 60, "height": 30}, "x") is True
    assert overlaps(label, {"x": 60, "y": 500, "width": 60, "height": 30}, "x") is False
    assert overlaps(label, {"x": 10, "y": 200, "width": 0, "height": 30}, "x") is False


def test_box_reads_rect_or_bounds_and_refuses_no_area():
    rect = {"x": 1, "y": 2, "width": 3, "height": 4}
    assert box({"rect": rect}) == rect
    assert box({"rect": None, "bounds": rect}) == rect
    assert box({"rect": {"x": 1, "y": 2, "width": 0, "height": 4}}) is None
    assert box({"rect": None}) is None
    assert box({}) is None


TOOLBAR = {"role": "toolbar", "id": "12"}


def ided(mark, role, native, name=None, box=(0, 0, 10, 10), area=None, **extra):
    """An element as an observation records it with a native id (plan K3): native_id as
    {"kind", "value"} and, when it sits in one, its area as "in"."""
    element = el(mark, role, name, box, **extra)
    element["native_id"] = {"kind": "automation_id", "value": native}
    if area is not None:
        element["in"] = area
    return element


def picture_menu():
    """Two menu items drawn as pictures: no name, no text near them, an id each, one toolbar."""
    return [
        ided(1, "toolbar", "12", box=(0, 0, 400, 40)),
        ided(2, "image", "56", box=(10, 5, 30, 30), area=TOOLBAR),
        ided(3, "image", "60", box=(50, 5, 30, 30), area=TOOLBAR),
    ]


def test_describe_adds_id_and_in_on_top_of_the_name():
    elements = [ided(1, "button", "7001", "조회", area=TOOLBAR), el(2, "button", "저장")]

    assert describe(elements, 1, use_ids=True) == {"role": "button", "name": "조회", "id": "7001", "in": TOOLBAR}


def test_describe_without_use_ids_is_what_it_was():
    elements = [ided(1, "button", "7001", "조회", area=TOOLBAR), el(2, "button", "저장")]

    assert describe(elements, 1) == {"role": "button", "name": "조회"}
    assert describe(elements, 1, use_ids=False) == {"role": "button", "name": "조회"}


def test_describe_with_use_ids_on_a_record_without_ids_is_what_it_was():
    """An observation recorded before native ids were kept makes the same locator, not an error."""
    elements = form()

    for element in elements:
        if element["role"] == "window" or element["scope"] is not None:
            continue
        try:
            plain = describe(in_scope(elements, None), element["mark"])
        except LocatorError:
            with pytest.raises(LocatorError):
                describe(in_scope(elements, None), element["mark"], use_ids=True)
            continue
        assert describe(in_scope(elements, None), element["mark"], use_ids=True) == plain


def test_an_element_without_an_area_gets_no_in():
    elements = [ided(1, "button", "7001", "조회")]

    assert describe(elements, 1, use_ids=True) == {"role": "button", "name": "조회", "id": "7001"}


def test_an_area_named_by_its_name_is_written_as_it_is():
    area = {"role": "pane", "name": "메뉴"}

    assert describe([ided(1, "image", "56", area=area)], 1, use_ids=True) == {"role": "image", "id": "56", "in": area}


def test_describe_tells_picture_only_items_apart_by_id():
    elements = picture_menu()

    assert describe(elements, 2, use_ids=True) == {"role": "image", "id": "56", "in": TOOLBAR}
    assert describe(elements, 3, use_ids=True) == {"role": "image", "id": "60", "in": TOOLBAR}
    with pytest.raises(LocatorError):
        describe(elements, 2)


def test_find_needs_the_id_and_the_container_a_locator_writes():
    other = {"role": "toolbar", "id": "13"}
    elements = [ided(1, "image", "56", area=TOOLBAR), ided(2, "image", "56", area=other),
                ided(3, "image", "56"), ided(4, "image", "60", area=TOOLBAR)]

    assert marks(find(elements, {"role": "image", "id": "56", "in": TOOLBAR})) == [1]
    assert marks(find(elements, {"role": "image", "id": "56", "in": other})) == [2]
    assert marks(find(elements, {"role": "image", "id": "56"})) == [1, 2, 3]
    assert marks(find(elements, {"role": "image", "id": "56", "in": {"role": "toolbar", "name": "12"}})) == []
    assert marks(find(elements, {"role": "button", "id": "56"})) == []


def test_find_with_an_id_still_needs_the_name():
    elements = [ided(1, "button", "7001", "조회"), ided(2, "button", "7001")]

    assert marks(find(elements, {"role": "button", "name": "검색", "id": "7001"})) == []
    assert marks(find(elements, {"role": "button", "name": "조회", "id": "7001"})) == [1]
    assert marks(find(elements, {"role": "button", "id": "7001"})) == [2]


def test_find_compares_ids_letter_for_letter():
    elements = [ided(1, "image", "56"), ided(2, "image", "056")]

    assert marks(find(elements, {"role": "image", "id": "56"})) == [1]


def test_find_without_an_id_does_not_look_at_ids():
    """A workflow saved before ids were written finds the same elements as before."""
    elements = [ided(1, "button", "7001", "조회", area=TOOLBAR), el(2, "button", "조회")]

    assert marks(find(elements, {"role": "button", "name": "조회"})) == [1, 2]


def test_find_reads_a_native_id_given_as_text():
    elements = [{**el(1, "image"), "native_id": "56"}]

    assert marks(find(elements, {"role": "image", "id": "56"})) == [1]


def test_find_after_describe_gives_only_the_target_with_ids():
    elements = [
        ided(1, "statictext", "l1", "Shop", (10, 200, 60, 30)),
        ided(2, "button", "pick", None, (100, 200, 30, 30), area=TOOLBAR),
        ided(3, "button", "pick", None, (150, 200, 30, 30), area=TOOLBAR),
        ided(4, "button", "clear", None, (200, 200, 30, 30), area=TOOLBAR),
        *picture_menu()[1:],
        el(7, "button", "조회(F5)", (500, 100, 80, 30)),
    ]
    elements[4]["mark"], elements[5]["mark"] = 5, 6

    for element in elements:
        locator = describe(elements, element["mark"], use_ids=True)
        assert find(elements, locator) == [element], locator


def test_the_anchor_counts_among_the_elements_that_share_the_id():
    """near's n-th is counted among the elements with the same role, name, description, id and
    area, the way find counts it: here the third button in the row is the second with id "a"."""
    elements = [
        el(1, "statictext", "Shop", (10, 200, 60, 30)),
        ided(2, "button", "a", None, (100, 200, 30, 30)),
        ided(3, "button", "b", None, (150, 200, 30, 30)),
        ided(4, "button", "a", None, (200, 200, 30, 30)),
    ]

    locator = describe(elements, 4, use_ids=True)

    assert locator == {"role": "button", "id": "a", "near": {"name": "Shop", "relation": "same_row", "nth": 2}}
    assert find(elements, locator) == [elements[3]]
    assert describe(elements, 4) == {"role": "button", "near": {"name": "Shop", "relation": "same_row", "nth": 3}}


def test_an_area_named_by_an_avoided_name_is_not_written():
    """An area named by text that changes (a date, a count) would not be there for another run:
    the id is written without it, and near is added when the id alone is not enough."""
    dated = {"role": "pane", "name": "2026-11-19 3건"}
    elements = [ided(1, "image", "56", box=(10, 5, 30, 30), area=dated),
                ided(2, "image", "60", box=(50, 5, 30, 30), area=TOOLBAR)]

    assert describe(elements, 1, avoid=[" 2026-11-19 3건 "], use_ids=True) == {"role": "image", "id": "56"}
    assert describe(elements, 1, use_ids=True) == {"role": "image", "id": "56", "in": dated}
    assert describe(elements, 2, avoid=["12"], use_ids=True) == {"role": "image", "id": "60", "in": TOOLBAR}

    twins = [el(1, "statictext", "Shop", (10, 200, 60, 30)),
             ided(2, "button", "a", None, (100, 200, 30, 30), area=dated),
             ided(3, "button", "a", None, (150, 200, 30, 30), area={"role": "pane", "name": "어제"})]
    assert describe(twins, 2, avoid=["2026-11-19 3건"], use_ids=True) == {
        "role": "button", "id": "a", "near": {"name": "Shop", "relation": "same_row", "nth": 1}}
