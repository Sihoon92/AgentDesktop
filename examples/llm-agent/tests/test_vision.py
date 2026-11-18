import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from agent.capture import Capture
from agent.vision import POINT_MODES, VISION_INSTRUCTIONS, Vision, VisionAnswer, auto_question

DATA_URL = "data:image/png;base64,"


class FakeReader:
    def __init__(self, text="{}", tokens=0, error=None):
        self.text = text
        self.tokens = tokens
        self.error = error
        self.calls = []

    def ask(self, instructions, question, image_url):
        self.calls.append((instructions, question, image_url))
        if self.error is not None:
            raise self.error
        return self.text, self.tokens

    def sent_size(self):
        url = self.calls[-1][2]
        assert url.startswith(DATA_URL)
        with Image.open(io.BytesIO(base64.b64decode(url[len(DATA_URL):]))) as image:
            return image.size


class Ticks:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


def make_capture(tmp_path, width, height, name="c1"):
    path = Path(tmp_path) / f"{name}.png"
    Image.new("RGB", (width, height), "white").save(path)
    return Capture(id=name, path=path, width=width, height=height, origin=(0, 0), window_id="w1",
                   window_bounds={"x": 0, "y": 0, "width": width, "height": height}, drawn=True, cropped=False)


def reply(**fields):
    return json.dumps({"answer": "", "marks": [], "points": [], "unsure": False, **fields})


def test_reads_marks_and_points_from_json(tmp_path):
    reader = FakeReader(reply(answer="조회", marks=[3, 99],
                              points=[{"label": "a", "x": 10, "y": 20}, {"label": "b", "x": 5000, "y": 1}]), tokens=812)
    answer = Vision(reader).read(make_capture(tmp_path, 800, 600), "which mark?", [3, 5])
    assert answer.answer == "조회"
    assert answer.marks == [3]
    assert answer.points == [{"label": "a", "x": 10, "y": 20}]
    assert answer.dropped == 2
    assert answer.unsure is False
    assert answer.error is None
    assert answer.tokens == 812
    assert reader.calls[0][0] == VISION_INSTRUCTIONS
    assert reader.calls[0][1] == "which mark?"


def test_marks_may_be_the_drawn_mark_table(tmp_path):
    reader = FakeReader(reply(marks=[3, 4]))
    answer = Vision(reader).read(make_capture(tmp_path, 800, 600), "q", {3: {"bounds": {}}})
    assert answer.marks == [3]
    assert answer.dropped == 1


def test_reads_json_inside_a_code_fence(tmp_path):
    fenced = "Here it is:\n```json\n" + reply(answer="yes", marks=[5]) + "\n```\n"
    answer = Vision(FakeReader(fenced)).read(make_capture(tmp_path, 800, 600), "q", [5])
    assert answer.answer == "yes"
    assert answer.marks == [5]
    assert answer.error is None


def test_code_fence_wins_over_braces_around_it(tmp_path):
    fenced = "Read {the box}:\n```\n" + reply(answer="closed", marks=[4]) + "\n```\nSee {note}."
    answer = Vision(FakeReader(fenced)).read(make_capture(tmp_path, 800, 600), "q", [4])
    assert answer.answer == "closed"
    assert answer.marks == [4]


def test_reads_json_between_the_first_and_last_brace(tmp_path):
    loose = "Sure. " + reply(answer="open", marks=[2]) + " Hope that helps."
    answer = Vision(FakeReader(loose)).read(make_capture(tmp_path, 800, 600), "q", [2])
    assert answer.answer == "open"
    assert answer.marks == [2]


def test_unparsable_text_becomes_the_answer(tmp_path):
    answer = Vision(FakeReader("모르겠습니다")).read(make_capture(tmp_path, 800, 600), "q", [1])
    assert answer.answer == "모르겠습니다"
    assert answer.marks == []
    assert answer.points == []
    assert answer.error is None


def test_unreadable_fence_falls_back_to_the_whole_text(tmp_path):
    text = "```json\nnot json at all\n```"
    answer = Vision(FakeReader(text)).read(make_capture(tmp_path, 800, 600), "q", [1])
    assert answer.answer == text
    assert answer.marks == []


def test_malformed_fields_are_dropped_not_raised(tmp_path):
    text = json.dumps({"answer": 7, "marks": [True, "3", 3], "points": [{"x": "a", "y": 1}, "p", {"x": 1}],
                       "unsure": "yes"})
    answer = Vision(FakeReader(text)).read(make_capture(tmp_path, 800, 600), "q", [3])
    assert answer.answer == "7"
    assert answer.marks == [3]
    assert answer.points == []
    assert answer.dropped == 5
    assert answer.unsure is False


def test_shrinks_a_large_capture_and_maps_points_back(tmp_path):
    capture = make_capture(tmp_path, 2560, 1440)
    original = capture.path.read_bytes()
    reader = FakeReader(reply(points=[{"label": "a", "x": 640, "y": 360}]))
    answer = Vision(reader, max_side=1280).read(capture, "q", [])
    assert reader.sent_size() == (1280, 720)
    assert answer.points == [{"label": "a", "x": 1280, "y": 720}]
    assert capture.path.read_bytes() == original


