import json
from datetime import date
from types import SimpleNamespace

import pytest

from agent.ask import NO_ANSWER, Answer
from agent.guard import AgentStopped
from agent.knowledge import KnowledgeStore
from agent.registration import DRAFT_INSTRUCTIONS, ITEM_SCHEMA, draft, register_after_run

RUN = "20261113-101500"
DAY = date(2026, 11, 13)

STUCK_EXCHANGE = {
    "question": {"kind": "stuck", "text": "목록이 열리지 않습니다. 무엇을 눌러야 하나요?",
                 "candidates": [{"n": 1, "label": "칸 가운데"}, {"n": 2, "label": "칸 오른쪽 끝"}]},
    "answer": {"text": "4번", "pin_on": None},
    "actions": [{"tool": "click_part", "part": "right", "target": {"role": "statictext", "name": "전일"},
                 "expect": "window_opened", "expected_met": True, "knowledge_id": None}],
    "failed_knowledge_id": None,
}
CONFIRM_EXCHANGE = {
    "question": {"kind": "confirm", "text": "이 셀이 맞나요?", "candidates": [{"n": 1, "label": "극판 소계 양품수"}]},
    "answer": {"text": "맞아", "pin_on": None},
    "actions": [],
    "failed_knowledge_id": None,
}

RESOLUTION_ITEM = {"action": "add", "id": None, "exchange": 1, "stage": None, "type": "resolution",
                   "situation": "선택 칸의 가운데를 눌러도 목록이 열리지 않는다",
                   "resolution": "선택 칸은 오른쪽 끝의 화살표를 눌러 연다",
                   "task": None, "checks": None}
DISCOVERED_ITEM = {"action": "add", "id": None, "exchange": None, "stage": 2, "type": "resolution",
                   "situation": "찾는 값이 세 칸 중 어디에 있는지 몰라 칸마다 열어 보았다",
                   "resolution": "값은 가운데 칸의 목록에 있다",
                   "task": None, "checks": None}
COMPLETION_ITEM = {"action": "add", "id": None, "exchange": None, "stage": None, "type": "completion",
                   "situation": None, "resolution": None, "task": "조회 결과를 엑셀로 저장하는 작업",
                   "checks": [{"kind": "file", "path": None, "pattern": "*.xlsx",
                               "role": None, "name": None, "name_contains": None}]}
HUMAN_NOTE = {"goal": "엑셀로 저장해줘", "evidence": "저장함", "saved_path": r"C:\out\a.xlsx", "by": "human"}
HARNESS_NOTE = {"goal": "설정 열기", "evidence": "열림", "saved_path": None, "by": "harness"}


def stage_record(n, status="done", signals=()):
    return {"n": n, "goal": f"단계 {n}의 결과", "done_when": f"단계 {n}의 확인", "status": status, "calls": 3,
            "kind": "text" if status == "done" else None, "note": f"단계 {n}의 메모", "limit_questions": 0,
            "actions": [{"tool": "click", "intent": f"단계 {n}에서 누름", "expected_met": True, "refused_code": None,
                         "opened": 1, "closed": 1}],
            "signals": list(signals)}


CLEAN_STAGE = stage_record(1)
DETOUR_STAGE = stage_record(2, signals=["abandoned_window", "looked_twice"])
STOPPED_STAGE = stage_record(3, status="stopped", signals=["mismatch"])


def function_call(items):
    return SimpleNamespace(type="function_call", name="propose_entries",
                           arguments=json.dumps({"items": items}, ensure_ascii=False), call_id="call_1")


class DraftingModel:
    def __init__(self, *outputs, tokens=100):
        self.outputs = list(outputs)
        self.tokens = tokens
        self.inputs = []

    def respond(self, instructions, items, tools):
        self.inputs.append({"instructions": instructions, "items": items, "tools": tools})
        return SimpleNamespace(output=self.outputs.pop(0), usage=SimpleNamespace(total_tokens=self.tokens))


def drafting(*items, tokens=100):
    return DraftingModel([function_call(list(items))], tokens=tokens)


