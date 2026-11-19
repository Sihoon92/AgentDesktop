import functools
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_cli import FakeCli
from test_loop import PLAN, ScriptedModel, call
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

    def respond(self, instructions, items, tools, effort=None):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test never released the model")
        output = [call("finish", {"success": False, "evidence": "done"})]
        return SimpleNamespace(output=output, usage=SimpleNamespace(total_tokens=10))


class BrokenModel:
    def respond(self, instructions, items, tools, effort=None):
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
            PLAN,
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
    monkeypatch.setattr(run_manager_module, "current_code", lambda: "abc1234")
    manager, exe = make_manager(tmp_path, launch_then_finish())

    run_id = manager.start("설정을 열어줘", None, 3)
    manager.join(timeout=5)

    assert seen == [{"goal": "설정을 열어줘", "exe": str(exe), "model": seen[0]["model"], "permission_level": 3,
                     "max_steps": 60, "started_by": "viewer", "system": "app", "code": "abc1234"}]
    assert run_json(tmp_path, run_id)["code"] == "abc1234"


def test_a_finished_viewer_run_leaves_its_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "current_code", lambda: "abc1234")
    manager, _ = make_manager(tmp_path, launch_then_finish())

    run_id = manager.start("설정을 열어줘", None, 3)
    manager.join(timeout=5)

    written = (tmp_path / "runs" / run_id / "summary.txt").read_text(encoding="utf-8").splitlines()
    final = run_json(tmp_path, run_id)
    verdict = f"  판정: {final['status']} · 스텝 {final['steps']}/60 · 토큰 {final['tokens']}"
    assert written[:3] == ["요약 (화면 내용 없음)", "  코드: abc1234", verdict]
    assert "설정을 열어줘" not in "\n".join(written)


def test_a_failing_summary_changes_neither_the_result_nor_the_free_slot(tmp_path, monkeypatch):
    def broken(_path):
        raise RuntimeError("summary is broken")

    monkeypatch.setattr(run_manager_module, "write_summary", broken)
    manager, _ = make_manager(tmp_path, launch_then_finish())

    run_id = manager.start("설정을 열어줘", None, 3)
    manager.join(timeout=5)

    assert manager.active() is None
    assert run_json(tmp_path, run_id)["status"] == "gave_up"
    assert not (tmp_path / "runs" / run_id / "summary.txt").exists()
    manager._model_factory = lambda _task: launch_then_finish()
    assert manager.start("다시", None, 3) != run_id
    manager.join(timeout=5)


def test_default_system_is_the_settings_system_or_none(tmp_path):
    with_setting, _ = make_manager(tmp_path, launch_then_finish(), task_defaults={"system": "mes"})
    without, _ = make_manager(tmp_path, launch_then_finish())

    assert with_setting.default_system == "mes"
    assert without.default_system is None


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
    assert (recorder.path / "summary.txt").read_text(encoding="utf-8").splitlines()[2].startswith("  판정: stopped")


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

    def slow_to_give_up(goal, exe, level, stage_call_limit=None):
        task = original(goal, exe, level, stage_call_limit)
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


def answer_once_waiting(manager, run_id, question_id, text, timeout=5, **answer):
    """Answers once the run's ask gate holds the question. The run writes the question event just
    before its gate starts waiting, so an answer sent the moment the event shows can come first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gate = manager._ask_gate
        if gate is not None and gate.waiting(question_id):
            return manager.answer(run_id, question_id, text, **answer)
        time.sleep(0.01)
    raise AssertionError(f"{question_id} never waited for an answer in {run_id}")


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

    def fake_run_agent(model, toolbox, task, log=None, gate=None, register=None, stop=None):
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
    assert answer_once_waiting(manager, run_id, question["id"], "1번") is True
    completion = wait_for_event(tmp_path, run_id, "question", count=2)
    assert completion["kind"] == "completion"
    assert answer_once_waiting(manager, run_id, completion["id"], "", accepted=True) is True
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


def test_task_defaults_from_the_settings_shape_every_new_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 0, "test"))
    defaults = {"model": "house-model", "reasoning_effort": "high", "image_mode": "off", "system": "공정",
                "knowledge_dir": str(tmp_path / "from-settings"), "exe": "C:/elsewhere/Other.exe"}
    models = []
    manager, spy = spy_manager(tmp_path, GatedModel(), task_defaults=defaults)
    manager._model_factory = lambda task: models.append((task.model, task.reasoning_effort)) or GatedModel()

    run_id = manager.start("열어줘", None, 2)
    manager.join(timeout=5)

    [call] = spy.calls
    assert models == [("house-model", "high")]
    assert call["task"].image_mode == "off"
    assert call["task"].exe == manager.default_exe
    assert call["knowledge"].folder == tmp_path / "from-settings" / "공정"
    assert (run_json(tmp_path, run_id)["model"], run_json(tmp_path, run_id)["system"]) == ("house-model", "공정")


def test_the_form_system_and_knowledge_dir_argument_beat_the_task_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 0, "test"))
    defaults = {"system": "공정", "knowledge_dir": str(tmp_path / "from-settings")}
    manager, spy = spy_manager(tmp_path, GatedModel(), task_defaults=defaults, knowledge_dir=tmp_path / "arg")

    manager.start("열어줘", None, 2, system="mes")
    manager.join(timeout=5)

    assert spy.calls[0]["knowledge"].folder == tmp_path / "arg" / "mes"


def test_without_task_defaults_new_runs_keep_the_code_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 0, "test"))
    manager, spy = spy_manager(tmp_path, GatedModel())

    manager.start("열어줘", None, 2)
    manager.join(timeout=5)

    assert spy.calls[0]["task"].model == "gpt-6.1-terra"


def test_viewer_meta_carries_the_task_step_limit(tmp_path, monkeypatch):
    seen = []

    class WatchedRecorder(run_manager_module.RunRecorder):
        def __init__(self, runs_dir, desktop, meta, *args, **kwargs):
            seen.append(dict(meta))
            super().__init__(runs_dir, desktop, meta, *args, **kwargs)

    monkeypatch.setattr(run_manager_module, "RunRecorder", WatchedRecorder)
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 3, "test"))
    manager, spy = spy_manager(tmp_path, GatedModel())

    run_id = manager.start("열어줘", None, 2)
    manager.join(timeout=5)

    assert [meta["max_steps"] for meta in seen] == [spy.calls[0]["task"].max_steps] == [60]
    assert run_json(tmp_path, run_id)["max_steps"] == 60


def test_the_run_list_reads_a_run_json_that_holds_the_step_limit(tmp_path):
    from viewer.history import RunIndex

    folder = tmp_path / "runs" / "20261114-010203"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"format": 2, "run_id": folder.name, "status": "gave_up", "steps": 3,
                                                 "max_steps": 40, "model": "m"}), encoding="utf-8")

    summary = RunIndex(tmp_path / "runs").run(folder.name)

    assert (summary["status"], summary["steps"], summary["model"]) == ("gave_up", 3, "m")
    assert not summary["newer_format"]


def recording(manager, model):
    """Makes the manager hand `model` to every run and keeps each run's task."""
    tasks = []
    manager._model_factory = lambda task: tasks.append(task) or model
    return tasks


