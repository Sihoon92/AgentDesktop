"""Launching a system and waiting until a window shows the ready signal (agent/ready.py)."""

import json
import math
from types import SimpleNamespace

import pytest

from agent.ready import (
    NO_WINDOW_S,
    READY_MESSAGES,
    READY_POLL_S,
    READY_STATUSES,
    RUNNING_HINT_MAX_WINDOWS,
    ReadyOutcome,
    candidates,
    has_named_element,
    measure,
    ready_message,
    recommended_read,
    signals,
    wait_until_ready,
)
from tests.ready_support import SIGNAL, FakeClock, FakeWindow, TimelineDesktop, node, window_tree

EXE = r"C:\Apps\System\Main.exe"
MENUS = ("생산준비", SIGNAL, "기준정보")


def wait(desktop, clock, timeout_s=180, **options):
    return wait_until_ready(desktop, EXE, SIGNAL, timeout_s, clock, clock.sleep, **options)


def main_window(window_id="w-main", pid=100, appear=0.0, menus_at=0.0, **options):
    return FakeWindow(window_id, pid, title="Main", appear=appear,
                      trees=[(appear, window_tree("Main")), (menus_at, window_tree("Main", *MENUS))], **options)


def test_constants_follow_the_contract():
    assert NO_WINDOW_S == 30
    assert READY_POLL_S == 0.3
    assert RUNNING_HINT_MAX_WINDOWS == 30
    assert READY_STATUSES == ("ready", "already_running", "no_new_window", "timeout", "launch_failed",
                              "cancelled")


def test_has_named_element_needs_the_whole_name():
    assert has_named_element(window_tree("Main", SIGNAL), SIGNAL)
    assert not has_named_element(window_tree("Main", f"{SIGNAL}시스템 시작 중"), SIGNAL)
    assert has_named_element(window_tree("Main", f"  {SIGNAL} "), SIGNAL)
    assert has_named_element(window_tree("Main", "생산준비", SIGNAL, deep=True), SIGNAL)
    assert has_named_element(window_tree("Main", SIGNAL), f" {SIGNAL}\n")
    assert not has_named_element(window_tree("Main", "생산준비"), SIGNAL)
    assert not has_named_element(node(role="pane", children=[node(None)]), SIGNAL)


def test_a_window_title_alone_is_not_the_signal():
    """A splash titled exactly like the signal is still not the ready window: its root is the
    window, not an element on it."""
    assert not has_named_element(window_tree(SIGNAL), SIGNAL)
    assert has_named_element(node(role="pane", children=[node(SIGNAL)]), SIGNAL)


def test_ready_in_the_launched_process():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=3.0)])
    outcome = wait(desktop, clock)
    assert outcome.status == "ready"
    assert (outcome.pid, outcome.instance, outcome.switched) == (100, "proc:100", False)
    assert outcome.window["id"] == "w-main"
    assert has_named_element(outcome.tree_envelope["data"]["tree"], SIGNAL)
    assert outcome.waited_ms == 3000
    assert outcome.polls == 11
    assert outcome.running_hint is False


def test_ready_in_a_new_process_after_the_splash():
    clock = FakeClock()
    splash = FakeWindow("w-splash", 100, title=f"{SIGNAL}시스템", appear=0.0, gone=2.0,
                        trees=[(0.0, window_tree(f"{SIGNAL}시스템", f"{SIGNAL}시스템을 시작하는 중", SIGNAL + "..."))])
    main = main_window("w-main", pid=200, appear=2.0, menus_at=4.0)
    desktop = TimelineDesktop(clock, [splash, main], launch_pid=100)
    outcome = wait(desktop, clock)
    assert outcome.status == "ready"
    assert (outcome.pid, outcome.instance, outcome.window["id"]) == (200, "proc:200", "w-main")
    assert outcome.switched is True
    assert desktop.snapshots_of("w-splash"), "the splash was a candidate and was read"


def test_already_running_stops_without_waiting():
    clock = FakeClock()
    running = main_window("w-old", pid=100, appear=-50.0, menus_at=-40.0)
    desktop = TimelineDesktop(clock, [running], launch_pid=100)
    outcome = wait(desktop, clock)
    assert outcome.status == "already_running"
    assert outcome.polls == 0
    assert desktop.commands("snapshot") == []
    assert clock.sleeps == []
    assert outcome.pid is None and outcome.window is None


