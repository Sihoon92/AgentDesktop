import re

import pytest
from fake_cli import FakeCli

from agent.desktop import DesktopClient
from agent.guard import AgentStopped
from agent.task import Task
from agent.tools import Toolbox

EXE = r"C:\apps\FootprintChart.exe"
REQUIRE = [
    {"role": "switch", "description_contains": "설정 패널", "states": ["checked"]},
    {"role": "statictext", "name": "지표"},
]
WINDOW_A = "w-3412692"
WINDOW_OTHER_INSTANCE = "w-999"

PANEL_LABEL = 'statictext "지표"'

def make(level=3, confirm=lambda _prompt: True, cli=None):
    cli = cli or FakeCli()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, level, REQUIRE),
        confirm=confirm,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
    )
    return toolbox, cli


def launched(level=3, **kwargs):
    toolbox, cli = make(level, **kwargs)
    toolbox.call("launch_target", {})
    return toolbox, cli


def settings_ref(observation):
    return re.search(r'switch "설정 .*?" (@\S+)', observation).group(1)


def observed(level=3, **kwargs):
    toolbox, cli = launched(level, **kwargs)
    result = toolbox.call(
        "observe",
        {"window_id": WINDOW_A, "root_ref": None, "surface": None, "include_bounds": False},
    )
    return toolbox, cli, settings_ref(result["observation"])


def test_every_tool_schema_meets_openai_strict_mode():
    toolbox, _ = make()

    for tool in toolbox.definitions():
        parameters = tool["parameters"]
        assert tool["type"] == "function"
        assert tool["strict"] is True
        assert parameters["additionalProperties"] is False
        assert sorted(parameters["required"]) == sorted(parameters["properties"])


@pytest.mark.parametrize(
    ("level", "allowed", "denied"),
    [
        (0, "click", "press"),
        (1, "press", "type"),
        (2, "mouse_click", "restart_target"),
        (3, "restart_target", None),
    ],
)
def test_permission_level_decides_which_tools_the_model_sees(level, allowed, denied):
    toolbox, _ = make(level)

    names = {tool["name"] for tool in toolbox.definitions()}

    assert allowed in names
    assert "finish" in names
    if denied:
        assert denied not in names


def test_calling_a_tool_above_the_level_is_refused_without_touching_the_app():
    toolbox, cli = launched(level=0)
    before = len(cli.calls)

    result = toolbox.call("press", {"combo": "escape", "window_id": WINDOW_A})

    assert result["ok"] is False
    assert len(cli.calls) == before


def test_launch_uses_the_exact_exe_path():
    toolbox, cli = make()

    result = toolbox.call("launch_target", {})

    assert cli.commands()[0] == ["launch", EXE]
    assert result["pid"] == 44584


def test_tools_need_a_launched_target_first():
    toolbox, cli = make()

    result = toolbox.call("list_windows", {})

    assert result["ok"] is False
    assert cli.calls == []


def test_list_windows_excludes_another_instance_with_the_same_name():
    toolbox, _ = launched()

    ids = [w["id"] for w in toolbox.call("list_windows", {})["windows"]]

    assert ids == ["w-3412692", "w-20845296"]


def test_observing_a_window_of_another_instance_is_refused():
    toolbox, cli = launched()

    result = toolbox.call(
        "observe",
        {"window_id": WINDOW_OTHER_INSTANCE, "root_ref": None, "surface": None, "include_bounds": False},
    )

    assert result["ok"] is False
    assert "snapshot" not in cli.command_names()


def test_action_on_a_ref_never_observed_is_refused():
    toolbox, cli = launched()

    result = toolbox.call("click", {"ref": "@made:e1"})

    assert result["ok"] is False
    assert "click" not in cli.command_names()


def test_action_returns_a_fresh_observation_showing_the_new_state():
    toolbox, cli, ref = observed()

    result = toolbox.call("toggle", {"ref": ref})

    assert ["toggle", ref] in cli.commands()
    assert cli.command_names()[-1] == "snapshot"
    assert result["ok"] is True
    assert 'switch "설정 ▸"' in result["observation"]
    assert result["disposition"]["delivery"] == "delivered_verified"


def test_second_toggle_of_the_same_control_never_reaches_the_app():
    toolbox, cli, ref = observed()
    fresh = settings_ref(toolbox.call("toggle", {"ref": ref})["observation"])

    result = toolbox.call("toggle", {"ref": fresh})

    assert result["ok"] is False
    assert cli.command_names().count("toggle") == 1


