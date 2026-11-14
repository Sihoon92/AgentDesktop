"""The loop with stages: the plan, stage_done, per-stage call limits, the limit question, the
trimmed transcript and the last turn at the ceiling. The model is a script; the app is FakeCli."""

import json

import pytest
from test_loop import (
    GIVE_UP,
    OBSERVE_SCENE,
    RecordingGate,
    ScriptedModel,
    call,
    finish,
    free_task,
    last_output,
    scene_toolbox,
    stuck_question,
    text_reply,
)

from agent.ask import NO_ANSWER, Answer, NoAsker
from agent.loop import SYSTEM_PROMPT, run_agent

LAUNCH = [call("launch_target", {}, "c-launch")]
OBSERVE = [call("observe", OBSERVE_SCENE, "c-observe")]
CLICK = {"ref": "@s1:e1", "intent": "조회를 누른다", "expect": "any", "knowledge_id": None}
STAGES = [{"goal": "조회 버튼이 보인다", "done_when": "버튼이 관찰에 있다"},
          {"goal": "조회 결과가 나와 있다", "done_when": "결과 칸에 값이 있다"},
          {"goal": "결과를 확인했다", "done_when": "양품수가 보인다"}]
FULL_OBSERVATION = '[2] button "조회" @s1:e1'
NOTE = "조회 버튼이 관찰의 [2]에 있다"

_ids = iter(range(10_000))


def turn(name, arguments):
    return [call(name, arguments, f"c{next(_ids)}")]


def plan(count=3, stages=None):
    return turn("plan", {"stages": stages or STAGES[:count]})


def stage_done(marks=(2,), capture=None, note=NOTE):
    return turn("stage_done", {"marks": None if marks is None else list(marks), "capture": capture, "note": note})


def windows():
    return turn("list_windows", {})


def click():
    return turn("click", CLICK)


def look():
    return turn("look", {"mark": None})


class Asker:
    """Answers in order; an answer may be a function of the question. Remembers how many model
    calls had happened when each question came."""

    interactive = True

    def __init__(self, model, *answers):
        self.model = model
        self.answers = list(answers)
        self.questions = []
        self.calls_before = []

    def ask(self, question):
        self.questions.append(question)
        self.calls_before.append(len(self.model.inputs))
        answer = self.answers.pop(0) if self.answers else NO_ANSWER
        return answer(question) if callable(answer) else answer


def run(turns, task=None, answers=(), asker=None, gate=None, register=None, tokens=100):
    model = ScriptedModel(turns, tokens_per_turn=tokens)
    asker = asker if asker is not None else Asker(model, *answers)
    if isinstance(asker, Asker):
        asker.model = model
    task = task or free_task()
    toolbox = scene_toolbox(_tmp(), asker=asker, task=task)
    log = []
    toolbox._observer.on_event = log.append
    result = run_agent(model, toolbox, task, log=log.append, gate=gate, register=register)
    return result, model, toolbox, log, asker


_tmp_root = []


def _tmp():
    return _tmp_root[-1]


@pytest.fixture(autouse=True)
def _tmp_dir(tmp_path):
    _tmp_root.append(tmp_path)
    yield
    _tmp_root.pop()


def events(log, kind):
    return [event for event in log if event.get("type") == kind]


def outputs(items):
    return [item for item in items if isinstance(item, dict) and item.get("type") == "function_call_output"]


def output_of(model, turn_index):
    """The tool output the model saw for the call it made at turn_index (0-based)."""
    return last_output(model.inputs[turn_index + 1])


def eight_calls():
    return [click()] + [windows() for _ in range(7)]


def to_the_limit(count=3):
    return [LAUNCH, OBSERVE, plan(count)] + eight_calls()


# Before the plan

def test_an_app_changing_call_before_a_plan_is_refused():
    result, model, toolbox, _, _ = run([LAUNCH, OBSERVE, click(), GIVE_UP])

    refusal = output_of(model, 2)
    assert refusal["refused_code"] == "needs_plan" and "plan" in refusal["refused"]
    assert result.status == "gave_up"


