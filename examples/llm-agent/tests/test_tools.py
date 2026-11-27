import json
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

def allow(toolbox, *points):
    """Stands in for an answered confirmation: lets one model-picked point through the policy."""
    toolbox._policy.note_answer([], list(points))


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
    allow(toolbox, (2000, 100))

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


FOCUS_DENIED = {"version": "2.6", "ok": False, "command": "focus-window", "error": {
    "code": "ACTION_FAILED", "message": "Target window did not become foreground before headed delivery",
    "suggestion": "Retry after ensuring the target window can accept focus"}}
FOCUS_PERM = {"version": "2.6", "ok": False, "command": "focus-window", "error": {
    "code": "PERM_DENIED", "message": "activation denied"}}


def test_physical_focus_failure_carries_the_cli_reason():
    cli = FakeCli()
    cli.action_results["focus-window"] = FOCUS_DENIED
    toolbox, _ = launched(cli=cli)
    allow(toolbox, (2000, 100))

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert result["refused_code"] == "focus_failed"
    assert result["focus_error"]["code"] == "ACTION_FAILED"
    assert "focus_window" in result["refused"]
    assert "mouse-click" not in cli.command_names()


def test_physical_focus_perm_denied_names_rights():
    cli = FakeCli()
    cli.action_results["focus-window"] = FOCUS_PERM
    toolbox, _ = launched(cli=cli)
    allow(toolbox, (2000, 100))

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert result["focus_error"]["code"] == "PERM_DENIED"
    assert "higher rights" in result["refused"]


def test_physical_unconfirmed_focus_has_its_own_code():
    cli = FakeCli()
    cli.focused = False
    toolbox, _ = launched(cli=cli)
    allow(toolbox, (2000, 100))

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert result["refused_code"] == "focus_not_confirmed"
    assert "focus_window" in result["refused"]


def test_focus_window_raises_our_window():
    toolbox, cli = launched()

    result = toolbox.call("focus_window", {"window_id": WINDOW_A, "intent": "앞으로"})

    assert result["ok"] is True
    assert ["focus-window", "--window-id", WINDOW_A] in cli.commands()
    assert "--headed" in cli.last_call("focus-window")
    assert toolbox.current_window == WINDOW_A


def test_focus_window_refuses_a_foreign_window():
    toolbox, cli = launched()
    before = cli.command_names().count("focus-window")

    result = toolbox.call("focus_window", {"window_id": WINDOW_OTHER_INSTANCE, "intent": "앞으로"})

    assert result["ok"] is False and "refused" in result
    assert cli.command_names().count("focus-window") == before


def test_focus_window_reports_the_cli_error():
    cli = FakeCli()
    toolbox, _ = launched(cli=cli)
    cli.action_results["focus-window"] = FOCUS_DENIED

    result = toolbox.call("focus_window", {"window_id": WINDOW_A, "intent": "앞으로"})

    assert result["ok"] is False
    assert result["code"] == "ACTION_FAILED"


def test_launch_target_raises_the_launched_window():
    toolbox, cli = launched()

    names = cli.command_names()
    assert names.index("launch") < names.index("focus-window")


def offered(level):
    from agent.tool_schemas import definitions_for

    return [definition["name"] for definition in definitions_for(level, False)]


def test_click_is_not_offered_where_click_part_is():
    for level in (2, 3):
        assert "click" not in offered(level) and "click_part" in offered(level)


def test_click_stays_offered_below_the_physical_level():
    for level in (0, 1):
        assert "click" in offered(level) and "click_part" not in offered(level)


def test_focus_window_is_offered_at_level_zero():
    from agent.tool_schemas import definitions_for

    assert "focus_window" in [definition["name"] for definition in definitions_for(0, False)]


def test_drag_needs_both_points_inside_the_window():
    toolbox, cli = launched()
    outside = {"window_id": WINDOW_A, "from_x": 2000, "from_y": 100, "to_x": 100, "to_y": 100}
    inside = {"window_id": WINDOW_A, "from_x": 2000, "from_y": 100, "to_x": 2100, "to_y": 200}

    assert toolbox.call("drag", outside)["ok"] is False
    assert "drag" not in cli.command_names()

    allow(toolbox, (2000, 100))
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

    for name in ("click_part", "press", "mouse_click"):
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
        allow(toolbox, (2000, 100))
        toolbox.call("mouse_click", {**point, "intent": intent})
    allow(toolbox, (2000, 100))
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


from fake_cli import scene_window  # noqa: E402

from agent.effects import EXPECTATIONS  # noqa: E402
from agent.tool_schemas import READ_ONLY_TOOLS, SCHEMAS  # noqa: E402
from agent.tools import REOBSERVING_TOOLS  # noqa: E402

MAIN_BOUNDS = {"x": 1000, "y": 500, "width": 416, "height": 308}
LINE_BOX = {"x": 1100, "y": 550, "width": 104, "height": 24}
MAIN = scene_window("w-1", "메인", MAIN_BOUNDS)
LIST_WINDOW = scene_window("w-2", "목록", {"x": 1100, "y": 574, "width": 200, "height": 200})


def _node(role, name, bounds, **extra):
    return {"role": role, "name": name, "bounds": bounds, **extra}