def test_same_pid_with_another_instance_is_not_already_running():
    """A reused pid is a new process: only pid and process_instance together say it ran before."""
    clock = FakeClock()
    old = main_window("w-old", pid=100, instance="proc:old", appear=-50.0, menus_at=-50.0)
    desktop = TimelineDesktop(clock, [old, main_window("w-new", pid=100, menus_at=1.0)], launch_pid=100)
    outcome = wait(desktop, clock)
    assert (outcome.status, outcome.window["id"], outcome.instance) == ("ready", "w-new", "proc:100")
    assert desktop.snapshots_of("w-old") == []


def test_list_before_launch_is_read_before_launching():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=0.0)])
    wait(desktop, clock)
    assert [call[0] for call in desktop.calls[:2]] == ["list-windows", "launch"]
    assert desktop.commands("launch") == [("launch", EXE)]


def test_another_system_left_open_is_not_a_candidate():
    clock = FakeClock()
    other = main_window("w-other", pid=50, appear=-100.0, menus_at=-90.0)
    mine = main_window("w-mine", pid=100, appear=0.0, menus_at=5.0)
    desktop = TimelineDesktop(clock, [other, mine], launch_pid=100)
    outcome = wait(desktop, clock)
    assert outcome.status == "ready"
    assert (outcome.pid, outcome.window["id"], outcome.switched) == (100, "w-mine", False)
    assert outcome.waited_ms == 5100
    assert desktop.snapshots_of("w-other") == []


def test_a_new_window_of_an_old_process_is_a_candidate():
    """A window the old process opens after the launch was not in the list before it."""
    clock = FakeClock()
    old_first = FakeWindow("w-a", 50, appear=-100.0)
    old_second = main_window("w-b", pid=50, appear=1.0, menus_at=2.0)
    launched = FakeWindow("w-l", 100, appear=0.0, gone=1.0)
    desktop = TimelineDesktop(clock, [old_first, old_second, launched], launch_pid=100)
    outcome = wait(desktop, clock)
    assert (outcome.status, outcome.window["id"], outcome.switched) == ("ready", "w-b", True)
    assert desktop.snapshots_of("w-a") == []


def test_a_minimized_window_is_still_a_candidate():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=1.0, minimized=True)])
    assert wait(desktop, clock).status == "ready"


def test_no_new_window_for_30_seconds():
    clock = FakeClock()
    other = main_window("w-old", pid=50, appear=-100.0, menus_at=-90.0)
    desktop = TimelineDesktop(clock, [other], launch_pid=100)
    outcome = wait(desktop, clock, timeout_s=180)
    assert outcome.status == "no_new_window"
    assert outcome.running_hint is True
    assert NO_WINDOW_S <= outcome.waited_ms / 1000 < NO_WINDOW_S + 2
    assert clock.now < 40
    assert len(desktop.snapshots_of("w-old")) == 1, "only the hint reads the window from before"


def test_no_new_window_without_hint():
    clock = FakeClock()
    other = FakeWindow("w-old", 50, title="Editor", appear=-100.0, trees=[(-100.0, window_tree("Editor", "File"))])
    desktop = TimelineDesktop(clock, [other], launch_pid=100)
    outcome = wait(desktop, clock)
    assert (outcome.status, outcome.running_hint) == ("no_new_window", False)


def test_a_splash_seen_once_is_a_new_window():
    """A splash that showed and closed counts: the 30 seconds rule does not apply after it."""
    clock = FakeClock()
    splash = FakeWindow("w-splash", 100, appear=0.0, gone=1.5)
    desktop = TimelineDesktop(clock, [splash], launch_pid=100)
    outcome = wait(desktop, clock, timeout_s=45)
    assert outcome.status == "timeout"
    assert outcome.waited_ms >= 45000


def test_the_window_launch_returned_is_a_new_window():
    """launch may return the splash window that closes before the first list-windows."""
    clock = FakeClock()
    splash = FakeWindow("w-splash", 100, appear=0.0, gone=0.0)
    desktop = TimelineDesktop(clock, [splash], launch_pid=100)
    desktop._launch = lambda now: {"ok": True, "data": {"pid": 100, "process_instance": "proc:100",
                                                        "window": splash.row()}}
    outcome = wait(desktop, clock, timeout_s=40)
    assert outcome.status == "timeout"


def test_timeout_when_the_signal_never_appears():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, appear=0.0)], launch_pid=100)
    outcome = wait(desktop, clock, timeout_s=20)
    assert outcome.status == "timeout"
    assert 20 <= outcome.waited_ms / 1000 < 22
    assert outcome.running_hint is False
    assert outcome.pid is None


