import threading

import pytest

from agent.ask import NO_ANSWER, Answer, AskGate, NoAsker, TerminalAsker

STUCK_WITH_TWO = {
    "type": "question", "id": "q1", "step": 3, "kind": "stuck",
    "text": "여기서 무엇을 눌러야 하나요?",
    "candidates": [
        {"n": 1, "label": "라인 칸의 가운데", "mark": 4, "bounds": None, "point": None},
        {"n": 2, "label": "화살표", "mark": None, "bounds": None, "point": [300, 200]},
    ],
    "proposed": None, "basis": [], "window_id": "w-1",
}

CONFIRM_WITH_PROPOSAL = {
    "type": "question", "id": "q2", "step": 4, "kind": "confirm",
    "text": "이 셀이 맞나요?",
    "candidates": [
        {"n": 1, "label": "극판 소계 × 양품수", "mark": 57, "bounds": None, "point": None},
        {"n": 2, "label": "극판 코팅 × 양품수", "mark": 58, "bounds": None, "point": None},
    ],
    "proposed": 1, "basis": [{"mark": 12, "bounds": None}],
}

COMPLETION = {"type": "question", "id": "q3", "step": 9, "kind": "completion",
              "text": "결과가 맞나요?", "candidates": []}

REGISTRATION_OF_THREE = {
    "type": "question", "id": "q4", "step": 10, "kind": "registration",
    "text": "등록할 내용을 확인해 주세요",
    "candidates": [],
    "items": [
        {"n": 1, "action": "add", "id": None,
         "entry": {"type": "resolution", "situation": "가운데를 눌러도 목록이 안 열린다",
                   "resolution": "오른쪽 끝의 화살표를 누른다"}},
        {"n": 2, "action": "update", "id": "r-20261113-001",
         "entry": {"type": "resolution", "situation": "선택 칸이 안 열린다",
                   "resolution": "가운데를 두 번 누른다"}},
        {"n": 3, "action": "add", "id": None,
         "entry": {"type": "completion", "task": "엑셀로 저장하는 작업",
                   "checks": [{"kind": "file", "pattern": "*.xlsx"}]}},
    ],
}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def advancing(self, step):
        def tick():
            value = self.now
            self.now += step
            return value
        return tick


def scripted(*lines):
    answers = iter(lines)
    return lambda _prompt: next(answers)


def closed(_prompt):
    raise EOFError


def answer_when_waiting(gate, question_id, answer):
    """Answers from another thread as soon as the question is waiting."""
    def keep_trying():
        for _ in range(500):
            if gate.answer(question_id, answer):
                return
            threading.Event().wait(0.01)
    thread = threading.Thread(target=keep_trying)
    thread.start()
    return thread


def answered_gate(question_id):
    gate = AskGate(timeout_s=5)
    helper = answer_when_waiting(gate, question_id, Answer(text="처음"))
    assert gate.ask({"id": question_id, "kind": "stuck", "text": "?"}).text == "처음"
    helper.join(timeout=5)
    return gate


def test_answer_from_another_thread_releases_the_question():
    gate = AskGate(timeout_s=5)
    helper = answer_when_waiting(gate, "q1", Answer(text="2번"))

    assert gate.ask({"id": "q1", "kind": "stuck", "text": "?"}).text == "2번"
    helper.join(timeout=5)


def test_on_waiting_runs_once_the_question_can_be_answered():
    gate = AskGate(timeout_s=5)
    seen = []

    def on_waiting():
        seen.append(gate.waiting("q1"))
        seen.append(gate.answer("q1", Answer(text="바로")))

    answer = gate.ask({"id": "q1", "kind": "stuck", "text": "?"}, on_waiting=on_waiting)

    assert seen == [True, True]
    assert answer == Answer(text="바로")
    assert gate.waiting("q1") is False