class FakeToolbox:
    def __init__(self, exchanges=(), answers=(), finish_note=None, can_ask=True, stage_records=None):
        self._exchanges = list(exchanges)
        self.answers = list(answers)
        self.finish_note = finish_note
        self.can_ask = can_ask
        self.stage_records = stage_records
        self.questions = []

    def exchanges(self):
        return list(self._exchanges)

    def ask(self, question):
        self.questions.append(question)
        answer = self.answers.pop(0)(question) if self.answers else NO_ANSWER
        if answer.timed_out:
            raise AgentStopped("No answer")
        return answer


def approve_all(question):
    return Answer(items=tuple({"n": item["n"], "decision": "approve"} for item in question["items"]))


def reject_all(question):
    return Answer(items=tuple({"n": item["n"], "decision": "reject"} for item in question["items"]))


def editing(**changes):
    def answer(question):
        return Answer(items=tuple({"n": item["n"], "decision": "edit", "entry": {**item["entry"], **changes}}
                                  for item in question["items"]))
    return answer


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path, "mes")


@pytest.fixture
def events():
    return []


def test_nothing_to_learn_calls_no_model(store, events):
    model = drafting(RESOLUTION_ITEM)
    for toolbox in (FakeToolbox(), FakeToolbox(finish_note=HARNESS_NOTE)):
        assert register_after_run(model, toolbox, events.append, store=store, run_id=RUN) == 0
    assert model.inputs == [] and events == []


def test_no_one_to_approve_calls_no_model(store, events):
    model = drafting(RESOLUTION_ITEM)
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], can_ask=False)

    assert register_after_run(model, toolbox, events.append, store=store, run_id=RUN) == 0
    assert model.inputs == [] and toolbox.questions == [] and store.entries() == []


def test_approved_item_is_saved_with_its_source(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all])

    tokens = register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    saved = store.entries()[0]
    assert tokens == 100 and saved["resolution"] == RESOLUTION_ITEM["resolution"]
    assert saved["id"] == "r-20261113-001"
    assert saved["source"] == {"run_id": RUN, "question": STUCK_EXCHANGE["question"]["text"],
                               "answer": "4번", "origin": "question", "date": "2026-11-13"}
    assert events[-1] == {"type": "registration",
                          "saved": [{"id": "r-20261113-001", "type": "resolution", "action": "add"}],
                          "rejected": 0}


def test_the_question_shows_the_draft_items_in_the_contract_shape(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all])

    register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    question = toolbox.questions[0]
    assert question["kind"] == "registration" and question["text"] == "다음 내용을 등록할까요?"
    assert question["items"] == [{"n": 1, "action": "add", "id": None, "origin": "question", "stage": None, "entry": {
        "type": "resolution", "situation": RESOLUTION_ITEM["situation"], "resolution": RESOLUTION_ITEM["resolution"]}}]


def test_edited_item_is_saved_as_edited(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[editing(resolution="칸 오른쪽 끝의 작은 화살표를 누른다")])

    register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    saved = store.entries()[0]
    assert saved["resolution"] == "칸 오른쪽 끝의 작은 화살표를 누른다"
    assert saved["situation"] == RESOLUTION_ITEM["situation"]


def test_edit_cannot_change_the_entry_type(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[editing(type="completion")])

    register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    assert [e["type"] for e in store.entries()] == ["resolution"]


def test_rejected_item_is_not_saved_and_is_counted(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[reject_all])

    register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN)

    assert store.entries() == [] and events[-1] == {"type": "registration", "saved": [], "rejected": 1}


def test_an_item_without_a_decision_is_not_saved(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[lambda question: Answer(items=())])

    register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN)

    assert store.entries() == [] and events[-1]["saved"] == [] and events[-1]["rejected"] == 1


def test_failed_knowledge_becomes_an_update_of_that_entry(store, events):
    old = store.add({"type": "resolution", "situation": "선택 칸이 안 열린다", "resolution": "칸을 두 번 누른다"}, today=DAY)
    store.count_failure(old["id"])
    exchange = {**STUCK_EXCHANGE, "failed_knowledge_id": old["id"]}
    update = {**RESOLUTION_ITEM, "action": "update", "id": old["id"]}
    model = drafting(update)
    toolbox = FakeToolbox(exchanges=[exchange], answers=[approve_all])

    register_after_run(model, toolbox, events.append, store=store, run_id=RUN, today=DAY)

    entries = store.entries()
    assert len(entries) == 1 and entries[0]["id"] == old["id"]
    assert entries[0]["resolution"] == RESOLUTION_ITEM["resolution"] and entries[0]["failures"] == 1
    assert entries[0]["source"]["run_id"] == RUN
    assert events[-1]["saved"] == [{"id": old["id"], "type": "resolution", "action": "update"}]
    sent = json.dumps(model.inputs[0]["items"], ensure_ascii=False)
    assert old["id"] in sent and "선택 칸이 안 열린다" in sent


