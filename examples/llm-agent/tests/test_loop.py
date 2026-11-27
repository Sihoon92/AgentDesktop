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
        self.efforts = []

    def respond(self, instructions, items, tools, effort=None):
        self.inputs.append(list(items))
        self.efforts.append(effort)
        self.instructions = instructions
        self.tools = tools
        turn = self.turns.pop(0)
        output = turn(items) if callable(turn) else turn
        return SimpleNamespace(output=output, usage=SimpleNamespace(total_tokens=self.tokens))


def last_output(items):
    outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
    return json.loads(outputs[-1]["output"])


PLAN = [call("plan", {"stages": [{"goal": "설정 패널이 열려 있다", "done_when": "지표가 보인다"}]})]
STAGE_DONE = [call("stage_done", {"marks": [1], "capture": None, "note": "설정 패널이 열림"})]


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
            PLAN,
            [call("observe", OBSERVE)],
            settings_call,
            STAGE_DONE,
            [call("finish", {"success": True, "evidence": "opened"})],
        ]
    )

    result = run_agent(model, toolbox, task)

    assert result.status == "verified"
    assert result.steps == 6
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
    assert result.steps == 3
    assert [tool["name"] for tool in model.tools] == ["finish"]


def test_token_budget_stops_the_run():
    task, toolbox, _ = setup(token_budget=150)
    model = ScriptedModel([[call("launch_target", {})] for _ in range(5)])

    result = run_agent(model, toolbox, task)

    assert result.status == "stopped"
    assert result.steps == 3
    assert [tool["name"] for tool in model.tools] == ["finish"]


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
        [[call("launch_target", {})], PLAN, [call("observe", OBSERVE)]] + [click_settings] * 8
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


def test_responder_uses_a_given_effort_for_that_call_only():
    sent = []
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: sent.append(kwargs["reasoning"])))
    responder = OpenAIResponder("gpt-6.1-terra", "low", client=client)

    responder.respond("rules", [], [], effort="medium")
    responder.respond("rules", [], [])

    assert sent == [{"effort": "medium"}, {"effort": "low"}]


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


import time  # noqa: E402

import pytest  # noqa: E402
from test_tools import LIST_WINDOW, SCENE  # noqa: E402

from agent.approval import Decision  # noqa: E402
from agent.ask import NO_ANSWER, Answer, NoAsker  # noqa: E402
from agent.knowledge import KnowledgeStore  # noqa: E402
from agent.loop import SYSTEM_PROMPT  # noqa: E402

SCENE_EXE = r"C:\apps\Scene.exe"
BASE_TOKENS = 100


class ScriptedAsker:
    interactive = True

    def __init__(self, answers):
        self.answers = list(answers)
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return self.answers.pop(0)


class RecordingGate:
    def __init__(self, approve=True):
        self.approve = approve
        self.proposals = []

    def review(self, proposal):
        self.proposals.append(proposal)
        return Decision(self.approve, "")


def free_task(**overrides):
    return Task("조회 결과를 엑셀로 저장해줘", SCENE_EXE, 2, [], **overrides)


def scene_toolbox(tmp_path, asker=None, knowledge=None, task=None):
    cli = FakeCli(scene=SCENE)
    cli.on_click = lambda x, y: cli.open_window(LIST_WINDOW) if x > 1180 else cli.focus(mark=3)
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), task or free_task(),
                      confirm=lambda _p: False, sleep=lambda _s: None, asker=asker, knowledge=knowledge,
                      captures_dir=tmp_path / "captures", wall=lambda: time.time() - 60)
    return toolbox


def finish(success=True, evidence="저장함", saved_path=None, completion_id=None):
    return [call("finish", {"success": success, "evidence": evidence, "saved_path": saved_path,
                            "completion_id": completion_id})]


GIVE_UP = finish(success=False, evidence="못 함")


def store_with(tmp_path, *entries):
    store = KnowledgeStore(tmp_path / "knowledge", "scene")
    return store, [store.add(entry) for entry in entries]


RESOLUTION = {"type": "resolution", "situation": "선택 칸이 가운데 클릭으로 안 열린다",
              "resolution": "선택 칸은 오른쪽 끝을 눌러 연다"}
FILE_COMPLETION = {"type": "completion", "task": "엑셀 저장", "checks": [{"kind": "file", "pattern": "*.xlsx"}]}


def first_user_text(model):
    return model.inputs[0][0]["content"]