SCENE = {
    "windows": [MAIN],
    "tree": {"role": "window", "name": "메인", "bounds": MAIN_BOUNDS, "children": [
        _node("statictext", "Shop", {"x": 1010, "y": 520, "width": 60, "height": 24}),
        _node("button", "조회", {"x": 1300, "y": 520, "width": 80, "height": 24}, ref_id="@s1:e1",
              available_actions=["Click"]),
        _node("statictext", "라인", LINE_BOX),
        _node("statictext", "양품수", {"x": 1250, "y": 640, "width": 80, "height": 24}),
        _node("statictext", "믹싱", {"x": 1010, "y": 664, "width": 80, "height": 24}),
        _node("statictext", "바인더", {"x": 1090, "y": 664, "width": 80, "height": 24}),
        {"role": "cell", "value": "0", "ref_id": "@s1:e2",
         "bounds": {"x": 1250, "y": 664, "width": 80, "height": 24}},
    ]},
}


def _single(node):
    return {"windows": [MAIN], "tree": {"role": "window", "name": "메인", "bounds": MAIN_BOUNDS,
                                         "children": [node]}}


OFFSCREEN_MARK = _single(_node("statictext", "숨은 칸", LINE_BOX, states=["offscreen"]))
ZERO_SIZE_MARK = _single(_node("statictext", "빈 칸", {**LINE_BOX, "width": 0}))
OBSERVE = {"window_id": None, "root_ref": None, "surface": None, "include_bounds": False}


class EventObserver(RecordingObserver):
    def __init__(self):
        super().__init__()
        self.events = []

    def on_event(self, event):
        self.events.append(event)


def toolbox_for(scene, tmp_path, image_mode="user_message", level=2, **options):
    cli = FakeCli(scene=scene)
    cli.on_click = lambda x, y: cli.open_window(LIST_WINDOW) if x > 1180 else cli.focus(mark=3)
    observer = EventObserver()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, level, [], image_mode=image_mode),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=observer,
        captures_dir=tmp_path / "captures",
        **options,
    )
    toolbox.call("launch_target", {})
    toolbox.call("observe", OBSERVE)
    return toolbox, cli, observer


@pytest.fixture
def scene(tmp_path):
    return toolbox_for(SCENE, tmp_path)


def part_call(mark, part, action="click", expect=None, knowledge_id=None):
    arguments = {"mark": mark, "part": part, "action": action, "intent": "누른다", "knowledge_id": knowledge_id}
    return {**arguments, "expect": expect} if expect else arguments


def point_call(capture, x, y, action="click", expect=None):
    arguments = {"capture": capture, "x": x, "y": y, "action": action, "intent": "누른다", "knowledge_id": None}
    return {**arguments, "expect": expect} if expect else arguments


def test_new_tools_are_in_the_schema_at_their_levels():
    levels = {s["definition"]["name"]: s["level"] for s in SCHEMAS}
    assert (levels["look"], levels["find_cells"], levels["click_part"], levels["click_point"]) == (0, 0, 2, 2)
    assert {"look", "find_cells"} <= READ_ONLY_TOOLS
    assert not {"click_part", "click_point"} & READ_ONLY_TOOLS


def test_every_app_changing_tool_takes_expect_and_knowledge_id():
    for schema in SCHEMAS:
        definition = schema["definition"]
        properties = definition["parameters"]["properties"]
        has = "expect" in properties and "knowledge_id" in properties
        assert has is (definition["name"] in REOBSERVING_TOOLS), definition["name"]
        if has:
            assert properties["expect"]["enum"] == list(EXPECTATIONS)
            assert properties["knowledge_id"]["type"] == ["string", "null"]
    assert {"click_part", "click_point"} <= REOBSERVING_TOOLS


def test_observation_lines_carry_marks(scene):
    toolbox, _, _ = scene
    observation = toolbox.call("observe", OBSERVE)["observation"]
    assert re.search(r"\[3\] statictext \"라인\"", observation)
    assert '[2] button "조회" @s1:e1' in observation


def test_click_part_right_end_clicks_inside_the_box_and_reports_the_new_window(scene):
    toolbox, cli, _ = scene
    result = toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened"))
    call = cli.full_call("mouse-click")
    assert "--xy=1192,562" in call and "--headed" in call
    assert "--button=left" in call and "--count=1" in call
    assert cli.command_names().index("focus-window") < cli.command_names().index("mouse-click")
    assert result["ok"] and result["expect"] == "window_opened"
    assert result["expected_met"] is True and result["effects"]["windows_opened"][0]["title"] == "목록"
    assert "capture" not in result and toolbox.take_attachments() == []
    assert "observation" in result


def test_center_click_that_only_moves_focus_is_a_mismatch_with_a_capture(scene):
    toolbox, _, observer = scene
    result = toolbox.call("click_part", part_call(mark=3, part="center", expect="window_opened"))
    assert result["expected_met"] is False and result["effects"]["focus"] == {"mark": 3, "name": "라인"}
    assert result["effects"]["windows_opened"] == []
    assert [shot.id for shot in toolbox.take_attachments()] == [result["capture"]]
    capture = observer.events[-1]
    assert capture["type"] == "capture" and capture["id"] == result["capture"]
    expected_file = "captures/" + result["capture"] + ".png"
    assert (capture["reason"], capture["mark"], capture["file"]) == ("mismatch", None, expected_file)


