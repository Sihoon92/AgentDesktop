import pytest

from agent.guard import ActionGuard, AgentStopped, signature
from agent.policy import TOLERANCE_PX

SWITCH_CLOSED = {"role": "switch", "name": "설정 ◂", "description": "패널"}
SWITCH_OPEN = {"role": "switch", "name": "설정 ▸", "description": "패널"}
NEXT = {"role": "button", "name": "다음", "description": None}
DELIVERED = {"ok": True, "disposition": {"delivery": "delivered_verified", "retry": "unsafe"}}
NOT_DELIVERED = {"ok": False, "disposition": {"delivery": "not_delivered", "retry": "safe"}}
UNKNOWN = {"ok": False, "disposition": {"delivery": "delivered_unverified", "retry": "unsafe"}}


def test_second_toggle_is_refused_even_when_the_label_arrow_changed():
    guard = ActionGuard()
    guard.record(signature("toggle", SWITCH_CLOSED), DELIVERED)

    refusal = guard.check(signature("toggle", SWITCH_OPEN))

    assert refusal is not None
    assert "check" in refusal


def test_toggle_may_retry_after_not_delivered():
    guard = ActionGuard()
    guard.record(signature("toggle", SWITCH_CLOSED), NOT_DELIVERED)

    assert guard.check(signature("toggle", SWITCH_CLOSED)) is None


def test_failed_action_with_unsafe_retry_is_refused():
    guard = ActionGuard()
    guard.record(signature("click", NEXT), UNKNOWN)

    assert guard.check(signature("click", NEXT)) is not None


def test_delivered_click_may_repeat_until_the_limit_then_stops_the_run():
    guard = ActionGuard(max_repeats=3)
    sig = signature("click", NEXT)
    for _ in range(3):
        assert guard.check(sig) is None
        guard.record(sig, DELIVERED)

    with pytest.raises(AgentStopped):
        guard.check(sig)


def test_different_arguments_are_different_actions():
    guard = ActionGuard()
    guard.record(signature("set_value", NEXT, "1"), UNKNOWN)

    assert guard.check(signature("set_value", NEXT, "2")) is None


ORIGIN = (1000, 500)


def nameless(x, y, native=None):
    element = {"role": "button", "name": None, "description": None,
               "bounds": {"x": x, "y": y, "width": 24, "height": 24}}
    if native is not None:
        element["native_id"] = {"kind": "automation_id", "value": native}
    return element


def test_nameless_elements_with_different_native_ids_are_different_actions():
    guard = ActionGuard(max_repeats=1)
    guard.record(signature("click", nameless(10, 10, "7001"), origin=ORIGIN), DELIVERED)

    assert guard.check(signature("click", nameless(10, 10, "7002"), origin=ORIGIN)) is None
    with pytest.raises(AgentStopped):
        guard.check(signature("click", nameless(400, 400, "7001"), origin=ORIGIN))


def test_nameless_elements_without_native_ids_are_told_apart_by_place():
    guard = ActionGuard(max_repeats=1)
    guard.record(signature("click", nameless(1100, 540), origin=ORIGIN), DELIVERED)

    assert guard.check(signature("click", nameless(1150, 540), origin=ORIGIN)) is None
    with pytest.raises(AgentStopped):
        guard.check(signature("click", nameless(1100 + TOLERANCE_PX, 540 - TOLERANCE_PX), origin=ORIGIN))


def test_a_place_is_relative_to_the_window():
    guard = ActionGuard(max_repeats=1)
    guard.record(signature("click", nameless(1100, 540), origin=ORIGIN), DELIVERED)

    with pytest.raises(AgentStopped):
        guard.check(signature("click", nameless(1400, 580), origin=(1300, 540)))


def test_a_place_just_beyond_the_tolerance_is_another_element():
    guard = ActionGuard(max_repeats=1)
    guard.record(signature("click", nameless(1100, 540), origin=ORIGIN), DELIVERED)

    assert guard.check(signature("click", nameless(1100 + TOLERANCE_PX + 1, 540), origin=ORIGIN)) is None


def test_named_elements_are_still_told_apart_by_name_alone_unless_they_have_native_ids():
    first = {**NEXT, "bounds": {"x": 0, "y": 0, "width": 10, "height": 10}}
    second = {**NEXT, "bounds": {"x": 500, "y": 500, "width": 10, "height": 10}}
    assert signature("click", first, origin=ORIGIN) == signature("click", second, origin=ORIGIN)
    assert signature("click", {**first, "native_id": {"kind": "automation_id", "value": "a"}}) != signature(
        "click", {**second, "native_id": {"kind": "automation_id", "value": "b"}})


def test_an_unsafe_failure_blocks_only_the_element_at_that_place():
    guard = ActionGuard()
    guard.record(signature("click", nameless(1100, 540), origin=ORIGIN), UNKNOWN)

    assert guard.check(signature("click", nameless(1103, 538), origin=ORIGIN)) is not None
    assert guard.check(signature("click", nameless(1250, 540), origin=ORIGIN)) is None
