import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from replay_support import (
    ITEMS,
    STARTS,
    AppOptions,
    StopAfter,
    full_steps,
    make_replay,
    make_values,
    make_workflow,
)

ROOT = Path(__file__).resolve().parents[1]
OBSERVE_MAIN = {"window_id": "w-1", "root_ref": None, "surface": None, "include_bounds": False}


def element(toolbox, name, role=None):
    found = [e for e in toolbox.marked_elements() if e.get("name") == name and (role is None or e["role"] == role)]
    assert len(found) == 1, (name, found)
    return found[0]


def press_ref(toolbox, ref, tool="click", expect=None):
    arguments = {"ref": ref, "intent": "누른다", "knowledge_id": None}
    if expect:
        arguments["expect"] = expect
    return toolbox.call(tool, arguments)


def press_mark(toolbox, mark, expect="any"):
    return toolbox.call("click_part", {"mark": mark, "part": "center", "action": "click", "intent": "누른다",
                                       "knowledge_id": None, "expect": expect})


def launched(tmp_path, **options):
    replay = make_replay(tmp_path, AppOptions(**options))
    replay.toolbox.call("launch_target", {})
    replay.toolbox.call("observe", OBSERVE_MAIN)
    return replay


def row_ids(toolbox):
    return [row["id"] for row in toolbox.call("list_windows", {})["windows"]]


def test_fake_app_lists_a_popup_as_a_row_and_as_a_window_in_the_main_tree(tmp_path):
    replay = launched(tmp_path)
    toolbox = replay.toolbox

    result = press_ref(toolbox, element(toolbox, "메뉴")["ref"], expect="window_opened")

    assert result["expected_met"] is True
    assert row_ids(toolbox) == ["w-menu", "w-1"]
    toolbox.call("observe", OBSERVE_MAIN)
    assert element(toolbox, "메뉴 목록")["role"] == "window"
    assert element(toolbox, "현황")["ref"] == "@s1:e6"


def test_fake_app_row_press_flips_the_check_and_only_the_picture(tmp_path):
    replay = launched(tmp_path, checked=set())
    toolbox = replay.toolbox
    press_ref(toolbox, "@s1:e2")
    toolbox.call("observe", OBSERVE_MAIN)

    result = press_mark(toolbox, element(toolbox, "WA0")["mark"])

    assert replay.app.checked == {"WA0"}
    assert result.get("verdict") == "unconfirmed" and result["effects"]["picture"]["changed"] is True
    toolbox.call("observe", OBSERVE_MAIN)
    again = press_mark(toolbox, element(toolbox, "WA1")["mark"])
    assert replay.app.checked == {"WA0", "WA1"} and again.get("verdict") == "unconfirmed"
    toolbox.call("observe", OBSERVE_MAIN)
    press_mark(toolbox, element(toolbox, "Select")["mark"], expect="window_closed")
    assert replay.app.applied == {"WA0", "WA1"} and row_ids(toolbox) == ["w-1"]


def test_fake_app_dead_rows_change_no_picture(tmp_path):
    replay = launched(tmp_path, dead_rows=True)
    toolbox = replay.toolbox
    press_ref(toolbox, "@s1:e2")
    toolbox.call("observe", OBSERVE_MAIN)

    result = press_mark(toolbox, element(toolbox, "WA0")["mark"])

    assert result["expected_met"] is False and replay.app.checked == {"WA2"}


def test_fake_app_checkbox_rows_show_their_state(tmp_path):
    replay = launched(tmp_path, checkboxes=True)
    toolbox = replay.toolbox
    press_ref(toolbox, "@s1:e2")
    toolbox.call("observe", OBSERVE_MAIN)

    states = {e["name"]: e["states"] for e in toolbox.marked_elements() if e["role"] == "checkbox"}

    assert states == {"WA0": [], "WA1": [], "WA2": ["checked"], "WA3": []}
    press_ref(toolbox, element(toolbox, "WA0")["ref"], tool="check")
    assert replay.app.checked == {"WA0", "WA2"}


