"""The one rule that tells a node by role, native id and the area it sits in (agent/identity.py)."""

import ast
from pathlib import Path

import pytest

from agent import identity
from agent.identity import as_container, child_container, count, fits, is_leaf, native_value, walk
from tests.ready_support import SIGNAL, node, window_tree

HERE = Path(__file__).resolve().parent.parent


def element(role, native=None, name=None, children=(), **extra):
    found = {"role": role, "children": list(children), **extra}
    if native is not None:
        found["native_id"] = {"kind": "automation_id", "value": native}
    if name is not None:
        found["name"] = name
    return found


def containers(tree):
    return [(found.get("role"), native_value(found), container) for found, container in walk(tree)]


def menu_window(*toolbars, root_native=None):
    return element("window", native=root_native, name="Main", children=toolbars)


def test_the_container_is_the_nearest_ancestor_with_an_id_or_a_name():
    image = element("image", native="56")
    tree = menu_window(element("toolbar", native="12", children=[element("group", children=[image])]))

    assert containers(tree) == [
        ("toolbar", "12", None),
        ("group", None, {"role": "toolbar", "id": "12"}),
        ("image", "56", {"role": "toolbar", "id": "12"}),
    ]


def test_an_ancestor_with_only_a_name_is_named_by_its_name_without_outer_spaces():
    tree = menu_window(element("pane", name="  주문 관리 ", children=[element("image", native="60")]))

    assert containers(tree)[1] == ("image", "60", {"role": "pane", "name": "주문 관리"})


def test_an_ancestor_with_an_id_and_a_name_is_named_by_its_id():
    tree = menu_window(element("toolbar", native="12", name="메뉴", children=[element("image", native="56")]))

    assert containers(tree)[1] == ("image", "56", {"role": "toolbar", "id": "12"})


def test_a_blank_name_is_no_name():
    tree = menu_window(element("pane", name="   ", children=[element("image", native="56")]))

    assert containers(tree)[1] == ("image", "56", None)


def test_the_root_window_is_never_a_container():
    tree = menu_window(element("image", native="56"), root_native="1")

    assert containers(tree) == [("image", "56", None)]
    assert child_container(tree, None, is_root=True) is None
    assert child_container(element("pane", native="3"), {"role": "toolbar", "id": "12"}, is_root=True) is None


def test_child_container_inherits_when_the_node_has_neither_an_id_nor_a_name():
    inherited = {"role": "toolbar", "id": "12"}

    assert child_container(element("group"), inherited, is_root=False) == inherited
    assert child_container(element("group", native="7"), inherited, is_root=False) == {"role": "group", "id": "7"}


def test_a_tree_whose_root_is_not_a_window_keeps_its_root():
    """A part read with --root: the area is found inside that part, and its root may be one."""
    tree = element("toolbar", native="12", children=[element("image", native="56")])

    assert containers(tree) == [("toolbar", "12", None), ("image", "56", {"role": "toolbar", "id": "12"})]


def test_walk_goes_in_tree_order_and_skips_what_is_not_a_node():
    tree = menu_window(
        element("pane", name="a", children=[element("text", name="a1"), "junk", element("text", name="a2")]),
        element("pane", name="b"),
    )

    assert [found.get("name") for found, _ in walk(tree)] == ["a", "a1", "a2", "b"]
    assert list(walk(None)) == []
    assert list(walk("junk")) == []


def test_as_container():
    assert as_container(element("toolbar", native="12", name="메뉴")) == {"role": "toolbar", "id": "12"}
    assert as_container(element("pane", name=" 목록 ")) == {"role": "pane", "name": "목록"}
    assert as_container(element("group")) is None
    assert as_container(element("group", name="")) is None


def test_is_leaf():
    assert is_leaf(element("image", native="56"))
    assert is_leaf({"role": "image"})
    assert not is_leaf(element("group", children=[element("image")]))
    assert not is_leaf({"role": "group", "children_count": 3})


def test_native_value_reads_a_dict_a_string_and_nothing():
    assert native_value({"native_id": {"kind": "automation_id", "value": "56"}}) == "56"
    assert native_value({"native_id": "56"}) == "56"
    assert native_value({"native_id": {"kind": "automation_id", "value": ""}}) is None
    assert native_value({"native_id": ""}) is None
    assert native_value({"native_id": None}) is None
    assert native_value({"native_id": {"kind": "automation_id"}}) is None
    assert native_value({"role": "image"}) is None
    assert native_value(None) is None


def test_native_value_reads_an_element_dict_the_same_way():
    """Elements carry native_id as {"kind", "value"} too, so the same reader serves both."""
    marked = {"mark": 13, "role": "image", "native_id": {"kind": "automation_id", "value": "60"}}

    assert native_value(marked) == "60"