def test_mismatch_with_images_off_attaches_nothing(tmp_path):
    toolbox, _, _ = toolbox_for(SCENE, tmp_path, image_mode="off")
    result = toolbox.call("click_part", part_call(mark=3, part="center", expect="window_opened"))
    assert result["expected_met"] is False and "capture" not in result
    assert toolbox.take_attachments() == []


@pytest.mark.parametrize("action,button,count", [
    ("double_click", "left", 2), ("right_click", "right", 1)])
def test_double_click_and_right_click_use_count_and_button(scene, action, button, count):
    toolbox, cli, _ = scene
    toolbox.call("click_part", part_call(mark=3, part="center", action=action))
    call = cli.full_call("mouse-click")
    assert f"--count={count}" in call and f"--button={button}" in call


@pytest.mark.parametrize("unsafe", [OFFSCREEN_MARK, ZERO_SIZE_MARK])
def test_click_part_refuses_what_cannot_be_clicked(tmp_path, unsafe):
    toolbox, cli, _ = toolbox_for(unsafe, tmp_path)
    assert "refused" in toolbox.call("click_part", part_call(mark=1, part="center"))
    assert "mouse-click" not in cli.command_names()


def test_click_part_refuses_an_unknown_mark(scene):
    toolbox, cli, _ = scene
    assert "refused" in toolbox.call("click_part", part_call(mark=99, part="center"))
    assert "mouse-click" not in cli.command_names()


def test_click_part_is_a_physical_tool():
    toolbox, _ = make(level=1)
    assert "click_part" not in {tool["name"] for tool in toolbox.definitions()}


def test_find_cells_reads_the_latest_observation(scene):
    toolbox, _, _ = scene
    found = toolbox.call("find_cells", {"rows": ["믹싱", "바인더"], "columns": ["양품수"]})
    assert found["ok"] and found["reason"] is None and len(found["candidates"]) == 1
    assert found["candidates"][0]["mark"] == 7 and found["candidates"][0]["value"] == "0"


def test_find_cells_without_names_is_refused(scene):
    toolbox, _, _ = scene
    assert "refused" in toolbox.call("find_cells", {"rows": [], "columns": ["양품수"]})


def test_look_returns_a_capture_id_and_attaches_the_image(scene):
    toolbox, _, observer = scene
    result = toolbox.call("look", {"mark": None})
    assert result["ok"] and result["capture"] == "c1"
    assert (result["width"], result["height"], result["cropped"]) == (400, 300, False)
    assert result["drawn"] is True
    assert len(toolbox.take_attachments()) == 1
    assert toolbox.take_attachments() == []
    assert observer.events[-1]["reason"] == "look" and observer.events[-1]["id"] == "c1"


def test_look_around_a_mark_crops(scene):
    toolbox, _, observer = scene
    result = toolbox.call("look", {"mark": 3})
    assert result["cropped"] is True and observer.events[-1]["mark"] == 3


def test_look_at_an_unknown_mark_is_refused(scene):
    toolbox, _, _ = scene
    assert "refused" in toolbox.call("look", {"mark": 99})
    assert toolbox.take_attachments() == []


def test_click_point_maps_image_pixels_to_the_screen(scene):
    toolbox, cli, _ = scene
    toolbox.call("look", {"mark": None})
    allow(toolbox, (1018, 520))
    result = toolbox.call("click_point", point_call(capture="c1", x=10, y=20))
    assert "--xy=1018,520" in cli.full_call("mouse-click") and result["ok"]


def test_click_point_after_the_window_moved_is_stale(scene):
    toolbox, cli, _ = scene
    toolbox.call("look", {"mark": None})
    cli.window_shift = 100
    result = toolbox.call("click_point", point_call(capture="c1", x=10, y=20))
    assert result["refused_code"] == "stale_capture"
    assert "mouse-click" not in cli.command_names()


def test_click_point_on_an_unknown_capture_is_stale(scene):
    toolbox, cli, _ = scene
    assert toolbox.call("click_point", point_call(capture="c9", x=10, y=20))["refused_code"] == "stale_capture"
    assert "mouse-click" not in cli.command_names()


def test_click_point_outside_the_capture_is_refused(scene):
    toolbox, cli, _ = scene
    toolbox.call("look", {"mark": None})
    result = toolbox.call("click_point", point_call(capture="c1", x=400, y=20))
    assert "refused" in result and "look" in result["refused"]
    assert "mouse-click" not in cli.command_names()


def test_look_with_images_off_is_refused_with_an_alternative(tmp_path):
    toolbox, _, _ = toolbox_for(SCENE, tmp_path, image_mode="off")
    assert "find_cells" in toolbox.call("look", {"mark": None})["refused"]


def test_legacy_call_without_expect_has_no_expectation_fields(scene):
    toolbox, _, _ = scene
    result = toolbox.call("check", {"ref": "@s1:e1"})
    assert "expected_met" not in result and "expect" not in result
    assert result["effects"]["none"] is True


