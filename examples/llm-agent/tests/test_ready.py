"""Launching a system and waiting until a window shows the ready signal (agent/ready.py)."""

import math

import pytest

from agent.ready import (
    NO_WINDOW_S,
    READY_MESSAGES,
    READY_POLL_S,
    READY_STATUSES,
    RUNNING_HINT_MAX_WINDOWS,
    ReadyOutcome,
    has_named_element,
    ready_message,
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
    assert READY_POLL_S == 1.0
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
    assert outcome.polls == 4
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
    assert outcome.waited_ms == 5000
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


def test_polls_wait_one_second_after_each_read():
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
    assert outcome.waited_ms == 2000
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
