"""When the harness makes the model ask a person before it may change the app.

Two kinds of target need a confirmation first: a point the model chose itself (a raw
coordinate), and any point inside the candidates of the latest table lookup that found
more than one (the whole table when one of them is off the screen). An answered question allows one such action, at an answered candidate. Once
an action inside the candidates has passed and been delivered, those candidates stop being
ambiguous. An allowed action that was not delivered (refused before it ran, or reported
not_delivered) uses up neither the answer nor the ambiguity, so repeating it needs no new
question.
Expectations that fail mismatch_limit times in a row, or once right after the model cited
stored knowledge, block every app-changing call until any question is answered. An
unconfirmed outcome (expected_met None) is left out of that count.
"""

from dataclasses import dataclass

from agent.geometry import contains

TOLERANCE_PX = 12
NEEDS_CONFIRMATION = "needs_confirmation"
BLOCKED_UNTIL_QUESTION = "blocked_until_question"


@dataclass(frozen=True)
class _Pending:
    """What the last allowed action will use up once it is delivered."""

    spends_answer: bool
    settles_ambiguity: bool


class AskPolicy:
    def __init__(self, mismatch_limit=2):
        self._limit = mismatch_limit
        self._ambiguous = []
        self._allowed_rects = []
        self._allowed_points = []
        self._streak = 0
        self.blocked = False
        self._pending = None

    def refusal(self, raw_point, point, cites_knowledge, consume=True):
        """Returns the refused_code for this action, or None when it may run.

        consume=False judges the same way and changes nothing, so the real call keeps an
        answer's permission. With consume=True an allowed action holds what it would use up
        until note_ran says whether it was delivered; an earlier allowed action that never
        reported back is counted as having used the answer.
        """
        if consume:
            self._abandon_pending()
        if self.blocked:
            return BLOCKED_UNTIL_QUESTION
        ambiguous = self._in_ambiguous(point)
        if not raw_point and not ambiguous:
            return None
        if cites_knowledge:
            if consume and ambiguous:
                self._pending = _Pending(spends_answer=False, settles_ambiguity=True)
            return None
        if point is not None and self._allowed(point):
            if consume:
                self._pending = _Pending(spends_answer=True, settles_ambiguity=ambiguous)
            return None
        return NEEDS_CONFIRMATION

    def note_ran(self, delivered=True):
        """Tells the policy whether the call it last allowed (with consume=True) was delivered.
        A delivered call uses up the answer that allowed it, and settles the ambiguity if it
        pressed inside the candidates. A call that was not delivered leaves both in place.
        Delivery that is unknown counts as delivered, since the press may have landed."""
        pending, self._pending = self._pending, None
        if pending is None or not delivered:
            return
        if pending.spends_answer:
            self._spend_answer()
        if pending.settles_ambiguity:
            self._ambiguous = []

    def note_ambiguous(self, rects, always=False):
        """Records the candidate areas of the latest table lookup; fewer than two clears them,
        unless always (a lookup with a candidate off the screen holds the one area it gives)."""
        self._ambiguous = list(rects) if always or len(rects) >= 2 else []

    def note_answer(self, rects, points):
        """Records an answered question: it allows one action at these candidates and unblocks."""
        self._allowed_rects = list(rects)
        self._allowed_points = [tuple(p) for p in points]
        self._streak = 0
        self.blocked = False

    def note_result(self, expected_met, cited_knowledge):
        """Counts an action's outcome against the expectation it declared. None (unconfirmed:
        only the picture around the target changed) neither counts nor clears the streak, and
        does not block a call that cited knowledge."""
        if expected_met is None:
            return
        if expected_met:
            self._streak = 0
            return
        self._streak += 1
        if cited_knowledge or self._streak >= self._limit:
            self.blocked = True

    def _abandon_pending(self):
        if self._pending is not None and self._pending.spends_answer:
            self._spend_answer()
        self._pending = None

    def _spend_answer(self):
        self._allowed_rects = []
        self._allowed_points = []

    def _in_ambiguous(self, point):
        return point is not None and any(contains(rect, *point) for rect in self._ambiguous)

    def _allowed(self, point):
        if any(contains(rect, *point) for rect in self._allowed_rects):
            return True
        return any(
            abs(point[0] - x) <= TOLERANCE_PX and abs(point[1] - y) <= TOLERANCE_PX
            for x, y in self._allowed_points
        )
