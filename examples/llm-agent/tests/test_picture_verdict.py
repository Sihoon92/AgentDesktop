"""The third verdict: the observation text is unchanged but the picture around the target changed.

A list that exposes only its item text (no check state) gives the same observation before and
after a click that toggles a check box beside the text. The harness then compares a screenshot
of the pressed window taken just before the action with one taken after it, around the target
only, and answers "unconfirmed" (expected_met null) instead of a mismatch.
"""

import copy
import time

import pytest
from fake_cli import FakeCli
from test_tools import EXE, LIST_WINDOW, MAIN, OBSERVE, SCENE, EventObserver, part_call, point_call

from agent import capture
from agent.ask import Answer
from agent.desktop import DesktopClient
from agent.knowledge import KnowledgeStore
from agent.task import Task
from agent.tools import Toolbox

LINE_MARK = 3
BESIDE_LINE = [({"x": 1064, "y": 552, "width": 18, "height": 3}, (0, 0, 0)),
               ({"x": 1064, "y": 555, "width": 17, "height": 1}, (0, 0, 0))]
FAR_AWAY = [({"x": 1350, "y": 780, "width": 40, "height": 20}, (0, 0, 0))]
QUERY_REF = "@s1:e1"
QUERY_BESIDE = [({"x": 1280, "y": 525, "width": 10, "height": 10}, (0, 0, 0))]


class ScriptedAsker:
    interactive = True

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return self.answers.pop(0)


def toolbox_with(tmp_path, image_mode="user_message", knowledge=None, asker=None):
    cli = FakeCli(scene=copy.deepcopy(SCENE))
    cli.screenshot_colors = {"w-1": (255, 255, 255)}
    observer = EventObserver()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, 2, [], image_mode=image_mode),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=observer,
        asker=asker or ScriptedAsker(),
        knowledge=knowledge,
        captures_dir=tmp_path / "captures",
        wall=lambda: time.time() - 60,
    )
    toolbox.call("launch_target", {})
    toolbox.call("observe", OBSERVE)
    return toolbox, cli, observer


def toggles(cli, patches):
    """Each click paints `patches` on the main window or wipes them, like a check box."""
    def click(_x, _y):
        if cli.screenshot_patches.get("w-1"):
            cli.screenshot_patches["w-1"] = []
        else:
            cli.screenshot_patches["w-1"] = list(patches)
    cli.on_click = click


def press_line(toolbox, expect="value_changed", knowledge_id=None):
    return toolbox.call("click_part", part_call(mark=LINE_MARK, part="center", expect=expect,
                                                knowledge_id=knowledge_id))


def screenshots(cli, since):
    return cli.command_names()[since:].count("screenshot")


def test_a_change_beside_the_target_with_the_same_text_is_unconfirmed(tmp_path):
    toolbox, cli, observer = toolbox_with(tmp_path)
    toggles(cli, BESIDE_LINE)

    result = press_line(toolbox)

    assert result["expected_met"] is None and result["verdict"] == "unconfirmed", result
    assert result["effects"]["picture"] == {"changed": True, "pixels": 71}
    assert result["effects"]["none"] is True
    captured = [e for e in observer.events if e["type"] == "capture"]
    assert [e["reason"] for e in captured] == ["unconfirmed"] and result["capture"] == captured[0]["id"]


def marks_beside(cli):
    """Each click toggles a 72-pixel mark 50 px left of the pressed point."""
    def click(x, y):
        patch = ({"x": x - 50, "y": y - 2, "width": 18, "height": 4}, (0, 0, 0))
        patches = cli.screenshot_patches.setdefault("w-1", [])
        if patch in patches:
            patches.remove(patch)
        else:
            patches.append(patch)
    cli.on_click = click


def press(toolbox, mark, part="center"):
    return toolbox.call("click_part", part_call(mark=mark, part=part, expect="value_changed"))


def test_unconfirmed_results_neither_count_nor_clear_the_mismatch_streak(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path)
    marks_beside(cli)
    assert press(toolbox, LINE_MARK)["expected_met"] is None
    assert press(toolbox, LINE_MARK, "right")["expected_met"] is None
    assert press(toolbox, 4)["expected_met"] is None
    cli.on_click = None
    assert press(toolbox, 5)["expected_met"] is False
    marks_beside(cli)
    assert press(toolbox, 6)["expected_met"] is None
    cli.on_click = None

    assert press(toolbox, 1)["expected_met"] is False
    assert press(toolbox, 4, "right")["refused_code"] == "blocked_until_question"


def test_the_same_text_and_the_same_picture_is_a_mismatch_and_two_block(tmp_path):
    toolbox, _, observer = toolbox_with(tmp_path)

    first = press_line(toolbox)
    second = press_line(toolbox)

    assert first["expected_met"] is False and "verdict" not in first
    assert first["effects"]["picture"] == {"changed": False, "pixels": 0}
    assert second["expected_met"] is False
    assert [e["reason"] for e in observer.events if e["type"] == "capture"] == ["mismatch", "mismatch"]
    assert press_line(toolbox)["refused_code"] == "blocked_until_question"