def test_launch_observe_and_look_run_before_a_plan():
    _, model, _, _, _ = run([LAUNCH, OBSERVE, look(), windows(), GIVE_UP])

    for index in range(4):
        assert "refused" not in output_of(model, index)


def test_a_run_without_any_plan_ends_at_the_call_ceiling():
    task = free_task(max_steps=5)
    result, model, _, _, _ = run([LAUNCH] + [click() for _ in range(20)], task=task)

    assert result.status == "stopped" and result.reason == "Step limit reached"
    assert len(model.inputs) == 6
    assert all(output["refused_code"] == "needs_plan"
               for output in map(json.loads, (o["output"] for o in outputs(model.inputs[-1])[1:])))


def test_the_plan_call_uses_the_plan_effort_and_later_calls_do_not():
    _, model, _, _, _ = run([LAUNCH, OBSERVE, plan(), windows(), GIVE_UP])

    assert model.efforts == ["medium", "medium", "medium", None, None]


def test_the_plan_effort_comes_from_the_task():
    _, model, _, _, _ = run([LAUNCH, GIVE_UP], task=free_task(plan_reasoning_effort="high"))

    assert model.efforts == ["high", "high"]


def test_a_refused_plan_leaves_no_plan_and_no_event():
    result, model, _, log, _ = run([LAUNCH, OBSERVE, plan(stages=[{"goal": "", "done_when": "x"}]), click(),
                                    GIVE_UP])

    assert output_of(model, 2)["refused_code"] == "bad_plan"
    assert output_of(model, 3)["refused_code"] == "needs_plan"
    assert events(log, "plan") == []


# stage_done

def test_stage_done_with_marks_from_the_latest_observation_closes_the_stage():
    _, model, _, log, _ = run([LAUNCH, OBSERVE, plan(), stage_done(), GIVE_UP])

    assert output_of(model, 3)["ok"] is True
    done, started = events(log, "stage")
    assert (done["n"], done["status"], done["kind"], done["note"]) == (1, "done", "text", NOTE)
    assert (started["n"], started["status"]) == (2, "started")
    assert done["step"] == started["step"] == 4


def test_stage_done_with_a_fresh_capture_is_picture_evidence():
    _, _, _, log, _ = run([LAUNCH, OBSERVE, look(), plan(), stage_done(marks=None, capture="c1"), GIVE_UP])

    assert events(log, "stage")[0]["kind"] == "picture"


@pytest.mark.parametrize("marks,capture,code", [
    ((99,), None, "stale_evidence"),
    (None, "c9", "stale_capture"),
    ((2,), "c1", "bad_evidence"),
    (None, None, "bad_evidence"),
    ((), None, "bad_evidence"),
])
def test_stage_done_needs_evidence_in_the_latest_observation(marks, capture, code):
    _, model, _, log, _ = run([LAUNCH, OBSERVE, look(), plan(), stage_done(marks, capture), GIVE_UP])

    refusal = output_of(model, 4)
    assert refusal["refused_code"] == code and "refused" in refusal
    assert events(log, "stage") == []


def test_stage_done_is_refused_while_actions_are_blocked():
    def blocked_then_done(_items):
        toolbox._policy.note_result(False, True)
        return stage_done()

    model = ScriptedModel([LAUNCH, OBSERVE, plan(), blocked_then_done, GIVE_UP])
    toolbox = scene_toolbox(_tmp(), asker=Asker(model))
    log = []
    run_agent(model, toolbox, free_task(), log=log.append)

    assert output_of(model, 3)["refused_code"] == "blocked_until_question"
    assert events(log, "stage") == []


def test_stage_done_without_a_plan_is_refused():
    _, model, _, _, _ = run([LAUNCH, OBSERVE, stage_done(), GIVE_UP])

    assert output_of(model, 2)["refused_code"] == "no_plan"


# Counting

