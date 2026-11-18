"""Asking a vision model one question about one capture, and reading its answer.

The text model that runs the task may not take pictures. When a picture has to be read,
the harness sends one capture and one question to a vision model and puts the answer it
checked into the tool result. A reader object does the call; this module prepares the
picture, reads the reply, keeps only what the capture can back, and never raises.

Coordinates: the capture is shrunk before it is sent when its long side exceeds
`max_side`, so the model sees the pixels the harness knows. Points the model returns are
mapped back to the original capture's pixels; with `normalized_1000` they are first read
as 0-1000 of the sent picture.
"""

import base64
import io
import json
import re
import time
from dataclasses import dataclass, field

from agent.checks import llm_error_code

try:
    from PIL import Image
except ImportError:
    Image = None

POINT_MODES = ("pixel", "normalized_1000")
DEFAULT_MAX_SIDE = 1280
NORMALIZED_RANGE = 1000
IMAGE_UNREADABLE = "IMAGE_UNREADABLE"
ANSWER_UNREADABLE = "ANSWER_UNREADABLE"
DATA_URL_PREFIX = "data:image/png;base64,"
CODE_FENCE = re.compile(r"```[A-Za-z0-9_-]*\s*\n?(.*?)```", re.DOTALL)

VISION_INSTRUCTIONS = """\
You look at one screenshot of a desktop application and answer one question about it.
Reply with one JSON object and nothing else:
{"answer": "<short answer to the question>", "marks": [<mark numbers>], \
"points": [{"label": "<what is there>", "x": <int>, "y": <int>}], "unsure": <true or false>}
Rules:
- Some elements are outlined with numbered boxes. When a numbered box covers what the question \
asks about, put its number in "marks". Prefer marks: use them whenever a box fits.
- Use "points" only for a target that has no numbered box. Give its center as x and y pixels \
of this image, measured from the top-left corner.
- Leave "marks" or "points" as an empty list when there is nothing to put there.
- Set "unsure" to true when the image does not show enough to answer; say why in "answer".
- Answer from what the image shows. Do not guess hidden content.
"""

TARGET_PHRASE = 'the {role} named "{name}"'
REASON_PHRASES = {
    "mismatch": "the accessibility tree did not show the expected result",
    "unconfirmed": "the accessibility tree could not confirm the result",
}


@dataclass(frozen=True)
class VisionAnswer:
    answer: str
    marks: list
    points: list
    unsure: bool
    error: str | None = None
    dropped: int = 0
    tokens: int = 0
    seconds: float = 0.0

    def as_result(self):
        """The `vision` value of a tool result; `error` and `dropped` only when set."""
        result = {
            "answer": self.answer,
            "marks": list(self.marks),
            "points": [dict(point) for point in self.points],
            "unsure": self.unsure,
        }
        if self.error:
            result["error"] = self.error
        if self.dropped:
            result["dropped"] = self.dropped
        return result

    def as_event(self):
        """The answer part of a `vision` event, every field present."""
        return {
            "answer": self.answer,
            "marks": list(self.marks),
            "points": [dict(point) for point in self.points],
            "unsure": self.unsure,
            "error": self.error,
            "dropped": self.dropped,
            "tokens": self.tokens,
            "seconds": round(self.seconds, 2),
        }


@dataclass(frozen=True)
class Picture:
    url: str
    size: tuple
    sent: tuple

    @property
    def scale(self):
        return max(self.size) / max(self.sent)


@dataclass
class Parsed:
    answer: str = ""
    marks: list = field(default_factory=list)
    points: list = field(default_factory=list)
    unsure: bool = False
    dropped: int = 0


