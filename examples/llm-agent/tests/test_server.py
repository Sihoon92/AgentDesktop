import http.client
import json
import struct
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_loop import OBSERVE, ScriptedModel, call
from test_run_manager import GatedModel, WatchedGate, launch_then_finish, make_manager

from agent.approval import Decision
from viewer import history as history_module
from viewer import server as server_module
from viewer.server import make_server

TOKEN = "test-token"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


def write_run(runs_dir, run_id, started_at, events=()):
    run = runs_dir / run_id
    (run / "frames").mkdir(parents=True)
    meta = {"run_id": run_id, "goal": "설정 열기", "started_at": started_at, "status": "running"}
    (run / "run.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    lines = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    (run / "events.jsonl").write_text(lines, encoding="utf-8")
    (run / "frames" / "0001.png").write_bytes(PNG_BYTES)
    return run


@contextmanager
def serving(runs_dir, run_manager=None):
    instance = make_server(runs_dir, TOKEN, port=0, run_manager=run_manager)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


@pytest.fixture
def server(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (tmp_path / "run.py").write_text("secret", encoding="utf-8")
    with serving(runs_dir) as instance:
        yield instance, runs_dir


def request(instance, path, headers=None, method="GET", body=None):
    port = instance.server_address[1]
    sent = {"Host": f"127.0.0.1:{port}", "X-Agent-Token": TOKEN}
    if body is not None:
        sent["Content-Type"] = "application/json"
        sent["Content-Length"] = str(len(body))
    sent.update(headers or {})
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest(method, path, skip_host=True)
        for name, value in sent.items():
            if value is not None:
                connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type", ""), response.read()
    finally:
        connection.close()


def post_json(instance, path, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _, raw = request(instance, path, headers, method="POST", body=body)
    return status, json.loads(raw or b"{}")


def test_api_without_token_is_403(server):
    instance, runs_dir = server
    write_run(runs_dir, "20261107-120000", "2026-11-07T12:00:00Z")

    status, _, _ = request(instance, "/api/runs", {"X-Agent-Token": None})
    wrong_status, _, _ = request(instance, "/api/runs", {"X-Agent-Token": "nope"})

    assert status == 403
    assert wrong_status == 403


def test_api_with_wrong_host_header_is_403(server):
    instance, runs_dir = server
    write_run(runs_dir, "20261107-120000", "2026-11-07T12:00:00Z")
    port = instance.server_address[1]

    rebound, _, _ = request(instance, "/api/runs", {"Host": f"attacker.example:{port}"})
    other_port, _, _ = request(instance, "/api/runs", {"Host": f"127.0.0.1:{port + 1}"})
    localhost, _, _ = request(instance, "/api/runs", {"Host": f"localhost:{port}"})

    assert rebound == 403
    assert other_port == 403
    assert localhost == 200


def test_runs_are_listed_newest_first(server):
    instance, runs_dir = server
    write_run(runs_dir, "20261107-120000", "2026-11-07T12:00:00Z")
    write_run(runs_dir, "20261107-130000", "2026-11-07T13:00:00Z")
    write_run(runs_dir, "20261107-120000-2", "2026-11-07T12:00:00Z")
    (runs_dir / "not-a-run").mkdir()

    status, content_type, body = request(instance, "/api/runs")

    assert status == 200
    assert content_type.startswith("application/json")
    ids = [run["run_id"] for run in json.loads(body)["runs"]]
    assert ids == ["20261107-130000", "20261107-120000-2", "20261107-120000"]


def test_events_since_returns_only_new_lines(server):
    instance, runs_dir = server
    events = [{"type": "observation", "id": 1}, {"type": "action", "step": 1}, {"type": "observation", "id": 2}]
    run = write_run(runs_dir, "20261107-120000", "2026-11-07T12:00:00Z", events)
    with (run / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"type": "model", "st')

    status, _, body = request(instance, "/api/runs/20261107-120000/events?since=1")

    assert status == 200
    payload = json.loads(body)
    assert payload["events"] == events[1:]
    assert payload["next"] == 3


def test_path_traversal_in_run_id_or_frame_is_404(server):
    instance, runs_dir = server
    write_run(runs_dir, "20261107-120000", "2026-11-07T12:00:00Z")

    paths = [
        "/api/runs/..%2F..%2Frun.py/events",
        "/api/runs/..%2F..%2Frun.py",
        "/api/runs/20261107-120000/frames/0001.png/../x",
        "/api/runs/20261107-120000/frames/..%2F..%2F..%2Frun.py",
        "/api/runs/20261107-120000/frames/1.png",
        "/static/..%2F..%2Frun.py",
    ]

    for path in paths:
        status, _, body = request(instance, path)
        assert status == 404, path
        assert b"secret" not in body


def test_trailing_newline_in_run_id_or_frame_is_404(server):
    instance, runs_dir = server
    write_run(runs_dir, "20261107-120000", "2026-11-07T12:00:00Z")

    paths = [
        "/api/runs/20261107-120000%0A/events",
        "/api/runs/20261107-120000%0A/frames/0001.png",
        "/api/runs/20261107-120000/frames/0001.png%0A",
        "/static/index.html%0A",
    ]

    for path in paths:
        status, _, _ = request(instance, path)
        assert status == 404, path


def test_name_patterns_reject_a_trailing_newline():
    assert not server_module.is_run_id("20261107-120000\n")
    assert not server_module.is_frame_name("0001.png\n")
    assert not server_module.is_static_name("index.html\n")
    assert server_module.is_run_id("20261107-120000-2")
    assert server_module.is_frame_name("0001.png")
    assert server_module.is_static_name("app.js")


def test_server_binds_loopback_only(server):
    instance, _ = server

    assert instance.server_address[0] == "127.0.0.1"


def test_frame_is_served_as_png(server):
    instance, runs_dir = server
    write_run(runs_dir, "20261107-120000", "2026-11-07T12:00:00Z")

    status, content_type, body = request(instance, "/api/runs/20261107-120000/frames/0001.png")

    assert status == 200
    assert content_type == "image/png"
    assert body == PNG_BYTES


def test_page_needs_the_right_host_but_no_token(server):
    instance, _ = server
    port = instance.server_address[1]

    status, content_type, _ = request(instance, f"/?t={TOKEN}", {"X-Agent-Token": None})
    rebound, _, _ = request(instance, "/", {"Host": f"attacker.example:{port}", "X-Agent-Token": None})

    assert status == 200
    assert content_type.startswith("text/html")
    assert rebound == 403


SAMPLE_RUNS = Path(__file__).parent / "fixtures" / "run_sample"


@pytest.fixture
def sample_server():
    with serving(SAMPLE_RUNS) as instance:
        yield instance


def test_sample_run_frames_and_boxes_are_consistent(sample_server):
    status, _, body = request(sample_server, "/api/runs")
    runs = json.loads(body)["runs"]
    assert status == 200
    assert [run["run_id"] for run in runs] == [
        "20261108-010000", "20261108-005000", "20261107-150000", "20261107-140000", "20261107-130000", "20261107-120000", "20261107-115500",
        "20261107-090000",
    ]
    assert [run["status"] for run in runs][:3] == ["verified", "verified", "unreadable"]
    assert runs[-1]["kind"] == "legacy"

    _, _, body = request(sample_server, "/api/runs/20261107-120000/events")
    events = json.loads(body)["events"]
    observations = {event["id"]: event for event in events if event["type"] == "observation"}
    actions = [event for event in events if event["type"] == "action"]
    assert len(observations) == 2
    assert len(actions) == 1
    assert actions[0]["before"] in observations
    for observation in observations.values():
        status, content_type, png = request(sample_server, f"/api/runs/20261107-120000/frames/{observation['frame']}")
        assert (status, content_type) == (200, "image/png")
        width, height = struct.unpack(">II", png[16:24])
        assert (width, height) == (observation["image"]["width"], observation["image"]["height"])
        drawn = [item["rect"] for item in observation["elements"] + [actions[0]["target"]] if item["rect"]]
        assert drawn
        for rect in drawn:
            assert 0 <= rect["x"] and rect["x"] + rect["width"] <= width
            assert 0 <= rect["y"] and rect["y"] + rect["height"] <= height


def sample_observations(sample_server, run_id):
    _, _, body = request(sample_server, f"/api/runs/{run_id}/events")
    return [event for event in json.loads(body)["events"] if event["type"] == "observation"]


def test_sample_runs_carry_text_elements_in_the_recorded_format(sample_server):
    for run_id in ("20261107-130000", "20261107-120000", "20261107-115500"):
        for observation in sample_observations(sample_server, run_id):
            assert type(observation["text_truncated"]) is bool
            for item in observation["elements"]:
                assert set(item) == {"kind", "ref", "role", "name", "description", "rect"}
                assert item["kind"] in ("control", "text")
                assert (item["ref"] is None) == (item["kind"] == "text")

    texts = [item for item in sample_observations(sample_server, "20261107-120000")[0]["elements"] if item["kind"] == "text"]
    assert any(item["rect"] for item in texts)
    assert any(item["rect"] is None for item in texts)


def test_wait_sample_run_records_each_wait_as_one_observation(sample_server):
    observations = sample_observations(sample_server, "20261107-140000")
    sources = [observation["source"] for observation in observations]
    waits = [observation["wait"] for observation in observations if observation["source"] == "wait_for_text"]

    assert sources == ["observe", "wait_for_text", "action", "wait_for_text"]
    assert all("wait" not in o for o in observations if o["source"] != "wait_for_text")
    assert [set(wait) for wait in waits] == [{"text", "found", "polls", "waited_ms"}] * 2
    assert [wait["found"] for wait in waits] == [False, True]
    for observation in observations:
        status, content_type, _ = request(sample_server, f"/api/runs/20261107-140000/frames/{observation['frame']}")
        assert (status, content_type) == (200, "image/png")


def test_dense_sample_run_has_the_400_text_cap(sample_server):
    [observation] = sample_observations(sample_server, "20261107-130000")
    texts = [item for item in observation["elements"] if item["kind"] == "text"]

    assert observation["text_truncated"] is True
    assert len(texts) == 400
    for item in texts:
        rect = item["rect"]
        assert rect["x"] + rect["width"] <= observation["image"]["width"]
        assert rect["y"] + rect["height"] <= observation["image"]["height"]


def test_page_lets_frames_load_as_blob_urls_but_no_inline_script(server):
    instance, _ = server

    connection = http.client.HTTPConnection("127.0.0.1", instance.server_port, timeout=5)
    try:
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{instance.server_port}"})
        policy = connection.getresponse().getheader("Content-Security-Policy", "")
    finally:
        connection.close()

    directives = dict(part.strip().split(" ", 1) for part in policy.split(";"))
    assert "blob:" in directives["img-src"].split()
    assert "'unsafe-inline'" not in policy
    assert "script-src" not in directives or directives["script-src"] == "'self'"


def finishing_model():
    return ScriptedModel([[call("finish", {"success": True, "evidence": "done"})]])


def runs_created(tmp_path):
    runs_dir = tmp_path / "runs"
    return sorted(path.name for path in runs_dir.iterdir()) if runs_dir.exists() else []


def test_post_runs_starts_a_run_and_returns_its_id(tmp_path):
    manager, exe = make_manager(tmp_path, finishing_model())
    with serving(tmp_path / "runs", manager) as instance:
        status, payload = post_json(instance, "/api/runs", {"goal": "설정을 열어줘", "level": 1})
        manager.join(timeout=5)
        _, _, listing = request(instance, "/api/runs")

    assert status == 201
    runs = {run["run_id"]: run for run in json.loads(listing)["runs"]}
    assert payload["run_id"] in runs
    assert runs[payload["run_id"]]["exe"] == str(exe)
    assert runs[payload["run_id"]]["permission_level"] == 1


def test_post_runs_without_goal_is_400(tmp_path):
    manager, exe = make_manager(tmp_path, finishing_model())
    with serving(tmp_path / "runs", manager) as instance:
        missing, missing_body = post_json(instance, "/api/runs", {"exe": str(exe)})
        blank, _ = post_json(instance, "/api/runs", {"goal": "  "})
        not_json, _, _ = request(instance, "/api/runs", method="POST", body=b"{goal")
        not_object, _ = post_json(instance, "/api/runs", ["goal"])
        wrong_type, _, _ = request(
            instance, "/api/runs", {"Content-Type": "text/plain"}, method="POST", body=b'{"goal": "g"}'
        )

    assert (missing, blank, not_json, not_object) == (400, 400, 400, 400)
    assert "error" in missing_body
    assert wrong_type == 415
    assert manager.active() is None
    assert runs_created(tmp_path) == []


def test_post_runs_while_running_is_409(tmp_path):
    model = GatedModel()
    manager, _ = make_manager(tmp_path, model)
    with serving(tmp_path / "runs", manager) as instance:
        first, _ = post_json(instance, "/api/runs", {"goal": "하나"})
        second, body = post_json(instance, "/api/runs", {"goal": "둘"})
        model.release.set()
        manager.join(timeout=5)

    assert (first, second) == (201, 409)
    assert "error" in body
    assert len(runs_created(tmp_path)) == 1


def test_post_runs_needs_the_token(tmp_path):
    manager, _ = make_manager(tmp_path, finishing_model())
    with serving(tmp_path / "runs", manager) as instance:
        status, _ = post_json(instance, "/api/runs", {"goal": "g"}, {"X-Agent-Token": "nope"})

    assert status == 403
    assert runs_created(tmp_path) == []


def test_post_runs_without_a_run_manager_is_503(server):
    instance, _ = server

    status, body = post_json(instance, "/api/runs", {"goal": "g"})

    assert status == 503
    assert "error" in body


def test_status_reports_the_active_run_and_default_exe(tmp_path):
    model = GatedModel()
    manager, exe = make_manager(tmp_path, model)
    with serving(tmp_path / "runs", manager) as instance:
        _, _, idle = request(instance, "/api/status")
        _, started = post_json(instance, "/api/runs", {"goal": "g"})
        _, _, busy = request(instance, "/api/status")
        model.release.set()
        manager.join(timeout=5)

    assert json.loads(idle) == {"can_start": True, "active": None, "default_exe": str(exe)}
    assert json.loads(busy)["active"] == started["run_id"]


def test_status_without_a_run_manager_says_runs_cannot_start(server):
    instance, _ = server

    status, _, body = request(instance, "/api/status")

    assert status == 200
    assert json.loads(body) == {"can_start": False, "active": None, "default_exe": None}


def test_favicon_request_is_answered_without_a_404(server):
    instance, _ = server

    status, _, body = request(instance, "/favicon.ico", {"X-Agent-Token": None})

    assert status == 204
    assert body == b""


class PausingModel:
    """Launches and observes, then waits for `release` before finishing."""

    def __init__(self):
        self.release = threading.Event()
        self.paused = threading.Event()
        self.turns = [
            [call("launch_target", {"intent": "앱을 연다"})],
            [call("observe", OBSERVE)],
        ]

    def respond(self, instructions, items, tools):
        if self.turns:
            output = self.turns.pop(0)
        else:
            self.paused.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("test never released the model")
            output = [call("finish", {"success": True, "evidence": "done"})]
        return SimpleNamespace(output=output, usage=SimpleNamespace(total_tokens=10))


def test_events_of_a_live_run_arrive_incrementally(tmp_path):
    model = PausingModel()
    manager, _ = make_manager(tmp_path, model)
    with serving(tmp_path / "runs", manager) as instance:
        _, started = post_json(instance, "/api/runs", {"goal": "설정을 열어줘"})
        run_id = started["run_id"]
        assert model.paused.wait(timeout=5)
        _, _, first = request(instance, f"/api/runs/{run_id}/events?since=0")
        _, _, listing = request(instance, "/api/runs")
        model.release.set()
        manager.join(timeout=5)
        first_page = json.loads(first)
        _, _, rest = request(instance, f"/api/runs/{run_id}/events?since={first_page['next']}")
        _, _, everything = request(instance, f"/api/runs/{run_id}/events?since=0")

    live_types = [event["type"] for event in first_page["events"]]
    assert "observation" in live_types
    assert "finish" not in live_types
    assert json.loads(listing)["runs"][0]["status"] == "running"
    rest_events = json.loads(rest)["events"]
    assert rest_events[-1]["type"] == "finish"
    assert first_page["events"] + rest_events == json.loads(everything)["events"]


def test_post_decision_answers_the_waiting_proposal(tmp_path):
    gate = WatchedGate()
    manager, _ = make_manager(tmp_path, launch_then_finish(), gate_factory=lambda: gate)
    with serving(tmp_path / "runs", manager) as instance:
        status, started = post_json(instance, "/api/runs", {"goal": "앱을 열어줘", "approve": True})
        run_id = started["run_id"]
        assert gate.waiting.wait(timeout=5)
        decided, body = post_json(
            instance, f"/api/runs/{run_id}/decision", {"proposal_id": "p1", "approved": False, "comment": "아니요"}
        )
        again, _ = post_json(instance, f"/api/runs/{run_id}/decision", {"proposal_id": "p1", "approved": True})
        manager.join(timeout=5)
        _, _, page = request(instance, f"/api/runs/{run_id}/events")

    assert (status, decided, again) == (201, 200, 409)
    assert body == {"accepted": True}
    decision = next(e for e in json.loads(page)["events"] if e["type"] == "decision")
    assert (decision["approved"], decision["comment"]) == (False, "아니요")


def test_bad_decision_requests_are_rejected(tmp_path):
    gate = WatchedGate()
    manager, _ = make_manager(tmp_path, launch_then_finish(), gate_factory=lambda: gate)
    with serving(tmp_path / "runs", manager) as instance:
        _, started = post_json(instance, "/api/runs", {"goal": "앱을 열어줘", "approve": True})
        run_id = started["run_id"]
        assert gate.waiting.wait(timeout=5)
        path = f"/api/runs/{run_id}/decision"
        not_bool, _ = post_json(instance, path, {"proposal_id": "p1", "approved": "yes"})
        no_id, _ = post_json(instance, path, {"approved": True})
        bad_comment, _ = post_json(instance, path, {"proposal_id": "p1", "approved": True, "comment": 5})
        long_comment, _ = post_json(instance, path, {"proposal_id": "p1", "approved": True, "comment": "x" * 2001})
        no_token, _ = post_json(instance, path, {"proposal_id": "p1", "approved": True}, {"X-Agent-Token": "nope"})
        bad_run, _ = post_json(instance, "/api/runs/..%2Fx/decision", {"proposal_id": "p1", "approved": True})
        other_run, _ = post_json(instance, "/api/runs/20261107-000000/decision", {"proposal_id": "p1", "approved": True})
        still_waiting = gate.decide("p1", Decision(True, ""))
        manager.join(timeout=5)

    assert (not_bool, no_id, bad_comment, long_comment) == (400, 400, 400, 400)
    assert no_token == 403
    assert bad_run == 404
    assert other_run == 409
    assert still_waiting is True


def test_post_runs_approve_must_be_a_boolean(tmp_path):
    manager, _ = make_manager(tmp_path, finishing_model())
    with serving(tmp_path / "runs", manager) as instance:
        status, _ = post_json(instance, "/api/runs", {"goal": "g", "approve": "yes"})

    assert status == 400
    assert runs_created(tmp_path) == []


def test_decision_without_a_run_manager_is_503(server):
    instance, _ = server

    status, _ = post_json(instance, "/api/runs/20261107-120000/decision", {"proposal_id": "p1", "approved": True})

    assert status == 503


def write_meta(runs_dir, run_id, **fields):
    run = runs_dir / run_id
    run.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": run_id, "goal": "설정 열기", "started_at": f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T00:00:00Z",
            "status": "verified", **fields}
    (run / "run.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return run


def list_page(instance, query=""):
    status, _, body = request(instance, f"/api/runs{query}")
    assert status == 200, body
    return json.loads(body)


def by_id(page):
    return {run["run_id"]: run for run in page["runs"]}


def test_run_list_summarizes_a_format_2_record(server):
    instance, runs_dir = server
    write_meta(
        runs_dir, "20261108-010000", format=2, exe="C:\\apps\\Sample App\\AgentDeskSample.exe", model="gpt-6.1-terra",
        permission_level=3, started_by="run.py --view", started_at="2026-11-08T01:00:00Z",
        finished_at="2026-11-08T01:02:05Z", heartbeat_at="2026-11-08T01:02:05Z", pid=4321,
        process_started_at="2026-11-08T00:59:58.123Z", status="verified", reason="ok", tokens=4050, steps=2,
    )

    run = by_id(list_page(instance))["20261108-010000"]

    assert run["kind"] == "run"
    assert run["format"] == 2
    assert run["newer_format"] is False
    assert run["status"] == "verified"
    assert run["steps"] == 2
    assert run["tokens"] == 4050
    assert run["duration_s"] == 125
    assert run["started_by"] == "run.py --view"
    assert run["exe_name"] == "AgentDeskSample.exe"
    assert run["goal"] == "설정 열기"
    assert run["date"] == "2026-11-08"


def test_run_list_shows_what_a_format_1_record_has(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000", exe="C:\\apps\\App.exe", tokens=900, steps=1)

    run = by_id(list_page(instance))["20261107-120000"]

    assert run["format"] == 1
    assert run["duration_s"] is None
    assert run["started_by"] is None
    assert run["exe_name"] == "App.exe"
    assert run["steps"] == 1


def test_broken_missing_or_half_written_run_json_is_listed_as_unreadable(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")
    (runs_dir / "20261107-130000").mkdir()
    (runs_dir / "20261107-140000").mkdir()
    (runs_dir / "20261107-140000" / "run.json").write_text('{"run_id": "20261107-140000", "goal": "깨', encoding="utf-8")
    (runs_dir / "20261107-150000").mkdir()
    (runs_dir / "20261107-150000" / "run.json").write_text("[1, 2]", encoding="utf-8")
    (runs_dir / "20261107-160000").mkdir()
    (runs_dir / "20261107-160000" / "run.json").write_text('{"run_id": "20261107-160000", "goal": "상태 없음"}', encoding="utf-8")

    runs = by_id(list_page(instance))

    assert runs["20261107-120000"]["status"] == "verified"
    for run_id in ("20261107-130000", "20261107-140000", "20261107-150000", "20261107-160000"):
        assert runs[run_id]["status"] == "unreadable", run_id
        assert runs[run_id]["kind"] == "run"
        assert runs[run_id]["date"] == "2026-11-07"


def test_a_newer_format_is_flagged(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261109-010000", format=3, future_field={"x": 1})
    write_meta(runs_dir, "20261108-010000", format=2)

    runs = by_id(list_page(instance))

    assert runs["20261109-010000"]["newer_format"] is True
    assert runs["20261109-010000"]["status"] == "verified"
    assert runs["20261108-010000"]["newer_format"] is False


LEGACY_LINES = [
    {"step": 1, "tool": "launch_target", "arguments": "{}",
     "result": {"ok": True, "pid": 7, "windows": [{"id": "w-1"}, {"id": "w-2"}]}},
    {"step": 2, "tool": "observe", "arguments": json.dumps({"window_id": "w-1", "surface": "window"}),
     "result": {"ok": True, "window_id": "w-1", "complete": True,
                "observation": "window \"앱\"\n" + "  button \"x\"\n" * 9000}},
    {"step": 3, "tool": "toggle", "arguments": json.dumps({"ref": "@s1:e2"}),
     "result": {"ok": False, "code": "ACTION_FAILED", "message": "Post-action state does not match",
                "disposition": {"delivery": "delivered_unverified", "retry": "unsafe"}}},
    {"step": 4, "tool": "finish", "arguments": json.dumps({"success": True, "evidence": "보임"}, ensure_ascii=False),
     "result": {"status": "claimed", "reason": "보임"}},
]


def write_legacy(runs_dir, run_id, lines=LEGACY_LINES):
    path = runs_dir / f"{run_id}.jsonl"
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
    return path


def test_legacy_log_is_listed_and_opened_as_text_steps(server):
    instance, runs_dir = server
    write_legacy(runs_dir, "20261107-184207")
    (runs_dir / "notes.jsonl").write_text("{}\n", encoding="utf-8")

    page = list_page(instance)
    status, _, body = request(instance, "/api/legacy/20261107-184207")

    [run] = page["runs"]
    assert run["run_id"] == "20261107-184207"
    assert run["kind"] == "legacy"
    assert run["status"] == "legacy"
    assert run["steps"] == 4
    assert run["date"] == "2026-11-07"
    assert status == 200
    opened = json.loads(body)
    assert opened["run"]["run_id"] == "20261107-184207"
    steps = opened["steps"]
    assert [step["tool"] for step in steps] == ["launch_target", "observe", "toggle", "finish"]
    assert steps[1]["arguments"] == {"window_id": "w-1", "surface": "window"}
    assert steps[0]["ok"] is True and "창 2개" in steps[0]["summary"]
    assert "관찰 9001줄" in steps[1]["summary"]
    assert len(steps[1]["observation"]) <= server_module.history.LEGACY_OBSERVATION_CHARS + 1
    assert steps[2]["ok"] is False
    assert "ACTION_FAILED" in steps[2]["summary"] and "delivered_unverified" in steps[2]["summary"]
    assert "claimed" in steps[3]["summary"]
    assert len(body) < 40_000


def test_legacy_endpoint_rejects_unknown_or_bad_ids(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")

    for path in ("/api/legacy/20261107-120000", "/api/legacy/..%2Frun", "/api/legacy/20261107-184207%0A"):
        status, _, _ = request(instance, path)
        assert status == 404, path


def test_one_run_summary_is_served_by_id(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000", steps=3)

    status, _, body = request(instance, "/api/runs/20261107-120000")
    missing, _, _ = request(instance, "/api/runs/20261107-130000")
    bad, _, _ = request(instance, "/api/runs/..%2F..%2Frun.py")

    assert status == 200
    assert json.loads(body)["run"]["steps"] == 3
    assert missing == 404
    assert bad == 404


def test_run_list_filters_by_goal_or_exe_name_ignoring_case(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000", goal="Open the SETTINGS panel", exe="C:\\a\\Chart.exe")
    write_meta(runs_dir, "20261107-130000", goal="지표를 켜줘", exe="C:\\settings\\Footprint.exe")
    write_meta(runs_dir, "20261107-140000", goal="표를 읽어줘", exe="C:\\a\\Other.exe")
    write_legacy(runs_dir, "20261107-150000")

    assert set(by_id(list_page(instance, "?q=settings"))) == {"20261107-120000"}
    assert set(by_id(list_page(instance, "?q=FOOTPRINT"))) == {"20261107-130000"}
    assert set(by_id(list_page(instance, "?q=%EC%A7%80%ED%91%9C"))) == {"20261107-130000"}


def test_run_list_filters_by_status_and_date(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261106-235959", status="claimed")
    write_meta(runs_dir, "20261107-000000", status="claimed")
    write_meta(runs_dir, "20261108-090000", status="verified")
    (runs_dir / "20261108-100000").mkdir()
    write_legacy(runs_dir, "20261108-110000")

    assert set(by_id(list_page(instance, "?status=claimed"))) == {"20261106-235959", "20261107-000000"}
    assert set(by_id(list_page(instance, "?status=unreadable"))) == {"20261108-100000"}
    assert set(by_id(list_page(instance, "?status=legacy"))) == {"20261108-110000"}
    assert list_page(instance, "?status=interrupted")["runs"] == []
    assert set(by_id(list_page(instance, "?since=2026-11-07"))) == {
        "20261107-000000", "20261108-090000", "20261108-100000", "20261108-110000"}
    assert set(by_id(list_page(instance, "?since=2026-11-08&status=verified"))) == {"20261108-090000"}


def test_run_list_pages_with_a_before_cursor(server):
    instance, runs_dir = server
    ids = [f"20261107-10{minute:02d}00" for minute in range(7)]
    for run_id in ids:
        write_meta(runs_dir, run_id)
    newest_first = list(reversed(ids))

    first = list_page(instance, "?limit=3")
    second = list_page(instance, f"?limit=3&before={first['next_before']}")
    third = list_page(instance, f"?limit=3&before={second['next_before']}")
    default = list_page(instance)

    assert [run["run_id"] for run in first["runs"]] == newest_first[:3]
    assert first["next_before"] == newest_first[2]
    assert [run["run_id"] for run in second["runs"]] == newest_first[3:6]
    assert [run["run_id"] for run in third["runs"]] == newest_first[6:]
    assert third["next_before"] is None
    assert len(default["runs"]) == 7 and default["next_before"] is None


def test_run_list_default_limit_is_50(server):
    instance, runs_dir = server
    for index in range(55):
        write_meta(runs_dir, f"20261107-12{index // 60:02d}{index % 60:02d}")

    page = list_page(instance)

    assert len(page["runs"]) == 50
    assert page["next_before"] == page["runs"][-1]["run_id"]


def test_bad_run_list_parameters_are_400(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")
    queries = [
        "?status=bogus", "?since=2026-13-01", "?since=yesterday", "?since=2026-11-7", "?since=20261107", "?limit=0", "?limit=201",
        "?limit=abc", "?limit=-1", "?before=..%2Frun", "?before=20261107-120000%0A", "?q=" + "x" * 201,
    ]

    for query in queries:
        status, _, body = request(instance, f"/api/runs{query}")
        assert status == 400, query
        assert json.loads(body)["error"]


def test_run_list_reads_each_run_json_once_until_it_changes(server, monkeypatch):
    instance, runs_dir = server
    for index in range(300):
        write_meta(runs_dir, f"20261107-{10 + index // 60:02d}{index % 60:02d}00", steps=index)
    loads = []
    original = server_module.history.load_json

    def counting(path):
        loads.append(path)
        return original(path)

    monkeypatch.setattr(server_module.history, "load_json", counting)

    first = list_page(instance, "?limit=200")
    first_loads = len(loads)
    loads.clear()
    second = list_page(instance, "?limit=200")
    second_loads = len(loads)
    write_meta(runs_dir, "20261107-100000", steps=12345, goal="바뀐 지시")
    third = list_page(instance, "?limit=200&before=20261107-100100")

    assert first_loads == 300
    assert second_loads == 0
    assert second["runs"] == first["runs"]
    assert len(loads) == 1
    assert third["runs"][0]["goal"] == "바뀐 지시"


NOW = datetime(2026, 11, 8, 2, 0, 0, tzinfo=timezone.utc)
LIVE_START = "2026-11-08T01:59:00.500Z"


def running_v2(runs_dir, run_id, **fields):
    meta = {"format": 2, "status": "running", "started_at": "2026-11-08T01:59:00Z", "finished_at": None,
            "heartbeat_at": "2026-11-08T01:59:55Z", "pid": 4321, "process_started_at": LIVE_START, **fields}
    return write_meta(runs_dir, run_id, **meta)


def judged_index(runs_dir, live=None, now=NOW):
    lookups = []
    processes = {4321: LIVE_START} if live is None else live

    def lookup(pid):
        lookups.append(pid)
        return processes.get(pid)

    return history_module.RunIndex(runs_dir, clock=lambda: now, process_started_at=lookup), lookups


def statuses(index):
    return {run["run_id"]: (run["status"], run.get("interrupted_reason")) for run in index.summaries()}


def test_a_running_run_whose_process_is_alive_with_a_fresh_heartbeat_stays_running(tmp_path):
    running_v2(tmp_path, "20261108-015900")
    index, lookups = judged_index(tmp_path)

    assert statuses(index) == {"20261108-015900": ("running", None)}
    assert lookups == [4321]


def test_a_running_run_without_a_pid_is_interrupted(tmp_path):
    running_v2(tmp_path, "20261108-015900", pid=None)
    index, lookups = judged_index(tmp_path)

    status, reason = statuses(index)["20261108-015900"]
    assert status == "interrupted"
    assert "프로세스" in reason
    assert lookups == []


def test_a_running_run_whose_process_is_gone_is_interrupted(tmp_path):
    running_v2(tmp_path, "20261108-015900")
    index, _ = judged_index(tmp_path, live={})

    status, reason = statuses(index)["20261108-015900"]
    assert status == "interrupted"
    assert reason == "기록하던 프로세스가 없음"


def test_a_reused_pid_with_another_start_time_is_interrupted(tmp_path):
    running_v2(tmp_path, "20261108-015900")
    index, _ = judged_index(tmp_path, live={4321: "2026-11-08T01:59:30.000Z"})

    status, reason = statuses(index)["20261108-015900"]
    assert status == "interrupted"
    assert "다른 프로세스" in reason


def test_a_heartbeat_older_than_60_seconds_is_interrupted(tmp_path):
    running_v2(tmp_path, "20261108-015900", heartbeat_at="2026-11-08T01:58:59Z")
    running_v2(tmp_path, "20261108-015901", heartbeat_at="2026-11-08T01:59:00Z")
    index, _ = judged_index(tmp_path)

    judged = statuses(index)
    assert judged["20261108-015900"][0] == "interrupted"
    assert "60초" in judged["20261108-015900"][1]
    assert judged["20261108-015901"] == ("running", None)


def test_a_format_1_running_record_is_interrupted_only_after_an_hour(tmp_path):
    write_meta(tmp_path, "20261108-005959", status="running", started_at="2026-11-08T00:59:59Z")
    write_meta(tmp_path, "20261108-010000", status="running", started_at="2026-11-08T01:00:00Z")
    index, lookups = judged_index(tmp_path)

    judged = statuses(index)
    assert judged["20261108-005959"][0] == "interrupted"
    assert "1시간" in judged["20261108-005959"][1]
    assert judged["20261108-010000"] == ("running", None)
    assert lookups == []


def test_finished_runs_are_not_judged(tmp_path):
    write_meta(tmp_path, "20261107-120000", status="verified", pid=4321, heartbeat_at="2026-11-07T12:00:00Z")
    index, lookups = judged_index(tmp_path, live={})

    assert statuses(index) == {"20261107-120000": ("verified", None)}
    assert lookups == []


def test_the_judgment_follows_the_clock_even_when_run_json_is_cached(tmp_path):
    running_v2(tmp_path, "20261108-015900")
    now = [NOW]
    index = history_module.RunIndex(tmp_path, clock=lambda: now[0], process_started_at=lambda pid: LIVE_START)

    first = statuses(index)["20261108-015900"][0]
    now[0] = NOW + timedelta(seconds=120)
    later = statuses(index)["20261108-015900"][0]

    assert (first, later) == ("running", "interrupted")


def test_interrupted_runs_show_in_the_list_filter_and_run_info(tmp_path):
    runs_dir = tmp_path / "runs"
    running_v2(runs_dir, "20261108-015900", pid=None)
    running_v2(runs_dir, "20261108-015800")
    index, _ = judged_index(runs_dir)
    instance = make_server(runs_dir, TOKEN, port=0, history=index)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        interrupted = list_page(instance, "?status=interrupted")
        running = list_page(instance, "?status=running")
        status, _, body = request(instance, "/api/runs/20261108-015900")
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)

    assert [run["run_id"] for run in interrupted["runs"]] == ["20261108-015900"]
    assert [run["run_id"] for run in running["runs"]] == ["20261108-015800"]
    assert status == 200
    assert json.loads(body)["run"]["status"] == "interrupted"
    assert json.loads(body)["run"]["interrupted_reason"]


def test_a_record_without_its_own_start_time_falls_back_to_the_process_and_heartbeat(tmp_path):
    running_v2(tmp_path, "20261108-015900", process_started_at=None)
    alive, _ = judged_index(tmp_path)
    gone, _ = judged_index(tmp_path, live={})

    assert statuses(alive)["20261108-015900"] == ("running", None)
    assert statuses(gone)["20261108-015900"][0] == "interrupted"


KST = timezone(timedelta(hours=9))


def mixed_runs(runs_dir):
    write_meta(runs_dir, "20261107-132854", started_at="2026-11-07T13:28:54Z")
    write_meta(runs_dir, "20261107-121336", started_at="2026-11-07T12:13:36Z")
    write_meta(runs_dir, "20261107-120000-2", started_at="2026-11-07T12:00:00Z")
    write_meta(runs_dir, "20261107-120000", started_at="2026-11-07T12:00:00Z")
    write_legacy(runs_dir, "20261107-204022")
    write_legacy(runs_dir, "20261107-230000")
    write_legacy(runs_dir, "20261107-210500")
    write_legacy(runs_dir, "20261108-050000")


MIXED_NEWEST_FIRST = [
    ("legacy", "20261108-050000"),
    ("legacy", "20261107-230000"),
    ("run", "20261107-132854"),
    ("run", "20261107-121336"),
    ("legacy", "20261107-210500"),
    ("run", "20261107-120000-2"),
    ("run", "20261107-120000"),
    ("legacy", "20261107-204022"),
]


def test_old_log_names_are_local_time_and_sort_by_their_utc_moment(tmp_path):
    mixed_runs(tmp_path)
    index = history_module.RunIndex(tmp_path, local_timezone=KST)

    runs = index.summaries()

    assert [(run["kind"], run["run_id"]) for run in runs] == MIXED_NEWEST_FIRST
    legacy = {run["run_id"]: run for run in runs if run["kind"] == "legacy"}
    assert legacy["20261107-204022"]["sort_at"] == "2026-11-07T11:40:22Z"
    assert legacy["20261107-204022"]["started_at"] == "2026-11-07T11:40:22Z"
    assert legacy["20261108-050000"]["date"] == "2026-11-07"
    assert runs[2]["sort_at"] == "2026-11-07T13:28:54Z"
    assert (index.latest()["kind"], index.latest()["run_id"]) == ("legacy", "20261108-050000")


def test_latest_is_the_recorded_run_when_old_logs_are_older_in_utc(tmp_path):
    write_meta(tmp_path, "20261107-132854", started_at="2026-11-07T13:28:54Z")
    write_legacy(tmp_path, "20261107-204022")

    index = history_module.RunIndex(tmp_path, local_timezone=KST)

    assert (index.latest()["kind"], index.latest()["run_id"]) == ("run", "20261107-132854")


def test_since_uses_the_utc_date_of_old_logs(tmp_path):
    mixed_runs(tmp_path)
    index = history_module.RunIndex(tmp_path, local_timezone=KST)

    shown, _ = index.page(history_module.parse_query({"since": ["2026-11-08"]}))

    assert shown == []


def test_paging_a_mixed_list_two_at_a_time_misses_and_repeats_nothing(tmp_path):
    runs_dir = tmp_path / "runs"
    mixed_runs(runs_dir)
    index = history_module.RunIndex(runs_dir, local_timezone=KST)
    instance = make_server(runs_dir, TOKEN, port=0, history=index)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    seen, cursors = [], []
    try:
        query = "?limit=2"
        while True:
            page = list_page(instance, query)
            seen += [(run["kind"], run["run_id"]) for run in page["runs"]]
            if page["next_before"] is None:
                break
            cursors.append(page["next_before"])
            query = f"?limit=2&before={page['next_before']}"
            assert len(cursors) < 10
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)

    assert seen == MIXED_NEWEST_FIRST
    assert cursors == ["legacy:20261107-230000", "20261107-121336", "20261107-120000-2"]


def test_changes_sample_run_records_states_and_values_in_format_2(sample_server):
    status, _, body = request(sample_server, "/api/runs/20261108-005000/events")
    events = json.loads(body)["events"]
    before, after, _ = [event for event in events if event["type"] == "observation"]
    action = next(event for event in events if event["type"] == "action")
    keyed = lambda observation: [(item["kind"], item["role"], item["name"], tuple(item["states"]), item["value"])
                                 for item in observation["elements"]]

    assert status == 200
    assert action["before"] == before["id"] and after["source"] == "action"
    assert all("states" in item and "value" in item for item in before["elements"] + after["elements"])
    assert ("control", "switch", "5분", (), None) in keyed(before)
    assert ("control", "switch", "5분", ("checked",), None) in keyed(after)
    assert [entry for entry in keyed(before) if entry[2] == "표시"] == [
        ("control", "checkbox", "표시", ("checked",), None), ("control", "checkbox", "표시", (), None)]
    assert len([entry for entry in keyed(before) if entry[2] == "적용"]) == 2
    assert len([entry for entry in keyed(after) if entry[2] == "적용"]) == 1
    assert [event["approved"] for event in events if event["type"] == "decision"] == [True, False]


def all_files(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def test_keep_mark_is_created_and_removed(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")

    status, body = post_json(instance, "/api/runs/20261107-120000/keep", {"keep": True})
    again, _ = post_json(instance, "/api/runs/20261107-120000/keep", {"keep": True})
    kept = (runs_dir / "20261107-120000" / "keep").is_file()
    removed, removed_body = post_json(instance, "/api/runs/20261107-120000/keep", {"keep": False})
    removed_again, _ = post_json(instance, "/api/runs/20261107-120000/keep", {"keep": False})

    assert (status, body) == (200, {"keep": True})
    assert again == 200
    assert kept
    assert (removed, removed_body) == (200, {"keep": False})
    assert removed_again == 200
    assert not (runs_dir / "20261107-120000" / "keep").exists()


def test_keep_mark_keeps_an_existing_file_untouched(server):
    instance, runs_dir = server
    run = write_meta(runs_dir, "20261107-120000")
    (run / "keep").write_text("", encoding="utf-8")

    status, _ = post_json(instance, "/api/runs/20261107-120000/keep", {"keep": True})

    assert status == 200
    assert (run / "keep").read_text(encoding="utf-8") == ""


def test_an_old_log_cannot_be_kept(server):
    instance, runs_dir = server
    write_legacy(runs_dir, "20261107-184207")
    before = all_files(runs_dir)

    status, body = post_json(instance, "/api/runs/20261107-184207/keep", {"keep": True})

    assert status == 400
    assert body["error"]
    assert all_files(runs_dir) == before


def test_keep_needs_the_token_and_a_good_body(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")
    before = all_files(runs_dir.parent)
    path = "/api/runs/20261107-120000/keep"

    no_token, _ = post_json(instance, path, {"keep": True}, {"X-Agent-Token": None})
    wrong_host, _ = post_json(instance, path, {"keep": True}, {"Host": "attacker.example"})
    bad = [post_json(instance, path, payload)[0] for payload in ({"keep": "yes"}, {"keep": 1}, {}, [True])]
    not_json, _, _ = request(instance, path, method="POST", body=b"{keep")
    wrong_type, _, _ = request(instance, path, {"Content-Type": "text/plain"}, method="POST", body=b'{"keep": true}')

    assert no_token == 403
    assert wrong_host == 403
    assert bad == [400, 400, 400, 400]
    assert not_json == 400
    assert wrong_type == 415
    assert all_files(runs_dir.parent) == before


def test_keep_for_unknown_or_bad_ids_writes_nothing(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")
    (runs_dir.parent / "escape").mkdir()
    before = all_files(runs_dir.parent)

    for path in ("/api/runs/20261107-130000/keep", "/api/runs/..%2F..%2Frun.py/keep", "/api/runs/20261107-120000%0A/keep",
                 "/api/runs/..%2F20261107-120000/keep", "/api/runs/..%2Fescape/keep", "/api/runs/..%5Cescape/keep"):
        status, _ = post_json(instance, path, {"keep": True})
        assert status == 404, path

    assert all_files(runs_dir.parent) == before


@pytest.mark.skipif(not hasattr(__import__("_winapi"), "CreateJunction") if __import__("sys").platform == "win32" else True,
                    reason="needs a Windows junction")
def test_keep_refuses_a_run_folder_that_points_elsewhere(server, tmp_path):
    import _winapi

    instance, runs_dir = server
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "run.json").write_text('{"status": "verified"}', encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(runs_dir / "20261107-120000"))

    status, _ = post_json(instance, "/api/runs/20261107-120000/keep", {"keep": True})

    assert status == 400
    assert not (outside / "keep").exists()


def test_run_list_shows_keep_and_filters_with_keep_1(server):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000", status="claimed")
    write_meta(runs_dir, "20261107-130000", status="verified")
    write_meta(runs_dir, "20261107-140000", status="claimed")
    write_legacy(runs_dir, "20261107-150000")
    (runs_dir / "20261107-120000" / "keep").write_text("", encoding="utf-8")
    (runs_dir / "20261107-130000" / "keep").write_text("", encoding="utf-8")
    write_legacy(runs_dir, "20261107-130000")

    legacy_keep = [run["keep"] for run in list_page(instance)["runs"] if run["kind"] == "legacy"]
    kept = by_id(list_page(instance, "?keep=1"))
    kept_claimed = by_id(list_page(instance, "?keep=1&status=claimed"))
    status, _, body = request(instance, "/api/runs/20261107-130000")

    assert {(run["kind"], run["run_id"]): run["keep"] for run in list_page(instance)["runs"]} == {
        ("run", "20261107-120000"): True, ("run", "20261107-130000"): True, ("run", "20261107-140000"): False,
        ("legacy", "20261107-150000"): False, ("legacy", "20261107-130000"): False}
    assert legacy_keep == [False, False]
    assert set(kept) == {"20261107-120000", "20261107-130000"}
    assert set(kept_claimed) == {"20261107-120000"}
    assert status == 200 and json.loads(body)["run"]["keep"] is True


def test_keep_is_read_every_request_even_when_run_json_is_cached(server, monkeypatch):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")
    loads = []
    original = server_module.history.load_json
    monkeypatch.setattr(server_module.history, "load_json", lambda path: loads.append(path) or original(path))

    first = list_page(instance)["runs"][0]["keep"]
    post_json(instance, "/api/runs/20261107-120000/keep", {"keep": True})
    second = list_page(instance)["runs"][0]["keep"]

    assert (first, second) == (False, True)
    assert len(loads) == 1


@pytest.mark.parametrize("value", ["0", "true", "yes", "2"])
def test_keep_filter_accepts_only_1(server, value):
    instance, runs_dir = server
    write_meta(runs_dir, "20261107-120000")

    status, _, body = request(instance, f"/api/runs?keep={value}")

    assert status == 400
    assert json.loads(body)["error"]
