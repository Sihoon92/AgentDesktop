import json
import re
from types import SimpleNamespace

from fake_cli import FakeCli
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseReasoningItem

from agent.baseline import run_baseline
from agent.desktop import DesktopClient
from agent.llm import OpenAIResponder
from agent.loop import run_agent
from agent.task import Task
from agent.tools import Toolbox

REQUIRE = [
    {"role": "switch", "description_contains": "설정 패널", "states": ["checked"]},
    {"role": "statictext", "name": "지표"},
]
OBSERVE = {"window_id": "w-3412692", "root_ref": None, "surface": None, "include_bounds": False}


def call(name, arguments, call_id=None):
    payload = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ResponseFunctionToolCall(
        type="function_call", name=name, arguments=payload, call_id=call_id or f"call_{name}"
    )


def text_reply(text):
    return ResponseOutputMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    )


class ScriptedModel:
    """Each turn is a function from the conversation so far to output items."""

    def __init__(self, turns, tokens_per_turn=100):
        self.turns = list(turns)
        self.tokens = tokens_per_turn
        self.inputs = []

    def respond(self, instructions, items, tools):
        self.inputs.append(list(items))
        self.instructions = instructions
        self.tools = tools
        turn = self.turns.pop(0)
        output = turn(items) if callable(turn) else turn
        return SimpleNamespace(output=output, usage=SimpleNamespace(total_tokens=self.tokens))


def last_output(items):
    outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
    return json.loads(outputs[-1]["output"])


def settings_call(items):
    observation = last_output(items)["observation"]
    ref = re.search(r'switch "설정 .*?" (@\S+)', observation).group(1)
    return [call("check", {"ref": ref})]


def setup(level=3, max_steps=12, token_budget=200_000, cli=None):
    cli = cli or FakeCli()
    task = Task("설정 패널을 연다", r"C:\apps\FootprintChart.exe", level, REQUIRE,
                max_steps=max_steps, token_budget=token_budget)
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), task,
                      confirm=lambda _p: False, sleep=lambda _s: None)
    return task, toolbox, cli


def test_full_run_opens_the_panel_and_is_verified_by_the_harness():
    task, toolbox, cli = setup()
    model = ScriptedModel(
        [
            [call("launch_target", {})],
            [call("observe", OBSERVE)],
            settings_call,
            [call("finish", {"success": True, "evidence": "opened"})],
        ]
    )

    result = run_agent(model, toolbox, task)

    assert result.status == "verified"
    assert result.steps == 4
    assert "check" in cli.command_names()


def test_model_items_including_reasoning_are_sent_back_with_matching_call_ids():
    task, toolbox, _ = setup()
    reasoning = ResponseReasoningItem(id="rs_1", type="reasoning", summary=[])
    first = call("launch_target", {}, call_id="call_abc")
    model = ScriptedModel([[reasoning, first], [call("finish", {"success": False, "evidence": "x"})]])

    run_agent(model, toolbox, task)

    second_input = model.inputs[1]
    assert reasoning in second_input
    assert first in second_input
    outputs = [i for i in second_input if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert outputs[0]["call_id"] == "call_abc"


def test_finish_claiming_success_while_the_panel_is_closed_is_rejected_and_the_run_continues():
    task, toolbox, _ = setup()
    model = ScriptedModel(
        [
            [call("launch_target", {})],
            [call("finish", {"success": True, "evidence": "trust me"})],
            lambda items: [call("finish", {"success": False, "evidence": "gave up"})]
            if last_output(items)["verified"] is False
            else [],
        ]
    )

    result = run_agent(model, toolbox, task)

    assert result.status == "gave_up"
    assert result.steps == 3


def test_finish_without_requirements_is_claimed_not_verified():
    task = Task("5분봉으로 바꿔줘", r"C:\apps\FootprintChart.exe", 3, [])
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=FakeCli()), task,
                      confirm=lambda _p: False, sleep=lambda _s: None)
    verify_calls = []
    toolbox.verify = lambda: verify_calls.append(1) or {"met": True}
    model = ScriptedModel(
        [[call("launch_target", {})], [call("finish", {"success": True, "evidence": "바꿨음"})]]
    )

    result = run_agent(model, toolbox, task)

    assert result.status == "claimed"
    assert result.reason == "바꿨음"
    assert verify_calls == []


