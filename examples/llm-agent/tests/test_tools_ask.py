import json
import time

import pytest
from fake_cli import FakeCli
from test_tools import (
    EXE,
    LINE_BOX,
    LIST_WINDOW,
    MAIN,
    MAIN_BOUNDS,
    OBSERVE,
    SCENE,
    EventObserver,
    _node,
    part_call,
    point_call,
)

from agent.ask import NO_ANSWER, Answer, AskGate, NoAsker
from agent.desktop import DesktopClient
from agent.guard import AgentStopped
from agent.knowledge import KnowledgeStore
from agent.task import Task
from agent.tool_schemas import READ_ONLY_TOOLS, SCHEMAS
from agent.tools import Toolbox

CELL_A = {"x": 1250, "y": 664, "width": 80, "height": 24}
CELL_B = {"x": 1250, "y": 688, "width": 80, "height": 24}
TABLE_SCENE = {
    "windows": [MAIN],
    "tree": {"role": "window", "name": "메인", "bounds": MAIN_BOUNDS, "children": [
        _node("statictext", "양품수", {"x": 1250, "y": 640, "width": 80, "height": 24}),
        _node("statictext", "극판", {"x": 1010, "y": 664, "width": 80, "height": 24}),
        _node("statictext", "소계", {"x": 1090, "y": 664, "width": 80, "height": 24}),
        {"role": "cell", "value": "12,480", "ref_id": "@s1:e2", "bounds": CELL_A},
        _node("statictext", "극판", {"x": 1010, "y": 688, "width": 80, "height": 24}),
        _node("statictext", "소계", {"x": 1090, "y": 688, "width": 80, "height": 24}),
        {"role": "cell", "value": "0", "ref_id": "@s1:e3", "bounds": CELL_B},
    ]},
}


class ScriptedAsker:
    interactive = True

    def __init__(self, answers):
        self.answers = list(answers)
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return self.answers.pop(0)


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "knowledge", "scene")


def resolution(store, status="active"):
    return store.add({"type": "resolution", "status": status,
                      "situation": "선택 칸의 가운데를 눌러도 목록이 열리지 않는다",
                      "resolution": "선택 칸은 오른쪽 끝을 눌러 연다"})


def file_completion(store):
    return store.add({"type": "completion", "task": "조회 결과를 엑셀로 저장하는 작업",
                      "checks": [{"kind": "file", "pattern": "*.xlsx"}]})


def toolbox_with(tmp_path, asker=None, knowledge=None, scene=SCENE, image_mode="user_message"):
    cli = FakeCli(scene=scene)
    cli.on_click = lambda x, y: cli.open_window(LIST_WINDOW) if x > 1180 and y < 600 else cli.focus(mark=3)
    observer = EventObserver()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, 2, [], image_mode=image_mode),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=observer,
        asker=asker,
        knowledge=knowledge,
        captures_dir=tmp_path / "captures",
        wall=lambda: time.time() - 60,
    )
    toolbox.call("launch_target", {})
    toolbox.call("observe", OBSERVE)
    return toolbox, cli, observer


def question(kind="stuck", marks=(), capture=None, proposed=None, basis=()):
    candidates = [{"label": f"후보 {mark}", "mark": mark, "capture": None, "x": None, "y": None} for mark in marks]
    if capture is not None:
        name, x, y = capture
        candidates.append({"label": "찍은 곳", "mark": None, "capture": name, "x": x, "y": y})
    return {"kind": kind, "text": "무엇을 눌러야 하나요?", "candidates": candidates,
            "proposed": proposed, "basis": list(basis)}


def mismatch_twice(toolbox):
    for _ in range(2):
        toolbox.call("click_part", part_call(mark=3, part="center", expect="window_opened"))


