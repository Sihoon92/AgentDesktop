import pytest

from agent.geometry import PARTS, contains, part_point, smallest_containing

BOX = {"x": 100, "y": 50, "width": 104, "height": 24}


@pytest.mark.parametrize("part,point", [
    ("center", (152, 62)), ("left", (112, 62)), ("right", (192, 62)),
    ("top", (152, 56)), ("bottom", (152, 68))])
def test_part_points_of_a_wide_box(part, point):
    assert part_point(BOX, part) == point


def test_parts_lists_every_part():
    assert PARTS == ("center", "left", "right", "top", "bottom")


def test_square_box_uses_a_quarter_inset():
    assert part_point({"x": 0, "y": 0, "width": 20, "height": 20}, "right") == (15, 10)


def test_tall_box_insets_by_half_its_width_at_most():
    assert part_point({"x": 0, "y": 0, "width": 20, "height": 200}, "bottom") == (10, 190)


def test_float_bounds_give_integer_points():
    point = part_point({"x": 0.5, "y": 0.5, "width": 99.0, "height": 9.0}, "center")
    assert point == (50, 5) and all(isinstance(value, int) for value in point)


@pytest.mark.parametrize("bounds", [
    None, {"x": 0, "y": 0, "width": 0, "height": 10}, {"x": 0, "y": 0, "width": 10, "height": -1}])
def test_no_area_raises(bounds):
    with pytest.raises(ValueError):
        part_point(bounds, "center")


def test_unknown_part_raises():
    with pytest.raises(ValueError):
        part_point(BOX, "middle")


def test_contains_includes_the_left_and_top_edges_only():
    assert contains(BOX, 100, 50)
    assert contains(BOX, 203, 73)
    assert not contains(BOX, 204, 60)
    assert not contains(None, 0, 0)


def test_smallest_containing_prefers_the_inner_element():
    outer = {"mark": 1, "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}}
    inner = {"mark": 2, "bounds": {"x": 10, "y": 10, "width": 20, "height": 20}}
    nowhere = {"mark": 3, "bounds": None}
    assert smallest_containing([outer, inner, nowhere], 15, 15)["mark"] == 2
    assert smallest_containing([outer, inner, nowhere], 500, 500) is None
