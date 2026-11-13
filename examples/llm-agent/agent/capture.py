"""Window captures for the model: optionally cropped around an element, with mark boxes drawn.

A capture remembers where its top-left pixel sits on screen and the window bounds at the
time it was taken, so a point the model picks in the image maps back to the screen, and a
caller can refuse that point once the window has moved. Without Pillow the raw window
screenshot is used as is: no crop, no boxes.
"""

from dataclasses import dataclass
from pathlib import Path

from agent.frames import frame_origin, to_image_rect

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None

CROP_MARGIN_PX = 120
MAX_DRAWN_MARKS = 150
BOX_COLOR = (230, 0, 120)
LABEL_TEXT = (255, 255, 255)


@dataclass(frozen=True)
class Capture:
    id: str
    path: Path
    width: int
    height: int
    origin: tuple
    window_id: str
    window_bounds: dict
    drawn: bool
    cropped: bool


def take(desktop, window, marks, out_dir, capture_id, around=None):
    """Screenshots `window` into out_dir/<capture_id>.png. Returns a Capture, or None on failure.

    `around` is an element's screen bounds; the image is then cut to that box plus
    CROP_MARGIN_PX on each side. Mark boxes are drawn for the marks inside the image
    unless there are more than MAX_DRAWN_MARKS of them.
    """
    bounds = window.get("bounds")
    if not bounds:
        return None
    path = Path(out_dir) / f"{capture_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    shot = desktop.run("screenshot", "--window-id", window["id"], str(path))
    if not shot.get("ok"):
        return None
    size = (shot["data"]["width"], shot["data"]["height"])
    image = _open(path)
    if image is not None:
        size = image.size
    origin = _rounded(frame_origin(bounds, *size))
    drawn = cropped = False
    if image is not None:
        if around:
            box = _crop_box(around, origin, size)
            if box is not None:
                image = image.crop(box)
                origin = (origin[0] + box[0], origin[1] + box[1])
                size = image.size
                cropped = True
        drawn = _draw_marks(image, marks, origin)
        if drawn or cropped:
            image.save(path)
        image.close()
    return Capture(
        id=capture_id, path=path, width=size[0], height=size[1], origin=origin,
        window_id=window["id"], window_bounds=dict(bounds), drawn=drawn, cropped=cropped,
    )


def to_screen(capture, x, y):
    """Maps an image pixel of `capture` to the screen, or None when it lies outside the image."""
    if not (0 <= x < capture.width and 0 <= y < capture.height):
        return None
    return (int(capture.origin[0] + x), int(capture.origin[1] + y))


def _open(path):
    if Image is None:
        return None
    try:
        with Image.open(path) as opened:
            return opened.convert("RGB")
    except (OSError, ValueError):
        return None


def _crop_box(around, origin, size):
    rect = to_image_rect(around, origin, size)
    if rect is None:
        return None
    left = max(int(rect["x"] - CROP_MARGIN_PX), 0)
    top = max(int(rect["y"] - CROP_MARGIN_PX), 0)
    right = min(int(rect["x"] + rect["width"] + CROP_MARGIN_PX), size[0])
    bottom = min(int(rect["y"] + rect["height"] + CROP_MARGIN_PX), size[1])
    return (left, top, right, bottom)


def _draw_marks(image, marks, origin):
    inside = []
    for number in sorted(marks or {}):
        bounds = marks[number].get("bounds")
        rect = to_image_rect(bounds, origin, image.size) if bounds else None
        if rect is not None:
            inside.append((number, rect))
    if not inside or len(inside) > MAX_DRAWN_MARKS:
        return False
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    for number, rect in inside:
        left, top = int(rect["x"]), int(rect["y"])
        right = int(rect["x"] + rect["width"]) - 1
        bottom = int(rect["y"] + rect["height"]) - 1
        draw.rectangle((left, top, max(left, right), max(top, bottom)), outline=BOX_COLOR, width=2)
        label = str(number)
        text_box = draw.textbbox((0, 0), label, font=font)
        width, height = text_box[2] - text_box[0] + 4, text_box[3] - text_box[1] + 4
        draw.rectangle((left, top, left + width, top + height), fill=BOX_COLOR)
        draw.text((left + 2 - text_box[0], top + 2 - text_box[1]), label, fill=LABEL_TEXT, font=font)
    return True


def _rounded(point):
    return (int(round(point[0])), int(round(point[1])))