def test_fits_needs_role_id_and_the_written_container():
    toolbar = {"role": "toolbar", "id": "12"}
    entry = {"id": "56", "role": "image", "in": toolbar}

    assert fits("image", "56", {"role": "toolbar", "id": "12"}, entry)
    assert not fits("button", "56", toolbar, entry)
    assert not fits("image", "57", toolbar, entry)
    assert not fits("image", "56", {"role": "toolbar", "id": "13"}, entry)
    assert not fits("image", "56", {"role": "menubar", "id": "12"}, entry)
    assert not fits("image", "56", None, entry)
    assert not fits("image", None, toolbar, entry)


def test_fits_does_not_look_at_the_container_when_the_entry_writes_none():
    entry = {"id": "56", "role": "image"}

    assert fits("image", "56", None, entry)
    assert fits("image", "56", {"role": "toolbar", "id": "12"}, entry)


def test_an_area_named_by_its_id_is_not_one_named_by_the_same_text():
    """{role, id} and {role, name} are different areas even when the text is the same."""
    entry = {"id": "56", "role": "image", "in": {"role": "toolbar", "id": "12"}}

    assert not fits("image", "56", {"role": "toolbar", "name": "12"}, entry)


def test_ids_are_compared_letter_for_letter():
    entry = {"id": "56", "role": "image"}

    assert not fits("image", " 56", None, entry)
    assert not fits("image", "056", None, entry)


@pytest.mark.parametrize("tree, text, expected", [
    (window_tree("Main", SIGNAL), SIGNAL, True),
    (window_tree("Main", f"{SIGNAL}시스템 시작 중"), SIGNAL, False),
    (window_tree("Main", f"  {SIGNAL} "), SIGNAL, True),
    (window_tree("Main", "생산준비", SIGNAL, deep=True), SIGNAL, True),
    (window_tree("Main", SIGNAL), f" {SIGNAL}\n", True),
    (window_tree("Main", "생산준비"), SIGNAL, False),
    (node(role="pane", children=[node(None)]), SIGNAL, False),
    (window_tree(SIGNAL), SIGNAL, False),
    (node(role="pane", children=[node(SIGNAL)]), SIGNAL, True),
], ids=["whole", "part", "spaces", "deep", "spaced-signal", "absent", "no-name", "title", "pane-root"])
def test_count_of_a_name_signal_answers_as_has_named_element_does(tree, text, expected):
    """agent.ready.has_named_element moves onto count; these are tests/test_ready.py's trees."""
    assert (count(tree, {"name": text}) > 0) is expected


def test_count_of_a_name_signal_ignores_only_outer_spaces():
    tree = menu_window(element("button", name=" 조회 "), element("text", name="조회 결과"),
                       element("button", name="Search"), element("button", name="조  회"))

    assert count(tree, {"name": "조회"}) == 1
    assert count(tree, {"name": "search"}) == 0
    assert count(tree, {"name": "조 회"}) == 0


def test_count_of_a_name_signal_with_a_role_needs_that_role():
    tree = menu_window(element("button", name="조회"), element("text", name="조회"))

    assert count(tree, {"name": "조회"}) == 2
    assert count(tree, {"name": "조회", "role": "button"}) == 1
    assert count(tree, {"name": "조회", "role": "menuitem"}) == 0


def test_count_of_a_name_signal_skips_the_window_title():
    assert count(menu_window(), {"name": "Main"}) == 0
    assert count(menu_window(element("text", name="Main")), {"name": "Main"}) == 1


def test_count_of_an_id_signal_tells_two_containers_apart():
    tree = menu_window(
        element("toolbar", native="12", children=[element("image", native="56")]),
        element("toolbar", native="13", children=[element("image", native="56")]),
    )

    assert count(tree, {"id": "56", "role": "image", "in": {"role": "toolbar", "id": "12"}}) == 1
    assert count(tree, {"id": "56", "role": "image"}) == 2
    assert count(tree, {"id": "56", "role": "button"}) == 0


def test_count_of_an_id_signal_skips_the_root_window():
    tree = menu_window(element("pane", name="x"), root_native="56")

    assert count(tree, {"id": "56", "role": "window"}) == 0


def test_count_of_anything_on_no_tree_is_zero():
    assert count(None, {"name": "조회"}) == 0
    assert count({}, {"id": "56", "role": "image"}) == 0


def agent_imports(module):
    tree = ast.parse((HERE / "agent" / module).read_text(encoding="utf-8"))
    imported = {(found.module, alias.name) for found in ast.walk(tree)
                if isinstance(found, ast.ImportFrom) and (found.module or "").split(".")[0] == "agent"
                for alias in found.names}
    imported |= {(alias.name, None) for found in ast.walk(tree) if isinstance(found, ast.Import)
                 for alias in found.names if alias.name.split(".")[0] == "agent"}
    return imported


def test_identity_imports_nothing_from_the_agent():
    """agent.observe uses identity, so identity must not reach back into observe or the rest."""
    assert agent_imports("identity.py") == set()
    assert identity.__name__ == "agent.identity"


def test_profile_takes_only_identity_and_the_system_name_rule_from_the_agent():
    assert {module for module, name in agent_imports("profile.py") if (module, name) != ("agent.knowledge",
                                                                                         "system_name")} \
        <= {"agent.identity"}
