"""Compares what the harness saw before and after an action, and judges an expectation.

An element is identified by (kind, role, name, description) plus its order among the
elements sharing that key, so neither a value nor a position is identity. Focus moves
are reported on their own and never count as a change: a click that only places a
caret must not look like the screen reacted.
"""

from agent.tool_schemas import EXPECTATIONS

__all__ = ["EXPECTATIONS", "diff", "met", "state"]

MAX_LISTED = 8
FOCUSED = "focused"


def state(windows, marks):
    """Freezes the window list and one window's mark map ({n: element}) for diff()."""
    return {
        "windows": [{"id": w.get("id"), "title": w.get("title")} for w in windows or []],
        "elements": [marks[n] for n in sorted(marks or {})],
    }


def diff(before, after):
    """Returns the effects object of an action: windows, added, removed, changed, focus, none."""
    before_ids = {w["id"] for w in before["windows"]}
    after_ids = {w["id"] for w in after["windows"]}
    opened = [w for w in after["windows"] if w["id"] not in before_ids]
    closed = [w for w in before["windows"] if w["id"] not in after_ids]

    old = _keyed(before["elements"])
    new = _keyed(after["elements"])
    added = [new[key] for key in new if key not in old]
    removed = [old[key] for key in old if key not in new]
    changed = []
    focus = None
    for key, element in new.items():
        previous = old.get(key)
        if previous is None:
            continue
        if (previous.get("value") or None) != (element.get("value") or None):
            changed.append(_change(element, "value", previous.get("value"), element.get("value")))
        old_states, new_states = _states(previous), _states(element)
        if old_states != new_states:
            changed.append(_change(element, "states", old_states, new_states))
        if focus is None and _focused(element) and not _focused(previous):
            focus = {"mark": element.get("mark"), "name": _label(element)}
    if focus is None:
        focus = next(
            ({"mark": e.get("mark"), "name": _label(e)} for e in added if _focused(e)),
            None,
        )
    effects = {
        "windows_opened": opened,
        "windows_closed": closed,
        "added": _listed(added),
        "removed": _listed(removed),
        "changed": changed[:MAX_LISTED],
        "focus": focus,
    }
    effects["none"] = not (opened or closed or added or removed or changed or focus)
    return effects


def met(expect, effects):
    """True when `effects` contain the kind of change `expect` names."""
    if expect not in EXPECTATIONS:
        raise ValueError(f"unknown expectation: {expect!r}")
    content = effects["added"]["count"] > 0 or effects["removed"]["count"] > 0
    if expect == "window_opened":
        return bool(effects["windows_opened"])
    if expect == "window_closed":
        return bool(effects["windows_closed"])
    if expect == "content_changed":
        return content
    if expect == "value_changed":
        return bool(effects["changed"]) or content
    return not effects["none"]


def _keyed(elements):
    counts = {}
    keyed = {}
    for element in elements:
        key = (element.get("kind"), element.get("role"), element.get("name"), element.get("description"))
        order = counts.get(key, 0)
        counts[key] = order + 1
        keyed[key + (order,)] = element
    return keyed


def _states(element):
    return sorted(s for s in element.get("states") or [] if s != FOCUSED)


def _focused(element):
    return FOCUSED in (element.get("states") or [])


def _change(element, field, before, after):
    return {"mark": element.get("mark"), "name": _label(element), "field": field,
            "before": before, "after": after}


def _listed(elements):
    return {"count": len(elements), "names": [_label(e) for e in elements[:MAX_LISTED]]}


def _label(element):
    return element.get("name") or element.get("value") or element.get("description")