def test_update_of_an_unknown_entry_is_dropped_before_asking(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all])

    register_after_run(drafting({**RESOLUTION_ITEM, "action": "update", "id": "r-20261101-009"}), toolbox,
                       events.append, store=store, run_id=RUN)

    assert toolbox.questions == [] and store.entries() == []
    assert events[-1]["type"] == "warning" and "r-20261101-009" in events[-1]["message"]


def test_draft_with_coordinates_is_dropped_before_asking(store, events):
    good = {**RESOLUTION_ITEM, "exchange": 2, "situation": "셀이 여럿", "resolution": "그룹 이름만 말하면 소계 행이다"}
    bad = {**RESOLUTION_ITEM, "resolution": "(412, 233)을 누른다"}
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE, CONFIRM_EXCHANGE], answers=[approve_all])

    register_after_run(drafting(bad, good), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    assert [item["n"] for item in toolbox.questions[0]["items"]] == [1]
    assert toolbox.questions[0]["items"][0]["entry"]["resolution"] == good["resolution"]
    assert any(e["type"] == "warning" for e in events)
    assert store.entries()[0]["source"]["answer"] == "맞아"


def test_an_edit_the_store_refuses_saves_nothing_and_does_not_raise(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[editing(resolution="x=412 지점을 누른다")])

    register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    assert store.entries() == []
    assert events[-1] == {"type": "registration", "saved": [], "rejected": 0}
    assert any(e["type"] == "warning" and "x=412" in e["message"] for e in events)


def test_human_confirmed_save_proposes_a_file_completion(store, events):
    toolbox = FakeToolbox(answers=[approve_all], finish_note=HUMAN_NOTE)

    register_after_run(drafting(COMPLETION_ITEM), toolbox, events.append, store=store, run_id=RUN)

    entry = store.entries()[0]
    assert entry["checks"] == [{"kind": "file", "pattern": "*.xlsx"}]
    assert entry["source"]["run_id"] == RUN and "question" not in entry["source"]


def test_element_and_human_checks_are_converted(store, events):
    checks = [{"kind": "element", "path": None, "pattern": None, "role": "statictext", "name": None,
               "name_contains": "저장 완료"},
              {"kind": "human", "path": None, "pattern": None, "role": None, "name": None, "name_contains": None}]
    toolbox = FakeToolbox(answers=[approve_all], finish_note=HUMAN_NOTE)

    register_after_run(drafting({**COMPLETION_ITEM, "checks": checks}), toolbox, events.append, store=store, run_id=RUN)

    assert store.entries()[0]["checks"] == [
        {"kind": "element", "require": {"role": "statictext", "name_contains": "저장 완료"}}, {"kind": "human"}]


def test_unanswered_registration_saves_nothing_and_does_not_raise(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[])

    tokens = register_after_run(drafting(RESOLUTION_ITEM), toolbox, events.append, store=store, run_id=RUN)

    assert tokens == 100 and store.entries() == [] and len(toolbox.questions) == 1
    assert events[-1]["type"] == "warning"


def test_model_that_proposes_nothing_asks_nothing(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all])

    assert register_after_run(DraftingModel([], tokens=70), toolbox, events.append, store=store, run_id=RUN) == 70
    assert register_after_run(drafting(tokens=30), toolbox, events.append, store=store, run_id=RUN) == 30
    assert toolbox.questions == [] and store.entries() == [] and events == []


def test_unreadable_proposal_is_an_empty_draft(store, events):
    broken = SimpleNamespace(type="function_call", name="propose_entries", arguments="{not json", call_id="c")
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all])

    register_after_run(DraftingModel([broken]), toolbox, events.append, store=store, run_id=RUN)

    assert toolbox.questions == [] and events[-1]["type"] == "warning"


