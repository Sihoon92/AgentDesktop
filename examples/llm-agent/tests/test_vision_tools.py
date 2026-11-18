"""The Toolbox with a vision model (split mode): look asks it one question about the capture,
and the capture after a mismatched or unconfirmed action asks it the harness's question.

Most tests use a stand-in with the shape of agent.vision.Vision: read(capture, question, marks)
returns an answer with as_result() and as_event(). The tests at the end run the real Vision
with a fake reader.
"""

import copy
from dataclasses import dataclass, field

import pytest
from fake_cli import FakeCli
from test_picture_verdict import BESIDE_LINE, toggles
from test_tools import EXE, LIST_WINDOW, OBSERVE, SCENE, EventObserver, part_call

from agent.checks import llm_error_code
from agent.desktop import DesktopClient
from agent.task import Task
from agent.tools import Toolbox
from agent.vision import Vision, auto_question


@dataclass(frozen=True)
class FakeAnswer:
    answer: str = "The search button is [2]."
    marks: list = field(default_factory=lambda: [2])
    points: list = field(default_factory=list)
    unsure: bool = False
    error: str | None = None
    dropped: int = 0
    tokens: int = 0
    seconds: float = 0.0

    def as_result(self):
        result = {"answer": self.answer, "marks": self.marks, "points": self.points, "unsure": self.unsure}
        if self.error is not None:
            result["error"] = self.error
        if self.dropped:
            result["dropped"] = self.dropped
        return result

    def as_event(self):
        return {"answer": self.answer, "marks": self.marks, "points": self.points, "unsure": self.unsure,
                "error": self.error, "dropped": self.dropped, "tokens": self.tokens, "seconds": self.seconds}


class FakeVision:
    def __init__(self, *answers):
        self.answers = list(answers) or [FakeAnswer()]
        self.reads = []

    def read(self, capture, question, marks):
        self.reads.append((capture, question, list(marks)))
        return self.answers[min(len(self.reads), len(self.answers)) - 1]


def build(tmp_path, vision=None, image_mode="user_message", level=2, scene=SCENE):
    cli = FakeCli(scene=copy.deepcopy(scene))
    cli.screenshot_colors = {"w-1": (255, 255, 255)}
    cli.on_click = lambda x, y: cli.open_window(LIST_WINDOW) if x > 1180 else cli.focus(mark=3)
    observer = EventObserver()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, level, [], image_mode=image_mode),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=observer,
        captures_dir=tmp_path / "captures",
        vision=vision,
    )
    toolbox.call("launch_target", {})
    toolbox.call("observe", OBSERVE)
    return toolbox, cli, observer


def look_definition(toolbox):
    return next(tool for tool in toolbox.definitions() if tool["name"] == "look")


QUESTION = "Which mark is the search button?"


def test_without_vision_look_schema_is_unchanged(tmp_path):
    toolbox, _, _ = build(tmp_path)
    parameters = look_definition(toolbox)["parameters"]
    assert set(parameters["properties"]) == {"mark"}
    assert parameters["required"] == ["mark"]
    assert toolbox.vision_enabled is False


def test_with_vision_look_requires_a_question(tmp_path):
    toolbox, _, _ = build(tmp_path, vision=FakeVision())
    definition = look_definition(toolbox)
    parameters = definition["parameters"]
    assert set(parameters["properties"]) == {"mark", "question"}
    assert sorted(parameters["required"]) == ["mark", "question"]
    assert parameters["properties"]["question"]["type"] == "string"
    assert parameters["additionalProperties"] is False and definition["strict"] is True
    assert toolbox.vision_enabled is True


def test_only_look_differs_between_the_two_modes(tmp_path):
    single, _, _ = build(tmp_path / "a")
    split, _, _ = build(tmp_path / "b", vision=FakeVision())
    others = [tool for tool in single.definitions() if tool["name"] != "look"]
    assert [tool for tool in split.definitions() if tool["name"] != "look"] == others
    assert [tool["name"] for tool in split.definitions()] == [tool["name"] for tool in single.definitions()]


