"""The ready wait inside the Toolbox, the run loop and the baseline (plan task 3, C5)."""

import json

from test_loop import GIVE_UP, RecordingGate, ScriptedModel, call, first_user_text

from agent.baseline import run_baseline
from agent.loop import READY_BRIEFING, run_agent
from agent.ready import READY_MESSAGES
from agent.task import Task
from agent.tools import Toolbox
from tests.ready_support import SIGNAL, FakeClock, FakeWindow, TimelineDesktop, window_tree

EXE = r"C:\Apps\System\Main.exe"
MENUS = ("생산준비", SIGNAL, "기준정보")
END_KEYS = {"type", "phase", "status", "waited_ms", "polls", "switched", "running_hint"}


class RecordingObserver:
    def __init__(self):
        self.events = []
        self.observations = []
        self.actions = []

    def on_observation(self, window, envelope, text, source=None, wait=None):
        self.observations.append({"window": window, "envelope": envelope, "text": text, "source": source})

    def on_action(self, tool, arguments, target, result):
        self.actions.append(tool)

    def on_event(self, event):
        self.events.append(event)

    def ready_events(self):
        return [event for event in self.events if event.get("type") == "ready"]


def task(ready=True, **overrides):
    if ready:
        overrides.setdefault("ready_text", SIGNAL)
        overrides.setdefault("ready_timeout_s", 60)
    return Task("첫 화면에서 시작한다", EXE, 3, [], **overrides)


def splash_then_main(clock):
    """A splash in the launched pid 100 that closes at 2 s, then the main screen in pid 200
    whose menus fill in at 4 s."""
    splash = FakeWindow("w-splash", 100, title=f"{SIGNAL}시스템", appear=0.0, gone=2.0,
                        trees=[(0.0, window_tree(f"{SIGNAL}시스템", f"{SIGNAL} 시작 중"))])
    main = FakeWindow("w-main", 200, title="Main", appear=2.0,
                      trees=[(2.0, window_tree("Main")), (4.0, window_tree("Main", *MENUS))])
    return TimelineDesktop(clock, [splash, main], launch_pid=100)


def toolbox_for(desktop, clock, the_task=None, confirm=lambda _prompt: True):
    observer = RecordingObserver()
    toolbox = Toolbox(desktop, the_task or task(), confirm=confirm, sleep=clock.sleep, clock=clock,
                      observer=observer)
    return toolbox, observer


def already_running(clock):
    running = FakeWindow("w-old", 100, title="Main", appear=-50.0, trees=[(-50.0, window_tree("Main", *MENUS))])
    return TimelineDesktop(clock, [running], launch_pid=100)


def test_launch_ready_moves_the_target_to_the_ready_window():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, _ = toolbox_for(desktop, clock)
    result = toolbox.launch_ready()
    assert result["ok"] is True
    assert result["pid"] == 200
    assert result["ready"] == {"waited_ms": 4000, "switched": True}
    assert [window["id"] for window in result["windows"]] == ["w-main"]
    assert [window["id"] for window in toolbox.call("list_windows", {})["windows"]] == ["w-main"]


def test_launch_ready_records_events_and_one_ready_observation():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, observer = toolbox_for(desktop, clock)
    toolbox.launch_ready()
    start, end = observer.ready_events()
    assert start == {"type": "ready", "phase": "start"}
    assert set(end) == END_KEYS
    assert end == {"type": "ready", "phase": "end", "status": "ready", "waited_ms": 4000, "polls": 5,
                   "switched": True, "running_hint": False}
    assert [seen["source"] for seen in observer.observations] == ["ready"]
    seen = observer.observations[0]
    assert seen["window"]["id"] == "w-main"
    assert SIGNAL in seen["text"]
    assert len(desktop.snapshots_of("w-main")) == 3, "the judging read is the observation, not read again"
    assert all("--include-bounds" in snapshot for snapshot in desktop.snapshots_of("w-main"))
    assert SIGNAL not in json.dumps(observer.ready_events(), ensure_ascii=False)


def test_the_ready_window_is_the_current_window():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, _ = toolbox_for(desktop, clock)
    toolbox.launch_ready()
    observed = toolbox.call("observe", {"window_id": None, "root_ref": None, "surface": None,
                                        "include_bounds": False})
    assert observed["window_id"] == "w-main"


def test_launch_ready_failure_keeps_no_target():
    clock = FakeClock()
    desktop = already_running(clock)
    toolbox, observer = toolbox_for(desktop, clock)
    result = toolbox.launch_ready()
    assert result == {"ok": False, "code": "READY_ALREADY_RUNNING", "message": READY_MESSAGES["already_running"]}
    assert observer.observations == []
    assert observer.ready_events()[-1]["status"] == "already_running"
    assert "refused" in toolbox.call("list_windows", {})


def test_failure_codes_follow_the_status():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, appear=0.0)], launch_pid=100)
    toolbox, _ = toolbox_for(desktop, clock, task(ready_timeout_s=5))
    result = toolbox.launch_ready()
    assert (result["ok"], result["code"], result["message"]) == (False, "READY_TIMEOUT", READY_MESSAGES["timeout"])