def test_expect_on_a_ref_tool_is_judged(scene):
    toolbox, _, _ = scene
    result = toolbox.call("check", {"ref": "@s1:e1", "expect": "any", "knowledge_id": None})
    assert result["expected_met"] is False


def test_expect_and_knowledge_id_do_not_change_guard_identity(scene):
    toolbox, _, _ = scene
    point = {"window_id": "w-1", "x": 1050, "y": 600, "button": "left"}
    for expect in ("any", "window_opened", "content_changed"):
        allow(toolbox, (1050, 600))
        toolbox.call("mouse_click", {**point, "expect": expect, "knowledge_id": None})
    allow(toolbox, (1050, 600))
    with pytest.raises(AgentStopped):
        toolbox.call("mouse_click", {**point, "expect": "value_changed", "knowledge_id": None})


def test_preview_of_click_part_gives_the_element_and_the_point(scene):
    toolbox, cli, _ = scene
    before = len(cli.calls)
    target = toolbox.preview("click_part", part_call(mark=3, part="right"))
    assert target["name"] == "라인" and target["points"] == [[1192, 562]]
    assert target["bounds"] == LINE_BOX and target["window_id"] == "w-1"
    assert toolbox.preview("click_part", part_call(mark=99, part="right")) is None
    assert len(cli.calls) == before


def test_preview_of_click_point_gives_the_window_and_the_screen_point(scene):
    toolbox, _, _ = scene
    toolbox.call("look", {"mark": None})
    assert toolbox.preview("click_point", point_call(capture="c1", x=10, y=20)) == {
        "window_id": "w-1", "points": [[1018, 520]]}
    assert toolbox.preview("click_point", point_call(capture="c9", x=10, y=20)) is None


def test_action_is_reported_before_its_observation(scene):
    toolbox, _, observer = scene
    toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened"))
    tool, _, target, result = observer.actions[-1]
    assert tool == "click_part" and target["points"] == [[1192, 562]]
    assert result["expected_met"] is True and "observation" not in result
    assert observer.sources[-1][0] == "action"


ARROW_Y = 542


def _arrow(number, x, native=True):
    node = {"role": "button", "ref_id": f"@s1:e{number}", "available_actions": ["Click"],
            "bounds": {"x": x, "y": ARROW_Y, "width": 24, "height": 24}}
    if native:
        node["native_id"] = {"kind": "automation_id", "value": str(7000 + number)}
    return node


def nameless_scene(native=True):
    return {"windows": [MAIN], "tree": {"role": "window", "name": "메인", "bounds": MAIN_BOUNDS, "children": [
        _arrow(1, 1122, native), _arrow(2, 1272, native), _arrow(3, 1392, native)]}}


def click_ref(ref):
    return {"ref": ref, "intent": "누른다", "knowledge_id": None}


@pytest.mark.parametrize("native", [True, False], ids=["native_id", "position"])
def test_three_nameless_buttons_then_the_third_again_does_not_stop_the_run(tmp_path, native):
    toolbox, cli, _ = toolbox_for(nameless_scene(native), tmp_path)

    for ref in ("@s1:e1", "@s1:e2", "@s1:e3", "@s1:e3"):
        assert toolbox.call("click", click_ref(ref))["ok"]

    assert cli.command_names().count("click") == 4


@pytest.mark.parametrize("native", [True, False], ids=["native_id", "position"])
def test_the_same_nameless_button_a_fourth_time_still_stops_the_run(tmp_path, native):
    toolbox, _, _ = toolbox_for(nameless_scene(native), tmp_path)
    for _ in range(3):
        assert toolbox.call("click", click_ref("@s1:e2"))["ok"]

    with pytest.raises(AgentStopped):
        toolbox.call("click", click_ref("@s1:e2"))


def _shift(scene_tree, dx, dy):
    if "bounds" in scene_tree:
        scene_tree["bounds"] = {**scene_tree["bounds"], "x": scene_tree["bounds"]["x"] + dx,
                                "y": scene_tree["bounds"]["y"] + dy}
    for child in scene_tree.get("children", []):
        _shift(child, dx, dy)


def test_a_nameless_button_is_the_same_after_the_window_moves(tmp_path):
    toolbox, cli, _ = toolbox_for(nameless_scene(native=False), tmp_path)
    for _ in range(3):
        assert toolbox.call("click", click_ref("@s1:e2"))["ok"]
    _shift(cli.scene["tree"], 300, 40)
    cli.scene["windows"][0]["bounds"] = dict(cli.scene["tree"]["bounds"])
    toolbox.call("observe", OBSERVE)

    with pytest.raises(AgentStopped):
        toolbox.call("click", click_ref("@s1:e2"))


def test_a_nameless_button_that_shifts_a_few_pixels_is_the_same(tmp_path):
    toolbox, cli, _ = toolbox_for(nameless_scene(native=False), tmp_path)
    for dx in (0, 5, -4):
        cli.scene["tree"]["children"][1]["bounds"]["x"] = 1272 + dx
        toolbox.call("observe", OBSERVE)
        assert toolbox.call("click", click_ref("@s1:e2"))["ok"]
    cli.scene["tree"]["children"][1]["bounds"]["x"] = 1272 + 7
    toolbox.call("observe", OBSERVE)

    with pytest.raises(AgentStopped):
        toolbox.call("click", click_ref("@s1:e2"))