def test_look_with_vision_returns_the_answer(tmp_path):
    answer = FakeAnswer(tokens=40)
    vision = FakeVision(answer)
    toolbox, _, _ = build(tmp_path, vision=vision)
    result = toolbox.call("look", {"mark": None, "question": QUESTION})
    shot = toolbox._captures["c1"]
    assert result == {"ok": True, "capture": "c1", "window_id": shot.window_id, "width": 400, "height": 300,
                      "drawn": True, "cropped": False, "vision": answer.as_result()}
    assert len(vision.reads) == 1
    capture, question, marks = vision.reads[0]
    assert capture is shot and question == QUESTION
    assert marks == sorted(toolbox._marks)


def test_look_around_a_mark_passes_only_the_marks_drawn_in_the_crop(tmp_path):
    scene = copy.deepcopy(SCENE)
    scene["tree"]["children"].append(
        {"role": "statictext", "name": "far", "bounds": {"x": 1370, "y": 770, "width": 30, "height": 20}})
    vision = FakeVision()
    toolbox, _, _ = build(tmp_path, vision=vision, scene=scene)
    far = next(number for number, element in toolbox._marks.items() if element.get("name") == "far")
    result = toolbox.call("look", {"mark": 3, "question": QUESTION})
    assert result["cropped"] is True
    marks = vision.reads[0][2]
    assert 3 in marks and far not in marks
    assert set(marks) == set(toolbox._marks) - {far}


def test_look_with_vision_works_when_image_mode_is_off(tmp_path):
    vision = FakeVision()
    toolbox, _, _ = build(tmp_path, vision=vision, image_mode="off")
    result = toolbox.call("look", {"mark": None, "question": QUESTION})
    assert "refused" not in result and result["ok"] is True
    assert result["capture"] == "c1" and "vision" in result
    assert len(vision.reads) == 1


def test_look_emits_a_vision_event(tmp_path):
    answer = FakeAnswer(tokens=812, seconds=2.4)
    toolbox, _, observer = build(tmp_path, vision=FakeVision(answer))
    result = toolbox.call("look", {"mark": None, "question": QUESTION})
    capture_event, vision_event = observer.events[-2:]
    assert capture_event["type"] == "capture" and capture_event["id"] == result["capture"]
    assert vision_event == {"type": "vision", "step": None, "capture": result["capture"], "asked_by": "look",
                            "question": QUESTION, **answer.as_event()}


def test_vision_error_still_returns_the_capture(tmp_path):
    answer = FakeAnswer(answer="", marks=[], error="TimeoutError")
    toolbox, _, observer = build(tmp_path, vision=FakeVision(answer))
    result = toolbox.call("look", {"mark": None, "question": QUESTION})
    assert result["ok"] is True and result["capture"] == "c1"
    assert result["vision"]["error"] == "TimeoutError"
    assert observer.events[-1]["error"] == "TimeoutError"


def test_captures_are_not_attached_for_the_text_model(tmp_path):
    toolbox, _, observer = build(tmp_path, vision=FakeVision())
    toolbox.call("look", {"mark": None, "question": QUESTION})
    assert toolbox.take_attachments() == []
    assert [event["type"] for event in observer.events[-2:]] == ["capture", "vision"]


def test_vision_usage_is_counted_and_reset(tmp_path):
    toolbox, _, _ = build(tmp_path, vision=FakeVision(FakeAnswer(tokens=100), FakeAnswer(tokens=50)))
    assert toolbox.take_vision_usage() == (0, 0)
    toolbox.call("look", {"mark": None, "question": QUESTION})
    toolbox.call("look", {"mark": 3, "question": QUESTION})
    assert toolbox.take_vision_usage() == (2, 150)
    assert toolbox.take_vision_usage() == (0, 0)


