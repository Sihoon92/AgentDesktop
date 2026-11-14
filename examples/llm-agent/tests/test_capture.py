import json
from pathlib import Path

import pytest
from PIL import Image

from agent import capture
from agent.capture import Capture, other_windows, stacked, take, to_screen, window_at
from agent.geometry import overlap_area

WINDOW = {"id": "w-1", "title": "메인", "bounds": {"x": 1000, "y": 500, "width": 416, "height": 308}}
BACKGROUND = (240, 240, 240)
TARGET = {"x": 1208, "y": 650, "width": 100, "height": 24}


def _mark(n, x, y, width=40, height=20):
    return {"mark": n, "kind": "text", "name": f"m{n}", "bounds": {"x": x, "y": y, "width": width, "height": height}}


MARKS = {1: _mark(1, 1408, 790)}
MARK_AT_1108_600 = _mark(1, 1108, 600)


def many_marks(count):
    return {n: _mark(n, 1010 + (n % 30) * 12, 510 + (n // 30) * 40, 10, 10) for n in range(1, count + 1)}


class FakeDesktop:
    def __init__(self, size=(400, 300), ok=True):
        self.size = size
        self.ok = ok
        self.calls = []

    def run(self, *args, headed=False):
        self.calls.append(args)
        if not self.ok:
            return {"ok": False, "error": {"code": "ACTION_FAILED", "message": "no"}}
        Image.new("RGB", self.size, BACKGROUND).save(args[-1])
        return {"ok": True, "data": {"path": args[-1], "width": self.size[0], "height": self.size[1]}}


@pytest.fixture
def desktop():
    return FakeDesktop()


def pixel(path, x, y):
    with Image.open(path) as image:
        return image.convert("RGB").getpixel((x, y))


def test_whole_window_capture_maps_image_to_screen(desktop, tmp_path):
    shot = take(desktop, WINDOW, MARKS, tmp_path, "c1")
    assert isinstance(shot, Capture)
    assert (shot.width, shot.height) == (400, 300) and shot.origin == (1008, 500)
    assert shot.id == "c1" and shot.path == Path(tmp_path) / "c1.png" and shot.path.exists()
    assert shot.window_id == "w-1" and shot.window_bounds == WINDOW["bounds"]
    assert not shot.cropped
    assert desktop.calls[0][:3] == ("screenshot", "--window-id", "w-1")
    assert to_screen(shot, 10, 20) == (1018, 520)
    assert to_screen(shot, 0, 0) == (1008, 500)
    assert to_screen(shot, 399, 299) == (1407, 799)


@pytest.mark.parametrize("x,y", [(400, 0), (0, 300), (-1, 5), (5, -1)])
def test_points_outside_the_capture_map_to_nothing(desktop, tmp_path, x, y):
    assert to_screen(take(desktop, WINDOW, MARKS, tmp_path, "c1"), x, y) is None


def test_crop_around_a_mark_keeps_the_mapping(desktop, tmp_path):
    shot = take(desktop, WINDOW, MARKS, tmp_path, "c2", around=TARGET)
    assert shot.cropped and shot.origin == (1088, 530)
    assert (shot.width, shot.height) == (320, 264)
    assert Image.open(shot.path).size == (320, 264)
    assert to_screen(shot, 120, 120) == (1208, 650)


def test_mark_boxes_are_drawn(desktop, tmp_path):
    shot = take(desktop, WINDOW, {1: MARK_AT_1108_600}, tmp_path, "c3")
    assert shot.drawn and pixel(shot.path, 100, 100) != BACKGROUND
    assert pixel(shot.path, 300, 250) == BACKGROUND


def test_marks_outside_the_image_are_not_drawn_or_counted(desktop, tmp_path):
    outside = {n: _mark(n, 5000, 5000) for n in range(1, 200)}
    shot = take(desktop, WINDOW, {**outside, 999: MARK_AT_1108_600}, tmp_path, "c3")
    assert shot.drawn and pixel(shot.path, 100, 100) != BACKGROUND


def test_too_many_marks_are_not_drawn(desktop, tmp_path):
    shot = take(desktop, WINDOW, many_marks(151), tmp_path, "c4")
    assert shot.drawn is False
    assert pixel(shot.path, 14, 10) == BACKGROUND


def test_without_pillow_the_raw_window_is_returned(desktop, tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "Image", None)
    shot = take(desktop, WINDOW, MARKS, tmp_path, "c5", around=TARGET)
    assert shot is not None and not shot.drawn and not shot.cropped
    assert (shot.width, shot.height) == (400, 300) and shot.origin == (1008, 500)


def test_failed_screenshot_gives_none(tmp_path):
    assert take(FakeDesktop(ok=False), WINDOW, MARKS, tmp_path, "c6") is None


def test_window_without_bounds_gives_none(desktop, tmp_path):
    assert take(desktop, {"id": "w-1", "bounds": None}, MARKS, tmp_path, "c7") is None


MAIN_COLOR = (200, 220, 240)
POPUP_COLOR = (10, 120, 30)
SECOND_POPUP_COLOR = (150, 20, 20)
PROCESS = {"pid": 7, "process_instance": "inst-7"}
MAIN_ROW = {**WINDOW, **PROCESS, "visible": True, "minimized": False}
POPUP = {"id": "w-2", "title": "", "bounds": {"x": 1100, "y": 700, "width": 216, "height": 208},
         **PROCESS, "visible": True, "minimized": False}


class WindowsDesktop:
    """Screenshots each window at its visible size (bounds minus a 16 px wide, 8 px tall frame)."""

    def __init__(self, rows, colors, failing=()):
        self.rows = {row["id"]: row for row in rows}
        self.colors = colors
        self.failing = set(failing)
        self.calls = []

    def run(self, *args, headed=False):
        self.calls.append(args)
        window_id = args[args.index("--window-id") + 1]
        if window_id in self.failing:
            return {"ok": False, "error": {"code": "WINDOW_NOT_FOUND", "message": "gone"}}
        bounds = self.rows[window_id]["bounds"]
        size = (int(bounds["width"]) - 16, int(bounds["height"]) - 8)
        Image.new("RGB", size, self.colors[window_id]).save(args[-1])
        return {"ok": True, "data": {"path": args[-1], "width": size[0], "height": size[1]}}


def popup_at(window_id, x, y, width=216, height=208):
    return {**POPUP, "id": window_id, "bounds": {"x": x, "y": y, "width": width, "height": height}}


def test_a_popup_is_pasted_where_it_sits_on_screen(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[POPUP])

    assert shot.origin == (1008, 500) and (shot.width, shot.height) == (400, 400)
    assert Image.open(shot.path).size == (400, 400)
    assert pixel(shot.path, 10, 10) == MAIN_COLOR
    assert pixel(shot.path, 100, 200) == POPUP_COLOR and pixel(shot.path, 299, 399) == POPUP_COLOR
    assert pixel(shot.path, 50, 350) == capture.FILL_COLOR
    assert [window_id for window_id, _ in shot.windows] == ["w-1", "w-2"]
    assert shot.missing == 0
    assert [call[2] for call in desktop.calls] == ["w-1", "w-2"]
    assert to_screen(shot, 150, 350) == (1158, 850)
    assert window_at(shot, 1158, 850) == "w-2" and window_at(shot, 1010, 510) == "w-1"


def test_a_popup_above_and_left_of_the_main_window_moves_the_origin(tmp_path):
    popup = popup_at("w-2", 900, 400)
    desktop = WindowsDesktop([MAIN_ROW, popup], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[popup])

    assert shot.origin == (908, 400) and (shot.width, shot.height) == (500, 400)
    assert pixel(shot.path, 0, 0) == POPUP_COLOR
    assert pixel(shot.path, 499, 399) == MAIN_COLOR
    assert pixel(shot.path, 450, 50) == capture.FILL_COLOR
    assert to_screen(shot, 0, 0) == (908, 400)


def test_windows_listed_first_are_drawn_on_top_and_win_the_point(tmp_path):
    first, second = popup_at("w-2", 1100, 600), popup_at("w-3", 1150, 650)
    desktop = WindowsDesktop([MAIN_ROW, first, second],
                             {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR, "w-3": SECOND_POPUP_COLOR})

    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[second, first])

    assert pixel(shot.path, 1200 - 1008, 700 - 500) == SECOND_POPUP_COLOR
    assert pixel(shot.path, 1110 - 1008, 610 - 500) == POPUP_COLOR
    assert window_at(shot, 1200, 700) == "w-3" and window_at(shot, 1110, 610) == "w-2"


def test_a_point_on_no_window_belongs_to_the_main_window(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})
    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[POPUP])

    assert window_at(shot, 1020, 850) == "w-1"