def test_a_stage_call_limit_from_the_form_reaches_the_task(tmp_path):
    manager, _ = make_manager(tmp_path, None)
    tasks = recording(manager, launch_then_finish())

    manager.start("설정을 열어줘", None, 3, stage_call_limit=3)
    manager.join(timeout=5)

    assert [task.stage_call_limit for task in tasks] == [3]


def test_an_empty_stage_call_limit_keeps_the_settings_value_or_the_default(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "set").mkdir()
    plain, _ = make_manager(tmp_path / "plain", GatedModel())
    configured, _ = make_manager(tmp_path / "set", GatedModel(), task_defaults={"stage_call_limit": 5})

    assert plain.build_task("열어줘", None, 3).stage_call_limit == 8
    assert plain.build_task("열어줘", None, 3, stage_call_limit=None).stage_call_limit == 8
    assert configured.build_task("열어줘", None, 3).stage_call_limit == 5
    assert configured.build_task("열어줘", None, 3, stage_call_limit=2).stage_call_limit == 2
    assert (plain.default_stage_call_limit, configured.default_stage_call_limit) == (8, 5)


@pytest.mark.parametrize("value", [0, -1, "8", "", 2.5, True])
def test_a_bad_stage_call_limit_is_rejected_before_anything_starts(tmp_path, value):
    manager, _ = make_manager(tmp_path, GatedModel())

    with pytest.raises(InvalidRun, match="단계별 호출 한도"):
        manager.start("열어줘", None, 3, stage_call_limit=value)

    assert manager.active() is None
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


