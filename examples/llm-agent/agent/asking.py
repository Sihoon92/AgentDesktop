"""The Toolbox part that asks a person, enforces when it must, and uses learned knowledge.

AskingTools is a mixin of Toolbox. It owns the `ask` tool and toolbox.ask(), the AskPolicy
gate in front of every app-changing call, knowledge_id checks and counts, the briefing,
completion checks, and the exchanges (question, answer, actions after it) that registration
turns into new entries. Exchanges never hold coordinates.
"""

import inspect
import re

from agent import capture as capturing
from agent.ask import NO_ANSWER
from agent.completion import run_checks
from agent.geometry import has_area, smallest_containing
from agent.guard import AgentStopped
from agent.knowledge import briefing as knowledge_briefing
from agent.knowledge import relevant
from agent.observe import render_marked
from agent.policy import BLOCKED_UNTIL_QUESTION, NEEDS_CONFIRMATION, TOLERANCE_PX, AskPolicy
from agent.results import refused
from agent.tool_schemas import EXPECTING_TOOLS

ASK_KINDS = ("confirm", "stuck")
RAW_POINT_TOOLS = frozenset({"mouse_click", "click_point", "drag"})
NUMBER_ANSWER = re.compile(r"^(\d+)\s*번?$")
CIRCLED_ONE, CIRCLED_LAST = 0x2460, 0x2473
COMPLETION_QUESTION = "결과가 맞나요?"
SCREEN_CHANGED_NOTE = (
    "Windows of the application opened or closed while the user answered, so the [n] marks and "
    "the captures from before the question no longer match the screen. Observe again before acting."
)
BRIEFING_HEADER = (
    "Learned in earlier runs: the user's past answers for this application. When one fits, follow "
    "it without asking again and pass its id as knowledge_id. Completion entries name how to check "
    "a finished task; pass the matching id as completion_id to finish."
)
REFUSAL_REASONS = {
    NEEDS_CONFIRMATION: (
        "This target needs the user's confirmation first: it is a point you picked, or one of several "
        "matching table cells. Call ask with kind \"confirm\", list the candidates and the one you "
        "propose, then repeat this action. If a learned entry covers it, pass its id as knowledge_id."
    ),
    BLOCKED_UNTIL_QUESTION: (
        "Recent actions did not have the effect you expected, or a learned entry failed. Changing the "
        "app is blocked until you ask the user: call ask with kind \"stuck\" and number the candidates "
        "near the problem. Reading tools still work."
    ),
}


