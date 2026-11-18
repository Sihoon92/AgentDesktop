"""A stop signal ends a run at a safe place: before a model call, before each tool, while a
question or a proposal waits, and during the ready wait (viewer stop plan, task 1, C1)."""

import threading
import time

from test_loop import (
    GIVE_UP,
    OBSERVE,
    OBSERVE_SCENE,
    PLAN,
    ScriptedModel,
    call,
    free_task,
    scene_toolbox,
    settings_call,
    setup,
    stuck_question,
)
from test_loop_ready import splash_then_main, task, toolbox_for

from agent.approval import ApprovalGate
from agent.ask import AskGate
from agent.loop import USER_STOP_REASON, run_agent
from agent.ready import READY_MESSAGES
from tests.ready_support import FakeClock, FakeWindow, TimelineDesktop, window_tree

QUICK_S = 5.0


def stopped(event=None):
    event = event or threading.Event()
    event.set()
    return event


def setting_stop(stop, turn):
    """A model turn that sets the stop signal while it answers, as a stop pressed during a call."""

    def answer(items):
        stop.set()
        return turn(items) if callable(turn) else turn

    return answer


def test_user_stop_reason_is_the_ready_cancelled_message():
    assert USER_STOP_REASON == "사용자가 실행을 멈췄습니다"
    assert READY_MESSAGES["cancelled"] == USER_STOP_REASON


def test_stop_before_a_model_call_ends_without_calling_it():
    the_task, toolbox, cli = setup()
    model = ScriptedModel([[call("launch_target", {})], GIVE_UP])

    result = run_agent(model, toolbox, the_task, stop=stopped())

    assert (result.status, result.reason, result.steps) == ("stopped", USER_STOP_REASON, 0)
    assert model.inputs == []
    assert cli.command_names() == []


def test_stop_set_during_a_model_call_runs_no_tool_from_its_answer():
    the_task, toolbox, cli = setup()
    stop = threading.Event()
    model = ScriptedModel([
        [call("launch_target", {})],
        PLAN,
        [call("observe", OBSERVE)],
        setting_stop(stop, settings_call),
        GIVE_UP,
    ])

    result = run_agent(model, toolbox, the_task, stop=stop)

    assert (result.status, result.reason, result.steps) == ("stopped", USER_STOP_REASON, 4)
    assert len(model.inputs) == 4
    assert "check" not in cli.command_names()


def test_stop_between_two_tools_of_one_answer_runs_neither_after_it():
    the_task, toolbox, cli = setup()
    stop = threading.Event()
    log = []

    def stop_after_launch(event):
        log.append(event)
        if event.get("type") == "tool" and event.get("tool") == "launch_target":
            stop.set()

    model = ScriptedModel([[call("launch_target", {}), call("observe", OBSERVE)], GIVE_UP])

    result = run_agent(model, toolbox, the_task, log=stop_after_launch, stop=stop)

    assert (result.status, result.reason) == ("stopped", USER_STOP_REASON)
    assert [event["tool"] for event in log if event.get("type") == "tool"] == ["launch_target"]
    assert "snapshot" not in cli.command_names()



def test_stop_during_the_last_call_at_the_ceiling_skips_its_finish():
    the_task, toolbox, _ = setup(max_steps=1)
    stop = threading.Event()
    model = ScriptedModel([[call("launch_target", {})], setting_stop(stop, GIVE_UP)])

    result = run_agent(model, toolbox, the_task, stop=stop)

    assert (result.status, result.reason, result.steps) == ("stopped", USER_STOP_REASON, 2)


def finished_within(seconds, work):
    """Runs `work` in a daemon thread and returns what it returned, failing (not hanging) when it
    is still running after `seconds`."""
    done = []
    thread = threading.Thread(target=lambda: done.append(work()), daemon=True)
    thread.start()
    thread.join(seconds)
    assert done, f"still running after {seconds} s"
    return done[0]


def stop_when(condition, stop, close):
    """Waits in another thread until `condition()` and then sets the stop and closes the wait,
    as the viewer does. Polls every 10 ms; gives up after QUICK_S."""

    def watch():
        deadline = time.monotonic() + QUICK_S
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
        close()

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def test_stop_while_a_question_waits_ends_at_once_with_the_user_reason(tmp_path):
    gate = AskGate(timeout_s=1800)
    stop = threading.Event()
    model = ScriptedModel([[call("launch_target", {})], [call("observe", OBSERVE_SCENE)],
                           [call("ask", stuck_question())], GIVE_UP])
    watcher = stop_when(lambda: gate.waiting("q1"), stop, gate.close)

    toolbox = scene_toolbox(tmp_path, asker=gate)
    result = finished_within(QUICK_S * 2, lambda: run_agent(model, toolbox, free_task(), stop=stop))
    watcher.join(QUICK_S)

    assert (result.status, result.reason) == ("stopped", USER_STOP_REASON)
    assert len(model.inputs) == 3