def coordinate_keys(value):
    if isinstance(value, dict):
        found = {key for key in value if key in ("x", "y", "bounds", "point", "points", "pin")}
        return found.union(*(coordinate_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(coordinate_keys(item) for item in value))
    return set()


def test_ask_is_a_read_only_tool_at_level_zero():
    levels = {s["definition"]["name"]: s for s in SCHEMAS}
    ask = levels["ask"]
    assert ask["level"] == 0 and "ask" in READ_ONLY_TOOLS
    item = ask["definition"]["parameters"]["properties"]["candidates"]["items"]
    assert item["additionalProperties"] is False and sorted(item["required"]) == sorted(item["properties"])


def test_ask_passes_candidates_with_bounds_and_returns_the_answer(tmp_path):
    asker = ScriptedAsker([Answer(text="2번")])
    toolbox, _, _ = toolbox_with(tmp_path, asker)

    result = toolbox.call("ask", question(marks=[3, 4], proposed=1, basis=[1]))

    assert result == {"ok": True, "answer": "2번", "pin": None}
    asked = asker.questions[0]
    assert (asked["type"], asked["id"], asked["kind"], asked["window_id"]) == ("question", "q1", "stuck", "w-1")
    assert [c["n"] for c in asked["candidates"]] == [1, 2]
    assert asked["candidates"][0] == {"n": 1, "label": "후보 3", "mark": 3, "bounds": LINE_BOX, "point": None}
    assert asked["proposed"] == 1 and asked["basis"][0]["mark"] == 1
    assert asked["basis"][0]["bounds"] == {"x": 1010, "y": 520, "width": 60, "height": 24}


def test_a_recorded_question_can_always_be_answered_through_the_gate(tmp_path):
    gate = AskGate(timeout_s=5)
    toolbox, _, observer = toolbox_with(tmp_path, gate)
    replies = []
    record = observer.on_event

    def answer_on_sight(event):
        record(event)
        if event.get("type") == "question":
            replies.append(gate.answer(event["id"], Answer(text="1번")))

    observer.on_event = answer_on_sight

    result = toolbox.call("ask", question(marks=[3, 4]))

    assert replies == [True]
    assert result == {"ok": True, "answer": "1번", "pin": None}
    kinds = [event["type"] for event in observer.events if event["type"] in ("question", "answer")]
    assert kinds == ["question", "answer"]


class SilentGateAsker:
    """Takes on_waiting but never calls it, as a gate that times out at once might."""

    interactive = True

    def ask(self, question, on_waiting=None):
        return Answer(text="그냥")


def test_the_question_is_recorded_once_even_if_the_asker_never_calls_back(tmp_path):
    toolbox, _, observer = toolbox_with(tmp_path, SilentGateAsker())

    toolbox.call("ask", question(marks=[3]))

    kinds = [event["type"] for event in observer.events if event["type"] in ("question", "answer")]
    assert kinds == ["question", "answer"]


def test_an_asker_without_on_waiting_sees_its_question_already_recorded(tmp_path):
    seen = []

    class LookingAsker(ScriptedAsker):
        def ask(self, question):
            seen.append([event["type"] for event in observer.events].count("question"))
            return super().ask(question)

    toolbox, _, observer = toolbox_with(tmp_path, LookingAsker([Answer(text="2번")]))

    toolbox.call("ask", question(marks=[3]))

    assert seen == [1]


def test_question_shows_the_screen_without_renumbering_the_model_marks(tmp_path):
    toolbox, cli, observer = toolbox_with(tmp_path, ScriptedAsker([Answer(text="1번")]))

    toolbox.call("ask", question(marks=[3]))

    assert observer.sources[-1][0] == "question"
    assert [e["type"] for e in observer.events[-2:]] == ["question", "answer"]
    toolbox.call("click_part", part_call(mark=3, part="right"))
    assert "--xy=1192,562" in cli.full_call("mouse-click")


def test_capture_candidate_is_mapped_to_a_screen_point(tmp_path):
    asker = ScriptedAsker([Answer(text="맞아")])
    toolbox, _, _ = toolbox_with(tmp_path, asker)
    toolbox.call("look", {"mark": None})

    toolbox.call("ask", question(kind="confirm", capture=("c1", 10, 20), proposed=1))

    assert asker.questions[0]["candidates"][0]["point"] == [1018, 520]
    assert asker.questions[0]["candidates"][0]["bounds"] is None


@pytest.mark.parametrize("bad", [
    question(marks=[99]), question(marks=[3], proposed=2), question(capture=("c9", 1, 1)),
    question(marks=[3], basis=[99]), {**question(marks=[3]), "kind": "maybe"}])
def test_bad_questions_are_refused_without_asking(tmp_path, bad):
    asker = ScriptedAsker([Answer(text="x")])
    toolbox, _, _ = toolbox_with(tmp_path, asker)
    assert "refused" in toolbox.call("ask", bad)
    assert asker.questions == []


def test_capture_candidate_after_the_window_moved_is_stale(tmp_path):
    asker = ScriptedAsker([Answer(text="x")])
    toolbox, cli, _ = toolbox_with(tmp_path, asker)
    toolbox.call("look", {"mark": None})
    cli.window_shift = 100
    assert toolbox.call("ask", question(capture=("c1", 10, 20)))["refused_code"] == "stale_capture"
    assert asker.questions == []


def test_pin_inside_an_element_names_its_mark(tmp_path):
    asker = ScriptedAsker([Answer(text="여기", pin=(1192, 562))])
    toolbox, _, observer = toolbox_with(tmp_path, asker)

    assert toolbox.call("ask", question(marks=[3]))["pin"] == {"x": 1192, "y": 562, "mark": 3}
    assert observer.events[-1]["pin"] == {"point": [1192, 562], "image_point": None, "mark": 3}


def test_unanswered_question_stops_the_run(tmp_path):
    toolbox, _, observer = toolbox_with(tmp_path, ScriptedAsker([NO_ANSWER]))
    with pytest.raises(AgentStopped):
        toolbox.call("ask", question(marks=[3]))
    assert observer.events[-1]["type"] == "answer" and observer.events[-1]["timed_out"] is True


@pytest.mark.parametrize("asker", [NoAsker(), None])
def test_no_one_to_ask_is_refused(tmp_path, asker):
    toolbox, _, _ = toolbox_with(tmp_path, asker)
    assert toolbox.can_ask is False
    assert toolbox.call("ask", question(marks=[3]))["refused_code"] == "no_one_to_ask"


def test_point_click_is_refused_until_confirmed(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text="맞아")]))
    toolbox.call("look", {"mark": None})
    click = point_call(capture="c1", x=10, y=20)

    assert toolbox.call("click_point", click)["refused_code"] == "needs_confirmation"
    assert "mouse-click" not in cli.command_names()
    toolbox.call("ask", question(kind="confirm", capture=("c1", 10, 20), proposed=1))
    assert toolbox.call("click_point", click)["ok"]
    assert toolbox.call("click_point", click)["refused_code"] == "needs_confirmation"