def test_fake_app_query_window_closes_on_the_fake_clock(tmp_path):
    replay = launched(tmp_path, query_s=4)
    toolbox = replay.toolbox

    press_ref(toolbox, element(toolbox, "조회")["ref"])
    assert "w-prog" in row_ids(toolbox) and not replay.app.queried
    replay.clock.sleep(4)

    assert row_ids(toolbox) == ["w-1"] and replay.app.queried
    toolbox.call("observe", OBSERVE_MAIN)
    assert element(toolbox, "결과")


def test_fake_app_saves_through_its_own_save_window(tmp_path):
    replay = launched(tmp_path)
    toolbox = replay.toolbox
    path = str(make_values(tmp_path)["save_path"])

    press_ref(toolbox, element(toolbox, "저장")["ref"])
    toolbox.call("observe", {**OBSERVE_MAIN, "window_id": "w-save"})
    toolbox.call("type", {"ref": element(toolbox, "파일 이름", role="edit")["ref"], "text": path,
                          "intent": "넣는다", "knowledge_id": None})
    toolbox.call("press", {"window_id": "w-save", "combo": "enter", "intent": "저장", "knowledge_id": None})

    assert Path(path).read_bytes() == b"saved" and replay.app.saved == [path]
    assert row_ids(toolbox) == ["w-1"]


def test_fake_app_period_text_in_the_main_window_does_nothing(tmp_path):
    replay = launched(tmp_path)
    toolbox = replay.toolbox
    press_ref(toolbox, element(toolbox, "기간 선택")["ref"])
    toolbox.call("observe", OBSERVE_MAIN)

    main_text = [e for e in toolbox.marked_elements() if e.get("name") == "전일"][0]
    press_mark(toolbox, main_text["mark"])
    assert "w-period" in row_ids(toolbox) and replay.app.period == "전일"


def names(toolbox, name):
    return [e for e in toolbox.marked_elements() if e.get("name") == name]


def test_fake_app_options_change_what_the_tree_shows(tmp_path):
    assert len(names(launched(tmp_path, duplicate_menu=True).toolbox, "메뉴")) == 2
    assert names(launched(tmp_path, offscreen_menu=True).toolbox, "메뉴")[0]["states"] == ["offscreen"]
    late = launched(tmp_path, menu_after_s=3)
    assert names(late.toolbox, "메뉴") == []
    late.clock.sleep(3)
    late.toolbox.call("observe", OBSERVE_MAIN)
    assert len(names(late.toolbox, "메뉴")) == 1


def test_fake_app_moves_and_scales_every_box(tmp_path):
    replay = launched(tmp_path, move=(-600, 150), scale=1.5)

    main = next(row for row in replay.app.scene["windows"] if row["id"] == "w-1")
    assert main["bounds"] == {"x": 400, "y": 650, "width": 1200, "height": 900}
    assert element(replay.toolbox, "메뉴")["bounds"] == {"x": 415, "y": 710, "width": 90, "height": 36}


def test_fake_app_menu_variants(tmp_path):
    failing = launched(tmp_path, failing_menu=True)
    assert press_ref(failing.toolbox, "@s1:e1")["ok"] is False and failing.app.presses == []

    delayed = launched(tmp_path, menu_open_delay_s=1)
    press_ref(delayed.toolbox, "@s1:e1")
    assert row_ids(delayed.toolbox) == ["w-1"]
    delayed.clock.sleep(1)
    assert row_ids(delayed.toolbox) == ["w-menu", "w-1"]

    tip = launched(tmp_path, sticky_menu=True, tip_window=True)
    assert row_ids(tip.toolbox) == ["w-tip", "w-1"] and tip.toolbox.current_window == "w-1"
    press_ref(tip.toolbox, "@s1:e1")
    tip.toolbox.call("observe", OBSERVE_MAIN)
    press_ref(tip.toolbox, element(tip.toolbox, "현황")["ref"])
    assert row_ids(tip.toolbox) == ["w-menu", "w-1"]


