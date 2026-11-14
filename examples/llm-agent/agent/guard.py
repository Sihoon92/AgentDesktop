"""Refuses repeated actions that could double-apply or loop.

An action is told apart by its tool, its element and its arguments. The element is its role,
name and description, plus the snapshot's native id when it has one. An element with no name,
description or native id is told apart by where it sits in its window instead: the center of
its bounds relative to the window's top-left, so moving the window keeps it the same element.
Two such places within TOLERANCE_PX on both axes are the same place, so a few pixels of
drift between observations do not make a new element.
"""

import re
from collections import Counter
from dataclasses import dataclass

from agent.policy import TOLERANCE_PX


class AgentStopped(Exception):
    """The run must end; the message says why."""


@dataclass(frozen=True)
class Spot:
    """Where a nameless element sits: its center relative to its window's top-left, in pixels."""

    x: int
    y: int

    def near(self, other):
        return abs(self.x - other.x) <= TOLERANCE_PX and abs(self.y - other.y) <= TOLERANCE_PX


def signature(tool, element, *arguments, origin=None):
    """Identifies an action across snapshots, ignoring punctuation such as a label's arrow.
    `origin` is the (x, y) of the top-left of the window the element was observed in."""
    return (tool, *element_key(element, origin), *arguments)


def element_key(element, origin=None):
    """Role, name and description, then the native id (kind, value) when the element has one,
    else, for an element with no name and no description, its Spot (None without bounds)."""
    name = _normalized(element.get("name"))
    description = _normalized(element.get("description"))
    key = (element.get("role"), name, description)
    native = element.get("native_id") or {}
    if native.get("value"):
        return (*key, (native.get("kind"), native["value"]))
    if name or description:
        return key
    return (*key, _spot(element.get("bounds"), origin))


def _spot(bounds, origin):
    if not bounds or origin is None:
        return None
    center_x = bounds["x"] + bounds["width"] / 2 - origin[0]
    center_y = bounds["y"] + bounds["height"] / 2 - origin[1]
    return Spot(round(center_x), round(center_y))


def _normalized(text):
    return re.sub(r"[\W_]+", "", text or "")


class ActionGuard:
    def __init__(self, max_repeats=3):
        self._max_repeats = max_repeats
        self._counts = Counter()
        self._blocked = {}

    def check(self, sig):
        """Returns a refusal message, or None when the action may run."""
        sig = self._known(sig)
        if self._counts[sig] >= self._max_repeats:
            raise AgentStopped(f"Same action ran {self._max_repeats} times: {sig}")
        return self._blocked.get(sig)

    def record(self, sig, result):
        sig = self._known(sig)
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

    def _known(self, sig):
        """The recorded signature this one names: itself, or, when it holds a Spot, an earlier
        signature that differs only by a Spot near this one."""
        if not any(isinstance(part, Spot) for part in sig):
            return sig
        for known in self._counts:
            if len(known) == len(sig) and all(_same(a, b) for a, b in zip(known, sig)):
                return known
        return sig


def _same(known, part):
    if isinstance(known, Spot) and isinstance(part, Spot):
        return known.near(part)
    return known == part