def test_look_without_a_question_in_split_mode_is_refused(tmp_path):
    vision = FakeVision()
    toolbox, _, _ = build(tmp_path, vision=vision)
    result = toolbox.call("look", {"mark": None, "question": "  "})
    assert "refused" in result and "question" in result["refused"]
    assert vision.reads == [] and toolbox._captures == {}


def test_failed_screenshot_does_not_call_vision(tmp_path):
    vision = FakeVision()
    toolbox, _, _ = build(tmp_path, vision=vision)
    toolbox._take_capture = lambda *args: None
    assert "refused" in toolbox.call("look", {"mark": None, "question": QUESTION})
    assert vision.reads == [] and toolbox.take_vision_usage() == (0, 0)


def test_one_model_mode_reports_no_vision_usage(tmp_path):
    toolbox, _, observer = build(tmp_path)
    result = toolbox.call("look", {"mark": None})
    assert "vision" not in result
    assert len(toolbox.take_attachments()) == 1
    assert toolbox.take_vision_usage() == (0, 0)
    assert all(event["type"] != "vision" for event in observer.events)


LINE_CLICK = part_call(mark=3, part="center", expect="window_opened")
PRESS = {"combo": "escape", "window_id": "w-1", "intent": "닫는다", "expect": "content_changed",
         "knowledge_id": None}


def vision_events(observer):
    return [event for event in observer.events if event["type"] == "vision"]


def test_mismatch_asks_vision_about_the_target(tmp_path):
    answer = FakeAnswer(answer="The line box is focused; no list opened.", marks=[], tokens=60)
    vision = FakeVision(answer)
    toolbox, _, observer = build(tmp_path, vision=vision)
    target = toolbox.preview("click_part", LINE_CLICK)

    result = toolbox.call("click_part", LINE_CLICK)

    assert result["expected_met"] is False and result["vision"] == answer.as_result()
    capture, question, _ = vision.reads[0]
    assert capture.id == result["capture"]
    assert question == auto_question("mismatch", "window_opened", target)
    assert target["role"] == "statictext" and target["name"] == "라인"
    [event] = vision_events(observer)
    assert (event["asked_by"], event["capture"], event["question"]) == ("mismatch", result["capture"], question)
    assert [e["type"] for e in observer.events][-2:] == ["capture", "vision"]
    assert toolbox.take_attachments() == [] and toolbox.take_vision_usage() == (1, 60)


def test_auto_capture_is_cropped_around_the_target(tmp_path):
    toolbox, _, _ = build(tmp_path, vision=FakeVision())
    result = toolbox.call("click_part", LINE_CLICK)
    assert toolbox._captures[result["capture"]].cropped is True


def test_window_expectation_takes_no_extra_picture_but_still_crops(tmp_path):
    toolbox, cli, _ = build(tmp_path, vision=FakeVision())
    before = cli.command_names().count("screenshot")
    result = toolbox.call("click_part", LINE_CLICK)
    assert "picture" not in result["effects"]
    assert toolbox._captures[result["capture"]].cropped is True
    assert cli.command_names().count("screenshot") - before == 1


def test_auto_capture_without_a_target_uses_the_whole_window(tmp_path):
    vision = FakeVision()
    toolbox, _, _ = build(tmp_path, vision=vision)
    result = toolbox.call("press", PRESS)
    assert result["expected_met"] is False, result
    assert toolbox._captures[result["capture"]].cropped is False
    assert vision.reads[0][1] == auto_question("mismatch", "content_changed", toolbox.preview("press", PRESS))
    assert "the target in the window" in vision.reads[0][1]