def test_calls_are_counted_per_stage_and_ask_turns_are_not():
    turns = [LAUNCH, OBSERVE, plan(), windows(), turn("ask", stuck_question()), windows(), stage_done(),
             windows(), GIVE_UP]
    result, _, _, log, _ = run(turns, answers=[Answer(text="1번")])

    models = events(log, "model")
    assert [(event.get("stage"), event.get("stage_calls")) for event in models] == [
        (None, None), (None, None), (None, None), (1, 1), (1, 1), (1, 2), (1, 3), (2, 1), (2, 2)]
    assert [event.get("stage_limit") for event in models] == [None] * 3 + [8] * 6
    assert all(event["call_tokens"] == 100 for event in models)
    assert [event["tokens"] for event in models] == [100 * n for n in range(1, 10)]
    assert result.stats["stage_calls"] == [3, 2, 0]
    assert result.steps == 9


def test_a_revising_plan_call_counts_on_the_current_stage():
    revised = STAGES[:1] + [{"goal": "다른 결과", "done_when": "다른 표시"}]
    result, _, _, log, _ = run([LAUNCH, OBSERVE, plan(), windows(), plan(stages=revised), GIVE_UP])

    assert [event["revision"] for event in events(log, "plan")] == [0, 1]
    assert result.stats["stage_calls"][0] == 3
    assert result.stats["plan_revisions"] == 1


# The limit question

@pytest.mark.parametrize("count", [1, 3])
def test_at_the_limit_the_person_is_asked_before_the_next_call(count):
    result, model, _, log, asker = run(to_the_limit(count) + [GIVE_UP], answers=[Answer(accepted=False)])

    [question] = asker.questions
    assert question["kind"] == "stage_limit"
    assert (question["stage"], question["calls"], question["intents"]) == (1, 8, ["조회를 누른다"])
    assert question["text"] and "\n" not in question["text"]
    assert STAGES[0]["goal"] not in question["text"]
    assert asker.calls_before == [11]
    assert result.status == "stopped"


def test_continue_gives_another_allowance():
    turns = to_the_limit() + [windows() for _ in range(8)] + [GIVE_UP]
    result, model, _, log, asker = run(turns, task=free_task(max_steps=60),
                                       answers=[Answer(accepted=True), Answer(accepted=False)])

    assert [q["calls"] for q in asker.questions] == [8, 16]
    assert asker.calls_before == [11, 19]
    [limit, stopped] = events(log, "stage")
    assert (limit["status"], limit["calls"], limit["limit"], limit["step"]) == ("limit", 8, 16, 11)
    assert stopped["status"] == "stopped"
    assert events(log, "model")[11]["stage_limit"] == 16
    assert not any("stage_limit question" in str(item.get("content")) for item in model.inputs[11]
                   if isinstance(item, dict))


def test_guidance_reaches_the_model_and_the_exchanges():
    guidance = "오른쪽 끝을 누르세요"
    turns = to_the_limit() + [GIVE_UP]
    _, model, toolbox, _, _ = run(turns, answers=[Answer(text=guidance, accepted=True)])

    notes = [item for item in model.inputs[11] if isinstance(item, dict) and item.get("role") == "user"
             and guidance in str(item.get("content"))]
    assert len(notes) == 1
    [exchange] = toolbox.exchanges()
    assert exchange["question"]["kind"] == "stage_limit" and exchange["question"]["stage"] == 1
    assert exchange["answer"]["text"] == guidance


def test_a_plain_continue_leaves_no_exchange():
    _, _, toolbox, _, _ = run(to_the_limit() + [GIVE_UP], answers=[Answer(accepted=True)])

    assert toolbox.exchanges() == []


def test_stop_ends_the_run_and_marks_the_stage_stopped():
    result, model, toolbox, log, _ = run(to_the_limit() + [GIVE_UP], answers=[Answer(accepted=False)])

    assert result.status == "stopped" and "limit" in result.reason
    assert len(model.inputs) == 11
    [stopped] = events(log, "stage")
    assert (stopped["n"], stopped["status"], stopped["calls"]) == (1, "stopped", 8)
    assert toolbox.stage_records[0]["status"] == "stopped"


