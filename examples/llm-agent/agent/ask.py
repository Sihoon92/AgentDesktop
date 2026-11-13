"""Lets the agent ask a person a question and wait for the answer.

AskGate answers through the viewer server, TerminalAsker on the terminal for `run.py`, and NoAsker
stands in when nobody can answer. Every asker returns an Answer; NO_ANSWER means the question went
unanswered, and the run must stop rather than guess.
"""

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Answer:
    text: str = ""
    pin: tuple | None = None
    accepted: bool | None = None
    items: tuple = ()
    timed_out: bool = False


NO_ANSWER = Answer(timed_out=True)

_PENDING = object()


class AskGate:
    """Holds each question until answer() replies from any thread, or the timeout passes.

    An unanswered question becomes NO_ANSWER, so a closed viewer never blocks a run forever.
    """

    interactive = True

    def __init__(self, timeout_s=1800, clock=time.monotonic):
        self._timeout_s = timeout_s
        self._clock = clock
        self._changed = threading.Condition()
        self._waiting = {}

    def ask(self, question):
        question_id = question["id"]
        with self._changed:
            self._waiting[question_id] = _PENDING
            deadline = self._clock() + self._timeout_s
            while self._waiting[question_id] is _PENDING:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    del self._waiting[question_id]
                    return NO_ANSWER
                self._changed.wait(remaining)
            return self._waiting.pop(question_id)

    def answer(self, question_id, answer):
        """Answers a waiting question. Returns False for an unknown or already answered id."""
        with self._changed:
            if self._waiting.get(question_id, None) is not _PENDING:
                return False
            self._waiting[question_id] = answer
            self._changed.notify_all()
            return True

    def waiting(self, question_id):
        """True while the question is asked and still has no answer."""
        with self._changed:
            return self._waiting.get(question_id, None) is _PENDING


class TerminalAsker:
    """Asks on the terminal for `run.py`. Candidates are listed by number; there is no pin."""

    interactive = True

    def __init__(self, ask=input, show=print):
        self._ask = ask
        self._show = show

    def ask(self, question):
        try:
            if question.get("kind") == "completion":
                return self._ask_completion(question)
            if question.get("kind") == "registration":
                return self._ask_registration(question)
            return self._ask_text(question)
        except EOFError:
            return NO_ANSWER

    def _ask_text(self, question):
        self._show_question(question)
        return Answer(text=self._ask("답: ").strip())

    def _ask_completion(self, question):
        self._show_question(question)
        typed = self._ask("결과가 맞나요? [y/N, 뒤에 의견을 이어서 적을 수 있음] ").strip()
        word, _, rest = typed.partition(" ")
        if word.lower() in ("y", "yes"):
            return Answer(text=rest.strip(), accepted=True)
        if word.lower() in ("n", "no"):
            return Answer(text=rest.strip(), accepted=False)
        return Answer(text=typed, accepted=False)

    def _ask_registration(self, question):
        self._show(question.get("text") or "등록할 내용을 확인해 주세요")
        decisions = []
        for item in question.get("items", []):
            self._show(_describe_item(item))
            typed = self._ask("등록할까요? [y 승인 / n 또는 빈 줄 거절 / 고칠 글을 적으면 그 글로 승인] ").strip()
            decisions.append(_decide(item, typed))
        return Answer(items=tuple(decisions))

    def _show_question(self, question):
        self._show(question.get("text", ""))
        proposed = question.get("proposed")
        for candidate in question.get("candidates", []):
            mark = " (제안)" if candidate.get("n") == proposed else ""
            self._show(f"  {candidate.get('n')}. {candidate.get('label', '')}{mark}")


def _decide(item, typed):
    if typed.lower() in ("y", "yes"):
        return {"n": item["n"], "decision": "approve"}
    if typed.lower() in ("", "n", "no"):
        return {"n": item["n"], "decision": "reject"}
    entry = dict(item.get("entry") or {})
    entry[_editable_field(entry)] = typed
    return {"n": item["n"], "decision": "edit", "entry": entry}


def _editable_field(entry):
    return "task" if entry.get("type") == "completion" else "resolution"


def _describe_item(item):
    entry = item.get("entry") or {}
    action = "고침" if item.get("action") == "update" else "새 항목"
    target = f" {item['id']}" if item.get("id") else ""
    if entry.get("type") == "completion":
        kinds = ", ".join(str(check.get("kind")) for check in entry.get("checks", []))
        body = f"완료 조건: {entry.get('task', '')} (확인: {kinds or '-'})"
    else:
        body = f"상황: {entry.get('situation', '')} / 해결: {entry.get('resolution', '')}"
    return f"{item.get('n')}. [{action}{target}] {body}"


class NoAsker:
    """Stands in when nobody can answer. Every question goes unanswered."""

    interactive = False

    def ask(self, question):
        return NO_ANSWER