def test_fake_app_confirm_and_quit_variants(tmp_path):
    confirm = launched(tmp_path, confirm_on_save=True)
    press_ref(confirm.toolbox, element(confirm.toolbox, "저장")["ref"])
    confirm.toolbox.call("press", {"window_id": "w-save", "combo": "enter", "intent": "저장", "knowledge_id": None})
    assert row_ids(confirm.toolbox) == ["w-confirm", "w-1"] and confirm.app.saved == []

    over = launched(tmp_path, confirm_over_save=True)
    press_ref(over.toolbox, element(over.toolbox, "저장")["ref"])
    over.toolbox.call("press", {"window_id": "w-save", "combo": "enter", "intent": "저장", "knowledge_id": None})
    assert row_ids(over.toolbox) == ["w-confirm", "w-save", "w-1"] and over.app.saved == []

    quitting = launched(tmp_path, quit_on_period_button=True)
    press_ref(quitting.toolbox, element(quitting.toolbox, "기간 선택")["ref"])
    assert row_ids(quitting.toolbox) == []


def test_the_fake_clock_fails_a_runaway_wait():
    from replay_support import FakeClock

    clock = FakeClock()
    clock.sleep(3600)
    with pytest.raises(AssertionError):
        clock.sleep(1)


def test_fake_app_never_ends_a_query_when_query_s_is_none(tmp_path):
    replay = launched(tmp_path, query_s=None)
    press_ref(replay.toolbox, element(replay.toolbox, "조회")["ref"])
    replay.clock.sleep(1000)
    assert "w-prog" in row_ids(replay.toolbox) and not replay.app.queried


def test_fake_app_late_progress_window_and_late_popup_element(tmp_path):
    late = launched(tmp_path, query_delay_s=2, query_s=40)
    press_ref(late.toolbox, element(late.toolbox, "조회")["ref"])
    assert row_ids(late.toolbox) == ["w-1"]
    late.clock.sleep(2)
    assert "w-prog" in row_ids(late.toolbox) and not late.app.queried
    late.clock.sleep(40)
    assert row_ids(late.toolbox) == ["w-1"] and late.app.queried

    popup = launched(tmp_path, late_popup_element=True)
    reread = press_ref(popup.toolbox, "@s1:e2")
    assert row_ids(popup.toolbox) == ["w-list", "w-1"] and '"목록"' not in reread["observation"]
    popup.toolbox.call("observe", OBSERVE_MAIN)
    assert element(popup.toolbox, "목록")["role"] == "window"


# --- replay (agent/workflow_run.py, plan task 7) ---------------------------------------------


@pytest.mark.parametrize("item_role", ["statictext", "checkbox"])
def test_the_test_workflows_meet_the_file_format(tmp_path, item_role):
    workflow_format = pytest.importorskip("agent.workflow_format")
    workflow = make_workflow(tmp_path, steps=full_steps(item_role))

    assert workflow_format.check_workflow(workflow) == workflow


def workflow_run():
    from agent import workflow_run as module

    return module


def replay_with(tmp_path, workflow=None, values=None, options=None, stop=None, ready_text=None):
    run = workflow_run()
    replay = make_replay(tmp_path, options or AppOptions(), ready_text=ready_text)
    events = []
    result = run.run_workflow(replay.toolbox, replay.task, workflow or make_workflow(tmp_path),
                              values or make_values(tmp_path), log=events.append, stop=stop,
                              started_at=time.time() - 1)
    return result, replay, [e for e in events if e.get("type") == "workflow_step"]


def stopped(result, code):
    return result.status == "stopped" and result.stats["workflow_run"]["code"] == code


def test_a_passing_replay_is_verified_and_never_calls_a_model(tmp_path):
    result, replay, _ = replay_with(tmp_path)

    assert result.status == "verified"
    assert result.stats["workflow_run"] == {"id": "w-20261119-001", "steps": 12, "steps_done": 12, "code": None}
    assert replay.app.applied == {"WA0", "WA1"} and replay.app.period == "전일" and replay.app.queried
    assert replay.app.saved == [make_values(tmp_path)["save_path"]]
    probe = "import sys, agent.workflow_run; print('agent.llm' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert out.stdout.strip() == "False", out.stderr


