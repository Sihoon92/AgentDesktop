import functools
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fake_cli import FakeCli
from test_loop import ScriptedModel, call
from test_tools import OBSERVE as SCENE_OBSERVE
from test_tools import SCENE

from agent.approval import ApprovalGate
from agent.ask import Answer, AskGate
from agent.desktop import DesktopClient
from agent.knowledge import DEFAULT_ROOT, KnowledgeStore
from agent.loop import RunResult
from agent.registration import register_after_run
from agent.tools import Toolbox
from viewer import run_manager as run_manager_module
from viewer.run_manager import InvalidRun, RunBusy, RunManager


class GatedModel:
    """Finishes once `release` is set, so a test can look at a run mid-flight. It reports no success:
    a viewer run that claims success without conditions asks the person (see the asking tests)."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    def respond(self, instructions, items, tools):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test never released the model")
        output = [call("finish", {"success": False, "evidence": "done"})]
        return SimpleNamespace(output=output, usage=SimpleNamespace(total_tokens=10))


class BrokenModel:
    def respond(self, instructions, items, tools):
        raise RuntimeError("model service is down")


def make_manager(tmp_path, model, cli=None, **options):
    cli = cli or FakeCli()
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"")
    manager = RunManager(
        tmp_path / "runs",
        "ad.exe",
        str(exe),
        lambda _task: model,
        lambda desktop, task, confirm, observer, asker, knowledge, captures_dir: Toolbox(
            desktop, task, confirm=confirm, sleep=lambda _s: None, observer=observer, asker=asker,
            knowledge=knowledge, captures_dir=captures_dir,
        ),
        desktop_factory=lambda: DesktopClient("ad.exe", "run1", runner=cli),
        **options,
    )
    return manager, exe


class WatchedGate(ApprovalGate):
    """An ApprovalGate that tells the test when a proposal is waiting."""

    def __init__(self, timeout_s=5):
        super().__init__(timeout_s=timeout_s)
        self.waiting = threading.Event()

    def review(self, proposal):
        self.waiting.set()
        return super().review(proposal)


def launch_then_finish():
    return ScriptedModel(
        [
            [call("launch_target", {"intent": "앱을 연다"})],
            [call("finish", {"success": False, "evidence": "done"})],
        ]
    )


def run_events(tmp_path, run_id):
    lines = (tmp_path / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def run_json(tmp_path, run_id):
    return json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))


def test_second_start_while_running_is_rejected(tmp_path):
    model = GatedModel()
    manager, _ = make_manager(tmp_path, model)

    first = manager.start("설정을 열어줘", None, 3)
    assert manager.active() == first
    with pytest.raises(RunBusy):
        manager.start("또 시작", None, 3)

    model.release.set()
    manager.join(timeout=5)
    assert manager.active() is None
    second = manager.start("다시 시작", None, 3)
    manager.join(timeout=5)
    assert second != first


def test_run_writes_running_then_final_status(tmp_path):
    model = GatedModel()
    manager, exe = make_manager(tmp_path, model)

    run_id = manager.start("설정을 열어줘", None, 2)
    assert model.started.wait(timeout=5)
    running = run_json(tmp_path, run_id)

    model.release.set()
    manager.join(timeout=5)
    final = run_json(tmp_path, run_id)

    assert running["status"] == "running"
    assert running["goal"] == "설정을 열어줘"
    assert running["exe"] == str(exe)
    assert running["permission_level"] == 2
    assert final["status"] == "gave_up"
    lines = (tmp_path / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["type"] == "finish"


def test_a_crashing_model_ends_the_run_as_stopped_and_frees_the_slot(tmp_path):
    manager, _ = make_manager(tmp_path, BrokenModel())

    run_id = manager.start("설정을 열어줘", None, 3)
    manager.join(timeout=5)

    final = run_json(tmp_path, run_id)
    assert final["status"] == "stopped"
    assert "model service is down" in final["reason"]
    assert manager.active() is None


def test_restart_target_is_refused_because_no_terminal_can_confirm(tmp_path):
    cli = FakeCli()
    model = ScriptedModel(
        [
            [call("launch_target", {"intent": "앱을 연다"})],
            [call("restart_target", {"intent": "앱을 다시 켠다"})],
            [call("finish", {"success": False, "evidence": "restart refused"})],
        ]
    )
    manager, _ = make_manager(tmp_path, model, cli)

    run_id = manager.start("앱을 다시 켜줘", None, 3)
    manager.join(timeout=5)

    lines = (tmp_path / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    restart = next(e for e in map(json.loads, lines) if e.get("tool") == "restart_target")
    assert restart["result"]["ok"] is False
    assert "declined" in restart["result"]["refused"]
    assert not any(argv[0] == "taskkill" for argv in cli.calls)


def test_invalid_requests_are_rejected_before_anything_starts(tmp_path):
    manager, _ = make_manager(tmp_path, GatedModel())

    for goal, exe, level in [
        ("", None, 3),
        ("   ", None, 3),
        ("열어줘", "relative\\App.exe", 3),
        ("열어줘", str(tmp_path / "missing.exe"), 3),
        ("열어줘", None, 4),
        ("열어줘", None, "3"),
    ]:
        with pytest.raises(InvalidRun):
            manager.start(goal, exe, level)

    assert manager.active() is None
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_approved_proposal_reaches_the_app_once(tmp_path):
    cli = FakeCli()
    gate = WatchedGate()
    manager, _ = make_manager(tmp_path, launch_then_finish(), cli, gate_factory=lambda: gate)

    run_id = manager.start("앱을 열어줘", None, 3, approve=True)
    assert gate.waiting.wait(timeout=5)
    assert "launch" not in cli.command_names()
    assert manager.decide(run_id, "p1", True, "좋아요") is True
    manager.join(timeout=5)

    events = run_events(tmp_path, run_id)
    kinds = [event["type"] for event in events]
    assert kinds.index("proposal") < kinds.index("decision") < kinds.index("action")
    assert next(e for e in events if e["type"] == "decision") == {
        "type": "decision", "id": "p1", "approved": True, "comment": "좋아요",
    }
    assert cli.command_names().count("launch") == 1
    assert run_json(tmp_path, run_id)["status"] == "gave_up"


def test_rejected_proposal_never_reaches_the_app(tmp_path):
    cli = FakeCli()
    gate = WatchedGate()
    manager, _ = make_manager(tmp_path, launch_then_finish(), cli, gate_factory=lambda: gate)

    run_id = manager.start("앱을 열어줘", None, 3, approve=True)
    assert gate.waiting.wait(timeout=5)
    assert manager.decide(run_id, "p1", False, "지금은 안 돼요") is True
    manager.join(timeout=5)

    events = run_events(tmp_path, run_id)
    assert "launch" not in cli.command_names()
    assert not any(event["type"] == "action" for event in events)
    decision = next(e for e in events if e["type"] == "decision")
    assert (decision["approved"], decision["comment"]) == (False, "지금은 안 돼요")


def test_unanswered_proposal_stops_the_run(tmp_path):
    cli = FakeCli()
    manager, _ = make_manager(
        tmp_path, launch_then_finish(), cli, gate_factory=lambda: ApprovalGate(timeout_s=0.2)
    )

    run_id = manager.start("앱을 열어줘", None, 3, approve=True)
    manager.join(timeout=5)

    assert run_json(tmp_path, run_id)["status"] == "stopped"
    assert "launch" not in cli.command_names()
    assert manager.active() is None


def test_without_approval_actions_run_unasked(tmp_path):
    cli = FakeCli()
    manager, _ = make_manager(tmp_path, launch_then_finish(), cli)

    run_id = manager.start("앱을 열어줘", None, 3)
    manager.join(timeout=5)

    assert not any(event["type"] == "proposal" for event in run_events(tmp_path, run_id))
    assert cli.command_names().count("launch") == 1


def test_decisions_for_other_runs_or_unknown_proposals_are_refused(tmp_path):
    gate = WatchedGate()
    manager, _ = make_manager(tmp_path, launch_then_finish(), gate_factory=lambda: gate)
    assert manager.decide("20261107-000000", "p1", True, "") is False

    run_id = manager.start("앱을 열어줘", None, 3, approve=True)
    assert gate.waiting.wait(timeout=5)
    assert manager.decide("20261107-000000", "p1", True, "") is False
    assert manager.decide(run_id, "p9", True, "") is False
    assert manager.decide(run_id, "p1", True, "") is True
    assert manager.decide(run_id, "p1", False, "") is False
    manager.join(timeout=5)
    assert manager.decide(run_id, "p1", True, "") is False


def test_runs_started_from_the_viewer_say_so_in_their_meta(tmp_path, monkeypatch):
    seen = []

    class WatchedRecorder(run_manager_module.RunRecorder):
        def __init__(self, runs_dir, desktop, meta, *args, **kwargs):
            seen.append(dict(meta))
            super().__init__(runs_dir, desktop, meta, *args, **kwargs)

    monkeypatch.setattr(run_manager_module, "RunRecorder", WatchedRecorder)
    manager, exe = make_manager(tmp_path, launch_then_finish())

    manager.start("설정을 열어줘", None, 3)
    manager.join(timeout=5)

    assert seen == [{"goal": "설정을 열어줘", "exe": str(exe), "model": seen[0]["model"], "permission_level": 3,
                     "started_by": "viewer", "system": "app"}]


@pytest.mark.parametrize("broken", ["toolbox", "gate"])
def test_a_factory_failing_after_the_recorder_stops_its_heartbeat_and_frees_the_slot(tmp_path, monkeypatch, broken):
    recorders = []

    class WatchedRecorder(run_manager_module.RunRecorder):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            recorders.append(self)

    def failing(*_args):
        raise RuntimeError(f"{broken} factory is broken")

    monkeypatch.setattr(run_manager_module, "RunRecorder", WatchedRecorder)
    options = {"gate_factory": failing} if broken == "gate" else {}
    manager, _ = make_manager(tmp_path, launch_then_finish(), **options)
    if broken == "toolbox":
        manager._toolbox_factory = failing

    with pytest.raises(run_manager_module.RunStartFailed):
        manager.start("설정을 열어줘", None, 3, approve=True)

    [recorder] = recorders
    assert not recorder._heartbeat_thread.is_alive()
    assert manager.active() is None
    assert run_json(tmp_path, recorder.run_id)["status"] == "stopped"


class WatchedAskGate(AskGate):
    """An AskGate that remembers how it was made, so a test can ask through it."""

    made = []

    def __init__(self, **options):
        super().__init__(**options)
        self.options = options
        WatchedAskGate.made.append(self)


def ask_in_background(gate, question):
    answers = []
    thread = threading.Thread(target=lambda: answers.append(gate.ask(question)))
    thread.start()
    for _ in range(500):
        if gate.waiting(question["id"]):
            break
        threading.Event().wait(0.01)
    return thread, answers


@pytest.fixture
def asking_manager(tmp_path):
    WatchedAskGate.made = []
    model = GatedModel()
    manager, _ = make_manager(tmp_path, model, ask_gate_factory=WatchedAskGate)
    yield manager, model
    model.release.set()
    manager.join(timeout=5)


def test_every_run_gets_an_ask_gate_even_without_approval(asking_manager):
    manager, model = asking_manager

    manager.start("목록을 열어줘", None, 3)
    assert model.started.wait(timeout=5)

    [gate] = WatchedAskGate.made
    assert gate.options == {"timeout_s": 1800}


def test_ask_gate_takes_the_task_timeout(tmp_path, monkeypatch):
    WatchedAskGate.made = []
    model = GatedModel()
    manager, _ = make_manager(tmp_path, model, ask_gate_factory=WatchedAskGate)
    original = manager.build_task

    def slow_to_give_up(goal, exe, level):
        task = original(goal, exe, level)
        object.__setattr__(task, "ask_timeout_s", 90)
        return task

    monkeypatch.setattr(manager, "build_task", slow_to_give_up)
    manager.start("목록을 열어줘", None, 3)
    model.release.set()
    manager.join(timeout=5)

    assert WatchedAskGate.made[0].options == {"timeout_s": 90}


def test_answer_reaches_the_question_of_the_active_run(asking_manager):
    manager, model = asking_manager
    run_id = manager.start("목록을 열어줘", None, 3)
    assert model.started.wait(timeout=5)
    [gate] = WatchedAskGate.made

    asker, answers = ask_in_background(gate, {"id": "q1", "kind": "stuck", "text": "?"})
    accepted = manager.answer(run_id, "q1", "여기", pin=(1018, 520))
    asker.join(timeout=5)

    assert accepted is True
    assert answers == [Answer(text="여기", pin=(1018, 520))]


def test_answer_carries_completion_and_registration_fields(asking_manager):
    manager, model = asking_manager
    run_id = manager.start("목록을 열어줘", None, 3)
    assert model.started.wait(timeout=5)
    [gate] = WatchedAskGate.made
    items = [{"n": 1, "decision": "approve"}, {"n": 2, "decision": "edit", "entry": {"resolution": "고침"}}]

    asker, answers = ask_in_background(gate, {"id": "q2", "kind": "registration", "text": "?"})
    manager.answer(run_id, "q2", "", accepted=True, items=items)
    asker.join(timeout=5)

    assert answers == [Answer(text="", accepted=True, items=tuple(items))]


def test_answers_for_other_runs_unknown_or_answered_questions_are_refused(asking_manager):
    manager, model = asking_manager
    assert manager.answer("20261113-000000", "q1", "1번") is False
    run_id = manager.start("목록을 열어줘", None, 3)
    assert model.started.wait(timeout=5)
    [gate] = WatchedAskGate.made
    asker, _ = ask_in_background(gate, {"id": "q1", "kind": "stuck", "text": "?"})

    assert manager.answer("20261113-000000", "q1", "1번") is False
    assert manager.answer(run_id, "q9", "1번") is False
    assert manager.answer(run_id, "q1", "1번") is True
    assert manager.answer(run_id, "q1", "2번") is False
    asker.join(timeout=5)
    model.release.set()
    manager.join(timeout=5)
    assert manager.answer(run_id, "q1", "3번") is False


def test_can_answer_only_while_a_run_is_active(asking_manager):
    manager, model = asking_manager
    idle = manager.can_answer()
    manager.start("목록을 열어줘", None, 3)
    assert model.started.wait(timeout=5)
    busy = manager.can_answer()
    model.release.set()
    manager.join(timeout=5)

    assert (idle, busy, manager.can_answer()) == (False, True, False)


def test_a_failing_ask_gate_factory_frees_the_slot(tmp_path):
    def failing(**_options):
        raise RuntimeError("ask gate factory is broken")

    manager, _ = make_manager(tmp_path, launch_then_finish(), ask_gate_factory=failing)

    with pytest.raises(run_manager_module.RunStartFailed):
        manager.start("설정을 열어줘", None, 3)

    assert manager.active() is None
    assert manager.can_answer() is False



class FactorySpy:
    """A toolbox factory that remembers what each run was given."""

    def __init__(self):
        self.calls = []

    def __call__(self, desktop, task, confirm, observer, asker, knowledge, captures_dir):
        self.calls.append({"asker": asker, "knowledge": knowledge, "captures_dir": captures_dir, "task": task})
        return Toolbox(desktop, task, confirm=confirm, sleep=lambda _s: None, observer=observer, asker=asker,
                       knowledge=knowledge, captures_dir=captures_dir)


def spy_manager(tmp_path, model, cli=None, **options):
    cli = cli or FakeCli()
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"")
    spy = FactorySpy()
    manager = RunManager(tmp_path / "runs", "ad.exe", str(exe), lambda _task: model, spy,
                         desktop_factory=lambda: DesktopClient("ad.exe", "run1", runner=cli), **options)
    return manager, spy


def wait_for_event(tmp_path, run_id, kind, count=1, timeout=5):
    path = tmp_path / "runs" / run_id / "events.jsonl"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            found = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            matching = [event for event in found if event.get("type") == kind]
            if len(matching) >= count:
                return matching[count - 1]
        time.sleep(0.02)
    raise AssertionError(f"no {kind} event #{count} in {run_id}")


def stuck_question():
    return {"kind": "stuck", "text": "무엇을 눌러야 하나요?", "proposed": None, "basis": [],
            "candidates": [{"label": "라인 오른쪽", "mark": 3, "capture": None, "x": None, "y": None}]}


def asking_model():
    return ScriptedModel([
        [call("launch_target", {"intent": "앱을 연다"})],
        [call("observe", SCENE_OBSERVE)],
        [call("ask", stuck_question())],
        [call("finish", {"success": True, "evidence": "목록을 열었음", "saved_path": None, "completion_id": None})],
    ])


class RegisterSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, model, toolbox, log, *, store, run_id, today=None):
        self.calls.append({"store": store, "run_id": run_id, "toolbox": toolbox})
        return 0


def test_started_run_gets_the_ask_gate_the_store_and_the_register_hook(tmp_path, monkeypatch):
    seen = {}

    def fake_run_agent(model, toolbox, task, log=None, gate=None, register=None):
        seen.update(register=register, toolbox=toolbox)
        return RunResult("gave_up", 0, "test")

    monkeypatch.setattr(run_manager_module, "run_agent", fake_run_agent)
    WatchedAskGate.made = []
    manager, spy = spy_manager(tmp_path, GatedModel(), ask_gate_factory=WatchedAskGate,
                               knowledge_dir=tmp_path / "knowledge")

    run_id = manager.start("목록을 열어줘", None, 2, system="mes")
    manager.join(timeout=5)

    [made] = spy.calls
    assert made["asker"] is WatchedAskGate.made[0]
    assert isinstance(made["knowledge"], KnowledgeStore)
    assert made["knowledge"].folder == tmp_path / "knowledge" / "mes"
    assert made["captures_dir"] == tmp_path / "runs" / run_id / "captures"
    register = seen["register"]
    assert isinstance(register, functools.partial) and register.func is register_after_run
    assert register.keywords == {"store": made["knowledge"], "run_id": run_id}


def test_question_from_the_run_is_answered_through_the_manager(tmp_path, monkeypatch):
    register = RegisterSpy()
    monkeypatch.setattr(run_manager_module, "register_after_run", register)
    manager, spy = spy_manager(tmp_path, asking_model(), FakeCli(scene=SCENE), knowledge_dir=tmp_path / "knowledge")

    run_id = manager.start("목록을 열어줘", None, 2)
    question = wait_for_event(tmp_path, run_id, "question")
    assert manager.can_answer() is True
    assert manager.answer(run_id, question["id"], "1번") is True
    completion = wait_for_event(tmp_path, run_id, "question", count=2)
    assert completion["kind"] == "completion"
    assert manager.answer(run_id, completion["id"], "", accepted=True) is True
    manager.join(timeout=5)

    events = run_events(tmp_path, run_id)
    answers = [event for event in events if event["type"] == "answer"]
    assert [answer["text"] for answer in answers] == ["1번", ""]
    assert run_json(tmp_path, run_id)["status"] == "confirmed"
    assert run_json(tmp_path, run_id)["system"] == "app"
    [registered] = register.calls
    assert registered["run_id"] == run_id and registered["store"] is spy.calls[0]["knowledge"]
    assert manager.can_answer() is False


def test_unanswered_question_ends_the_run_as_stopped(tmp_path, monkeypatch):
    register = RegisterSpy()
    monkeypatch.setattr(run_manager_module, "register_after_run", register)
    manager, _ = spy_manager(tmp_path, asking_model(), FakeCli(scene=SCENE), knowledge_dir=tmp_path / "knowledge",
                             ask_gate_factory=lambda timeout_s: AskGate(timeout_s=0.2))

    run_id = manager.start("목록을 열어줘", None, 2)
    manager.join(timeout=5)

    assert run_json(tmp_path, run_id)["status"] == "stopped"
    assert any(event["type"] == "answer" and event["timed_out"] for event in run_events(tmp_path, run_id))
    assert register.calls == []
    assert manager.active() is None


def test_system_from_the_form_selects_the_knowledge_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 0, "test"))
    manager, spy = spy_manager(tmp_path, GatedModel(), knowledge_dir=tmp_path / "knowledge")

    named = manager.start("열어줘", None, 2, system="생산MES")
    manager.join(timeout=5)
    unnamed = manager.start("열어줘", None, 2, system="")
    manager.join(timeout=5)

    assert [call["knowledge"].system for call in spy.calls] == ["생산MES", "app"]
    assert (run_json(tmp_path, named)["system"], run_json(tmp_path, unnamed)["system"]) == ("생산MES", "app")


@pytest.mark.parametrize("system", ["a/b", "..", "a b", "x" * 65, 5, ["mes"]])
def test_system_with_odd_characters_is_invalid(tmp_path, system):
    manager, spy = spy_manager(tmp_path, GatedModel(), knowledge_dir=tmp_path / "knowledge")

    with pytest.raises(InvalidRun):
        manager.start("열어줘", None, 2, system=system)

    assert manager.active() is None and spy.calls == []
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_knowledge_folder_defaults_like_run_py(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 0, "test"))
    monkeypatch.delenv("AGENT_KNOWLEDGE_DIR", raising=False)
    manager, spy = spy_manager(tmp_path, GatedModel())
    manager.start("열어줘", None, 2)
    manager.join(timeout=5)
    monkeypatch.setenv("AGENT_KNOWLEDGE_DIR", str(tmp_path / "from-env"))
    manager.start("열어줘", None, 2)
    manager.join(timeout=5)

    assert [call["knowledge"].folder for call in spy.calls] == [DEFAULT_ROOT / "app", (tmp_path / "from-env" / "app").resolve()]
