"""When the harness makes the model ask a person before it may change the app.

Two kinds of target need a confirmation first: a point the model chose itself (a raw
coordinate), and any point inside the candidates of the latest table lookup that found
more than one. An answered question allows one such action, at an answered candidate. Once
an action inside the candidates has passed and run, those candidates stop being ambiguous.
Expectations that fail mismatch_limit times in a row, or once right after the model cited
stored knowledge, block every app-changing call until any question is answered.
"""

from agent.geometry import contains

TOLERANCE_PX = 12
NEEDS_CONFIRMATION = "needs_confirmation"
BLOCKED_UNTIL_QUESTION = "blocked_until_question"


class AskPolicy:
    def __init__(self, mismatch_limit=2):
        self._limit = mismatch_limit
        self._ambiguous = []
        self._allowed_rects = []
        self._allowed_points = []
        self._streak = 0
        self.blocked = False
        self._clear_on_run = False

    def refusal(self, raw_point, point, cites_knowledge, consume=True):
        """Returns the refused_code for this action, or None when it may run.

        consume=False judges the same way and changes nothing, so the real call keeps an
        answer's permission.
        """
        if consume:
            self._clear_on_run = False
        if self.blocked:
            return BLOCKED_UNTIL_QUESTION
        ambiguous = self._in_ambiguous(point)
        if not raw_point and not ambiguous:
            return None
        if cites_knowledge:
            self._clear_on_run = consume and ambiguous
            return None
        if point is not None and self._allowed(point):
            if consume:
                self._allowed_rects = []
                self._allowed_points = []
                self._clear_on_run = ambiguous
            return None
        return NEEDS_CONFIRMATION

    def note_ran(self):
        """Tells the policy the call it last allowed has run. If that call pressed inside the
        ambiguous candidates (with consume=True), the ambiguity is settled and forgotten."""
        if self._clear_on_run:
            self._ambiguous = []
        self._clear_on_run = False

    def note_ambiguous(self, rects):
        """Records the candidate areas of the latest table lookup; fewer than two clears them."""
        self._ambiguous = list(rects) if len(rects) >= 2 else []

    def note_answer(self, rects, points):
        """Records an answered question: it allows one action at these candidates and unblocks."""
        self._allowed_rects = list(rects)
        self._allowed_points = [tuple(p) for p in points]
        self._streak = 0
        self.blocked = False

    def note_result(self, expected_met, cited_knowledge):
        """Counts an action's outcome against the expectation it declared."""
        if expected_met:
            self._streak = 0
            return
        self._streak += 1
        if cited_knowledge or self._streak >= self._limit:
            self.blocked = True

    def _in_ambiguous(self, point):
        return point is not None and any(contains(rect, *point) for rect in self._ambiguous)

    def _allowed(self, point):
        if any(contains(rect, *point) for rect in self._allowed_rects):
            return True
        return any(
            abs(point[0] - x) <= TOLERANCE_PX and abs(point[1] - y) <= TOLERANCE_PX
            for x, y in self._allowed_points
        )