class RawFactory:
    """A toolbox factory that keeps the exact positional and keyword arguments of each call."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        desktop, task, confirm, observer, asker, knowledge, captures_dir = args
        return Toolbox(desktop, task, confirm=confirm, sleep=lambda _s: None, observer=observer, asker=asker,
                       knowledge=knowledge, captures_dir=captures_dir, vision=kwargs.get("vision"))


def models_manager(tmp_path, factory_result, toolbox_factory, monkeypatch):
    """A manager whose model factory returns `factory_result`; returns (manager, models given to the loop)."""
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"")
    given = []
    monkeypatch.setattr(run_manager_module, "run_agent",
                        lambda model, *args, **kwargs: given.append(model) or RunResult("gave_up", 0, "test"))
    manager = RunManager(tmp_path / "runs", "ad.exe", str(exe), lambda _task: factory_result, toolbox_factory,
                         desktop_factory=lambda: DesktopClient("ad.exe", "run1", runner=FakeCli()))
    return manager, given


def test_model_factory_returns_models_and_toolbox_gets_vision(tmp_path, monkeypatch):
    from agent.llm import Models

    text, vision = GatedModel(), SimpleNamespace(read=lambda *args: None)
    factory = RawFactory()
    models = Models(text=text, vision=vision, text_source="env", vision_source="env", image_mode_ignored=False)
    manager, given = models_manager(tmp_path, models, factory, monkeypatch)

    manager.start("열어줘")
    manager.join(timeout=5)

    assert given == [text]
    [(args, kwargs)] = factory.calls
    assert len(args) == 7 and kwargs == {"vision": vision}


def test_models_without_vision_call_the_toolbox_factory_exactly_as_before(tmp_path, monkeypatch):
    from agent.llm import Models

    text = GatedModel()
    factory = RawFactory()
    models = Models(text=text, vision=None, text_source="기본값", vision_source=None, image_mode_ignored=False)
    manager, given = models_manager(tmp_path, models, factory, monkeypatch)

    manager.start("열어줘")
    manager.join(timeout=5)

    assert given == [text]
    [(args, kwargs)] = factory.calls
    assert len(args) == 7 and kwargs == {}


@pytest.mark.parametrize("with_vision", [True, False])
def test_viewer_run_json_says_whether_a_vision_model_read_the_pictures(tmp_path, monkeypatch, with_vision):
    from agent.llm import Models

    vision = SimpleNamespace(read=lambda *args: None) if with_vision else None
    models = Models(text=GatedModel(), vision=vision, text_source="env",
                    vision_source="env" if with_vision else None, image_mode_ignored=False)
    manager, _ = models_manager(tmp_path, models, RawFactory(), monkeypatch)

    run_id = manager.start("열어줘")
    manager.join(timeout=5)

    assert run_json(tmp_path, run_id)["vision"] is with_vision


def test_a_plain_model_from_the_factory_is_the_text_model(tmp_path, monkeypatch):
    model = GatedModel()
    factory = RawFactory()
    manager, given = models_manager(tmp_path, model, factory, monkeypatch)

    manager.start("열어줘")
    manager.join(timeout=5)

    assert given == [model]
    assert factory.calls[0][1] == {}


def test_default_toolbox_takes_the_vision(tmp_path):
    vision = SimpleNamespace(read=lambda *args: None)
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"")
    manager = RunManager(tmp_path / "runs", "ad.exe", str(exe), None)
    desktop = DesktopClient("ad.exe", "run1", runner=FakeCli())
    built_task = manager.build_task("열어줘", None, 3)

    with_vision = run_manager_module.default_toolbox(desktop, built_task, None, None, None, None, tmp_path, vision=vision)
    without = run_manager_module.default_toolbox(desktop, built_task, None, None, None, None, tmp_path)

    assert (with_vision.vision_enabled, without.vision_enabled) == (True, False)


def test_broken_vision_setup_fails_the_start(tmp_path, monkeypatch):
    from agent.llm import ModelSetupError

    def refuse(_task):
        raise ModelSetupError("VISION_BASE_URL가 있지만 비전 모델 이름이 없습니다. VISION_MODEL를 넣어 주세요")

    manager, _ = make_manager(tmp_path, None)
    manager._model_factory = refuse

    with pytest.raises(run_manager_module.RunStartFailed) as raised:
        manager.start("열어줘")

    assert "VISION_MODEL" in str(raised.value)
    assert manager.active() is None
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_the_environment_gives_viewer_runs_their_model(tmp_path):
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"")
    with_env = RunManager(tmp_path / "runs", "ad.exe", str(exe), None, environ={"OPENAI_MODEL": "env-model"})
    configured = RunManager(tmp_path / "runs", "ad.exe", str(exe), None, environ={"OPENAI_MODEL": "env-model"},
                            task_defaults={"model": "house"})
    without = RunManager(tmp_path / "runs", "ad.exe", str(exe), None)

    assert (with_env.build_task("g", None, 3).model, with_env.build_task("g", None, 3).model_source) == (
        "env-model", "env")
    assert configured.build_task("g", None, 3).model_source == "설정 파일"
    assert without.build_task("g", None, 3).model_source == "기본값"


def site_exes(tmp_path):
    exes = {}
    for key, folder, name in (("ulsan", "SiteA", "MES.exe"), ("cheonan", "SiteB", "MES.exe"), ("other", "Tools", "Other.exe")):
        target = tmp_path / folder / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"")
        exes[key] = target
    return exes


def systems_manager(tmp_path, monkeypatch, default_exe=None, **settings):
    """A manager with no default exe unless given, whose settings name two systems; returns (manager, spy, exes)."""
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 0, "test"))
    exes = site_exes(tmp_path)
    defaults = {"systems": {"울산": str(exes["ulsan"]), "천안소형": str(exes["cheonan"])}, **settings}
    spy = FactorySpy()
    manager = RunManager(tmp_path / "runs", "ad.exe", default_exe, lambda _task: GatedModel(), spy,
                         desktop_factory=lambda: DesktopClient("ad.exe", "run1", runner=FakeCli()),
                         knowledge_dir=tmp_path / "knowledge", task_defaults=defaults)
    return manager, spy, exes


def started(manager, *args, **kwargs):
    run_id = manager.start("열어줘", *args, **kwargs)
    manager.join(timeout=5)
    return run_id


def test_start_with_a_system_name_uses_its_exe(tmp_path, monkeypatch):
    manager, spy, exes = systems_manager(tmp_path, monkeypatch)

    run_id = started(manager, None, 2, system="천안소형")

    [call] = spy.calls
    assert call["task"].exe == str(exes["cheonan"])
    assert call["knowledge"].system == "천안소형"
    assert run_json(tmp_path, run_id)["system"] == "천안소형"


def test_a_system_name_with_surrounding_spaces_still_finds_its_exe(tmp_path, monkeypatch):
    manager, spy, exes = systems_manager(tmp_path, monkeypatch)

    started(manager, None, 2, system=" 울산 ")

    assert spy.calls[0]["task"].exe == str(exes["ulsan"]) and spy.calls[0]["knowledge"].system == "울산"


def test_the_form_exe_beats_the_system_entry(tmp_path, monkeypatch):
    manager, spy, exes = systems_manager(tmp_path, monkeypatch)

    started(manager, str(exes["other"]), 2, system="울산")

    assert spy.calls[0]["task"].exe == str(exes["other"])
    assert spy.calls[0]["knowledge"].system == "울산"


def test_without_a_default_exe_the_default_choice_runs(tmp_path, monkeypatch):
    manager, spy, exes = systems_manager(tmp_path, monkeypatch, system="천안소형")

    run_id = started(manager, None, 2)

    assert spy.calls[0]["task"].exe == str(exes["cheonan"])
    assert run_json(tmp_path, run_id)["system"] == "천안소형"


def test_without_a_default_exe_or_settings_system_the_first_system_runs(tmp_path, monkeypatch):
    manager, spy, exes = systems_manager(tmp_path, monkeypatch)

    run_id = started(manager, None, 2, system="")

    assert spy.calls[0]["task"].exe == str(exes["ulsan"])
    assert run_json(tmp_path, run_id)["system"] == "울산"


def test_a_default_exe_beats_the_default_choice_when_the_form_names_no_system(tmp_path, monkeypatch):
    other = tmp_path / "Tools" / "Other.exe"
    manager, spy, exes = systems_manager(tmp_path, monkeypatch, default_exe=str(other))

    started(manager, None, 2)

    assert spy.calls[0]["task"].exe == str(exes["other"])


def test_an_unknown_system_with_a_default_exe_is_only_a_knowledge_name(tmp_path, monkeypatch):
    other = tmp_path / "Tools" / "Other.exe"
    manager, spy, exes = systems_manager(tmp_path, monkeypatch, default_exe=str(other))

    started(manager, None, 2, system="공정")

    assert spy.calls[0]["task"].exe == str(exes["other"]) and spy.calls[0]["knowledge"].system == "공정"


def test_an_unknown_system_without_any_exe_is_invalid(tmp_path, monkeypatch):
    manager, spy, _ = systems_manager(tmp_path, monkeypatch)

    with pytest.raises(InvalidRun) as raised:
        manager.start("열어줘", None, 2, system="부산")

    assert "설정 파일의 systems에 없는 시스템" in str(raised.value)
    assert manager.active() is None and spy.calls == []
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("system", [5, ["울산"]])
def test_a_system_that_is_not_text_is_still_invalid_with_systems(tmp_path, monkeypatch, system):
    manager, spy, _ = systems_manager(tmp_path, monkeypatch)

    with pytest.raises(InvalidRun):
        manager.start("열어줘", None, 2, system=system)

    assert spy.calls == []


def test_systems_and_default_choice_come_from_the_settings(tmp_path, monkeypatch):
    with_system, _, _ = systems_manager(tmp_path, monkeypatch, system="천안소형")
    first, _, _ = systems_manager(tmp_path, monkeypatch, system="공정")
    plain = RunManager(tmp_path / "runs", "ad.exe", None, None, task_defaults={"system": "공정"})

    assert with_system.systems == ["울산", "천안소형"]
    assert (with_system.default_choice, first.default_choice) == ("천안소형", "울산")
    assert (plain.systems, plain.default_choice) == ([], None)
    assert (RunManager(tmp_path / "runs", "ad.exe", None, None).systems, ) == ([], )


def test_viewer_run_gets_the_ready_signal_only_for_a_configured_exe(tmp_path, monkeypatch):
    manager, spy, exes = systems_manager(tmp_path, monkeypatch, ready_text="생산운영", ready_timeout_s=90)
    models_saw = []
    manager._model_factory = lambda task: models_saw.append(task.ready_text) or GatedModel()

    started(manager, None, 2, system="울산")
    started(manager, str(exes["cheonan"]).upper(), 2)
    started(manager, str(exes["other"]), 2, system="울산")

    tasks = [call["task"] for call in spy.calls]
    assert [(task.ready_text, task.ready_timeout_s) for task in tasks] == [
        ("생산운영", 90), ("생산운영", 90), (None, 180)]
    assert models_saw == ["생산운영", "생산운영", None]


def test_the_settings_exe_as_default_gets_the_ready_signal(tmp_path, monkeypatch):
    other = str(tmp_path / "Tools" / "Other.exe")
    manager, spy, _ = systems_manager(tmp_path, monkeypatch, default_exe=other, exe=other, ready_text="생산운영")

    started(manager, None, 2)

    assert spy.calls[0]["task"].ready_text == "생산운영"


def test_a_cli_default_exe_the_settings_do_not_name_gets_no_ready_signal(tmp_path, monkeypatch):
    other = str(tmp_path / "Tools" / "Other.exe")
    manager, spy, _ = systems_manager(tmp_path, monkeypatch, default_exe=other, ready_text="생산운영")

    started(manager, None, 2)

    assert spy.calls[0]["task"].ready_text is None


def test_the_ready_text_stays_out_of_run_json(tmp_path, monkeypatch):
    manager, _, _ = systems_manager(tmp_path, monkeypatch, ready_text="생산운영")

    run_id = started(manager, None, 2, system="울산")

    text = (tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8")
    assert "생산운영" not in text


from agent.loop import USER_STOP_REASON  # noqa: E402


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("condition never held")
        time.sleep(0.01)


def gated_run(tmp_path, monkeypatch, **options):
    monkeypatch.setattr(run_manager_module, "current_code", lambda: "abc1234")
    model = GatedModel()
    manager, _ = make_manager(tmp_path, model, **options)
    run_id = manager.start("설정을 열어줘", None, 3)
    assert model.started.wait(timeout=5)
    return manager, model, run_id


def test_stop_ends_the_active_run_as_stopped_with_a_summary(tmp_path, monkeypatch):
    manager, model, run_id = gated_run(tmp_path, monkeypatch)

    assert manager.stop(run_id) is True
    model.release.set()
    manager.join(timeout=5)

    final = run_json(tmp_path, run_id)
    assert (final["status"], final["reason"]) == ("stopped", USER_STOP_REASON)
    assert (tmp_path / "runs" / run_id / "summary.txt").exists()
    assert not [event for event in run_events(tmp_path, run_id) if event["type"] == "action"]
    assert manager.active() is None


def test_stop_of_another_or_no_run_is_false(tmp_path, monkeypatch):
    manager, _ = make_manager(tmp_path, GatedModel())
    assert manager.stop("20261118-000000") is False
    assert manager.stopping() is False

    (tmp_path / "second").mkdir()
    manager, model, run_id = gated_run(tmp_path / "second", monkeypatch)
    assert manager.stop("20261118-000000") is False
    assert manager.stopping() is False
    model.release.set()
    manager.join(timeout=5)
    assert manager.stop(run_id) is False
    assert run_json(tmp_path / "second", run_id)["status"] == "gave_up"


def test_stop_answers_a_waiting_question_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "register_after_run", RegisterSpy())
    manager, _ = spy_manager(tmp_path, asking_model(), FakeCli(scene=SCENE), knowledge_dir=tmp_path / "knowledge")
    run_id = manager.start("목록을 열어줘", None, 2)
    wait_until(lambda: manager._ask_gate is not None and manager._ask_gate.waiting("q1"))

    assert manager.stop(run_id) is True
    manager.join(timeout=1)

    assert manager.active() is None
    final = run_json(tmp_path, run_id)
    assert (final["status"], final["reason"]) == ("stopped", USER_STOP_REASON)


def test_stop_answers_a_waiting_proposal_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(run_manager_module, "current_code", lambda: "abc1234")
    gate = WatchedGate(timeout_s=600)
    manager, _ = make_manager(tmp_path, launch_then_finish(), gate_factory=lambda: gate)
    run_id = manager.start("설정을 열어줘", None, 3, approve=True)
    assert gate.waiting.wait(timeout=5)

    assert manager.stop(run_id) is True
    manager.join(timeout=1)

    assert manager.active() is None
    final = run_json(tmp_path, run_id)
    assert (final["status"], final["reason"]) == ("stopped", USER_STOP_REASON)


def test_stop_and_wait_returns_true_once_the_run_ended(tmp_path, monkeypatch):
    manager, model, run_id = gated_run(tmp_path, monkeypatch)
    threading.Timer(0.1, model.release.set).start()

    assert manager.stop_and_wait(5) is True

    assert manager.active() is None
    assert run_json(tmp_path, run_id)["reason"] == USER_STOP_REASON


def test_stop_and_wait_without_a_run_is_true(tmp_path):
    manager, _ = make_manager(tmp_path, GatedModel())
    assert manager.stop_and_wait(0.1) is True


def test_stop_and_wait_gives_up_after_the_timeout(tmp_path, monkeypatch):
    manager, model, run_id = gated_run(tmp_path, monkeypatch)

    started = time.monotonic()
    assert manager.stop_and_wait(0.2) is False
    assert time.monotonic() - started < 2
    assert manager.active() == run_id

    model.release.set()
    manager.join(timeout=5)
    assert manager.active() is None


def test_stopping_is_true_until_the_run_ends(tmp_path, monkeypatch):
    manager, model, run_id = gated_run(tmp_path, monkeypatch)
    assert manager.stopping() is False

    manager.stop(run_id)
    assert manager.stopping() is True

    model.release.set()
    manager.join(timeout=5)
    assert manager.stopping() is False


def test_stop_requested_is_recorded(tmp_path, monkeypatch):
    manager, model, run_id = gated_run(tmp_path, monkeypatch)

    manager.stop(run_id)
    manager.stop(run_id)
    model.release.set()
    manager.join(timeout=5)

    kinds = [event["type"] for event in run_events(tmp_path, run_id)]
    assert kinds.count("stop_requested") == 1
    assert kinds.index("stop_requested") < kinds.index("finish")


def test_a_new_run_does_not_inherit_the_stop(tmp_path, monkeypatch):
    manager, model, run_id = gated_run(tmp_path, monkeypatch)
    manager.stop(run_id)
    model.release.set()
    manager.join(timeout=5)

    model.started.clear()
    second = manager.start("설정을 열어줘", None, 3)
    assert model.started.wait(timeout=5)
    assert manager.stopping() is False
    manager.join(timeout=5)

    assert run_json(tmp_path, second)["status"] == "gave_up"


# --- workflows: listing and replaying from the viewer (plan task 9) ---------------------------

from agent import workflow_store  # noqa: E402
from agent.ask import NoAsker  # noqa: E402
from agent.workflow_store import WorkflowStore  # noqa: E402
from replay_support import AppOptions, FakeApp, FakeClock, make_values  # noqa: E402
from replay_support import make_workflow as replay_workflow  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_workflow_folder(tmp_path, monkeypatch):
    """No test here may write into examples/llm-agent/workflows."""
    monkeypatch.setattr(workflow_store, "DEFAULT_ROOT", tmp_path / "default-workflows")


class BlockingClock(FakeClock):
    """A fake clock whose sleeps wait until the test releases them."""

    def __init__(self):
        super().__init__()
        self.sleeping = threading.Event()
        self.release = threading.Event()

    def sleep(self, seconds):
        self.sleeping.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test never released the clock")
        super().sleep(seconds)


def no_model(_task):
    raise AssertionError("a replay must not build a model")


def workflow_manager(tmp_path, options=None, clock=None, model_factory=no_model, **extra):
    clock = clock or FakeClock()
    app = FakeApp(clock, options)
    exe = tmp_path / "Mes.exe"
    exe.write_bytes(b"")
    manager = RunManager(
        tmp_path / "runs", "ad.exe", str(exe), model_factory,
        desktop_factory=lambda: DesktopClient("ad.exe", "run1", runner=app),
        replay_toolbox_factory=lambda desktop, task, observer, captures_dir: Toolbox(
            desktop, task, confirm=lambda _prompt: False, sleep=clock.sleep, clock=clock.now, observer=observer,
            asker=NoAsker(), captures_dir=captures_dir, gated=False),
        workflow_dir=tmp_path / "workflows",
        **extra,
    )
    return manager, app


def stored(tmp_path, system="mes", **overrides):
    make_values(tmp_path)
    return WorkflowStore(tmp_path / "workflows", system).add(replay_workflow(tmp_path, system=system, **overrides))


def given(tmp_path, **overrides):
    values = {"save_path": str(tmp_path / "out" / "a.xlsx")}
    values.update(overrides)
    return values


def test_workflows_lists_the_systems_entries(tmp_path):
    first = stored(tmp_path)
    second = stored(tmp_path, goal="다른 지시")
    stored(tmp_path, system="other")
    manager, _ = workflow_manager(tmp_path)

    listed = manager.workflows("mes")

    assert [item["id"] for item in listed] == [first["id"], second["id"]]
    assert set(listed[0]) == {"id", "goal", "status", "passes", "failures", "verified_with_other_values",
                              "params", "fixed"}
    assert listed[0]["status"] == "unverified" and listed[0]["passes"] == 0 and listed[1]["goal"] == "다른 지시"
    assert listed[0]["params"][0] == {"name": "shop", "kind": "list", "example": ["WA0", "WA1"]}
    assert [param["name"] for param in listed[0]["params"]] == ["shop", "period", "save_path"]
    assert manager.workflows("nothing-here") == []


def test_workflows_survives_a_broken_file(tmp_path):
    good = stored(tmp_path)
    folder = tmp_path / "workflows" / "mes"
    (folder / "w-20261119-900.yaml").write_text("format: 1\nid: [broken", encoding="utf-8")
    (folder / "w-20261119-901.yaml").write_text("format: 1\nid: w-20261119-901\n", encoding="utf-8")
    manager, _ = workflow_manager(tmp_path)

    assert [item["id"] for item in manager.workflows("mes")] == [good["id"]]


def test_workflows_of_an_invalid_system_name_are_refused(tmp_path):
    manager, _ = workflow_manager(tmp_path)

    with pytest.raises(InvalidRun):
        manager.workflows("../escape")


def test_the_workflow_folder_follows_the_argument_then_the_settings_then_the_environment(tmp_path):
    exe = tmp_path / "Mes.exe"
    exe.write_bytes(b"")
    common = {"runs_dir": tmp_path / "runs", "binary": "ad.exe", "default_exe": str(exe), "model_factory": no_model}
    env = {"AGENT_WORKFLOW_DIR": str(tmp_path / "env")}
    settings = {"workflow_dir": str(tmp_path / "set")}

    assert RunManager(**common, workflow_dir=tmp_path / "arg", task_defaults=settings,
                      environ=env).workflow_root == (tmp_path / "arg").resolve()
    assert RunManager(**common, task_defaults=settings, environ=env).workflow_root == (tmp_path / "set").resolve()
    assert RunManager(**common, environ=env).workflow_root == (tmp_path / "env").resolve()
    assert RunManager(**common).workflow_root == tmp_path / "default-workflows"


def test_start_workflow_runs_without_a_model(tmp_path):
    workflow = stored(tmp_path)
    manager, app = workflow_manager(tmp_path)

    run_id = manager.start_workflow(workflow["id"], given(tmp_path, shop="WA3"), system="mes")
    manager.join(timeout=10)

    meta = run_json(tmp_path, run_id)
    assert meta["status"] == "verified" and meta["started_by"] == "workflow" and meta["model"] == "workflow"
    assert meta["workflow_run"] == {"id": workflow["id"], "steps": 12, "steps_done": 12, "code": None}
    assert app.applied == {"WA3"} and app.saved == [str(tmp_path / "out" / "a.xlsx")]
    steps = [event for event in run_events(tmp_path, run_id) if event["type"] == "workflow_step"]
    assert len(steps) == 12 and all(event["ok"] for event in steps)
    assert (tmp_path / "runs" / run_id / "summary.txt").is_file()
    assert manager.active() is None


@pytest.mark.parametrize(("workflow_id", "values", "code"), [
    ("w-20991231-001", {}, "WORKFLOW_NOT_FOUND"),
    (None, {"nope": "1"}, "PARAM_UNKNOWN"),
    (None, {"shop": "WA0,,WA1"}, "PARAM_INVALID"),
    (None, {"save_path": "relative.xlsx"}, "PARAM_INVALID"),
])
def test_start_workflow_refuses_bad_input(tmp_path, workflow_id, values, code):
    workflow = stored(tmp_path)
    manager, app = workflow_manager(tmp_path)

    with pytest.raises(InvalidRun, match=code):
        manager.start_workflow(workflow_id or workflow["id"], values, system="mes")

    assert not (tmp_path / "runs").exists() and app.calls == [] and manager.active() is None


def test_start_workflow_refuses_a_save_path_that_exists(tmp_path):
    workflow = stored(tmp_path)
    taken = tmp_path / "out" / "taken.xlsx"
    taken.write_bytes(b"old")
    manager, app = workflow_manager(tmp_path)

    with pytest.raises(InvalidRun, match="FILE_EXISTS"):
        manager.start_workflow(workflow["id"], {"save_path": str(taken)}, system="mes")

    assert not (tmp_path / "runs").exists() and app.calls == []


@pytest.mark.parametrize("values", [None, ["shop"], {"shop": 3}, {1: "x"}])
def test_start_workflow_needs_text_values_by_name(tmp_path, values):
    workflow = stored(tmp_path)
    manager, _ = workflow_manager(tmp_path)

    with pytest.raises(InvalidRun):
        manager.start_workflow(workflow["id"], values, system="mes")


def test_start_workflow_refuses_a_broken_workflow_file(tmp_path):
    workflow = stored(tmp_path)
    path = tmp_path / "workflows" / "mes" / f"{workflow['id']}.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "x: 1\n", encoding="utf-8")
    manager, _ = workflow_manager(tmp_path)

    with pytest.raises(InvalidRun, match="WORKFLOW_INVALID"):
        manager.start_workflow(workflow["id"], given(tmp_path), system="mes")


def test_only_one_run_at_a_time_includes_replays(tmp_path):
    workflow = stored(tmp_path)
    clock = BlockingClock()
    manager, _ = workflow_manager(tmp_path, AppOptions(menu_after_s=3), clock=clock,
                                  model_factory=lambda _task: GatedModel())

    run_id = manager.start_workflow(workflow["id"], given(tmp_path), system="mes")
    assert clock.sleeping.wait(timeout=5)
    assert manager.active() == run_id
    with pytest.raises(RunBusy):
        manager.start_workflow(workflow["id"], given(tmp_path, save_path=str(tmp_path / "out" / "b.xlsx")),
                               system="mes")
    with pytest.raises(RunBusy):
        manager.start("설정을 열어줘", None, 2)

    clock.release.set()
    manager.join(timeout=10)
    assert manager.active() is None


def test_stop_reaches_a_replay(tmp_path):
    workflow = stored(tmp_path)
    clock = BlockingClock()
    manager, app = workflow_manager(tmp_path, AppOptions(menu_after_s=3), clock=clock)

    run_id = manager.start_workflow(workflow["id"], given(tmp_path), system="mes")
    assert clock.sleeping.wait(timeout=5)
    assert manager.stop(run_id) and manager.stopping()
    clock.release.set()
    manager.join(timeout=10)

    meta = run_json(tmp_path, run_id)
    assert meta["status"] == "stopped" and meta["workflow_run"]["code"] == "STOPPED_BY_USER"
    assert meta["workflow_run"]["steps_done"] == 1
    assert app.process_alive and "close-app" not in app.command_names()
    assert any(event["type"] == "stop_requested" for event in run_events(tmp_path, run_id))


def test_the_replay_updates_the_store(tmp_path):
    workflow = stored(tmp_path)
    store = WorkflowStore(tmp_path / "workflows", "mes")
    manager, _ = workflow_manager(tmp_path)
    manager.start_workflow(workflow["id"], given(tmp_path, shop="WA3"), system="mes")
    manager.join(timeout=10)

    passed = store.get(workflow["id"])
    assert passed["status"] == "verified" and passed["passes"] == 1 and passed["verified_with_other_values"] is True

    failing, _ = workflow_manager(tmp_path, AppOptions(duplicate_menu=True))
    run_id = failing.start_workflow(workflow["id"], given(tmp_path, save_path=str(tmp_path / "out" / "c.xlsx")),
                                    system="mes")
    failing.join(timeout=10)
    after = store.get(workflow["id"])
    assert run_json(tmp_path, run_id)["status"] == "stopped"
    assert after["failures"] == 1 and after["status"] == "verified" and after["passes"] == 1


class AfterRunSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, model, result, run_dir, store, log):
        self.calls.append({"model": model, "status": result.status, "run_dir": run_dir, "folder": store.folder,
                           "finished": json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["status"]})
        log({"type": "workflow", "created": True, "id": "w-20261119-001", "code": None, "steps": 3, "params": 1})
        return RunResult(result.status, result.steps, result.reason, tokens=(result.tokens or 0) + 7,
                         stats=result.stats)


def test_a_model_run_started_here_makes_a_workflow(tmp_path, monkeypatch):
    spy = AfterRunSpy()
    monkeypatch.setattr(run_manager_module, "after_run", spy)
    model = launch_then_finish()
    manager, _ = make_manager(tmp_path, model, workflow_dir=tmp_path / "workflows")

    run_id = manager.start("설정을 열어줘", None, 2, system="mes")
    manager.join(timeout=10)

    assert len(spy.calls) == 1
    seen = spy.calls[0]
    assert seen["model"] is model and seen["run_dir"] == tmp_path / "runs" / run_id
    assert seen["folder"] == (tmp_path / "workflows").resolve() / "mes"
    assert seen["finished"] == "running"
    meta = run_json(tmp_path, run_id)
    assert meta["workflow"] == {"created": True, "id": "w-20261119-001", "code": None}
    assert meta["tokens"] >= 7


class RunThenDraft:
    """Plays the list run's turns, then answers the workflow draft call with the list draft."""

    def __init__(self, turns, draft):
        self.run = ScriptedModel(turns)
        self.draft = draft

    def respond(self, instructions, items, tools, effort=None):
        if any(tool.get("name") == "propose_workflow" for tool in tools):
            return self.draft.respond(instructions, items, tools, effort)
        return self.run.respond(instructions, items, tools, effort)


