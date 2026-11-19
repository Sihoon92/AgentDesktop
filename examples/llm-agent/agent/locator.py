"""Tells one element apart from the others in its window without coordinates.

The functions take the element dicts of one observation (each with `mark` and `scope`)
and nothing else. A target is written as its role, name and description, and when those
are shared, as the n-th such element after a uniquely named neighbour in the same row or
column. Position comes from `rect` (a recorded picture) or `bounds` (the live screen);
rows and columns are judged by how much two boxes overlap relative to their own size,
so the same locator finds the same element at another scale or window position.

With use_ids (a system whose ids stay the same when it restarts), the element's native id
and, when it sits in one, its area (agent.identity) are added to the role, name and
description: an id never stands in for a name. A locator without "id" or "in" does not look
at ids, so workflows saved before ids were written find what they found.

`find` never guesses: it returns every element that fits, and choosing is the caller's.
"""

from agent.identity import container_key, native_value

ROW = "same_row"
COLUMN = "same_column"
WINDOW_ROLE = "window"


class LocatorError(ValueError):
    pass


def in_scope(elements, scope_mark):
    """The elements whose nearest window ancestor is `scope_mark`, without any window element."""
    return [e for e in elements if e.get("role") != WINDOW_ROLE and e.get("scope") == scope_mark]


def window_elements(elements):
    return [e for e in elements if e.get("role") == WINDOW_ROLE]


def window_handle(elements, window):
    """Names a window element by its name and its order among windows of the same name."""
    name = _text(window.get("name"))
    same = [w for w in window_elements(elements) if _text(w.get("name")) == name]
    for index, candidate in enumerate(same, 1):
        if _same_element(candidate, window):
            return {"name": name, "nth": index}
    raise LocatorError("the window is not among the elements")


def resolve_handle(elements, handle):
    name = _text(handle.get("name"))
    same = [w for w in window_elements(elements) if _text(w.get("name")) == name]
    nth = handle.get("nth")
    return same[nth - 1] if isinstance(nth, int) and 1 <= nth <= len(same) else None


def new_windows(before, after):
    """The window elements of `after` whose (name, order among that name) was not in `before`."""
    seen = {_window_key(before, window) for window in window_elements(before)}
    return [window for window in window_elements(after) if _window_key(after, window) not in seen]


def describe(scoped, mark, avoid=(), use_ids=False):
    """The locator, without its scope, that finds exactly the element numbered `mark`.

    Names in `avoid` are never used as the anchor: text that changes from run to run,
    such as the current value beside a label, would not be there to find next time.
    With use_ids, the element's id and area are part of what it is written as, except an area
    named by a name in `avoid`: it would not be there under another value.
    """
    target = next((e for e in scoped if e.get("mark") == mark), None)
    if target is None:
        raise LocatorError("the target is not in this scope")
    avoided = {_text(name) for name in avoid} - {None}
    locator = _identity(target, use_ids, avoided)
    if _only(find(scoped, locator), target):
        return locator
    for near in _anchors(scoped, target, locator, avoided):
        anchored = {**locator, "near": near}
        if _only(find(scoped, anchored), target):
            return anchored
    raise LocatorError("the target cannot be told apart in its scope")


def find(scoped, locator, name=None):
    """Every element of `scoped` that fits `locator`. `name` replaces the locator's name. An "id"
    must be the element's native id letter for letter, and an "in" the element's area."""
    wanted = _text(name if name is not None else locator.get("name"))
    matches = [
        e for e in scoped
        if e.get("role") == locator.get("role")
        and _text(e.get("name")) == wanted
        and _text(e.get("description")) == _text(locator.get("description"))
        and ("id" not in locator or native_value(e) == locator["id"])
        and ("in" not in locator or (container_key(e.get("in")) is not None
                                     and container_key(e.get("in")) == container_key(locator["in"])))
    ]
    near = locator.get("near")
    if not near:
        return matches
    found = []
    anchor_name = _text(near.get("name"))
    for anchor in (e for e in scoped if anchor_name is not None and _text(e.get("name")) == anchor_name):
        for element in _nth_after(anchor, matches, near.get("relation"), near.get("nth")):
            if not any(element is kept for kept in found):
                found.append(element)
    return found