def test_raw_mouse_click_needs_confirmation_too(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([]))
    click = {"window_id": "w-1", "x": 1050, "y": 600, "button": "left", "intent": "누른다",
             "expect": "any", "knowledge_id": None}
    assert toolbox.call("mouse_click", click)["refused_code"] == "needs_confirmation"
    assert "mouse-click" not in cli.command_names()


def test_two_mismatches_block_until_a_question_is_answered(tmp_path):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text="4번")]))
    mismatch_twice(toolbox)

    assert toolbox.call("click_part", part_call(mark=3, part="right"))["refused_code"] == "blocked_until_question"
    assert toolbox.call("check", {"ref": "@s1:e1"})["refused_code"] == "blocked_until_question"
    assert toolbox.call("observe", OBSERVE)["ok"]
    toolbox.call("ask", question(marks=[3, 4]))
    result = toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened"))
    assert result["ok"] and result["expected_met"] is True


def test_ambiguous_cell_needs_a_confirmation_or_a_knowledge_id(tmp_path, store):
    entry = resolution(store)
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store, scene=TABLE_SCENE)
    found = toolbox.call("find_cells", {"rows": ["극판", "소계"], "columns": ["양품수"]})
    assert len(found["candidates"]) == 2
    cell = part_call(mark=found["candidates"][0]["mark"], part="center")

    assert toolbox.call("click_part", cell)["refused_code"] == "needs_confirmation"
    assert toolbox.call("click", {"ref": "@s1:e2"})["refused_code"] == "needs_confirmation"
    assert "mouse-click" not in cli.command_names() and "click" not in cli.command_names()
    result = toolbox.call("click_part", {**cell, "knowledge_id": entry["id"]})
    assert result["ok"] and result["knowledge_id"] == entry["id"]
    assert store.get(entry["id"])["uses"] == 1