def user_parts(items):
    return [part for item in items if isinstance(item, dict) and item.get("role") == "user"
            and isinstance(item.get("content"), list) for part in item["content"]]


def saved_file(tmp_path, name="out.xlsx"):
    path = tmp_path / name
    path.write_bytes(b"x")
    return str(path)


def stuck_question():
    return {"kind": "stuck", "text": "무엇을 눌러야 하나요?", "proposed": None, "basis": [],
            "candidates": [{"label": "라인 오른쪽", "mark": 3, "capture": None, "x": None, "y": None}]}


def test_first_message_carries_the_goal_and_the_briefing(tmp_path):
    store, (entry,) = store_with(tmp_path, RESOLUTION)
    model = ScriptedModel([GIVE_UP])

    run_agent(model, scene_toolbox(tmp_path, knowledge=store), free_task())

    text = first_user_text(model)
    assert text.startswith(free_task().goal) and entry["resolution"] in text and entry["id"] in text


def test_without_knowledge_the_first_message_is_the_goal():
    task, toolbox, _ = setup()
    model = ScriptedModel([[call("finish", {"success": False, "evidence": "x"})]])
    run_agent(model, toolbox, task)
    assert model.inputs[0] == [{"role": "user", "content": task.goal}]


def test_capture_reaches_the_model_as_an_image_message(tmp_path):
    model = ScriptedModel([[call("launch_target", {})], [call("look", {"mark": None})], GIVE_UP])

    run_agent(model, scene_toolbox(tmp_path), free_task())

    assert any(part.get("type") == "input_image" for part in user_parts(model.inputs[-1]))


def test_tool_output_image_mode_puts_the_image_in_the_output(tmp_path):
    task = free_task(image_mode="tool_output")
    model = ScriptedModel([[call("launch_target", {})], [call("look", {"mark": None})], GIVE_UP])

    run_agent(model, scene_toolbox(tmp_path, task=task), task)

    output = [i for i in model.inputs[-1] if isinstance(i, dict) and i.get("type") == "function_call_output"][-1]
    assert [part["type"] for part in output["output"]] == ["input_text", "input_image"]


def test_ask_only_turns_do_not_use_steps(tmp_path):
    turns = [[call("launch_target", {})], [call("observe", OBSERVE_SCENE)]]
    turns += [[call("ask", stuck_question())]] * 5 + [GIVE_UP]
    task = free_task(max_steps=3)
    asker = ScriptedAsker([Answer(text="1번")] * 5)

    result = run_agent(ScriptedModel(turns), scene_toolbox(tmp_path, asker, task=task), task)

    assert result.status == "gave_up" and result.steps == 8


def test_turns_that_also_act_still_use_steps(tmp_path):
    turns = [[call("launch_target", {})]] + [[call("ask", stuck_question()), call("list_windows", {})]] * 3
    task = free_task(max_steps=2)

    result = run_agent(ScriptedModel(turns), scene_toolbox(tmp_path, ScriptedAsker([Answer("1")] * 3), task=task),
                       task)

    assert result.status == "stopped" and result.reason == "Step limit reached"


def test_question_events_carry_the_model_turn(tmp_path):
    asker = ScriptedAsker([Answer(text="1번")])
    turns = [[call("launch_target", {})], [call("observe", OBSERVE_SCENE)], [call("ask", stuck_question())], GIVE_UP]
    run_agent(ScriptedModel(turns), scene_toolbox(tmp_path, asker), free_task())
    assert asker.questions[0]["step"] == 3


def test_finish_with_a_completion_entry_is_verified_by_the_file(tmp_path):
    store, (entry,) = store_with(tmp_path, FILE_COMPLETION)
    toolbox = scene_toolbox(tmp_path, ScriptedAsker([]), store)
    model = ScriptedModel([finish(saved_path=saved_file(tmp_path), completion_id=entry["id"])])

    result = run_agent(model, toolbox, free_task())

    assert result.status == "verified"
    assert toolbox.finish_note == {"goal": free_task().goal, "evidence": "저장함",
                                   "saved_path": saved_file(tmp_path), "by": "harness"}


def test_unmet_completion_goes_back_to_the_model(tmp_path):
    store, (entry,) = store_with(tmp_path, FILE_COMPLETION)
    missing = str(tmp_path / "missing.xlsx")
    model = ScriptedModel([finish(saved_path=missing, completion_id=entry["id"]), GIVE_UP])

    result = run_agent(model, scene_toolbox(tmp_path, ScriptedAsker([]), store), free_task())

    assert result.status == "gave_up"
    output = last_output(model.inputs[-1])
    assert output["verified"] is False and "missing.xlsx" in output["unmet"]