def test_timeout_with_a_hint_from_a_window_before():
    clock = FakeClock()
    other = main_window("w-old", pid=50, appear=-100.0, menus_at=-90.0)
    desktop = TimelineDesktop(clock, [other, FakeWindow("w-main", 100, appear=0.0)], launch_pid=100)
    outcome = wait(desktop, clock, timeout_s=20)
    assert (outcome.status, outcome.running_hint) == ("timeout", True)


def test_launch_failure_is_an_outcome():
    clock = FakeClock()
    error = {"code": "APP_NOT_FOUND", "message": "missing"}
    desktop = TimelineDesktop(clock, [], launch_error=error)
    outcome = wait(desktop, clock)
    assert outcome.status == "launch_failed"
    assert outcome.launch_error == error
    assert desktop.commands("snapshot") == []
    assert outcome.polls == 0
    assert outcome.running_hint is False


def test_launch_failure_reads_the_hint_from_windows_before():
    """A launcher of a system already running may end without a window, and launch fails."""
    clock = FakeClock()
    other = main_window("w-old", pid=50, appear=-100.0, menus_at=-90.0)
    desktop = TimelineDesktop(clock, [other], launch_error={"code": "TIMEOUT", "message": "no window"})
    outcome = wait(desktop, clock)
    assert (outcome.status, outcome.running_hint) == ("launch_failed", True)
    assert desktop.snapshots_of("w-old")
    assert clock.sleeps == []


def test_polls_wait_the_pause_after_each_read():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=10.0)], snapshot_s=1.5)
    outcome = wait(desktop, clock)
    assert outcome.status == "ready"
    assert clock.sleeps and set(clock.sleeps) == {READY_POLL_S}
    assert len(clock.sleeps) == outcome.polls - 1
    assert outcome.waited_ms == round(clock.now * 1000)


def test_a_failed_snapshot_skips_that_window_this_round():
    clock = FakeClock()
    broken = FakeWindow("w-broken", 100, appear=0.0)
    desktop = TimelineDesktop(clock, [broken, main_window("w-main", pid=100, menus_at=2.0)], failing={"w-broken"})
    outcome = wait(desktop, clock)
    assert (outcome.status, outcome.window["id"]) == ("ready", "w-main")


def test_a_failed_window_list_is_an_empty_round():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=0.0)])
    real_run = desktop.run
    lists = []

    def run(*args, headed=False):
        if args[0] == "list-windows":
            lists.append(clock())
            if len(lists) == 2:
                return {"ok": False, "error": {"code": "INTERNAL", "message": "x"}}
        return real_run(*args, headed=headed)

    desktop.run = run
    outcome = wait(desktop, clock)
    assert outcome.status == "ready"
    assert outcome.polls == 2


def test_the_hint_reads_at_most_30_windows_and_stops_at_the_first_signal():
    clock = FakeClock()
    many = [FakeWindow(f"w-{index}", 1000 + index, appear=-100.0) for index in range(40)]
    desktop = TimelineDesktop(clock, many, launch_pid=100)
    outcome = wait(desktop, clock)
    assert (outcome.status, outcome.running_hint) == ("no_new_window", False)
    assert len(desktop.commands("snapshot")) == RUNNING_HINT_MAX_WINDOWS

    clock = FakeClock()
    signalled = [FakeWindow("w-a", 1, appear=-100.0), main_window("w-b", pid=2, appear=-100.0, menus_at=-100.0),
                 FakeWindow("w-c", 3, appear=-100.0)]
    desktop = TimelineDesktop(clock, signalled, launch_pid=100)
    assert wait(desktop, clock).running_hint is True
    assert [call[2] for call in desktop.commands("snapshot")] == ["w-a", "w-b"]


def test_snapshot_command_shape():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=0.0)])
    wait(desktop, clock)
    assert desktop.commands("snapshot")[0] == ("snapshot", "--window-id", "w-main", "--compact", "--timeout-ms", "8000",
                                               "--include-bounds")


def test_outcome_defaults():
    outcome = ReadyOutcome("timeout")
    assert (outcome.pid, outcome.window, outcome.waited_ms, outcome.polls, outcome.switched,
            outcome.running_hint, outcome.launch_error, outcome.tree_envelope) == (None, None, 0, 0, False, False,
                                                                                  None, None)


