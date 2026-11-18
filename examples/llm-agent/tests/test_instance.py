import io
import json
import os
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from agent.process_identity import process_started_at
from viewer import instance
from viewer.instance import (
    INSTANCE_FILE,
    PORT_TRIES,
    TAKEOVER_WAIT_S,
    clear_instance,
    instance_alive,
    open_viewer,
    read_instance,
    request_shutdown,
    write_instance,
)
from viewer.server import make_server

PORT = 8765
TOKEN = "instance-token"
OLD = {"pid": 4242, "started_at": "2026-11-18T08:00:00.000Z", "port": PORT, "token": "old-token"}


def test_contract_values():
    assert (INSTANCE_FILE, TAKEOVER_WAIT_S, PORT_TRIES) == (".viewer.json", 10, 20)


def test_write_read_and_clear_only_with_the_same_token(tmp_path):
    path = write_instance(tmp_path, PORT, TOKEN, pid=1234, started_at="2026-11-18T09:00:00.000Z")

    assert path == tmp_path / INSTANCE_FILE
    assert read_instance(tmp_path) == {"pid": 1234, "started_at": "2026-11-18T09:00:00.000Z",
                                       "port": PORT, "token": TOKEN}
    assert [entry.name for entry in tmp_path.iterdir()] == [INSTANCE_FILE]
    clear_instance(tmp_path, "someone-else")
    assert path.is_file()
    clear_instance(tmp_path, TOKEN)
    assert not path.exists()
    clear_instance(tmp_path, TOKEN)


def test_write_defaults_to_this_process(tmp_path):
    write_instance(tmp_path / "new-runs", PORT, TOKEN)

    info = read_instance(tmp_path / "new-runs")
    assert info["pid"] == os.getpid()
    assert info["started_at"] == process_started_at(os.getpid())


@pytest.mark.parametrize("text", [
    "", "not json", "[]", '"x"',
    json.dumps({**OLD, "pid": "4242"}), json.dumps({**OLD, "pid": True}), json.dumps({**OLD, "pid": 0}),
    json.dumps({**OLD, "port": 0}), json.dumps({**OLD, "port": 70000}), json.dumps({**OLD, "port": None}),
    json.dumps({**OLD, "token": ""}), json.dumps({**OLD, "token": 5}), json.dumps({**OLD, "started_at": 5}),
])
def test_broken_instance_file_reads_as_none(tmp_path, text):
    (tmp_path / INSTANCE_FILE).write_text(text, encoding="utf-8")

    assert read_instance(tmp_path) is None


def test_missing_instance_file_reads_as_none(tmp_path):
    assert read_instance(tmp_path) is None
    assert read_instance(tmp_path / "absent") is None


def test_undecodable_instance_file_reads_as_none(tmp_path):
    (tmp_path / INSTANCE_FILE).write_bytes(b"\xff\xfe\x00")

    assert read_instance(tmp_path) is None


def test_alive_needs_the_same_start_time():
    assert instance_alive(OLD, started_at_of=lambda pid: OLD["started_at"]) is True
    assert instance_alive(OLD, started_at_of=lambda pid: "2026-11-18T09:30:00.000Z") is False
    assert instance_alive(OLD, started_at_of=lambda pid: None) is False
    assert instance_alive({**OLD, "started_at": None}, started_at_of=lambda pid: None) is False
    assert instance_alive(None) is False


def test_this_process_is_alive_by_its_real_start_time(tmp_path):
    write_instance(tmp_path, PORT, TOKEN)

    assert instance_alive(read_instance(tmp_path)) is (os.name == "nt")


class Ports:
    """make(port) for open_viewer: ports in `busy` raise OSError, others open. A shutdown that
    answers 202 frees its port after `frees_after` more tries."""

    def __init__(self, busy, frees_after=0):
        self.busy = set(busy)
        self.tried = []
        self.frees_after = frees_after
        self.freeing = None

    def make(self, port):
        self.tried.append(port)
        if self.freeing == port:
            if self.frees_after <= 0:
                self.busy.discard(port)
            self.frees_after -= 1
        if port in self.busy:
            raise OSError(10048, "in use")
        return SimpleNamespace(port=port)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def opened(ports, *, read=lambda runs_dir: None, alive=lambda info: True, answer=(202, {}), clock=None):
    clock = clock or Clock()
    asked = []

    def shutdown(info, stop_run):
        asked.append((info["port"], stop_run))
        if answer[0] == 202:
            ports.freeing = info["port"]
        return answer

    server, notice = open_viewer("runs", PORT, ports.make, read=read, alive=alive, shutdown=shutdown,
                                 sleep=clock.sleep, clock=clock)
    return server, notice, asked