def test_an_unsafe_failure_on_one_nameless_button_does_not_block_another(tmp_path):
    toolbox, cli, _ = toolbox_for(nameless_scene(native=False), tmp_path)
    cli.action_results["click"] = {"version": "2.6", "ok": False, "command": "click",
                                   "error": {"code": "ACTION_FAILED", "message": "m",
                                             "disposition": {"delivery": "unknown", "retry": "unsafe"}}}
    assert toolbox.call("click", click_ref("@s1:e1"))["ok"] is False
    del cli.action_results["click"]

    assert toolbox.call("click", click_ref("@s1:e2"))["ok"]
    assert "refused" in toolbox.call("click", click_ref("@s1:e1"))


def test_nameless_toggles_are_told_apart_by_native_id(tmp_path):
    toolbox, _, _ = toolbox_for(nameless_scene(native=True), tmp_path)

    assert toolbox.call("toggle", click_ref("@s1:e1"))["ok"]
    assert toolbox.call("toggle", click_ref("@s1:e2"))["ok"]
    assert "refused" in toolbox.call("toggle", click_ref("@s1:e1"))


def test_a_ref_preview_does_not_carry_the_native_id(tmp_path):
    toolbox, _, _ = toolbox_for(nameless_scene(native=True), tmp_path)

    target = toolbox.preview("click", click_ref("@s1:e1"))

    assert target["role"] == "button" and "native_id" not in target


SHOP_MARK, LINE_MARK, GOOD_MARK = 1, 3, 4


def raw_click(window_id="w-1", x=1300, y=530):
    return {"window_id": window_id, "x": x, "y": y, "button": "left", "intent": "누른다", "knowledge_id": None}


def mismatch_twice(toolbox):
    """Two presses that only move the caret while a window was expected: two mismatches in a row."""
    return [toolbox.call("click_part", part_call(mark=mark, part="center", expect="window_opened"))
            for mark in (SHOP_MARK, LINE_MARK)]


def test_marked_elements_are_the_latest_observation_in_mark_order(scene):
    toolbox, cli, _ = scene

    elements = toolbox.marked_elements()

    assert [e["mark"] for e in elements] == list(range(1, 8))
    assert [e["name"] for e in elements] == ["Shop", "조회", "라인", "양품수", "믹싱", "바인더", None]
    assert elements[1]["ref"] == "@s1:e1" and elements[1]["role"] == "button"
    assert elements[2]["bounds"] == LINE_BOX
    cli.scene["trees"] = {"w-1": _single(_node("statictext", "하나뿐", LINE_BOX))["tree"]}
    toolbox.call("observe", OBSERVE)
    assert [(e["mark"], e["name"]) for e in toolbox.marked_elements()] == [(1, "하나뿐")]


def test_marked_elements_are_copies(scene):
    toolbox, _, _ = scene

    first = toolbox.marked_elements()
    first[0]["name"] = "바뀜"
    first[0]["states"].append("checked")
    first[0]["bounds"]["x"] = 0
    first.clear()

    again = toolbox.marked_elements()
    assert again[0]["name"] == "Shop" and again[0]["states"] == [] and again[0]["bounds"]["x"] == 1010
    assert len(again) == 7


def test_current_window_follows_the_observed_window(scene):
    fresh, _ = make()
    assert fresh.current_window is None
    toolbox, cli, _ = scene
    assert toolbox.current_window == "w-1"

    cli.open_window(LIST_WINDOW)
    toolbox.call("observe", {**OBSERVE, "window_id": "w-2"})
    assert toolbox.current_window == "w-2"

    toolbox.call("observe", {**OBSERVE, "window_id": "w-1"})
    assert toolbox.current_window == "w-1"


def test_an_ungated_toolbox_runs_a_raw_point_click_without_a_question(tmp_path):
    toolbox, cli, _ = toolbox_for(SCENE, tmp_path, gated=False)

    result = toolbox.call("mouse_click", raw_click())

    assert "refused" not in result and result["ok"] is True
    assert ["mouse-click", "--xy=1300,530", "--button=left"] in cli.commands()


def test_an_ungated_toolbox_is_not_blocked_after_mismatches(tmp_path):
    toolbox, cli, _ = toolbox_for(SCENE, tmp_path, gated=False)

    first, second = mismatch_twice(toolbox)
    third = toolbox.call("click_part", part_call(mark=GOOD_MARK, part="center", expect="window_opened"))

    assert first["expected_met"] is False and second["expected_met"] is False
    assert first["effects"]["focus"] == {"mark": LINE_MARK, "name": "라인"}
    assert "refused" not in third and third["expected_met"] is True
    assert cli.command_names().count("mouse-click") == 3
    assert toolbox.evidence([GOOD_MARK], None) == {"ok": True, "kind": "text"}