def test_ready_message_texts():
    assert set(READY_MESSAGES) == {"already_running", "no_new_window_hint", "no_new_window", "timeout",
                                   "launch_failed", "cancelled"}
    assert ready_message(ReadyOutcome("already_running")) == "같은 시스템이 이미 떠 있습니다. 닫고 다시 실행해 주세요"
    assert ready_message(ReadyOutcome("no_new_window", running_hint=True)) == \
        "같은 시스템이 이미 떠 있는 것 같습니다. 닫고 다시 실행해 주세요"
    assert ready_message(ReadyOutcome("no_new_window")) == "실행했지만 창이 뜨지 않았습니다"
    assert ready_message(ReadyOutcome("timeout")) == "준비 신호가 정한 시간 안에 보이지 않았습니다"
    assert ready_message(ReadyOutcome("launch_failed")) == "대상 앱을 띄우지 못했습니다"
    hinted = ready_message(ReadyOutcome("timeout", running_hint=True))
    assert hinted.startswith(READY_MESSAGES["timeout"])
    assert hinted.endswith(READY_MESSAGES["no_new_window_hint"])
    failed = ready_message(ReadyOutcome("launch_failed", running_hint=True))
    assert failed == f"{READY_MESSAGES['launch_failed']}. {READY_MESSAGES['no_new_window_hint']}"


@pytest.mark.parametrize("status", READY_STATUSES)
def test_messages_never_carry_the_signal(status):
    assert SIGNAL not in ready_message(ReadyOutcome(status, running_hint=True))


def cancel_after_polls(desktop, polls):
    """True once `polls` window lists were read after the one before the launch."""
    return lambda: len(desktop.commands("list-windows")) > polls


def test_cancelled_before_the_next_round():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=math.inf)])

    outcome = wait(desktop, clock, cancelled=cancel_after_polls(desktop, 2))

    assert (outcome.status, outcome.polls, outcome.running_hint) == ("cancelled", 2, False)
    assert outcome.waited_ms == 600
    assert len(desktop.commands("launch")) == 1
    assert ready_message(outcome) == READY_MESSAGES["cancelled"] == "사용자가 실행을 멈췄습니다"


def test_cancelled_before_the_launch_launches_nothing():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window()])

    outcome = wait(desktop, clock, cancelled=lambda: True)

    assert (outcome.status, outcome.polls) == ("cancelled", 0)
    assert desktop.calls == []


def test_ready_wins_over_a_cancel_that_never_comes():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=3.0)])

    assert wait(desktop, clock, cancelled=lambda: False).status == "ready"


# --- system profile: signals, counts, the find way (plan task 2, K4) ---

ORDER = "Order Management"
SHIPMENT = "Shipment Plan"
TOOLBAR = {"role": "toolbar", "id": "12"}
ID_SIGNAL = {"id": "56", "role": "image", "in": TOOLBAR}


def needs_identity():
    """ID signals are read by agent.identity.count (plan K1, task 1)."""
    pytest.importorskip("agent.identity")


def wait_for(desktop, clock, wanted, timeout_s=180, **options):
    return wait_until_ready(desktop, EXE, wanted, timeout_s, clock, clock.sleep, **options)


def named_window(window_id="w-main", pid=100, appear=0.0, title="Main", trees=(), **options):
    """A window of the launch whose tree changes at the times of `trees` [(start, names...)]."""
    shown = [(appear, window_tree(title))] + [(start, window_tree(title, *names)) for start, *names in trees]
    return FakeWindow(window_id, pid, title=title, appear=appear, trees=shown, **options)


def menu_tree(title, *children):
    return node(title, role="window", children=list(children))


def toolbar(native, *children):
    return node(role="toolbar", native=native, children=list(children))


def image(native):
    return node(role="image", native=native)


def non_launch_commands(desktop):
    return [call for call in desktop.calls if call[0] not in ("list-windows", "launch")]


def test_the_pause_between_rounds_is_three_tenths_of_a_second():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=1.0)])

    outcome = wait(desktop, clock)

    assert READY_POLL_S == 0.3
    assert outcome.status == "ready"
    assert clock.sleeps == [0.3] * 4
    assert (outcome.polls, outcome.waited_ms) == (5, 1200)


def test_signals_of_a_task():
    assert signals(SimpleNamespace(ready=(ID_SIGNAL, {"name": ORDER}), ready_text="x")) == (ID_SIGNAL, {"name": ORDER})
    assert signals(SimpleNamespace(ready=(), ready_text=ORDER)) == ({"name": ORDER},)
    assert signals(SimpleNamespace(ready=(), ready_text=None)) == ()
    assert signals(SimpleNamespace(ready_text=None)) == ()


def test_every_signal_must_show_in_the_same_window():
    clock = FakeClock()
    first = named_window("w-a", trees=[(0.0, ORDER), (2.0, ORDER, SHIPMENT)])
    second = named_window("w-b", trees=[(0.0, SHIPMENT)])
    desktop = TimelineDesktop(clock, [second, first])

    outcome = wait_for(desktop, clock, [{"name": ORDER}, {"name": SHIPMENT}])

    assert (outcome.status, outcome.window["id"], outcome.waited_ms) == ("ready", "w-a", 2100)