def test_a_successful_model_run_here_makes_a_workflow_with_the_real_authoring(tmp_path):
    from test_workflow_author import DraftModel, list_draft
    from workflow_support import LIST_GOAL, ListApp, _Accepting, _turns

    saved = tmp_path / "out" / "front.xlsx"
    saved.parent.mkdir(parents=True)
    app = ListApp(saved)
    exe = tmp_path / "Mes.exe"
    exe.write_bytes(b"")
    draft = DraftModel(list_draft())
    manager = RunManager(
        tmp_path / "runs", "ad.exe", str(exe), lambda _task: RunThenDraft(_turns(app, str(saved)), draft),
        lambda desktop, task, confirm, observer, asker, knowledge, captures_dir: Toolbox(
            desktop, task, confirm=confirm, sleep=lambda _s: None, observer=observer, asker=_Accepting(),
            knowledge=knowledge, captures_dir=captures_dir),
        desktop_factory=lambda: DesktopClient("ad.exe", "run1", runner=app.run),
        task_defaults={"max_steps": 60}, workflow_dir=tmp_path / "workflows", knowledge_dir=tmp_path / "knowledge",
    )

    run_id = manager.start(LIST_GOAL, None, 2, system="mes")
    manager.join(timeout=30)

    meta = run_json(tmp_path, run_id)
    assert meta["status"] == "confirmed", meta.get("reason")
    made = [event for event in run_events(tmp_path, run_id) if event["type"] == "workflow"]
    assert len(made) == 1 and made[0]["created"] is True, made
    entries = WorkflowStore(tmp_path / "workflows", "mes").entries()
    assert [entry["id"] for entry in entries] == [made[0]["id"]] == [meta["workflow"]["id"]]
    assert entries[0]["status"] == "unverified" and len(draft.calls) == 1


