"""The one rule that tells a snapshot node by its role, its native id and the area it sits in.

A native id (Windows' AutomationId) is never a key on its own: an app may give the same id to
elements on other screens. It is always read with the node's role and, when written, with the
node's area: its nearest ancestor that has an id or a name, as {"role", "id"} or {"role",
"name"}. The window at a tree's root is never an area, as it is never an element; a tree whose
root is not a window (a part read with --root) finds areas inside that part only, root included.

The ready wait, the observation, the workflow locator and the registration screen all match
through here, so they cannot drift apart. Every function takes snapshot nodes (element dicts
carry native_id in the same shape) and changes nothing. This module imports nothing from the
agent: agent.observe uses it.
"""


def native_value(node):
    """node["native_id"] as text: the "value" of {"kind", "value"}, or the text itself. None when
    there is none or it is empty. A number is read as its text."""
    if not isinstance(node, dict):
        return None
    native = node.get("native_id")
    value = native.get("value") if isinstance(native, dict) else native
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value:
        return None
    return value


def is_leaf(node):
    """Whether the node has no children, neither listed nor only counted."""
    return not node.get("children") and not node.get("children_count")


def as_container(node):
    """The node as the area of what it holds: {"role", "id"} when it has an id, else {"role",
    "name"} with the name's outer spaces dropped, else None."""
    native = native_value(node)
    if native is not None:
        return {"role": node.get("role"), "id": native}
    name = node.get("name")
    if isinstance(name, str) and name.strip():
        return {"role": node.get("role"), "name": name.strip()}
    return None


def child_container(node, inherited, is_root):
    """The area the node's children sit in: none below the root window, else the node itself
    when it can be one, else the area the node sits in."""
    if is_root:
        return None
    return as_container(node) or inherited


def container_key(container):
    """An area as a hashable key, (role, "id" | "name", text), or None. {role, id} and {role, name}
    stay apart even when their text is the same."""
    if not isinstance(container, dict):
        return None
    if "id" in container:
        return container.get("role"), "id", container.get("id")
    if "name" in container:
        return container.get("role"), "name", container.get("name")
    return None


def walk(tree):
    """Every node below the root window with the area it sits in, in tree order. A root whose role
    is not window is yielded too, in no area. Entries that are not dicts are skipped."""
    if not isinstance(tree, dict):
        return
    pending: list[tuple[object, dict | None]]
    if tree.get("role") == "window":
        pending = [(child, None) for child in reversed(tree.get("children") or [])]
    else:
        pending = [(tree, None)]
    while pending:
        node, container = pending.pop()
        if not isinstance(node, dict):
            continue
        yield node, container
        inner = child_container(node, container, is_root=False)
        pending.extend((child, inner) for child in reversed(node.get("children") or []))


def fits(role, native, container, entry):
    """Whether a node with this role, native id and area is the one `entry` ({"id", "role", "in"?})
    writes. Ids are compared letter for letter. The area counts only when the entry writes one."""
    if native is None or role != entry.get("role") or native != entry.get("id"):
        return False
    if "in" in entry:
        return container_key(container) is not None and container_key(container) == container_key(entry["in"])
    return True


def count(tree, signal):
    """How many nodes below the root window show the ready signal. A name signal ({"name",
    "role"?}, or a bare text) needs the whole name apart from outer spaces, and the role when it
    gives one. An id signal ({"id", "role", "in"?}) goes through fits."""
    if isinstance(signal, str):
        signal = {"name": signal}
    if "id" in signal:
        return sum(1 for node, container in walk(tree)
                   if fits(node.get("role"), native_value(node), container, signal))
    wanted = str(signal.get("name") or "").strip()
    role = signal.get("role")
    return sum(1 for node, _ in walk(tree) if _named(node, wanted) and (role is None or node.get("role") == role))


def _named(node, wanted):
    name = node.get("name")
    return isinstance(name, str) and name.strip() == wanted