def test_on_waiting_does_not_hold_the_gate_lock():
    gate = AskGate(timeout_s=5)
    answered = []
    during_callback = []

    def on_waiting():
        helper = threading.Thread(target=lambda: answered.append(gate.answer("q1", Answer(text="다른 스레드"))))
        helper.start()
        helper.join(timeout=1)
        during_callback.extend(answered)

    assert gate.ask({"id": "q1", "kind": "stuck", "text": "?"}, on_waiting=on_waiting).text == "다른 스레드"
    assert during_callback == [True]


def test_a_failing_on_waiting_leaves_no_question_waiting():
    gate = AskGate(timeout_s=5)

    def broken():
        raise RuntimeError("recording failed")

    with pytest.raises(RuntimeError):
        gate.ask({"id": "q1", "kind": "stuck", "text": "?"}, on_waiting=broken)
    assert gate.waiting("q1") is False
    assert gate.answer("q1", Answer(text="늦음")) is False


def test_answer_keeps_the_pin_and_the_completion_fields():
    gate = AskGate(timeout_s=5)
    sent = Answer(text="여기", pin=(1018, 520), accepted=None)
    helper = answer_when_waiting(gate, "q1", sent)

    assert gate.ask({"id": "q1", "kind": "stuck", "text": "?"}) == sent
    helper.join(timeout=5)


def test_unanswered_question_times_out_as_no_answer():
    clock = FakeClock()
    gate = AskGate(timeout_s=30, clock=clock.advancing(31))

    assert gate.ask({"id": "q1", "kind": "stuck", "text": "?"}) is NO_ANSWER


def test_timed_out_question_cannot_be_answered_later():
    clock = FakeClock()
    gate = AskGate(timeout_s=30, clock=clock.advancing(31))
    gate.ask({"id": "q1", "kind": "stuck", "text": "?"})

    assert gate.answer("q1", Answer(text="늦은 답")) is False


def test_second_answer_and_unknown_id_are_rejected():
    gate = answered_gate("q1")

    assert gate.answer("q1", Answer(text="또")) is False
    assert gate.answer("q9", Answer(text="?")) is False


def test_second_answer_before_the_asker_wakes_is_rejected_and_the_first_wins():
    gate = AskGate(timeout_s=5)
    seen = []
    asker = threading.Thread(target=lambda: seen.append(gate.ask({"id": "q1", "kind": "stuck", "text": "?"})))
    asker.start()
    for _ in range(500):
        if gate.waiting("q1"):
            break
        threading.Event().wait(0.01)

    with gate._changed:
        first = gate.answer("q1", Answer(text="1번"))
        second = gate.answer("q1", Answer(text="2번"))
    asker.join(timeout=5)

    assert (first, second) == (True, False)
    assert seen == [Answer(text="1번")]


def test_answer_before_the_question_is_asked_is_rejected():
    assert AskGate().answer("q1", Answer(text="먼저")) is False


def test_waiting_reports_only_unanswered_questions():
    gate = AskGate(timeout_s=5)
    seen = []

    def ask():
        seen.append(gate.ask({"id": "q1", "kind": "stuck", "text": "?"}))

    asker = threading.Thread(target=ask)
    asker.start()
    for _ in range(500):
        if gate.waiting("q1"):
            break
        threading.Event().wait(0.01)

    assert gate.waiting("q1") is True
    assert gate.waiting("q9") is False
    assert gate.answer("q1", Answer(text="1번")) is True
    asker.join(timeout=5)
    assert gate.waiting("q1") is False
    assert seen == [Answer(text="1번")]


def test_gates_say_whether_someone_can_answer():
    assert AskGate.interactive is True
    assert TerminalAsker.interactive is True
    assert NoAsker.interactive is False


def test_no_answer_is_a_timed_out_empty_answer():
    assert NO_ANSWER == Answer(timed_out=True)
    assert NO_ANSWER.text == "" and NO_ANSWER.pin is None and NO_ANSWER.items == ()