def test_launch_target_after_ready_does_not_launch_again():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, _ = toolbox_for(desktop, clock)
    toolbox.launch_ready()
    result = toolbox.call("launch_target", {})
    assert result["ok"] is True
    assert result["pid"] == 200
    assert [window["id"] for window in result["windows"]] == ["w-main"]
    assert len(desktop.commands("launch")) == 1


def test_launch_target_without_ready_still_launches():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, observer = toolbox_for(desktop, clock, task(ready=False))
    result = toolbox.call("launch_target", {})
    assert (result["ok"], result["pid"]) == (True, 100)
    assert len(desktop.commands("launch")) == 1
    assert observer.ready_events() == []


def test_restart_target_waits_for_ready_again():
    clock = FakeClock()

    def second_launch(desktop, now):
        if desktop.commands("launch")[1:]:
            desktop.launch_pid, desktop.launch_instance = 300, "proc:300"
            desktop.windows.append(FakeWindow("w-again", 400, title="Main", appear=now + 1.0,
                                              trees=[(now + 1.0, window_tree("Main", *MENUS))]))

    desktop = splash_then_main(clock)
    desktop.on_launch = second_launch
    toolbox, observer = toolbox_for(desktop, clock)
    toolbox.launch_ready()
    result = toolbox.call("restart_target", {})
    assert desktop.closed == [200]
    assert result["ok"] is True
    assert result["pid"] == 400
    assert result["ready"]["switched"] is True
    assert len(desktop.commands("launch")) == 2
    assert [event["phase"] for event in observer.ready_events()] == ["start", "end", "start", "end"]
    assert [window["id"] for window in toolbox.call("list_windows", {})["windows"]] == ["w-again"]


def test_restart_target_without_ready_launches_as_before():
    clock = FakeClock()
    desktop = TimelineDesktop(clock, [FakeWindow("w-main", 100, appear=0.0)], launch_pid=100)
    toolbox, observer = toolbox_for(desktop, clock, task(ready=False))
    toolbox.call("launch_target", {})
    desktop.windows.append(FakeWindow("w-next", 100, appear=0.0))
    toolbox.call("restart_target", {})
    assert len(desktop.commands("launch")) == 2
    assert observer.ready_events() == []


def test_run_waits_before_the_first_model_call():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, _ = toolbox_for(desktop, clock)
    launches_at_first_call = []

    def first_turn(items):
        launches_at_first_call.append(len(desktop.commands("launch")))
        return GIVE_UP

    model = ScriptedModel([first_turn])
    result = run_agent(model, toolbox, task())
    assert launches_at_first_call == [1]
    text = first_user_text(model)
    assert text.startswith("첫 화면에서 시작한다")
    assert READY_BRIEFING in text
    assert '"w-main"' in text
    assert SIGNAL not in text.split(READY_BRIEFING, 1)[0]
    assert len(model.inputs) == 1
    assert result.tokens == model.tokens


def test_run_stops_without_calling_the_model_when_not_ready():
    clock = FakeClock()
    desktop = already_running(clock)
    toolbox, _ = toolbox_for(desktop, clock)
    model = ScriptedModel([GIVE_UP])
    result = run_agent(model, toolbox, task())
    assert result.status == "stopped"
    assert result.reason == READY_MESSAGES["already_running"]
    assert (result.steps, result.tokens) == (0, 0)
    assert model.inputs == []
    assert result.stats is not None
    assert toolbox.stage_records == []


def test_run_with_a_gate_never_asks_for_the_ready_wait():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, _ = toolbox_for(desktop, clock)
    gate = RecordingGate()
    run_agent(ScriptedModel([GIVE_UP]), toolbox, task(), gate=gate)
    assert gate.proposals == []

    clock = FakeClock()
    toolbox, _ = toolbox_for(already_running(clock), clock)
    gate = RecordingGate()
    run_agent(ScriptedModel([GIVE_UP]), toolbox, task(), gate=gate)
    assert gate.proposals == []


def test_run_without_ready_text_is_unchanged():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox, observer = toolbox_for(desktop, clock, task(ready=False))
    launches_at_first_call = []

    def first_turn(items):
        launches_at_first_call.append(len(desktop.commands("launch")))
        return [call("launch_target", {})]

    model = ScriptedModel([first_turn, GIVE_UP])
    run_agent(model, toolbox, task(ready=False))
    assert launches_at_first_call == [0]
    assert first_user_text(model) == "첫 화면에서 시작한다"
    assert len(desktop.commands("launch")) == 1
    assert observer.ready_events() == []


def test_baseline_waits_for_ready_when_the_task_has_a_signal():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    the_task = task(baseline_target={"role": "menuitem", "name": SIGNAL})
    toolbox, observer = toolbox_for(desktop, clock, the_task)
    result = run_baseline(toolbox, the_task, timeout_s=1)
    assert len(desktop.commands("launch")) == 1
    assert [event["phase"] for event in observer.ready_events()] == ["start", "end"]
    assert result.steps >= 2, "it went past the launch to the element wait"
    assert observer.observations[0]["source"] == "ready"


def test_baseline_stops_with_the_ready_reason():
    clock = FakeClock()
    the_task = task(baseline_target={"role": "menuitem", "name": SIGNAL})
    toolbox, _ = toolbox_for(already_running(clock), clock, the_task)
    result = run_baseline(toolbox, the_task)
    assert (result.status, result.steps, result.reason) == ("stopped", 1, READY_MESSAGES["already_running"])