class Vision:
    """Prepares the picture, asks `reader`, and checks the answer against the capture.

    `reader.ask(instructions, question, image_url)` returns (text, tokens) or raises.
    """

    def __init__(self, reader, max_side=DEFAULT_MAX_SIDE, points=POINT_MODES[0], clock=time.monotonic):
        if points not in POINT_MODES:
            raise ValueError(f"points must be one of {POINT_MODES}")
        if isinstance(max_side, bool) or not isinstance(max_side, int) or max_side < 1:
            raise ValueError("max_side must be a positive integer")
        self.reader = reader
        self.max_side = max_side
        self.points = points
        self.clock = clock

    def read(self, capture, question, marks):
        """The checked answer about `capture`. Failures come back as `error`, never raised.
        `marks` holds the mark numbers drawn on this capture (a list or the mark table)."""
        started = self.clock()
        try:
            picture = self._picture(capture)
        except Exception:
            return self._failed(IMAGE_UNREADABLE, started)
        try:
            text, tokens = self.reader.ask(VISION_INSTRUCTIONS, question, picture.url)
        except Exception as error:
            return self._failed(_error_code(error), started)
        tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0 else 0
        try:
            parsed = self._parse(text, picture, marks)
        except Exception:
            return self._failed(ANSWER_UNREADABLE, started, tokens)
        return VisionAnswer(
            answer=parsed.answer, marks=list(parsed.marks), points=list(parsed.points), unsure=parsed.unsure,
            dropped=parsed.dropped, tokens=tokens, seconds=self._since(started),
        )

    def _failed(self, code, started, tokens=0):
        return VisionAnswer(answer="", marks=[], points=[], unsure=False, error=code, tokens=tokens,
                            seconds=self._since(started))

    def _since(self, started):
        try:
            return max(0.0, float(self.clock() - started))
        except Exception:
            return 0.0

    def _picture(self, capture):
        if Image is None:
            raise RuntimeError("Pillow is not installed")
        with Image.open(capture.path) as image:
            image.load()
            size = image.size
            if min(size) < 1:
                raise ValueError("empty image")
            sent = _shrunk_size(size, self.max_side)
            picture = image if sent == size else image.resize(sent, Image.Resampling.LANCZOS)
            if picture.mode not in ("RGB", "RGBA", "L", "LA"):
                picture = picture.convert("RGBA")
            buffer = io.BytesIO()
            picture.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return Picture(url=DATA_URL_PREFIX + encoded, size=size, sent=sent)

    def _parse(self, text, picture, marks):
        text = text if isinstance(text, str) else ("" if text is None else str(text))
        reply = _json_object(text)
        if reply is None:
            return Parsed(answer=text.strip())
        parsed = Parsed()
        answer = reply.get("answer")
        parsed.answer = answer if isinstance(answer, str) else ("" if answer is None else str(answer))
        parsed.unsure = reply.get("unsure") is True
        allowed = _mark_numbers(marks)
        for mark in _as_list(reply.get("marks")):
            if _is_int(mark) and mark in allowed and mark not in parsed.marks:
                parsed.marks.append(mark)
            else:
                parsed.dropped += 1
        for point in _as_list(reply.get("points")):
            mapped = self._map_point(point, picture)
            if mapped is None:
                parsed.dropped += 1
            else:
                parsed.points.append(mapped)
        return parsed

    def _map_point(self, point, picture):
        if not isinstance(point, dict):
            return None
        x, y = point.get("x"), point.get("y")
        if not (_is_number(x) and _is_number(y)):
            return None
        if self.points == "normalized_1000":
            x = x * picture.sent[0] / NORMALIZED_RANGE
            y = y * picture.sent[1] / NORMALIZED_RANGE
        x, y = round(x * picture.scale), round(y * picture.scale)
        width, height = picture.size
        if not (0 <= x < width and 0 <= y < height):
            return None
        label = point.get("label")
        return {"label": label if isinstance(label, str) else ("" if label is None else str(label)), "x": x, "y": y}


def auto_question(reason, expect, element):
    """The harness's question for a capture taken after an action: what was expected, on
    which target, and a request for the on-screen state rather than mark numbers. It asks for a
    description only; whether the action worked is the text model's judgement, not the vision
    model's, because the vision model reasons less well."""
    target = _target_phrase(element)
    expectation = f'the expected result "{expect}"' if expect else "no particular expected result"
    why = REASON_PHRASES.get(reason, f'the check ended as "{reason}"' if reason else "the result is unknown")
    return (
        f"An action was just performed on {target} with {expectation}, and {why}; "
        f"looking at this image, describe the current on-screen state of {target} "
        f"(for example checked or not, selected or not, its value, open or closed). "
        f"Describe only what the image shows and do not judge whether the action worked; "
        f"answer with the state rather than mark numbers."
    )


def _target_phrase(element):
    element = element if isinstance(element, dict) else {}
    role = str(element.get("role") or "").strip()
    name = str(element.get("name") or "").strip()
    if name:
        return TARGET_PHRASE.format(role=role or "element", name=name)
    if role:
        return f"the {role}"
    return "the target in the window"


def _shrunk_size(size, max_side):
    width, height = size
    longest = max(width, height)
    if longest <= max_side:
        return (width, height)
    ratio = max_side / longest
    return (max(1, round(width * ratio)), max(1, round(height * ratio)))


def _json_object(text):
    candidates = [text]
    fence = CODE_FENCE.search(text)
    if fence:
        candidates.append(fence.group(1))
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate.strip())
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _mark_numbers(marks):
    try:
        return {mark for mark in (marks or ()) if _is_int(mark)}
    except TypeError:
        return set()


def _as_list(value):
    return value if isinstance(value, list) else []


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _error_code(error):
    try:
        return llm_error_code(error)
    except Exception:
        return type(error).__name__
