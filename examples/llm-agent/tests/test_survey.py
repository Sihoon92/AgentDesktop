import hashlib
import json
from pathlib import Path

import pytest

from agent.survey import id_keys, kinds

FIXTURES = Path(__file__).parent / "fixtures"
KIND_KEYS = {"named_ref", "unnamed_ref", "unnamed_ref_with_id", "text", "blank", "nodes", "depth"}


def snapshot_tree(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["data"]["tree"]


def box(x=0, y=0, width=40, height=20):
    return {"x": x, "y": y, "width": width, "height": height}


def window(*children):
    return {"role": "window", "name": "제목", "bounds": box(0, 0, 800, 600), "children": list(children)}


def counts(tree):
    found = kinds(tree)
    return {key: found[key] for key in ("named_ref", "unnamed_ref", "unnamed_ref_with_id", "text", "blank")}


def test_kinds_returns_every_contract_key_for_an_empty_window():
    found = kinds({"role": "window"})
    assert set(found) == KIND_KEYS
    assert found == {"named_ref": 0, "unnamed_ref": 0, "unnamed_ref_with_id": 0, "text": 0, "blank": 0,
                     "nodes": 1, "depth": 1}


def test_a_ref_with_a_name_is_named_and_one_without_is_unnamed():
    tree = window(
        {"role": "button", "name": "확인", "ref_id": "@s:e1", "bounds": box()},
        {"role": "button", "ref_id": "@s:e2", "bounds": box()},
        {"role": "button", "name": "   ", "ref_id": "@s:e3"},
    )
    assert counts(tree) == {"named_ref": 1, "unnamed_ref": 2, "unnamed_ref_with_id": 0, "text": 0, "blank": 0}


def test_unnamed_ref_with_id_counts_unnamed_refs_that_carry_a_native_id():
    native = {"kind": "automation_id", "value": "date-from"}
    tree = window(
        {"role": "edit", "ref_id": "@s:e1", "native_id": native},
        {"role": "edit", "ref_id": "@s:e2", "native_id": {"kind": "automation_id", "value": ""}},
        {"role": "button", "name": "확인", "ref_id": "@s:e3", "native_id": native},
    )
    found = counts(tree)
    assert found["unnamed_ref"] == 2 and found["unnamed_ref_with_id"] == 1 and found["named_ref"] == 1


def test_text_is_a_named_element_without_a_ref_that_has_a_position():
    tree = window(
        {"role": "statictext", "name": "라벨", "bounds": box()},
        {"role": "statictext", "name": "위치 없음"},
        {"role": "statictext", "name": "크기 없음", "bounds": box(width=0)},
        {"role": "group", "name": "묶음", "bounds": box(), "children": [{"role": "statictext", "name": "안", "bounds": box()}]},
    )
    assert counts(tree)["text"] == 3


def test_blank_is_a_nameless_refless_childless_element_with_an_area():
    tree = window(
        {"role": "group", "bounds": box(0, 0, 300, 200)},
        {"role": "image", "bounds": box(0, 0, 300, 0)},
        {"role": "group"},
        {"role": "group", "bounds": box(), "children": [{"role": "group"}]},
        {"role": "group", "bounds": box(), "children_count": 3},
        {"role": "group", "bounds": box(), "ref_id": "@s:e9"},
        {"role": "group", "bounds": box(), "children": []},
    )
    assert counts(tree)["blank"] == 2


def test_classification_ignores_roles():
    plain = window({"name": "가", "bounds": box()}, {"bounds": box()}, {"ref_id": "@s:e1"})
    roled = window(
        {"role": "button", "name": "가", "bounds": box()},
        {"role": "statictext", "bounds": box()},
        {"role": "statictext", "ref_id": "@s:e1"},
    )
    assert kinds(plain) == kinds(roled)


def test_the_root_is_counted_as_a_node_but_never_classified():
    assert counts({"role": "window", "name": "제목", "bounds": box()}) == counts({"role": "window"})
    assert counts({"name": "x", "ref_id": "@s:e1", "bounds": box()})["named_ref"] == 0


def test_nodes_and_depth_cover_the_whole_tree():
    tree = window({"role": "group", "children": [{"role": "group", "children": [{"role": "text"}]}]}, {"role": "x"})
    found = kinds(tree)
    assert found["nodes"] == 5 and found["depth"] == 4


@pytest.mark.parametrize(
    "name, expected",
    [
        ("snapshot_virtual_mes_easy.json",
         {"named_ref": 20, "unnamed_ref": 2, "unnamed_ref_with_id": 2, "text": 2, "blank": 0, "nodes": 31, "depth": 4}),
        ("snapshot_virtual_mes_mes_like.json",
         {"named_ref": 3, "unnamed_ref": 8, "unnamed_ref_with_id": 8, "text": 12, "blank": 1, "nodes": 30, "depth": 4}),
        ("snapshot_virtual_mes_hard.json",
         {"named_ref": 1, "unnamed_ref": 2, "unnamed_ref_with_id": 2, "text": 2, "blank": 3, "nodes": 12, "depth": 4}),
    ],
)
def test_the_three_practice_profiles_give_their_own_counts(name, expected):
    assert kinds(snapshot_tree(name)) == expected


def test_the_three_practice_profiles_differ_where_exposure_differs():
    easy, mes_like, hard = (kinds(snapshot_tree(f"snapshot_virtual_mes_{p}.json")) for p in ("easy", "mes_like", "hard"))
    assert easy["named_ref"] > mes_like["named_ref"] > hard["named_ref"]
    assert easy["blank"] < mes_like["blank"] < hard["blank"]
    assert mes_like["text"] > max(easy["text"], hard["text"])


def test_a_captured_compact_snapshot_without_bounds_has_no_text_or_blank():
    found = kinds(snapshot_tree("snapshot_settings_open.json"))
    assert found == {"named_ref": 37, "unnamed_ref": 6, "unnamed_ref_with_id": 2, "text": 0, "blank": 0,
                     "nodes": 144, "depth": found["depth"]}
    assert found["depth"] > 5


def key(role, kind, value):
    return hashlib.sha256(f"{role}\n{kind}\n{value}".encode("utf-8")).hexdigest()[:16]


def test_id_keys_hash_role_kind_and_value_of_every_element_with_a_native_id():
    tree = window(
        {"role": "button", "name": "확인", "ref_id": "@s:e1", "native_id": {"kind": "automation_id", "value": "ok"}},
        {"role": "group", "native_id": {"kind": "automation_id", "value": "panel"},
         "children": [{"role": "edit", "native_id": {"kind": "automation_id", "value": "date-from"}}]},
        {"role": "button", "name": "취소"},
    )
    assert id_keys(tree) == {
        key("button", "automation_id", "ok"),
        key("group", "automation_id", "panel"),
        key("edit", "automation_id", "date-from"),
    }


def test_id_keys_never_hold_the_raw_value_and_are_sixteen_hex_characters():
    keys = id_keys(window({"role": "edit", "native_id": {"kind": "automation_id", "value": "date-from"}}))
    [only] = keys
    assert len(only) == 16 and set(only) <= set("0123456789abcdef") and "date" not in only


def test_id_keys_differ_by_role_and_kind_and_skip_empty_values():
    tree = window(
        {"role": "edit", "native_id": {"kind": "automation_id", "value": "x"}},
        {"role": "button", "native_id": {"kind": "automation_id", "value": "x"}},
        {"role": "button", "native_id": {"kind": "runtime_id", "value": "x"}},
        {"role": "button", "native_id": {"kind": "automation_id", "value": ""}},
        {"role": "button", "native_id": None},
    )
    assert len(id_keys(tree)) == 3


def test_id_keys_are_the_same_for_the_same_ids_in_a_fresh_snapshot():
    first = snapshot_tree("snapshot_settings_open.json")
    again = json.loads(json.dumps(first).replace("s1fdmvm0ic1pbm", "s9other0000000"))
    assert id_keys(first) == id_keys(again) and len(id_keys(first)) > 10
