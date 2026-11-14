"""Screen points inside an element's bounds. Knows nothing about any application.

An end part sits a little inside the edge. The inset is half the short side, but at
most a quarter of the side it runs along, so the right end of a wide box is the
centre of the square at its right edge: where a dropdown arrow usually sits.
"""

import math

PARTS = ("center", "left", "right", "top", "bottom")


def part_point(bounds, part):
    """Returns the integer screen point for `part` of `bounds`.

    Raises ValueError for an unknown part or bounds with no area.
    """
    if part not in PARTS:
        raise ValueError(f"unknown part: {part!r}")
    if not has_area(bounds):
        raise ValueError("element has no area")
    x, y = bounds["x"], bounds["y"]
    width, height = bounds["width"], bounds["height"]
    short = min(width, height)
    inset_x = min(short / 2, width / 4)
    inset_y = min(short / 2, height / 4)
    center_x = x + width / 2
    center_y = y + height / 2
    points = {
        "center": (center_x, center_y),
        "left": (x + inset_x, center_y),
        "right": (x + width - inset_x, center_y),
        "top": (center_x, y + inset_y),
        "bottom": (center_x, y + height - inset_y),
    }
    px, py = points[part]
    return (_round(px), _round(py))


def has_area(bounds):
    return bool(bounds) and bounds.get("width", 0) > 0 and bounds.get("height", 0) > 0


def contains(bounds, x, y):
    """True when (x, y) lies inside bounds, counting the left and top edges only."""
    if not has_area(bounds):
        return False
    return (
        bounds["x"] <= x < bounds["x"] + bounds["width"]
        and bounds["y"] <= y < bounds["y"] + bounds["height"]
    )


def overlap_area(first, second):
    """The area two bounds share, or 0 when either has no area or they do not meet."""
    if not has_area(first) or not has_area(second):
        return 0
    width = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
    height = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
    return width * height if width > 0 and height > 0 else 0


def area(bounds):
    """The area of bounds, or 0 when it has none."""
    return _area(bounds) if has_area(bounds) else 0


def smallest_containing(elements, x, y):
    """Returns the element whose bounds contain (x, y) with the smallest area, or None."""
    best = None
    for element in elements:
        bounds = element.get("bounds")
        if not contains(bounds, x, y):
            continue
        if best is None or _area(bounds) < _area(best["bounds"]):
            best = element
    return best


def _area(bounds):
    return bounds["width"] * bounds["height"]


def _round(value):
    return int(math.floor(value + 0.5))
