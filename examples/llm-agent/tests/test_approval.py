import re
import threading

from fake_cli import FakeCli
from test_loop import OBSERVE, PLAN, STAGE_DONE, ScriptedModel, call, last_output

from agent.approval import NO_ANSWER, ApprovalGate, Decision, TerminalGate
from agent.desktop import DesktopClient
from agent.loop import run_agent
from agent.task import Task
from agent.tools import Toolbox

REQUIRE = [
    {"role": "switch", "description_contains": "설정 패널", "states": ["checked"]},
    {"role": "statictext", "name": "지표"},
]


class ScriptedGate:
    """Approves every proposal except the tools named in `reject`."""

    def __init__(self, reject=None):
        self.reject = reject or {}
        self.proposals = []

    def review(self, proposal):
        self.proposals.append(proposal)
        if proposal["tool"] in self.reject:
            return Decision(False, self.reject[proposal["tool"]])
        return Decision(True, "")


def setup():
    cli = FakeCli()
    task = Task("설정 패널을 연다", r"C:\apps\FootprintChart.exe", 3, REQUIRE)
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), task,
                      confirm=lambda _p: False, sleep=lambda _s: None)
    return task, toolbox, cli


def check_settings(items):
    ref = re.search(r'switch "설정 .*?" (@\S+)', last_output(items)["observation"]).group(1)
    return [call("check", {"ref": ref, "intent": "설정 패널을 연다"})]


def opening_model(*after_check):
    return ScriptedModel(
        [
            [call("launch_target", {"intent": "앱을 연다"})],
            PLAN,
            [call("observe", OBSERVE)],
            check_settings,
            *after_check,
        ]
    )


def test_rejected_action_never_reaches_the_app_and_the_model_hears_why():
    task, toolbox, cli = setup()
    heard = []

    def give_up(items):
        heard.append(last_output(items))
        return [call("finish", {"success": False, "evidence": "rejected"})]

    events = []
    result = run_agent(opening_model(give_up), toolbox, task, events.append,
                       gate=ScriptedGate(reject={"check": "아직 안 돼"}))

    assert "check" not in cli.command_names()
    assert heard[0]["ok"] is False
    assert heard[0]["refused"] == "사용자가 거부함: 아직 안 돼"
    assert result.status == "gave_up"
    decisions = [e for e in events if e.get("type") == "decision"]
    assert decisions[-1]["approved"] is False
    assert decisions[-1]["comment"] == "아직 안 돼"


def test_approved_action_runs_once():
    task, toolbox, cli = setup()
    gate = ScriptedGate()
    events = []

    result = run_agent(opening_model(STAGE_DONE, [call("finish", {"success": True, "evidence": "opened"})]),
                       toolbox, task, events.append, gate=gate)

    assert result.status == "verified"
    assert cli.command_names().count("check") == 1
    check = next(p for p in gate.proposals if p["tool"] == "check")
    assert check["type"] == "proposal"
    assert check["step"] == 4
    assert check["intent"] == check["arguments"]["intent"]
    assert check["target"]["name"] == "설정 ◂"
    proposal_event = next(e for e in events if e.get("type") == "proposal" and e["tool"] == "check")
    decision_event = next(e for e in events if e.get("type") == "decision" and e["id"] == proposal_event["id"])
    assert decision_event == {"type": "decision", "id": proposal_event["id"], "approved": True, "comment": ""}


def test_observation_tools_are_not_gated():
    task, toolbox, _ = setup()
    gate = ScriptedGate()
    model = ScriptedModel(
        [
            [call("launch_target", {"intent": "앱을 연다"})],
            [call("list_windows", {})],
            [call("observe", OBSERVE)],
            [call("wait_for_text", {"text": "설정", "timeout_ms": 0})],
            [call("finish", {"success": False, "evidence": "done"})],
        ]
    )

    run_agent(model, toolbox, task, gate=gate)

    assert [p["tool"] for p in gate.proposals] == ["launch_target"]


def test_unanswered_proposal_times_out_as_rejection():
    task, toolbox, cli = setup()
    ticks = iter([0.0, 601.0])
    gate = ApprovalGate(timeout_s=600, clock=lambda: next(ticks))
    model = ScriptedModel([[call("launch_target", {"intent": "앱을 연다"})]])
    events = []

    result = run_agent(model, toolbox, task, events.append, gate=gate)

    assert result.status == "stopped"
    assert cli.calls == []
    decision = next(e for e in events if e.get("type") == "decision")
    assert decision["approved"] is False
    assert decision["comment"] == "응답 없음"


def test_decision_for_unknown_proposal_is_ignored():
    gate = ApprovalGate()

    assert gate.decide("p404", Decision(True, "")) is False


def test_decision_from_another_thread_releases_the_review():
    gate = ApprovalGate(timeout_s=5)
    answers = []
    reviewer = threading.Thread(target=lambda: answers.append(gate.review({"id": "p1"})))
    reviewer.start()

    accepted = False
    for _ in range(500):
        accepted = gate.decide("p1", Decision(False, "그만"))
        if accepted:
            break
        threading.Event().wait(0.01)
    reviewer.join(timeout=5)

    assert accepted is True
    assert answers == [Decision(False, "그만")]
    assert gate.decide("p1", Decision(True, "")) is False


def test_terminal_gate_asks_and_keeps_the_reason():
    shown = []
    answers = iter(["y", "n 너무 위험해", ""])
    gate = TerminalGate(ask=lambda _prompt: next(answers), show=shown.append)
    proposal = {"id": "p1", "step": 1, "tool": "check", "intent": "연다", "arguments": {}, "target": None}

    assert gate.review(proposal) == Decision(True, "")
    assert gate.review(proposal) == Decision(False, "너무 위험해")
    assert gate.review(proposal).approved is False
    assert "check" in shown[0] and "연다" in shown[0]


def test_terminal_gate_without_input_rejects():
    def closed(_prompt):
        raise EOFError

    gate = TerminalGate(ask=closed, show=lambda _text: None)

    assert gate.review({"id": "p1", "step": 1, "tool": "check", "intent": "", "arguments": {}}).approved is False


def test_closed_approval_gate_answers_no_answer_now_and_later():
    gate = ApprovalGate(timeout_s=600)
    answers = []
    reviewer = threading.Thread(target=lambda: answers.append(gate.review({"id": "p1"})), daemon=True)
    reviewer.start()
    for _ in range(500):
        if "p1" in gate._waiting:
            break
        threading.Event().wait(0.01)

    gate.close()
    reviewer.join(timeout=5)

    assert not reviewer.is_alive()
    assert answers == [NO_ANSWER]
    later = []
    second = threading.Thread(target=lambda: later.append(gate.review({"id": "p2"})), daemon=True)
    second.start()
    second.join(timeout=5)
    assert later == [NO_ANSWER]
    assert gate.decide("p1", Decision(True, "")) is False
    assert gate.decide("p2", Decision(True, "")) is False