def test_a_name_signal_with_a_role_needs_that_role():
    clock = FakeClock()
    wrong = menu_tree("Main", node(ORDER, role="text"))
    right = menu_tree("Main", node(ORDER, role="text"), node(ORDER, role="button"))
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, title="Main", appear=0.0,
                                                 trees=[(0.0, wrong), (1.0, right)])])

    outcome = wait_for(desktop, clock, [{"name": ORDER, "role": "button"}])

    assert (outcome.status, outcome.waited_ms) == ("ready", 1200)


def test_an_id_signal_needs_its_role_and_container():
    needs_identity()
    clock = FakeClock()
    wrong_place = menu_tree("Main", toolbar("13", image("56")), node(role="button", native="56"))
    right_place = menu_tree("Main", toolbar("13", image("56")), toolbar("12", node(role="group", children=[image("56")])))
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, title="Main", appear=0.0,
                                                 trees=[(0.0, wrong_place), (2.0, right_place)])])

    outcome = wait_for(desktop, clock, [ID_SIGNAL])

    assert (outcome.status, outcome.waited_ms) == ("ready", 2100)
    other_clock = FakeClock()
    without_place = wait_for(TimelineDesktop(other_clock, [FakeWindow("w-main", 100, title="Main", appear=0.0,
                                                                      trees=[(0.0, wrong_place)])]),
                             other_clock, [{"id": "56", "role": "image"}], timeout_s=5)
    assert without_place.status == "ready"


def test_a_text_is_still_one_name_signal():
    outcomes = []
    for wanted in (SIGNAL, [{"name": SIGNAL}]):
        clock = FakeClock()
        desktop = TimelineDesktop(clock, [main_window(menus_at=2.0)])
        outcomes.append((wait_for(desktop, clock, wanted), desktop.calls))

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0].status == "ready"


def test_the_outcome_counts_reads_failures_and_the_longest_read():
    clock = FakeClock()
    broken = FakeWindow("w-broken", 100, appear=0.0)
    desktop = TimelineDesktop(clock, [broken, main_window("w-main", menus_at=5.0)], failing={"w-broken"},
                              snapshot_s=2.0)

    outcome = wait(desktop, clock)

    assert outcome.status == "ready"
    assert (outcome.polls, outcome.reads, outcome.failed_reads, outcome.timeouts) == (2, 4, 2, 0)
    assert (outcome.max_read_ms, outcome.candidates, outcome.read) == (2000, 2, "snapshot")


def test_the_outcome_of_a_wait_that_never_read_counts_nothing():
    clock = FakeClock()
    outcome = wait(TimelineDesktop(clock, [], launch_error={"code": "APP_NOT_FOUND", "message": "x"}), clock)

    assert (outcome.reads, outcome.failed_reads, outcome.timeouts, outcome.max_read_ms, outcome.candidates) == \
        (0, 0, 0, 0, 0)
    assert ReadyOutcome("timeout").read == "snapshot"


def test_find_mode_asks_one_find_per_signal_and_reads_the_tree_once_at_the_end():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [named_window(trees=[(0.0, ORDER, SHIPMENT)])])

    outcome = wait_for(desktop, clock, [{"name": ORDER}, {"name": SHIPMENT, "role": "menuitem"}], read="find")

    assert (outcome.status, outcome.read, outcome.reads, outcome.polls) == ("ready", "find", 3, 1)
    assert non_launch_commands(desktop) == [
        ("find", "--window-id", "w-main", "--name", ORDER, "--exact", "--first", "--timeout-ms", "3000"),
        ("find", "--window-id", "w-main", "--name", SHIPMENT, "--role", "menuitem", "--exact", "--first",
         "--timeout-ms", "3000"),
        ("snapshot", "--window-id", "w-main", "--compact", "--timeout-ms", "8000", "--include-bounds"),
    ]
    assert outcome.tree_envelope["command"] == "snapshot"
    assert has_named_element(outcome.tree_envelope["data"]["tree"], SHIPMENT)


def test_find_mode_asks_an_id_signal_by_its_id_and_role():
    needs_identity()
    clock = FakeClock()
    tree = menu_tree("Main", toolbar("12", image("56")))
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, title="Main", appear=0.0, trees=[(0.0, tree)])])

    outcome = wait_for(desktop, clock, [{"id": "56", "role": "image"}], read="find")

    assert outcome.status == "ready"
    assert desktop.commands("find") == [
        ("find", "--window-id", "w-main", "--native-id", "56", "--role", "image", "--first", "--timeout-ms", "3000")]