def test_answering_a_confirm_question_lets_one_ambiguous_cell_be_clicked(tmp_path):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text="1번")]), scene=TABLE_SCENE)
    found = toolbox.call("find_cells", {"rows": ["극판", "소계"], "columns": ["양품수"]})
    marks = [c["mark"] for c in found["candidates"]]
    toolbox.call("ask", question(kind="confirm", marks=marks, proposed=1))
    assert toolbox.call("click", {"ref": "@s1:e2"})["ok"]


def test_single_cell_needs_no_confirmation(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([]), scene=TABLE_SCENE)
    toolbox.call("find_cells", {"rows": ["극판", "소계"], "columns": ["양품수"]})
    toolbox.call("find_cells", {"rows": ["양품수"], "columns": ["양품수"]})
    assert toolbox.call("click", {"ref": "@s1:e2"})["ok"]


@pytest.mark.parametrize("which", ["missing", "disabled"])
def test_unknown_or_disabled_knowledge_is_refused(tmp_path, store, which):
    entry_id = "r-none" if which == "missing" else resolution(store, status="disabled")["id"]
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store)
    result = toolbox.call("click_part", part_call(mark=3, part="right", knowledge_id=entry_id))
    assert result["refused_code"] == "unknown_knowledge"
    assert "mouse-click" not in cli.command_names()


def test_knowledge_id_without_a_store_is_unknown(tmp_path):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([]))
    result = toolbox.call("click_part", part_call(mark=3, part="right", knowledge_id="r-1"))
    assert result["refused_code"] == "unknown_knowledge"


def test_cited_knowledge_is_logged_as_used(tmp_path, store):
    entry = resolution(store)
    toolbox, _, observer = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store)
    toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened", knowledge_id=entry["id"]))
    used = next(e for e in observer.events if e["type"] == "knowledge_used")
    assert (used["id"], used["text"]) == (entry["id"], entry["resolution"])


def test_mismatch_after_knowledge_counts_a_failure_and_blocks(tmp_path, store):
    entry = resolution(store)
    toolbox, _, observer = toolbox_with(tmp_path, ScriptedAsker([Answer(text="오른쪽")]), knowledge=store)

    toolbox.call("click_part", part_call(mark=3, part="center", expect="window_opened", knowledge_id=entry["id"]))

    assert store.get(entry["id"])["failures"] == 1
    assert any(e["type"] == "knowledge_failed" and e["id"] == entry["id"] for e in observer.events)
    assert toolbox.call("click_part", part_call(mark=3, part="right"))["refused_code"] == "blocked_until_question"
    toolbox.call("ask", question(marks=[3]))
    assert toolbox.exchanges()[0]["failed_knowledge_id"] == entry["id"]


def test_exchanges_hold_the_answer_and_the_actions_after_it_without_coordinates(tmp_path):
    asker = ScriptedAsker([Answer(text="4번", pin=(1192, 562))])
    toolbox, _, _ = toolbox_with(tmp_path, asker)
    mismatch_twice(toolbox)
    toolbox.call("ask", question(marks=[3, 4]))
    toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened"))

    [exchange] = toolbox.exchanges()

    assert exchange["question"] == {"kind": "stuck", "text": "무엇을 눌러야 하나요?",
                                    "candidates": [{"n": 1, "label": "후보 3"}, {"n": 2, "label": "후보 4"}]}
    assert exchange["answer"] == {"text": "4번", "pin_on": {"role": "statictext", "name": "라인"}}
    assert exchange["actions"] == [{"tool": "click_part", "part": "right",
                                    "target": {"role": "statictext", "name": "라인"},
                                    "expect": "window_opened", "expected_met": True, "knowledge_id": None}]
    assert exchange["failed_knowledge_id"] is None
    assert coordinate_keys(exchange) == set()
    json.dumps(exchange)


