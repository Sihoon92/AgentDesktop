"""Refuses repeated actions that could double-apply or loop."""

import re
from collections import Counter


class AgentStopped(Exception):
    """The run must end; the message says why."""


def signature(tool, element, *arguments):
    """Identifies an action across snapshots, ignoring punctuation such as a label's arrow."""
    return (
        tool,
        element.get("role"),
        _normalized(element.get("name")),
        _normalized(element.get("description")),
        *arguments,
    )


def _normalized(text):
    return re.sub(r"[\W_]+", "", text or "")


class ActionGuard:
    def __init__(self, max_repeats=3):
        self._max_repeats = max_repeats
        self._counts = Counter()
        self._blocked = {}

    def check(self, sig):
        """Returns a refusal message, or None when the action may run."""
        if self._counts[sig] >= self._max_repeats:
            raise AgentStopped(f"Same action ran {self._max_repeats} times: {sig}")
        return self._blocked.get(sig)

    def record(self, sig, result):
        self._counts[sig] += 1
        disposition = result.get("disposition") or {}
        if disposition.get("delivery") == "not_delivered":
            return
        if sig[0] == "toggle":
            self._blocked[sig] = (
                "This toggle was already delivered and another would undo it. "
                "Read the current state, then use check or uncheck."
            )
        elif not result.get("ok") and disposition.get("retry") != "safe":
            self._blocked[sig] = (
                "The previous attempt may have landed and is not safe to repeat. "
                "Read the current state and choose from that."
            )
