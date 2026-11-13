from pathlib import Path

import pytest
from PIL import Image

from agent import capture
from agent.capture import Capture, take, to_screen

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
