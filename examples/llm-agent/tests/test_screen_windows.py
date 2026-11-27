"""Captures for the model and click_point when the target shows a popup window of its own."""

import copy
import time

from fake_cli import FakeCli, scene_window
from PIL import Image
from test_tools import MAIN, OBSERVE, SCENE, EventObserver, point_call

from agent.ask import Answer
from agent.capture import FILL_COLOR
from agent.desktop import DesktopClient
from agent.task import Task
from agent.tools import Toolbox

EXE = r"C:\apps\Scene.exe"
POPUP = scene_window("w-2", "", {"x": 1100, "y": 650, "width": 216, "height": 258})
POPUP_PIXELS = (10, 120, 30)
ON_POPUP = (142, 350)
ON_POPUP_SCREEN = (1150, 850)


class ConfirmingAsker:
    interactive = True

    def __init__(self):
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return Answer(text="맞아")


def toolbox_with(tmp_path, windows=(POPUP, MAIN)):
    """A toolbox launched on MAIN that then lists `windows`, top first, and observed MAIN."""
    scene = copy.deepcopy(SCENE)
    scene["windows"] = [copy.deepcopy(MAIN)]
    cli = FakeCli(scene=scene)
    cli.screenshot_colors = {"w-2": POPUP_PIXELS}
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, 2, [], image_mode="user_message"),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=EventObserver(),
        asker=ConfirmingAsker(),
        captures_dir=tmp_path / "captures",
        wall=lambda: time.time() - 60,
    )
    toolbox.call("launch_target", {})
    cli.scene["windows"] = copy.deepcopy(list(windows))
    toolbox.call("observe", OBSERVE)
    return toolbox, cli


def confirm_point(toolbox, capture, x, y):
    candidate = {"label": "찍은 곳", "mark": None, "capture": capture, "x": x, "y": y}
    return toolbox.call("ask", {"kind": "confirm", "text": "여기인가요?", "candidates": [candidate],
                                "proposed": 1, "basis": []})


def counts(cli, since):
    names = cli.command_names()[since:]
    return names.count("screenshot"), names.count("list-windows")


def test_look_shows_the_popup_with_one_more_screenshot_and_no_more_listings(tmp_path):
    alone, alone_cli = toolbox_with(tmp_path / "one", windows=(MAIN,))
    shown, shown_cli = toolbox_with(tmp_path / "two")
    before_alone, before_shown = len(alone_cli.command_names()), len(shown_cli.command_names())

    alone_result = alone.call("look", {"mark": None})
    result = shown.call("look", {"mark": None})

    assert (alone_result["width"], alone_result["height"]) == (400, 300)
    assert (result["width"], result["height"]) == (400, 400) and "windows_left_out" not in result
    shots, listings = counts(shown_cli, before_shown)
    alone_shots, alone_listings = counts(alone_cli, before_alone)
    assert (shots, listings) == (alone_shots + 1, alone_listings)
    with Image.open(shown.take_attachments()[0].path) as image:
        assert image.convert("RGB").getpixel(ON_POPUP) == POPUP_PIXELS
        assert image.convert("RGB").getpixel((50, 350)) == FILL_COLOR


def test_look_says_how_many_windows_it_left_out(tmp_path):
    toolbox, cli = toolbox_with(tmp_path)
    cli.failing_screenshots = {"w-2"}

    result = toolbox.call("look", {"mark": None})

    assert result["windows_left_out"] == 1 and (result["width"], result["height"]) == (400, 300)


def test_a_point_on_the_popup_clicks_the_popup_window(tmp_path):
    toolbox, cli = toolbox_with(tmp_path)
    toolbox.call("look", {"mark": None})
    confirm_point(toolbox, "c1", *ON_POPUP)

    result = toolbox.call("click_point", point_call(capture="c1", x=ON_POPUP[0], y=ON_POPUP[1]))

    assert result["ok"], result
    assert "--window-id" in cli.last_call("focus-window") and "w-2" in cli.last_call("focus-window")
    assert f"--xy={ON_POPUP_SCREEN[0]},{ON_POPUP_SCREEN[1]}" in cli.full_call("mouse-click")