def test_unconfirmed_asks_vision_too(tmp_path):
    vision = FakeVision()
    toolbox, cli, observer = build(tmp_path, vision=vision)
    toggles(cli, BESIDE_LINE)
    arguments = part_call(mark=3, part="center", expect="value_changed")
    target = toolbox.preview("click_part", arguments)

    result = toolbox.call("click_part", arguments)

    assert result["verdict"] == "unconfirmed" and "vision" in result
    assert vision.reads[0][1] == auto_question("unconfirmed", "value_changed", target)
    assert vision_events(observer)[0]["asked_by"] == "unconfirmed"


def test_vision_timeout_after_an_action_keeps_the_result(tmp_path):
    single, _, _ = build(tmp_path / "single")
    alone = single.call("click_part", LINE_CLICK)
    timed_out = FakeVision(FakeAnswer(answer="", marks=[], error="TimeoutError"))
    toolbox, _, observer = build(tmp_path / "split", vision=timed_out)

    result = toolbox.call("click_part", LINE_CLICK)

    for key in ("ok", "effects", "expect", "expected_met", "capture", "observation"):
        assert result[key] == alone[key], key
    assert result["vision"]["error"] == "TimeoutError"
    assert set(result) - set(alone) == {"vision"}
    assert vision_events(observer)[0]["error"] == "TimeoutError"


def test_question_refusal_capture_does_not_call_vision(tmp_path):
    vision = FakeVision()
    toolbox, _, observer = build(tmp_path, vision=vision, image_mode="off")
    click = {"window_id": "w-1", "x": 1050, "y": 600, "button": "left", "intent": "누른다",
             "expect": "content_changed", "knowledge_id": None}

    result = toolbox.call("mouse_click", click)

    assert result["refused_code"] == "needs_confirmation" and "capture" in result
    assert "vision" not in result and vision.reads == []
    assert vision_events(observer) == [] and observer.events[-1]["reason"] == "question"
    assert toolbox.take_attachments() == [] and toolbox.take_vision_usage() == (0, 0)


@pytest.mark.parametrize("expect", ["window_opened", "value_changed"])
def test_one_model_mode_auto_capture_is_unchanged(tmp_path, expect):
    toolbox, _, observer = build(tmp_path)
    result = toolbox.call("click_part", {**LINE_CLICK, "expect": expect})
    assert result["expected_met"] is False and "vision" not in result
    shot = toolbox._captures[result["capture"]]
    assert shot.cropped is False
    assert toolbox.take_attachments() == [shot]
    assert vision_events(observer) == [] and observer.events[-1]["type"] == "capture"


class FakeReader:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.asked = []

    def ask(self, instructions, question, image_url):
        self.asked.append((instructions, question, image_url))
        if self.error is not None:
            raise self.error
        return self.reply, 30


FENCED_REPLY = '```json\n{"answer": "It is [2].", "marks": [2, 99], "points": [], "unsure": false}\n```'


def test_look_through_the_real_vision_keeps_only_drawn_marks(tmp_path):
    reader = FakeReader(reply=FENCED_REPLY)
    toolbox, _, observer = build(tmp_path, vision=Vision(reader))

    result = toolbox.call("look", {"mark": None, "question": QUESTION})

    assert result["vision"] == {"answer": "It is [2].", "marks": [2], "points": [], "unsure": False,
                                "dropped": 1}
    _, question, image_url = reader.asked[0]
    assert question == QUESTION and image_url.startswith("data:image/png;base64,")
    event = vision_events(observer)[0]
    assert (event["capture"], event["asked_by"], event["tokens"], event["error"]) == (
        result["capture"], "look", 30, None)
    assert toolbox.take_vision_usage() == (1, 30)


def test_real_vision_timeout_after_an_action_does_not_stop_the_call(tmp_path):
    toolbox, _, _ = build(tmp_path, vision=Vision(FakeReader(error=TimeoutError("slow"))))

    result = toolbox.call("click_part", LINE_CLICK)

    assert result["ok"] is True and result["expected_met"] is False and "observation" in result
    assert result["vision"]["error"] == llm_error_code(TimeoutError("slow"))
    assert toolbox.take_vision_usage() == (1, 0)