def test_value_starting_with_a_dash_is_passed_after_the_separator():
    toolbox, cli, ref = observed()

    toolbox.call("set_value", {"ref": ref, "value": "-5"})

    assert ["set-value", ref, "--", "-5"] in cli.commands()


def test_semantic_action_stays_headless_but_type_is_headed():
    toolbox, cli, ref = observed()

    toolbox.call("click", {"ref": ref})
    assert "--headed" not in cli.full_call("click")

    toolbox.call("type", {"ref": ref, "text": "hi"})
    assert "--headed" in cli.full_call("type")


def test_press_targets_our_window_and_never_forces():
    toolbox, cli = launched()

    toolbox.call("press", {"combo": "escape", "window_id": WINDOW_A})

    assert ["press", "--window-id", WINDOW_A, "escape"] in cli.commands()
    assert "--force" not in cli.full_call("press")


def test_press_on_another_instance_is_refused():
    toolbox, cli = launched()

    result = toolbox.call("press", {"combo": "escape", "window_id": WINDOW_OTHER_INSTANCE})

    assert result["ok"] is False
    assert "press" not in cli.command_names()


def test_mouse_click_inside_our_focused_window_is_sent_headed():
    toolbox, cli = launched()

    toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert ["mouse-click", "--xy=2000,100", "--button=left"] in cli.commands()
    assert "--headed" in cli.full_call("mouse-click")
    assert cli.command_names().index("focus-window") < cli.command_names().index("mouse-click")


def test_mouse_click_outside_the_window_is_refused():
    toolbox, cli = launched()

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 100, "y": 100, "button": "left"})

    assert result["ok"] is False
    assert "mouse-click" not in cli.command_names()


def test_mouse_click_is_refused_when_our_window_could_not_take_focus():
    cli = FakeCli()
    cli.focused = False
    toolbox, _ = launched(cli=cli)

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert result["ok"] is False
    assert "mouse-click" not in cli.command_names()


def test_drag_needs_both_points_inside_the_window():
    toolbox, cli = launched()
    outside = {"window_id": WINDOW_A, "from_x": 2000, "from_y": 100, "to_x": 100, "to_y": 100}
    inside = {"window_id": WINDOW_A, "from_x": 2000, "from_y": 100, "to_x": 2100, "to_y": 200}

    assert toolbox.call("drag", outside)["ok"] is False
    assert "drag" not in cli.command_names()

    toolbox.call("drag", inside)
    assert ["drag", "--from-xy=2000,100", "--to-xy=2100,200"] in cli.commands()


def test_restart_declined_by_the_user_leaves_the_app_alone():
    toolbox, cli = launched(confirm=lambda _prompt: False)

    result = toolbox.call("restart_target", {})

    assert result["ok"] is False
    assert not any(call[0] == "taskkill" for call in cli.calls)


def test_restart_closes_only_our_pid_gracefully_then_launches_again():
    toolbox, cli = launched()

    result = toolbox.call("restart_target", {})

    kill = next(call for call in cli.calls if call[0] == "taskkill")
    assert kill == ["taskkill", "/PID", "44584"]
    assert cli.command_names().count("launch") == 2
    assert result["ok"] is True


def test_restart_is_refused_when_the_pid_now_belongs_to_a_different_process():
    cli = FakeCli()
    toolbox, _ = launched(cli=cli)
    cli.instance = "windows-proc-v1:reused-pid"

    result = toolbox.call("restart_target", {})

    assert result["ok"] is False
    assert not any(call[0] == "taskkill" for call in cli.calls)


def test_wait_for_text_returns_once_the_text_appears():
    cli = FakeCli()
    toolbox, _ = launched(cli=cli)
    toolbox.call(
        "observe",
        {"window_id": WINDOW_A, "root_ref": None, "surface": None, "include_bounds": False},
    )
    cli.panel_open = True

    result = toolbox.call("wait_for_text", {"text": "지표", "timeout_ms": 5000})

    assert result["found"] is True


def test_wait_for_text_reports_timeout():
    toolbox, _ = launched()
    toolbox.call(
        "observe",
        {"window_id": WINDOW_A, "root_ref": None, "surface": None, "include_bounds": False},
    )

    result = toolbox.call("wait_for_text", {"text": "없는글자", "timeout_ms": 3000})

    assert result["found"] is False