@pytest.mark.parametrize("nobody", [False, True])
def test_an_unanswered_limit_question_stops_the_run(nobody):
    asker = NoAsker() if nobody else None
    result, model, _, log, _ = run(to_the_limit() + [GIVE_UP], asker=asker)

    assert result.status == "stopped"
    assert len(model.inputs) == 11
    assert [event["status"] for event in events(log, "stage")] == ["stopped"]
    assert [event["kind"] for event in events(log, "question")] == ["stage_limit"]


def test_the_limit_question_shows_the_current_screen():
    observations = []
    model = ScriptedModel(to_the_limit() + [GIVE_UP])
    toolbox = scene_toolbox(_tmp(), asker=Asker(model, Answer(accepted=False)))
    toolbox._observer.on_observation = lambda *args, source=None, wait=None: observations.append(source)
    run_agent(model, toolbox, free_task())

    assert observations[-1] == "question"


# Revisions past the limit

def revisions(count):
    return [plan(stages=STAGES[:2] + [{"goal": f"고친 결과 {n}", "done_when": "보인다"}]) for n in range(count)]


def test_a_revision_past_the_limit_asks_and_continue_allows_more():
    turns = [LAUNCH, OBSERVE, plan()] + revisions(4) + revisions(1) + [GIVE_UP]
    _, model, _, log, asker = run(turns, answers=[Answer(accepted=True)])

    [question] = asker.questions
    assert (question["kind"], question["stage"], question["revisions"]) == ("plan_limit", 1, 3)
    assert output_of(model, 6)["refused_code"] == "too_many_revisions"
    assert "refused" not in output_of(model, 7)
    assert [event["revision"] for event in events(log, "plan")] == [0, 1, 2, 3, 4]


def test_stopping_at_the_revision_limit_stops_the_run():
    result, model, _, log, _ = run([LAUNCH, OBSERVE, plan()] + revisions(4) + [GIVE_UP],
                                   answers=[Answer(accepted=False)])

    assert result.status == "stopped" and len(model.inputs) == 7
    assert events(log, "stage")[-1]["status"] == "stopped"


# finish

def test_finish_with_unfinished_stages_is_refused_but_giving_up_is_not():
    result, model, _, _, asker = run([LAUNCH, OBSERVE, plan(2), stage_done(), finish(), GIVE_UP])

    refusal = output_of(model, 4)
    assert refusal["refused_code"] == "stages_unfinished" and "2" in refusal["refused"]
    assert asker.questions == []
    assert result.status == "gave_up"


def test_finish_after_every_stage_is_judged():
    result, _, _, _, asker = run([LAUNCH, OBSERVE, plan(1), stage_done(), finish()],
                                 answers=[Answer(accepted=True)])

    assert result.status == "confirmed" and asker.questions[0]["kind"] == "completion"


def test_finish_without_a_plan_is_judged_as_before():
    result, _, _, _, _ = run([LAUNCH, finish()], answers=[Answer(accepted=True)])

    assert result.status == "confirmed"


@pytest.mark.parametrize("ceiling", ["steps", "tokens"])
def test_finish_is_accepted_at_the_ceiling(ceiling):
    task = free_task(max_steps=4) if ceiling == "steps" else free_task(token_budget=350)
    result, model, _, log, asker = run([LAUNCH, OBSERVE, plan(), windows(), finish()], task=task,
                                       answers=[Answer(accepted=True)])

    assert result.status == "confirmed"
    assert asker.questions[0]["kind"] == "completion"
    assert len(model.inputs) == 5
    assert result.tokens == 500 and len(events(log, "model")) == 5


def test_the_ceiling_turn_offers_only_finish():
    task = free_task(max_steps=2)
    result, model, _, log, _ = run([LAUNCH, windows(), windows()], task=task)

    assert [tool["name"] for tool in model.tools] == ["finish"]
    assert "finish" in str(model.inputs[-1][-1]["content"])
    assert result.status == "stopped" and result.reason == "Step limit reached"
    assert len(events(log, "tool")) == 2