def test_each_step_logs_one_workflow_step_event(tmp_path):
    workflow = make_workflow(tmp_path)
    _, _, steps = replay_with(tmp_path, workflow)

    assert [(e["n"], e["kind"], e["ok"], e["code"]) for e in steps] == [
        (s["n"], s["do"], True, None) for s in workflow["steps"]]
    assert [e["goal"] for e in steps] == [s["goal"] for s in workflow["steps"]]
    assert all(isinstance(e["ms"], int) and e["ms"] >= 0 for e in steps)


def test_steps_are_recorded_as_actions_with_the_goal_as_intent(tmp_path):
    _, replay, _ = replay_with(tmp_path)

    acting = [(tool, arguments) for tool, arguments, _ in replay.observer.actions if "intent" in arguments]
    goals = {s["goal"] for s in full_steps()}
    assert acting and all(arguments["intent"] in goals for _, arguments in acting)
    assert all(arguments.get("knowledge_id") is None for _, arguments in acting)
    assert [tool for tool, _ in acting][:3] == ["click", "click", "click"]
    assert {tool for tool, _ in acting} >= {"click", "click_part", "type", "press"}


def test_the_target_is_found_again_after_the_window_moved_and_scaled(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(move=(-600, 150), scale=1.5))

    assert result.status == "verified" and replay.app.applied == {"WA0", "WA1"}


def test_a_target_that_appears_late_is_waited_for(tmp_path):
    late, replay, _ = replay_with(tmp_path, options=AppOptions(menu_after_s=3))
    assert late.status == "verified" and 3 <= replay.clock.slept

    never, replay, steps = replay_with(tmp_path, options=AppOptions(menu_after_s=1000))
    assert stopped(never, "TARGET_NOT_FOUND") and steps[-1]["n"] == 1
    assert 15 <= replay.clock.slept < 20 and replay.app.presses == []


def test_a_window_that_opened_while_the_target_was_awaited_is_not_the_steps(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(menu_after_s=3, late_tip_s=1))

    assert result.status == "verified" and "w-tip" in [row["id"] for row in replay.app.scene["windows"]]


def test_two_matches_stop_at_once(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(duplicate_menu=True))

    assert stopped(result, "TARGET_AMBIGUOUS") and result.stats["workflow_run"]["steps_done"] == 0
    assert replay.app.presses == [] and replay.clock.slept == 0


def test_an_offscreen_target_stops(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(offscreen_menu=True))

    assert stopped(result, "TARGET_OFFSCREEN") and replay.app.presses == []


def test_a_refused_or_failed_action_stops(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(failing_menu=True))

    assert stopped(result, "ACTION_FAILED") and result.stats["workflow_run"]["steps_done"] == 0
    assert "w-menu" not in [row["id"] for row in replay.app.scene["windows"]]


def test_expect_is_polled_until_met(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(menu_open_delay_s=1))

    assert result.status == "verified" and replay.clock.slept >= 1


def test_an_unmet_expect_stops(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(sticky_menu=True))

    assert stopped(result, "EXPECT_UNMET") and result.stats["workflow_run"]["steps_done"] == 1
    assert result.steps == 1 and [e["ok"] for e in steps] == [True, False] and steps[-1]["code"] == "EXPECT_UNMET"
    assert 15 <= replay.clock.slept < 20


def test_window_closed_checks_the_window_its_step_opened(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(sticky_menu=True, tip_window=True))

    assert stopped(result, "EXPECT_UNMET") and steps[-1]["n"] == 2
    assert "w-tip" not in [row["id"] for row in replay.app.scene["windows"]]


def without(step, *keys):
    return {key: value for key, value in step.items() if key not in keys}


def test_window_closed_without_closes_takes_any_row_that_was_there(tmp_path):
    steps = full_steps()
    steps[1] = without(steps[1], "closes")
    workflow = make_workflow(tmp_path, steps=steps)

    passing, _, _ = replay_with(tmp_path, workflow, options=AppOptions(sticky_menu=True, tip_window=True))
    failing, _, steps_logged = replay_with(tmp_path, workflow, options=AppOptions(sticky_menu=True))

    assert passing.status == "verified"
    assert stopped(failing, "EXPECT_UNMET") and steps_logged[-1]["n"] == 2


