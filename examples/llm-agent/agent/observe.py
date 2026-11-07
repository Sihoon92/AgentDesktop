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


def _render(node, depth, include_bounds, lines, refs):
    if node.get("ref_id"):
        refs[node["ref_id"]] = {
            "role": node.get("role"),
            "name": node.get("name"),
            "description": node.get("description"),
            "bounds": node.get("bounds"),
        }
    if _is_informative(node):
        lines.append("  " * depth + _line(node, include_bounds))
        depth += 1
    for child in node.get("children", []):
        _render(child, depth, include_bounds, lines, refs)


def visible_elements(tree):
    """Returns, in tree order, the elements the rendered text shows as their own line.

    A node with a ref is a "control". A node without one is "text" when it has a name,
    description or value. The window at the root is neither.
    """
    elements = []
    _collect(tree, tree.get("role") == "window", elements)
    return elements


def _collect(node, is_window_root, elements):
    has_text = any(node.get(key) for key in ("name", "description", "value"))
    if node.get("ref_id") or (has_text and not is_window_root):
        elements.append(
            {
                "kind": "control" if node.get("ref_id") else "text",
                "ref": node.get("ref_id"),
                "role": node.get("role"),
                "name": node.get("name"),
                "description": node.get("description"),
                "states": list(node.get("states") or []),
                "value": node.get("value"),
                "bounds": node.get("bounds"),
            }
        )
    for child in node.get("children", []):
        _collect(child, False, elements)


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