def test_an_ungated_toolbox_keeps_the_level_and_the_process_but_not_the_repeat_guard(tmp_path):
    low, low_cli, _ = toolbox_for(SCENE, tmp_path, level=1, gated=False)
    assert "refused" in low.call("mouse_click", raw_click())
    assert "mouse-click" not in low_cli.command_names()

    toolbox, cli, _ = toolbox_for(SCENE, tmp_path, gated=False)
    other = {"window_id": WINDOW_OTHER_INSTANCE, "combo": "enter", "intent": "누른다", "knowledge_id": None}
    assert "refused" in toolbox.call("press", other)
    assert "press" not in cli.command_names()
    for _ in range(5):
        result = toolbox.call("click_part", part_call(mark=SHOP_MARK, part="center", expect="window_opened"))
        assert "refused" not in result
    assert cli.command_names().count("mouse-click") == 5
    assert "refused" not in toolbox.call("toggle", {"ref": "@s1:e1", "intent": "켠다", "knowledge_id": None})
    assert "refused" not in toolbox.call("toggle", {"ref": "@s1:e1", "intent": "켠다", "knowledge_id": None})
    assert cli.command_names().count("toggle") == 2


def test_a_gated_toolbox_keeps_the_repeat_guard(tmp_path):
    toolbox, cli, _ = toolbox_for(SCENE, tmp_path)
    for _ in range(2):
        toolbox.call("toggle", {"ref": "@s1:e1", "intent": "켠다", "knowledge_id": None})
    assert cli.command_names().count("toggle") == 1
    for _ in range(3):
        toolbox.call("click", {"ref": "@s1:e1", "intent": "누른다", "knowledge_id": None})
    with pytest.raises(AgentStopped):
        toolbox.call("click", {"ref": "@s1:e1", "intent": "누른다", "knowledge_id": None})


def test_a_gated_toolbox_is_unchanged(tmp_path):
    for options in ({}, {"gated": True}):
        toolbox, cli, _ = toolbox_for(SCENE, tmp_path, **options)

        assert toolbox.call("mouse_click", raw_click())["refused_code"] == "needs_confirmation"
        assert "mouse-click" not in cli.command_names()
        first, second = mismatch_twice(toolbox)
        third = toolbox.call("click_part", part_call(mark=GOOD_MARK, part="center", expect="window_opened"))

        assert first["expected_met"] is False and second["expected_met"] is False
        assert third["refused_code"] == "blocked_until_question"
        assert cli.command_names().count("mouse-click") == 2
        assert toolbox.precheck("click_part", part_call(mark=GOOD_MARK, part="center"))["refused_code"] \
            == "blocked_until_question"
        assert toolbox.evidence([GOOD_MARK], None)["refused_code"] == "blocked_until_question"


# --- system profile: id and alias in the Toolbox (plan task 4, K6) ---

MENU_BAR = {"role": "toolbar", "id": "12"}
PLAN_ENTRY = {"id": "60", "role": "image", "in": MENU_BAR, "name": "Execution Plan"}
BAR_ENTRY = {"id": "12", "role": "toolbar", "name": "Menu bar"}
SHIP_BOX = {"x": 1010, "y": 530, "width": 40, "height": 20}
PLAN_BOX = {"x": 1060, "y": 530, "width": 40, "height": 20}


def _id_node(role, native, bounds, children=()):
    return {"role": role, "native_id": {"kind": "automation_id", "value": native}, "bounds": bounds,
            "children": list(children)}


def id_scene(plan_box=PLAN_BOX):
    """A named button, then toolbar 12 holding picture-only items 56 and 60, and toolbar 13 that
    reuses id 56. Every item sits left of 1180, so a click on one only moves the focus."""
    return {"windows": [MAIN], "tree": {"role": "window", "name": "메인", "bounds": MAIN_BOUNDS, "children": [
        _node("button", "조회", {"x": 1300, "y": 520, "width": 80, "height": 24}, ref_id="@s1:e1",
              available_actions=["Click"]),
        _id_node("toolbar", "12", {"x": 1005, "y": 525, "width": 160, "height": 30}, [
            _id_node("image", "56", dict(SHIP_BOX)), _id_node("image", "60", dict(plan_box))]),
        _id_node("toolbar", "13", {"x": 1005, "y": 560, "width": 160, "height": 30}, [
            _id_node("image", "56", {"x": 1010, "y": 565, "width": 40, "height": 20})]),
    ]}}


def menu_profile(*entries):
    from agent.profile import Profile

    return Profile(system="MES", elements=entries or (PLAN_ENTRY, BAR_ENTRY))


def mark_of(toolbox, native, area=None):
    return next(element["mark"] for element in toolbox.marked_elements()
                if (element.get("native_id") or {}).get("value") == native
                and (area is None or element.get("in") == area))


def marks_by_line(toolbox):
    return [(element["mark"], element["role"], (element.get("native_id") or {}).get("value"), element.get("alias"))
            for element in toolbox.marked_elements()]


def test_the_observation_text_shows_id_and_alias_from_the_profile(tmp_path):
    toolbox, _, _ = toolbox_for(id_scene(), tmp_path, profile=menu_profile())

    observation = toolbox.call("observe", OBSERVE)["observation"]

    assert '[2] toolbar id="12" alias="Menu bar"' in observation
    assert '[3] image id="56"\n' in observation
    assert '[4] image id="60" alias="Execution Plan"' in observation
    assert '[5] image id="56"' in observation
    assert '[1] button "조회" @s1:e1' in observation and 'id="' not in observation.split("\n")[1]


