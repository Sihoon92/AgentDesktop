"""The Toolbox part that works from marks and captures: click_part, click_point, look,
find_cells, and the before/after comparison every app-changing call reports.

ScreenTools is a mixin of Toolbox and uses its window and observation helpers
(_rows, _owned_window, _snapshot, _physical, _marks, _observer).
"""

import tempfile
from pathlib import Path

from agent import capture as capturing
from agent.effects import diff, met, state
from agent.geometry import has_area, part_point
from agent.observe import render_marked
from agent.results import refused
from agent.table import find_cells

CLICK_ACTIONS = {"click": ("left", 1), "double_click": ("left", 2), "right_click": ("right", 1)}


class ScreenTools:
    def _init_screen(self, captures_dir):
        self._captures_dir = Path(captures_dir) if captures_dir is not None else None
        self._marks = {}
        self._marks_view = None
        self._captures = {}
        self._capture_count = 0
        self._attachments = []

    def take_attachments(self):
        """Returns the captures made since the last call, and forgets them."""
        attachments, self._attachments = self._attachments, []
        return attachments

    def _part_preview(self, arguments):
        element = self._marks.get(arguments.get("mark"))
        if element is None:
            return None
        target = {key: element.get(key) for key in ("mark", "ref", "role", "name", "description", "bounds")}
        target["window_id"] = self._marks_window()
        point = self._part_point(element, arguments.get("part"))
        target["points"] = [list(point)] if point else []
        return target

    def _point_preview(self, arguments):
        shot = self._captures.get(arguments.get("capture"))
        if shot is None:
            return None
        point = capturing.to_screen(shot, arguments.get("x"), arguments.get("y"))
        return {"window_id": shot.window_id, "points": [list(point)] if point else []}

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
        window = {"window_id": self._marks_window()}
        identity = [arguments["part"], arguments["action"]]
        return self._physical("click_part", window, [(x, y)], args, element=element, identity=identity)

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
        return self._physical("click_point", {"window_id": shot.window_id}, [point], args, identity=identity)

    def _fresh_capture(self, capture_id):
        """The capture with that id while its window has not moved: (Capture, None) or (None, refusal)."""
        shot = self._captures.get(capture_id)
        if shot is None:
            return None, refused(f"Capture {capture_id} is unknown. Call look again.", "stale_capture")
        row = self._owned_window(shot.window_id)
        if row is None or row.get("bounds") != shot.window_bounds:
            return None, refused(
                f"The window moved, resized or closed after capture {capture_id}. Call look again.",
                "stale_capture",
            )
        return shot, None

    def _tool_look(self, arguments):
        if self._image_mode() == "off":
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
        row = self._owned_window(window_id) if window_id else None
        if row is None:
            return refused(f"{window_id} is not a window of the target process")
        taken = self._take_capture(row, around, "look", number)
        if taken is None:
            return refused("The screenshot failed. Observe again, then retry look.")
        shot, event = taken
        self._emit(event)
        return {"ok": True, "capture": shot.id, "window_id": shot.window_id, "width": shot.width,
                "height": shot.height, "drawn": shot.drawn, "cropped": shot.cropped}

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
        if name == "click_point":
            shot = self._captures.get(arguments.get("capture"))
            return shot.window_id if shot else None
        if name == "click_part":
            return self._marks_window()
        return arguments.get("window_id") or self._current_window

    def _before_state(self, name, arguments):
        """The window list now, and the marks of the window the action's re-read will show."""
        rows = self._rows()
        window_id = self._acting_window(name, arguments)
        if window_id is not None and self._marks_view == (window_id, True):
            return state(rows, self._marks)
        envelope = self._snapshot(window_id) if window_id else {}
        marks = render_marked(envelope["data"]["tree"])[2] if envelope.get("ok") else {}
        return state(rows, marks)

    def _judged(self, expect, before, after):
        """The effects of an action, and whether `expect` was met when one was given."""
        if before is None:
            return {}
        if "observation" in after:
            after_state = state(after["windows"], self._marks)
        elif after.get("windows") == []:
            after_state = state([], {})
        else:
            return {}
        effects = diff(before, after_state)
        judged = {"effects": effects}
        if expect is not None:
            judged["expect"] = expect
            judged["expected_met"] = met(expect, effects)
        return judged

    def _capture_current(self, reason):
        """Captures the current window for the model when images are on: (Capture, event) or None."""
        if self._image_mode() == "off" or not self._current_window:
            return None
        row = self._owned_window(self._current_window)
        return self._take_capture(row, None, reason, None) if row else None

    def _take_capture(self, row, around, reason, mark):
        """Screenshots `row`; returns (Capture, capture event) or None. The event is not sent."""
        self._capture_count += 1
        capture_id = f"c{self._capture_count}"
        marks = self._marks if self._marks_window() == row["id"] else {}
        shot = capturing.take(self._desktop, row, marks, self._captures_path(), capture_id, around=around)
        if shot is None:
            return None
        self._captures[capture_id] = shot
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


def _outside(shot, x, y):
    return (
        f"({x}, {y}) is outside capture {shot.id}, which is {shot.width}x{shot.height} "
        "pixels. Call look again and pick a pixel inside the image."
    )
