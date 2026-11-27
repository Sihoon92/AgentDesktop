"""Draws a snapshot's element boxes over the window picture taken at the same moment.

Each element is outlined in the color of its kind, with the same rules `agent.survey.kinds`
counts by, so a person can see which parts of a window the harness reaches by name, only by
number, or not at all. Boxes carry no text; a legend in the lower right corner says what each
color means. The picture is a company-PC-only file.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from agent.frames import to_image_rect
from agent.geometry import has_area
from agent.survey import is_id_only, node_kind

LEGEND = (("named_ref", "파랑", (0, 120, 255)), ("unnamed_ref", "보라", (160, 0, 200)),
          ("text", "초록", (0, 170, 0)), ("blank", "빨강", (230, 0, 0)), ("id_only", "주황", (255, 140, 0)))
MEANING = {"named_ref": "이름 있는 ref", "unnamed_ref": "이름 없는 ref", "text": "글자만(ref 없음)",
           "blank": "아무것도 없는 칸", "id_only": "ID만 있음"}
COLORS = {kind: color for kind, _name, color in LEGEND}
LINE = 2
LEGEND_FONT_PX = 14
LEGEND_MARGIN = 8


def overlay_boxes(tree: dict, origin: tuple[int, int], size: tuple[int, int]) -> list[tuple[str, dict]]:
    """(kind, image rectangle) of every classified element below the root that falls in the
    picture. A ref without an area is counted by `kinds` but cannot be drawn. An id-only leaf
    yields both its blank box and its id-only box, as `kinds` counts it."""
    boxes = []
    stack = list(reversed(tree.get("children") or []))
    while stack:
        node = stack.pop()
        bounds = node.get("bounds")
        found = [kind for kind in (node_kind(node), "id_only" if is_id_only(node) else None) if kind]
        if found and has_area(bounds):
            rect = to_image_rect(bounds, origin, size)
            if rect is not None:
                boxes.extend((kind, rect) for kind in found)
        stack.extend(reversed(node.get("children") or []))
    return boxes


def _font():
    for name in ("malgun.ttf", "gulim.ttc", "arial.ttf"):
        try:
            return ImageFont.truetype(name, LEGEND_FONT_PX)
        except OSError:
            continue
    return ImageFont.load_default()


def _legend(draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
    font = _font()
    lines = [(color, f"{name} = {MEANING[kind]}") for kind, name, color in LEGEND]
    row = LEGEND_FONT_PX + 6
    width = max(int(draw.textlength(text, font=font)) for _color, text in lines) + row + 3 * LEGEND_MARGIN
    height = row * len(lines) + 2 * LEGEND_MARGIN
    left, top = max(0, size[0] - width), max(0, size[1] - height)
    draw.rectangle((left, top, size[0] - 1, size[1] - 1), fill=(255, 255, 255), outline=(0, 0, 0))
    for index, (color, text) in enumerate(lines):
        y = top + LEGEND_MARGIN + index * row
        draw.rectangle((left + LEGEND_MARGIN, y, left + LEGEND_MARGIN + LEGEND_FONT_PX, y + LEGEND_FONT_PX), fill=color)
        draw.text((left + 2 * LEGEND_MARGIN + LEGEND_FONT_PX, y), text, fill=(0, 0, 0), font=font)


def draw_overlay(image: Image.Image, origin: tuple[int, int], tree: dict, out: Path) -> int:
    """Saves `image` with the boxes of `tree` to `out` and returns how many boxes it drew.
    Node bounds are screen coordinates; `origin` is the screen position of the image's top-left pixel."""
    picture = image.convert("RGB")
    draw = ImageDraw.Draw(picture)
    boxes = overlay_boxes(tree, origin, picture.size)
    for kind, rect in boxes:
        inset = LINE if kind == "id_only" else 0
        right = rect["x"] + rect["width"] - 1 - inset
        bottom = rect["y"] + rect["height"] - 1 - inset
        left, top = rect["x"] + inset, rect["y"] + inset
        if right > left and bottom > top:
            draw.rectangle((left, top, right, bottom), outline=COLORS[kind], width=LINE)
    _legend(draw, picture.size)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    picture.save(out)
    return len(boxes)