def test_free_port_opens_without_asking_anyone(tmp_path):
    ports = Ports(busy=())
    reads = []

    server, notice, asked = opened(ports, read=lambda runs_dir: reads.append(runs_dir) or OLD)

    assert server.port == PORT and notice is None
    assert asked == [] and reads == []


def test_idle_old_viewer_is_shut_down_and_its_port_reused(tmp_path):
    ports = Ports(busy={PORT}, frees_after=2)
    clock = Clock()

    server, notice, asked = opened(ports, read=lambda runs_dir: OLD, clock=clock)

    assert server.port == PORT
    assert asked == [(PORT, False)]
    assert "이전 뷰어" in notice and str(PORT) in notice
    assert ports.tried == [PORT, PORT, PORT, PORT]
    assert clock.now <= TAKEOVER_WAIT_S


def test_old_viewer_with_a_run_is_left_alone(tmp_path):
    ports = Ports(busy={PORT})

    server, notice, asked = opened(ports, read=lambda runs_dir: OLD, answer=(409, {"active": "20261118-080000"}))

    assert server.port == PORT + 1
    assert asked == [(PORT, False)]
    assert "실행 중" in notice and str(PORT + 1) in notice


def test_old_viewer_that_does_not_go_away_in_time_moves_on(tmp_path):
    ports = Ports(busy={PORT}, frees_after=10_000)
    clock = Clock()

    server, notice, asked = opened(ports, read=lambda runs_dir: OLD, clock=clock)

    assert server.port == PORT + 1
    assert TAKEOVER_WAIT_S <= clock.now < TAKEOVER_WAIT_S + 1
    assert "꺼지지 않았습니다" in notice


@pytest.mark.parametrize("answer", [(None, {"error": "URLError"}), (403, {}), (404, {})])
def test_old_viewer_that_refuses_or_cannot_be_reached_moves_on(tmp_path, answer):
    ports = Ports(busy={PORT})

    server, notice, _ = opened(ports, read=lambda runs_dir: OLD, answer=answer)

    assert server.port == PORT + 1
    assert "답하지 않았습니다" in notice


@pytest.mark.parametrize("case", ["no file", "dead process", "other port"])
def test_a_port_held_by_something_else_moves_to_the_next(tmp_path, case):
    info = {"no file": None, "dead process": OLD, "other port": {**OLD, "port": PORT + 5}}[case]
    ports = Ports(busy={PORT})

    server, notice, asked = opened(ports, read=lambda runs_dir: info, alive=lambda found: case != "dead process")

    assert server.port == PORT + 1
    assert asked == []
    assert "다른 프로그램" in notice and str(PORT + 1) in notice


def test_a_reused_pid_gets_no_shutdown_request(tmp_path):
    ports = Ports(busy={PORT})
    someone_else = lambda info: instance_alive(info, started_at_of=lambda pid: "2026-11-18T09:59:59.000Z")  # noqa: E731

    server, _, asked = opened(ports, read=lambda runs_dir: OLD, alive=someone_else)

    assert asked == []
    assert server.port == PORT + 1


def test_old_viewer_on_a_later_port_is_found_there(tmp_path):
    ports = Ports(busy={PORT, PORT + 1})

    server, notice, asked = opened(ports, read=lambda runs_dir: {**OLD, "port": PORT + 1},
                                   answer=(409, {"active": "20261118-080000"}))

    assert asked == [(PORT + 1, False)]
    assert server.port == PORT + 2
    assert "실행 중" in notice and "다른 프로그램" not in notice


def test_all_ports_taken_raises(tmp_path):
    ports = Ports(busy=range(PORT, PORT + 100))

    with pytest.raises(OSError):
        opened(ports)

    assert ports.tried == list(range(PORT, PORT + PORT_TRIES))


def test_ports_stop_at_the_highest_port(tmp_path):
    ports = Ports(busy={65534, 65535})

    with pytest.raises(OSError):
        open_viewer("runs", 65534, ports.make, read=lambda runs_dir: None)

    assert ports.tried == [65534, 65535]


