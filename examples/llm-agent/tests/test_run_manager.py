import json
import threading
from types import SimpleNamespace

import pytest
from fake_cli import FakeCli
from test_loop import ScriptedModel, call

from agent.approval import ApprovalGate
from agent.desktop import DesktopClient
from agent.tools import Toolbox
from viewer import run_manager as run_manager_module
from viewer.run_manager import InvalidRun, RunBusy, RunManager


class GatedModel:
    """Finishes with success once `release` is set, so a test can look at a run mid-flight."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    def respond(self, instructions, items, tools):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test never released the model")
        output = [call("finish", {"success": True, "evidence": "done"})]
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
        lambda desktop, task, confirm, observer: Toolbox(
            desktop, task, confirm=confirm, sleep=lambda _s: None, observer=observer
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
            [call("finish", {"success": True, "evidence": "done"})],
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
    assert final["status"] == "claimed"
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
    assert run_json(tmp_path, run_id)["status"] == "claimed"


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
                     "started_by": "viewer"}]


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