def test_verify_is_false_while_the_panel_is_closed_and_true_once_open():
    cli = FakeCli()
    toolbox, _ = launched(cli=cli)

    assert toolbox.verify()["met"] is False

    cli.panel_open = True
    assert toolbox.verify()["met"] is True


def test_verify_never_counts_another_instance():
    cli = FakeCli(panel_open=True)
    toolbox, _ = launched(cli=cli)

    toolbox.verify()

    snapshot_windows = [w[w.index("--window-id") + 1] for w in cli.commands() if w[0] == "snapshot"]
    assert WINDOW_OTHER_INSTANCE not in snapshot_windows
    assert set(snapshot_windows) <= {"w-3412692", "w-20845296"}


@pytest.mark.parametrize(
    ("tool", "arguments", "expected", "headed"),
    [
        ("expand", {}, ["expand", "REF"], False),
        ("collapse", {}, ["collapse", "REF"], False),
        ("select", {"value": "5분"}, ["select", "REF", "--", "5분"], False),
        ("check", {}, ["check", "REF"], False),
        ("uncheck", {}, ["uncheck", "REF"], False),
        ("scroll", {"direction": "up", "amount": 2}, ["scroll", "REF", "--direction=up", "--amount=2"], False),
        ("double_click", {}, ["double-click", "REF"], True),
        ("right_click", {}, ["right-click", "REF"], True),
    ],
)
def test_ref_tool_builds_the_documented_command(tool, arguments, expected, headed):
    toolbox, cli, ref = observed()

    toolbox.call(tool, {"ref": ref, **arguments})

    want = [ref if word == "REF" else word for word in expected]
    assert want in cli.commands()
    assert ("--headed" in cli.full_call(expected[0])) is headed


def test_mouse_wheel_is_checked_against_the_window_and_sent_headed():
    toolbox, cli = launched()

    outside = {"window_id": WINDOW_A, "x": 100, "y": 100, "dy": -3}
    assert toolbox.call("mouse_wheel", outside)["ok"] is False
    assert "mouse-wheel" not in cli.command_names()

    toolbox.call("mouse_wheel", {"window_id": WINDOW_A, "x": 2000, "y": 100, "dy": -3})
    assert ["mouse-wheel", "--x=2000", "--y=100", "--dy=-3"] in cli.commands()
    assert "--headed" in cli.full_call("mouse-wheel")


def test_restore_window_only_accepts_our_window():
    toolbox, cli = launched()

    assert toolbox.call("restore_window", {"window_id": WINDOW_OTHER_INSTANCE})["ok"] is False
    assert "restore" not in cli.command_names()

    toolbox.call("restore_window", {"window_id": WINDOW_A})
    assert ["restore", "--window-id", WINDOW_A] in cli.commands()


SETTINGS_BOUNDS = {"x": 3705.0, "y": 118.0, "width": 108.0, "height": 45.0}


class RecordingObserver:
    def __init__(self):
        self.observations = []
        self.actions = []
        self.sources = []

    def on_observation(self, window, envelope, text, source=None, wait=None):
        self.observations.append((window, envelope, text))
        self.sources.append((source, wait))

    def on_action(self, tool, arguments, target, result):
        self.actions.append((tool, arguments, target, result))


def test_snapshots_always_request_bounds_but_model_text_omits_them():
    toolbox, cli = launched()

    result = toolbox.call(
        "observe",
        {"window_id": WINDOW_A, "root_ref": None, "surface": None, "include_bounds": False},
    )

    snapshots = [words for words in cli.commands() if words[0] == "snapshot"]
    assert snapshots and all("--include-bounds" in words for words in snapshots)
    assert "bounds=" not in result["observation"]


def test_observer_sees_each_observation_and_action_target():
    observer = RecordingObserver()
    cli = FakeCli()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, 3, REQUIRE),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=observer,
    )
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    observed_text = toolbox.call(
        "observe",
        {"window_id": WINDOW_A, "root_ref": None, "surface": None, "include_bounds": False},
    )["observation"]
    ref = settings_ref(observed_text)

    toolbox.call("toggle", {"ref": ref, "intent": "설정 패널을 연다"})

    assert len(observer.observations) == 2
    window, envelope, text = observer.observations[0]
    assert window["id"] == WINDOW_A
    assert envelope["command"] == "snapshot"
    assert text == observed_text
    tool, arguments, target, result = observer.actions[-1]
    assert tool == "toggle"
    assert arguments["intent"] == "설정 패널을 연다"
    assert target == {
        "ref": ref,
        "role": "switch",
        "name": "설정 ◂",
        "description": "오른쪽 지표 설정 패널 펼치기·접기",
        "bounds": SETTINGS_BOUNDS,
    }
    assert result["ok"] is True
    assert "observation" not in result