def test_find_mode_takes_the_role_the_cli_found_for_an_id_signal():
    """The find answer has no native id, so the role it gives is what the harness can check."""
    needs_identity()
    clock = FakeClock()
    tree = menu_tree("Main", toolbar("12", image("56")))
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, title="Main", appear=0.0, trees=[(0.0, tree)])])
    real_find = desktop._find

    def find_answering_another_role(args):
        answer = real_find(args)
        if answer.get("ok") and answer["data"]["match"] is not None:
            answer["data"]["match"]["role"] = "button"
        return answer

    desktop._find = find_answering_another_role

    assert wait_for(desktop, clock, [{"id": "56", "role": "image"}], timeout_s=2, read="find").status == "timeout"


def test_find_mode_stops_at_the_first_missing_signal_of_a_window():
    clock = FakeClock()
    partial = named_window("w-a", trees=[(0.0, SHIPMENT, "기준정보")])
    whole = named_window("w-b", trees=[(1.0, ORDER, SHIPMENT, "기준정보")])
    desktop = TimelineDesktop(clock, [partial, whole])

    outcome = wait_for(desktop, clock, [{"name": ORDER}, {"name": SHIPMENT}, {"name": "기준정보"}], read="find")

    assert (outcome.status, outcome.window["id"]) == ("ready", "w-b")
    asked_of_a = [call for call in desktop.commands("find") if call[2] == "w-a"]
    assert len(asked_of_a) == outcome.polls
    assert {call[4] for call in asked_of_a} == {ORDER}
    assert desktop.snapshots_of("w-a") == []


def test_a_find_timeout_is_not_yet_and_is_counted_apart():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=0.0)], find_timeouts={"w-main"})

    outcome = wait(desktop, clock, read="find", on_round=lambda _info: desktop.find_timeouts.clear())

    assert (outcome.status, outcome.polls) == ("ready", 2)
    assert (outcome.timeouts, outcome.failed_reads) == (1, 0)


def test_a_failed_find_is_a_failed_read():
    clock = FakeClock()
    broken = FakeWindow("w-broken", 100, appear=0.0)
    desktop = TimelineDesktop(clock, [broken, main_window(menus_at=0.0)], failing={"w-broken"})

    outcome = wait(desktop, clock, read="find")

    assert outcome.status == "ready"
    assert (outcome.timeouts, outcome.failed_reads, outcome.reads) == (0, 1, 3)


def test_a_looser_find_match_is_not_the_signal():
    for read in ("find", "snapshot"):
        clock = FakeClock()
        desktop = TimelineDesktop(clock, [named_window(trees=[(0.0, "order   MANAGEMENT")])])

        outcome = wait_for(desktop, clock, [{"name": ORDER}], timeout_s=3, read=read)

        assert outcome.status == "timeout", read
    assert desktop.commands("find") == []
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [named_window(trees=[(0.0, "order   MANAGEMENT")])])
    wait_for(desktop, clock, [{"name": ORDER}], timeout_s=3, read="find")
    assert desktop.commands("find") and desktop.commands("snapshot") == []


@pytest.mark.parametrize("title", [ORDER, " order  management "])
def test_a_window_titled_like_a_name_signal_is_judged_by_snapshot(title):
    """`--first` may give the window root itself, whose name is the title, and hide the element."""
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [named_window(title=title, trees=[(1.0, ORDER)])])

    outcome = wait_for(desktop, clock, [{"name": ORDER}], read="find")

    assert (outcome.status, outcome.read) == ("ready", "find")
    assert desktop.commands("find") == []
    assert len(desktop.snapshots_of("w-main")) == outcome.polls


def test_the_window_root_a_find_gives_back_is_never_the_signal():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [named_window(title=ORDER)])
    row = desktop.windows[0].row()
    desktop.launched = True

    measured = measure(desktop, row, [{"name": ORDER}], clock)

    assert measured["find"]["found"] == [False]
    assert measured["snapshot"]["found"] == [False]


def test_find_mode_checks_the_container_on_the_final_tree():
    needs_identity()
    clock = FakeClock()
    elsewhere = menu_tree("Main", toolbar("13", image("56")))
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, title="Main", appear=0.0, trees=[(0.0, elsewhere)])])

    outcome = wait_for(desktop, clock, [ID_SIGNAL], timeout_s=2, read="find")

    assert outcome.status == "timeout"
    assert desktop.commands("find") and desktop.snapshots_of("w-main")
    assert outcome.tree_envelope is None


