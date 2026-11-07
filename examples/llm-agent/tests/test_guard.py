import pytest

from agent.guard import ActionGuard, AgentStopped, signature

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