def overlaps(first, second, axis):
    """True when two boxes' spans along `axis` ("x" or "y") share at least half of the shorter
    span: the same column for "x", the same row for "y"."""
    return _overlaps(first, second, axis)


def box(element):
    """The element's `rect` (recorded) or `bounds` (live), or None when it has no area."""
    return _box(element)


def _anchors(scoped, target, identity, avoided):
    """Row anchors first, then column anchors, each nearest first. The n-th is counted among
    the elements that fit `identity`, as find counts it."""
    box = _box(target)
    if box is None:
        return []
    names = [_text(e.get("name")) for e in scoped]
    unique = [
        e for e, name in zip(scoped, names)
        if name is not None and name not in avoided and names.count(name) == 1 and e is not target
        and _box(e) is not None
    ]
    matches = find(scoped, identity)
    nears = []
    for relation in (ROW, COLUMN):
        axis = _axis(relation)
        placed = [a for a in unique if _before(_box(a), box, axis)]
        placed.sort(key=lambda a: _center(box, axis) - _center(_box(a), axis))
        for anchor in placed:
            line = _line_after(anchor, matches, relation)
            nth = next((i for i, e in enumerate(line, 1) if e is target), None)
            if nth is not None:
                nears.append({"name": _text(anchor.get("name")), "relation": relation, "nth": nth})
    return nears


def _identity(element, use_ids=False, avoided=frozenset()):
    locator = {"role": element.get("role")}
    for key in ("name", "description"):
        if _text(element.get(key)) is not None:
            locator[key] = _text(element.get(key))
    native = native_value(element) if use_ids else None
    if native is not None:
        locator["id"] = native
        area = element.get("in")
        if container_key(area) is not None and ("id" in area or _text(area.get("name")) not in avoided):
            locator["in"] = {key: area[key] for key in ("role", "id", "name") if key in area}
    return locator


def _nth_after(anchor, matches, relation, nth):
    if relation not in (ROW, COLUMN) or not isinstance(nth, int) or nth < 1:
        return []
    line = _line_after(anchor, matches, relation)
    if len(line) < nth:
        return []
    axis = _axis(relation)
    chosen = _center(_box(line[nth - 1]), axis)
    return [e for e in line if _center(_box(e), axis) == chosen]


def _line_after(anchor, matches, relation):
    """The matches in the anchor's row (or column) that come after it, in reading order."""
    anchor_box = _box(anchor)
    if anchor_box is None:
        return []
    axis = _axis(relation)
    line = [
        e for e in matches
        if _box(e) is not None and _overlaps(anchor_box, _box(e), _cross(axis)) and _before(anchor_box, _box(e), axis)
    ]
    line.sort(key=lambda e: _center(_box(e), axis))
    return line


def _axis(relation):
    return "x" if relation == ROW else "y"


def _cross(axis):
    return "y" if axis == "x" else "x"


def _size_key(axis):
    return "width" if axis == "x" else "height"


def _before(first, second, axis):
    """True when `first` ends no later than the middle of `second` along `axis`."""
    return first[axis] + first[_size_key(axis)] <= _center(second, axis)


def _overlaps(first, second, axis):
    """True when the two spans along `axis` share at least half of the shorter span."""
    size = _size_key(axis)
    shorter = min(first[size], second[size])
    shared = min(first[axis] + first[size], second[axis] + second[size]) - max(first[axis], second[axis])
    return shorter > 0 and shared >= shorter / 2


def _center(box, axis):
    return box[axis] + box[_size_key(axis)] / 2


def _box(element):
    box = element.get("rect") or element.get("bounds")
    if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
        return None
    return box


def _text(value):
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _only(found, target):
    return len(found) == 1 and found[0] is target


def _same_element(first, second):
    if first.get("mark") is not None and second.get("mark") is not None:
        return first.get("mark") == second.get("mark")
    return first is second


def _window_key(elements, window):
    handle = window_handle(elements, window)
    return handle["name"], handle["nth"]
