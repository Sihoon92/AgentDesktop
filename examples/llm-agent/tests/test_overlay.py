from PIL import Image

from agent.overlay import LEGEND, draw_overlay, overlay_boxes
from agent.survey import kinds

ORIGIN = (100, 50)
COLORS = {kind: color for kind, _name, color in LEGEND}


def box(x, y, w=60, h=30):
    return {"x": ORIGIN[0] + x, "y": ORIGIN[1] + y, "width": w, "height": h}


def tree_of(*children):
    return {"role": "window", "bounds": box(0, 0, 800, 600), "children": list(children)}


NODES = {
    "named_ref": {"ref_id": "@e1", "name": "SECRET_NAME", "bounds": box(10, 10)},
    "unnamed_ref": {"ref_id": "@e2", "bounds": box(110, 10)},
    "text": {"name": "SECRET_NAME", "bounds": box(210, 10)},
    "blank": {"bounds": box(310, 10)},
    "id_only": {"native_id": {"kind": "automation_id", "value": "x1"}, "bounds": box(410, 10),
                "children": [{"name": "inner", "bounds": box(415, 15, 10, 10)}]},
}


def test_each_kind_is_drawn_in_its_color(tmp_path):
    out = tmp_path / "overlay.png"
    drawn = draw_overlay(Image.new("RGB", (800, 600), "white"), ORIGIN, tree_of(*NODES.values()), out)
    assert drawn == 6
    picture = Image.open(out).convert("RGB")
    for kind, node in NODES.items():
        left = node["bounds"]["x"] - ORIGIN[0]
        top = node["bounds"]["y"] - ORIGIN[1]
        inset = 2 if kind == "id_only" else 0
        assert picture.getpixel((left + inset + 20, top + inset)) == COLORS[kind], kind


def test_node_outside_the_capture_is_skipped(tmp_path):
    outside = {"ref_id": "@e9", "name": "x", "bounds": {"x": 5000, "y": 5000, "width": 10, "height": 10}}
    flat = {"ref_id": "@e8", "name": "y", "bounds": box(10, 10, 0, 0)}
    inside = {"ref_id": "@e7", "name": "z", "bounds": box(10, 10)}
    assert draw_overlay(Image.new("RGB", (800, 600), "white"), ORIGIN, tree_of(outside, flat, inside),
                        tmp_path / "o.png") == 1
    assert overlay_boxes(tree_of(outside), ORIGIN, (800, 600)) == []


def test_survey_kinds_and_overlay_agree():
    tree = tree_of(*NODES.values(), {"ref_id": "@e3", "name": "z", "bounds": box(10, 100),
                                     "children": [{"bounds": box(12, 102, 5, 5)}]})
    counted = kinds(tree)
    drawn: dict[str, int] = {}
    for kind, _rect in overlay_boxes(tree, ORIGIN, (800, 600)):
        drawn[kind] = drawn.get(kind, 0) + 1
    for kind in COLORS:
        assert drawn.get(kind, 0) == counted[kind], kind


def test_root_is_not_drawn(tmp_path):
    root_only = {"ref_id": "@e1", "name": "root", "bounds": box(0, 0, 800, 600)}
    assert draw_overlay(Image.new("RGB", (800, 600), "white"), ORIGIN, root_only, tmp_path / "r.png") == 0
