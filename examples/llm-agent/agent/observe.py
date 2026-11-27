"""Turns a snapshot tree into compact text for the model.

A system profile (agent.profile) may register names for elements that the app gives only an id,
such as a menu drawn as pictures. Matching goes through agent.identity: role, id and, when the
entry writes one, the area the element sits in. Without a profile only the rules that need none
apply: an id-only leaf is an element, and an unnamed element's line shows its id.
"""

import json
from collections import Counter

from agent import identity
from agent.profile import entry_of
from agent.structure_view import render_summary

NOISE_ACTIONS = {"ScrollTo", "SetFocus"}


def render_tree(tree, include_bounds=False, profile=None):
    """Returns the rendered text and a map from ref id to element identity and bounds.

    include_bounds only decides whether the text shows bounds; the map always carries them.
    """
    text, refs, _ = _render(tree, include_bounds, numbered=False, profile=profile, structure=None)
    return text, refs


def render_marked(tree, include_bounds=False, profile=None, structure=None):
    """Like render_tree, but numbers every visible element and returns that numbering too.

    Each line that visible_elements would return starts with "[n] " after its indentation.
    The third value maps n to that element with "mark": n and "scope" added (see
    visible_elements). Numbers follow tree order from 1 and are only meaningful for this one
    observation.

    structure, when given, is this tree's agent.structure.analyze result: its summary leads the
    text and the cells of its tables get no line of their own. Numbers and the returned marks
    stay the same; a hidden cell is still in them.
    """
    return _render(tree, include_bounds, numbered=True, profile=profile, structure=structure)


def numbered_nodes(tree, profile=None):
    """Every node in tree order as (node, number, area): number is its render_marked number, or
    None for a node that is no element, and area is the one it sits in (agent.identity), or None.
    The nodes are the tree's own dicts."""
    return [(item.node, item.mark, item.container) for item in _items(tree, profile)]


def _render(tree, include_bounds, numbered, profile, structure):
    """Fills refs and marks for the harness. A ref or element with a native id (such as a UIA
    AutomationId) also carries it as "native_id"; the text shows its value only on an element
    without a name."""
    lines = []
    refs = {}
    marks = {}
    hidden = structure.cell_marks if structure is not None else frozenset()
    for item in _items(tree, profile):
        node, mark = item.node, item.mark
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
        if numbered and mark is not None:
            marks[mark] = _numbered_element(item)
            prefix = f"[{mark}] "
        if item.has_line and (mark is None or mark not in hidden):
            lines.append("  " * item.depth + prefix + _line(item, include_bounds))
    text = "\n".join(lines)
    if structure is not None:
        summary = render_summary(structure)
        text = f"{summary}\n{text}" if summary else text
    return text, refs, marks


class _Item:
    """One node as the walk saw it: its line's indentation, its element number (None for no
    element), its scope, the area it sits in, and the registered name it shows (or None)."""

    __slots__ = ("node", "depth", "mark", "scope", "container", "alias", "has_line")

    def __init__(self, node, depth, mark, scope, container, has_line):
        self.node = node
        self.depth = depth
        self.mark = mark
        self.scope = scope
        self.container = container
        self.alias = None
        self.has_line = has_line


def _items(tree, profile):
    """The one numbering that render_marked and visible_elements share, with aliases settled.

    An alias is known only once the whole tree is walked: it shows only when exactly one
    element of this observation fits its profile entry. Entries are counted, not names, so two
    entries that register the same name never hide each other."""
    walked = list(_walk(tree, profile))
    fitted = Counter(id(entry) for item, entry in walked if item.mark is not None and entry is not None)
    for item, entry in walked:
        if item.mark is not None and entry is not None and fitted[id(entry)] == 1:
            item.alias = entry.get("name")
    return [item for item, _ in walked]


def _walk(tree, profile):
    """Yields (item, entry) for every node in tree order; entry is the profile entry the node
    fits, or None.

    depth is the indentation of the node's line; a node without a line of its own does not
    indent its children. scope is the mark of the nearest ancestor element whose role is window,
    or None. The observed window at the root is never a scope, even when a ref makes it an
    element, and never an area. A window that only an id would make an element is no element."""
    count = 0
    root_is_window = tree.get("role") == "window"
    stack: list[tuple[dict, int, int | None, bool, dict | None]] = [(tree, 0, None, root_is_window, None)]
    while stack:
        node, depth, scope, is_window_root, container = stack.pop()
        native = identity.native_value(node)
        entry = None
        if profile is not None and native is not None:
            entry = entry_of(profile, node.get("role"), native, container)
        mark = None
        if _is_element(node, is_window_root, native, entry):
            count += 1
            mark = count
        has_line = mark is not None or _is_informative(node)
        yield _Item(node, depth, mark, scope, container, has_line), entry
        child_depth = depth + 1 if has_line else depth
        opens_scope = mark is not None and node.get("role") == "window" and not is_window_root
        child_scope = mark if opens_scope else scope
        inner = identity.child_container(node, container, is_window_root)
        for child in reversed(node.get("children", [])):
            stack.append((child, child_depth, child_scope, False, inner))


def _native_id(node):
    """The snapshot's native id as {"kind", "value"}, or None when it has no value."""
    value = identity.native_value(node)
    if value is None:
        return None
    native = node.get("native_id")
    return {"kind": native.get("kind") if isinstance(native, dict) else None, "value": value}


def visible_elements(tree, profile=None):
    """Returns, in tree order, the elements the rendered text shows as their own line.

    A node with a ref is a "control". A node without one is "text" when it has a name,
    description or value, and "id" when it has none of these but a native id, is not a window,
    and is a leaf or fits an entry of the profile. The window at the root is none of these,
    unless it has a ref. Each element carries "mark", its render_marked number, and "scope",
    the mark of the nearest enclosing window element below the root, or None. An element with
    a native id also carries "native_id" and, when it sits in one, "in", its area; "alias" is
    its registered name when exactly one element fits that entry.
    """
    return [_numbered_element(item) for item in _items(tree, profile) if item.mark is not None]


def _numbered_element(item):
    element = dict(_element(item.node), mark=item.mark, scope=item.scope)
    native_id = _native_id(item.node)
    if native_id is not None:
        element["native_id"] = native_id
        if item.container is not None:
            element["in"] = dict(item.container)
    if item.alias:
        element["alias"] = item.alias
    return element


def _has_text(node):
    return any(node.get(key) for key in ("name", "description", "value"))


def _is_element(node, is_window_root, native, entry):
    if node.get("ref_id") or (_has_text(node) and not is_window_root):
        return True
    if native is None or _has_text(node) or node.get("role") == "window":
        return False
    return identity.is_leaf(node) or entry is not None


def _element(node):
    if node.get("ref_id"):
        kind = "control"
    elif _has_text(node):
        kind = "text"
    else:
        kind = "id"
    return {
        "kind": kind,
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


def _line(item, include_bounds):
    node = item.node
    parts = [node.get("role") or "unknown"]
    if node.get("name"):
        parts.append(_quoted(node["name"]))
    if node.get("ref_id"):
        parts.append(node["ref_id"])
    native = identity.native_value(node)
    if item.mark is not None and native is not None and not node.get("name"):
        parts.append(f"id={_quoted(native)}")
    if item.alias:
        parts.append(f"alias={_quoted(item.alias)}")
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
