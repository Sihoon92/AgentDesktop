import json

import run
import yaml


def write_task(tmp_path, exe):
    path = tmp_path / "task.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "goal": "g",
                "exe": str(exe),
                "permission_level": 3,
                "success": {"require": [{"role": "switch"}]},
                "baseline_target": {"role": "switch"},
            }
        ),
        encoding="utf-8",
    )
    return path


def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "ENV_FILES", [])
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_DESKTOP_BIN", raising=False)
    binary = tmp_path / "agent-desktop.exe"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_DESKTOP_BIN", str(binary))
    calls = []
    monkeypatch.setattr(run, "start_session", lambda *_a, **_k: "session-1")
    monkeypatch.setattr(run, "run_agent", lambda *a, **k: calls.append("agent"))
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: calls.append("baseline"))
    return calls


def test_agent_run_without_an_api_key_stops_before_touching_the_app(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")

    code = run.main([str(write_task(tmp_path, exe))])

    assert code == 2
    assert calls == []
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_missing_exe_stops_before_touching_the_app(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    code = run.main([str(write_task(tmp_path, tmp_path / "gone.exe"))])

    assert code == 2
    assert calls == []
    assert "gone.exe" in capsys.readouterr().err


def test_missing_binary_stops_before_touching_the_app(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_DESKTOP_BIN", str(tmp_path / "missing.exe"))

    code = run.main([str(write_task(tmp_path, exe)), "--baseline"])

    assert code == 2
    assert calls == []
    assert "missing.exe" in capsys.readouterr().err


def test_baseline_needs_no_api_key_and_failure_returns_exit_1(monkeypatch, tmp_path):
    calls = isolate(monkeypatch, tmp_path)
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")

    def stopped(*_args, **_kwargs):
        calls.append("baseline")
        return run.RunResult("stopped", 1, "no")

    monkeypatch.setattr(run, "run_baseline", stopped)

    assert run.main([str(write_task(tmp_path, exe)), "--baseline"]) == 1
    assert calls == ["baseline"]


def test_verified_run_returns_exit_0(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    assert run.main([str(write_task(tmp_path, exe)), "--baseline"]) == 0


def test_failed_session_start_stops_before_touching_the_app(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "start_session", lambda *_a, **_k: None)
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")

    code = run.main([str(write_task(tmp_path, exe)), "--baseline"])

    assert code == 2
    assert calls == []
    assert "세션" in capsys.readouterr().err


def make_exe(tmp_path):
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    return exe


def capture_agent(monkeypatch, status):
    seen = []

    def agent(_model, _toolbox, task, *_args, **_kwargs):
        seen.append(task)
        return run.RunResult(status, 2, "done")

    monkeypatch.setattr(run, "run_agent", agent)
    return seen


def test_goal_and_exe_flags_run_without_a_task_file(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    seen = capture_agent(monkeypatch, "claimed")
    exe = make_exe(tmp_path)

    run.main(["--exe", str(exe), "--goal", "5분봉으로 바꿔줘", "--level", "1"])

    assert [t.goal for t in seen] == ["5분봉으로 바꿔줘"]
    assert seen[0].exe == str(exe)
    assert seen[0].permission_level == 1
    assert seen[0].require == []


def test_goal_flag_overrides_the_task_file_goal_and_keeps_its_requirements(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    seen = capture_agent(monkeypatch, "verified")

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--goal", "새 지시"])

    assert code == 0
    assert seen[0].goal == "새 지시"
    assert seen[0].require == [{"role": "switch"}]


def test_claimed_run_returns_exit_3(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    capture_agent(monkeypatch, "claimed")

    assert run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"]) == 3


def test_goal_without_exe_or_task_file_is_exit_2(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    code = run.main(["--goal", "g"])

    assert code == 2
    assert calls == []
    assert "--exe" in capsys.readouterr().err


def test_exe_without_goal_is_exit_2(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    code = run.main(["--exe", str(make_exe(tmp_path))])

    assert code == 2
    assert calls == []
    assert "--goal" in capsys.readouterr().err


def test_baseline_with_goal_flags_is_exit_2(monkeypatch, tmp_path):
    calls = isolate(monkeypatch, tmp_path)

    code = run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g", "--baseline"])

    assert code == 2
    assert calls == []


def recorded_runs(tmp_path):
    folders = sorted((tmp_path / "runs").iterdir())
    return [
        (
            json.loads((folder / "run.json").read_text(encoding="utf-8")),
            [json.loads(line)["type"] for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()],
        )
        for folder in folders
    ]


def test_agent_run_is_recorded_in_its_own_folder(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def agent(_model, _toolbox, _task, log, gate=None):
        log({"type": "model", "step": 1, "tokens": 7})
        log({"type": "tool", "step": 1, "tool": "launch_target", "arguments": "{}", "result": {"ok": True}})
        return run.RunResult("claimed", 1, "done", 7)

    monkeypatch.setattr(run, "run_agent", agent)

    code = run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    [(meta, kinds)] = recorded_runs(tmp_path)
    assert code == 3
    assert meta["goal"] == "g"
    assert (meta["status"], meta["steps"], meta["tokens"]) == ("claimed", 1, 7)
    assert kinds == ["model", "finish"]
    assert "[1] launch_target" in capsys.readouterr().out


def test_baseline_run_is_recorded_too(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline"])

    [(meta, kinds)] = recorded_runs(tmp_path)
    assert (meta["status"], meta["model"]) == ("verified", "baseline")
    assert kinds == ["finish"]


def test_approve_flag_hands_a_terminal_gate_to_the_agent(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(run, "is_interactive", lambda: True)
    gates = []

    def agent(_model, _toolbox, _task, _log, gate=None):
        gates.append(gate)
        return run.RunResult("claimed", 1, "done")

    monkeypatch.setattr(run, "run_agent", agent)

    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g", "--approve"])
    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    assert isinstance(gates[0], run.TerminalGate)
    assert gates[1] is None


def test_approve_without_a_terminal_is_exit_2(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(run, "is_interactive", lambda: False)

    code = run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g", "--approve"])

    assert code == 2
    assert calls == []
    assert "--approve" in capsys.readouterr().err


def test_approve_with_baseline_is_exit_2(monkeypatch, tmp_path):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "is_interactive", lambda: True)

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline", "--approve"])

    assert code == 2
    assert calls == []


class ViewerProbe:
    """Wraps the real viewer server and records what run.py opened."""

    def __init__(self, monkeypatch, interactive=False):
        self.servers = []
        self.opened = []
        self.waited = 0
        real_make_server = run.make_server

        def make_server(*args, **kwargs):
            server = real_make_server(*args, **kwargs)
            self.servers.append(server)
            return server

        def wait_for_enter():
            self.waited += 1

        monkeypatch.setattr(run, "make_server", make_server)
        monkeypatch.setattr(run, "open_browser", self.opened.append)
        monkeypatch.setattr(run, "wait_for_enter", wait_for_enter)
        monkeypatch.setattr(run, "is_interactive", lambda: interactive)


def api_runs(url):
    import http.client
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url)
    connection = http.client.HTTPConnection("127.0.0.1", parts.port, timeout=5)
    token = parse_qs(parts.query)["t"][0]
    connection.request("GET", "/api/runs", headers={"Host": f"127.0.0.1:{parts.port}", "X-Agent-Token": token})
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()
    return response.status, body["runs"]


def test_view_opens_the_viewer_on_this_run(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    probe = ViewerProbe(monkeypatch)
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline", "--view"])

    [folder] = (tmp_path / "runs").iterdir()
    [server] = probe.servers
    assert code == 0
    assert probe.opened == [f"http://127.0.0.1:{server.server_port}/?t={server.token}#run={folder.name}"]
    assert len(server.token) >= 40


def test_view_serves_the_running_run_on_loopback_while_it_runs(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    probe = ViewerProbe(monkeypatch)
    seen = []

    def agent(*_args, **_kwargs):
        seen.append(api_runs(probe.opened[0]))
        return run.RunResult("claimed", 1, "done")

    monkeypatch.setattr(run, "run_agent", agent)

    code = run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g", "--view"])

    [(status, runs)] = seen
    assert code == 3
    assert probe.servers[0].server_address[0] == "127.0.0.1"
    assert status == 200
    assert [(r["goal"], r["status"]) for r in runs] == [("g", "running")]


def test_view_without_a_terminal_closes_the_viewer_when_the_run_ends(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    probe = ViewerProbe(monkeypatch, interactive=False)
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("stopped", 2, "no"))

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline", "--view"])

    assert code == 1
    assert probe.waited == 0
    assert probe.servers[0].socket.fileno() == -1


def test_view_in_a_terminal_waits_for_enter_before_closing(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    probe = ViewerProbe(monkeypatch, interactive=True)
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline", "--view"])

    assert code == 0
    assert probe.waited == 1
    assert "Enter" in capsys.readouterr().out
    assert probe.servers[0].socket.fileno() == -1


def test_without_view_no_viewer_starts(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    probe = ViewerProbe(monkeypatch, interactive=True)
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline"])

    assert probe.servers == []
    assert probe.opened == []
    assert probe.waited == 0


def test_viewer_that_cannot_start_only_warns(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    probe = ViewerProbe(monkeypatch)

    def broken(*_args, **_kwargs):
        raise OSError("port busy")

    monkeypatch.setattr(run, "make_server", broken)
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline", "--view"])

    assert code == 0
    assert probe.opened == []
    assert "port busy" in capsys.readouterr().err


def agent_returning(status):
    def agent(_model, _toolbox, _task, log, gate=None):
        log({"type": "model", "step": 1, "tokens": 7})
        return run.RunResult(status, 1, "done", 7)

    return agent


def test_started_by_names_how_the_run_was_started(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(run, "run_agent", agent_returning("claimed"))
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))
    ViewerProbe(monkeypatch)
    task = str(write_task(tmp_path, make_exe(tmp_path)))

    run.main([task, "--baseline"])
    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])
    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g", "--view"])

    started_by = {meta["started_by"] for meta, _ in recorded_runs(tmp_path)}
    assert started_by == {"baseline", "run.py", "run.py --view"}
    assert all(meta["format"] == 2 for meta, _ in recorded_runs(tmp_path))


def test_run_prints_how_to_open_it_later(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline"])

    [folder] = (tmp_path / "runs").iterdir()
    lines = capsys.readouterr().out.splitlines()
    record = next(i for i, line in enumerate(lines) if line.startswith("기록: "))
    assert lines[record + 1] == f"나중에 보기: python view.py --open {folder.name}"