def test_without_a_profile_the_toolbox_shows_ids_but_no_alias(tmp_path):
    toolbox, _, _ = toolbox_for(id_scene(), tmp_path)

    observation = toolbox.call("observe", OBSERVE)["observation"]

    assert 'image id="60"' in observation and "alias=" not in observation
    assert 'toolbar id="12"' not in observation, "an unregistered node with children is no element"


def test_click_part_presses_an_id_only_element_by_its_mark(tmp_path):
    toolbox, cli, _ = toolbox_for(id_scene(), tmp_path, profile=menu_profile())
    mark = mark_of(toolbox, "60")

    result = toolbox.call("click_part", part_call(mark=mark, part="center"))

    assert result["ok"] is True
    clicks = [words for words in cli.commands() if words[0] == "mouse-click"]
    assert len(clicks) == 1 and "--xy=1080,540" in clicks[0]


def test_click_part_on_an_id_element_needs_permission_level_two(tmp_path):
    toolbox, cli, _ = toolbox_for(id_scene(), tmp_path, level=1, profile=menu_profile())

    result = toolbox.call("click_part", part_call(mark=mark_of(toolbox, "60"), part="center"))

    assert "refused" in result
    assert "click_part" not in {tool["name"] for tool in toolbox.definitions()}
    assert not [words for words in cli.commands() if words[0] == "mouse-click"]


def test_the_guard_tells_two_unnamed_id_elements_apart(tmp_path):
    toolbox, _, _ = toolbox_for(id_scene(), tmp_path, profile=menu_profile())
    ship, plan = mark_of(toolbox, "56", MENU_BAR), mark_of(toolbox, "60")

    for _ in range(3):
        assert toolbox.call("click_part", part_call(mark=ship, part="center"))["ok"]
    assert toolbox.call("click_part", part_call(mark=plan, part="center"))["ok"]
    with pytest.raises(AgentStopped):
        toolbox.call("click_part", part_call(mark=ship, part="center"))


def test_the_guard_knows_an_id_element_that_moved_in_its_window(tmp_path):
    """The id, not the spot, names an element without a name: a moved item is the same press."""
    toolbox, cli, _ = toolbox_for(id_scene(), tmp_path, profile=menu_profile())
    for left in (1060, 1100, 1140):
        cli.scene = id_scene({**PLAN_BOX, "x": left})
        toolbox.call("observe", OBSERVE)
        assert toolbox.call("click_part", part_call(mark=mark_of(toolbox, "60"), part="center"))["ok"]
    cli.scene = id_scene({**PLAN_BOX, "x": 1020})
    toolbox.call("observe", OBSERVE)

    with pytest.raises(AgentStopped):
        toolbox.call("click_part", part_call(mark=mark_of(toolbox, "60"), part="center"))


def test_marked_elements_carry_native_id_in_and_alias(tmp_path):
    toolbox, _, _ = toolbox_for(id_scene(), tmp_path, profile=menu_profile())

    elements = {element["mark"]: element for element in toolbox.marked_elements()}

    assert elements[4]["native_id"] == {"kind": "automation_id", "value": "60"}
    assert (elements[4]["in"], elements[4]["alias"], elements[4]["kind"]) == (MENU_BAR, "Execution Plan", "id")
    assert elements[3]["in"] == MENU_BAR and "alias" not in elements[3]
    assert elements[5]["in"] == {"role": "toolbar", "id": "13"} and "alias" not in elements[5]
    assert (elements[2]["alias"], "in" in elements[2]) == ("Menu bar", False)
    assert not {"native_id", "in", "alias"} & set(elements[1])


def test_every_read_uses_the_profile(tmp_path):
    """observe, the re-read after an action, wait_for_text, observed_trees and the ready window
    all number the registered toolbar, so a mark means one element everywhere."""
    toolbox, cli, observer = toolbox_for(id_scene(), tmp_path, profile=menu_profile())
    expected = marks_by_line(toolbox)
    assert (2, "toolbar", "12", "Menu bar") in expected

    toolbox.call("click", click_ref("@s1:e1"))
    after_action = marks_by_line(toolbox)
    toolbox.call("wait_for_text", {"text": "조회", "timeout_ms": 0})
    after_wait = marks_by_line(toolbox)
    list(toolbox.observed_trees(source=None))
    after_trees = marks_by_line(toolbox)

    assert after_action == after_wait == after_trees == expected
    assert all('alias="Menu bar"' in text for _window, _envelope, text in observer.observations)


def test_the_ready_window_is_read_with_the_profile(tmp_path):
    cli = FakeCli(scene=id_scene())
    cli.process_alive = False
    observer = EventObserver()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli),
                      Task("goal", EXE, 2, [], ready_text="조회", ready_timeout_s=30),
                      confirm=lambda _prompt: True, sleep=lambda _s: None, clock=lambda: next(clock),
                      observer=observer, captures_dir=tmp_path / "captures", profile=menu_profile())

    assert toolbox.launch_ready()["ok"] is True

    [ready] = [text for (_window, _envelope, text), (source, _wait) in zip(observer.observations, observer.sources)
               if source == "ready"]
    assert '[2] toolbar id="12" alias="Menu bar"' in ready
    assert (2, "toolbar", "12", "Menu bar") in marks_by_line(toolbox)