def test_an_act_without_expect_or_args_only_needs_delivery(tmp_path):
    steps = [without(step, "args") if step.get("args") == {} else step for step in full_steps()]
    steps[1] = without(steps[1], "expect", "closes")
    result, replay, _ = replay_with(tmp_path, make_workflow(tmp_path, steps=steps),
                                    options=AppOptions(sticky_menu=True))

    assert result.status == "verified" and ("click", "현황") in replay.app.presses
    assert replay.clock.slept == 0


def test_settle_waits_for_the_progress_window_then_checks_content(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(query_s=40))

    assert result.status == "verified" and replay.app.queried and 40 <= replay.clock.slept < 60


def test_settle_waits_for_a_progress_window_that_opens_late(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(query_delay_s=2, query_s=40))

    assert result.status == "verified" and replay.app.queried and 42 <= replay.clock.slept < 70


def test_settle_counts_its_check_time_from_when_the_windows_went_away(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(query_s=10, query_changes_nothing=True))

    assert stopped(result, "EXPECT_UNMET") and steps[-1]["n"] == 8
    assert 25 <= replay.clock.slept < 30


def test_settle_passes_when_the_window_was_never_seen(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(query_s=0))

    assert result.status == "verified" and replay.app.queried


def test_settle_times_out(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(query_s=None))

    assert stopped(result, "WAIT_TIMEOUT") and steps[-1]["n"] == 8
    assert 180 <= replay.clock.slept < 200 and replay.app.saved == []


def test_an_unexpected_window_stops(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(confirm_on_save=True))

    assert stopped(result, "UNEXPECTED_WINDOW") and steps[-1]["n"] == 11
    assert replay.app.saved == []


def test_an_unexpected_window_instead_of_the_expected_result_is_not_an_unmet_expect(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(confirm_over_save=True))

    assert stopped(result, "UNEXPECTED_WINDOW") and steps[-1]["n"] == 11 and steps[-1]["code"] == "UNEXPECTED_WINDOW"
    assert result.stats["workflow_run"]["steps_done"] == 10 and replay.app.saved == []


def test_a_window_element_that_appears_after_its_row_is_still_found(tmp_path):
    result, replay, _ = replay_with(tmp_path, options=AppOptions(late_popup_element=True))

    assert result.status == "verified" and replay.app.applied == {"WA0", "WA1"} and replay.app.period == "전일"


def four_lists():
    steps = []
    for index in range(4):
        n = 3 * index + 1
        steps += [
            {**full_steps()[2], "n": n},
            {**full_steps()[3], "n": n + 1, "scope": {"opened_by": n}},
            {**full_steps()[4], "n": n + 2, "target": {**full_steps()[4]["target"], "scope": {"opened_by": n}},
             "closes": n},
        ]
    return steps


def test_four_lists_press_all_and_close_four_times(tmp_path):
    workflow = make_workflow(tmp_path, steps=four_lists(), params={"shop": {"kind": "list", "example": ["WA0", "WA1"]}})
    result, replay, _ = replay_with(tmp_path, workflow, values={"shop": ["WA0", "WA1"]})

    assert result.status == "verified" and result.stats["workflow_run"]["steps_done"] == 12
    labels = [label for _, label in replay.app.list_presses()]
    assert labels.count("All") == 4 and labels.count("Select") == 4 and labels.count("WA2") == 4
    assert replay.app.applied == {"WA0", "WA1"}


def test_scope_keeps_same_named_texts_apart(tmp_path):
    result, replay, _ = replay_with(tmp_path)

    assert result.status == "verified"
    assert ("mouse", "전일") in replay.app.presses and ("mouse", "기간 값") not in replay.app.presses


def test_a_top_level_scope_is_read_on_its_own(tmp_path):
    result, replay, _ = replay_with(tmp_path)

    assert result.status == "verified"
    assert any(c[:3] == ["snapshot", "--window-id", "w-save"] for c in replay.app.commands())
    assert ("type", "파일 이름") in replay.app.presses