class AskingTools:
    def _init_asking(self, asker, knowledge, wall, task, gated=True):
        self.asker = asker
        self.knowledge = knowledge
        self.step = None
        self.finish_note = None
        self.stage_records = []
        self._wall = wall
        self._started_at = wall()
        self._gated = gated
        self._policy = AskPolicy(mismatch_limit=getattr(task, "mismatch_limit", 2))
        self._question_count = 0
        self._question_changes = None
        self._exchanges = []
        self._failed_knowledge = None

    @property
    def can_ask(self):
        return bool(self.asker is not None and getattr(self.asker, "interactive", False))

    def ask(self, question):
        """Asks a person: fills id and step, logs the question and the answer, and stops the run
        (AgentStopped) when the answer never comes. Returns the Answer.

        Answering can change the app without any tool call: a person who switches to the viewer
        takes activation from the app, and a popup list that closes on that is gone. So the next
        action reads the screen afresh instead of reusing the marks from before the question, and
        when a window opened or closed meanwhile the marks are dropped and
        take_question_changes() reports it."""
        self._question_count += 1
        question_id = f"q{self._question_count}"
        question = {"candidates": [], "proposed": None, "basis": [], "items": [], "window_id": None,
                    "text": "", **question, "type": "question", "id": question_id, "step": self.step}
        windows_before = self._rows() if self._pid is not None else None
        answer = self._asked(question)
        pin_element = self._pinned_element(answer.pin)
        changes = self._windows_changed_since(windows_before)
        event = {
            "type": "answer", "id": question_id, "text": answer.text,
            "pin": None if answer.pin is None else {
                "point": [int(answer.pin[0]), int(answer.pin[1])], "image_point": None,
                "mark": pin_element.get("mark") if pin_element else None},
            "accepted": answer.accepted, "items": list(answer.items), "timed_out": answer.timed_out,
        }
        if changes is not None:
            event["windows_changed"] = {key: [w["id"] for w in rows] for key, rows in changes.items()}
        self._emit(event)
        if answer.timed_out:
            raise AgentStopped(f"No answer to question {question_id}, so the run stopped")
        return answer

    def take_question_changes(self):
        """The note for the model when windows of the target opened or closed while the last
        question waited for its answer, or None. Each note is given once."""
        note, self._question_changes = self._question_changes, None
        return note

    def _windows_changed_since(self, windows_before):
        """Marks the screen as unread after a question, and returns {"windows_opened",
        "windows_closed"} when the window list differs from `windows_before`, else None."""
        if self._marks_view is not None:
            self._marks_view = (self._marks_view[0], False)
        if windows_before is None:
            return None
        windows_after = self._rows()
        before_ids = {row["id"] for row in windows_before}
        after_ids = {row["id"] for row in windows_after}
        changes = {
            "windows_opened": [_window_label(row) for row in windows_after if row["id"] not in before_ids],
            "windows_closed": [_window_label(row) for row in windows_before if row["id"] not in after_ids],
        }
        if not (changes["windows_opened"] or changes["windows_closed"]):
            return None
        self._marks = {}
        self._question_changes = {**changes, "note": SCREEN_CHANGED_NOTE}
        return changes

    def _asked(self, question):
        """Records the question and returns the asker's answer. An asker whose ask() takes
        on_waiting (the viewer's gate) records it only once the question can be answered, so a
        question seen in the record is never refused; any other asker sees it already recorded."""
        recorded = []

        def record():
            if not recorded:
                recorded.append(True)
                self._emit(question)

        if self.asker is None:
            record()
            return NO_ANSWER
        if _takes_on_waiting(self.asker):
            answer = self.asker.ask(question, on_waiting=record)
        else:
            record()
            answer = self.asker.ask(question)
        record()
        return answer

    def ask_about_screen(self, question):
        """Asks a question the harness made, such as the stage limit question, over the current
        window: the window is reported as the question's observation first, as for the ask tool."""
        self._show_question_screen()
        return self.ask({"window_id": self._marks_window() or self._current_window, **question})

    def note_exchange(self, question, answer):
        """Keeps a harness question the person answered with text, so registration can turn it into
        an entry like an ask-tool question. The actions after it are logged into it as well."""
        self._exchanges.append({
            "question": {"candidates": [], **question},
            "answer": {"text": answer.text, "pin_on": None},
            "actions": [],
            "failed_knowledge_id": self._failed_knowledge,
        })
        self._failed_knowledge = None

    def evidence(self, marks, capture):
        """Checks stage_done's evidence: exactly one of marks (all in the latest observation) or a
        fresh capture, and actions not blocked until a question. Returns {"ok": True, "kind":
        "text" | "picture"} or a refusal. Whether the evidence means the goal is the model's call."""
        if self._policy.blocked:
            return refused(REFUSAL_REASONS[BLOCKED_UNTIL_QUESTION], BLOCKED_UNTIL_QUESTION)
        if bool(marks) == (capture is not None):
            return refused("Give exactly one kind of evidence: marks from the latest observation, or the "
                           "capture id of a look.", "bad_evidence")
        if capture is not None:
            refusal = self._fresh_capture(capture)[1]
            return refusal if refusal is not None else {"ok": True, "kind": "picture"}
        missing = [number for number in marks if number not in self._marks]
        if missing:
            return refused(f"{', '.join(f'[{n}]' for n in missing)} not in the latest observation. "
                           "Observe again.", "stale_evidence")
        return {"ok": True, "kind": "text"}

    def briefing(self):
        """What the model should know from earlier runs, or "" without knowledge."""
        if self.knowledge is None:
            return ""
        entries = relevant(self.knowledge, self._task.goal)
        text, truncated = knowledge_briefing(entries)
        shown = [entry["id"] for entry in entries if f"({entry['id']})" in text]
        self._emit({"type": "knowledge_loaded", "system": self.knowledge.system, "ids": shown,
                    "truncated": truncated})
        if not text:
            return ""
        more = "\n(More entries exist than fit here.)" if truncated else ""
        return f"{BRIEFING_HEADER}\n{text}{more}"

    def exchanges(self):
        """Each answered ask-tool question with its answer and the actions after it, no coordinates."""
        return [dict(exchange, actions=list(exchange["actions"])) for exchange in self._exchanges]

    def check_completion(self, completion_id, saved_path):
        """Runs the checks of an active completion entry: {"met", "by": "harness" | "human", "detail"}."""
        entry = self.knowledge.get(completion_id) if self.knowledge is not None and completion_id else None
        if entry is None or entry.get("status") != "active" or entry.get("type") != "completion":
            result = {"met": False, "by": "harness", "detail": "unknown completion id"}
        else:
            self.knowledge.count_use(completion_id)
            result = run_checks(
                entry["checks"],
                verify_elements=self.verify_elements,
                saved_path=saved_path,
                started_at=self._started_at,
                ask_human=self._ask_human if self.can_ask else None,
            )
        self._emit({"type": "completion", "by": "human" if result["by"] == "human" else "knowledge",
                    "id": completion_id, "met": result["met"], "detail": result["detail"]})
        return result

    def precheck(self, name, arguments):
        """The refusal the policy would give this call, or None. Runs nothing and uses up nothing."""
        if name not in EXPECTING_TOOLS or not self._may_run(name):
            return None
        return self._gate(name, arguments, consume=False)

    def _ask_human(self):
        return self.ask({"kind": "completion", "text": COMPLETION_QUESTION}).accepted is True

    def _tool_ask(self, arguments):
        if not self.can_ask:
            return refused(
                "No one can answer questions in this run. Keep reading, or call finish with "
                "success=false and say what you needed to know.",
                "no_one_to_ask",
            )
        if arguments.get("kind") not in ASK_KINDS:
            return refused("kind must be one of " + ", ".join(ASK_KINDS))
        candidates = []
        for number, candidate in enumerate(arguments.get("candidates") or [], 1):
            resolved = self._candidate(number, candidate)
            if "refused" in resolved:
                return resolved
            candidates.append(resolved)
        proposed = arguments.get("proposed")
        if proposed is not None and not 1 <= proposed <= len(candidates):
            return refused(f"proposed must be a candidate number from 1 to {len(candidates)}, or null")
        basis = []
        for number in arguments.get("basis") or []:
            element = self._marks.get(number)
            if element is None:
                return refused(f"[{number}] is not in the latest observation. Observe again.")
            basis.append({"mark": number, "bounds": element.get("bounds")})
        self._show_question_screen()
        answer = self.ask({
            "kind": arguments["kind"], "text": arguments.get("text", ""), "candidates": candidates,
            "proposed": proposed, "basis": basis,
            "window_id": self._marks_window() or self._current_window,
        })
        pin_element = self._pinned_element(answer.pin)
        self._allow_answered(candidates, answer)
        self._start_exchange(arguments, candidates, answer, pin_element)
        pin = None if answer.pin is None else {
            "x": int(answer.pin[0]), "y": int(answer.pin[1]),
            "mark": pin_element.get("mark") if pin_element else None}
        result = {"ok": True, "answer": answer.text, "pin": pin}
        changes = self.take_question_changes()
        if changes is not None:
            result["screen_changed"] = changes
        return result

    def _allow_answered(self, candidates, answer):
        """Allows what the answer points at: the pinned place, else the one candidate the answer
        names by number alone, else every candidate."""
        if answer.pin is not None:
            self._policy.note_answer([], [(int(answer.pin[0]), int(answer.pin[1]))])
            return
        named = _named_candidate(answer.text, candidates)
        allowed = [named] if named is not None else candidates
        rects = [c["bounds"] for c in allowed if c["bounds"]]
        points = [tuple(c["point"]) for c in allowed if c["point"]]
        self._policy.note_answer(rects, points)

    def _candidate(self, number, candidate):
        label = candidate.get("label") or ""
        mark = candidate.get("mark")
        resolved = {"n": number, "label": label, "mark": mark, "bounds": None, "point": None}
        if mark is not None:
            element = self._marks.get(mark)
            if element is None:
                return refused(f"Candidate {number}: [{mark}] is not in the latest observation.")
            resolved["bounds"] = element.get("bounds")
        elif candidate.get("capture") is not None:
            shot, refusal = self._fresh_capture(candidate["capture"])
            if refusal is not None:
                return refusal
            point = capturing.to_screen(shot, candidate.get("x"), candidate.get("y"))
            if point is None:
                return refused(f"Candidate {number}: ({candidate.get('x')}, {candidate.get('y')}) "
                               f"is outside capture {shot.id} ({shot.width}x{shot.height}).")
            resolved["point"] = list(point)
        return resolved

    def _show_question_screen(self):
        """Reports the current window as the question's observation without changing the marks
        or refs the model holds, so the viewer draws the boxes on what the person sees now."""
        window_id = self._marks_window() or self._current_window
        row = self._owned_window(window_id) if window_id else None
        if row is None:
            return
        envelope = self._snapshot(row["id"])
        if envelope.get("ok"):
            text = render_marked(envelope["data"]["tree"], profile=self._profile)[0]
            self._observer.on_observation(row, envelope, text, source="question")

    def _pinned_element(self, pin):
        if pin is None:
            return None
        return smallest_containing(list(self._marks.values()), int(pin[0]), int(pin[1]))

    def _start_exchange(self, arguments, candidates, answer, pin_element):
        self._exchanges.append({
            "question": {"kind": arguments["kind"], "text": arguments.get("text", ""),
                         "candidates": [{"n": c["n"], "label": c["label"]} for c in candidates]},
            "answer": {"text": answer.text, "pin_on": _identity(pin_element)},
            "actions": [],
            "failed_knowledge_id": self._failed_knowledge,
        })
        self._failed_knowledge = None

    def _note_cells(self, candidates, whole=None):
        """Tells the policy where the candidates of the latest table lookup are. A candidate with
        neither a box nor a point is off the screen: then the whole table (or, without its box,
        every candidate that has one) stays held until a question is answered."""
        rects = []
        unplaced = False
        for candidate in candidates:
            if has_area(candidate.get("bounds")):
                rects.append(candidate["bounds"])
            elif candidate.get("point") is not None:
                x, y = candidate["point"]
                rects.append({"x": x - TOLERANCE_PX, "y": y - TOLERANCE_PX,
                              "width": 2 * TOLERANCE_PX + 1, "height": 2 * TOLERANCE_PX + 1})
            else:
                unplaced = True
        if unplaced and len(candidates) >= 2:
            self._policy.note_ambiguous([whole] if has_area(whole) else rects, always=True)
        else:
            self._policy.note_ambiguous(rects)

    def _gate(self, name, arguments, consume):
        """The refusal for an app-changing call before it runs, or None. Checks knowledge_id,
        then asks the policy. An ungated toolbox refuses nothing here."""
        if not self._gated:
            return None
        knowledge_id = arguments.get("knowledge_id")
        if knowledge_id is not None and self._active_entry(knowledge_id) is None:
            return refused(
                f"{knowledge_id} is not an active learned entry of this application. "
                "Use an id from the learned list, or null.",
                "unknown_knowledge",
            )
        point = self._press_point(name, arguments)
        if name == "click_point" and (point is None or consume and self._capture_is_stale(arguments)):
            return None
        code = self._policy.refusal(name in RAW_POINT_TOOLS, point, knowledge_id is not None, consume=consume)
        return None if code is None else refused(REFUSAL_REASONS[code], code)

    def _press_point(self, name, arguments):
        """The screen point a call would press, or None when it presses no known point."""
        if name == "mouse_click":
            return (arguments.get("x"), arguments.get("y"))
        if name == "drag":
            return (arguments.get("from_x"), arguments.get("from_y"))
        if name == "click_point":
            shot = self._captures.get(arguments.get("capture"))
            return capturing.to_screen(shot, arguments.get("x"), arguments.get("y")) if shot else None
        if name == "click_part":
            element = self._marks.get(arguments.get("mark"))
            return self._part_point(element, arguments.get("part")) if element else None
        element = self._refs.get(arguments.get("ref")) if "ref" in arguments else None
        if element and has_area(element.get("bounds")):
            return self._part_point(element, "center")
        return None

    def _capture_is_stale(self, arguments):
        """True when click_point's capture no longer matches its window: the tool's own
        stale_capture refusal is more useful to the model than a confirmation request."""
        return self._fresh_capture(arguments.get("capture"))[1] is not None

    def _active_entry(self, entry_id):
        entry = self.knowledge.get(entry_id) if self.knowledge is not None else None
        return entry if entry is not None and entry.get("status") == "active" else None

    def _cite_knowledge(self, arguments):
        """Counts and logs the use of the cited entry. Call only after _gate passed."""
        entry_id = arguments.get("knowledge_id")
        if entry_id is None:
            return
        entry = self._active_entry(entry_id) or {}
        self.knowledge.count_use(entry_id)
        self._emit({"type": "knowledge_used", "step": self.step, "id": entry_id,
                    "text": entry.get("resolution") or entry.get("task")})

    def _note_delivery(self, result):
        """Tells the policy whether the action it allowed reached the app. Only a result that
        says not_delivered (every harness refusal does) keeps the answer and the ambiguity."""
        disposition = result.get("disposition") or {}
        not_delivered = result.get("ok") is False and disposition.get("delivery") == "not_delivered"
        self._policy.note_ran(delivered=not not_delivered)

    def _after_action(self, name, arguments, target, result):
        """Feeds the outcome to the policy, counts a failed citation, and logs the action into
        the open exchange."""
        self._note_delivery(result)
        expected_met = result.get("expected_met")
        knowledge_id = arguments.get("knowledge_id")
        if "expected_met" in result and self._gated:
            self._policy.note_result(expected_met, knowledge_id is not None)
            if expected_met is False and knowledge_id is not None:
                self.knowledge.count_failure(knowledge_id)
                self._emit({"type": "knowledge_failed", "step": self.step, "id": knowledge_id})
                self._failed_knowledge = knowledge_id
        if self._exchanges:
            action = {"tool": name}
            if name == "click_part":
                action["part"] = arguments.get("part")
            action.update(target=_identity(target), expect=arguments.get("expect"),
                          expected_met=expected_met, knowledge_id=knowledge_id)
            self._exchanges[-1]["actions"].append(action)


def _named_candidate(text, candidates):
    """The candidate an answer names by its number alone ("2", "2번", "②"), or None."""
    text = (text or "").strip()
    if len(text) == 1 and CIRCLED_ONE <= ord(text) <= CIRCLED_LAST:
        number = ord(text) - CIRCLED_ONE + 1
    else:
        found = NUMBER_ANSWER.match(text)
        number = int(found.group(1)) if found else None
    return next((c for c in candidates if c["n"] == number), None)


def _identity(element):
    if not element or not element.get("role"):
        return None
    return {"role": element.get("role"), "name": element.get("name")}


def _window_label(row):
    return {"id": row.get("id"), "title": row.get("title")}


def _takes_on_waiting(asker):
    """True when the asker's ask() accepts on_waiting, the callback the viewer's gate runs once a
    question can be answered. TerminalAsker, NoAsker and other one-argument askers do not."""
    try:
        return "on_waiting" in inspect.signature(asker.ask).parameters
    except (TypeError, ValueError):
        return False