def test_marks_inside_a_popup_are_drawn_on_it(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    shot = take(desktop, MAIN_ROW, {1: _mark(1, 1150, 820)}, tmp_path, "c1", stack=[POPUP])

    assert shot.drawn and pixel(shot.path, 1150 - 1008, 820 - 500) == capture.BOX_COLOR


def test_crop_works_on_the_combined_image(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})
    around = {"x": 1150, "y": 850, "width": 20, "height": 20}

    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", around=around, stack=[POPUP])

    assert shot.cropped and to_screen(shot, 120, 120) == (1150, 850)


def test_a_failed_popup_is_left_out_and_counted(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR}, failing={"w-2"})

    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[POPUP])

    assert (shot.width, shot.height) == (400, 300) and shot.origin == (1008, 500)
    assert [window_id for window_id, _ in shot.windows] == ["w-1"] and shot.missing == 1
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == ["c1.png"]


def test_a_failed_main_window_gives_none_even_with_popups(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-2": POPUP_COLOR}, failing={"w-1"})

    assert take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[POPUP]) is None
    assert [call[2] for call in desktop.calls] == ["w-1"]


def test_popup_screenshots_leave_no_files_behind(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[POPUP])

    assert sorted(p.name for p in Path(tmp_path).iterdir()) == ["c1.png"]


