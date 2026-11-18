import pytest

from agent.effects import EXPECTATIONS, diff, met, state

W1 = {"id": "w-1", "title": "메인", "bounds": {"x": 0, "y": 0, "width": 10, "height": 10}}
W2 = {"id": "w-2", "title": "목록", "bounds": {"x": 0, "y": 0, "width": 10, "height": 10}}


def _element(mark, name, kind="text", role="statictext", value=None, states=(), description=None):
    return {"kind": kind, "ref": None if kind == "text" else f"@s1:e{mark}", "role": role, "name": name,
            "description": description, "states": list(states), "value": value,
            "bounds": {"x": mark * 10, "y": 0, "width": 10, "height": 10}, "mark": mark}


MARKS = {
    1: _element(1, "Shop"),
    2: _element(2, "조회", kind="control", role="button"),
    3: _element(3, "라인"),
    4: _element(4, "라인"),
    5: _element(5, "기간", kind="control", role="combobox", value="전일"),
}


def with_state(marks, mark, add):
    changed = dict(marks)
    changed[mark] = dict(marks[mark], states=marks[mark]["states"] + [add])
    return changed


def with_value(marks, mark, value):
    changed = dict(marks)
    changed[mark] = dict(marks[mark], value=value)
    return changed


def cells(*values):
    marks = {1: _element(1, "양품수")}
    for offset, value in enumerate(values, 2):
        marks[offset] = _element(offset, value)
    return marks


def many(count):
    return {n: _element(n, f"행{n}") for n in range(1, count + 1)}


def test_expectations_are_the_closed_list():
    assert EXPECTATIONS == ("window_opened", "window_closed", "content_changed", "value_changed", "any")


def test_new_window_is_reported_and_meets_window_opened():
    effects = diff(state([W1], MARKS), state([W1, W2], MARKS))
    assert effects["windows_opened"] == [{"id": "w-2", "title": "목록"}]
    assert effects["windows_closed"] == []
    assert met("window_opened", effects) and not met("window_closed", effects)
    assert met("any", effects)


def test_closed_window_meets_window_closed():
    effects = diff(state([W1, W2], MARKS), state([W1], MARKS))
    assert effects["windows_closed"] == [{"id": "w-2", "title": "목록"}]
    assert met("window_closed", effects) and not met("window_opened", effects)


def test_caret_only_click_changes_nothing_but_focus():
    after = with_state(MARKS, mark=3, add="focused")
    effects = diff(state([W1], MARKS), state([W1], after))
    assert effects["focus"] == {"mark": 3, "name": "라인"}
    assert effects["added"]["count"] == 0 and effects["changed"] == []
    assert effects["none"] is False
    assert not met("window_opened", effects) and not met("content_changed", effects)
    assert not met("value_changed", effects)
    assert met("any", effects)


def test_focus_that_stays_put_is_not_reported():
    focused = with_state(MARKS, mark=3, add="focused")
    effects = diff(state([W1], focused), state([W1], focused))
    assert effects["focus"] is None and effects["none"] is True


def test_same_named_elements_are_told_apart_by_order():
    effects = diff(state([W1], MARKS), state([W1], with_state(MARKS, mark=4, add="focused")))
    assert effects["focus"] == {"mark": 4, "name": "라인"}


def test_value_change_names_before_and_after():
    effects = diff(state([W1], MARKS), state([W1], with_value(MARKS, mark=5, value="전주")))
    assert effects["changed"] == [
        {"mark": 5, "name": "기간", "field": "value", "before": "전일", "after": "전주"}]
    assert met("value_changed", effects) and not met("content_changed", effects)


def test_state_change_other_than_focus_is_a_change():
    effects = diff(state([W1], MARKS), state([W1], with_state(MARKS, mark=2, add="checked")))
    assert effects["changed"] == [
        {"mark": 2, "name": "조회", "field": "states", "before": [], "after": ["checked"]}]


def test_text_cells_that_change_meet_value_changed():
    effects = diff(state([W1], cells("0", "0")), state([W1], cells("0", "12,480")))
    assert effects["added"] == {"count": 1, "names": ["12,480"]}
    assert effects["removed"] == {"count": 1, "names": ["0"]}
    assert met("value_changed", effects) and met("content_changed", effects)


def test_identical_states_are_none():
    effects = diff(state([W1], MARKS), state([W1], MARKS))
    assert effects["none"] is True and not met("any", effects)
    assert not any(met(expect, effects) for expect in EXPECTATIONS)


def test_names_are_capped_at_eight():
    effects = diff(state([W1], {}), state([W1], many(30)))
    assert effects["added"]["count"] == 30 and len(effects["added"]["names"]) == 8


def test_changes_are_capped_at_eight():
    before = {n: _element(n, f"칸{n}", value="0") for n in range(1, 20)}
    after = {n: dict(element, value="1") for n, element in before.items()}
    assert len(diff(state([W1], before), state([W1], after))["changed"]) == 8


def test_unknown_expectation_raises():
    with pytest.raises(ValueError):
        met("popup", diff(state([W1], MARKS), state([W1], MARKS)))


def test_marks_read_from_two_windows_are_not_compared():
    popup_items = {1: _element(1, "WA2"), 2: _element(2, "Select")}

    effects = diff(state([W1, W2], popup_items, "w-2"), state([W1], MARKS, "w-1"))

    assert effects["observed_window"] == {"before": "w-2", "after": "w-1"}
    assert effects["added"]["count"] == 0 and effects["removed"]["count"] == 0 and effects["changed"] == []
    assert effects["windows_closed"] == [{"id": "w-2", "title": "목록"}] and effects["none"] is False
    assert met("window_closed", effects) and met("any", effects)
    assert met("content_changed", effects) and not met("value_changed", effects)


def test_a_window_that_opens_or_closes_meets_content_changed_with_no_element_added():
    opened = diff(state([W1], MARKS), state([W1, W2], MARKS))
    closed = diff(state([W1, W2], MARKS), state([W1], MARKS))

    assert opened["added"]["count"] == 0 and closed["removed"]["count"] == 0
    assert met("content_changed", opened) and met("content_changed", closed)
    assert not met("value_changed", opened) and not met("value_changed", closed)


def test_a_window_switch_alone_is_still_an_effect():
    effects = diff(state([W1, W2], MARKS, "w-2"), state([W1, W2], MARKS, "w-1"))

    assert effects["none"] is False and effects["observed_window"] == {"before": "w-2", "after": "w-1"}


def test_marks_of_the_same_window_or_of_an_unnamed_one_are_compared():
    same = diff(state([W1], MARKS, "w-1"), state([W1], with_value(MARKS, 5, "금일"), "w-1"))
    unnamed = diff(state([W1], MARKS), state([W1], with_value(MARKS, 5, "금일"), "w-1"))

    assert "observed_window" not in same and same["changed"][0]["after"] == "금일"
    assert "observed_window" not in unnamed and unnamed["changed"][0]["after"] == "금일"