def test_stop_while_an_approval_waits_ends_with_the_user_reason(tmp_path):
    gate = ApprovalGate(timeout_s=600)
    stop = threading.Event()
    proposed = threading.Event()

    def log(event):
        if event.get("type") == "proposal":
            proposed.set()

    model = ScriptedModel([[call("launch_target", {})], GIVE_UP])
    watcher = stop_when(proposed.is_set, stop, gate.close)

    toolbox = scene_toolbox(tmp_path)
    result = finished_within(QUICK_S * 2,
                             lambda: run_agent(model, toolbox, free_task(), log=log, gate=gate, stop=stop))
    watcher.join(QUICK_S)

    assert (result.status, result.reason) == ("stopped", USER_STOP_REASON)
    assert len(model.inputs) == 1


def never_ready(clock):
    main = FakeWindow("w-main", 100, title="Main", appear=0.0, trees=[(0.0, window_tree("Main"))])
    return TimelineDesktop(clock, [main], launch_pid=100)


def test_stop_during_the_ready_wait_is_cancelled():
    clock = FakeClock()
    desktop = never_ready(clock)
    stop = threading.Event()

    def sleep(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) >= 2:
            stop.set()

    toolbox, observer = toolbox_for(desktop, clock)
    toolbox.sleep = sleep
    model = ScriptedModel([GIVE_UP])

    result = run_agent(model, toolbox, task(ready_timeout_s=60), stop=stop)

    assert (result.status, result.reason, result.steps) == ("stopped", USER_STOP_REASON, 0)
    assert model.inputs == []
    end = observer.ready_events()[-1]
    assert (end["phase"], end["status"], end["polls"]) == ("end", "cancelled", 2)
    assert len(clock.sleeps) == 2


def test_stop_before_the_ready_wait_launches_nothing():
    clock = FakeClock()
    desktop = never_ready(clock)
    toolbox, observer = toolbox_for(desktop, clock)
    model = ScriptedModel([GIVE_UP])

    result = run_agent(model, toolbox, task(), stop=stopped())

    assert (result.status, result.reason) == ("stopped", USER_STOP_REASON)
    assert desktop.commands("launch") == []
    assert observer.ready_events()[-1]["status"] == "cancelled"


def test_a_restart_waits_for_ready_with_the_same_cancel():
    clock = FakeClock()
    cancel = []
    desktop = splash_then_main(clock)
    toolbox, observer = toolbox_for(desktop, clock)
    assert toolbox.launch_ready(cancelled=lambda: bool(cancel))["ok"] is True
    cancel.append(True)

    result = toolbox.call("restart_target", {})

    assert (result["ok"], result["code"], result["message"]) == (False, "READY_CANCELLED", USER_STOP_REASON)
    assert len(desktop.commands("launch")) == 1
    assert observer.ready_events()[-1]["status"] == "cancelled"


def run_events(stop):
    the_task, toolbox, cli = setup()
    log = []
    model = ScriptedModel([[call("launch_target", {})], PLAN, [call("observe", OBSERVE)], settings_call, GIVE_UP])
    result = run_agent(model, toolbox, the_task, log=log.append, stop=stop)
    return result, log, cli.command_names()


def test_without_stop_the_run_is_unchanged():
    assert run_events(threading.Event()) == run_events(None)
    result, _, commands = run_events(None)
    assert result.status == "gave_up"
    assert "check" in commands


def test_stopping_never_closes_the_target():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, _ = toolbox_for(desktop, clock)
    stop = threading.Event()
    model = ScriptedModel([setting_stop(stop, [call("restart_target", {})]), GIVE_UP])

    result = run_agent(model, toolbox, task(), stop=stop)

    assert (result.status, result.reason) == ("stopped", USER_STOP_REASON)
    assert desktop.closed == []
    assert len(desktop.commands("launch")) == 1


def test_stop_at_a_stage_limit_question_keeps_the_user_reason(tmp_path):
    gate = AskGate(timeout_s=1800)
    stop = threading.Event()
    the_task = free_task(stage_call_limit=1)
    model = ScriptedModel([[call("launch_target", {})], PLAN, [call("list_windows", {})], GIVE_UP])
    watcher = stop_when(lambda: any(gate.waiting(f"q{n}") for n in range(1, 4)), stop, gate.close)

    toolbox = scene_toolbox(tmp_path, asker=gate, task=the_task)
    result = finished_within(QUICK_S * 2, lambda: run_agent(model, toolbox, the_task, stop=stop))
    watcher.join(QUICK_S)

    assert (result.status, result.reason) == ("stopped", USER_STOP_REASON)