def test_a_single_window_capture_keeps_its_one_window(desktop, tmp_path):
    shot = take(desktop, WINDOW, MARKS, tmp_path, "c1")

    assert shot.windows == (("w-1", WINDOW["bounds"]),) and shot.missing == 0
    assert len(desktop.calls) == 1


def test_without_pillow_popups_are_left_out_and_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "Image", None)
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=[POPUP])

    assert (shot.width, shot.height) == (400, 300) and shot.missing == 1
    assert [call[2] for call in desktop.calls] == ["w-1"]


@pytest.mark.parametrize("change", [
    {"pid": 8},
    {"process_instance": "inst-other"},
    {"minimized": True},
    {"visible": False},
    {"bounds": {"x": 1100, "y": 700, "width": 0, "height": 10}},
    {"bounds": None},
    {"id": "w-1"},
])
def test_other_windows_are_visible_windows_of_the_same_process(change):
    assert other_windows(MAIN_ROW, [MAIN_ROW, {**POPUP, **change}]) == []


def test_other_windows_keep_the_listing_order():
    first, second = popup_at("w-3", 1100, 600), popup_at("w-2", 1150, 650)

    assert other_windows(MAIN_ROW, [second, MAIN_ROW, first]) == [second, first]


def test_a_window_without_process_identity_has_no_other_windows():
    lone = {"id": "w-1", "bounds": WINDOW["bounds"]}

    assert other_windows(lone, [lone, {**POPUP, "pid": None, "process_instance": None}]) == []


MAXIMIZED_LEFT = {**MAIN_ROW, "id": "w-10", "bounds": {"x": -8, "y": -8, "width": 1936, "height": 1048}}
MAXIMIZED_RIGHT = {**MAIN_ROW, "id": "w-11", "bounds": {"x": 1912, "y": -8, "width": 1936, "height": 1048}}


def test_a_list_spilling_past_the_bottom_of_the_window_comes_in():
    spilling = popup_at("w-2", 1100, 650, width=200, height=250)

    assert other_windows(MAIN_ROW, [MAIN_ROW, spilling]) == [spilling]


def test_a_maximized_window_on_the_next_monitor_stays_out():
    assert other_windows(MAXIMIZED_LEFT, [MAXIMIZED_LEFT, MAXIMIZED_RIGHT]) == []


def test_the_two_captured_footprint_windows_stay_apart():
    rows = json.loads((Path(__file__).parent / "fixtures" / "windows.json").read_text(encoding="utf-8"))["data"]

    assert len(rows) == 2 and rows[0]["pid"] == rows[1]["pid"]
    assert other_windows(rows[0], rows) == [] and other_windows(rows[1], rows) == []


def test_observing_a_popup_brings_in_the_window_under_it():
    assert other_windows(POPUP, [MAIN_ROW, POPUP]) == [MAIN_ROW]


def test_a_popup_over_a_popup_follows_its_parent_in():
    parent = popup_at("w-2", 1300, 700, width=200, height=200)
    child = popup_at("w-3", 1420, 750, width=200, height=100)

    assert other_windows(MAIN_ROW, [child, MAIN_ROW, parent]) == [child, parent]