def test_the_default_replay_toolbox_asks_no_one_and_knows_nothing(tmp_path):
    from agent.task import Task

    toolbox = run_manager_module.default_replay_toolbox(
        DesktopClient("ad.exe", "run1", runner=FakeCli()), Task("g", str(tmp_path / "A.exe"), 2, []), None,
        tmp_path / "captures")

    assert toolbox._gated is False and isinstance(toolbox.asker, NoAsker) and toolbox.knowledge is None
    assert toolbox.can_ask is False


def test_a_replay_without_a_save_path_saves_under_its_own_run_id(tmp_path):
    workflow = stored(tmp_path)
    manager, app = workflow_manager(tmp_path)

    run_id = manager.start_workflow(workflow["id"], {}, system="mes")
    manager.join(timeout=10)

    assert run_json(tmp_path, run_id)["status"] == "verified"
    [saved] = app.saved
    assert run_id in Path(saved).name and Path(saved).parent == tmp_path / "out" and "check" not in Path(saved).name


# --- system profile: the viewer's runs and the registration screen (plan task 9, K9, K11) ----

from agent import profile as profile_module  # noqa: E402
from agent.profile import Profile, save_profile  # noqa: E402
from viewer.profile_editor import ProfileEditor  # noqa: E402

PROFILE_READY = ({"name": "생산운영"}, {"id": "56", "role": "image"})
PROFILE_ELEMENTS = ({"id": "60", "role": "image", "in": {"role": "toolbar", "id": "12"}, "name": "Execution Plan"},)


