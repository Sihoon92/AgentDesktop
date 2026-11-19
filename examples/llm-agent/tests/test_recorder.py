import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

import pytest
from fake_cli import FakeCli, scene_window
from PIL import Image
from test_loop import OBSERVE, ScriptedModel, call, settings_call

from agent import capture
from agent.desktop import DesktopClient
from agent.loop import RunResult, run_agent
from agent.process_identity import process_started_at
from agent.profile import Profile
from agent.recorder import RunRecorder
from agent.task import Task
from agent.tools import Toolbox

REQUIRE = [
    {"role": "switch", "description_contains": "설정 패널", "states": ["checked"]},
    {"role": "statictext", "name": "지표"},
]
TASK = Task("설정 패널을 연다", r"C:\apps\FootprintChart.exe", 3, REQUIRE)
META = {"goal": TASK.goal, "exe": TASK.exe, "model": "gpt-test", "permission_level": 3, "started_by": "run.py"}
NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
SETTINGS_RECT = {"x": 1775.5, "y": 118.5, "width": 108.0, "height": 45.0}

PANEL_LABEL = 'statictext "지표"'

OPEN_RECORDERS = []


@pytest.fixture(autouse=True)
def no_heartbeat_thread_left():
    yield
    recorders = list(OPEN_RECORDERS)
    OPEN_RECORDERS.clear()
    for recorder in recorders:
        recorder.close()
    assert not [r.run_id for r in recorders if r._heartbeat_thread.is_alive()]


