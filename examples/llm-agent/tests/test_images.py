import base64
import json
from pathlib import Path

import pytest

from agent.capture import Capture
from agent.images import image_items

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="
)


@pytest.fixture
def shot(tmp_path):
    path = tmp_path / "c1.png"
    path.write_bytes(PNG)
    return Capture(id="c1", path=Path(path), width=1, height=1, origin=(0, 0), window_id="w-1",
                   window_bounds={"x": 0, "y": 0, "width": 1, "height": 1}, drawn=True, cropped=False)


def test_user_message_mode_appends_an_image_message(shot):
    items = image_items("call_1", {"ok": True}, [shot], "user_message")
    assert items[0] == {"type": "function_call_output", "call_id": "call_1", "output": json.dumps({"ok": True})}
    assert items[1]["role"] == "user"
    assert items[1]["content"][0]["type"] == "input_text" and "c1" in items[1]["content"][0]["text"]
    assert items[1]["content"][1]["type"] == "input_image"
    assert items[1]["content"][1]["image_url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_tool_output_mode_puts_the_image_in_the_output(shot):
    items = image_items("call_1", {"ok": True}, [shot], "tool_output")
    assert len(items) == 1 and items[0]["call_id"] == "call_1"
    assert [part["type"] for part in items[0]["output"]] == ["input_text", "input_image"]
    assert json.loads(items[0]["output"][0]["text"]) == {"ok": True}


def test_off_mode_sends_no_image(shot):
    items = image_items("call_1", {"ok": True}, [shot], "off")
    assert len(items) == 1 and json.loads(items[0]["output"]) == {"ok": True, "image": "unavailable"}


@pytest.mark.parametrize("mode", ["off", "user_message", "tool_output"])
def test_no_capture_gives_the_plain_output(mode):
    assert image_items("call_1", {"ok": True, "텍스트": "가"}, [], mode) == [
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok": true, "텍스트": "가"}'}]


def test_two_captures_go_in_one_message(shot):
    items = image_items("call_1", {"ok": True}, [shot, shot], "user_message")
    assert [part["type"] for part in items[1]["content"]] == ["input_text", "input_image", "input_image"]


def test_unreadable_capture_is_skipped(shot, tmp_path):
    missing = Capture(**{**shot.__dict__, "path": tmp_path / "gone.png"})
    items = image_items("call_1", {"ok": True}, [missing], "user_message")
    assert len(items) == 1 and json.loads(items[0]["output"])["image"] == "unavailable"


def test_unknown_mode_raises(shot):
    with pytest.raises(ValueError):
        image_items("call_1", {"ok": True}, [shot], "inline")
