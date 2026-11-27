"""The Toolbox part that works from marks and captures: click_part, click_point, look,
find_cells, and the before/after comparison every app-changing call reports.

ScreenTools is a mixin of Toolbox and uses its window and observation helpers
(_rows, _owned_window, _snapshot, _physical, _marks, _observer, _profile).
"""

import tempfile
from pathlib import Path

from agent import capture as capturing
from agent.effects import WINDOW_EXPECTATIONS, diff, met, state
from agent.frames import to_image_rect
from agent.geometry import contains, has_area, part_point
from agent.observe import render_marked
from agent.results import refused
from agent.table import find_cells
from agent.vision import auto_question

CLICK_ACTIONS = {"click": ("left", 1), "double_click": ("left", 2), "right_click": ("right", 1)}
POINT_ARGUMENTS = (("x", "y"), ("from_x", "from_y"), ("to_x", "to_y"))
UNCONFIRMED = "unconfirmed"
ELEMENT_EXPECTATIONS = ("content_changed", "value_changed")


class ScreenTools:
    def _init_screen(self, captures_dir, vision=None):
        self._captures_dir = Path(captures_dir) if captures_dir is not None else None
        self._marks = {}
        self._marks_view = None
        self._captures = {}
        self._capture_count = 0
        self._picture_count = 0
        self._attachments = []
        self._capture_marks = {}
        self._vision = vision
        self._vision_calls = 0
        self._vision_tokens = 0

    @property
    def vision_enabled(self):
        """Whether a vision model reads the pictures (split mode)."""
        return self._vision is not None

    def take_attachments(self):
        """Returns the captures made since the last call, and forgets them."""
        attachments, self._attachments = self._attachments, []
        return attachments

    def take_vision_usage(self):
        """(vision reads, vision tokens) since the last call, which resets both to 0."""
        usage = (self._vision_calls, self._vision_tokens)
        self._vision_calls = self._vision_tokens = 0
        return usage

    def _ask_vision(self, shot, question, asked_by):
        """Asks the vision model `question` about capture `shot` and counts the read. Returns
        (the tool result's vision value, the vision event); the event is not sent."""
        with self.progress.span("model", name="vision") as span:
            answer = self._vision.read(shot, question, _drawn_marks(shot, self._capture_marks.get(shot.id)))
            span.finish(ok=True, tokens=answer.tokens or None)
        self._vision_calls += 1
        self._vision_tokens += answer.tokens
        event = {"type": "vision", "step": None, "capture": shot.id, "asked_by": asked_by,
                 "question": question, **answer.as_event()}
        return answer.as_result(), event

    def _capture_after_action(self, reason, expect, target, before):
        """The capture after a mismatched or unconfirmed action: (Capture, capture event,
        vision value or None, vision event or None), or None when nothing was captured.

        In split mode the capture is cut around where the target was before the action (the
        whole window when that is unknown) and the vision model is asked about the target's
        state; the question names no mark, since the re-read numbers marks afresh."""
        if not self.vision_enabled:
            taken = self._capture_current(reason)
            return None if taken is None else (*taken, None, None)
        known = (before or {}).get("target")
        taken = self._capture_current(reason, around=known[1] if known else None)
        if taken is None:
            return None
        vision, event = self._ask_vision(taken[0], auto_question(reason, expect, target), reason)
        return (*taken, vision, event)

    def _part_preview(self, arguments):
        element = self._marks.get(arguments.get("mark"))
        if element is None:
            return None
        target = {key: element.get(key) for key in ("mark", "ref", "role", "name", "description", "bounds")}
        if element.get("alias"):
            target["alias"] = element["alias"]
        target["window_id"] = self._marks_window()
        point = self._part_point(element, arguments.get("part"))
        target["points"] = [list(point)] if point else []
        return target

    def _point_preview(self, arguments):
        shot = self._captures.get(arguments.get("capture"))
        if shot is None:
            return None
        point = capturing.to_screen(shot, arguments.get("x"), arguments.get("y"))
        window_id = capturing.window_at(shot, *point) if point else shot.window_id
        return {"window_id": window_id, "points": [list(point)] if point else []}

    def _tool_click_part(self, arguments):
        number = arguments["mark"]
        element = self._marks.get(number)
        if element is None:
            return refused(f"[{number}] is not in the latest observation. Observe again.")
        if "offscreen" in (element.get("states") or []):
            return refused(f"[{number}] is offscreen. Scroll it into view and observe again.")
        if not has_area(element.get("bounds")):
            return refused(f"[{number}] has no area on screen, so there is nothing to click.")
        if arguments["action"] not in CLICK_ACTIONS:
            return refused("action must be one of " + ", ".join(CLICK_ACTIONS))
        try:
            x, y = part_point(element["bounds"], arguments["part"])
        except ValueError as error:
            return refused(str(error))
        args = self._click_args(x, y, arguments["action"])
        window = {"window_id": self._window_under((x, y))}
        identity = [arguments["part"], arguments["action"]]
        return self._physical("click_part", window, [(x, y)], args, element=element, identity=identity,
                              observed=self._marks_window())

    def _tool_click_point(self, arguments):
        capture_id = arguments["capture"]
        shot, refusal = self._fresh_capture(capture_id)
        if refusal is not None:
            return refusal
        if arguments["action"] not in CLICK_ACTIONS:
            return refused("action must be one of " + ", ".join(CLICK_ACTIONS))
        x, y = arguments["x"], arguments["y"]
        point = capturing.to_screen(shot, x, y)
        if point is None:
            return refused(_outside(shot, x, y))
        args = self._click_args(*point, arguments["action"])
        identity = [point[0], point[1], arguments["action"]]
        window = {"window_id": capturing.window_at(shot, *point)}
        return self._physical("click_point", window, [point], args, identity=identity, observed=shot.window_id)

    def _window_under(self, point, rows=None):
        """The window click_part presses at `point`: the topmost window of the observed window's
        picture (capture.stacked, drawn in the same order) that holds the point, else the
        observed window. A popup element past the window's edge is pressed on the popup.
        `rows` is a window list just read, or None to read one."""
        window_id = self._marks_window()
        rows = self._rows() if rows is None else rows
        main = next((row for row in rows if row["id"] == window_id), None)
        if main is None:
            return window_id
        for row in capturing.stacked(main, rows):
            if contains(row["bounds"], *point):
                return row["id"]
        return window_id

    def _fresh_capture(self, capture_id):
        """The capture with that id while none of its windows has moved or closed:
        (Capture, None) or (None, refusal)."""
        shot = self._captures.get(capture_id)
        if shot is None:
            return None, refused(f"Capture {capture_id} is unknown. Call look again.", "stale_capture")
        listed = {row["id"]: row.get("bounds") for row in self._rows()}
        shown = shot.windows or ((shot.window_id, shot.window_bounds),)
        if any(window_id not in listed or listed[window_id] != bounds for window_id, bounds in shown):
            return None, refused(
                f"A window moved, resized or closed after capture {capture_id}. Call look again.",
                "stale_capture",
            )
        return shot, None

    def _tool_look(self, arguments):
        question = (arguments.get("question") or "").strip()
        if self.vision_enabled and not question:
            return refused("look needs a question for the vision model: the one thing you need to know.")
        if self._image_mode() == "off" and not self.vision_enabled:
            return refused(
                "Images are turned off for this run. Use the [n] marks in the observation, "
                "find_cells for table cells, and click_part for elements without a ref."
            )
        number = arguments.get("mark")
        around = None
        if number is not None:
            element = self._marks.get(number)
            if element is None:
                return refused(f"[{number}] is not in the latest observation. Observe again.")
            around = element.get("bounds")
        window_id = self._marks_window() or self._current_window
        rows = self._rows() if window_id else []
        row = next((r for r in rows if r["id"] == window_id), None)
        if row is None:
            return refused(f"{window_id} is not a window of the target process")
        taken = self._take_capture(row, around, "look", number, rows)
        if taken is None:
            return refused("The screenshot failed. Observe again, then retry look.")
        shot, event = taken
        self._emit(event)
        result = {"ok": True, "capture": shot.id, "window_id": shot.window_id, "width": shot.width,
                  "height": shot.height, "drawn": shot.drawn, "cropped": shot.cropped}
        if shot.missing:
            result["windows_left_out"] = shot.missing
        if self.vision_enabled:
            result["vision"], vision_event = self._ask_vision(shot, question, "look")
            self._emit(vision_event)
        return result

    def _tool_find_cells(self, arguments):
        try:
            found = find_cells(list(self._marks.values()), arguments["rows"], arguments["columns"])
        except ValueError as error:
            return refused(str(error))
        self._note_cells(found["candidates"])
        return {"ok": True, **found}

    def _note_cells(self, candidates):
        """Hook for the asking part: the candidates of the latest table lookup."""

    def _part_point(self, element, part):
        try:
            return part_point(element.get("bounds"), part)
        except ValueError:
            return None

    def _click_args(self, x, y, action):
        button, count = CLICK_ACTIONS[action]
        return ["mouse-click", f"--xy={x},{y}", f"--button={button}", f"--count={count}"]

    def _image_mode(self):
        return getattr(self._task, "image_mode", "user_message")

    def _marks_window(self):
        return self._marks_view[0] if self._marks_view else None

    def _acting_window(self, name, arguments):
        """The window the action's re-read will show while it stays open: for click_part and
        click_point the window whose marks or capture the target came from, even when the
        point falls on a popup over it."""
        if name == "click_point":
            shot = self._captures.get(arguments.get("capture"))
            return shot.window_id if shot else None
        if name == "click_part":
            return self._marks_window()
        return arguments.get("window_id") or self._current_window

    def _before_state(self, name, arguments):
        """The window list now, the marks of the window the action's re-read will show, and,
        when the call declares an expectation a picture can speak to, the picture around its
        target (see _picture). A window expectation is answered by the window list alone.
        In split mode it also keeps the target's (window id, screen bounds) as "target", even
        for a window expectation, so the capture after a mismatch can be cut around it."""
        rows = self._rows()
        window_id = self._acting_window(name, arguments)
        if window_id is not None and self._marks_view == (window_id, True):
            before = state(rows, self._marks, window_id)
        else:
            envelope = self._snapshot(window_id) if window_id else {}
            marks = render_marked(envelope["data"]["tree"], profile=self._profile)[2] if envelope.get("ok") else {}
            before = state(rows, marks, window_id)
        expect = arguments.get("expect")
        picturing = expect is not None and expect not in WINDOW_EXPECTATIONS
        target = self._picture_target(name, arguments, rows) if picturing or self.vision_enabled else None
        if self.vision_enabled and target is not None:
            before["target"] = target
        if picturing:
            before["picture"] = self._picture(target, rows)
        return before

    def _judged(self, expect, before, after):
        """The effects of an action, and whether `expect` was met when one was given.

        When the observation text and window list do not meet `expect`, the picture around the
        target is compared with the one taken before the action (none is taken for a window
        expectation, which stays a mismatch). A change there makes the
        verdict UNCONFIRMED (expected_met None): the harness saw something change but cannot
        say whether it was the expected change. effects["picture"] holds that comparison.

        When the marks before and after were read from two different windows, no element was
        compared, so an unmet content or value expectation is UNCONFIRMED too, never a mismatch:
        nothing was checked that could count against the call or the knowledge it cited."""
        if before is None:
            return {}
        if "observation" in after:
            after_state = state(after["windows"], self._marks, after.get("window_id"))
        elif after.get("windows") == []:
            after_state = state([], {})
        else:
            return {}
        effects = diff(before, after_state)
        judged = {"effects": effects}
        if expect is not None:
            judged["expect"] = expect
            judged["expected_met"] = met(expect, effects)
            picture = None if judged["expected_met"] else self._picture_change(before.get("picture"), after)
            if picture is not None:
                effects["picture"] = picture
                if picture["changed"]:
                    judged["expected_met"] = None
                    judged["verdict"] = UNCONFIRMED
            if judged["expected_met"] is False and expect in ELEMENT_EXPECTATIONS and "observed_window" in effects:
                judged["expected_met"] = None
                judged["verdict"] = UNCONFIRMED
        return judged

    def _picture(self, target, rows):
        """The Region around the call's target (see _picture_target) in the window it presses,
        or None when the call has no target on screen or the screenshot cannot be taken."""
        if target is None:
            return None
        window_id, around = target
        row = next((r for r in rows if r["id"] == window_id), None)
        if row is None:
            return None
        return capturing.shoot_region(self._desktop, row, around, self._picture_path())

    def _picture_target(self, name, arguments, rows):
        """(window id, screen bounds) of what the call presses: the element of a ref or a mark,
        or the point(s) of click_point and of the window point tools. None otherwise."""
        if name == "click_part":
            element = self._marks.get(arguments.get("mark"))
            point = self._part_point(element, arguments.get("part")) if element else None
            return (self._window_under(point, rows), element["bounds"]) if point else None
        if name == "click_point":
            shot = self._captures.get(arguments.get("capture"))
            point = capturing.to_screen(shot, arguments.get("x"), arguments.get("y")) if shot else None
            return (capturing.window_at(shot, *point), _box_of([point])) if point else None
        if "ref" in arguments:
            element = self._refs.get(arguments.get("ref"))
            if element is None or not has_area(element.get("bounds")) or self._current_window is None:
                return None
            return self._current_window, element["bounds"]
        points = [(arguments[x], arguments[y]) for x, y in POINT_ARGUMENTS if x in arguments and y in arguments]
        if points and arguments.get("window_id"):
            return arguments["window_id"], _box_of(points)
        return None

    def _picture_change(self, region, after):
        """{"changed", "pixels"} for the region taken before the action, or None when there is
        none or its window is no longer listed or cannot be screenshot the same size."""
        if region is None or region.window_id not in [w.get("id") for w in after.get("windows") or []]:
            return None
        pixels = capturing.changed_pixels(self._desktop, region, self._picture_path())
        if pixels is None:
            return None
        return {"changed": pixels >= capturing.CHANGED_PIXELS, "pixels": pixels}

    def _picture_path(self):
        self._picture_count += 1
        folder = self._captures_path()
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"picture-{self._picture_count}.png"

    def _capture_current(self, reason, around=None):
        """Captures the current window for the model when images are on, or always in split mode,
        cut around `around` (screen bounds) when given: (Capture, event) or None."""
        if (self._image_mode() == "off" and not self.vision_enabled) or not self._current_window:
            return None
        rows = self._rows()
        row = next((r for r in rows if r["id"] == self._current_window), None)
        return self._take_capture(row, around, reason, None, rows) if row else None

    def _take_capture(self, row, around, reason, mark, rows):
        """Screenshots `row` with the other visible windows of the process among `rows`, stacked
        as `rows` lists them (capture.stacked); returns (Capture, capture event) or None. The
        event is not sent. In split mode the capture is kept for the record and the vision model,
        never attached for the text model."""
        self._capture_count += 1
        capture_id = f"c{self._capture_count}"
        marks = self._marks if self._marks_window() == row["id"] else {}
        shot = capturing.take(self._desktop, row, marks, self._captures_path(), capture_id, around=around,
                              stack=capturing.stacked(row, rows))
        if shot is None:
            return None
        self._captures[capture_id] = shot
        self._capture_marks[capture_id] = marks
        if not self.vision_enabled:
            self._attachments.append(shot)
        event = {"type": "capture", "id": capture_id, "step": None, "observation": None, "mark": mark,
                 "reason": reason, "file": f"captures/{shot.path.name}"}
        return shot, event

    def _captures_path(self):
        if self._captures_dir is None:
            self._captures_dir = Path(tempfile.mkdtemp(prefix="agent-captures-"))
        return self._captures_dir

    def _emit(self, event):
        on_event = getattr(self._observer, "on_event", None)
        if on_event is not None:
            on_event(event)


def _drawn_marks(shot, marks):
    """The mark numbers drawn on capture `shot` from the `marks` it was taken with: those whose
    box lies inside the image, or none when no box was drawn (see capture.take)."""
    if not shot.drawn:
        return []
    size = (shot.width, shot.height)
    return [number for number in sorted(marks or {})
            if marks[number].get("bounds")
            and to_image_rect(marks[number]["bounds"], shot.origin, size) is not None]


def _box_of(points):
    """The smallest screen bounds holding every point, one pixel wide at least."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs) + 1, "height": max(ys) - min(ys) + 1}


def _outside(shot, x, y):
    return (
        f"({x}, {y}) is outside capture {shot.id}, which is {shot.width}x{shot.height} "
        "pixels. Call look again and pick a pixel inside the image."
    )
