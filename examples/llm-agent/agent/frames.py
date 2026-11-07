"""Maps physical screen bounds onto a window screenshot.

`list-windows` reports the window rectangle including the invisible resize
border, while `screenshot --window-id` captures only the visible frame. The
border is the same on the left, right and bottom and absent at the top, so
the image sits centered horizontally and flush with the bottom edge minus
that border. Both rectangles are physical pixels, so no scaling applies.
"""


def frame_origin(window_bounds, image_width, image_height):
    """Returns the screen position of the screenshot's top-left pixel."""
    border = (window_bounds["width"] - image_width) / 2
    x = window_bounds["x"] + border
    y = window_bounds["y"] + (window_bounds["height"] - image_height) - border
    return (x, y)


def to_image_rect(bounds, origin, image_size):
    """Returns the part of `bounds` that falls inside the image, or None."""
    image_width, image_height = image_size
    left = max(bounds["x"] - origin[0], 0)
    top = max(bounds["y"] - origin[1], 0)
    right = min(bounds["x"] + bounds["width"] - origin[0], image_width)
    bottom = min(bounds["y"] + bounds["height"] - origin[1], image_height)
    if right <= left or bottom <= top:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}