def test_the_ceiling_is_checked_before_calling_the_model():
    task = free_task(token_budget=250)
    result, model, _, _, _ = run([LAUNCH, OBSERVE, windows(), GIVE_UP], task=task)

    assert result.status == "gave_up"
    assert [tool["name"] for tool in model.tools] == ["finish"]


# The trimmed transcript

def full_observations(items):
    return [o for o in outputs(items) if FULL_OBSERVATION in json.loads(o["output"]).get("observation", "")]


def test_a_closed_stage_is_one_line_in_the_next_request():
    clicked = click()
    turns = [LAUNCH, OBSERVE, plan(), clicked, stage_done(), windows(), OBSERVE, GIVE_UP]
    _, model, _, _, _ = run(turns)

    after_close = model.inputs[5]
    assert len(full_observations(after_close)) == 1
    assert full_observations(after_close)[0]["call_id"] == clicked[0].call_id
    summaries = [item for item in after_close if isinstance(item, dict) and item.get("role") == "user"
                 and NOTE in str(item.get("content"))]
    assert len(summaries) == 1
    assert STAGES[0]["goal"] in summaries[0]["content"] and CLICK["intent"] in summaries[0]["content"]
    assert "c-launch" not in {getattr(item, "call_id", None) for item in after_close}

    after_new_observation = model.inputs[7]
    assert [o["call_id"] for o in full_observations(after_new_observation)] == ["c-observe"]
    assert clicked[0].call_id not in {getattr(item, "call_id", None) for item in after_new_observation}


def test_the_plan_turn_is_still_sent_after_a_stage_closes():
    planned = plan()
    _, model, _, _, _ = run([LAUNCH, OBSERVE, planned, stage_done(), GIVE_UP])

    assert planned[0] in model.inputs[4]


# What is left for the recorder and registration

def test_stats_and_stage_records_are_left_for_the_recorder_and_registration():
    seen = []

    def register(_model, toolbox, _log):
        seen.append(toolbox.stage_records)
        return 50

    result, _, toolbox, _, _ = run([LAUNCH, OBSERVE, plan(1), click(), stage_done(), finish()],
                                   answers=[Answer(accepted=True)], register=register)

    assert result.status == "confirmed" and result.tokens == 650
    assert result.stats == {"stage_count": 1, "stages_done": 1, "stage_calls": [2], "limit_questions": 0,
                            "plan_revisions": 0, "max_call_tokens": 100}
    [records] = seen
    assert records == toolbox.stage_records
    assert records[0]["status"] == "done" and records[0]["note"] == NOTE
    assert [action["intent"] for action in records[0]["actions"]] == [CLICK["intent"]]


def test_a_run_without_a_plan_still_reports_numbers():
    result, _, toolbox, _, _ = run([LAUNCH, GIVE_UP], tokens=70)

    assert result.stats["stage_count"] == 0 and result.stats["max_call_tokens"] == 70
    assert toolbox.stage_records == []


def test_a_reply_without_a_call_stops_the_stage_too():
    result, _, _, log, _ = run([LAUNCH, OBSERVE, plan(), [text_reply("끝")]])

    assert result.status == "stopped"
    assert [event["status"] for event in events(log, "stage")] == ["stopped"]
    assert events(log, "model")[-1]["stage"] == 1


def test_plan_and_stage_done_skip_the_approval_gate():
    gate = RecordingGate(approve=True)
    run([LAUNCH, OBSERVE, plan(), stage_done(), click(), GIVE_UP], gate=gate)

    assert [proposal["tool"] for proposal in gate.proposals] == ["launch_target", "click"]


def test_the_plan_event_has_the_stages_and_the_step():
    _, _, _, log, _ = run([LAUNCH, OBSERVE, plan(), GIVE_UP])

    [event] = events(log, "plan")
    assert event["step"] == 3 and event["revision"] == 0
    assert [stage["status"] for stage in event["stages"]] == ["active", "pending", "pending"]


def test_the_prompt_teaches_plans_and_stages():
    for word in ("plan", "stage_done", "done_when", "marks", "capture"):
        assert word in SYSTEM_PROMPT
    assert "latest observation stays" in SYSTEM_PROMPT