def test_overlap_is_measured_against_the_smaller_window():
    just_enough = popup_at("w-2", 1366, 450, width=100, height=100)
    too_little = popup_at("w-3", 1367, 450, width=100, height=100)

    assert overlap_area(just_enough["bounds"], MAIN_ROW["bounds"]) == 50 * 50
    assert other_windows(MAIN_ROW, [MAIN_ROW, just_enough]) == [just_enough]
    assert other_windows(MAIN_ROW, [MAIN_ROW, too_little]) == []
    assert capture.OVERLAP_SHARE == 0.25


def test_a_list_opened_inside_the_window_comes_in_even_when_most_of_it_hangs_below():
    mostly_outside = popup_at("w-2", 1100, 760, width=200, height=300)

    assert overlap_area(mostly_outside["bounds"], MAIN_ROW["bounds"]) < 0.25 * 200 * 300
    assert other_windows(MAIN_ROW, [MAIN_ROW, mostly_outside]) == [mostly_outside]


def test_a_window_starting_outside_and_barely_overlapping_stays_out():
    barely = popup_at("w-2", 1300, 790, width=400, height=300)

    assert other_windows(MAIN_ROW, [MAIN_ROW, barely]) == []


@pytest.mark.parametrize("x,y,joins", [(1392, 784, True), (1393, 784, False), (1392, 785, False)])
def test_a_corner_must_be_inset_px_inside_the_window(x, y, joins):
    corner = popup_at("w-2", x, y, width=400, height=400)

    assert capture.INSET_PX == 24
    assert (other_windows(MAIN_ROW, [MAIN_ROW, corner]) == [corner]) is joins