class Clock:
    """Each call is one second after the last, starting at NOW."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return NOW + timedelta(seconds=self.calls - 1)


def new_recorder(tmp_path, desktop, now=lambda: NOW, heartbeat_s=60):
    recorder = RunRecorder(tmp_path / "runs", desktop, META, now=now, heartbeat_s=heartbeat_s)
    OPEN_RECORDERS.append(recorder)
    return recorder


def recorded(tmp_path, cli=None, now=lambda: NOW, heartbeat_s=60):
    cli = cli or FakeCli()
    desktop = DesktopClient("ad.exe", "run1", runner=cli)
    recorder = new_recorder(tmp_path, desktop, now, heartbeat_s)
    toolbox = Toolbox(desktop, TASK, confirm=lambda _p: False, sleep=lambda _s: None, observer=recorder)
    return recorder, toolbox, cli


def events(recorder):
    lines = (recorder.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def run_json(recorder, attempts=50):
    """Reads run.json like any reader of a live run: on Windows, opening it at the moment the
    heartbeat thread replaces it is refused with PermissionError, so a refused read is retried."""
    path = recorder.path / "run.json"
    for _ in range(attempts - 1):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            time.sleep(0.01)
    return json.loads(path.read_text(encoding="utf-8"))


def settings_element(observation):
    return next(e for e in observation["elements"] if e["name"] == "설정 ◂")


def full_run_model():
    return ScriptedModel(
        [
            [call("launch_target", {"intent": "앱을 연다"})],
            [call("plan", {"stages": [{"goal": "설정 패널이 열려 있다", "done_when": "지표가 보인다"}]})],
            [call("observe", OBSERVE)],
            settings_call,
            [call("stage_done", {"marks": [1], "capture": None, "note": "패널이 보인다"})],
            [call("finish", {"success": True, "evidence": "opened"})],
        ]
    )


def test_observation_writes_frame_and_image_space_boxes(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})

    toolbox.call("observe", OBSERVE)

    first = events(recorder)[1]
    assert first["type"] == "observation"
    assert first["id"] == 1
    assert first["frame"] == "0001.png"
    assert (recorder.path / "frames" / "0001.png").read_bytes().startswith(b"\x89PNG")
    assert first["image"] == {"width": 1896, "height": 2060}
    assert first["geometry_stale"] is False
    assert first["window"]["id"] == "w-3412692"
    assert settings_element(first)["rect"] == SETTINGS_RECT
    assert settings_element(first)["kind"] == "control"
    assert first["text_truncated"] is False
    assert 'switch "설정 ◂"' in first["text"]


def test_action_event_links_to_the_observation_it_acted_on(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    toolbox.call("observe", OBSERVE)
    ref = settings_element(events(recorder)[1])["ref"]

    toolbox.call("toggle", {"ref": ref, "intent": "설정 패널을 연다"})

    kinds = [(event["type"], event.get("id")) for event in events(recorder)]
    assert kinds == [("action", None), ("observation", 1), ("action", None), ("observation", 2)]
    action = events(recorder)[2]
    assert action["before"] == 1
    assert action["tool"] == "toggle"
    assert action["intent"] == "설정 패널을 연다"
    assert action["arguments"] == {"ref": ref, "intent": "설정 패널을 연다"}
    assert action["target"]["rect"] == SETTINGS_RECT
    result = dict(action["result"])
    assert result.pop("effects")["added"]["count"] > 0
    assert result == {
        "ok": True,
        "disposition": {"delivery": "delivered_verified", "retry": "unsafe"},
        "post_state": {"enabled": True, "offscreen": False, "role": "switch", "states": ["checked"], "value": ""},
    }
    launch = events(recorder)[0]
    assert launch["before"] is None
    assert set(launch["result"]) <= {"ok", "disposition", "code", "message", "refused", "post_state"}


def test_point_and_window_actions_carry_image_points(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    toolbox.call("observe", OBSERVE)

    toolbox.call("mouse_click", {"window_id": "w-3412692", "x": 2000, "y": 100, "button": "left", "intent": "누른다"})
    toolbox.call("press", {"window_id": "w-3412692", "combo": "escape", "intent": "닫는다"})

    click, press = [e for e in events(recorder) if e["type"] == "action"][1:]
    assert click["target"] == {"window_id": "w-3412692", "points": [[2000, 100]], "image_points": [[70.5, 100.5]]}
    assert press["target"] == {"window_id": "w-3412692", "points": [], "image_points": []}


def test_moved_window_marks_frame_stale(tmp_path):
    cli = FakeCli()
    cli.move_window_on_screenshot = True
    recorder, toolbox, _ = recorded(tmp_path, cli)
    toolbox.call("launch_target", {"intent": "앱을 연다"})

    toolbox.call("observe", OBSERVE)

    assert events(recorder)[1]["geometry_stale"] is True


def test_screenshot_failure_does_not_stop_the_run(tmp_path):
    cli = FakeCli()
    cli.screenshot_result = {
        "version": "2.6",
        "ok": False,
        "command": "screenshot",
        "error": {"code": "WINDOW_NOT_FOUND", "message": "minimized"},
    }
    recorder, toolbox, _ = recorded(tmp_path, cli)

    result = run_agent(full_run_model(), toolbox, TASK, recorder.on_event)

    observations = [e for e in events(recorder) if e["type"] == "observation"]
    assert result.status == "verified"
    assert observations and all(e["frame"] is None for e in observations)
    assert "WINDOW_NOT_FOUND" in observations[0]["warning"]
    assert settings_element(observations[0])["rect"] is None


def test_run_json_reflects_final_status(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    started = run_json(recorder)
    assert started["status"] == "running"
    assert started["run_id"] == "20261107-120000"
    assert started["started_at"] == "2026-11-07T12:00:00Z"
    assert started["goal"] == TASK.goal

    result = run_agent(full_run_model(), toolbox, TASK, recorder.on_event)
    recorder.finish(result)

    final = run_json(recorder)
    assert final["status"] == "verified"
    assert final["steps"] == 6
    assert final["tokens"] == 600
    assert final["reason"] == "opened"
    assert (final["stages"]["stage_count"], final["stages"]["stages_done"]) == (1, 1)
    assert events(recorder)[-1] == {"type": "finish", "status": "verified", "steps": 6,
                                    "reason": "opened", "tokens": 600}
    assert [e["step"] for e in events(recorder) if e["type"] == "model"] == [1, 2, 3, 4, 5, 6]


def test_two_runs_in_the_same_second_get_distinct_ids(tmp_path):
    desktop = DesktopClient("ad.exe", "run1", runner=FakeCli())

    first = new_recorder(tmp_path, desktop)
    second = new_recorder(tmp_path, desktop)

    assert first.run_id == "20261107-120000"
    assert second.run_id == "20261107-120000-2"
    assert first.path != second.path
    assert run_json(second)["run_id"] == "20261107-120000-2"


def test_recorder_errors_never_reach_the_loop(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    shutil.rmtree(recorder.path)

    result = run_agent(full_run_model(), toolbox, TASK, recorder.on_event)
    recorder.finish(result)

    assert result.status == "verified"
    assert recorder.errors


def test_proposal_gets_the_image_rect_and_decision_is_kept(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    toolbox.call("observe", OBSERVE)
    ref = settings_element(events(recorder)[1])["ref"]
    arguments = {"ref": ref, "intent": "설정 패널을 연다"}

    recorder.on_event({"type": "proposal", "id": "p2", "step": 3, "tool": "check", "intent": arguments["intent"],
                       "arguments": arguments, "target": toolbox.preview("check", arguments)})
    recorder.on_event({"type": "decision", "id": "p2", "approved": False, "comment": "아직"})

    proposal, decision = events(recorder)[-2:]
    assert proposal["type"] == "proposal"
    assert proposal["id"] == "p2"
    assert proposal["arguments"] == arguments
    assert proposal["target"]["rect"] == SETTINGS_RECT
    assert decision == {"type": "decision", "id": "p2", "approved": False, "comment": "아직"}


def window_row():
    return {"id": "w-3412692", "title": "t", "bounds": {"x": 1920.0, "y": 0.0, "width": 1915.0, "height": 2069.0}}


def labels_tree(count, label_bounds=None):
    children = [{"role": "statictext", "name": f"라벨 {n}", "bounds": label_bounds} for n in range(count)]
    children.append({"role": "button", "name": "끝", "ref_id": "@s9:e1"})
    return {"version": "2.6", "ok": True, "command": "snapshot",
            "data": {"tree": {"role": "window", "name": "창", "children": children}}}


def test_open_panel_text_is_recorded_as_text_elements(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path, FakeCli(panel_open=True))
    toolbox.call("launch_target", {"intent": "앱을 연다"})

    seen = toolbox.call("observe", OBSERVE)["observation"]

    observation = events(recorder)[1]
    label = next(e for e in observation["elements"] if e["name"] == "지표")
    assert label == {"kind": "text", "ref": None, "role": "statictext", "name": "지표",
                     "description": label["description"], "states": [], "value": None, "rect": None,
                     "mark": label["mark"], "scope": None}
    assert isinstance(label["mark"], int)
    controls = [e for e in observation["elements"] if e["kind"] == "control"]
    assert len(controls) == 43
    assert all(e["ref"] for e in controls)
    assert observation["text_truncated"] is False
    assert observation["text"] == seen


def test_text_elements_map_to_image_rects(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_observation(window_row(), labels_tree(1, {"x": 3600.0, "y": 160.0, "width": 40.0, "height": 24.0}), "t")

    label = events(recorder)[0]["elements"][0]
    assert label["kind"] == "text"
    assert label["rect"] == {"x": 1670.5, "y": 160.5, "width": 40.0, "height": 24.0}


def test_text_elements_stop_at_400_and_say_so(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_observation(window_row(), labels_tree(405), "t")

    observation = events(recorder)[0]
    texts = [e for e in observation["elements"] if e["kind"] == "text"]
    assert len(texts) == 400
    assert texts[-1]["name"] == "라벨 399"
    assert observation["elements"][-1]["ref"] == "@s9:e1"
    assert observation["text_truncated"] is True


def test_observation_carries_the_kinds_of_the_whole_tree_even_when_text_is_cut(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    envelope = labels_tree(405, {"x": 2000.0, "y": 160.0, "width": 40.0, "height": 24.0})
    envelope["data"]["complete"] = False

    recorder.on_observation(window_row(), envelope, "t")

    observation = events(recorder)[0]
    assert observation["text_truncated"] is True
    assert observation["kinds"] == {"named_ref": 1, "unnamed_ref": 0, "unnamed_ref_with_id": 0, "text": 405,
                                    "blank": 0, "id_only": 0, "nodes": 407, "depth": 2}
    assert observation["complete"] is False


def test_observation_complete_is_null_when_the_snapshot_does_not_say(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_observation(window_row(), labels_tree(1), "t")

    observation = events(recorder)[0]
    assert observation["complete"] is None and observation["kinds"]["named_ref"] == 1


def test_observed_snapshots_record_kinds_and_complete(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})

    toolbox.call("observe", OBSERVE)

    observation = events(recorder)[1]
    controls = [e for e in observation["elements"] if e["kind"] == "control"]
    assert observation["complete"] is True
    assert observation["kinds"]["named_ref"] + observation["kinds"]["unnamed_ref"] == len(controls) == 42


def test_wait_for_text_records_one_observation_with_one_screenshot(tmp_path):
    cli = FakeCli()
    cli.open_panel_on_snapshot = 3
    recorder, toolbox, _ = recorded(tmp_path, cli)
    toolbox.call("launch_target", {"intent": "앱을 연다"})

    result = toolbox.call("wait_for_text", {"text": PANEL_LABEL, "timeout_ms": 20000})

    observations = [e for e in events(recorder) if e["type"] == "observation"]
    assert len(observations) == 1
    [observation] = observations
    assert observation["source"] == "wait_for_text"
    assert {k: observation["wait"][k] for k in ("text", "found", "polls")} == {"text": PANEL_LABEL, "found": True, "polls": 3}
    assert observation["text"] == result["observation"]
    assert cli.command_names().count("screenshot") == 1
    assert [p.name for p in (recorder.path / "frames").iterdir()] == ["0001.png"]


def test_timed_out_wait_is_one_observation_marked_not_found(tmp_path):
    recorder, toolbox, cli = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})

    toolbox.call("wait_for_text", {"text": "없는글자", "timeout_ms": 2000})

    observations = [e for e in events(recorder) if e["type"] == "observation"]
    assert len(observations) == 1
    assert observations[0]["wait"]["found"] is False
    assert cli.command_names().count("screenshot") == 1


def test_observe_and_action_observations_carry_their_source(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    toolbox.call("observe", OBSERVE)
    ref = settings_element(events(recorder)[1])["ref"]

    toolbox.call("toggle", {"ref": ref, "intent": "설정 패널을 연다"})

    observations = [e for e in events(recorder) if e["type"] == "observation"]
    assert [e["source"] for e in observations] == ["observe", "action"]
    assert all("wait" not in e for e in observations)


def wait_until(condition, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


def test_run_json_is_format_2_with_process_identity(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    started = run_json(recorder)

    assert started["format"] == 2
    assert started["started_by"] == "run.py"
    assert started["pid"] == os.getpid()
    assert started["process_started_at"] == process_started_at(os.getpid())
    assert started["heartbeat_at"] == "2026-11-07T12:00:00Z"
    assert started["finished_at"] is None
    assert started["status"] == "running"


def test_writing_an_event_moves_the_heartbeat(tmp_path):
    recorder, _, _ = recorded(tmp_path, now=Clock())
    before = run_json(recorder)["heartbeat_at"]

    recorder.on_event({"type": "decision", "id": "p1", "approved": True, "comment": ""})

    assert run_json(recorder)["heartbeat_at"] > before
    assert run_json(recorder)["started_at"] == "2026-11-07T12:00:00Z"


def test_heartbeat_moves_without_events_after_the_interval(tmp_path):
    recorder, _, _ = recorded(tmp_path, now=Clock(), heartbeat_s=0.01)
    before = run_json(recorder)["heartbeat_at"]

    wait_until(lambda: run_json(recorder)["heartbeat_at"] > before)

    assert recorder._heartbeat_thread.daemon
    assert not recorder.errors


def test_finish_sets_finished_at_and_stops_the_heartbeat(tmp_path):
    recorder, _, _ = recorded(tmp_path, now=Clock(), heartbeat_s=0.01)

    recorder.finish(RunResult("verified", 4, "opened", 400))

    final = run_json(recorder)
    assert final["finished_at"] == final["heartbeat_at"]
    assert final["finished_at"] > final["started_at"]
    assert not recorder._heartbeat_thread.is_alive()
    recorder._heartbeat()
    time.sleep(0.05)
    assert run_json(recorder) == final


def test_heartbeat_write_failure_stays_inside_the_recorder(tmp_path):
    recorder, _, _ = recorded(tmp_path, now=Clock(), heartbeat_s=0.01)
    shutil.rmtree(recorder.path)

    recorder._heartbeat()
    wait_until(lambda: len(recorder.errors) >= 2)

    assert recorder._heartbeat_thread.is_alive()
    assert all(error.startswith("_heartbeat") for error in recorder.errors)


def test_run_json_write_retries_while_a_reader_holds_the_file(tmp_path, monkeypatch):
    recorder, _, _ = recorded(tmp_path)
    real_replace = os.replace
    attempts = []

    def busy_twice(source, target):
        attempts.append(target)
        if len(attempts) <= 2:
            raise PermissionError(13, "file in use")
        real_replace(source, target)

    monkeypatch.setattr("agent.recorder.os.replace", busy_twice)
    recorder.finish(RunResult("verified", 4, "opened", 400))

    assert run_json(recorder)["status"] == "verified"
    assert len(attempts) >= 3
    assert not recorder.errors


def test_elements_carry_states_and_value(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    toolbox.call("observe", OBSERVE)
    ref = settings_element(events(recorder)[1])["ref"]

    toolbox.call("check", {"ref": ref, "intent": "설정 패널을 연다"})

    before, after = [e for e in events(recorder) if e["type"] == "observation"]
    assert settings_element(before)["states"] == []
    assert settings_element(before)["value"] is None
    opened = next(e for e in after["elements"] if e["name"] == "설정 ▸")
    assert opened["states"] == ["checked"]
    assert all(isinstance(e["states"], list) and "value" in e for e in after["elements"])


def baseline_run(tmp_path, target, open_on=None, timeout_s=30):
    from agent.baseline import run_baseline

    cli = FakeCli()
    cli.open_panel_on_snapshot = open_on
    desktop = DesktopClient("ad.exe", "run1", runner=cli)
    recorder = new_recorder(tmp_path, desktop)
    task = Task("g", TASK.exe, 3, REQUIRE, baseline_target=target)
    ticks = iter(range(0, 10_000))
    toolbox = Toolbox(desktop, task, confirm=lambda _p: False, sleep=lambda _s: None,
                      clock=lambda: next(ticks), observer=recorder)
    return run_baseline(toolbox, task, timeout_s=timeout_s), recorder, cli


def test_baseline_records_one_wait_observation_after_three_polls(tmp_path):
    result, recorder, cli = baseline_run(tmp_path, {"role": "switch", "name": "설정 ▸"}, open_on=5)
    # Two target windows, so each poll reads two trees: read 5 is poll 3.

    observations = [e for e in events(recorder) if e["type"] == "observation"]
    waits = [e for e in observations if e["source"] == "wait_for_text"]
    assert result.status == "verified"
    assert [e["source"] for e in observations] == ["wait_for_text", "action"]
    assert waits[0]["wait"]["found"] is True
    assert waits[0]["wait"]["polls"] == 3
    assert waits[0]["wait"]["text"] == "switch 설정 ▸"
    assert waits[0]["wait"]["waited_ms"] >= 0
    assert cli.command_names().count("screenshot") == 2
    assert any(e["name"] == "설정 ▸" for e in waits[0]["elements"])


def test_baseline_that_never_finds_its_element_records_one_observation(tmp_path):
    target = {"role": "button", "name": "없는 버튼"}
    result, recorder, cli = baseline_run(tmp_path, target, timeout_s=5)

    observations = [e for e in events(recorder) if e["type"] == "observation"]
    assert result.status == "stopped"
    assert len(observations) == 1
    assert observations[0]["wait"]["found"] is False
    assert observations[0]["wait"]["polls"] > 1
    assert observations[0]["wait"]["text"] == "button 없는 버튼"
    assert cli.command_names().count("screenshot") == 1


def test_baseline_wait_label_is_the_element_condition():
    from agent.baseline import target_label

    target = {"role": "switch", "description_contains": "설정 패널", "states": ["checked"]}
    assert target_label(target) == "switch 설정 패널 checked"


def _settings_ref(observation):
    import re

    return re.search(r'switch "설정 .*?" (@\S+)', observation).group(1)


def test_capture_event_is_written_with_the_current_step_and_observation(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {})
    toolbox.call("observe", OBSERVE)
    recorder.on_event({"type": "model", "step": 4, "tokens": 10})
    capture = {"type": "capture", "id": "c1", "step": None, "observation": None, "mark": 3,
               "reason": "look", "file": "captures/c1.png"}

    recorder.on_event(capture)

    assert events(recorder)[-1] == {**capture, "step": 4, "observation": 1}


def test_capture_event_keeps_the_step_and_observation_it_was_given(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    capture = {"type": "capture", "id": "c2", "step": 2, "observation": 7, "mark": None,
               "reason": "mismatch", "file": "captures/c2.png"}

    recorder.on_event(capture)

    assert events(recorder)[-1] == capture


def test_action_event_carries_the_expectation_and_its_effects(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {})
    observed_text = toolbox.call("observe", OBSERVE)["observation"]
    ref = _settings_ref(observed_text)

    toolbox.call("check", {"ref": ref, "intent": "연다", "expect": "content_changed", "knowledge_id": None})

    action = [e for e in events(recorder) if e["type"] == "action"][-1]
    assert action["result"]["expect"] == "content_changed"
    assert action["result"]["expected_met"] is True
    assert action["result"]["effects"]["added"]["count"] > 0
    assert [e["type"] for e in events(recorder)][-2:] == ["action", "observation"]


def test_action_without_expect_records_effects_only(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {})
    ref = _settings_ref(toolbox.call("observe", OBSERVE)["observation"])

    toolbox.call("check", {"ref": ref, "intent": "연다"})

    result = [e for e in events(recorder) if e["type"] == "action"][-1]["result"]
    assert "effects" in result and "expect" not in result and "expected_met" not in result


SETTINGS_BOUNDS = {"x": 3705.0, "y": 118.0, "width": 108.0, "height": 45.0}
SETTINGS_ORIGIN = (3705.0 - SETTINGS_RECT["x"], 118.0 - SETTINGS_RECT["y"])


def observed_recorder(tmp_path):
    recorder, toolbox, cli = recorded(tmp_path)
    toolbox.call("launch_target", {})
    toolbox.call("observe", OBSERVE)
    return recorder, toolbox, cli


def test_question_event_gets_image_rects_and_the_observation_it_was_asked_on(tmp_path):
    recorder, _, _ = observed_recorder(tmp_path)
    recorder.on_event({"type": "model", "step": 2, "tokens": 5})
    point = [3715, 128]
    recorder.on_event({
        "type": "question", "id": "q1", "step": None, "kind": "confirm", "text": "이 칸이 맞나요?",
        "candidates": [{"n": 1, "label": "설정", "mark": 4, "bounds": SETTINGS_BOUNDS, "point": None},
                       {"n": 2, "label": "찍은 곳", "mark": None, "bounds": None, "point": point}],
        "proposed": 1, "basis": [{"mark": 4, "bounds": SETTINGS_BOUNDS}], "items": [], "window_id": "w-3412692"})

    event = events(recorder)[-1]
    assert (event["step"], event["observation"]) == (2, 1)
    first, second = event["candidates"]
    assert first["rect"] == SETTINGS_RECT and first["image_point"] is None
    assert second["rect"] is None
    assert second["image_point"] == [point[0] - SETTINGS_ORIGIN[0], point[1] - SETTINGS_ORIGIN[1]]
    assert event["basis"][0]["rect"] == SETTINGS_RECT
    assert first["bounds"] == SETTINGS_BOUNDS


def test_answer_pin_gets_its_image_point(tmp_path):
    recorder, _, _ = observed_recorder(tmp_path)
    recorder.on_event({"type": "answer", "id": "q1", "text": "여기", "accepted": None, "items": [],
                       "timed_out": False, "pin": {"point": [3715, 128], "image_point": None, "mark": 4}})

    pin = events(recorder)[-1]["pin"]
    assert pin["image_point"] == [3715 - SETTINGS_ORIGIN[0], 128 - SETTINGS_ORIGIN[1]] and pin["mark"] == 4


def test_answer_without_a_pin_is_kept_as_is(tmp_path):
    recorder, _, _ = observed_recorder(tmp_path)
    answer = {"type": "answer", "id": "q1", "text": "맞아", "pin": None, "accepted": None, "items": [],
              "timed_out": False}
    recorder.on_event(answer)
    assert events(recorder)[-1] == answer


@pytest.mark.parametrize("event", [
    {"type": "knowledge_loaded", "system": "mes", "ids": ["r-1"], "truncated": False},
    {"type": "knowledge_used", "step": 3, "id": "r-1", "text": "오른쪽 끝을 눌러 연다"},
    {"type": "knowledge_failed", "step": 3, "id": "r-1"},
    {"type": "completion", "by": "knowledge", "id": "c-1", "met": True, "detail": "파일 확인: C:\\out.xlsx"},
    {"type": "registration", "saved": [{"id": "r-2", "type": "resolution", "action": "add"}], "rejected": 0},
])
def test_knowledge_completion_and_registration_events_are_written(tmp_path, event):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event(event)
    assert events(recorder)[-1] == event


def test_run_json_holds_no_question_answer_knowledge_or_completion_text(tmp_path):
    recorder, _, _ = observed_recorder(tmp_path)
    recorder.on_event({"type": "question", "id": "q1", "step": 1, "kind": "stuck", "text": "비밀질문글",
                       "candidates": [{"n": 1, "label": "비밀후보", "mark": None, "bounds": None, "point": None}],
                       "proposed": None, "basis": [], "items": [], "window_id": None})
    recorder.on_event({"type": "answer", "id": "q1", "text": "비밀답글", "pin": None, "accepted": None,
                       "items": [], "timed_out": False})
    recorder.on_event({"type": "knowledge_used", "step": 1, "id": "r-1", "text": "비밀지식글"})
    recorder.on_event({"type": "completion", "by": "knowledge", "id": "c-1", "met": True, "detail": "비밀경로"})
    recorder.finish(RunResult("verified", 3, "done", 10))

    text = (recorder.path / "run.json").read_text(encoding="utf-8")
    for secret in ("비밀질문글", "비밀후보", "비밀답글", "비밀지식글", "비밀경로"):
        assert secret not in text
    assert "비밀질문글" in (recorder.path / "events.jsonl").read_text(encoding="utf-8")


def test_policy_refusal_and_mismatch_capture_reach_the_action_result(tmp_path):
    recorder, toolbox, _ = observed_recorder(tmp_path)
    toolbox.call("mouse_click", {"window_id": "w-3412692", "x": 2000, "y": 100, "button": "left",
                                 "intent": "누른다", "expect": "any", "knowledge_id": None})

    action = [e for e in events(recorder) if e["type"] == "action"][-1]
    capture = events(recorder)[-1]
    assert action["result"]["refused_code"] == "needs_confirmation"
    assert capture["type"] == "capture" and action["result"]["capture"] == capture["id"]
    assert capture["reason"] == "question"


def test_run_json_names_the_system(tmp_path):
    desktop = DesktopClient("ad.exe", "run1", runner=FakeCli())
    recorder = RunRecorder(tmp_path / "runs", desktop, {**META, "system": "virtualmes"}, now=lambda: NOW)
    OPEN_RECORDERS.append(recorder)
    assert run_json(recorder)["system"] == "virtualmes"
    assert run_json(new_recorder(tmp_path / "other", desktop))["system"] is None


def test_run_json_keeps_the_step_limit_or_null(tmp_path):
    desktop = DesktopClient("ad.exe", "run1", runner=FakeCli())
    recorder = RunRecorder(tmp_path / "runs", desktop, {**META, "max_steps": 40}, now=lambda: NOW)
    OPEN_RECORDERS.append(recorder)
    assert run_json(recorder)["max_steps"] == 40
    assert run_json(new_recorder(tmp_path / "other", desktop))["max_steps"] is None


@pytest.mark.parametrize(("given", "kept"), [(True, True), (False, False), ("gpt-vision-test", None),
                                             ({"model": "gpt-vision-test"}, None)])
def test_run_json_keeps_whether_a_vision_model_was_used_and_never_its_name(tmp_path, given, kept):
    desktop = DesktopClient("ad.exe", "run1", runner=FakeCli())
    recorder = RunRecorder(tmp_path / "runs", desktop, {**META, "vision": given}, now=lambda: NOW)
    OPEN_RECORDERS.append(recorder)
    recorder.finish(RunResult("verified", 1, "opened", 10))
    assert run_json(recorder)["vision"] is kept
    assert "gpt-vision-test" not in (recorder.path / "run.json").read_text(encoding="utf-8")


def test_run_json_vision_is_null_when_the_meta_does_not_say(tmp_path):
    desktop = DesktopClient("ad.exe", "run1", runner=FakeCli())
    assert run_json(new_recorder(tmp_path, desktop))["vision"] is None


def test_confirmed_status_and_a_warning_are_recorded(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event({"type": "warning", "message": "등록 중 오류: RuntimeError: x"})
    recorder.finish(RunResult("confirmed", 2, "사람이 확인함", 30))
    assert run_json(recorder)["status"] == "confirmed"
    assert events(recorder)[-2] == {"type": "warning", "message": "등록 중 오류: RuntimeError: x"}


SCENE_MAIN_BOUNDS = {"x": 1000, "y": 500, "width": 416, "height": 308}
SCENE_LIST_BOUNDS = {"x": 1100, "y": 650, "width": 216, "height": 258}
ITEM_BOUNDS = {"x": 1150, "y": 820, "width": 40, "height": 20}
QUERY_BOUNDS = {"x": 1010, "y": 510, "width": 80, "height": 24}
MAIN_PIXELS, LIST_PIXELS = (200, 220, 240), (10, 120, 30)
SCENE_OBSERVE = {"window_id": None, "root_ref": None, "surface": None, "include_bounds": False}


def popup_scene(list_bounds=SCENE_LIST_BOUNDS, with_list=True):
    main = scene_window("w-1", "메인", SCENE_MAIN_BOUNDS)
    listed = scene_window("w-2", "", list_bounds)
    tree = {"role": "window", "name": "메인", "bounds": SCENE_MAIN_BOUNDS, "children": [
        {"role": "button", "name": "조회", "ref_id": "@s1:e1", "bounds": QUERY_BOUNDS},
        {"role": "listitem", "name": "항목", "ref_id": "@s1:e2", "bounds": ITEM_BOUNDS},
    ]}
    cli = FakeCli(scene={"windows": [main, listed] if with_list else [main], "tree": tree})
    cli.screenshot_colors = {"w-1": MAIN_PIXELS, "w-2": LIST_PIXELS}
    return cli


def observed_scene(tmp_path, cli):
    recorder, toolbox, _ = recorded(tmp_path, cli)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    toolbox.call("observe", SCENE_OBSERVE)
    return recorder, toolbox, [e for e in events(recorder) if e["type"] == "observation"][-1]


def element(observation, name):
    return next(e for e in observation["elements"] if e["name"] == name)


def frame_pixel(recorder, observation, x, y):
    with Image.open(recorder.path / "frames" / observation["frame"]) as image:
        return image.convert("RGB").getpixel((x, y))


def test_a_popup_window_is_in_the_frame_and_its_elements_land_on_it(tmp_path):
    recorder, _, observation = observed_scene(tmp_path, popup_scene())

    assert observation["image"] == {"width": 400, "height": 400}
    assert observation["warning"] is None
    assert element(observation, "항목")["rect"] == {"x": 142, "y": 320, "width": 40, "height": 20}
    assert element(observation, "조회")["rect"] == {"x": 2, "y": 10, "width": 80, "height": 24}
    assert frame_pixel(recorder, observation, 150, 330) == LIST_PIXELS
    assert frame_pixel(recorder, observation, 10, 10) == MAIN_PIXELS
    assert frame_pixel(recorder, observation, 50, 350) == capture.FILL_COLOR


def test_a_popup_adds_one_screenshot_and_no_window_listing(tmp_path):
    _, _, _ = observed_scene(tmp_path / "one", single := popup_scene(with_list=False))
    _, _, _ = observed_scene(tmp_path / "two", double := popup_scene())

    assert double.command_names().count("list-windows") == single.command_names().count("list-windows")
    assert double.command_names().count("screenshot") == single.command_names().count("screenshot") + 1


def test_a_popup_above_and_left_moves_the_boxes_of_the_main_window(tmp_path):
    _, _, observation = observed_scene(tmp_path, popup_scene({"x": 920, "y": 420, "width": 216, "height": 208}))

    assert observation["image"] == {"width": 480, "height": 380}
    assert element(observation, "조회")["rect"] == {"x": 82, "y": 90, "width": 80, "height": 24}


def test_a_failed_popup_screenshot_leaves_it_out_and_says_how_many(tmp_path):
    cli = popup_scene()
    cli.failing_screenshots = {"w-2"}

    recorder, _, observation = observed_scene(tmp_path, cli)

    assert observation["image"] == {"width": 400, "height": 300}
    assert "1개" in observation["warning"]
    assert element(observation, "조회")["rect"] == {"x": 2, "y": 10, "width": 80, "height": 24}
    assert element(observation, "항목")["rect"] is None
    assert sorted(p.name for p in (recorder.path / "frames").iterdir()) == ["0001.png"]


def test_a_failed_main_screenshot_still_gives_no_frame(tmp_path):
    cli = popup_scene()
    cli.failing_screenshots = {"w-1"}

    _, _, observation = observed_scene(tmp_path, cli)

    assert observation["frame"] is None and "WINDOW_NOT_FOUND" in observation["warning"]
    assert cli.command_names().count("screenshot") == 1


def test_a_point_on_the_popup_window_gets_its_image_point(tmp_path):
    recorder, toolbox, _ = observed_scene(tmp_path, popup_scene())

    toolbox.call("mouse_click", {"window_id": "w-2", "x": 1150, "y": 850, "button": "left", "intent": "누른다"})

    click = [e for e in events(recorder) if e["type"] == "action"][-1]
    assert click["target"]["image_points"] == [[142, 350]]


def test_a_point_on_a_window_left_out_of_the_frame_gets_no_image_point(tmp_path):
    barely = {"x": 1300, "y": 790, "width": 400, "height": 300}
    recorder, toolbox, observation = observed_scene(tmp_path, popup_scene(barely))
    assert observation["image"] == {"width": 400, "height": 300}

    toolbox.call("mouse_click", {"window_id": "w-2", "x": 1350, "y": 795, "button": "left", "intent": "누른다"})

    click = [e for e in events(recorder) if e["type"] == "action"][-1]
    assert click["target"]["image_points"] == [None]


def test_the_frame_of_an_observed_popup_shows_it_over_the_window_listed_under_it(tmp_path):
    cli = popup_scene()
    recorder, toolbox, _ = recorded(tmp_path, cli)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    cli.scene["windows"].reverse()

    toolbox.call("observe", {**SCENE_OBSERVE, "window_id": "w-2"})

    observation = [e for e in events(recorder) if e["type"] == "observation"][-1]
    assert observation["window"]["id"] == "w-2" and observation["image"] == {"width": 400, "height": 400}
    assert frame_pixel(recorder, observation, 1150 - 1008, 700 - 500) == LIST_PIXELS
    assert frame_pixel(recorder, observation, 10, 10) == MAIN_PIXELS


MARK = "ZQX-단계글-7781"
STAGE_STATS = {"stage_count": 2, "stages_done": 1, "stage_calls": [3, 8], "limit_questions": 1,
               "plan_revisions": 1, "max_call_tokens": 9120}


def staged_events(recorder):
    recorder.on_event({"type": "model", "step": 1, "tokens": 100, "call_tokens": 100})
    recorder.on_event({"type": "plan", "step": 1, "revision": 0, "stages": [
        {"n": 1, "goal": f"{MARK} 목표", "done_when": f"{MARK} 확인", "status": "active"},
        {"n": 2, "goal": f"{MARK} 둘째", "done_when": f"{MARK} 둘째 확인", "status": "pending"}]})
    recorder.on_event({"type": "model", "step": 2, "tokens": 300, "call_tokens": 200, "stage": 1, "stage_calls": 1,
                       "stage_limit": 8})
    recorder.on_event({"type": "stage", "step": 2, "n": 1, "status": "done", "calls": 3, "limit": 8, "kind": "text",
                       "note": f"{MARK} 메모"})
    recorder.on_event({"type": "stage", "step": 2, "n": 2, "status": "started", "calls": 0, "limit": 8})


def test_plan_and_stage_events_are_kept(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    staged_events(recorder)

    kept = [event for event in events(recorder) if event["type"] in ("plan", "stage")]
    assert [(event["type"], event.get("status")) for event in kept] == [("plan", None), ("stage", "done"),
                                                                       ("stage", "started")]
    assert kept[0]["stages"][0]["goal"] == f"{MARK} 목표"
    assert kept[1]["note"] == f"{MARK} 메모"


def test_model_events_keep_call_and_stage_counts(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_event({"type": "model", "step": 1, "tokens": 100})
    recorder.on_event({"type": "model", "step": 2, "tokens": 300, "call_tokens": 200, "stage": 1, "stage_calls": 2,
                       "stage_limit": 8, "extra": "버림"})

    models = [event for event in events(recorder) if event["type"] == "model"]
    assert models == [
        {"type": "model", "step": 1, "tokens": 100},
        {"type": "model", "step": 2, "tokens": 300, "call_tokens": 200, "stage": 1, "stage_calls": 2, "stage_limit": 8},
    ]


def test_run_json_holds_stage_numbers_only(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.finish(RunResult("verified", 4, "opened", 400, stats={**STAGE_STATS, "note": MARK,
                                                                   "stage_calls_text": ["가"]}))

    assert run_json(recorder)["stages"] == STAGE_STATS


@pytest.mark.parametrize("stats", [{"stage_count": "2", "stage_calls": [1, "x"], "stages_done": True}])
def test_odd_stage_values_are_left_out_of_run_json(tmp_path, stats):
    recorder, _, _ = recorded(tmp_path)

    recorder.finish(RunResult("verified", 4, "opened", 400, stats=stats))

    assert run_json(recorder)["stages"] == {}


def test_a_result_without_stats_leaves_no_stages(tmp_path):
    from types import SimpleNamespace

    first, _, _ = recorded(tmp_path / "a")
    first.finish(RunResult("verified", 4, "opened", 400))
    second, _, _ = recorded(tmp_path / "b")
    second.finish(SimpleNamespace(status="stopped", steps=1, reason="old shape", tokens=5))

    assert "stages" not in run_json(first)
    assert "stages" not in run_json(second) and run_json(second)["status"] == "stopped"
    assert first.errors == [] and second.errors == []


def test_an_unconfirmed_verdict_is_recorded(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_action("click", {"intent": "체크한다"}, None,
                       {"ok": True, "expect": "value_changed", "expected_met": None, "verdict": "unconfirmed",
                        "effects": {"none": True, "picture": {"changed": True, "pixels": 71}}})

    result = [event for event in events(recorder) if event["type"] == "action"][0]["result"]
    assert result["verdict"] == "unconfirmed" and result["expected_met"] is None


def test_stage_texts_never_reach_run_json_or_the_summary(tmp_path):
    from agent.summary import summary_of

    recorder, _, _ = recorded(tmp_path)
    staged_events(recorder)
    recorder.on_event({"type": "question", "id": "q1", "kind": "stage_limit", "text": f"{MARK} 질문", "stage": 1,
                       "calls": 8, "intents": [f"{MARK} 의도"]})
    recorder.on_event({"type": "answer", "id": "q1", "text": f"{MARK} 답", "accepted": True, "timed_out": False})
    recorder.finish(RunResult("verified", 2, "opened", 300, stats={**STAGE_STATS, "goal": f"{MARK} 목표"}))

    assert MARK in (recorder.path / "events.jsonl").read_text(encoding="utf-8")
    assert MARK not in (recorder.path / "run.json").read_text(encoding="utf-8")
    summary = "\n".join(summary_of(recorder.path))
    assert MARK not in summary
    assert "단계: 2 · 완료 1 · 단계별 호출 3/8 · 한도 질문 1 · 계획 고침 1" in summary
    assert "호출당 토큰 최대: 9120" in summary


def vision_event(**fields):
    return {"type": "vision", "step": None, "capture": "c1", "asked_by": "look", "question": f"{MARK} 질문",
            "answer": f"{MARK} 답", "marks": [2], "points": [{"label": f"{MARK} 칸", "x": 4, "y": 5}],
            "unsure": False, "error": None, "dropped": 0, "tokens": 812, "seconds": 2.4, **fields}


def test_vision_event_is_recorded_with_a_step(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event({"type": "model", "step": 5, "tokens": 10})

    recorder.on_event(vision_event())

    assert events(recorder)[-1] == vision_event(step=5)


def test_vision_event_keeps_the_step_it_was_given(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event({"type": "model", "step": 5, "tokens": 10})

    recorder.on_event(vision_event(step=3, asked_by="mismatch"))

    assert events(recorder)[-1] == vision_event(step=3, asked_by="mismatch")


def test_run_json_gets_vision_counts(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.finish(RunResult("verified", 4, "opened", 400,
                              stats={**STAGE_STATS, "vision_calls": 2, "vision_tokens": 1500}))

    data = run_json(recorder)
    assert (data["vision_calls"], data["vision_tokens"]) == (2, 1500)
    assert data["stages"] == STAGE_STATS


@pytest.mark.parametrize("stats", [STAGE_STATS, {**STAGE_STATS, "vision_calls": "2", "vision_tokens": True}])
def test_run_json_has_no_vision_counts_without_whole_numbers(tmp_path, stats):
    recorder, _, _ = recorded(tmp_path)

    recorder.finish(RunResult("verified", 4, "opened", 400, stats=stats))

    data = run_json(recorder)
    assert "vision_calls" not in data and "vision_tokens" not in data


def test_vision_texts_stay_in_events_jsonl_alone(tmp_path):
    from agent.summary import summary_of

    recorder, _, _ = recorded(tmp_path)
    recorder.on_event({"type": "model", "step": 1, "tokens": 100})
    recorder.on_event(vision_event())
    recorder.finish(RunResult("verified", 1, "opened", 912,
                              stats={**STAGE_STATS, "vision_calls": 1, "vision_tokens": 812}))

    assert MARK in (recorder.path / "events.jsonl").read_text(encoding="utf-8")
    assert MARK not in (recorder.path / "run.json").read_text(encoding="utf-8")
    summary = "\n".join(summary_of(recorder.path))
    assert MARK not in summary and "비전 호출 1 · 비전 토큰 812" in summary


READY_MARK = "준비신호글자"


def ready_end(**fields):
    return {"type": "ready", "phase": "end", "status": "ready", "waited_ms": 12400, "polls": 9, "switched": True,
            "running_hint": False, **fields}


def test_ready_events_are_kept_and_the_end_goes_to_run_json(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event({"type": "ready", "phase": "start"})
    assert "ready" not in run_json(recorder)
    recorder.on_event(ready_end())

    kept = [event for event in events(recorder) if event["type"] == "ready"]
    assert kept == [{"type": "ready", "phase": "start"}, ready_end()]
    assert run_json(recorder)["ready"] == {"status": "ready", "waited_s": 12, "switched": True}
    recorder.finish(RunResult("verified", 1, "opened", 100))
    assert run_json(recorder)["ready"] == {"status": "ready", "waited_s": 12, "switched": True}


def test_ready_seconds_are_rounded_and_the_last_end_wins(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event(ready_end(waited_ms=12600, switched=False))
    assert run_json(recorder)["ready"] == {"status": "ready", "waited_s": 13, "switched": False}
    recorder.on_event(ready_end(status="timeout", waited_ms=180000, switched=False))
    assert run_json(recorder)["ready"] == {"status": "timeout", "waited_s": 180, "switched": False}


@pytest.mark.parametrize("fields", [{"status": "x"}, {"status": None}, {"waited_ms": "12400"}, {"waited_ms": True},
                                    {"waited_ms": -1}, {"switched": "yes"}])
def test_unknown_ready_status_is_not_written(tmp_path, fields):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event(ready_end(**fields))
    assert "ready" not in run_json(recorder)


NEW_COUNTS = {"read": "find", "reads": 31, "failed_reads": 4, "timeouts": 0, "max_read_ms": 7900, "candidates": 2}


def test_run_json_ready_takes_the_new_counts(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event(ready_end(**NEW_COUNTS, note=READY_MARK))
    assert run_json(recorder)["ready"] == {
        "status": "ready", "waited_s": 12, "switched": True, "read": "find", "polls": 9, "reads": 31,
        "failed_reads": 4, "timeouts": 0, "max_read_ms": 7900}
    assert READY_MARK not in (recorder.path / "run.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("fields", [
    {"reads": -1}, {"reads": "31"}, {"failed_reads": True}, {"timeouts": 1.5}, {"max_read_ms": None},
    {"read": "fast"}, {"read": None}, {"polls": -2}, {"reads": None, "read": None, "failed_reads": None}])
def test_a_ready_end_with_a_malformed_count_keeps_the_three_keys(tmp_path, fields):
    recorder, _, _ = recorded(tmp_path)
    end = {key: value for key, value in ready_end(**{**NEW_COUNTS, **fields}).items() if value is not None}
    recorder.on_event(end)
    assert run_json(recorder)["ready"] == {"status": "ready", "waited_s": 12, "switched": True}


def test_run_json_ready_holds_no_text(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event(ready_end(note=READY_MARK))
    assert set(run_json(recorder)["ready"]) == {"status", "waited_s", "switched"}
    assert READY_MARK not in (recorder.path / "run.json").read_text(encoding="utf-8")


def test_a_run_without_ready_has_no_ready_key(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.finish(RunResult("verified", 1, "opened", 100))
    assert "ready" not in run_json(recorder)


def test_a_ready_run_is_recorded_from_the_toolbox_to_the_summary(tmp_path):
    from test_loop import GIVE_UP
    from test_loop_ready import splash_then_main, task

    from agent.summary import summary_of
    from tests.ready_support import SIGNAL, FakeClock

    clock = FakeClock()
    desktop = splash_then_main(clock)
    recorder = new_recorder(tmp_path, desktop)
    the_task = task()
    toolbox = Toolbox(desktop, the_task, confirm=lambda _p: False, sleep=clock.sleep, clock=clock, observer=recorder)
    result = run_agent(ScriptedModel([GIVE_UP]), toolbox, the_task, log=recorder.on_event)
    recorder.finish(result)

    recorded_events = events(recorder)
    assert [(event["type"], event.get("phase")) for event in recorded_events[:3]] == [
        ("ready", "start"), ("observation", None), ("ready", "end")]
    assert recorded_events[1]["source"] == "ready"
    ready = run_json(recorder)["ready"]
    assert {key: ready[key] for key in ("status", "waited_s", "switched")} == {
        "status": "ready", "waited_s": 4, "switched": True}
    assert set(ready) == {"status", "waited_s", "switched", "read", "polls", "reads", "failed_reads", "timeouts",
                          "max_read_ms"}
    assert (ready["read"], ready["polls"], ready["failed_reads"], ready["timeouts"]) == ("snapshot", 15, 0, 0)
    assert SIGNAL not in (recorder.path / "run.json").read_text(encoding="utf-8")
    summary = summary_of(recorder.path)
    assert ("  준비: ready · 4초 · snapshot · 확인 15바퀴 · 읽기 15회(실패 0, 시간 초과 0, 최대 0.0초) · 대상 바뀜"
            in summary)
    assert SIGNAL not in "\n".join(summary)


def test_stop_requested_is_kept_with_the_current_step(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event({"type": "model", "step": 3, "tokens": 100})

    recorder.on_event({"type": "stop_requested"})

    assert [event for event in events(recorder) if event["type"] == "stop_requested"] == [
        {"type": "stop_requested", "step": 3}]
    assert "stop_requested" not in json.dumps(run_json(recorder))


def test_stop_requested_after_the_finish_is_not_recorded(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.finish(RunResult("stopped", 1, "사용자가 실행을 멈췄습니다", 100))

    recorder.on_event({"type": "stop_requested"})

    assert [event["type"] for event in events(recorder)][-1] == "finish"
    assert all(event["type"] != "stop_requested" for event in events(recorder))


def popup_in_tree():
    children = [
        {"role": "window", "name": "목록", "children": [
            {"role": "statictext", "name": "All"},
            {"role": "checkbox", "name": "WA0", "ref_id": "@s9:e2"}]},
        {"role": "button", "name": "조회", "ref_id": "@s9:e1"},
    ]
    return {"version": "2.6", "ok": True, "command": "snapshot",
            "data": {"tree": {"role": "window", "name": "창", "children": children}}}


def test_observation_elements_keep_mark_and_scope(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_observation(window_row(), popup_in_tree(), "t")

    elements = events(recorder)[0]["elements"]
    assert [(e["name"], e["mark"], e["scope"]) for e in elements] == [
        ("목록", 1, None), ("All", 2, 1), ("WA0", 3, 1), ("조회", 4, None)]


def test_observation_elements_keep_native_id_in_and_alias(tmp_path):
    cli = FakeCli()
    desktop = DesktopClient("ad.exe", "run1", runner=cli)
    profile = Profile(system="sys", elements=(
        {"id": "56", "role": "image", "name": "Shipment Plan", "in": {"role": "toolbar", "id": "tb1"}},))
    recorder = RunRecorder(tmp_path / "runs", desktop, META, now=lambda: NOW, heartbeat_s=60, profile=profile)
    OPEN_RECORDERS.append(recorder)
    bounds = {"x": 2000.0, "y": 160.0, "width": 40.0, "height": 24.0}
    tree = {"role": "window", "name": "창", "children": [
        {"role": "toolbar", "native_id": {"kind": "automation_id", "value": "tb1"}, "children": [
            {"role": "image", "native_id": {"kind": "automation_id", "value": "56"}, "bounds": bounds}]},
        {"role": "statictext", "name": "라벨"}]}

    recorder.on_observation(window_row(), {"ok": True, "data": {"tree": tree}}, "t")

    image, label = events(recorder)[0]["elements"]
    assert (image["kind"], image["mark"], image["alias"]) == ("id", 1, "Shipment Plan")
    assert image["native_id"] == {"kind": "automation_id", "value": "56"}
    assert image["in"] == {"role": "toolbar", "id": "tb1"}
    assert image["rect"] == {"x": 70.5, "y": 160.5, "width": 40.0, "height": 24.0} and "bounds" not in image
    assert not {"native_id", "in", "alias"} & set(label)


def test_a_recorder_without_a_profile_still_records_id_only_leaves(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    tree = {"role": "window", "name": "창", "children": [
        {"role": "image", "native_id": {"kind": "automation_id", "value": "56"}}]}

    recorder.on_observation(window_row(), {"ok": True, "data": {"tree": tree}}, "t")

    [image] = events(recorder)[0]["elements"]
    assert image["kind"] == "id" and image["native_id"]["value"] == "56" and "alias" not in image


def test_id_elements_are_not_cut_with_the_text(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    envelope = labels_tree(401)
    envelope["data"]["tree"]["children"].append(
        {"role": "image", "native_id": {"kind": "automation_id", "value": "56"}})

    recorder.on_observation(window_row(), envelope, "t")

    observation = events(recorder)[0]
    assert len([e for e in observation["elements"] if e["kind"] == "text"]) == 400
    assert observation["elements"][-1]["kind"] == "id" and observation["elements"][-1]["mark"] == 403
    assert observation["text_truncated"] is True


def test_marks_survive_the_text_cut(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_observation(window_row(), labels_tree(401), "t")

    elements = events(recorder)[0]["elements"]
    assert [e["mark"] for e in elements[:400]] == list(range(1, 401))
    assert elements[-1]["ref"] == "@s9:e1"
    assert elements[-1]["mark"] == 402
    assert elements[-1]["scope"] is None


@pytest.mark.parametrize("event", [
    {"type": "finish_note", "by": "human", "saved_path": r"C:\out\a.xlsx"},
    {"type": "workflow", "created": True, "id": "w-20261119-001", "code": None, "steps": 12, "params": 2},
    {"type": "workflow_step", "n": 3, "kind": "act", "ok": False, "code": "EXPECT_UNMET", "ms": 15000,
     "goal": "목록 창이 열려 있다"},
])
def test_finish_note_workflow_and_workflow_step_events_are_kept(tmp_path, event):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_event(event)

    assert events(recorder)[-1] == event


WORKFLOW_MADE = {"type": "workflow", "created": True, "id": "w-20261119-001", "code": None, "steps": 12, "params": 2}
WORKFLOW_RUN = {"id": "w-20261119-001", "steps": 12, "steps_done": 5, "code": "EXPECT_UNMET"}


def test_a_workflow_event_reaches_run_json_as_three_fields(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_event(WORKFLOW_MADE)

    assert run_json(recorder)["workflow"] == {"created": True, "id": "w-20261119-001", "code": None}
    assert events(recorder)[-1] == WORKFLOW_MADE


def test_a_failed_workflow_event_keeps_its_code(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_event({"type": "workflow", "created": False, "id": None, "code": "LOCATOR_NOT_UNIQUE", "steps": 0,
                       "params": 0})
    recorder.finish(RunResult("confirmed", 4, "done", 400))

    assert run_json(recorder)["workflow"] == {"created": False, "id": None, "code": "LOCATOR_NOT_UNIQUE"}
    assert run_json(recorder)["status"] == "confirmed"


def test_finish_stats_with_workflow_run_reach_run_json(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.finish(RunResult("stopped", 5, "EXPECT_UNMET", 0, stats={"workflow_run": dict(WORKFLOW_RUN)}))

    assert run_json(recorder)["workflow_run"] == WORKFLOW_RUN


def test_a_passed_workflow_run_has_no_code(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    passed = {**WORKFLOW_RUN, "steps_done": 12, "code": None}

    recorder.finish(RunResult("verified", 12, "workflow 통과", 0, stats={"workflow_run": passed}))

    assert run_json(recorder)["workflow_run"] == passed


@pytest.mark.parametrize("change", [{"code": "화면 글 C:\\out"}, {"code": 3}, {"id": "보고서 이름"},
                                    {"id": "w-2026/../x"}, {"created": "yes"}])
def test_malformed_workflow_events_are_left_out(tmp_path, change):
    recorder, _, _ = recorded(tmp_path)

    recorder.on_event({**WORKFLOW_MADE, **change})

    assert "workflow" not in run_json(recorder)
    assert events(recorder)[-1]["type"] == "workflow"


@pytest.mark.parametrize("change", [{"steps": -1}, {"steps_done": "5"}, {"steps_done": True}, {"code": "화면의 글"},
                                    {"id": None}, {"id": "w-abc"}, {"extra": "C:\\out\\a.xlsx"}])
def test_malformed_workflow_numbers_are_left_out(tmp_path, change):
    recorder, _, _ = recorded(tmp_path)

    recorder.finish(RunResult("stopped", 5, "x", 0, stats={"workflow_run": {**WORKFLOW_RUN, **change}}))

    run = run_json(recorder)
    assert "workflow_run" not in run
    assert recorder.errors == []


def test_a_workflow_run_that_is_not_a_dict_is_left_out(tmp_path):
    recorder, _, _ = recorded(tmp_path)

    recorder.finish(RunResult("stopped", 5, "x", 0, stats={"workflow_run": ["w-20261119-001"]}))

    assert "workflow_run" not in run_json(recorder)


def test_run_json_holds_no_workflow_goal_param_or_path_text(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.on_event({"type": "plan", "revision": 0, "stages": [{"n": 1, "goal": f"{MARK} 목표", "done_when": "d",
                                                                  "status": "active"}],
                       "params": [{"name": "shop", "value": f"{MARK} 값"}], "step": 1})
    recorder.on_event({"type": "finish_note", "by": "human", "saved_path": f"C:\\{MARK}\\a.xlsx"})
    recorder.on_event(WORKFLOW_MADE)
    recorder.on_event({"type": "workflow_step", "n": 1, "kind": "act", "ok": True, "code": None, "ms": 10,
                       "goal": f"{MARK} 단계"})

    recorder.finish(RunResult("verified", 1, "workflow 통과", 0, stats={"workflow_run": dict(WORKFLOW_RUN)}))

    text = (recorder.path / "run.json").read_text(encoding="utf-8")
    assert MARK not in text and "shop" not in text