def test_terminal_lists_candidates_and_returns_the_typed_line():
    shown = []
    asker = TerminalAsker(ask=lambda _prompt: "2번", show=shown.append)

    answer = asker.ask(STUCK_WITH_TWO)

    assert answer == Answer(text="2번")
    screen = "\n".join(shown)
    assert "여기서 무엇을 눌러야 하나요?" in screen
    assert "1. 라인 칸의 가운데" in screen
    assert "2. 화살표" in screen


def test_terminal_marks_the_proposed_candidate():
    shown = []
    TerminalAsker(ask=lambda _prompt: "맞아", show=shown.append).ask(CONFIRM_WITH_PROPOSAL)

    proposed = [line for line in shown if "극판 소계" in line]
    others = [line for line in shown if "극판 코팅" in line]
    assert proposed and "제안" in proposed[0]
    assert others and "제안" not in others[0]


def test_terminal_strips_the_typed_line():
    assert TerminalAsker(ask=lambda _p: "  아니, 코팅 쪽 \n", show=lambda _l: None).ask(
        CONFIRM_WITH_PROPOSAL).text == "아니, 코팅 쪽"


@pytest.mark.parametrize("typed,accepted,text", [
    ("y", True, ""),
    ("Y 잘 됐어", True, "잘 됐어"),
    ("n 파일이 비었어", False, "파일이 비었어"),
    ("", False, ""),
    ("파일이 없어", False, "파일이 없어"),
])
def test_terminal_completion_answer(typed, accepted, text):
    answer = TerminalAsker(ask=lambda _prompt: typed, show=lambda _line: None).ask(COMPLETION)

    assert answer == Answer(text=text, accepted=accepted)


def test_terminal_registration_approves_edits_and_rejects():
    asker = TerminalAsker(ask=scripted("y", "오른쪽 끝의 화살표로 연다", "n"), show=lambda _line: None)

    items = asker.ask(REGISTRATION_OF_THREE).items

    assert [item["decision"] for item in items] == ["approve", "edit", "reject"]
    assert [item["n"] for item in items] == [1, 2, 3]
    assert items[1]["entry"]["resolution"] == "오른쪽 끝의 화살표로 연다"
    assert items[1]["entry"]["situation"] == "선택 칸이 안 열린다"
    assert "entry" not in items[0] and "entry" not in items[2]


def test_terminal_registration_edit_of_a_completion_changes_its_task():
    asker = TerminalAsker(ask=scripted("", "", "결과를 엑셀로 저장하는 작업"), show=lambda _line: None)

    items = asker.ask(REGISTRATION_OF_THREE).items

    assert [item["decision"] for item in items] == ["reject", "reject", "edit"]
    assert items[2]["entry"]["task"] == "결과를 엑셀로 저장하는 작업"
    assert items[2]["entry"]["checks"] == [{"kind": "file", "pattern": "*.xlsx"}]


def test_terminal_registration_edit_does_not_change_the_question():
    asker = TerminalAsker(ask=scripted("n", "고친 글", "n"), show=lambda _line: None)

    asker.ask(REGISTRATION_OF_THREE)

    assert REGISTRATION_OF_THREE["items"][1]["entry"]["resolution"] == "가운데를 두 번 누른다"


def test_terminal_registration_shows_each_item():
    shown = []
    TerminalAsker(ask=scripted("n", "n", "n"), show=shown.append).ask(REGISTRATION_OF_THREE)

    screen = "\n".join(shown)
    assert "오른쪽 끝의 화살표를 누른다" in screen
    assert "r-20261113-001" in screen
    assert "엑셀로 저장하는 작업" in screen


@pytest.mark.parametrize("question", [STUCK_WITH_TWO, COMPLETION, REGISTRATION_OF_THREE])
def test_terminal_eof_is_no_answer(question):
    assert TerminalAsker(ask=closed, show=lambda _line: None).ask(question) is NO_ANSWER


def test_terminal_eof_in_the_middle_of_registration_is_no_answer():
    lines = iter(["y"])

    def ask(_prompt):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    assert TerminalAsker(ask=ask, show=lambda _line: None).ask(REGISTRATION_OF_THREE) is NO_ANSWER