def test_a_parameter_name_picks_another_item(tmp_path):
    result, replay, _ = replay_with(tmp_path, values=make_values(tmp_path, period="전주"))

    assert result.status == "verified" and replay.app.period == "전주"


def test_typed_text_and_key_combo_use_the_recorded_tools(tmp_path):
    values = make_values(tmp_path, save_path=str(tmp_path / "out" / "other.xlsx"))
    result, replay, _ = replay_with(tmp_path, values=values)

    assert result.status == "verified"
    assert ["type", "@s1:e30", "--", values["save_path"]] in replay.app.commands()
    assert ["press", "--window-id", "w-save", "enter"] in replay.app.commands()
    assert replay.app.typed == values["save_path"] and replay.app.saved == [values["save_path"]]


@pytest.mark.parametrize("start", sorted(STARTS))
@pytest.mark.parametrize("wanted", [["WA0", "WA1"], ["WA3"]])
def test_select_only_from_any_start_state(tmp_path, start, wanted):
    result, replay, _ = replay_with(tmp_path, values=make_values(tmp_path, shop=wanted),
                                    options=AppOptions(checked=set(STARTS[start])))

    assert result.status == "verified"
    assert replay.app.applied == set(wanted)


def test_select_only_with_every_item_only_presses_all(tmp_path):
    result, replay, _ = replay_with(tmp_path, values=make_values(tmp_path, shop=list(ITEMS)),
                                    options=AppOptions(checked=set()))

    assert result.status == "verified" and replay.app.applied == set(ITEMS)
    assert [label for _, label in replay.app.list_presses()] == ["All", "Select"]


def test_select_only_refuses_an_unknown_value_before_pressing(tmp_path):
    result, replay, steps = replay_with(tmp_path, values=make_values(tmp_path, shop=["WA0", "WA9"]))

    assert stopped(result, "PARAM_VALUE_NOT_FOUND") and steps[-1]["n"] == 4
    assert replay.app.list_presses() == [] and replay.app.checked == {"WA2"}


def test_select_only_is_case_sensitive(tmp_path):
    result, replay, _ = replay_with(tmp_path, values=make_values(tmp_path, shop=["wa0"]))

    assert stopped(result, "PARAM_VALUE_NOT_FOUND") and replay.app.list_presses() == []


def test_select_only_ignores_only_surrounding_spaces(tmp_path):
    result, replay, _ = replay_with(tmp_path, values=make_values(tmp_path, shop=[" WA0 "]))

    assert result.status == "verified" and replay.app.applied == {"WA0"}


def test_select_only_stops_when_a_press_changes_no_picture(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(dead_rows=True))

    assert stopped(result, "EXPECT_UNMET") and steps[-1]["n"] == 4
    assert replay.app.applied is None


def test_select_only_reads_states_when_the_tree_shows_them(tmp_path):
    workflow = make_workflow(tmp_path, steps=full_steps(item_role="checkbox"))
    result, replay, _ = replay_with(tmp_path, workflow, values=make_values(tmp_path, shop=["WA0", "WA1"]),
                                    options=AppOptions(checkboxes=True, checked={"WA1", "WA2"}))

    assert result.status == "verified" and replay.app.applied == {"WA0", "WA1"}
    assert replay.app.list_presses() == [("check", "WA0"), ("uncheck", "WA2"), ("mouse", "Select")]


def test_select_only_stops_when_a_checkbox_keeps_its_state(tmp_path):
    workflow = make_workflow(tmp_path, steps=full_steps(item_role="checkbox"))
    result, replay, steps = replay_with(tmp_path, workflow, options=AppOptions(checkboxes=True, dead_rows=True))

    assert stopped(result, "EXPECT_UNMET") and steps[-1]["n"] == 4 and replay.app.applied is None


def test_select_only_stops_on_a_window_a_row_opened(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(alert_on_row=True))

    assert stopped(result, "UNEXPECTED_WINDOW") and steps[-1]["n"] == 4 and replay.app.applied is None