def test_small_capture_is_sent_as_is(tmp_path):
    reader = FakeReader(reply(points=[{"label": "a", "x": 799, "y": 599}]))
    answer = Vision(reader, max_side=1280).read(make_capture(tmp_path, 800, 600), "q", [])
    assert reader.sent_size() == (800, 600)
    assert answer.points == [{"label": "a", "x": 799, "y": 599}]


def test_points_on_the_far_edge_are_outside(tmp_path):
    reader = FakeReader(reply(points=[{"label": "a", "x": 800, "y": 10}, {"label": "b", "x": -1, "y": 10}]))
    answer = Vision(reader).read(make_capture(tmp_path, 800, 600), "q", [])
    assert answer.points == []
    assert answer.dropped == 2


def test_normalized_points_become_capture_pixels(tmp_path):
    reader = FakeReader(reply(points=[{"label": "a", "x": 500, "y": 500}]))
    answer = Vision(reader, max_side=1280, points="normalized_1000").read(make_capture(tmp_path, 2000, 1000), "q", [])
    assert reader.sent_size() == (1280, 640)
    assert answer.points == [{"label": "a", "x": 1000, "y": 500}]


def test_unknown_point_mode_is_refused():
    assert POINT_MODES == ("pixel", "normalized_1000")
    with pytest.raises(ValueError):
        Vision(FakeReader(), points="percent")
    with pytest.raises(ValueError):
        Vision(FakeReader(), max_side=0)


def test_reader_failure_is_an_error_not_an_exception(tmp_path):
    answer = Vision(FakeReader(error=TimeoutError())).read(make_capture(tmp_path, 800, 600), "q", [1])
    assert answer.error == "TimeoutError"
    assert answer.answer == ""
    assert answer.marks == []
    assert answer.points == []
    assert answer.tokens == 0


def test_reader_failure_carries_the_status_code(tmp_path):
    class Refused(Exception):
        status_code = 403

    answer = Vision(FakeReader(error=Refused("secret body"))).read(make_capture(tmp_path, 800, 600), "q", [])
    assert answer.error == "Refused 403"


def test_unreadable_capture_is_an_error(tmp_path):
    capture = make_capture(tmp_path, 800, 600)
    capture.path.unlink()
    reader = FakeReader(reply(answer="x"))
    answer = Vision(reader).read(capture, "q", [1])
    assert answer.error == "IMAGE_UNREADABLE"
    assert reader.calls == []


def test_corrupt_capture_is_an_error(tmp_path):
    capture = make_capture(tmp_path, 800, 600)
    capture.path.write_bytes(b"not a png")
    reader = FakeReader(reply(answer="x"))
    answer = Vision(reader).read(capture, "q", [1])
    assert answer.error == "IMAGE_UNREADABLE"
    assert reader.calls == []


def test_odd_reply_shapes_do_not_raise(tmp_path):
    capture = make_capture(tmp_path, 800, 600)

    class OddReader:
        def ask(self, instructions, question, image_url):
            return None, "many"

    answer = Vision(OddReader()).read(capture, "q", [1])
    assert answer.error is None
    assert answer.answer == ""
    assert answer.tokens == 0


def test_seconds_come_from_the_clock(tmp_path):
    answer = Vision(FakeReader(reply()), clock=Ticks(10.0, 12.5)).read(make_capture(tmp_path, 800, 600), "q", [])
    assert answer.seconds == pytest.approx(2.5)


def test_as_result_leaves_out_empty_error_and_dropped():
    plain = VisionAnswer(answer="a", marks=[3], points=[{"label": "l", "x": 1, "y": 2}], unsure=False, tokens=5,
                         seconds=1.0)
    assert plain.as_result() == {"answer": "a", "marks": [3], "points": [{"label": "l", "x": 1, "y": 2}],
                                 "unsure": False}
    failed = VisionAnswer(answer="", marks=[], points=[], unsure=False, error="TimeoutError", dropped=2)
    assert failed.as_result()["error"] == "TimeoutError"
    assert failed.as_result()["dropped"] == 2


def test_as_event_has_every_field():
    answer = VisionAnswer(answer="a", marks=[3], points=[], unsure=True, tokens=812, seconds=2.4)
    assert answer.as_event() == {"answer": "a", "marks": [3], "points": [], "unsure": True, "error": None,
                                 "dropped": 0, "tokens": 812, "seconds": 2.4}


def test_answer_lists_are_not_shared(tmp_path):
    answer = Vision(FakeReader(reply(marks=[1]))).read(make_capture(tmp_path, 800, 600), "q", [1])
    answer.as_result()["marks"].append(9)
    assert answer.marks == [1]


def test_auto_question_names_the_expectation_and_the_target():
    question = auto_question("mismatch", "value_changed", {"role": "checkbox", "name": "x"})
    assert "value_changed" in question
    assert "checkbox" in question
    assert '"x"' in question
    assert auto_question("unconfirmed", None, None)
    assert auto_question("mismatch", "", {"role": "", "name": ""})


def test_auto_question_asks_for_state_not_numbers():
    question = auto_question("unconfirmed", "selected", {"role": "tab"})
    assert "state" in question
    assert "number" in question
    assert question.isascii()


def test_instructions_ask_for_marks_before_pixels():
    for word in ("marks", "points", "unsure", "answer", "JSON"):
        assert word in VISION_INSTRUCTIONS
    assert VISION_INSTRUCTIONS.index("marks") < VISION_INSTRUCTIONS.index("points")
    assert VISION_INSTRUCTIONS.isascii()