def test_find_mode_needs_the_container_element_in_the_final_tree():
    """Plan decision 9: an `in` signal whose element is not in the recorded tree is not ready."""
    needs_identity()
    clock = FakeClock()
    tree = menu_tree("Main", toolbar("12", image("56")))
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, title="Main", appear=0.0, trees=[(0.0, tree)])])
    real_snapshot = desktop._snapshot

    def shallow_snapshot(args):
        answer = real_snapshot(args)
        if answer.get("ok"):
            answer["data"]["tree"] = menu_tree("Main", toolbar("12"))
        return answer

    desktop._snapshot = shallow_snapshot

    assert wait_for(desktop, clock, [ID_SIGNAL], timeout_s=2, read="find").status == "timeout"


def test_a_failed_final_read_leaves_the_round_unready():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=0.0)],
                              snapshot_failing=lambda fake, _window: len(fake.commands("snapshot")) == 1)

    outcome = wait(desktop, clock, read="find")

    assert (outcome.status, outcome.polls, outcome.failed_reads) == ("ready", 2, 1)
    assert outcome.tree_envelope["ok"] is True


def test_on_round_gets_numbers_and_no_text():
    clock = FakeClock()
    broken = FakeWindow("w-broken", 100, title="Broken", appear=0.0)
    desktop = TimelineDesktop(clock, [broken, named_window(trees=[(1.5, ORDER, SHIPMENT)])], failing={"w-broken"},
                              list_s=0.1, find_s=0.2)
    rounds = []

    wait_for(desktop, clock, [{"name": ORDER}, {"name": SHIPMENT, "role": "menuitem"}], read="find",
             on_round=rounds.append)

    assert [info["round"] for info in rounds] == [1, 2, 3]
    assert all(set(info) == {"round", "list_ms", "candidates", "reads"} for info in rounds)
    assert rounds[0]["list_ms"] == 100 and rounds[0]["candidates"] == 2
    assert rounds[0]["reads"] == [
        {"window": 1, "signal": 1, "found": False, "ms": 200, "code": "WINDOW_NOT_FOUND"},
        {"window": 2, "signal": 1, "found": False, "ms": 200, "code": None},
    ]
    assert rounds[-1]["reads"][1:] == [
        {"window": 2, "signal": 1, "found": True, "ms": 200, "code": None},
        {"window": 2, "signal": 2, "found": True, "ms": 200, "code": None},
        {"window": 2, "signal": None, "found": True, "ms": 0, "code": None},
    ]
    text = json.dumps(rounds, ensure_ascii=False)
    for secret in (ORDER, SHIPMENT, "menuitem", "w-main", "w-broken", "Main", "Broken"):
        assert secret not in text
    for info in rounds:
        for read in info["reads"]:
            assert all(value is None or type(value) in (int, bool) for key, value in read.items() if key != "code")


def test_on_round_of_the_snapshot_way_has_one_read_per_window():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [main_window(menus_at=0.0)], snapshot_s=1.0)
    rounds = []

    outcome = wait(desktop, clock, on_round=rounds.append)

    assert rounds == [{"round": 1, "list_ms": 0, "candidates": 1,
                       "reads": [{"window": 1, "signal": None, "found": True, "ms": 1000, "code": None}]}]
    assert outcome.max_read_ms == 1000


def test_measure_times_both_ways_on_one_window():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [named_window(trees=[(0.0, ORDER, SHIPMENT)])], snapshot_s=2.0, find_s=0.5)
    desktop.launched = True
    row = desktop.windows[0].row()

    both = measure(desktop, row, [{"name": ORDER}, {"name": SHIPMENT}], clock)
    one_missing = measure(desktop, row, [{"name": ORDER}, {"name": "기준정보"}, {"name": SHIPMENT}], clock)

    assert both == {"snapshot": {"ms": 2000, "ok": True, "found": [True, True]},
                    "find": {"ms": 1000, "ok": True, "timeouts": 0, "found": [True, True]}}
    assert one_missing["snapshot"]["found"] == [True, False, True]
    assert one_missing["find"] == {"ms": 1500, "ok": True, "timeouts": 0, "found": [True, False, True]}
    assert [call[0] for call in desktop.calls] == ["snapshot", "find", "find", "snapshot", "find", "find", "find"]


def test_measure_counts_a_find_timeout_and_a_failed_snapshot():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [named_window(trees=[(0.0, ORDER)])], failing=(), find_timeouts={"w-main"},
                              snapshot_failing=lambda _fake, _window: True)
    desktop.launched = True

    measured = measure(desktop, desktop.windows[0].row(), [{"name": ORDER}], clock)

    assert measured == {"snapshot": {"ms": 0, "ok": False, "found": [False]},
                        "find": {"ms": 0, "ok": True, "timeouts": 1, "found": [False]}}