def test_action_tools_require_intent():
    toolbox, _ = make()

    required = {tool["name"]: tool["parameters"]["required"] for tool in toolbox.definitions()}

    for name in ("click", "press", "mouse_click"):
        assert "intent" in required[name]
    for name in ("observe", "list_windows", "wait_for_text", "finish"):
        assert "intent" not in required[name]


def test_intent_does_not_change_guard_identity():
    toolbox, cli, ref = observed()
    fresh = settings_ref(toolbox.call("toggle", {"ref": ref, "intent": "연다"})["observation"])

    result = toolbox.call("toggle", {"ref": fresh, "intent": "한 번 더 확실히 연다"})

    assert result["ok"] is False
    assert cli.command_names().count("toggle") == 1

    point = {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"}
    for intent in ("첫째", "둘째", "셋째"):
        toolbox.call("mouse_click", {**point, "intent": intent})
    with pytest.raises(AgentStopped):
        toolbox.call("mouse_click", {**point, "intent": "넷째"})


def test_preview_returns_target_without_running_the_cli():
    toolbox, cli, ref = observed()
    before = len(cli.calls)

    ref_target = toolbox.preview("toggle", {"ref": ref, "intent": "연다"})
    point_target = toolbox.preview(
        "drag",
        {"window_id": WINDOW_A, "from_x": 2000, "from_y": 100, "to_x": 2100, "to_y": 200, "intent": "옮긴다"},
    )

    assert ref_target["name"] == "설정 ◂"
    assert ref_target["bounds"] == SETTINGS_BOUNDS
    assert point_target == {"window_id": WINDOW_A, "points": [[2000, 100], [2100, 200]]}
    assert toolbox.preview("toggle", {"ref": "@made:e1", "intent": "x"}) is None
    assert toolbox.preview("observe", {}) is None
    assert len(cli.calls) == before


def watched(cli=None):
    observer = RecordingObserver()
    cli = cli or FakeCli()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, 3, REQUIRE),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=observer,
    )
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    return toolbox, cli, observer


def test_wait_for_text_tells_the_observer_once_after_polling():
    cli = FakeCli()
    cli.open_panel_on_snapshot = 3
    toolbox, _, observer = watched(cli)

    result = toolbox.call("wait_for_text", {"text": PANEL_LABEL, "timeout_ms": 20000})

    assert result["found"] is True
    assert cli.snapshots == 3
    assert len(observer.observations) == 1
    assert observer.observations[0][2] == result["observation"]
    source, wait = observer.sources[0]
    assert source == "wait_for_text"
    assert {key: wait[key] for key in ("text", "found", "polls")} == {"text": PANEL_LABEL, "found": True, "polls": 3}
    assert wait["waited_ms"] >= 0


def test_wait_for_text_that_times_out_is_still_one_observation():
    toolbox, cli, observer = watched()

    result = toolbox.call("wait_for_text", {"text": "없는글자", "timeout_ms": 3000})

    assert result["found"] is False
    assert cli.snapshots > 1
    assert len(observer.observations) == 1
    source, wait = observer.sources[0]
    assert (source, wait["found"], wait["polls"]) == ("wait_for_text", False, cli.snapshots)


def test_each_observation_says_which_tool_made_it():
    toolbox, _, observer = watched()
    ref = settings_ref(toolbox.call(
        "observe", {"window_id": WINDOW_A, "root_ref": None, "surface": None, "include_bounds": False},
    )["observation"])

    toolbox.call("toggle", {"ref": ref, "intent": "연다"})
    toolbox.call("restore_window", {"window_id": WINDOW_A, "intent": "복원"})
    list(toolbox.observed_trees())

    assert [source for source, _ in observer.sources] == ["observe", "action", "action", "observe", "observe"]
    assert all(wait is None for _, wait in observer.sources)
