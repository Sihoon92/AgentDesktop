"""Window captures for the model: optionally cropped around an element, with mark boxes drawn.

A capture shows the observed window and the other visible windows of the same process (a
popup menu or a dropdown list is a top-level window of its own). Each window is screenshot
alone with `screenshot --window-id` and pasted where it sits on screen; the screen itself is
never captured, so no other application reaches the image. Places no window covers are
FILL_COLOR. The windows are stacked as `list-windows` lists them, top first, the observed
window in its own place (see stacked), so a popup stays on top of the window under it
whichever of the two is observed.

A capture remembers where its top-left pixel sits on screen and the bounds of every window
in it, so a point the model picks in the image maps back to the screen and to the window it
falls on, and a caller can refuse that point once a window has moved. Without Pillow the raw
window screenshot is used as is: no other windows, no crop, no boxes.

shoot_region and changed_pixels compare one window before and after an action, only within
PICTURE_MARGIN_PX of the target, counting pixels that changed by more than PIXEL_TOLERANCE.
"""

from dataclasses import dataclass
from pathlib import Path

from agent.frames import frame_origin, to_image_rect
from agent.geometry import area, contains, has_area, overlap_area

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageChops = None
    ImageDraw = None
    ImageFont = None

CROP_MARGIN_PX = 120
MAX_DRAWN_MARKS = 150
BOX_COLOR = (230, 0, 120)
LABEL_TEXT = (255, 255, 255)
FILL_COLOR = (64, 64, 64)
OVERLAP_SHARE = 0.25
INSET_PX = 24
PICTURE_MARGIN_PX = 64
PIXEL_TOLERANCE = 32
CHANGED_PIXELS = 20


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
    windows: tuple = ()
    missing: int = 0


@dataclass(frozen=True)
class Region:
    """The part of one window's screenshot around an action's target. `size` is the whole
    screenshot's size and `box` the part kept, in its pixels; `image` holds that part."""

    window_id: str
    size: tuple
    box: tuple
    image: object


@dataclass(frozen=True)
class Layout:
    """How add_windows placed the windows. `shift` is the combined image's top-left relative
    to the main window's image (never positive), `size` its size or None when the main image
    was left as it was, `windows` the (id, bounds) drawn, bottom first, the main window
    among them in its place, and `missing` the number of other windows left out."""

    shift: tuple
    size: tuple | None
    windows: tuple
    missing: int


def other_windows(main, rows):
    """The rows that belong in a picture of `main`, in listing order.

    Candidates are the visible, not minimized windows with an area, of the same pid and
    process instance, other than `main`. Starting from `main`, a candidate joins when it
    belongs with a window already in, and this repeats until none joins. It belongs when the
    two share at least OVERLAP_SHARE of the smaller one's area, or when its top-left corner
    lies inside that window at least INSET_PX from every edge. So a popup over a popup
    follows its parent in, a list opened inside the window comes in even when most of it
    hangs below, and observing a popup brings in the window under it. Two maximized windows
    on neighbouring monitors, which share only a few pixels of invisible border, stay apart,
    as does a window of the app that does not overlap.
    """
    if main.get("pid") is None or not has_area(main.get("bounds")):
        return []
    candidates = [
        row for row in rows
        if row.get("id") != main.get("id")
        and row.get("pid") == main["pid"]
        and row.get("process_instance") == main.get("process_instance")
        and row.get("visible", True) is not False
        and not row.get("minimized")
        and has_area(row.get("bounds"))
    ]
    joined = [main["bounds"]]
    chosen = set()
    grew = True
    while grew:
        grew = False
        for index, row in enumerate(candidates):
            if index not in chosen and any(_belongs(row["bounds"], bounds) for bounds in joined):
                chosen.add(index)
                joined.append(row["bounds"])
                grew = True
    return [row for index, row in enumerate(candidates) if index in chosen]


def stacked(main, rows):
    """The windows of a picture of `main`, top first: `main` and other_windows(main, rows)
    in the order `rows` lists them. The Windows adapter lists top-level windows in the order
    EnumWindows walks them, which is front to back. `main` goes at the bottom when `rows`
    does not list it."""
    ids = {main.get("id")} | {row.get("id") for row in other_windows(main, rows)}
    picture = [main if row.get("id") == main.get("id") else row for row in rows if row.get("id") in ids]
    if not any(row is main for row in picture):
        picture.append(main)
    return picture


def _belongs(window, joined):
    """True when `window` belongs in a picture that already holds `joined`."""
    if overlap_area(window, joined) >= OVERLAP_SHARE * min(area(window), area(joined)):
        return True
    return (joined["x"] + INSET_PX <= window["x"] <= joined["x"] + joined["width"] - INSET_PX
            and joined["y"] + INSET_PX <= window["y"] <= joined["y"] + joined["height"] - INSET_PX)