def test_draft_sends_one_call_with_only_the_proposal_tool_and_the_material():
    model = drafting(RESOLUTION_ITEM)
    existing = [{"id": "r-1", "type": "resolution", "status": "active", "situation": "가", "resolution": "나",
                 "uses": 0, "failures": 0}]

    items, tokens = draft(model, [STUCK_EXCHANGE], HUMAN_NOTE, existing)

    assert tokens == 100 and len(model.inputs) == 1
    assert [tool["name"] for tool in model.inputs[0]["tools"]] == ["propose_entries"]
    sent = json.dumps(model.inputs[0]["items"], ensure_ascii=False)
    for text in ("4번", "목록이 열리지 않습니다", "r-1", "엑셀로 저장해줘", ".xlsx", "human"):
        assert text in sent, text
    assert "coordinates" in model.inputs[0]["instructions"]
    assert items == [{"n": 1, "action": "add", "id": None, "exchange": 1, "stage": None, "origin": "question", "entry": {
        "type": "resolution", "situation": RESOLUTION_ITEM["situation"], "resolution": RESOLUTION_ITEM["resolution"]}}]


def test_the_proposal_tool_is_a_strict_schema():
    model = drafting()
    draft(model, [STUCK_EXCHANGE], None, [])
    tool = model.inputs[0]["tools"][0]

    def strict(schema):
        if schema.get("type") == "object" or "properties" in schema:
            assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
            for value in schema["properties"].values():
                strict(value)
        if "items" in schema:
            strict(schema["items"])

    assert tool["type"] == "function" and tool["strict"] is True
    strict(tool["parameters"])


def test_with_the_real_toolbox_the_answer_is_registered_without_coordinates(tmp_path):
    from test_tools import part_call
    from test_tools_ask import ScriptedAsker, coordinate_keys, mismatch_twice, question, toolbox_with

    approve = Answer(items=({"n": 1, "decision": "approve"},))
    asker = ScriptedAsker([Answer(text="4번", pin=(1192, 562)), approve])
    toolbox, _, observer = toolbox_with(tmp_path, asker)
    mismatch_twice(toolbox)
    toolbox.call("ask", question(marks=[3, 4]))
    toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened"))
    toolbox.finish_note = {**HARNESS_NOTE, "goal": "목록 열기"}
    store = KnowledgeStore(tmp_path / "knowledge", "scene")
    model = drafting(RESOLUTION_ITEM)
    events = []

    tokens = register_after_run(model, toolbox, events.append, store=store, run_id=RUN, today=DAY)

    assert tokens == 100 and store.entries()[0]["source"]["answer"] == "4번"
    assert coordinate_keys(json.loads(model.inputs[0]["items"][0]["content"])) == set()
    assert asker.questions[-1]["kind"] == "registration" and asker.questions[-1]["id"] == "q2"
    assert [e["type"] for e in observer.events if e["type"] in ("question", "answer")][-2:] == ["question", "answer"]
    assert events[-1]["saved"][0]["action"] == "add"


def material(model):
    return json.loads(model.inputs[0]["items"][0]["content"])


def test_a_run_with_no_question_but_a_detour_is_drafted(store, events):
    model = drafting(DISCOVERED_ITEM)
    toolbox = FakeToolbox(answers=[approve_all], finish_note=HARNESS_NOTE, stage_records=[CLEAN_STAGE, DETOUR_STAGE])

    tokens = register_after_run(model, toolbox, events.append, store=store, run_id=RUN, today=DAY)

    assert tokens == 100 and len(model.inputs) == 1
    assert material(model)["stages"] == [DETOUR_STAGE]
    assert material(model)["exchanges"] == []
    assert store.entries()[0]["resolution"] == DISCOVERED_ITEM["resolution"]


@pytest.mark.parametrize("records", [None, [], [CLEAN_STAGE], [CLEAN_STAGE, STOPPED_STAGE]])
def test_a_run_with_no_question_and_no_detour_drafts_nothing(store, events, records):
    model = drafting(DISCOVERED_ITEM)
    toolbox = FakeToolbox(answers=[approve_all], finish_note=HARNESS_NOTE, stage_records=records)

    assert register_after_run(model, toolbox, events.append, store=store, run_id=RUN) == 0
    assert model.inputs == [] and toolbox.questions == [] and events == []