def picture_steps():
    steps = full_steps()
    steps[3] = {"n": 4, "goal": "WA0 줄이 체크됨", "do": "act", "tool": "click_part",
                "args": {"part": "center", "action": "click"},
                "target": {"scope": {"opened_by": 3}, "role": "statictext", "name": "WA0"},
                "expect": "picture_changed"}
    return steps


def test_a_picture_change_step_is_judged_once_from_the_action(tmp_path):
    workflow = make_workflow(tmp_path, steps=picture_steps(), params={
        "period": {"kind": "text", "example": "전일"},
        "save_path": {"kind": "path", "example": str(tmp_path / "out" / "front.xlsx")}})
    values = make_values(tmp_path)
    del values["shop"]

    passing, replay, _ = replay_with(tmp_path, workflow, values)
    assert passing.status == "verified" and replay.app.applied == {"WA0", "WA2"}

    unmet, replay, steps = replay_with(tmp_path, workflow, values, options=AppOptions(dead_rows=True))
    assert stopped(unmet, "EXPECT_UNMET") and steps[-1]["n"] == 4 and replay.clock.slept == 0


def test_check_file_needs_a_file_written_after_the_start(tmp_path):
    missing, replay, steps = replay_with(tmp_path, options=AppOptions(save_writes_nothing=True))
    assert stopped(missing, "FILE_NOT_SAVED") and steps[-1]["n"] == 12

    values = make_values(tmp_path, save_path=str(tmp_path / "out" / "old.xlsx"))
    Path(values["save_path"]).write_bytes(b"old")
    os.utime(values["save_path"], (time.time() - 3600, time.time() - 3600))
    old, _, _ = replay_with(tmp_path, values=values, options=AppOptions(save_writes_nothing=True))
    assert stopped(old, "FILE_NOT_SAVED")


def test_the_app_closing_mid_run_stops_at_once(tmp_path):
    result, replay, steps = replay_with(tmp_path, options=AppOptions(quit_on_period_button=True))

    assert stopped(result, "APP_GONE") and steps[-1]["n"] == 6
    assert replay.clock.slept < 15


def test_stop_signal_is_heard_between_steps(tmp_path):
    run = workflow_run()
    replay = make_replay(tmp_path)
    events = []
    result = run.run_workflow(replay.toolbox, replay.task, make_workflow(tmp_path), make_values(tmp_path),
                              log=events.append, stop=StopAfter(replay.app, 2), started_at=time.time() - 1)

    assert stopped(result, "STOPPED_BY_USER") and result.stats["workflow_run"]["steps_done"] == 2
    assert len(replay.app.presses) == 2 and replay.app.process_alive
    assert "close-app" not in replay.app.command_names() and not any(c[0] == "taskkill" for c in replay.app.calls)


def test_a_failed_ready_wait_stops_with_its_code(tmp_path):
    result, replay, steps = replay_with(tmp_path, ready_text="메뉴")

    assert stopped(result, "READY_ALREADY_RUNNING") and result.stats["workflow_run"]["steps_done"] == 0
    assert replay.app.presses == [] and steps == []


class FakeStore:
    def __init__(self):
        self.calls = []

    def note_pass(self, workflow_id, other):
        self.calls.append(("pass", workflow_id, other))
        return {}

    def note_failure(self, workflow_id):
        self.calls.append(("failure", workflow_id))
        return {}


def test_record_outcome_updates_the_store(tmp_path):
    run = workflow_run()
    from agent.loop import RunResult

    workflow = make_workflow(tmp_path)
    store = FakeStore()
    stats = {"workflow_run": {"id": workflow["id"], "steps": 12, "steps_done": 12, "code": None}}
    example = make_values(tmp_path)
    other = make_values(tmp_path, shop=["WA3"])

    run.record_outcome(store, workflow, example, RunResult("verified", 12, "workflow 통과", stats=stats))
    run.record_outcome(store, workflow, other, RunResult("verified", 12, "workflow 통과", stats=stats))
    run.record_outcome(store, workflow, example, RunResult("stopped", 3, "EXPECT_UNMET", stats=stats))

    assert store.calls == [("pass", workflow["id"], False), ("pass", workflow["id"], True),
                           ("failure", workflow["id"])]