def test_a_point_on_the_main_window_still_clicks_the_main_window(tmp_path):
    toolbox, cli = toolbox_with(tmp_path)
    toolbox.call("look", {"mark": None})
    confirm_point(toolbox, "c1", 10, 20)

    assert toolbox.call("click_point", point_call(capture="c1", x=10, y=20))["ok"]
    assert "w-1" in cli.last_call("focus-window")


def test_the_preview_of_a_popup_point_names_the_popup_window(tmp_path):
    toolbox, _ = toolbox_with(tmp_path)
    toolbox.call("look", {"mark": None})

    target = toolbox.preview("click_point", point_call(capture="c1", x=ON_POPUP[0], y=ON_POPUP[1]))

    assert target == {"window_id": "w-2", "points": [list(ON_POPUP_SCREEN)]}


def test_a_closed_popup_makes_the_capture_stale(tmp_path):
    toolbox, cli = toolbox_with(tmp_path)
    toolbox.call("look", {"mark": None})
    cli.scene["windows"] = [row for row in cli.scene["windows"] if row["id"] != "w-2"]

    result = toolbox.call("click_point", point_call(capture="c1", x=10, y=20))

    assert result["refused_code"] == "stale_capture"


def test_a_moved_popup_makes_the_capture_stale(tmp_path):
    toolbox, cli = toolbox_with(tmp_path)
    toolbox.call("look", {"mark": None})
    cli.scene["windows"][1]["bounds"]["y"] += 10

    assert toolbox.call("click_point", point_call(capture="c1", x=10, y=20))["refused_code"] == "stale_capture"


def test_the_capture_for_a_refused_point_shows_the_popup(tmp_path):
    toolbox, _ = toolbox_with(tmp_path)
    toolbox.call("look", {"mark": None})
    toolbox.take_attachments()

    result = toolbox.call("click_point", point_call(capture="c1", x=10, y=20))

    assert result["refused_code"] == "needs_confirmation"
    attached = toolbox.take_attachments()
    assert [window_id for window_id, _ in attached[0].windows] == ["w-1", "w-2"]


def test_a_capture_of_the_main_window_listed_over_the_popup_draws_the_main_window_on_top(tmp_path):
    toolbox, _ = toolbox_with(tmp_path, windows=(MAIN, POPUP))

    toolbox.call("look", {"mark": None})

    shot = toolbox.take_attachments()[0]
    assert [window_id for window_id, _ in shot.windows] == ["w-2", "w-1"]


SPILLED_ITEM = {"x": 1150, "y": 850, "width": 60, "height": 20}
COVERING_ITEM = {"x": 1150, "y": 700, "width": 60, "height": 20}
LISTINGS_PER_CLICK_PART_BEFORE = 4


def toolbox_with_popup_items(tmp_path, windows=(POPUP, MAIN)):
    toolbox, cli = toolbox_with(tmp_path, windows)
    children = cli.scene["tree"]["children"]
    children.append({"role": "listitem", "name": "넘친 항목", "bounds": SPILLED_ITEM})
    children.append({"role": "listitem", "name": "겹친 항목", "bounds": COVERING_ITEM})
    toolbox.call("observe", OBSERVE)
    return toolbox, cli


def mark_named(toolbox, name):
    return next(number for number, element in toolbox._marks.items() if element.get("name") == name)


def part_click(toolbox, name):
    return toolbox.call("click_part", {"mark": mark_named(toolbox, name), "part": "center", "action": "click",
                                       "intent": "누른다", "knowledge_id": None})


def test_click_part_on_a_popup_item_past_the_window_edge_clicks_the_popup(tmp_path):
    toolbox, cli = toolbox_with_popup_items(tmp_path)

    result = part_click(toolbox, "넘친 항목")

    assert result["ok"], result
    assert "w-2" in cli.last_call("focus-window")
    assert "--xy=1180,860" in cli.full_call("mouse-click")