def test_no_asker_never_answers():
    assert NoAsker().ask({"id": "q1"}) is NO_ANSWER


STAGE_LIMIT = {"id": "q5", "kind": "stage_limit", "text": "이 단계에서 호출을 다 썼습니다. 계속할까요?", "stage": 2,
               "calls": 8, "intents": ["목록을 연다", None, "두 번째 칸을 누른다"]}
PLAN_LIMIT = {"id": "q6", "kind": "plan_limit", "text": "계획을 여러 번 고쳤습니다. 계속할까요?", "stage": 3,
              "revisions": 3}


@pytest.mark.parametrize(("typed", "expected"), [
    ("", Answer(text="", accepted=True)),
    ("   ", Answer(text="", accepted=True)),
    ("q", Answer(text="", accepted=False)),
    (" Q ", Answer(text="", accepted=False)),
    ("가운데 칸의 화살표를 누르세요", Answer(text="가운데 칸의 화살표를 누르세요", accepted=True)),
])
def test_the_terminal_asks_at_a_stage_limit(typed, expected):
    shown = []
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return typed

    answer = TerminalAsker(ask=ask, show=shown.append).ask(STAGE_LIMIT)

    assert answer == expected
    screen = "\n".join(shown)
    for text in (STAGE_LIMIT["text"], "단계 2", "호출 8", "목록을 연다", "두 번째 칸을 누른다"):
        assert text in screen, text
    assert "None" not in screen
    assert "q" in prompts[0]


@pytest.mark.parametrize(("typed", "accepted"), [("", True), ("q", False), ("다시 나눠", True)])
def test_the_terminal_asks_at_the_plan_limit(typed, accepted):
    shown = []

    answer = TerminalAsker(ask=lambda _prompt: typed, show=shown.append).ask(PLAN_LIMIT)

    assert answer.accepted is accepted
    screen = "\n".join(shown)
    assert PLAN_LIMIT["text"] in screen and "단계 3" in screen and "고친 횟수 3" in screen


@pytest.mark.parametrize("question", [STAGE_LIMIT, PLAN_LIMIT])
def test_terminal_eof_at_a_limit_is_no_answer(question):
    assert TerminalAsker(ask=closed, show=lambda _line: None).ask(question) is NO_ANSWER


def within(seconds, work):
    """What `work` returned, run in a daemon thread; fails instead of hanging after `seconds`."""
    done = []
    thread = threading.Thread(target=lambda: done.append(work()), daemon=True)
    thread.start()
    thread.join(seconds)
    assert done, f"still waiting after {seconds} s"
    return done[0]


def test_closed_ask_gate_answers_no_answer_now_and_later():
    gate = AskGate(timeout_s=1800)
    answers = []
    asker = threading.Thread(target=lambda: answers.append(gate.ask({"id": "q1", "kind": "stuck", "text": "?"})),
                             daemon=True)
    asker.start()
    for _ in range(500):
        if gate.waiting("q1"):
            break
        threading.Event().wait(0.01)

    gate.close()
    asker.join(timeout=5)
    seen = []
    later = within(5, lambda: gate.ask({"id": "q2", "kind": "stuck", "text": "?"},
                                       on_waiting=lambda: seen.append("q2")))

    assert not asker.is_alive()
    assert answers == [NO_ANSWER]
    assert later == NO_ANSWER
    assert seen == []
    assert gate.answer("q1", Answer(text="늦음")) is False
    assert gate.waiting("q1") is False and gate.waiting("q2") is False


def test_closing_while_on_waiting_runs_still_ends_the_question():
    gate = AskGate(timeout_s=1800)

    assert within(5, lambda: gate.ask({"id": "q1", "kind": "stuck", "text": "?"}, on_waiting=gate.close)) == NO_ANSWER
    assert gate.waiting("q1") is False