def test_port_zero_lets_the_system_pick(tmp_path):
    ports = Ports(busy=())

    server, notice = open_viewer("runs", 0, ports.make, read=lambda runs_dir: OLD)

    assert server.port == 0 and notice is None


class Response(io.BytesIO):
    def __init__(self, status, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status


def test_request_shutdown_sends_the_token_and_stop_run():
    seen = {}

    def opener(request, timeout):
        seen.update(url=request.full_url, method=request.get_method(), timeout=timeout,
                    headers={name.lower(): value for name, value in request.header_items()},
                    body=json.loads(request.data))
        return Response(202, {"shutting_down": True, "run_stopping": True})

    status, body = request_shutdown(OLD, stop_run=True, opener=opener)

    assert (status, body) == (202, {"shutting_down": True, "run_stopping": True})
    assert seen["url"] == f"http://127.0.0.1:{PORT}/api/shutdown" and seen["method"] == "POST"
    assert seen["headers"]["host"] == f"127.0.0.1:{PORT}"
    assert seen["headers"]["x-agent-token"] == "old-token"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["body"] == {"stop_run": True}
    assert seen["timeout"] == 5


def test_request_shutdown_returns_a_refusal_and_an_unreachable_viewer():
    def refusing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {},
                                     io.BytesIO(b'{"error": "busy", "active": "20261118-080000"}'))

    def unreachable(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    assert request_shutdown(OLD, False, opener=refusing) == (409, {"error": "busy", "active": "20261118-080000"})
    status, body = request_shutdown(OLD, False, opener=unreachable)
    assert status is None and "old-token" not in json.dumps(body)


def test_request_shutdown_skips_the_proxy_of_the_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    handlers = []
    real_build = instance.urllib.request.build_opener

    def build(*given):
        handlers.extend(given)
        return real_build(*given)

    monkeypatch.setattr(instance.urllib.request, "build_opener", build)
    status, _ = request_shutdown({**OLD, "port": 9}, False, timeout_s=0.5)

    proxies = [handler for handler in handlers if isinstance(handler, instance.urllib.request.ProxyHandler)]
    assert proxies and all(handler.proxies == {} for handler in proxies)
    assert status is None


def serve(runs_dir, port=0, run_manager=None):
    """A viewer that, like view.py, closes its socket once serve_forever ends."""
    server = make_server(runs_dir, TOKEN, port=port, run_manager=run_manager, allow_shutdown=True)

    def run():
        server.serve_forever(poll_interval=0.05)
        server.server_close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server, thread


def test_request_shutdown_closes_a_real_viewer(tmp_path):
    server, thread = serve(tmp_path)
    try:
        status, body = request_shutdown({**OLD, "port": server.server_port, "token": TOKEN}, stop_run=False)
        thread.join(timeout=5)
        ended = not thread.is_alive()
    finally:
        if thread.is_alive():
            server.shutdown()
        server.server_close()

    assert (status, body) == (202, {"shutting_down": True, "run_stopping": False})
    assert ended


class BusyManager:
    default_exe = default_system = None

    def active(self):
        return "20261118-080000"

    def can_answer(self):
        return False


@pytest.mark.skipif(os.name != "nt", reason="needs the Windows start time and exclusive ports")
def test_a_real_idle_old_viewer_hands_over_its_port(tmp_path):
    old, old_thread = serve(tmp_path)
    port = old.server_port
    write_instance(tmp_path, port, TOKEN)
    new = None
    try:
        new, notice = open_viewer(tmp_path, port, lambda candidate: make_server(tmp_path, "new", port=candidate))
        old_thread.join(timeout=5)
        assert new.server_port == port
        assert "이전 뷰어" in notice
        assert not old_thread.is_alive()
    finally:
        if old_thread.is_alive():
            old.shutdown()
        old.server_close()
        if new is not None:
            new.server_close()


@pytest.mark.skipif(os.name != "nt", reason="needs the Windows start time and exclusive ports")
def test_a_real_old_viewer_with_a_run_keeps_serving(tmp_path):
    old, old_thread = serve(tmp_path, run_manager=BusyManager())
    port = old.server_port
    write_instance(tmp_path, port, TOKEN)
    new = None
    try:
        new, notice = open_viewer(tmp_path, port, lambda candidate: make_server(tmp_path, "new", port=candidate))
        assert new.server_port != port
        assert "실행 중" in notice
        assert old_thread.is_alive()
    finally:
        old.shutdown()
        old.server_close()
        if new is not None:
            new.server_close()