def test_click_part_on_a_popup_item_over_the_window_clicks_the_popup(tmp_path):
    toolbox, cli = toolbox_with_popup_items(tmp_path)

    assert part_click(toolbox, "겹친 항목")["ok"]
    assert "w-2" in cli.last_call("focus-window")


def test_click_part_presses_the_window_listed_on_top_where_two_overlap(tmp_path):
    toolbox, cli = toolbox_with_popup_items(tmp_path, windows=(MAIN, POPUP))

    assert part_click(toolbox, "겹친 항목")["ok"]
    assert "w-1" in cli.last_call("focus-window")


def test_click_part_on_the_window_itself_clicks_the_observed_window(tmp_path):
    toolbox, cli = toolbox_with_popup_items(tmp_path)

    assert part_click(toolbox, "Shop")["ok"]
    assert "w-1" in cli.last_call("focus-window")


def test_click_part_ignores_a_window_left_out_of_the_picture(tmp_path):
    elsewhere = scene_window("w-2", "", {"x": 1300, "y": 790, "width": 400, "height": 300})
    toolbox, cli = toolbox_with_popup_items(tmp_path, windows=(MAIN, elsewhere))

    assert part_click(toolbox, "Shop")["ok"]
    assert "w-1" in cli.last_call("focus-window")


def test_click_part_lists_the_windows_once_more_than_before(tmp_path):
    toolbox, cli = toolbox_with_popup_items(tmp_path, windows=(MAIN,))
    before = len(cli.command_names())

    assert part_click(toolbox, "겹친 항목")["ok"]

    assert cli.command_names()[before:].count("list-windows") == LISTINGS_PER_CLICK_PART_BEFORE + 1


def test_click_part_under_a_window_left_out_of_the_picture_clicks_the_observed_window(tmp_path):
    elsewhere = scene_window("w-2", "", {"x": 1300, "y": 790, "width": 400, "height": 300})
    toolbox, cli = toolbox_with(tmp_path, windows=(MAIN, elsewhere))
    cli.scene["tree"]["children"].append({"role": "listitem", "name": "가장자리",
                                          "bounds": {"x": 1320, "y": 792, "width": 40, "height": 12}})
    toolbox.call("observe", OBSERVE)

    assert part_click(toolbox, "가장자리")["ok"]
    assert "--xy=1340,798" in cli.full_call("mouse-click")
    assert "w-1" in cli.last_call("focus-window")


POPUP_TREE = {"role": "window", "name": "", "bounds": POPUP["bounds"], "children": [
    {"role": "listitem", "name": "넘친 항목", "bounds": SPILLED_ITEM},
    {"role": "listitem", "name": "겹친 항목", "bounds": COVERING_ITEM},
]}
DIALOG = scene_window("w-3", "", {"x": 1050, "y": 550, "width": 200, "height": 120})


def toolbox_with_popup_trees(tmp_path):
    """The main window's tree holds the popup's items, as a UIA tree that reaches an owned
    popup does; the popup window alone holds only its items."""
    toolbox, cli = toolbox_with_popup_items(tmp_path)
    cli.scene["trees"] = {"w-2": POPUP_TREE}
    return toolbox, cli


def last_observation(toolbox):
    return toolbox._observer.observations[-1][0]["id"]


def test_after_a_click_on_a_popup_item_the_observation_stays_on_the_observed_window(tmp_path):
    toolbox, _ = toolbox_with_popup_trees(tmp_path)

    result = part_click(toolbox, "겹친 항목")

    assert result["ok"] and result["window_id"] == "w-1", result
    assert "겹친 항목" in result["observation"] and "Shop" in result["observation"]
    assert last_observation(toolbox) == "w-1"
    assert toolbox.call("look", {"mark": None})["window_id"] == "w-1"