def test_a_change_far_from_the_target_is_still_a_mismatch(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path)
    toggles(cli, FAR_AWAY)

    result = press_line(toolbox)

    assert result["expected_met"] is False and result["effects"]["picture"] == {"changed": False, "pixels": 0}


def test_a_few_pixels_beside_the_target_are_still_a_mismatch(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path)
    toggles(cli, [({"x": 1064, "y": 552, "width": 19, "height": 1}, (0, 0, 0))])

    result = press_line(toolbox)

    assert result["expected_met"] is False and result["effects"]["picture"] == {"changed": False, "pixels": 19}


def test_a_faint_highlight_around_the_target_is_still_a_mismatch(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path)
    toggles(cli, [({"x": 1040, "y": 530, "width": 220, "height": 70}, (229, 243, 255))])

    result = press_line(toolbox)

    assert result["expected_met"] is False and result["effects"]["picture"] == {"changed": False, "pixels": 0}


POPUP = {**LIST_WINDOW, "id": "w-2", "bounds": {"x": 1100, "y": 650, "width": 216, "height": 258}}
POPUP_ITEM = {"x": 1150, "y": 700, "width": 60, "height": 20}


def test_a_click_on_a_popup_item_compares_the_popup_window(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path)
    cli.scene["windows"] = [copy.deepcopy(POPUP), copy.deepcopy(MAIN)]
    cli.scene["tree"]["children"].append({"role": "listitem", "name": "항목", "bounds": POPUP_ITEM})
    cli.screenshot_colors["w-2"] = (255, 255, 255)
    toolbox.call("observe", OBSERVE)
    cli.on_click = lambda _x, _y: cli.screenshot_patches.update(
        {"w-2": [({"x": 1120, "y": 705, "width": 18, "height": 4}, (0, 0, 0))]})
    mark = next(n for n, e in toolbox._marks.items() if e.get("name") == "항목")

    result = toolbox.call("click_part", part_call(mark=mark, part="center", expect="value_changed"))

    assert result["window_id"] == "w-1" and result["expected_met"] is None, result
    assert result["effects"]["picture"] == {"changed": True, "pixels": 72}


