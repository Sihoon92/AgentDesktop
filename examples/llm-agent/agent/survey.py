"""Counts what kind of elements a snapshot shows, without keeping any of their text.

The counts say how much of a window the harness can reach by name, how much only by a
capture number or a point, and how much is drawn with nothing in the accessibility tree.
They look only at each node's ref_id, name, native_id, children and bounds, never at a
role or an application, so the same rules hold on any system.

The root is the observed window itself: it counts toward nodes and depth but is never
classified. A node with children_count but no children was cut by the observation depth;
it has children. A position means bounds with a positive width and height.
"""

import hashlib

from agent.geometry import has_area

KIND_KEYS = ("named_ref", "unnamed_ref", "unnamed_ref_with_id", "text", "blank", "id_only")
ID_KEY_LENGTH = 16


def kinds(tree):
    """Returns the element counts of one snapshot tree.

    named_ref: ref and name. unnamed_ref: ref without a name. unnamed_ref_with_id: the
    unnamed refs that carry a native id. text: a name and a position but no ref. blank: a
    position and nothing else, no name, ref or children; a candidate drawn-only area.
    id_only: no ref and no name, but a native id and a position, with or without children; the
    nodes a system profile can name. It is counted besides the others, so an id-only leaf is
    also blank. nodes: every node. depth: levels from the root, which is level 1.
    """
    found: dict[str, int] = {key: 0 for key in (*KIND_KEYS, "nodes", "depth")}
    stack = [(tree, 1, True)]
    while stack:
        node, level, is_root = stack.pop()
        found["nodes"] += 1
        found["depth"] = max(found["depth"], level)
        if not is_root:
            kind = _kind(node)
            if kind:
                found[kind] += 1
            if kind == "unnamed_ref" and _native_id(node):
                found["unnamed_ref_with_id"] += 1
            if _id_only(node):
                found["id_only"] += 1
        stack.extend((child, level + 1, False) for child in node.get("children") or [])
    return found


def id_keys(tree):
    """Returns one key per distinct (role, native id kind, native id value) in the tree.

    Each key is the first 16 hex characters of sha256(role + "\\n" + kind + "\\n" + value),
    so two snapshots can be compared without either holding the ids themselves.
    """
    keys = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        native = _native_id(node)
        if native:
            text = f"{node.get('role') or ''}\n{native[0]}\n{native[1]}"
            keys.add(hashlib.sha256(text.encode("utf-8")).hexdigest()[:ID_KEY_LENGTH])
        stack.extend(node.get("children") or [])
    return keys


def _id_only(node):
    named = bool(str(node.get("name") or "").strip())
    return not node.get("ref_id") and not named and bool(_native_id(node)) and has_area(node.get("bounds"))


def _kind(node):
    named = bool(str(node.get("name") or "").strip())
    if node.get("ref_id"):
        return "named_ref" if named else "unnamed_ref"
    if not has_area(node.get("bounds")):
        return None
    if named:
        return "text"
    if not node.get("children") and not node.get("children_count"):
        return "blank"
    return None


def _native_id(node):
    native = node.get("native_id")
    if isinstance(native, dict):
        kind, value = native.get("kind") or "", native.get("value")
    elif isinstance(native, str):
        kind, value = "", native
    else:
        return None
    if value is None or str(value) == "":
        return None
    return str(kind), str(value)