def test_the_state_before_an_action_reads_with_the_profile(tmp_path):
    """A window last read in part is read again before the action; read without the profile,
    the registered toolbar would count as added by the press."""
    toolbox, _, _ = toolbox_for(id_scene(), tmp_path, profile=menu_profile())
    toolbox.call("observe", {**OBSERVE, "root_ref": "@s1:e1"})

    result = toolbox.call("click", {**click_ref("@s1:e1"), "expect": "any"})

    effects = result["effects"]
    assert (effects["added"]["count"], effects["removed"]["count"]) == (0, 0)


def test_a_click_part_preview_shows_the_alias_but_not_the_id_or_area(tmp_path):
    toolbox, _, _ = toolbox_for(id_scene(), tmp_path, profile=menu_profile())

    target = toolbox.preview("click_part", part_call(mark=mark_of(toolbox, "60"), part="center"))
    plain = toolbox.preview("click_part", part_call(mark=mark_of(toolbox, "56", MENU_BAR), part="center"))

    assert (target["role"], target["alias"], target["points"]) == ("image", "Execution Plan", [[1080, 540]])
    assert not {"native_id", "in"} & set(target)
    assert "alias" not in plain


def test_a_ref_preview_shows_the_alias_of_its_element(tmp_path):
    scene = id_scene()
    scene["tree"]["children"][0] = {**_id_node("button", "7001", {"x": 1300, "y": 520, "width": 80, "height": 24}),
                                    "ref_id": "@s1:e1", "available_actions": ["Click"]}
    toolbox, _, _ = toolbox_for(scene, tmp_path, profile=menu_profile({"id": "7001", "role": "button", "name": "Run"}))

    target = toolbox.preview("click", click_ref("@s1:e1"))

    assert (target["role"], target["alias"]) == ("button", "Run")
    assert not {"native_id", "in"} & set(target)


class ProgressObserver:
    def __init__(self, path):
        from agent.progress import ProgressLog

        self.progress = ProgressLog(path)

    def on_observation(self, *args, **kwargs):
        pass

    def on_action(self, *args, **kwargs):
        pass


def progress_toolbox(tmp_path):
    cli = FakeCli()
    observer = ProgressObserver(tmp_path / "p.jsonl")
    clock = iter(range(0, 10_000))
    desktop = DesktopClient("ad.exe", "run1", runner=cli)
    desktop.set_progress(observer.progress)
    toolbox = Toolbox(desktop, Task("goal", EXE, 3, REQUIRE), confirm=lambda _p: True, sleep=lambda _s: None,
                      clock=lambda: next(clock), observer=observer)
    return toolbox, tmp_path / "p.jsonl"


def progress_of(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_tool_call_is_one_item_with_its_cli_commands_inside(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을\n연다"})
    records = progress_of(path)
    tool = records[0]
    assert (tool["kind"], tool["name"], tool["intent"]) == ("tool", "launch_target", "앱을 연다")
    assert all(r.get("parent") == tool["id"] for r in records if r["kind"] == "cli" and r["phase"] == "start")
    assert (records[-1]["id"], records[-1]["phase"], records[-1]["ok"]) == (tool["id"], "end", True)


def test_a_read_only_tool_is_marked(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    toolbox.call("launch_target", {"intent": "x"})
    toolbox.call("list_windows", {})
    tools = [r for r in progress_of(path) if r["phase"] == "start" and r["kind"] == "tool"]
    assert [r.get("read_only") for r in tools] == [None, True]


def test_a_tool_already_open_with_the_same_name_is_not_opened_twice(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    with toolbox.progress.span("tool", name="launch_target"):
        toolbox.call("launch_target", {"intent": "x"})
    assert len([r for r in progress_of(path) if r["kind"] == "tool" and r["phase"] == "start"]) == 1


def test_a_refused_tool_ends_with_its_refused_code(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    toolbox.call("launch_target", {"intent": "x"})
    toolbox.call("focus_window", {"window_id": WINDOW_OTHER_INSTANCE, "intent": "x"})
    last = progress_of(path)[-1]
    assert (last["phase"], last["kind"], last["ok"]) == ("end", "tool", False)


def test_a_toolbox_without_a_progress_observer_records_nothing():
    toolbox, _ = launched()
    assert toolbox.progress.innermost() is None


def test_a_vision_read_is_a_model_item_inside_the_tool(tmp_path):
    from types import SimpleNamespace

    class Answer:
        tokens = 77

        def as_result(self):
            return {"answer": "x"}

        def as_event(self):
            return {"answer": "x"}

    toolbox, path = progress_toolbox(tmp_path)
    toolbox._vision = SimpleNamespace(read=lambda shot, question, marks: Answer())
    with toolbox.progress.span("tool", name="look"):
        toolbox._ask_vision(SimpleNamespace(id="c1", drawn=False), "무엇이 보이나", "look")
    records = progress_of(path)
    model = next(r for r in records if r["kind"] == "model" and r["phase"] == "start")
    assert (model["name"], model["parent"]) == ("vision", records[0]["id"])
    assert next(r for r in records if r["kind"] == "model" and r["phase"] == "end")["tokens"] == 77