def test_an_observed_popup_stays_on_top_of_the_window_listed_under_it(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    shot = take(desktop, POPUP, {}, tmp_path, "c1", stack=stacked(POPUP, [POPUP, MAIN_ROW]))

    assert shot.window_id == "w-2" and shot.origin == (1008, 500)
    assert pixel(shot.path, 1150 - 1008, 750 - 500) == POPUP_COLOR
    assert pixel(shot.path, 1010 - 1008, 510 - 500) == MAIN_COLOR
    assert [window_id for window_id, _ in shot.windows] == ["w-1", "w-2"]
    assert window_at(shot, 1150, 750) == "w-2" and window_at(shot, 1010, 510) == "w-1"


def test_a_window_listed_above_the_observed_window_covers_it(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    shot = take(desktop, POPUP, {}, tmp_path, "c1", stack=stacked(POPUP, [MAIN_ROW, POPUP]))

    assert pixel(shot.path, 1150 - 1008, 750 - 500) == MAIN_COLOR
    assert pixel(shot.path, 1150 - 1008, 850 - 500) == POPUP_COLOR
    assert [window_id for window_id, _ in shot.windows] == ["w-2", "w-1"]
    assert window_at(shot, 1150, 750) == "w-1" and window_at(shot, 1150, 850) == "w-2"


def test_a_main_window_listed_over_its_popup_is_drawn_over_it(tmp_path):
    desktop = WindowsDesktop([MAIN_ROW, POPUP], {"w-1": MAIN_COLOR, "w-2": POPUP_COLOR})

    shot = take(desktop, MAIN_ROW, {}, tmp_path, "c1", stack=stacked(MAIN_ROW, [MAIN_ROW, POPUP]))

    assert pixel(shot.path, 1150 - 1008, 750 - 500) == MAIN_COLOR
    assert window_at(shot, 1150, 750) == "w-1"


def test_stacked_keeps_the_listing_order_with_the_observed_window_in_its_place():
    child = popup_at("w-3", 1150, 650)
    stranger = {**POPUP, "id": "w-9", "pid": 8}

    assert stacked(MAIN_ROW, [child, stranger, MAIN_ROW, POPUP]) == [child, MAIN_ROW, POPUP]
    assert stacked(POPUP, [POPUP, MAIN_ROW]) == [POPUP, MAIN_ROW]


def test_stacked_puts_an_unlisted_observed_window_at_the_bottom():
    assert stacked(MAIN_ROW, [POPUP]) == [POPUP, MAIN_ROW]
    assert stacked(MAIN_ROW, []) == [MAIN_ROW]


def test_a_stack_holding_only_the_observed_window_reads_and_writes_nothing_more(desktop, tmp_path):
    shot = take(desktop, WINDOW, {}, tmp_path, "c1", stack=[WINDOW])

    assert shot.windows == (("w-1", WINDOW["bounds"]),) and len(desktop.calls) == 1


RECORDED_SIZE = (1929, 1244)
RECORDED_ITEM = {"x": 337, "y": 404, "width": 98, "height": 49}


def recorded_pair():
    """The geometry measured on a real run: a 71-pixel check box to the left of the pressed
    item's text, and the whole title bar repainted in the window's active colour."""
    before = Image.new("RGB", RECORDED_SIZE, (255, 255, 255))
    after = before.copy()
    after.paste((52, 52, 52), (299, 422, 317, 425))
    after.paste((52, 52, 52), (299, 425, 316, 426))
    after.paste((200, 220, 255), (0, 0, RECORDED_SIZE[0], 52))
    return before, after


def test_the_recorded_check_box_beside_the_item_is_a_picture_change():
    before, after = recorded_pair()
    box = capture.region_box(RECORDED_ITEM, (0, 0), RECORDED_SIZE)

    pixels = capture.count_changed(before.crop(box), after.crop(box))

    assert pixels == 71 and pixels >= capture.CHANGED_PIXELS
    assert box == (337 - capture.PICTURE_MARGIN_PX, 404 - capture.PICTURE_MARGIN_PX,
                   435 + capture.PICTURE_MARGIN_PX, 453 + capture.PICTURE_MARGIN_PX)


def test_the_recorded_title_bar_change_is_outside_the_region():
    before, after = recorded_pair()
    box = capture.region_box(RECORDED_ITEM, (0, 0), RECORDED_SIZE)

    assert box[1] > 52
    assert capture.count_changed(before.crop((0, 0, RECORDED_SIZE[0], 52)), after.crop((0, 0, RECORDED_SIZE[0], 52))) > 0


def test_the_region_is_cut_to_the_window_image():
    assert capture.region_box({"x": 1010, "y": 505, "width": 20, "height": 10}, (1008, 500), (400, 300)) == (
        0, 0, 2 + 20 + capture.PICTURE_MARGIN_PX, 5 + 10 + capture.PICTURE_MARGIN_PX)
    assert capture.region_box({"x": 1390, "y": 790, "width": 20, "height": 10}, (1008, 500), (400, 300)) == (
        382 - capture.PICTURE_MARGIN_PX, 290 - capture.PICTURE_MARGIN_PX, 400, 300)
    assert capture.region_box({"x": 5000, "y": 5000, "width": 20, "height": 10}, (1008, 500), (400, 300)) is None


def test_a_change_below_the_pixel_count_is_no_change():
    before = Image.new("RGB", (100, 100), (255, 255, 255))
    after = before.copy()
    after.paste((0, 0, 0), (0, 0, capture.CHANGED_PIXELS - 1, 1))

    assert capture.count_changed(before, after) == capture.CHANGED_PIXELS - 1
    after.paste((0, 0, 0), (0, 1, 1, 2))
    assert capture.count_changed(before, after) == capture.CHANGED_PIXELS


def test_a_faint_tint_is_not_counted():
    before = Image.new("RGB", (40, 40), (255, 255, 255))
    faint = Image.new("RGB", (40, 40), (255 - capture.PIXEL_TOLERANCE, 255, 255))
    strong = Image.new("RGB", (40, 40), (255 - capture.PIXEL_TOLERANCE - 1, 255, 255))

    assert capture.count_changed(before, faint) == 0
    assert capture.count_changed(before, strong) == 40 * 40


def test_a_region_shot_keeps_only_the_part_around_the_target(tmp_path):
    desktop = FakeDesktop()
    region = capture.shoot_region(desktop, WINDOW, TARGET, tmp_path / "p.png")

    assert region.window_id == "w-1" and region.size == (400, 300)
    assert region.box == (200 - capture.PICTURE_MARGIN_PX, 150 - capture.PICTURE_MARGIN_PX,
                          300 + capture.PICTURE_MARGIN_PX, 174 + capture.PICTURE_MARGIN_PX)
    assert region.image.size == (region.box[2] - region.box[0], region.box[3] - region.box[1])
    assert not (tmp_path / "p.png").exists()
    assert capture.changed_pixels(desktop, region, tmp_path / "q.png") == 0


def test_a_region_shot_of_a_resized_window_is_not_compared(tmp_path):
    region = capture.shoot_region(FakeDesktop(), WINDOW, TARGET, tmp_path / "p.png")

    assert capture.changed_pixels(FakeDesktop(size=(380, 300)), region, tmp_path / "q.png") is None
    assert capture.changed_pixels(FakeDesktop(ok=False), region, tmp_path / "q.png") is None


def test_without_pillow_no_region_shot_is_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "Image", None)
    desktop = FakeDesktop()

    assert capture.shoot_region(desktop, WINDOW, TARGET, tmp_path / "p.png") is None
    assert desktop.calls == []
