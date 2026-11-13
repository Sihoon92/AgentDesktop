"""Finds where row labels and column labels of a table cross. Text matching and geometry only.

The model decides what the user's words mean (for example that a group name alone
means its subtotal row) and passes the label names; this module never interprets them.
"""

import re

from agent.geometry import has_area, smallest_containing

MAX_CANDIDATES = 20
MAX_CONTEXT = 6
EDGE_TOLERANCE_PX = 2
NUMERIC = re.compile(r"^[\d\s.,%+\-()]+$")


def find_cells(elements, rows, columns):
    """Returns the crossing points of `rows` (names on one line) and `columns` (stacked headers).

    `elements` are the values of a mark map: dicts with mark, name, value and bounds.
    Raises ValueError when either name list is empty.
    """
    rows = [_normalized(name) for name in rows or []]
    columns = [_normalized(name) for name in columns or []]
    if not rows or not columns:
        raise ValueError("rows and columns each need at least one name")
    labelled = [e for e in elements if _text(e) and has_area(e.get("bounds"))]
    if not labelled:
        return _none("no_text_elements")
    by_name = {}
    for name in rows + columns:
        by_name[name] = [e for e in labelled if _text(e) == name]
    for name in rows:
        if not by_name[name]:
            return _none(f"row_label_not_found: {name}")
    for name in columns:
        if not by_name[name]:
            return _none(f"column_label_not_found: {name}")

    row_sets = _aligned_sets(rows, by_name, _vertical)
    column_sets = _aligned_sets(columns, {n: _narrowest_only(by_name[n]) for n in columns}, _horizontal)
    candidates = {}
    for row_set in row_sets:
        band = _band(row_set, _vertical)
        for column_set in column_sets:
            if any(_bottom(e) > band[0] + EDGE_TOLERANCE_PX for e in column_set):
                continue
            labels = {id(e) for e in row_set + column_set}
            for candidate in _cells(elements, row_set, column_set, band, labels):
                candidates.setdefault(tuple(candidate["point"]), candidate)
    if not candidates:
        return _none("labels_not_aligned")
    ordered = sorted(candidates.values(), key=lambda c: (c["point"][1], c["point"][0]))
    return {"candidates": ordered[:MAX_CANDIDATES], "reason": None}


def _cells(elements, row_set, column_set, band, labels):
    column_band = _band(column_set, _horizontal)
    x = _middle(column_band)
    others = [e for e in elements if id(e) not in labels and has_area(e.get("bounds"))]
    sub_rows = _sub_rows(others, x, band)
    for top, bottom in sub_rows or [band]:
        point = [x, _middle((top, bottom))]
        cell = smallest_containing(others, *point)
        yield {
            "point": point,
            "mark": cell.get("mark") if cell else None,
            "value": _display(cell) if cell else None,
            "bounds": cell.get("bounds") if cell else None,
            "row_context": _row_context(elements, (top, bottom), x),
            "row_marks": [e.get("mark") for e in row_set],
            "column_marks": [e.get("mark") for e in column_set],
        }


def _sub_rows(others, x, band):
    """Rows inside `band` at column `x`, when the band spans several (a merged row label)."""
    ranges = []
    for element in others:
        left, right = _horizontal(element)
        top, bottom = _vertical(element)
        inside = band[0] - EDGE_TOLERANCE_PX <= top and bottom <= band[1] + EDGE_TOLERANCE_PX
        if left <= x < right and inside:
            ranges.append((top, bottom))
    kept = []
    for top, bottom in sorted(set(ranges), key=lambda r: (r[0], -(r[1] - r[0]))):
        if kept and top >= kept[-1][0] and bottom <= kept[-1][1]:
            continue
        kept.append((top, bottom))
    return kept if len(kept) > 1 else []


def _row_context(elements, band, x):
    names = []
    for element in sorted(elements, key=lambda e: _horizontal(e)[0] if e.get("bounds") else 0):
        text = _text(element)
        if not text or NUMERIC.match(text) or not has_area(element.get("bounds")):
            continue
        if _overlaps(_vertical(element), band) and _horizontal(element)[1] <= x:
            names.append(text)
    return names[:MAX_CONTEXT]


def _aligned_sets(names, by_name, axis):
    """Every choice of one distinct element per name whose ranges on `axis` all overlap."""
    sets = [[]]
    for name in names:
        grown = []
        for chosen in sets:
            for element in by_name[name]:
                if any(element is other for other in chosen):
                    continue
                if all(_overlaps(axis(element), axis(other)) for other in chosen):
                    grown.append(chosen + [element])
        sets = grown
    return sets


def _narrowest_only(matches):
    """Drops a match when a strictly narrower match of the same name overlaps it."""
    kept = []
    for element in matches:
        width = _span(_horizontal(element))
        narrower = any(
            _span(_horizontal(other)) < width and _overlaps(_horizontal(other), _horizontal(element))
            for other in matches
        )
        if not narrower:
            kept.append(element)
    return kept


def _band(chosen, axis):
    return min((axis(e) for e in chosen), key=_span)


def _overlaps(first, second):
    shared = min(first[1], second[1]) - max(first[0], second[0])
    return shared > 0 and shared >= min(_span(first), _span(second)) / 2


def _horizontal(element):
    bounds = element["bounds"]
    return (bounds["x"], bounds["x"] + bounds["width"])


def _vertical(element):
    bounds = element["bounds"]
    return (bounds["y"], bounds["y"] + bounds["height"])


def _bottom(element):
    return _vertical(element)[1]


def _span(extent):
    return extent[1] - extent[0]


def _middle(extent):
    return int((extent[0] + extent[1]) // 2)


def _text(element):
    return _normalized(element.get("name") or element.get("value") or "")


def _display(element):
    return element.get("value") or element.get("name")


def _normalized(text):
    return " ".join(str(text).split())


def _none(reason):
    return {"candidates": [], "reason": reason}