def test_briefing_lists_active_entries_and_logs_their_ids(tmp_path, store):
    active, disabled = resolution(store), resolution(store, status="disabled")
    toolbox, _, observer = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store)

    text = toolbox.briefing()

    assert active["id"] in text and active["resolution"] in text and disabled["id"] not in text
    assert observer.events[-1] == {"type": "knowledge_loaded", "system": "scene", "ids": [active["id"]],
                                   "truncated": False}


def test_briefing_without_knowledge_is_empty(tmp_path, store):
    assert toolbox_with(tmp_path, ScriptedAsker([]))[0].briefing() == ""
    assert toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store)[0].briefing() == ""


def test_check_completion_runs_the_entry_checks(tmp_path, store):
    entry = file_completion(store)
    toolbox, _, observer = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store)
    saved = tmp_path / "out.xlsx"
    saved.write_bytes(b"x")

    met = toolbox.check_completion(entry["id"], str(saved))

    assert met["met"] is True and met["by"] == "harness"
    assert observer.events[-1]["type"] == "completion"
    assert (observer.events[-1]["by"], observer.events[-1]["id"], observer.events[-1]["met"]) == (
        "knowledge", entry["id"], True)
    assert toolbox.check_completion(entry["id"], str(tmp_path / "none.xlsx"))["met"] is False
    assert toolbox.check_completion("c-none", str(saved)) == {"met": False, "by": "harness",
                                                              "detail": "unknown completion id"}


def test_human_check_asks_a_completion_question(tmp_path, store):
    entry = store.add({"type": "completion", "task": "입력 작업", "checks": [{"kind": "human"}]})
    asker = ScriptedAsker([Answer(text="", accepted=True)])
    toolbox, _, _ = toolbox_with(tmp_path, asker, knowledge=store)

    assert toolbox.check_completion(entry["id"], None) == {"met": True, "by": "human",
                                                           "detail": "사람이 결과를 확인함"}
    assert asker.questions[0]["kind"] == "completion"


def test_refusal_by_the_policy_attaches_a_capture_for_the_question(tmp_path):
    toolbox, _, observer = toolbox_with(tmp_path, ScriptedAsker([]))
    toolbox.call("look", {"mark": None})
    toolbox.take_attachments()

    result = toolbox.call("click_point", point_call(capture="c1", x=10, y=20))

    [shot] = toolbox.take_attachments()
    assert result["capture"] == shot.id
    assert observer.events[-1]["type"] == "capture" and observer.events[-1]["reason"] == "question"
    assert observer.actions[-1][3]["refused_code"] == "needs_confirmation"


def test_policy_refusal_with_images_off_has_no_capture(tmp_path):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([]), image_mode="off")
    click = {"window_id": "w-1", "x": 1050, "y": 600, "button": "left", "intent": "누른다"}
    result = toolbox.call("mouse_click", click)
    assert result["refused_code"] == "needs_confirmation" and "capture" not in result
    assert toolbox.take_attachments() == []


def test_precheck_reports_the_refusal_without_running_or_consuming_anything(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text="맞아")]))
    toolbox.call("look", {"mark": None})
    click = point_call(capture="c1", x=10, y=20)
    calls_before = len(cli.calls)

    assert toolbox.precheck("click_point", click)["refused_code"] == "needs_confirmation"
    assert toolbox.precheck("click_part", part_call(mark=3, part="center")) is None
    assert toolbox.precheck("observe", OBSERVE) is None
    assert len(cli.calls) == calls_before
    toolbox.call("ask", question(kind="confirm", capture=("c1", 10, 20)))
    assert toolbox.precheck("click_point", click) is None
    assert toolbox.call("click_point", click)["ok"]


def test_new_toolbox_has_no_finish_note_and_no_exchanges(tmp_path):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([]))
    assert toolbox.finish_note is None and toolbox.exchanges() == [] and toolbox.can_ask is True