def test_a_detour_needs_someone_to_approve(store, events):
    model = drafting(DISCOVERED_ITEM)
    toolbox = FakeToolbox(finish_note=HARNESS_NOTE, stage_records=[DETOUR_STAGE], can_ask=False)

    assert register_after_run(model, toolbox, events.append, store=store, run_id=RUN) == 0
    assert model.inputs == []


def test_only_done_stages_with_a_signal_are_given_to_the_draft(store, events):
    model = drafting()
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], finish_note=HARNESS_NOTE,
                          stage_records=[CLEAN_STAGE, DETOUR_STAGE, STOPPED_STAGE])

    register_after_run(model, toolbox, events.append, store=store, run_id=RUN)

    assert material(model)["stages"] == [DETOUR_STAGE]


def test_the_draft_instructions_explain_stage_records_and_keep_the_bans():
    instructions = DRAFT_INSTRUCTIONS
    for words in ("stages", "signals", "situation", "coordinates", "[n] mark numbers", "refs", "window ids",
                  "values"):
        assert words in instructions, words
    assert "stage" in ITEM_SCHEMA["required"]


def test_a_discovered_item_is_shown_with_its_origin(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all], finish_note=HARNESS_NOTE,
                          stage_records=[DETOUR_STAGE])

    register_after_run(drafting(RESOLUTION_ITEM, DISCOVERED_ITEM), toolbox, events.append, store=store, run_id=RUN,
                       today=DAY)

    shown = toolbox.questions[0]["items"]
    assert [(item["n"], item["origin"], item["stage"]) for item in shown] == [(1, "question", None),
                                                                              (2, "discovered", 2)]
    assert shown[1]["entry"] == {"type": "resolution", "situation": DISCOVERED_ITEM["situation"],
                                 "resolution": DISCOVERED_ITEM["resolution"]}


def test_a_stage_that_had_no_detour_does_not_make_an_item_discovered(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all], finish_note=HARNESS_NOTE,
                          stage_records=[CLEAN_STAGE, DETOUR_STAGE])
    named_clean_stage = {**DISCOVERED_ITEM, "stage": 1}
    from_question = {**RESOLUTION_ITEM, "stage": 2}

    register_after_run(drafting(named_clean_stage, from_question), toolbox, events.append, store=store, run_id=RUN,
                       today=DAY)

    assert [(item["origin"], item["stage"]) for item in toolbox.questions[0]["items"]] == [
        ("question", None), ("question", None)]


def test_a_saved_discovered_entry_records_its_origin(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[approve_all], finish_note=HARNESS_NOTE,
                          stage_records=[DETOUR_STAGE])

    register_after_run(drafting(DISCOVERED_ITEM), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    assert store.entries()[0]["source"] == {"run_id": RUN, "origin": "discovered", "stage": 2, "date": "2026-11-13"}
    assert events[-1]["saved"] == [{"id": "r-20261113-001", "type": "resolution", "action": "add"}]


def test_rejecting_every_item_saves_nothing(store, events):
    toolbox = FakeToolbox(exchanges=[STUCK_EXCHANGE], answers=[reject_all], finish_note=HARNESS_NOTE,
                          stage_records=[DETOUR_STAGE])

    register_after_run(drafting(RESOLUTION_ITEM, DISCOVERED_ITEM), toolbox, events.append, store=store, run_id=RUN,
                       today=DAY)

    assert len(toolbox.questions[0]["items"]) == 2
    assert store.entries(include_disabled=True) == []
    assert not store.folder.exists() or not any(store.folder.glob("*.yaml"))
    assert events[-1] == {"type": "registration", "saved": [], "rejected": 2}


def test_a_discovered_item_with_coordinates_is_dropped_before_asking(store, events):
    toolbox = FakeToolbox(answers=[approve_all], finish_note=HARNESS_NOTE, stage_records=[DETOUR_STAGE])
    pointed = {**DISCOVERED_ITEM, "resolution": "(1192, 562)을 누른다"}

    register_after_run(drafting(pointed), toolbox, events.append, store=store, run_id=RUN, today=DAY)

    assert toolbox.questions == [] and store.entries() == []
    assert events[-1]["type"] == "warning"