def test_human_confirmation_gives_confirmed(tmp_path):
    asker = ScriptedAsker([Answer(text="", accepted=True)])
    toolbox = scene_toolbox(tmp_path, asker)

    result = run_agent(ScriptedModel([finish(saved_path=saved_file(tmp_path, "a.xlsx"))]), toolbox, free_task())

    assert result.status == "confirmed"
    assert asker.questions[0]["kind"] == "completion"
    assert "저장함" in asker.questions[0]["text"] and "a.xlsx" in asker.questions[0]["text"]
    assert toolbox.finish_note["by"] == "human" and toolbox.finish_note["saved_path"].endswith("a.xlsx")


def test_human_rejection_returns_the_comment_to_the_model(tmp_path):
    model = ScriptedModel([finish(), GIVE_UP])
    asker = ScriptedAsker([Answer(text="파일이 비었어", accepted=False)])

    result = run_agent(model, scene_toolbox(tmp_path, asker), free_task())

    assert result.status == "gave_up"
    assert last_output(model.inputs[-1]) == {"confirmed": False, "comment": "파일이 비었어"}


def test_no_one_to_ask_stays_claimed(tmp_path):
    toolbox = scene_toolbox(tmp_path, NoAsker())
    assert run_agent(ScriptedModel([finish()]), toolbox, free_task()).status == "claimed"
    assert toolbox.finish_note["by"] == "harness"


def test_task_requirements_still_decide_first():
    task, toolbox, _ = setup()
    model = ScriptedModel([[call("launch_target", {})], PLAN, [call("observe", OBSERVE)], settings_call,
                           STAGE_DONE,
                           [call("finish", {"success": True, "evidence": "opened", "saved_path": None,
                                            "completion_id": "c-unknown"})]])
    assert run_agent(model, toolbox, task).status == "verified"


def test_timed_out_completion_question_stops_the_run(tmp_path):
    result = run_agent(ScriptedModel([finish()]), scene_toolbox(tmp_path, ScriptedAsker([NO_ANSWER])), free_task())
    assert result.status == "stopped"


def run_to(tmp_path, ending, register):
    store, (entry,) = store_with(tmp_path, FILE_COMPLETION)
    asker = ScriptedAsker([Answer(text="", accepted=True)])
    turns = {
        "verified": [finish(saved_path=saved_file(tmp_path), completion_id=entry["id"])],
        "confirmed": [finish()],
        "claimed": [finish()],
        "gave_up": [GIVE_UP],
        "stopped": [[text_reply("끝")]],
    }[ending]
    toolbox = scene_toolbox(tmp_path, NoAsker() if ending == "claimed" else asker, store)
    log = []
    result = run_agent(ScriptedModel(turns, tokens_per_turn=BASE_TOKENS), toolbox, free_task(),
                       log=log.append, register=register)
    assert result.status == ending
    return result, log


@pytest.mark.parametrize("ending,called", [
    ("verified", 1), ("confirmed", 1), ("claimed", 0), ("gave_up", 0), ("stopped", 0)])
def test_register_runs_only_after_a_checked_success(tmp_path, ending, called):
    calls = []
    run_to(tmp_path, ending, register=lambda model, toolbox, log: calls.append(1) or 50)
    assert len(calls) == called


def test_register_tokens_are_added_and_its_failure_does_not_change_the_result(tmp_path):
    def raising(*_args):
        raise RuntimeError("등록 실패")

    assert run_to(tmp_path / "a", "verified", register=lambda *_: 50)[0].tokens == BASE_TOKENS + 50
    result, log = run_to(tmp_path / "b", "verified", register=raising)
    assert (result.status, result.tokens) == ("verified", BASE_TOKENS)
    assert any(event["type"] == "warning" and "등록 실패" in event["message"] for event in log)


def test_policy_refusal_comes_before_the_approval_prompt(tmp_path):
    gate = RecordingGate(approve=True)
    click = {"window_id": "w-1", "x": 1050, "y": 600, "button": "left", "intent": "누른다",
             "expect": "any", "knowledge_id": None}
    model = ScriptedModel([[call("launch_target", {})], PLAN, [call("mouse_click", click)], GIVE_UP])

    run_agent(model, scene_toolbox(tmp_path, ScriptedAsker([])), free_task(), gate=gate)

    assert [p["tool"] for p in gate.proposals] == ["launch_target"]
    assert last_output(model.inputs[-1])["refused_code"] == "needs_confirmation"