def measured_on(tree, wanted, **options):
    clock = FakeClock()
    window = FakeWindow("w-main", 100, title="Main", appear=0.0, trees=[(0.0, tree)])
    desktop = TimelineDesktop(clock, [window], **options)
    desktop.launched = True
    return desktop, measure(desktop, window.row(), wanted, clock)


def test_measure_finds_an_id_signal_by_find_only_in_its_area():
    needs_identity()
    elsewhere = menu_tree("Main", node(ORDER, role="button"), toolbar("13", image("56")))

    desktop, measured = measured_on(elsewhere, [{"name": ORDER}, ID_SIGNAL])

    assert measured["find"]["found"] == [True, False], "find answers image 56, but it is not in toolbar 12"
    assert measured["snapshot"]["found"] == [True, False]
    assert recommended_read(measured) is None
    assert [call[0] for call in desktop.calls] == ["snapshot", "find", "find"], "no read is added"


def test_measure_finds_an_id_signal_by_find_in_its_area_as_before():
    needs_identity()
    placed = menu_tree("Main", node(ORDER, role="button"), toolbar("12", image("56")))

    _, measured = measured_on(placed, [{"name": ORDER}, ID_SIGNAL], snapshot_s=2.0, find_s=0.5)

    assert measured == {"snapshot": {"ms": 2000, "ok": True, "found": [True, True]},
                        "find": {"ms": 1000, "ok": True, "timeouts": 0, "found": [True, True]}}
    assert recommended_read(measured) == "find"


def test_measure_without_a_tree_counts_no_id_signal_with_an_area_found_by_find():
    needs_identity()
    placed = menu_tree("Main", node(ORDER, role="button"), toolbar("12", image("56")))
    no_area = {"id": "56", "role": "image"}

    _, measured = measured_on(placed, [{"name": ORDER}, no_area, ID_SIGNAL],
                              snapshot_failing=lambda _fake, _window: True)

    assert measured["snapshot"] == {"ms": 0, "ok": False, "found": [False, False, False]}
    assert measured["find"] == {"ms": 0, "ok": True, "timeouts": 0, "found": [True, True, False]}, \
        "a name signal and an id signal without an area stay find's own answer"


def test_recommended_read_needs_both_ways_to_find_every_signal():
    def measured(snapshot_ms, find_ms, snapshot_ok=True, find_ok=True, snapshot_found=(True,), find_found=(True,)):
        return {"snapshot": {"ms": snapshot_ms, "ok": snapshot_ok, "found": list(snapshot_found)},
                "find": {"ms": find_ms, "ok": find_ok, "timeouts": 0, "found": list(find_found)}}

    assert recommended_read(measured(4200, 300)) == "find"
    assert recommended_read(measured(300, 4200)) == "snapshot"
    assert recommended_read(measured(500, 500)) == "snapshot"
    assert recommended_read(measured(4200, 300, find_found=(True, False), snapshot_found=(True, True))) is None
    assert recommended_read(measured(4200, 300, snapshot_found=(False,))) is None
    assert recommended_read(measured(4200, 300, find_ok=False)) is None
    assert recommended_read(measured(4200, 300, snapshot_ok=False)) is None
    assert recommended_read(measured(4200, 300, find_found=(), snapshot_found=())) is None


def test_the_running_hint_reads_every_signal():
    wanted = [{"name": ORDER}, {"name": SHIPMENT}]
    for names, expected in (((ORDER, SHIPMENT), True), ((ORDER,), False)):
        clock = FakeClock()
        old = named_window("w-old", pid=50, appear=-100.0, trees=[(-100.0, *names)])
        desktop = TimelineDesktop(clock, [old], launch_error={"code": "TIMEOUT", "message": "no window"})

        outcome = wait_for(desktop, clock, wanted, read="find")

        assert (outcome.status, outcome.running_hint) == ("launch_failed", expected)
        assert desktop.commands("find") == []
        assert outcome.reads == 0


def test_candidates_are_the_launched_process_and_new_windows():
    old = {"id": "w-old", "pid": 50, "process_instance": "proc:50"}
    mine_before = {"id": "w-mine", "pid": 100, "process_instance": "proc:100"}
    fresh = {"id": "w-new", "pid": 70, "process_instance": "proc:70"}
    before_keys = {("w-old", 50, "proc:50"), ("w-mine", 100, "proc:100")}

    assert candidates([old, mine_before, fresh], (100, "proc:100"), before_keys) == [mine_before, fresh]