def test_after_a_point_on_the_popup_the_observation_stays_on_the_captured_window(tmp_path):
    toolbox, cli = toolbox_with_popup_trees(tmp_path)
    toolbox.call("look", {"mark": None})
    confirm_point(toolbox, "c1", *ON_POPUP)

    result = toolbox.call("click_point", point_call(capture="c1", x=ON_POPUP[0], y=ON_POPUP[1]))

    assert result["ok"] and "w-2" in cli.last_call("focus-window"), result
    assert result["window_id"] == "w-1" and last_observation(toolbox) == "w-1"


def test_a_click_on_a_popup_item_does_not_count_the_observed_window_as_removed(tmp_path):
    toolbox, _ = toolbox_with_popup_trees(tmp_path)
    call = {"mark": mark_named(toolbox, "겹친 항목"), "part": "center", "action": "click",
            "intent": "누른다", "expect": "value_changed", "knowledge_id": None}

    result = toolbox.call("click_part", call)

    assert result["effects"]["removed"]["count"] == 0 and result["effects"]["added"]["count"] == 0
    assert result["effects"]["none"] is True and result["expected_met"] is False
    assert "observed_window" not in result["effects"]


def test_a_point_on_the_popup_is_judged_on_the_captured_window(tmp_path):
    toolbox, _ = toolbox_with_popup_trees(tmp_path)
    toolbox.call("look", {"mark": None})
    confirm_point(toolbox, "c1", *ON_POPUP)

    result = toolbox.call("click_point", point_call(capture="c1", x=ON_POPUP[0], y=ON_POPUP[1],
                                                    expect="value_changed"))

    assert result["effects"]["removed"]["count"] == 0 and result["expected_met"] is False
    assert "observed_window" not in result["effects"] and result["effects"]["none"] is True


def test_when_the_observed_popup_closes_the_observation_moves_to_a_remaining_window(tmp_path):
    toolbox, cli = toolbox_with_popup_trees(tmp_path)
    toolbox.call("observe", {**OBSERVE, "window_id": "w-2"})
    cli.on_click = lambda _x, _y: cli.close_window("w-2")
    call = {"mark": mark_named(toolbox, "겹친 항목"), "part": "center", "action": "click",
            "intent": "누른다", "expect": "value_changed", "knowledge_id": None}

    result = toolbox.call("click_part", call)

    assert result["window_id"] == "w-1" and result["effects"]["windows_closed"] == [{"id": "w-2", "title": ""}]
    assert result["effects"]["observed_window"] == {"before": "w-2", "after": "w-1"}
    assert result["effects"]["added"]["count"] == 0 and result["effects"]["removed"]["count"] == 0
    assert result["effects"]["none"] is False
    assert result["expected_met"] is None and result["verdict"] == "unconfirmed"


def test_an_expected_close_of_the_observed_popup_is_met(tmp_path):
    toolbox, cli = toolbox_with_popup_trees(tmp_path)
    toolbox.call("observe", {**OBSERVE, "window_id": "w-2"})
    cli.on_click = lambda _x, _y: cli.close_window("w-2")
    call = {"mark": mark_named(toolbox, "겹친 항목"), "part": "center", "action": "click",
            "intent": "누른다", "expect": "window_closed", "knowledge_id": None}

    assert toolbox.call("click_part", call)["expected_met"] is True


def test_a_dialog_opened_by_a_click_is_reported_while_the_observation_stays(tmp_path):
    toolbox, cli = toolbox_with_popup_trees(tmp_path)
    cli.on_click = lambda _x, _y: cli.open_window(DIALOG)
    call = {"mark": mark_named(toolbox, "Shop"), "part": "center", "action": "click",
            "intent": "누른다", "expect": "window_opened", "knowledge_id": None}

    result = toolbox.call("click_part", call)

    assert result["window_id"] == "w-1" and result["expected_met"] is True
    assert result["effects"]["windows_opened"] == [{"id": "w-3", "title": ""}]
    assert "observed_window" not in result["effects"]
