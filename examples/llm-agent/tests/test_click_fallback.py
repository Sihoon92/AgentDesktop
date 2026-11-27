"""A ref that advertises no Click is pressed by hand at its center.

Seen at the company (H04: 74 UIA nodes, no pattern): elements carry refs because of their
roles but advertise no Invoke and no Legacy default action, so the CLI's `click @ref` could
only fail. The harness presses such an element physically, the way click_part would, without
sending the CLI a click. A ref that advertises Click keeps the CLI's click and its answer.
"""

import pytest

from agent.hand_click import refs_without_click
from tests.test_tools import SCENE, click_ref, toolbox_for

CELL_CENTER = "--xy=1290,676"


def failure(code, delivery):
    return {"version": "2.6", "ok": False, "command": "click",
            "error": {"code": code, "message": "All chain steps exhausted",
                      "disposition": {"delivery": delivery, "retry": "safe"}}}


def test_ref_without_a_click_action_is_pressed_by_hand_without_asking_the_cli(tmp_path):
    toolbox, cli, _ = toolbox_for(SCENE, tmp_path)

    result = toolbox.call("click", click_ref("@s1:e2"))

    names = cli.command_names()
    assert "click" not in names
    assert names.index("focus-window") < names.index("mouse-click")
    assert CELL_CENTER in cli.full_call("mouse-click")
    assert result["ok"] is True
    assert result["pressed_by_hand"] == {"mark": 7, "because": "NO_CLICK_ACTION"}


def test_ref_without_a_click_action_is_refused_when_physical_clicks_are_not_allowed(tmp_path):
    toolbox, cli, _ = toolbox_for(SCENE, tmp_path, level=0)

    result = toolbox.call("click", click_ref("@s1:e2"))

    assert not {"click", "mouse-click"} & set(cli.command_names())
    assert "click_part" in result["refused"]


def test_a_ref_that_advertises_click_keeps_the_cli_click(tmp_path):
    toolbox, cli, _ = toolbox_for(SCENE, tmp_path)
    assert toolbox.call("click", click_ref("@s1:e1"))["ok"] is True
    assert "click" in cli.command_names() and "mouse-click" not in cli.command_names()


@pytest.mark.parametrize("delivery", ["not_delivered", "unknown"])
def test_a_failed_click_on_a_ref_that_advertises_click_is_never_pressed_by_hand(tmp_path, delivery):
    """The CLI refused a control it could click (disabled, covered): pressing it by hand would
    be the wrong fix, so its failure goes back to the model as it is."""
    toolbox, cli, _ = toolbox_for(SCENE, tmp_path)
    cli.action_results["click"] = failure("ACTION_FAILED", delivery)

    result = toolbox.call("click", click_ref("@s1:e1"))

    assert "mouse-click" not in cli.command_names()
    assert result["ok"] is False and "pressed_by_hand" not in result


def test_refs_without_click_reads_the_observed_actions():
    tree = {"role": "window", "ref_id": "@s:e0", "available_actions": ["Click"], "children": [
        {"role": "button", "ref_id": "@s:e1", "available_actions": ["SetFocus"]},
        {"role": "button", "ref_id": "@s:e2"},
        {"role": "button", "ref_id": "@s:e3", "available_actions": ["Click", "SetFocus"]},
        {"role": "statictext", "name": "x"}]}
    assert refs_without_click(tree) == frozenset({"@s:e1", "@s:e2"})