@pytest.fixture(autouse=True)
def no_real_profile_folder(tmp_path, monkeypatch):
    """No test here may read examples/llm-agent/profiles."""
    monkeypatch.setattr(profile_module, "DEFAULT_ROOT", tmp_path / "default-profiles")


class ProfileSpy:
    """A toolbox factory that takes the profile keyword and remembers it."""

    def __init__(self):
        self.calls = []

    def __call__(self, desktop, task, confirm, observer, asker, knowledge, captures_dir, **extra):
        self.calls.append({"task": task, **extra})
        return Toolbox(desktop, task, confirm=confirm, sleep=lambda _s: None, observer=observer, asker=asker,
                       knowledge=knowledge, captures_dir=captures_dir, profile=extra.get("profile"))


class RecorderSpy:
    def __init__(self, real):
        self.real = real
        self.profiles = []

    def __call__(self, runs_dir, desktop, meta, **options):
        self.profiles.append(options.get("profile"))
        return self.real(runs_dir, desktop, meta, **options)


def profiled_manager(tmp_path, monkeypatch, profile_dir=None, **settings):
    """A systems manager whose toolbox factory takes the profile; returns (manager, spy, exes)."""
    monkeypatch.setattr(run_manager_module, "run_agent", lambda *args, **kwargs: RunResult("gave_up", 0, "test"))
    exes = site_exes(tmp_path)
    defaults = {"systems": {"울산": str(exes["ulsan"]), "천안소형": str(exes["cheonan"])}, **settings}
    spy = ProfileSpy()
    manager = RunManager(tmp_path / "runs", "ad.exe", None, lambda _task: GatedModel(), spy,
                         desktop_factory=lambda: DesktopClient("ad.exe", "run1", runner=FakeCli()),
                         knowledge_dir=tmp_path / "knowledge", task_defaults=defaults,
                         profile_dir=profile_dir if profile_dir is not None else tmp_path / "profiles")
    return manager, spy, exes