def test_knowledge_id_is_not_part_of_the_guard_identity(tmp_path, store):
    first, second = resolution(store), resolution(store)
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store)
    point = {"window_id": "w-1", "x": 1050, "y": 600, "button": "left", "intent": "누른다", "expect": None}
    for _ in range(3):
        toolbox.call("mouse_click", {**point, "knowledge_id": first["id"]})
    with pytest.raises(AgentStopped):
        toolbox.call("mouse_click", {**point, "knowledge_id": second["id"], "intent": "다시"})


def test_stale_capture_is_reported_before_a_confirmation_is_asked_for(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([]))
    toolbox.call("look", {"mark": None})
    cli.window_shift = 100
    assert toolbox.call("click_point", point_call(capture="c1", x=10, y=20))["refused_code"] == "stale_capture"



def cell_candidates(toolbox):
    found = toolbox.call("find_cells", {"rows": ["극판", "소계"], "columns": ["양품수"]})
    return [candidate["mark"] for candidate in found["candidates"]]


def test_a_confirmed_cell_that_was_clicked_stops_being_ambiguous(tmp_path):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text="1번")]), scene=TABLE_SCENE)
    toolbox.call("ask", question(kind="confirm", marks=cell_candidates(toolbox), proposed=1))

    assert toolbox.call("click", {"ref": "@s1:e2"})["ok"]
    assert toolbox.call("click", {"ref": "@s1:e3"})["ok"]


def test_precheck_does_not_clear_the_ambiguity(tmp_path, store):
    entry = resolution(store)
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store, scene=TABLE_SCENE)
    cell_candidates(toolbox)

    assert toolbox.precheck("click", {"ref": "@s1:e2", "knowledge_id": entry["id"]}) is None
    assert toolbox.call("click", {"ref": "@s1:e2"})["refused_code"] == "needs_confirmation"


def test_a_cited_cell_that_was_clicked_stops_being_ambiguous(tmp_path, store):
    entry = resolution(store)
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([]), knowledge=store, scene=TABLE_SCENE)
    cell_candidates(toolbox)

    assert toolbox.call("click", {"ref": "@s1:e2", "knowledge_id": entry["id"]})["ok"]
    assert toolbox.call("click", {"ref": "@s1:e3"})["ok"]


@pytest.mark.parametrize("text", ["2", " 2번 ", "2 번", "②"])
def test_an_answer_naming_one_candidate_allows_only_that_one(tmp_path, text):
    toolbox, cli, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text=text)]), scene=TABLE_SCENE)
    toolbox.call("ask", question(kind="confirm", marks=cell_candidates(toolbox), proposed=1))

    assert toolbox.call("click", {"ref": "@s1:e2"})["refused_code"] == "needs_confirmation"
    assert toolbox.call("click", {"ref": "@s1:e3"})["ok"]


@pytest.mark.parametrize("text", ["아래쪽 것", "5번", "1번이나 2번"])
def test_any_other_answer_allows_every_candidate(tmp_path, text):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text=text)]), scene=TABLE_SCENE)
    toolbox.call("ask", question(kind="confirm", marks=cell_candidates(toolbox), proposed=1))
    assert toolbox.call("click", {"ref": "@s1:e2"})["ok"]


def test_a_pinned_answer_allows_only_the_pinned_place(tmp_path):
    pin = (CELL_B["x"] + 40, CELL_B["y"] + 12)
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text="1번", pin=pin)]), scene=TABLE_SCENE)
    toolbox.call("ask", question(kind="confirm", marks=cell_candidates(toolbox), proposed=1))

    assert toolbox.call("click", {"ref": "@s1:e2"})["refused_code"] == "needs_confirmation"
    assert toolbox.call("click", {"ref": "@s1:e3"})["ok"]


def test_a_number_answer_to_a_capture_candidate_allows_its_point(tmp_path):
    toolbox, _, _ = toolbox_with(tmp_path, ScriptedAsker([Answer(text="2번")]))
    toolbox.call("look", {"mark": None})
    toolbox.call("ask", question(kind="confirm", marks=[3], capture=("c1", 10, 20), proposed=2))

    assert toolbox.call("click_point", point_call(capture="c1", x=10, y=20))["ok"]