def test_confirmed_action_still_goes_through_approval(tmp_path):
    gate = RecordingGate(approve=True)
    confirm = {"kind": "confirm", "text": "여기 맞나요?", "proposed": 1, "basis": [],
               "candidates": [{"label": "찍은 곳", "mark": None, "capture": "c1", "x": 10, "y": 20}]}
    click = {"capture": "c1", "x": 10, "y": 20, "action": "click", "intent": "누른다", "expect": "any",
             "knowledge_id": None}
    model = ScriptedModel([[call("launch_target", {})], [call("observe", OBSERVE_SCENE)], PLAN,
                           [call("look", {"mark": None})], [call("ask", confirm)],
                           [call("click_point", click)], GIVE_UP])

    run_agent(model, scene_toolbox(tmp_path, ScriptedAsker([Answer(text="맞아")])), free_task(), gate=gate)

    assert [p["tool"] for p in gate.proposals] == ["launch_target", "click_point"]


def test_system_prompt_teaches_the_new_tools():
    for word in ("[n]", "click_part", "expect", "expected_met", "find_cells", "ask", "click_point",
                 "knowledge_id", "saved_path", "completion_id"):
        assert word in SYSTEM_PROMPT


OBSERVE_SCENE = {"window_id": None, "root_ref": None, "surface": None, "include_bounds": False}


def test_finish_schema_takes_a_saved_path_and_a_completion_id():
    from agent.tool_schemas import SCHEMAS

    finish_tool = next(s["definition"] for s in SCHEMAS if s["definition"]["name"] == "finish")
    properties = finish_tool["parameters"]["properties"]
    assert properties["saved_path"]["type"] == ["string", "null"]
    assert properties["completion_id"]["type"] == ["string", "null"]
    assert sorted(finish_tool["parameters"]["required"]) == sorted(properties)



def completions(log):
    return [event for event in log if event.get("type") == "completion"]


def test_task_requirements_leave_a_completion_event():
    task, toolbox, _ = setup()
    log = []
    model = ScriptedModel([[call("launch_target", {})], [call("finish", {"success": True, "evidence": "early"})],
                           PLAN, [call("observe", OBSERVE)], settings_call, STAGE_DONE,
                           [call("finish", {"success": True, "evidence": "opened"})]])

    assert run_agent(model, toolbox, task, log=log.append).status == "verified"

    unmet, met = completions(log)
    assert (unmet["by"], unmet["id"], unmet["met"]) == ("task", None, False)
    assert "설정 패널" in unmet["detail"]
    assert (met["by"], met["id"], met["met"]) == ("task", None, True)


@pytest.mark.parametrize("answer,met", [(Answer(text="좋아", accepted=True), True),
                                        (Answer(text="파일이 비었어", accepted=False), False)])
def test_a_person_confirming_without_a_completion_entry_leaves_a_completion_event(tmp_path, answer, met):
    log = []
    turns = [finish()] + ([] if met else [GIVE_UP])
    run_agent(ScriptedModel(turns), scene_toolbox(tmp_path, ScriptedAsker([answer])), free_task(), log=log.append)

    [event] = completions(log)
    assert event == {"type": "completion", "by": "human", "id": None, "met": met, "detail": answer.text}


def test_a_claimed_run_leaves_no_completion_event(tmp_path):
    log = []
    run_agent(ScriptedModel([finish()]), scene_toolbox(tmp_path, NoAsker()), free_task(), log=log.append)
    assert completions(log) == []


def finish_notes(log):
    return [event for event in log if event.get("type") == "finish_note"]


def test_a_successful_finish_logs_the_saved_path(tmp_path):
    saved = saved_file(tmp_path, "a.xlsx")
    log = []

    result = run_agent(ScriptedModel([finish(saved_path=saved)]),
                       scene_toolbox(tmp_path, ScriptedAsker([Answer(text="", accepted=True)])), free_task(),
                       log=log.append)

    assert result.status == "confirmed"
    assert finish_notes(log) == [{"type": "finish_note", "by": "human", "saved_path": saved}]


def test_a_finish_note_without_a_saved_path_holds_null(tmp_path):
    log = []

    run_agent(ScriptedModel([finish()]), scene_toolbox(tmp_path, ScriptedAsker([Answer(accepted=True)])),
              free_task(), log=log.append)

    assert finish_notes(log) == [{"type": "finish_note", "by": "human", "saved_path": None}]


