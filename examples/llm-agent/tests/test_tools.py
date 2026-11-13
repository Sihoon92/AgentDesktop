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


def toolbox_for(scene, tmp_path, image_mode="user_message", level=2):
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
