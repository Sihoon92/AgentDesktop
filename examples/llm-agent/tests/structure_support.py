"""The company-shaped table tree (tests/fixtures/snapshot_company_table.json) and a toolbox that
observes it through FakeCli, for the structure, observation and table tool tests."""

import json
from pathlib import Path

from fake_cli import FakeCli, scene_window

from agent.desktop import DesktopClient
from agent.task import Task
from agent.tools import Toolbox

FIXTURES = Path(__file__).parent / "fixtures"
EXE = r"C:\apps\Practice.exe"
WINDOW_BOUNDS = {"x": 0.0, "y": 0.0, "width": 916.0, "height": 640.0}
OBSERVE = {"window_id": None, "root_ref": None, "surface": None, "include_bounds": False}


def load(name):
    loaded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded.get("data", {}).get("tree", loaded)


def company():
    return load("snapshot_company_table.json")


def table_node(tree):
    node = tree
    while node.get("role") != "table":
        node = node["children"][0]
    return node


def data_rows(tree):
    return table_node(tree)["children"][0]["children"]


def set_values(tree, rows):
    """Gives the table's cells these values row by row; None leaves a cell without a value."""
    for row, values in zip(data_rows(tree), rows):
        for cell, value in zip(row["children"], values):
            cell.pop("value", None)
            if value is not None:
                cell["value"] = value
    return tree


def add_rows(tree, rows):
    """Appends rows of (공정, 구분, 양품수) values below the fixture's three, in the same shape."""
    panel = table_node(tree)["children"][0]
    for values in rows:
        k = len(panel["children"])
        cells = []
        for column, (label, value) in enumerate(zip(("공정", "구분", "양품수"), values)):
            cell = {"role": "cell", "name": f"{label} row{k}", "ref_id": f"@scompany:e{k * 3 + column + 1}",
                    "bounds": {"x": 10.0 + 150 * column, "y": 200.0 + 24 * k, "width": 146.0, "height": 22.0},
                    "available_actions": ["Click"]}
            if value is not None:
                cell["value"] = value
            cells.append(cell)
        panel["children"].append({"role": "unknown", "name": f"Row {k + 1}", "children": cells,
                                  "bounds": {"x": 10.0, "y": 200.0 + 24 * k, "width": 446.0, "height": 22.0}})
    return tree


def scene(tree):
    return {"windows": [scene_window("w-1", "메인", WINDOW_BOUNDS)], "tree": tree}


def toolbox_for(tree, tmp_path, level=2, observer=None, **options):
    """A launched toolbox that has observed `tree` once, with its FakeCli."""
    cli = FakeCli(scene=scene(tree))
    clock = iter(range(0, 100_000))
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), Task("goal", EXE, level, []),
                      confirm=lambda _prompt: True, sleep=lambda _s: None, clock=lambda: next(clock),
                      observer=observer, captures_dir=tmp_path / "captures", **options)
    toolbox.call("launch_target", {})
    toolbox.call("observe", OBSERVE)
    return toolbox, cli