def add_windows(desktop, path, main, main_origin, stack):
    """Screenshots the other windows of `stack` and combines them with the main window's
    image at `path`, drawing the stack bottom up.

    `stack` is the picture's windows top first (see stacked); `main` goes at the bottom when
    it is not among them. `main_origin` is the screen position of the main image's top-left
    pixel. A window whose screenshot fails is left out and counted. With no other window
    nothing is read or written.
    """
    main_only = ((main["id"], dict(main["bounds"])),)
    bottom_up = list(reversed(list(stack)))
    if not any(row.get("id") == main["id"] for row in bottom_up):
        bottom_up.insert(0, main)
    others = len(bottom_up) - 1
    if not others:
        return Layout((0, 0), None, main_only, 0)
    base = _open(path) if Image is not None else None
    if base is None:
        return Layout((0, 0), None, main_only, others)
    layers, windows = [], []
    for index, row in enumerate(bottom_up):
        if row.get("id") == main["id"]:
            layers.append(((0, 0), base))
            windows.extend(main_only)
            continue
        image = _screenshot(desktop, row["id"], path.with_name(f"{path.stem}-w{index + 1}.png"))
        if image is None:
            continue
        origin = frame_origin(row["bounds"], *image.size)
        offset = (round(origin[0] - main_origin[0]), round(origin[1] - main_origin[1]))
        layers.append((offset, image))
        windows.append((row["id"], dict(row["bounds"])))
    missing = others - (len(layers) - 1)
    if len(layers) == 1:
        base.close()
        return Layout((0, 0), None, main_only, missing)
    left = min(offset[0] for offset, _ in layers)
    top = min(offset[1] for offset, _ in layers)
    right = max(offset[0] + image.size[0] for offset, image in layers)
    bottom = max(offset[1] + image.size[1] for offset, image in layers)
    canvas = Image.new("RGB", (right - left, bottom - top), FILL_COLOR)
    for offset, image in layers:
        canvas.paste(image, (offset[0] - left, offset[1] - top))
        image.close()
    canvas.save(path)
    return Layout((left, top), canvas.size, tuple(windows), missing)


def take(desktop, window, marks, out_dir, capture_id, around=None, stack=()):
    """Screenshots `window` into out_dir/<capture_id>.png. Returns a Capture, or None on failure.

    `stack` is the picture's windows top first (see stacked and add_windows); another window
    that fails is left out and counted in `missing`, while a failed `window` gives None. `around` is an
    element's screen bounds; the image is then cut to that box plus CROP_MARGIN_PX on each
    side. Mark boxes are drawn for the marks inside the image unless there are more than
    MAX_DRAWN_MARKS of them.
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
        image.close()
    main_origin = frame_origin(bounds, *size)
    layout = add_windows(desktop, path, window, main_origin, list(stack))
    rounded = _rounded(main_origin)
    origin = (rounded[0] + layout.shift[0], rounded[1] + layout.shift[1])
    size = layout.size or size
    image = _open(path)
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
        windows=layout.windows, missing=layout.missing,
    )


def to_screen(capture, x, y):
    """Maps an image pixel of `capture` to the screen, or None when it lies outside the image."""
    if not (0 <= x < capture.width and 0 <= y < capture.height):
        return None
    return (int(capture.origin[0] + x), int(capture.origin[1] + y))


def window_at(capture, x, y):
    """The id of the topmost window of `capture` whose bounds hold the screen point, or the
    capture's main window when none does."""
    for window_id, bounds in reversed(capture.windows):
        if contains(bounds, x, y):
            return window_id
    return capture.window_id


def region_box(around, origin, size):
    """The image box of `around` (screen bounds) widened by PICTURE_MARGIN_PX on every side and
    cut to an image of `size` whose top-left pixel sits at `origin`, or None when nothing is left."""
    left = max(int(round(around["x"] - origin[0])) - PICTURE_MARGIN_PX, 0)
    top = max(int(round(around["y"] - origin[1])) - PICTURE_MARGIN_PX, 0)
    right = min(int(round(around["x"] + around["width"] - origin[0])) + PICTURE_MARGIN_PX, size[0])
    bottom = min(int(round(around["y"] + around["height"] - origin[1])) + PICTURE_MARGIN_PX, size[1])
    if left >= right or top >= bottom:
        return None
    return (left, top, right, bottom)


def shoot_region(desktop, window, around, path):
    """Screenshots `window` (a row with id and bounds) into `path`, which it then removes, and
    keeps the region_box around `around`. Returns a Region, or None without Pillow, when the
    screenshot fails or the region falls outside the image."""
    if Image is None or not window.get("bounds"):
        return None
    image = _screenshot(desktop, window["id"], path)
    if image is None:
        return None
    box = region_box(around, frame_origin(window["bounds"], *image.size), image.size)
    if box is None:
        image.close()
        return None
    region = Region(window["id"], image.size, box, image.crop(box))
    image.close()
    return region


def changed_pixels(desktop, region, path):
    """Screenshots the region's window again and counts the pixels of the same box that changed
    (count_changed). None when the screenshot fails or the window's image changed size."""
    image = _screenshot(desktop, region.window_id, path)
    if image is None:
        return None
    try:
        if image.size != region.size:
            return None
        return count_changed(region.image, image.crop(region.box))
    finally:
        image.close()


def count_changed(before, after):
    """The number of pixels whose largest channel difference exceeds PIXEL_TOLERANCE."""
    difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    red, green, blue = difference.split()
    peak = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return sum(count for value, count in enumerate(peak.histogram()) if value > PIXEL_TOLERANCE)


def _screenshot(desktop, window_id, path):
    """Screenshots one window into a file it then removes; returns the image or None."""
    try:
        if not desktop.run("screenshot", "--window-id", window_id, str(path)).get("ok"):
            return None
        return _open(path)
    finally:
        path.unlink(missing_ok=True)


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