def test_text_that_meets_the_expectation_is_met_without_a_second_screenshot(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")

    def click(_x, _y):
        cli.screenshot_patches["w-1"] = list(BESIDE_LINE)
        cli.open_window(LIST_WINDOW)
    cli.on_click = click
    before = len(cli.command_names())

    result = press_line(toolbox, expect="window_opened")

    assert result["expected_met"] is True and "picture" not in result["effects"] and "verdict" not in result
    assert screenshots(cli, before) == 0


@pytest.mark.parametrize("expect", ["window_opened", "window_closed"])
def test_a_window_expectation_is_judged_by_the_window_list_alone(tmp_path, expect):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    toggles(cli, BESIDE_LINE)
    before = len(cli.command_names())

    result = press_line(toolbox, expect=expect)

    assert result["expected_met"] is False and "verdict" not in result, result
    assert "picture" not in result["effects"] and screenshots(cli, before) == 0


def test_two_window_expectations_that_only_change_the_picture_block_until_a_question(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    marks_beside(cli)

    assert press_line(toolbox, expect="window_opened")["expected_met"] is False
    assert press_line(toolbox, expect="window_opened")["expected_met"] is False

    refused = toolbox.call("click_part", part_call(mark=LINE_MARK, part="right", expect="window_opened"))
    assert refused["refused_code"] == "blocked_until_question"


def test_an_unmet_text_check_takes_one_more_screenshot(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    before = len(cli.command_names())

    press_line(toolbox)

    assert screenshots(cli, before) == 2


def test_an_action_without_an_expectation_takes_no_screenshot(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    before = len(cli.command_names())

    result = toolbox.call("click_part", part_call(mark=LINE_MARK, part="center"))

    assert result["ok"] and "expected_met" not in result and screenshots(cli, before) == 0


def test_a_tool_without_a_point_takes_no_screenshot(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    before = len(cli.command_names())

    result = toolbox.call("press", {"window_id": "w-1", "combo": "enter", "intent": "누른다",
                                    "expect": "value_changed", "knowledge_id": None})

    assert result["expected_met"] is False and "picture" not in result["effects"]
    assert screenshots(cli, before) == 0


def test_expecting_any_change_with_only_the_picture_changed_is_unconfirmed(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path)
    toggles(cli, BESIDE_LINE)

    result = press_line(toolbox, expect="any")

    assert result["expected_met"] is None and result["verdict"] == "unconfirmed"


def test_a_ref_click_compares_around_its_element(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path)
    cli.on_command = lambda name: cli.screenshot_patches.update({"w-1": list(QUERY_BESIDE)}) if name == "click" else None

    result = toolbox.call("click", {"ref": QUERY_REF, "intent": "누른다", "expect": "value_changed",
                                    "knowledge_id": None})

    assert result["expected_met"] is None and result["effects"]["picture"]["changed"] is True, result


def test_a_mouse_click_compares_around_its_point(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, asker=ScriptedAsker([Answer(text="맞아")]))
    toggles(cli, BESIDE_LINE)
    toolbox.call("ask", {"kind": "confirm", "text": "여기인가요?", "proposed": 1, "basis": [],
                         "candidates": [{"label": "점", "mark": LINE_MARK, "capture": None, "x": None, "y": None}]})

    result = toolbox.call("mouse_click", {"window_id": "w-1", "x": 1110, "y": 560, "button": "left",
                                          "intent": "누른다", "expect": "value_changed", "knowledge_id": None})

    assert result["expected_met"] is None and result["effects"]["picture"]["pixels"] == 71, result


def test_a_click_point_compares_around_its_point(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, asker=ScriptedAsker([Answer(text="맞아")]))
    toolbox.call("look", {"mark": None})
    shot = toolbox._captures["c1"]
    x, y = 1110 - shot.origin[0], 560 - shot.origin[1]
    toolbox.call("ask", {"kind": "confirm", "text": "여기인가요?", "proposed": 1, "basis": [],
                         "candidates": [{"label": "점", "mark": None, "capture": "c1", "x": x, "y": y}]})
    toggles(cli, BESIDE_LINE)

    result = toolbox.call("click_point", point_call(capture="c1", x=x, y=y, expect="value_changed"))

    assert result["expected_met"] is None and result["effects"]["picture"]["pixels"] == 71, result


def test_a_failed_screenshot_leaves_the_verdict_as_before(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    toggles(cli, BESIDE_LINE)
    cli.failing_screenshots = {"w-1"}

    result = press_line(toolbox)

    assert result["expected_met"] is False and "picture" not in result["effects"]


def test_a_screenshot_failing_after_the_action_leaves_the_verdict_as_before(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")

    def click(_x, _y):
        cli.screenshot_patches["w-1"] = list(BESIDE_LINE)
        cli.failing_screenshots = {"w-1"}
    cli.on_click = click

    result = press_line(toolbox)

    assert result["expected_met"] is False and "picture" not in result["effects"]


def test_without_pillow_the_verdict_is_as_before_and_nothing_is_screenshot(tmp_path, monkeypatch):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    toggles(cli, BESIDE_LINE)
    monkeypatch.setattr(capture, "Image", None)
    before = len(cli.command_names())

    result = press_line(toolbox)

    assert result["expected_met"] is False and "picture" not in result["effects"]
    assert screenshots(cli, before) == 0


def test_a_pressed_window_that_closed_is_not_compared_and_stays_unconfirmed(tmp_path):
    toolbox, cli, _ = toolbox_with(tmp_path, image_mode="off")
    cli.open_window(LIST_WINDOW)
    toolbox.call("observe", {**OBSERVE, "window_id": "w-2"})
    cli.scene["trees"] = {"w-2": {"role": "window", "name": "목록", "bounds": LIST_WINDOW["bounds"], "children": [
        {"role": "listitem", "name": "항목", "bounds": {"x": 1120, "y": 600, "width": 60, "height": 20}}]}}
    toolbox.call("observe", {**OBSERVE, "window_id": "w-2"})
    cli.on_click = lambda _x, _y: cli.close_window("w-2")
    mark = next(n for n, e in toolbox._marks.items() if e.get("name") == "항목")

    result = toolbox.call("click_part", part_call(mark=mark, part="center", expect="value_changed"))

    assert result["expected_met"] is None and result["verdict"] == "unconfirmed"
    assert "picture" not in result["effects"]


def test_without_images_an_unconfirmed_result_is_told_in_text_only(tmp_path):
    toolbox, cli, observer = toolbox_with(tmp_path, image_mode="off")
    toggles(cli, BESIDE_LINE)

    result = press_line(toolbox)

    assert result["verdict"] == "unconfirmed" and "capture" not in result
    assert not [e for e in observer.events if e["type"] == "capture"]


def test_the_model_is_told_about_the_unconfirmed_verdict():
    from agent.loop import SYSTEM_PROMPT
    from agent.tool_schemas import EXPECT

    prompt = SYSTEM_PROMPT.replace("\n  ", " ")
    assert 'expected_met is null with verdict "unconfirmed"' in prompt and "effects.picture" in prompt
    assert "window_opened and window_closed are judged from the window list alone" in prompt
    assert "It does not count as an unexpected result." in prompt
    assert "verdict unconfirmed" in EXPECT["description"]


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "knowledge", "scene")


def test_an_unconfirmed_action_that_cites_knowledge_is_not_blocked(tmp_path, store):
    entry = store.add({"type": "resolution", "status": "active", "situation": "목록 항목을 눌러도 글이 같다",
                       "resolution": "체크 칸을 그림으로 확인한다"})
    toolbox, cli, observer = toolbox_with(tmp_path, knowledge=store)
    toggles(cli, BESIDE_LINE)

    result = press_line(toolbox, knowledge_id=entry["id"])

    assert result["expected_met"] is None
    assert store.get(entry["id"])["failures"] == 0
    assert not [e for e in observer.events if e["type"] == "knowledge_failed"]
    assert press_line(toolbox)["ok"]