def write_profile(folder, system="울산", **fields):
    values = {"ready": PROFILE_READY, "elements": PROFILE_ELEMENTS, "stable_ids": True, "ready_read": "find", **fields}
    save_profile(folder, Profile(system=system, **values))


def test_exclusive_refuses_while_a_run_is_active(tmp_path, monkeypatch):
    manager, model, _ = gated_run(tmp_path, monkeypatch)
    try:
        with pytest.raises(RunBusy):
            with manager.exclusive():
                pass
    finally:
        model.release.set()
        manager.join(timeout=5)


def test_a_run_cannot_start_while_exclusive_is_held(tmp_path, monkeypatch):
    manager, _, _ = profiled_manager(tmp_path, monkeypatch)

    with manager.exclusive():
        assert manager.active() is None, "the status keeps naming only a run id"
        with pytest.raises(RunBusy):
            manager.start("열어줘", None, 2, system="울산")
        with pytest.raises(RunBusy):
            with manager.exclusive():
                pass
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").iterdir())

    started(manager, None, 2, system="울산")
    assert len(manager_runs(tmp_path)) == 1


def manager_runs(tmp_path):
    return [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]


def test_exclusive_is_released_when_its_body_raises(tmp_path, monkeypatch):
    manager, _, _ = profiled_manager(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        with manager.exclusive():
            raise ValueError("read failed")

    with manager.exclusive():
        pass


def test_a_viewer_run_gets_the_profile_like_run_py(tmp_path, monkeypatch):
    write_profile(tmp_path / "profiles")
    recorders = RecorderSpy(run_manager_module.RunRecorder)
    monkeypatch.setattr(run_manager_module, "RunRecorder", recorders)
    after = []
    monkeypatch.setattr(run_manager_module, "after_run",
                        lambda model, result, run_dir, store, log, **options: after.append(options) or result)
    manager, spy, _ = profiled_manager(tmp_path, monkeypatch, ready_text="설정 신호", ready_timeout_s=90)

    started(manager, None, 2, system="울산")

    task = spy.calls[0]["task"]
    assert (task.ready, task.ready_read, task.ready_text, task.ready_timeout_s) == (PROFILE_READY, "find", None, 90)
    profile = spy.calls[0]["profile"]
    assert (profile.elements, profile.stable_ids) == (PROFILE_ELEMENTS, True)
    assert recorders.profiles == [profile]
    assert after == [{"use_ids": True}]


def test_a_system_without_a_profile_runs_as_before(tmp_path, monkeypatch):
    after = []
    monkeypatch.setattr(run_manager_module, "after_run",
                        lambda model, result, run_dir, store, log, **options: after.append(options) or result)
    manager, spy, _ = profiled_manager(tmp_path, monkeypatch, ready_text="설정 신호")

    started(manager, None, 2, system="울산")

    assert "profile" not in spy.calls[0], "the toolbox factory is called as before"
    assert (spy.calls[0]["task"].ready_text, spy.calls[0]["task"].ready) == ("설정 신호", ())
    assert after == [{}]


def test_a_profile_without_stable_ids_makes_workflows_without_ids(tmp_path, monkeypatch):
    write_profile(tmp_path / "profiles", stable_ids=False)
    after = []
    monkeypatch.setattr(run_manager_module, "after_run",
                        lambda model, result, run_dir, store, log, **options: after.append(options) or result)
    manager, spy, _ = profiled_manager(tmp_path, monkeypatch)

    started(manager, None, 2, system="울산")

    assert after == [{}] and spy.calls[0]["profile"].elements == PROFILE_ELEMENTS


def test_the_profile_is_read_by_the_knowledge_system_name(tmp_path, monkeypatch):
    write_profile(tmp_path / "profiles", system="천안소형")
    manager, spy, _ = profiled_manager(tmp_path, monkeypatch)

    started(manager, None, 2, system="천안소형")
    started(manager, None, 2, system="울산")

    assert spy.calls[0]["task"].ready == PROFILE_READY
    assert spy.calls[1]["task"].ready == () and "profile" not in spy.calls[1]


def test_a_broken_profile_is_an_invalid_run(tmp_path, monkeypatch):
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "울산.yaml").write_text("format: 1\nsystem: 울산\nready:\n  - {name: '비밀 글', role: ''}\n",
                                      encoding="utf-8")
    manager, spy, _ = profiled_manager(tmp_path, monkeypatch)

    with pytest.raises(InvalidRun) as raised:
        manager.start("열어줘", None, 2, system="울산")

    assert str(raised.value).startswith("시스템 프로파일을 읽지 못했습니다: ")
    assert "비밀 글" not in str(raised.value)
    assert spy.calls == [] and manager.active() is None
    assert not (tmp_path / "runs").exists() or not manager_runs(tmp_path)