def test_step_limit_stops_the_run():
    task, toolbox, _ = setup(max_steps=2)
    model = ScriptedModel([[call("launch_target", {})] for _ in range(5)])

    result = run_agent(model, toolbox, task)

    assert result.status == "stopped"
    assert result.steps == 2


def test_token_budget_stops_the_run():
    task, toolbox, _ = setup(token_budget=150)
    model = ScriptedModel([[call("launch_target", {})] for _ in range(5)])

    result = run_agent(model, toolbox, task)

    assert result.status == "stopped"
    assert result.steps == 2


def test_reply_without_a_tool_call_stops_the_run():
    task, toolbox, _ = setup()
    model = ScriptedModel([[text_reply("끝났습니다")]])

    result = run_agent(model, toolbox, task)

    assert result.status == "stopped"


def test_malformed_arguments_go_back_to_the_model_without_touching_the_app():
    task, toolbox, cli = setup()
    model = ScriptedModel(
        [
            [call("launch_target", "{not json")],
            lambda items: [call("finish", {"success": False, "evidence": "bad"})]
            if last_output(items)["ok"] is False
            else [],
        ]
    )

    result = run_agent(model, toolbox, task)

    assert result.status == "gave_up"
    assert cli.calls == []


def test_repeating_the_same_action_stops_the_run():
    task, toolbox, cli = setup()

    def click_settings(items):
        observation = last_output(items)["observation"]
        ref = re.search(r'switch "설정 .*?" (@\S+)', observation).group(1)
        return [call("click", {"ref": ref})]

    model = ScriptedModel(
        [[call("launch_target", {})], [call("observe", OBSERVE)]] + [click_settings] * 8
    )

    result = run_agent(model, toolbox, task)

    assert result.status == "stopped"
    assert cli.command_names().count("click") == 3


def test_tool_exposed_to_the_model_respects_the_permission_level():
    task, toolbox, _ = setup(level=0)
    model = ScriptedModel([[call("finish", {"success": False, "evidence": "x"})]])

    run_agent(model, toolbox, task)

    names = {tool["name"] for tool in model.tools}
    assert "press" not in names
    assert "observe" in names


def test_responder_sends_the_conversation_with_one_tool_call_per_turn():
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return "response"

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    responder = OpenAIResponder("gpt-6.1-terra", "low", client=client)

    assert responder.respond("rules", [{"role": "user", "content": "go"}], [{"name": "t"}]) == "response"
    assert sent["model"] == "gpt-6.1-terra"
    assert sent["instructions"] == "rules"
    assert sent["input"] == [{"role": "user", "content": "go"}]
    assert sent["tools"] == [{"name": "t"}]
    assert sent["parallel_tool_calls"] is False
    assert sent["reasoning"] == {"effort": "low"}


def test_baseline_opens_the_panel_without_a_model():
    cli = FakeCli()
    task = Task("g", r"C:\apps\FootprintChart.exe", 3, REQUIRE,
                baseline_target={"role": "switch", "description_contains": "설정 패널"})
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), task,
                      confirm=lambda _p: False, sleep=lambda _s: None)

    result = run_baseline(toolbox, task)

    assert result.status == "verified"
    assert cli.command_names().count("check") == 1


def test_baseline_reports_failure_when_the_element_never_appears():
    cli = FakeCli()
    task = Task("g", r"C:\apps\FootprintChart.exe", 3, REQUIRE,
                baseline_target={"role": "button", "name": "없는 버튼"})
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), task,
                      confirm=lambda _p: False, sleep=lambda _s: None, clock=lambda: next(clock))

    result = run_baseline(toolbox, task, timeout_s=5)

    assert result.status == "stopped"
    assert "check" not in cli.command_names()


def test_prompt_sends_switches_and_checkboxes_to_toggle_not_click():
    from agent.loop import SYSTEM_PROMPT

    rule = " ".join(
        line for line in SYSTEM_PROMPT.replace("\n  ", " ").splitlines() if "switch" in line and "checkbox" in line
    )

    assert "toggle" in rule and "check" in rule
    assert "click" in rule and "button" in rule
    assert "Prefer semantic tools (click" not in SYSTEM_PROMPT


def test_click_and_toggle_descriptions_point_switches_to_toggle():
    from agent.tool_schemas import SCHEMAS

    describe = {s["definition"]["name"]: s["definition"]["description"] for s in SCHEMAS}

    assert "switch" in describe["click"] and "toggle" in describe["click"]
    assert "switch" in describe["toggle"] and "checkbox" in describe["toggle"]