def test_a_completion_entry_logs_the_finish_note_by_the_harness(tmp_path):
    store, (entry,) = store_with(tmp_path, FILE_COMPLETION)
    saved = saved_file(tmp_path)
    log = []

    run_agent(ScriptedModel([finish(saved_path=saved, completion_id=entry["id"])]),
              scene_toolbox(tmp_path, ScriptedAsker([]), store), free_task(), log=log.append)

    assert finish_notes(log) == [{"type": "finish_note", "by": "harness", "saved_path": saved}]


def test_a_refused_finish_logs_no_finish_note(tmp_path):
    log = []
    model = ScriptedModel([finish(saved_path=r"C:\out\a.xlsx"), GIVE_UP])

    result = run_agent(model, scene_toolbox(tmp_path, ScriptedAsker([Answer(text="아님", accepted=False)])),
                       free_task(), log=log.append)

    assert result.status == "gave_up"
    assert finish_notes(log) == []


def test_giving_up_logs_no_finish_note(tmp_path):
    log = []

    run_agent(ScriptedModel([GIVE_UP]), scene_toolbox(tmp_path, ScriptedAsker([])), free_task(), log=log.append)

    assert finish_notes(log) == []


def test_a_claimed_finish_logs_the_finish_note_by_the_harness(tmp_path):
    log = []

    result = run_agent(ScriptedModel([finish(saved_path=r"C:\out\a.xlsx")]), scene_toolbox(tmp_path, NoAsker()),
                       free_task(), log=log.append)

    assert result.status == "claimed"
    assert finish_notes(log) == [{"type": "finish_note", "by": "harness", "saved_path": r"C:\out\a.xlsx"}]


def test_the_system_prompt_explains_id_and_alias():
    """id= is the app's value that tells unnamed elements apart; alias= is a name an administrator
    gave a picture-only element, read as its name but checked with look or ask when the screen
    disagrees; an element without a ref is pressed with click_part. No example value is given."""
    import re

    bullets = SYSTEM_PROMPT.replace("\n  ", " ").splitlines()
    [about_id] = [line for line in bullets if 'id="' in line and 'alias="' not in line]
    [about_alias] = [line for line in bullets if 'alias="' in line]

    assert "name" in about_id and "app" in about_id
    for word in ("name", "look", "ask", "click_part", "ref"):
        assert word in about_alias, word
    assert re.search(r'(id|alias)="[^".…]', SYSTEM_PROMPT) is None, "the prompt quotes no example value"


class ProgressObserver:
    def __init__(self, path):
        from agent.progress import ProgressLog

        self.progress = ProgressLog(path)

    def on_observation(self, *args, **kwargs):
        pass

    def on_action(self, *args, **kwargs):
        pass


def test_model_calls_and_tool_calls_are_items_with_steps(tmp_path):
    cli = FakeCli()
    task = Task("설정 패널을 연다", r"C:\apps\FootprintChart.exe", 3, REQUIRE)
    observer = ProgressObserver(tmp_path / "p.jsonl")
    desktop = DesktopClient("ad.exe", "run1", runner=cli)
    desktop.set_progress(observer.progress)
    toolbox = Toolbox(desktop, task, confirm=lambda _p: False, sleep=lambda _s: None, observer=observer)
    model = ScriptedModel([[call("launch_target", {"intent": "연다"})], [GIVE_UP[0]]])

    run_agent(model, toolbox, task)

    records = [json.loads(line) for line in (tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines()]
    starts = [r for r in records if r["phase"] == "start" and r["kind"] in ("model", "tool")]
    assert [(r["kind"], r.get("name"), r.get("step")) for r in starts] == [
        ("model", None, 1), ("tool", "launch_target", 1), ("model", None, 2), ("tool", "finish", 2)]
    launch = starts[1]
    assert launch["intent"] == "연다" and "parent" not in launch
    assert [r for r in records if r["kind"] == "cli" and r["phase"] == "start"][0]["parent"] == launch["id"]
    model_end = next(r for r in records if r["phase"] == "end" and r["id"] == starts[0]["id"])
    assert model_end["tokens"] == 100
    tools = [r for r in records if r["phase"] == "start" and r["kind"] == "tool"]
    assert len(tools) == 2, "Toolbox.call does not open a second item inside the loop's"


def test_system_prompt_says_what_children_n_means():
    assert "- children=N on a line means N children were not read." in SYSTEM_PROMPT