def test_the_profile_folder_follows_the_argument_then_the_settings_then_the_environment(tmp_path):
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"")

    def root(**options):
        return RunManager(tmp_path / "runs", "ad.exe", str(exe), lambda _task: None, **options).profile_root

    assert root(profile_dir=tmp_path / "a", task_defaults={"profile_dir": str(tmp_path / "b")},
                environ={"AGENT_PROFILE_DIR": str(tmp_path / "c")}) == (tmp_path / "a").resolve()
    assert root(task_defaults={"profile_dir": str(tmp_path / "b")},
                environ={"AGENT_PROFILE_DIR": str(tmp_path / "c")}) == (tmp_path / "b").resolve()
    assert root(environ={"AGENT_PROFILE_DIR": str(tmp_path / "c")}) == (tmp_path / "c").resolve()
    assert root() == tmp_path / "default-profiles"


def test_the_manager_hands_the_registration_screen_its_systems_and_folder(tmp_path, monkeypatch):
    manager, _, exes = profiled_manager(tmp_path, monkeypatch)

    editor = manager.profile_editor

    assert isinstance(editor, ProfileEditor)
    assert editor._systems == {"울산": str(exes["ulsan"]), "천안소형": str(exes["cheonan"])}
    assert editor._root == (tmp_path / "profiles").resolve()
    assert manager.profile_editor is editor


def test_a_replay_reads_the_profile_too(tmp_path):
    workflow = stored(tmp_path)
    write_profile(tmp_path / "profiles", system="mes", ready=({"name": "메뉴", "role": "button"},),
                  ready_read="snapshot")
    seen = []
    manager, _ = workflow_manager(tmp_path, profile_dir=tmp_path / "profiles",
                                  task_defaults={"exe": str(tmp_path / "Mes.exe")})
    real = manager._replay_toolbox_factory

    def factory(desktop, task, observer, captures_dir, **extra):
        seen.append((task, extra))
        return real(desktop, task, observer, captures_dir)

    manager._replay_toolbox_factory = factory
    manager.start_workflow(workflow["id"], given(tmp_path), system="mes")
    manager.join(timeout=10)

    [(task, extra)] = seen
    assert extra["profile"].elements == PROFILE_ELEMENTS
    assert (task.ready, task.ready_read) == (({"name": "메뉴", "role": "button"},), "snapshot")


def test_a_broken_profile_refuses_a_replay(tmp_path):
    workflow = stored(tmp_path)
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "mes.yaml").write_text("format: 2\nsystem: mes\n", encoding="utf-8")
    manager, app = workflow_manager(tmp_path, profile_dir=folder)

    with pytest.raises(InvalidRun) as raised:
        manager.start_workflow(workflow["id"], given(tmp_path), system="mes")

    assert str(raised.value).startswith("시스템 프로파일을 읽지 못했습니다: ")
    assert app.calls == [] and manager.active() is None


def test_the_default_toolboxes_take_the_profile(tmp_path):
    from agent.task import Task

    profile = Profile(system="mes", elements=PROFILE_ELEMENTS)
    task = Task("g", str(tmp_path / "A.exe"), 2, [])
    desktop = DesktopClient("ad.exe", "run1", runner=FakeCli())

    model_run = run_manager_module.default_toolbox(desktop, task, lambda _p: False, None, NoAsker(), None,
                                                   tmp_path / "c1", profile=profile)
    replay = run_manager_module.default_replay_toolbox(desktop, task, None, tmp_path / "c2", profile=profile)

    assert model_run._profile is profile and replay._profile is profile
    assert run_manager_module.default_toolbox(desktop, task, lambda _p: False, None, NoAsker(), None,
                                              tmp_path / "c3")._profile is None


def test_a_replay_cannot_start_while_exclusive_is_held(tmp_path):
    workflow = stored(tmp_path)
    manager, app = workflow_manager(tmp_path)

    with manager.exclusive():
        with pytest.raises(RunBusy):
            manager.start_workflow(workflow["id"], given(tmp_path), system="mes")

    assert app.calls == [] and manager.active() is None
