"""Lets a person approve or reject each agent action before it reaches the app."""

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    approved: bool
    comment: str = ""
    timed_out: bool = False


NO_ANSWER = Decision(False, "응답 없음", timed_out=True)


class ApprovalGate:
    """Holds each proposal until decide() answers it from any thread, or the timeout passes.

    An unanswered proposal becomes NO_ANSWER, so a closed viewer never blocks a run forever.
    After close() every proposal, waiting or still to come, is NO_ANSWER at once.
    """

    def __init__(self, timeout_s=600, clock=time.monotonic):
        self._timeout_s = timeout_s
        self._clock = clock
        self._changed = threading.Condition()
        self._waiting = {}
        self._closed = False

    def review(self, proposal):
        proposal_id = proposal["id"]
        with self._changed:
            if self._closed:
                return NO_ANSWER
            self._waiting[proposal_id] = None
            deadline = self._clock() + self._timeout_s
            while self._waiting[proposal_id] is None:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    del self._waiting[proposal_id]
                    return NO_ANSWER
                self._changed.wait(remaining)
            return self._waiting.pop(proposal_id)

    def close(self):
        """Answers every waiting proposal with NO_ANSWER, and every later one at once; for a
        stopped run."""
        with self._changed:
            self._closed = True
            for proposal_id, value in self._waiting.items():
                if value is None:
                    self._waiting[proposal_id] = NO_ANSWER
            self._changed.notify_all()

    def decide(self, proposal_id, decision):
        """Answers a waiting proposal. Returns False for an unknown or already answered id."""
        with self._changed:
            if proposal_id not in self._waiting or self._waiting[proposal_id] is not None:
                return False
            self._waiting[proposal_id] = decision
            self._changed.notify_all()
            return True


class TerminalGate:
    """Asks on the terminal for `run.py --approve`. Any answer but y rejects; text after it is the reason."""

    def __init__(self, ask=input, show=print):
        self._ask = ask
        self._show = show

    def review(self, proposal):
        self._show(_describe(proposal))
        try:
            answer = self._ask("승인할까요? [y/N, 거부하면 이유를 이어서 적을 수 있음] ").strip()
        except EOFError:
            return Decision(False, "입력 없음")
        word, _, reason = answer.partition(" ")
        if word.lower() == "y":
            return Decision(True, reason.strip())
        return Decision(False, reason.strip() or "거부")


def _describe(proposal):
    target = proposal.get("target") or {}
    where = target.get("name") or target.get("ref") or target.get("window_id") or "-"
    return f"[{proposal.get('step')}] {proposal['tool']} 대상={where} 의도={proposal.get('intent')}"
