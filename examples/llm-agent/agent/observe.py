"""Turns a snapshot tree into compact text for the model."""

import json

NOISE_ACTIONS = {"ScrollTo", "SetFocus"}


def render_tree(tree, include_bounds=False):
    """Returns the rendered text and a map from ref id to element identity and bounds.

    include_bounds only decides whether the text shows bounds; the map always carries them.
    """
    lines = []
    refs = {}
    _render(tree, 0, include_bounds, lines, refs)
    return "\n".join(lines), refs


def render_marked(tree, include_bounds=False):
    """Like render_tree, but numbers every visible element and returns that numbering too.

    Each line that visible_elements would return starts with "[n] " after its indentation.
    The third value maps n to that element with "mark": n added. Numbers follow tree order
    from 1 and are only meaningful for this one observation.
    """
    lines = []
    refs = {}
    marks = {}
    _render(tree, 0, include_bounds, lines, refs, marks, tree.get("role") == "window")
    return "\n".join(lines), refs, marks


def _render(node, depth, include_bounds, lines, refs, marks=None, is_window_root=False):
    """Fills refs and marks for the harness. An element with a native id (such as a UIA
    AutomationId) also carries it as "native_id", for the action guard only; the rendered text
    never shows it."""
    native_id = _native_id(node)
    if node.get("ref_id"):
        refs[node["ref_id"]] = {
            "role": node.get("role"),
            "name": node.get("name"),
            "description": node.get("description"),
            "bounds": node.get("bounds"),
        }
        if native_id is not None:
            refs[node["ref_id"]]["native_id"] = native_id
    prefix = ""
    if marks is not None and _is_element(node, is_window_root):
        number = len(marks) + 1
        marks[number] = dict(_element(node), mark=number)
        if native_id is not None:
            marks[number]["native_id"] = native_id
        prefix = f"[{number}] "
    if _is_informative(node):
        lines.append("  " * depth + prefix + _line(node, include_bounds))
        depth += 1
    for child in node.get("children", []):
        _render(child, depth, include_bounds, lines, refs, marks)


def _native_id(node):
    """The snapshot's native id as {"kind", "value"}, or None when it has no value."""
    native = node.get("native_id") or {}
    if not native.get("value"):
        return None
    return {"kind": native.get("kind"), "value": native["value"]}


def visible_elements(tree):
    """Returns, in tree order, the elements the rendered text shows as their own line.

    A node with a ref is a "control". A node without one is "text" when it has a name,
    description or value. The window at the root is neither.
    """
    elements = []
    _collect(tree, tree.get("role") == "window", elements)
    return elements


def _collect(node, is_window_root, elements):
    if _is_element(node, is_window_root):
        elements.append(_element(node))
    for child in node.get("children", []):
        _collect(child, False, elements)


def _is_element(node, is_window_root):
    has_text = any(node.get(key) for key in ("name", "description", "value"))
    return bool(node.get("ref_id") or (has_text and not is_window_root))


def _element(node):
    return {
        "kind": "control" if node.get("ref_id") else "text",
        "ref": node.get("ref_id"),
        "role": node.get("role"),
        "name": node.get("name"),
        "description": node.get("description"),
        "states": list(node.get("states") or []),
        "value": node.get("value"),
        "bounds": node.get("bounds"),
    }


def _is_informative(node):
    keys = ("name", "ref_id", "value", "description", "subtree_truncated")
    return any(node.get(key) for key in keys)


def _line(node, include_bounds):
    parts = [node.get("role") or "unknown"]
    if node.get("name"):
        parts.append(_quoted(node["name"]))
    if node.get("ref_id"):
        parts.append(node["ref_id"])
    if node.get("description"):
        parts.append(f"desc={_quoted(node['description'])}")
    if node.get("value"):
        parts.append(f"value={_quoted(node['value'])}")
    if node.get("states"):
        parts.append("states=" + ",".join(node["states"]))
    actions = [a for a in node.get("available_actions", []) if a not in NOISE_ACTIONS]
    if actions:
        parts.append("actions=" + ",".join(actions))
    if node.get("children_count"):
        parts.append(f"children={node['children_count']}")
    if node.get("subtree_truncated"):
        parts.append("truncated")
    if include_bounds and node.get("bounds"):
        parts.append("bounds=" + _bounds(node["bounds"]))
    return " ".join(parts)


def _quoted(text):
    return json.dumps(str(text), ensure_ascii=False)


def _bounds(bounds):
    keys = ("x", "y", "width", "height")
    return ",".join(str(round(bounds[key])) for key in keys)
