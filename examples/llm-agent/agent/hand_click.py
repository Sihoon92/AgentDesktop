"""A ref the app cannot take a click through is pressed by hand.

On Windows, `click` delivers only through the element's Invoke pattern or its Legacy default
action, and the CLI lists Click among an element's actions exactly when it has one of them.
Some apps give elements interactive roles (so they carry refs) but advertise neither. For such
a ref the harness presses the element's center physically, as click_part would, instead of
sending the CLI a click that could only fail. A ref that does advertise Click keeps the CLI's
semantic click and its answer, failures included: pressing a control the CLI refused (disabled,
covered) by hand would be the wrong fix.
"""

CLICK = "Click"
NO_CLICK_ACTION = "NO_CLICK_ACTION"
NO_PHYSICAL_HINT = ("This element advertises no click the app takes through accessibility, so it "
                    "can only be pressed by hand with click_part, which this task does not allow "
                    "or the latest observation cannot place. Observe again, or ask the user.")


def refs_without_click(tree: dict) -> frozenset:
    """Refs whose element does not advertise Click. An empty action list is left out of the
    JSON, which reads the same here: no Click."""
    found = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.get("ref_id") and CLICK not in (node.get("available_actions") or ()):
            found.add(node["ref_id"])
        stack.extend(node.get("children") or ())
    return frozenset(found)


def mark_of(marks: dict, ref: str) -> int | None:
    """The mark the latest observation gave the element that carries `ref`."""
    return next((number for number, element in marks.items() if element.get("ref") == ref), None)
