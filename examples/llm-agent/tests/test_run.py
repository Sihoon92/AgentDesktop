import json
import os
from types import SimpleNamespace

import pytest
import run
import yaml

from agent import settings
from agent.ask import NoAsker, TerminalAsker
from agent.registration import register_after_run
from agent.task import Task


@pytest.fixture(autouse=True)
def no_settings_file(monkeypatch, tmp_path):
    """A settings.local.yaml on this machine must not change what these tests see."""
    monkeypatch.delenv(settings.SETTINGS_ENV, raising=False)
    monkeypatch.setattr(settings, "DEFAULT_PATH", tmp_path / "absent-settings.yaml")


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


MODEL_ENV_NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "VISION_MODEL", "VISION_API_KEY",
                   "VISION_BASE_URL")


def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "ENV_FILES", [])
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path / "runs")
    for name in MODEL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AGENT_DESKTOP_BIN", raising=False)
    monkeypatch.delenv("AGENT_PROFILE", raising=False)
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


def test_a_profile_without_its_env_file_stops_before_touching_the_app(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=k\n", encoding="utf-8")
    monkeypatch.setattr(run, "ENV_FILES", [tmp_path / ".env"])
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")

    code = run.main(["--profile", "company", str(write_task(tmp_path, exe))])

    assert code == 2
    assert calls == []
    assert ".env.company" in capsys.readouterr().err


def test_the_profile_file_comes_before_the_plain_env_file(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("OPENAI_MODEL=home-model\nAGENT_PROFILE=company\n", encoding="utf-8")
    (tmp_path / ".env.company").write_text("OPENAI_MODEL=company-model\n", encoding="utf-8")
    monkeypatch.setattr(run, "ENV_FILES", [tmp_path / ".env"])
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")

    try:
        run.main([str(write_task(tmp_path, exe))])
        model = os.environ.get("OPENAI_MODEL")
    finally:
        for name in ("OPENAI_MODEL", "AGENT_PROFILE"):
            os.environ.pop(name, None)

    assert model == "company-model"
    out = capsys.readouterr().out
    assert "프로필: company" in out
    assert "company-model" not in out


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

    def agent(_model, _toolbox, _task, log, gate=None, register=None):
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

    def agent(_model, _toolbox, _task, _log, gate=None, register=None):
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
    def agent(_model, _toolbox, _task, log, gate=None, register=None):
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


def test_captures_are_written_inside_the_run_folder(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(run, "run_baseline", lambda toolbox, task: seen.append(toolbox._captures_path())
                        or run.RunResult("verified", 4, "yes"))

    run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline"])

    [run_dir] = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    assert seen == [run_dir / "captures"]


def wired(monkeypatch, tmp_path, *argv, interactive=False, status="claimed"):
    """Runs main with the agent (or baseline) replaced; returns (exit code, toolbox, register)."""
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("AGENT_KNOWLEDGE_DIR", str(tmp_path / "env-knowledge"))
    monkeypatch.setattr(run, "is_interactive", lambda: interactive)
    seen = {}

    def agent(_model, toolbox, _task, _log, gate=None, register=None):
        seen.update(toolbox=toolbox, register=register)
        return run.RunResult(status, 2, "done")

    def baseline(toolbox, _task):
        seen.update(toolbox=toolbox, register=None)
        return run.RunResult(status, 2, "done")

    monkeypatch.setattr(run, "run_agent", agent)
    monkeypatch.setattr(run, "run_baseline", baseline)
    code = run.main([str(arg) for arg in argv])
    return code, seen.get("toolbox"), seen.get("register")


def mes_exe(tmp_path):
    exe = tmp_path / "VirtualMes.exe"
    exe.write_text("", encoding="utf-8")
    return exe


def test_confirmed_exits_zero(monkeypatch, tmp_path):
    assert wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x", status="confirmed")[0] == 0


def test_interactive_runs_ask_on_the_terminal_and_others_do_not(monkeypatch, tmp_path):
    _, asked, _ = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x", interactive=True)
    _, silent, _ = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x", interactive=False)

    assert isinstance(asked.asker, TerminalAsker) and asked.can_ask
    assert isinstance(silent.asker, NoAsker) and not silent.can_ask


def test_system_defaults_to_the_exe_name_and_can_be_overridden(monkeypatch, tmp_path):
    exe = mes_exe(tmp_path)
    assert wired(monkeypatch, tmp_path, "--exe", exe, "--goal", "x")[1].knowledge.system == "virtualmes"
    assert wired(monkeypatch, tmp_path, "--exe", exe, "--goal", "x", "--system", "mes")[1].knowledge.system == "mes"


def test_task_file_system_and_knowledge_dir_are_used(monkeypatch, tmp_path):
    task = write_task(tmp_path, mes_exe(tmp_path))
    task.write_text(task.read_text(encoding="utf-8")
                    + f"system: 공정\nknowledge_dir: {tmp_path / 'task-knowledge'}\n", encoding="utf-8")

    store = wired(monkeypatch, tmp_path, task)[1].knowledge

    assert store.system == "공정" and store.folder == tmp_path / "task-knowledge" / "공정"


def test_knowledge_dir_argument_wins(monkeypatch, tmp_path):
    exe = mes_exe(tmp_path)
    store = wired(monkeypatch, tmp_path, "--exe", exe, "--goal", "x", "--knowledge-dir", tmp_path / "arg")[1].knowledge
    assert store.folder == tmp_path / "arg" / "virtualmes"


def test_knowledge_dir_falls_back_to_the_environment(monkeypatch, tmp_path):
    store = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x")[1].knowledge
    assert store.folder == tmp_path / "env-knowledge" / "virtualmes"


def test_register_is_bound_to_the_store_and_the_run(monkeypatch, tmp_path):
    _, toolbox, register = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x")

    [run_dir] = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    assert register.func is register_after_run
    assert register.keywords == {"store": toolbox.knowledge, "run_id": run_dir.name}


def test_baseline_wires_no_asker_knowledge_or_register(monkeypatch, tmp_path):
    _, toolbox, register = wired(monkeypatch, tmp_path, write_task(tmp_path, mes_exe(tmp_path)), "--baseline",
                                 interactive=True)
    assert register is None and toolbox.asker is None and toolbox.knowledge is None


def test_run_json_names_the_system(monkeypatch, tmp_path):
    wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x", "--system", "mes")
    [(meta, _)] = recorded_runs(tmp_path)
    assert meta["system"] == "mes"


def settings_file(monkeypatch, tmp_path, text):
    path = tmp_path / "settings.local.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(settings, "DEFAULT_PATH", path)
    return path


class FakeClient:
    """Stands in for the OpenAI client and records every Responses API call."""

    def __init__(self):
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output=[], usage=SimpleNamespace(total_tokens=1))


def calling_agent(monkeypatch):
    """Replaces the OpenAI client with a FakeClient and the loop with one model call."""
    client = FakeClient()
    monkeypatch.setattr(run, "make_client", lambda api_key, base_url: client)

    def agent(model, _toolbox, _task, _log, gate=None, register=None):
        model.respond("instructions", [], [])
        return run.RunResult("claimed", 1, "done")

    monkeypatch.setattr(run, "run_agent", agent)
    return client


def test_goal_run_calls_the_model_named_in_the_settings_file(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    settings_file(monkeypatch, tmp_path, "model: house-model-7\nreasoning_effort: medium\n")
    client = calling_agent(monkeypatch)

    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    assert [(call["model"], call["reasoning"]) for call in client.calls] == [("house-model-7", {"effort": "medium"})]
    [(meta, _)] = recorded_runs(tmp_path)
    assert meta["model"] == "house-model-7"


def test_goal_run_without_a_settings_file_keeps_the_code_default(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    client = calling_agent(monkeypatch)

    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    assert [call["model"] for call in client.calls] == [Task.model]


def test_settings_file_named_by_the_environment_variable_is_used(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    other = tmp_path / "elsewhere.yaml"
    other.write_text("model: env-model\n", encoding="utf-8")
    monkeypatch.setenv(settings.SETTINGS_ENV, str(other))
    client = calling_agent(monkeypatch)

    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    assert [call["model"] for call in client.calls] == ["env-model"]


def test_task_file_model_beats_the_settings_file(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    settings_file(monkeypatch, tmp_path, "model: settings-model\n")
    task = write_task(tmp_path, make_exe(tmp_path))
    task.write_text(task.read_text(encoding="utf-8") + "model: task-model\n", encoding="utf-8")
    client = calling_agent(monkeypatch)

    run.main([str(task)])

    assert [call["model"] for call in client.calls] == ["task-model"]


def test_goal_run_uses_the_settings_exe_when_exe_is_not_given(monkeypatch, tmp_path):
    exe = mes_exe(tmp_path)
    settings_file(monkeypatch, tmp_path, f"exe: '{exe}'\n")

    code, toolbox, _ = wired(monkeypatch, tmp_path, "--goal", "x")

    assert code == 3
    assert toolbox._task.exe == str(exe)
    assert toolbox.knowledge.system == "virtualmes"


def test_exe_argument_beats_the_settings_exe(monkeypatch, tmp_path):
    settings_file(monkeypatch, tmp_path, f"exe: '{tmp_path / 'Other.exe'}'\n")
    exe = mes_exe(tmp_path)

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--exe", exe, "--goal", "x")

    assert toolbox._task.exe == str(exe)


def test_settings_exe_without_goal_is_exit_2(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    settings_file(monkeypatch, tmp_path, f"exe: '{mes_exe(tmp_path)}'\n")

    assert run.main([]) == 2
    assert calls == []
    assert "--goal" in capsys.readouterr().err


@pytest.mark.parametrize("text, words", [
    ("modle: m\n", "모르는 키"),
    ("image_mode: inline\n", "image_mode"),
    ("exe: App.exe\n", "절대 경로"),
])
def test_bad_settings_file_is_exit_2_in_korean_before_anything_starts(monkeypatch, tmp_path, capsys, text, words):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    path = settings_file(monkeypatch, tmp_path, text)

    code = run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    err = capsys.readouterr().err
    assert code == 2 and calls == []
    assert err.startswith("오류: ") and words in err and str(path) in err
    assert not (tmp_path / "runs").exists()


def test_settings_system_and_image_mode_reach_the_run(monkeypatch, tmp_path):
    settings_file(monkeypatch, tmp_path, "system: 공정\nimage_mode: 'off'\n")

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x")

    assert toolbox.knowledge.system == "공정"
    assert toolbox._task.image_mode == "off"


def test_system_argument_beats_the_settings_system(monkeypatch, tmp_path):
    settings_file(monkeypatch, tmp_path, "system: 공정\n")

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x", "--system", "mes")

    assert toolbox.knowledge.system == "mes"


def test_settings_knowledge_dir_comes_before_the_environment(monkeypatch, tmp_path):
    settings_file(monkeypatch, tmp_path, "knowledge_dir: from-settings\n")

    store = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x")[1].knowledge

    assert store.folder == tmp_path / "from-settings" / "virtualmes"


def test_knowledge_dir_argument_and_task_file_beat_the_settings(monkeypatch, tmp_path):
    settings_file(monkeypatch, tmp_path, "knowledge_dir: from-settings\n")
    exe = mes_exe(tmp_path)
    task = write_task(tmp_path, exe)
    task.write_text(task.read_text(encoding="utf-8") + f"knowledge_dir: {tmp_path / 'from-task'}\n",
                    encoding="utf-8")

    by_argument = wired(monkeypatch, tmp_path, "--exe", exe, "--goal", "x", "--knowledge-dir", tmp_path / "arg")
    by_task = wired(monkeypatch, tmp_path, task)

    assert by_argument[1].knowledge.folder == tmp_path / "arg" / "virtualmes"
    assert by_task[1].knowledge.folder == tmp_path / "from-task" / "virtualmes"


def test_the_settings_file_and_its_keys_are_named_but_not_its_values(monkeypatch, tmp_path, capsys):
    path = settings_file(monkeypatch, tmp_path, "model: secret-model-name\nsystem: 공정\n")

    wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x")

    out = capsys.readouterr().out
    assert f"설정 파일: {path} (model, system)" in out
    assert "secret-model-name" not in out


def test_a_bad_system_name_stops_before_touching_the_app(monkeypatch, tmp_path, capsys):
    code, toolbox, _ = wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x", "--system", "a/b")
    assert code == 2 and toolbox is None
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_run_meta_carries_the_task_step_limit(monkeypatch, tmp_path):
    seen = []

    class WatchedRecorder(run.RunRecorder):
        def __init__(self, runs_dir, desktop, meta, *args, **kwargs):
            seen.append(dict(meta))
            super().__init__(runs_dir, desktop, meta, *args, **kwargs)

    monkeypatch.setattr(run, "RunRecorder", WatchedRecorder)
    task = write_task(tmp_path, mes_exe(tmp_path))
    task.write_text(task.read_text(encoding="utf-8") + "max_steps: 9\n", encoding="utf-8")

    wired(monkeypatch, tmp_path, task)
    wired(monkeypatch, tmp_path, task, "--baseline")
    wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x")

    assert [meta["max_steps"] for meta in seen] == [9, 9, 60]
    assert sorted(meta["max_steps"] for meta, _ in recorded_runs(tmp_path)) == [9, 9, 60]


def test_task_file_run_takes_the_settings_model_when_the_file_has_none(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    settings_file(monkeypatch, tmp_path, "model: settings-model\n")
    client = calling_agent(monkeypatch)

    run.main([str(write_task(tmp_path, make_exe(tmp_path)))])

    assert [call["model"] for call in client.calls] == ["settings-model"]


def test_the_run_ends_with_its_summary_after_the_usual_lines_and_leaves_summary_txt(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(run, "current_code", lambda: "abc1234")
    monkeypatch.setattr(run, "run_agent", agent_returning("claimed"))

    code = run.main(["--exe", str(make_exe(tmp_path)), "--goal", "화면을 연다"])

    [folder] = (tmp_path / "runs").iterdir()
    out = capsys.readouterr().out.splitlines()
    trace = next(i for i, line in enumerate(out) if line.startswith("trace: "))
    written = (folder / "summary.txt").read_text(encoding="utf-8").splitlines()
    assert code == 3
    assert out[trace + 1:] == written
    assert written[:3] == ["요약 (화면 내용 없음)", "  코드: abc1234", "  판정: claimed · 스텝 1/60 · 토큰 7"]
    assert "화면을 연다" not in "\n".join(written)


def test_run_json_keeps_the_code_version_the_run_was_made_with(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "current_code", lambda: "abc1234+수정")
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))

    run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline"])

    [(meta, _)] = recorded_runs(tmp_path)
    assert meta["code"] == "abc1234+수정"


@pytest.mark.parametrize("failure", ["make", "write"])
def test_a_summary_that_fails_only_warns_and_keeps_the_exit_code(monkeypatch, tmp_path, capsys, failure):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "current_code", lambda: "abc1234")
    monkeypatch.setattr(run, "run_baseline", lambda *a, **k: run.RunResult("verified", 4, "yes"))
    answer = (None, "RuntimeError") if failure == "make" else (["요약 (화면 내용 없음)"], "PermissionError")
    monkeypatch.setattr(run, "write_summary", lambda _path: answer)

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline"])

    captured = capsys.readouterr()
    assert code == 0
    assert answer[1] in captured.err and "요약" in captured.err
    assert ("요약 (화면 내용 없음)" in captured.out) == (failure == "write")


def fake_clients(monkeypatch):
    """Every client the run makes is a FakeClient; returns the (api_key, base_url) of each."""
    made = []
    monkeypatch.setattr(run, "make_client", lambda api_key, base_url: made.append((api_key, base_url)) or FakeClient())
    return made


def recorder_metas(monkeypatch):
    """Keeps the meta run.py hands each RunRecorder (run.json keeps only the recorder's META_FIELDS)."""
    metas = []

    class WatchedRecorder(run.RunRecorder):
        def __init__(self, runs_dir, desktop, meta, *args, **kwargs):
            metas.append(dict(meta))
            super().__init__(runs_dir, desktop, meta, *args, **kwargs)

    monkeypatch.setattr(run, "RunRecorder", WatchedRecorder)
    return metas


def toolbox_run(monkeypatch, tmp_path, *argv):
    """Runs main with the agent replaced; returns (exit code, the toolbox it got, stdout+stderr)."""
    seen = {}

    def agent(_model, toolbox, task, _log, gate=None, register=None):
        seen.update(toolbox=toolbox, task=task)
        return run.RunResult("claimed", 1, "done")

    monkeypatch.setattr(run, "run_agent", agent)
    code = run.main([str(arg) for arg in argv])
    return code, seen


def test_run_passes_env_to_task_loading(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    fake_clients(monkeypatch)
    seen = capture_agent(monkeypatch, "claimed")

    run.main([str(write_task(tmp_path, make_exe(tmp_path)))])
    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    assert [(task.model, task.model_source) for task in seen] == [("env-model", "env"), ("env-model", "env")]


def test_run_prints_the_start_line(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://text.example")
    fake_clients(monkeypatch)
    capture_agent(monkeypatch, "claimed")

    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    out = capsys.readouterr()
    assert "텍스트 모델: env · 비전 모델: 없음" in out.out.splitlines()
    assert "env-model" not in out.out + out.err and "text.example" not in out.out + out.err


def test_run_refuses_a_broken_vision_setup(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("VISION_BASE_URL", "http://vision.example")
    sessions = []
    monkeypatch.setattr(run, "start_session", lambda *_a, **_k: sessions.append(1) or "session-1")
    made = fake_clients(monkeypatch)

    code = run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    err = capsys.readouterr().err
    assert code == 2
    assert "VISION_MODEL" in err and "vision.example" not in err
    assert (calls, sessions, made) == ([], [], [])
    assert not (tmp_path / "runs").exists()


def test_run_gives_the_toolbox_the_vision(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("VISION_MODEL", "eye-model")
    monkeypatch.setenv("VISION_API_KEY", "vk")
    made = fake_clients(monkeypatch)
    metas = recorder_metas(monkeypatch)

    code, seen = toolbox_run(monkeypatch, tmp_path, "--exe", make_exe(tmp_path), "--goal", "g")

    assert code == 3
    assert seen["toolbox"].vision_enabled is True
    assert sorted(made) == [("test", None), ("vk", None)]
    out = capsys.readouterr().out
    assert "텍스트 모델: 기본값 · 비전 모델: env" in out.splitlines()
    assert [meta["vision"] for meta in metas] == [True]
    [(written, _)] = recorded_runs(tmp_path)
    assert "eye-model" not in json.dumps(metas) + json.dumps(written) and "eye-model" not in out


def test_one_model_run_gives_the_toolbox_no_vision(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    fake_clients(monkeypatch)
    metas = recorder_metas(monkeypatch)

    code, seen = toolbox_run(monkeypatch, tmp_path, "--exe", make_exe(tmp_path), "--goal", "g")

    assert code == 3
    assert seen["toolbox"].vision_enabled is False
    assert [meta["vision"] for meta in metas] == [False]


def test_run_notes_an_ignored_image_mode(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("VISION_MODEL", "eye-model")
    settings_file(monkeypatch, tmp_path, "image_mode: off\n")
    fake_clients(monkeypatch)
    capture_agent(monkeypatch, "claimed")

    run.main(["--exe", str(make_exe(tmp_path)), "--goal", "g"])

    assert "텍스트 모델: 기본값 · 비전 모델: env · image_mode 무시(나눈 방식)" in capsys.readouterr().out.splitlines()


def test_baseline_builds_no_models_and_prints_no_start_line(monkeypatch, tmp_path, capsys):
    calls = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("VISION_BASE_URL", "http://vision.example")
    made = fake_clients(monkeypatch)
    metas = recorder_metas(monkeypatch)
    monkeypatch.setattr(run, "run_baseline", lambda *_a, **_k: calls.append("baseline") or run.RunResult("stopped", 1, "no"))

    code = run.main([str(write_task(tmp_path, make_exe(tmp_path))), "--baseline"])

    assert calls == ["baseline"]
    assert code == 1
    assert made == []
    assert "텍스트 모델" not in capsys.readouterr().out
    [meta] = metas
    assert meta["model"] == "baseline" and "vision" not in meta


PATH_ENV_NAMES = ("AGENT_DESKTOP_BIN", "AGENT_SETTINGS", "AGENT_KNOWLEDGE_DIR", "AGENT_PROFILE")


def test_env_example_names_the_model_and_path_values_and_holds_none():
    from dotenv import dotenv_values

    example = run.HERE / ".env.example"
    values = dotenv_values(example)

    assert tuple(values) == MODEL_ENV_NAMES + PATH_ENV_NAMES
    assert all(value == "" for value in values.values())
    comments = [line for line in example.read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    assert len(comments) >= len(values)


def test_env_example_is_not_ignored_by_git():
    import subprocess

    result = subprocess.run(["git", "check-ignore", "-q", str(run.HERE / ".env.example")], cwd=run.HERE,
                            capture_output=True)
    ignored_env = subprocess.run(["git", "check-ignore", "-q", str(run.HERE / ".env")], cwd=run.HERE,
                                 capture_output=True)

    assert result.returncode == 1
    assert ignored_env.returncode == 0


def site_exes(tmp_path):
    """Two configured systems' executables and one app the settings do not name."""
    paths = {}
    for name, folder in (("ulsan", "SiteA"), ("cheonan", "SiteB"), ("other", "Tools")):
        target = tmp_path / folder / ("Other.exe" if name == "other" else "MES.exe")
        target.parent.mkdir(exist_ok=True)
        target.write_text("", encoding="utf-8")
        paths[name] = target
    return paths


def systems_settings(monkeypatch, tmp_path, extra=""):
    exes = site_exes(tmp_path)
    settings_file(monkeypatch, tmp_path,
                  f"systems:\n  울산: '{exes['ulsan']}'\n  천안소형: '{exes['cheonan']}'\n" + extra)
    return exes


def test_system_option_picks_its_exe(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path)

    code, toolbox, _ = wired(monkeypatch, tmp_path, "--system", "천안소형", "--goal", "x")

    assert code == 3
    assert toolbox._task.exe == str(exes["cheonan"])
    assert toolbox.knowledge.system == "천안소형"
    [(meta, _)] = recorded_runs(tmp_path)
    assert meta["system"] == "천안소형"


def test_exe_option_beats_the_system_entry(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path)

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--exe", exes["other"], "--system", "울산", "--goal", "x")

    assert toolbox._task.exe == str(exes["other"])
    assert toolbox.knowledge.system == "울산"


def test_system_option_beats_the_task_files_exe(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path)
    task = write_task(tmp_path, exes["other"])

    _, toolbox, _ = wired(monkeypatch, tmp_path, task, "--system", "울산")

    assert toolbox._task.exe == str(exes["ulsan"])
    assert toolbox._task.require == [{"role": "switch"}]
    assert toolbox.knowledge.system == "울산"


def test_task_files_exe_beats_the_settings_system_entry(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path, "system: 울산\n")

    _, toolbox, _ = wired(monkeypatch, tmp_path, write_task(tmp_path, exes["other"]))

    assert toolbox._task.exe == str(exes["other"])


def test_settings_system_picks_its_entry_before_the_settings_exe(monkeypatch, tmp_path):
    exes = site_exes(tmp_path)
    settings_file(monkeypatch, tmp_path, f"exe: '{exes['other']}'\nsystem: 천안소형\n"
                                         f"systems:\n  울산: '{exes['ulsan']}'\n  천안소형: '{exes['cheonan']}'\n")

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--goal", "x")

    assert toolbox._task.exe == str(exes["cheonan"])
    assert toolbox.knowledge.system == "천안소형"


def test_settings_exe_stays_when_the_settings_system_names_no_entry(monkeypatch, tmp_path):
    exes = site_exes(tmp_path)
    settings_file(monkeypatch, tmp_path, f"exe: '{exes['other']}'\nsystem: 공정\nsystems:\n  울산: '{exes['ulsan']}'\n")

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--goal", "x")

    assert toolbox._task.exe == str(exes["other"])
    assert toolbox.knowledge.system == "공정"


def test_unknown_system_name_is_only_a_knowledge_name(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path, f"exe: '{tmp_path / 'Tools' / 'Other.exe'}'\n")

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--system", "공정", "--goal", "x")

    assert toolbox._task.exe == str(exes["other"])
    assert toolbox.knowledge.system == "공정"


def test_unknown_system_name_without_exe_is_refused(monkeypatch, tmp_path, capsys):
    systems_settings(monkeypatch, tmp_path)

    code, toolbox, _ = wired(monkeypatch, tmp_path, "--system", "부산", "--goal", "x")

    assert code == 2 and toolbox is None
    err = capsys.readouterr().err
    assert "systems에 없습니다" in err and "울산" in err and "천안소형" in err
    assert "시스템 이름을 쓸 수 없습니다" not in err
    assert not (tmp_path / "runs").exists()


def test_systems_without_a_chosen_one_ask_for_system_or_exe(monkeypatch, tmp_path, capsys):
    systems_settings(monkeypatch, tmp_path)

    code, toolbox, _ = wired(monkeypatch, tmp_path, "--goal", "x")

    assert code == 2 and toolbox is None
    err = capsys.readouterr().err
    assert "--system" in err and "--exe" in err


def test_ready_text_reaches_the_task_for_a_configured_exe(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path, "ready_text: 생산운영\nready_timeout_s: 90\n")

    by_name = wired(monkeypatch, tmp_path, "--system", "울산", "--goal", "x")[1]._task
    by_path = wired(monkeypatch, tmp_path, "--exe", str(exes["cheonan"]).upper(), "--goal", "x")[1]._task

    assert (by_name.ready_text, by_name.ready_timeout_s) == ("생산운영", 90)
    assert (by_path.ready_text, by_path.ready_timeout_s) == ("생산운영", 90)


def test_ready_text_reaches_the_task_for_the_settings_exe(monkeypatch, tmp_path):
    exes = site_exes(tmp_path)
    settings_file(monkeypatch, tmp_path, f"exe: '{exes['other']}'\nready_text: 생산운영\n")

    task = wired(monkeypatch, tmp_path, "--goal", "x")[1]._task

    assert (task.ready_text, task.ready_timeout_s) == ("생산운영", 180)


def test_ready_text_does_not_reach_another_exe(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path, "ready_text: 생산운영\n")

    by_exe = wired(monkeypatch, tmp_path, "--exe", exes["other"], "--goal", "x")[1]._task
    by_task_file = wired(monkeypatch, tmp_path, write_task(tmp_path, exes["other"]))[1]._task

    assert by_exe.ready_text is None and by_task_file.ready_text is None


def test_task_files_ready_text_beats_the_settings(monkeypatch, tmp_path):
    exes = systems_settings(monkeypatch, tmp_path, "ready_text: 생산운영\nready_timeout_s: 90\n")
    task = write_task(tmp_path, exes["ulsan"])
    task.write_text(task.read_text(encoding="utf-8") + "ready_text: 메뉴\nready_timeout_s: 20\n", encoding="utf-8")

    seen = wired(monkeypatch, tmp_path, task)[1]._task

    assert (seen.ready_text, seen.ready_timeout_s) == ("메뉴", 20)


def test_the_ready_text_and_system_paths_are_not_printed(monkeypatch, tmp_path, capsys):
    systems_settings(monkeypatch, tmp_path, "ready_text: 생산운영\n")

    wired(monkeypatch, tmp_path, "--system", "울산", "--goal", "x")

    out = capsys.readouterr().out
    assert "생산운영" not in out and "SiteA" not in out and "SiteB" not in out
    assert "systems, ready_text" in out
