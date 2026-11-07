"""Checks task success against a snapshot tree, independently of what the model claims."""


def first_unmet(requirements, tree):
    """Returns the first requirement no element satisfies, or None when all are met."""
    for requirement in requirements:
        if not _any_match(requirement, tree):
            return requirement
    return None


def find_element(requirement, node):
    """Returns the first element in tree order that satisfies the requirement, or None."""
    if _matches(requirement, node):
        return node
    for child in node.get("children", []):
        found = find_element(requirement, child)
        if found is not None:
            return found
    return None


def _any_match(requirement, node):
    return find_element(requirement, node) is not None


def _matches(requirement, node):
    name = node.get("name") or ""
    description = node.get("description") or ""
    states = node.get("states") or []
    return (
        ("role" not in requirement or node.get("role") == requirement["role"])
        and ("name" not in requirement or name == requirement["name"])
        and requirement.get("name_contains", "") in name
        and requirement.get("description_contains", "") in description
        and all(state in states for state in requirement.get("states", []))
    )
