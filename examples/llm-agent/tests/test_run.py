import datetime
import json
import os
import subprocess
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


# workflow: making one after a model run, replaying one, listing them (plan task 8, contract C12)

WORKFLOW_ID = "w-20261119-001"
REPLAY_DAY = datetime.date(2026, 11, 19)


def workflow_sample(tmp_path):
    from test_workflow_format import sample

    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return sample(save_example=str(out / "front.xlsx"))


def stored_workflow(tmp_path, system="virtualmes"):
    """A workflow saved under tmp_path/workflows/<system>; returns (store, the saved workflow)."""
    from agent.workflow_store import WorkflowStore

    store = WorkflowStore(tmp_path / "workflows", system)
    saved = store.add({**workflow_sample(tmp_path), "system": system}, today=REPLAY_DAY)
    return store, saved


def replay_setup(monkeypatch, tmp_path, result=None):
    """isolate, a stored workflow, AGENT_WORKFLOW_DIR on it, no API key, and run_workflow replaced:
    returns (seen calls, sessions started, the store)."""
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_WORKFLOW_DIR", str(tmp_path / "workflows"))
    store, _ = stored_workflow(tmp_path)
    sessions = []
    monkeypatch.setattr(run, "start_session", lambda *_a, **_k: sessions.append(1) or "session-1")
    monkeypatch.setattr(run, "make_client", lambda *_a, **_k: pytest.fail("a replay must not make a model client"))
    seen = []

    def replay(toolbox, task, workflow, values, log=lambda _e: None, stop=None, started_at=None):
        seen.append({"toolbox": toolbox, "task": task, "workflow": workflow, "values": values})
        log({"type": "workflow_step", "n": 1, "kind": "act", "ok": True, "code": None, "ms": 5, "goal": "메뉴 목록이 열림"})
        steps = len(workflow["steps"])
        return result or run.RunResult("verified", steps, "workflow 통과",
                                       stats={"workflow_run": {"id": workflow["id"], "steps": steps,
                                                               "steps_done": steps, "code": None}})

    monkeypatch.setattr(run, "run_workflow", replay)
    return seen, sessions, store


def test_workflow_replay_runs_without_a_model_or_api_key(monkeypatch, tmp_path, capsys):
    seen, _, _ = replay_setup(monkeypatch, tmp_path)

    code = run.main(["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID, "--param", "shop=WA0, WA2"])

    assert code == 0
    [call] = seen
    assert call["workflow"]["id"] == WORKFLOW_ID
    assert call["values"]["shop"] == ["WA0", "WA2"]
    assert call["values"]["save_path"].endswith(".xlsx") and call["values"]["save_path"] != workflow_sample(
        tmp_path)["params"]["save_path"]["example"]
    task = call["task"]
    assert (task.goal, task.permission_level, task.exe) == (call["workflow"]["goal"], 2, str(mes_exe(tmp_path)))
    toolbox = call["toolbox"]
    assert toolbox._gated is False and isinstance(toolbox.asker, NoAsker) and toolbox.knowledge is None
    [(meta, kinds)] = recorded_runs(tmp_path)
    assert meta["started_by"] == "workflow" and meta["model"] == "workflow" and meta["status"] == "verified"
    assert meta["workflow_run"] == {"id": WORKFLOW_ID, "steps": 10, "steps_done": 10, "code": None}
    assert "workflow_step" in kinds
    assert "OPENAI_API_KEY" not in capsys.readouterr().err


def test_replay_values_are_resolved_with_the_real_run_id(monkeypatch, tmp_path):
    seen, _, _ = replay_setup(monkeypatch, tmp_path)

    run.main(["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID])

    [run_dir] = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    assert run_dir.name in seen[0]["values"]["save_path"]
    assert "check" not in seen[0]["values"]["save_path"]


def test_replay_updates_the_store(monkeypatch, tmp_path):
    _, _, store = replay_setup(monkeypatch, tmp_path)

    assert run.main(["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID]) == 0
    assert store.get(WORKFLOW_ID)["status"] == "verified" and store.get(WORKFLOW_ID)["passes"] == 1

    stopped = run.RunResult("stopped", 3, "EXPECT_UNMET",
                            stats={"workflow_run": {"id": WORKFLOW_ID, "steps": 10, "steps_done": 3,
                                                    "code": "EXPECT_UNMET"}})
    (tmp_path / "second").mkdir()
    _, _, store = replay_setup(monkeypatch, tmp_path / "second", result=stopped)
    assert run.main(["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID]) == 1
    after = store.get(WORKFLOW_ID)
    assert after["failures"] == 1 and after["status"] == "unverified"


def test_replay_takes_the_settings_ready_signal_for_its_exe(monkeypatch, tmp_path):
    seen, _, _ = replay_setup(monkeypatch, tmp_path)
    exe = mes_exe(tmp_path)
    settings_file(monkeypatch, tmp_path, f"systems:\n  virtualmes: '{exe}'\nready_text: 준비\n")

    run.main(["--system", "virtualmes", "--workflow", WORKFLOW_ID])

    assert seen[0]["task"].exe == str(exe) and seen[0]["task"].ready_text == "준비"


@pytest.mark.parametrize("extra, text", [
    (["--workflow", "w-20990101-001"], "WORKFLOW_NOT_FOUND"),
    (["--workflow", WORKFLOW_ID, "--param", "nope=1"], "PARAM_UNKNOWN"),
    (["--workflow", WORKFLOW_ID, "--param", "period=전주"], "PARAM_UNKNOWN"),
    (["--workflow", WORKFLOW_ID, "--param", "shop"], "이름=값"),
    (["--workflow", WORKFLOW_ID, "--param", "shop=WA0,,WA1"], "PARAM_INVALID"),
    (["--workflow", WORKFLOW_ID, "--param", "save_path=out.xlsx"], "PARAM_INVALID"),
    (["--workflow", WORKFLOW_ID, "--goal", "g"], "함께 쓸 수 없습니다"),
    (["--workflow", WORKFLOW_ID, "--baseline"], "함께 쓸 수 없습니다"),
    (["--workflow", WORKFLOW_ID, "--make-workflow", "latest"], "함께 쓸 수 없습니다"),
    (["--workflow", WORKFLOW_ID, "--system", "다른시스템"], "SYSTEM_MISMATCH"),
    (["--param", "shop=WA0"], "--workflow"),
])
def test_bad_replay_arguments_fail_before_anything_starts(monkeypatch, tmp_path, capsys, extra, text):
    seen, sessions, _ = replay_setup(monkeypatch, tmp_path)

    code = run.main(["--exe", str(mes_exe(tmp_path)), *extra])

    assert code == 2
    assert text in capsys.readouterr().err
    assert seen == [] and sessions == []
    assert not (tmp_path / "runs").exists()


def test_an_existing_save_path_is_refused_before_anything_starts(monkeypatch, tmp_path, capsys):
    seen, sessions, _ = replay_setup(monkeypatch, tmp_path)
    taken = tmp_path / "out" / "taken.xlsx"
    taken.write_bytes(b"x")

    code = run.main(["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID, "--param", f"save_path={taken}"])

    assert code == 2 and "FILE_EXISTS" in capsys.readouterr().err
    assert seen == [] and sessions == [] and not (tmp_path / "runs").exists()


def test_a_broken_workflow_file_is_refused_before_anything_starts(monkeypatch, tmp_path, capsys):
    seen, sessions, store = replay_setup(monkeypatch, tmp_path)
    (store.folder / f"{WORKFLOW_ID}.yaml").write_text("format: 1\nsteps: [\n", encoding="utf-8")

    code = run.main(["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID])

    assert code == 2 and "WORKFLOW_INVALID" in capsys.readouterr().err
    assert seen == [] and sessions == []


def test_workflow_replay_with_a_task_file_is_refused(monkeypatch, tmp_path, capsys):
    seen, sessions, _ = replay_setup(monkeypatch, tmp_path)

    code = run.main([str(write_task(tmp_path, mes_exe(tmp_path))), "--workflow", WORKFLOW_ID])

    assert code == 2 and "함께 쓸 수 없습니다" in capsys.readouterr().err
    assert seen == [] and sessions == []


def test_workflows_lists_ids_status_and_param_names(monkeypatch, tmp_path, capsys):
    _, sessions, store = replay_setup(monkeypatch, tmp_path)
    (store.folder / "w-20261119-002.yaml").write_text("format: [broken\n", encoding="utf-8")

    code = run.main(["--system", "virtualmes", "--workflows"])

    out = capsys.readouterr().out
    assert code == 0
    assert WORKFLOW_ID in out and "unverified" in out and "shop" in out and "save_path" in out
    assert "w-20261119-002" not in out
    assert sessions == [] and not (tmp_path / "runs").exists()


def test_workflows_of_a_system_without_any_say_so(monkeypatch, tmp_path, capsys):
    replay_setup(monkeypatch, tmp_path)

    code = run.main(["--system", "없는시스템", "--workflows"])

    assert code == 0 and WORKFLOW_ID not in capsys.readouterr().out


@pytest.mark.parametrize("where", ["argument", "settings", "environment", "default"])
def test_workflow_dir_order(monkeypatch, tmp_path, capsys, where):
    from agent import workflow_store

    isolate(monkeypatch, tmp_path)
    roots = {name: tmp_path / name for name in ("argument", "settings", "environment", "default")}
    for name, root in roots.items():
        workflow_store.WorkflowStore(root, "virtualmes").add(
            {**workflow_sample(tmp_path), "system": "virtualmes", "goal": f"{name} 목표"}, today=REPLAY_DAY)
    monkeypatch.setattr(workflow_store, "DEFAULT_ROOT", roots["default"])
    argv = ["--system", "virtualmes", "--workflows"]
    if where == "argument":
        argv += ["--workflow-dir", str(roots["argument"])]
    if where in ("argument", "settings"):
        settings_file(monkeypatch, tmp_path, f"workflow_dir: '{roots['settings']}'\n")
    if where in ("argument", "settings", "environment"):
        monkeypatch.setenv("AGENT_WORKFLOW_DIR", str(roots["environment"]))
    else:
        monkeypatch.delenv("AGENT_WORKFLOW_DIR", raising=False)

    assert run.main(argv) == 0

    out = capsys.readouterr().out
    assert f"{where} 목표" in out
    assert [name for name in roots if f"{name} 목표" in out] == [where]


def authoring_setup(monkeypatch, tmp_path, status="confirmed", made=True):
    """A model run (run_agent replaced) whose after_run saves the sample through the store it is
    given, or fails with LOCATOR_NOT_UNIQUE. Returns what after_run saw."""
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("AGENT_WORKFLOW_DIR", str(tmp_path / "workflows"))
    monkeypatch.setattr(run, "make_client", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr(run, "run_agent", lambda *_a, **_k: run.RunResult(status, 4, "done", 100))
    seen = {}

    def after_run(model, result, run_dir, store, log, use_ids=False):
        seen.update(model=model, run_dir=run_dir, store=store, use_ids=use_ids,
                    finished=(run_dir / "run.json").read_text(encoding="utf-8"))
        if made:
            saved = store.add({**workflow_sample(tmp_path), "system": store.system})
            outcome = {"created": True, "id": saved["id"], "code": None, "steps": 10, "params": 2}
        else:
            outcome = {"created": False, "id": None, "code": "LOCATOR_NOT_UNIQUE", "steps": 0, "params": 0}
        log({"type": "workflow", **outcome})
        return run.RunResult(result.status, result.steps, result.reason, result.tokens + 50, result.stats)

    monkeypatch.setattr(run, "after_run", after_run)
    return seen


def test_a_successful_model_run_makes_a_workflow_and_prints_its_id(monkeypatch, tmp_path, capsys):
    seen = authoring_setup(monkeypatch, tmp_path)

    code = run.main(["--exe", str(mes_exe(tmp_path)), "--goal", "조회해서 저장해줘"])

    out = capsys.readouterr().out
    assert code == 0
    assert "workflow: w-" in out and "(미확인, 단계 10, 파라미터 2)" in out
    assert list((tmp_path / "workflows" / "virtualmes").glob("w-*.yaml"))
    assert seen["store"].folder == tmp_path / "workflows" / "virtualmes"
    assert '"status": "running"' in seen["finished"]
    [(meta, kinds)] = recorded_runs(tmp_path)
    assert meta["workflow"]["created"] is True and meta["tokens"] == 150
    assert kinds.index("workflow") < kinds.index("finish")


def test_a_failed_authoring_prints_the_code_and_keeps_the_result(monkeypatch, tmp_path, capsys):
    authoring_setup(monkeypatch, tmp_path, made=False)

    code = run.main(["--exe", str(mes_exe(tmp_path)), "--goal", "조회해서 저장해줘"])

    assert code == 0
    assert "workflow를 만들지 못했습니다: LOCATOR_NOT_UNIQUE" in capsys.readouterr().out
    [(meta, _)] = recorded_runs(tmp_path)
    assert meta["status"] == "confirmed" and meta["workflow"]["code"] == "LOCATOR_NOT_UNIQUE"


def test_a_stopped_run_makes_no_workflow(monkeypatch, tmp_path, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("AGENT_WORKFLOW_DIR", str(tmp_path / "workflows"))
    client = FakeClient()
    monkeypatch.setattr(run, "make_client", lambda *_a, **_k: client)
    monkeypatch.setattr(run, "run_agent", lambda *_a, **_k: run.RunResult("stopped", 2, "멈춤", 10))

    code = run.main(["--exe", str(mes_exe(tmp_path)), "--goal", "조회해서 저장해줘"])

    out = capsys.readouterr().out
    assert code == 1
    assert "workflow" not in out
    assert client.calls == []
    [(meta, kinds)] = recorded_runs(tmp_path)
    assert "workflow" not in meta and "workflow" not in kinds
    assert not (tmp_path / "workflows").exists()


def test_a_baseline_run_makes_no_workflow(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "run_baseline", lambda *_a, **_k: run.RunResult("verified", 1, "ok"))
    monkeypatch.setattr(run, "after_run", lambda *_a, **_k: pytest.fail("a baseline run makes no workflow"))

    assert run.main([str(write_task(tmp_path, mes_exe(tmp_path))), "--baseline"]) == 0


def past_run(tmp_path, run_id, system="virtualmes"):
    folder = tmp_path / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"format": 2, "run_id": run_id, "status": "confirmed",
                                                 "system": system, "goal": "g"}), encoding="utf-8")
    (folder / "events.jsonl").write_text("", encoding="utf-8")
    return folder


def make_workflow_setup(monkeypatch, tmp_path, key="test"):
    isolate(monkeypatch, tmp_path)
    if key:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("AGENT_WORKFLOW_DIR", str(tmp_path / "workflows"))
    monkeypatch.setattr(run, "make_client", lambda *_a, **_k: FakeClient())
    sessions = []
    monkeypatch.setattr(run, "start_session", lambda *_a, **_k: sessions.append(1) or "session-1")
    seen = []

    def make_workflow(model, run_dir, store, log, today=None, use_ids=False):
        seen.append({"model": model, "run_dir": run_dir, "store": store, "use_ids": use_ids})
        log({"type": "workflow", "created": True, "id": WORKFLOW_ID, "code": None, "steps": 9, "params": 2})
        return {"created": True, "id": WORKFLOW_ID, "code": None, "steps": 9, "params": 2}, 70

    monkeypatch.setattr(run, "make_workflow", make_workflow)
    return seen, sessions


@pytest.mark.parametrize("which", ["latest", "20261119-090000"])
def test_make_workflow_from_a_past_run(monkeypatch, tmp_path, capsys, which):
    seen, sessions = make_workflow_setup(monkeypatch, tmp_path)
    older = past_run(tmp_path, "20261119-090000")
    newer = past_run(tmp_path, "20261119-100000", system="mes")
    before = {path: path.read_bytes() for path in (tmp_path / "runs").rglob("*") if path.is_file()}

    code = run.main(["--make-workflow", which])

    assert code == 0
    assert {path: path.read_bytes() for path in (tmp_path / "runs").rglob("*") if path.is_file()} == before
    [call] = seen
    expected = newer if which == "latest" else older
    assert call["run_dir"] == expected
    assert call["store"].system == ("mes" if which == "latest" else "virtualmes")
    assert call["store"].folder.parent == tmp_path / "workflows"
    assert f"workflow: {WORKFLOW_ID}" in capsys.readouterr().out
    assert sessions == []


def test_make_workflow_needs_an_api_key(monkeypatch, tmp_path, capsys):
    seen, _ = make_workflow_setup(monkeypatch, tmp_path, key=None)
    past_run(tmp_path, "20261119-090000")

    code = run.main(["--make-workflow", "latest"])

    assert code == 2 and "OPENAI_API_KEY" in capsys.readouterr().err and seen == []


@pytest.mark.parametrize("which", ["20261119-235959", "latest"])
def test_make_workflow_from_a_missing_run_is_refused(monkeypatch, tmp_path, capsys, which):
    seen, _ = make_workflow_setup(monkeypatch, tmp_path)

    code = run.main(["--make-workflow", which])

    assert code == 2 and seen == []
    assert capsys.readouterr().err


def test_workflow_lines_never_print_param_values_or_paths(monkeypatch, tmp_path, capsys):
    seen, _, _ = replay_setup(monkeypatch, tmp_path)
    target = tmp_path / "out" / "secret-name.xlsx"

    run.main(["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID, "--param", "shop=WA9",
              "--param", f"save_path={target}"])

    out = capsys.readouterr().out
    summary_start = out.index("요약 (화면 내용 없음)")
    assert "WA9" not in out[summary_start:] and "secret-name" not in out[summary_start:]


def test_workflow_files_are_ignored_by_git():
    found = subprocess.run(["git", "check-ignore", "-q", "workflows/virtualmes/w-20261119-001.yaml"],
                           cwd=run.HERE, capture_output=True)

    assert found.returncode == 0


@pytest.mark.parametrize("extra", [["--goal", "g"], ["--exe", "C:\apps\a.exe"], ["--baseline"], ["--workflows"]])
def test_make_workflow_with_other_modes_is_refused(monkeypatch, tmp_path, capsys, extra):
    seen, sessions = make_workflow_setup(monkeypatch, tmp_path)
    past_run(tmp_path, "20261119-090000")

    code = run.main(["--make-workflow", "latest", *extra])

    assert code == 2 and "함께 쓸 수 없습니다" in capsys.readouterr().err
    assert seen == [] and sessions == []


def test_make_workflow_with_a_task_file_is_refused(monkeypatch, tmp_path, capsys):
    seen, _ = make_workflow_setup(monkeypatch, tmp_path)
    past_run(tmp_path, "20261119-090000")

    code = run.main([str(write_task(tmp_path, mes_exe(tmp_path))), "--make-workflow", "latest"])

    assert code == 2 and "함께 쓸 수 없습니다" in capsys.readouterr().err and seen == []


# system profile: one profile per run, read in run.py (plan task 6, contracts K5, K6, K8, K9)

from agent.profile import Profile, save_profile  # noqa: E402

ALIAS_MARK = "ZQ등록이름-5521"
SIGNAL_MARK = "ZQ준비신호-6631"
ID_MARK = "ID-7717"
AREA_ID = "AREA-8803"
SCENE_BOUNDS = {"x": 1000, "y": 500, "width": 800, "height": 600}


def write_profile(folder, system, **fields):
    save_profile(folder, Profile(system=system, **fields))
    return folder


def broken_profile(folder, system):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{system}.yaml").write_text(
        f"format: 1\nsystem: {system}\nelements:\n  - {{id: '{ID_MARK}', role: image, name: {ALIAS_MARK}, "
        f"colour: red}}\n", encoding="utf-8")
    return folder


def ready_profile(folder, system, text, **fields):
    return write_profile(folder, system, ready=({"name": text},), **fields)


def mes_settings(monkeypatch, tmp_path, extra=""):
    """Settings naming VirtualMes.exe as the system virtualmes, so a profile's ready signals reach it."""
    exe = mes_exe(tmp_path)
    settings_file(monkeypatch, tmp_path, f"systems:\n  virtualmes: '{exe}'\n" + extra)
    return exe


def watch_profiles(monkeypatch):
    """Keeps the profile each load, apply_ready, RunRecorder and Toolbox call gets."""
    seen = {"loaded": [], "ready": [], "recorder": [], "toolbox": []}
    real_load = run.load_profile
    real_apply = run.apply_ready

    def load(root, system):
        loaded = real_load(root, system)
        seen["loaded"].append((root, system, loaded))
        return loaded

    def apply(task, settings_values, profile=None):
        seen["ready"].append(profile)
        return real_apply(task, settings_values, profile)

    class WatchedRecorder(run.RunRecorder):
        def __init__(self, *args, profile=None, **kwargs):
            seen["recorder"].append(profile)
            super().__init__(*args, profile=profile, **kwargs)

    class WatchedToolbox(run.Toolbox):
        def __init__(self, *args, profile=None, **kwargs):
            seen["toolbox"].append(profile)
            super().__init__(*args, profile=profile, **kwargs)

    monkeypatch.setattr(run, "load_profile", load)
    monkeypatch.setattr(run, "apply_ready", apply)
    monkeypatch.setattr(run, "RunRecorder", WatchedRecorder)
    monkeypatch.setattr(run, "Toolbox", WatchedToolbox)
    return seen


@pytest.mark.parametrize("where", ["argument", "settings", "environment"])
def test_profile_dir_argument_beats_the_settings_and_the_env(monkeypatch, tmp_path, where):
    folders = {name: ready_profile(tmp_path / f"profiles-{name}", "virtualmes", f"{name} 신호")
               for name in ("argument", "settings", "environment")}
    order = ["argument", "settings", "environment"]
    given = order[order.index(where):]
    mes_settings(monkeypatch, tmp_path, f"profile_dir: '{folders['settings']}'\n" if "settings" in given else "")
    monkeypatch.setenv("AGENT_PROFILE_DIR", str(folders["environment"]))
    argv = ["--system", "virtualmes", "--goal", "x"]
    if where == "argument":
        argv += ["--profile-dir", folders["argument"]]

    _, toolbox, _ = wired(monkeypatch, tmp_path, *argv)

    assert toolbox._task.ready == ({"name": f"{where} 신호"},)


def test_a_run_without_a_profile_dir_reads_the_default_folder(monkeypatch, tmp_path):
    from agent import profile as profile_module

    monkeypatch.delenv("AGENT_PROFILE_DIR", raising=False)
    monkeypatch.setattr(profile_module, "DEFAULT_ROOT", ready_profile(tmp_path / "default", "virtualmes", "기본 신호"))
    mes_settings(monkeypatch, tmp_path)

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--system", "virtualmes", "--goal", "x")

    assert toolbox._task.ready == ({"name": "기본 신호"},)


def test_a_run_gives_one_profile_to_the_ready_wait_the_toolbox_and_the_recorder(monkeypatch, tmp_path):
    folder = ready_profile(tmp_path / "profiles", "virtualmes", "준비", ready_read="find", stable_ids=True)
    mes_settings(monkeypatch, tmp_path)
    seen = watch_profiles(monkeypatch)

    _, toolbox, _ = wired(monkeypatch, tmp_path, "--system", "virtualmes", "--goal", "x", "--profile-dir", folder)

    [(root, system, profile)] = seen["loaded"]
    assert (root, system) == (folder.resolve(), "virtualmes")
    assert seen["ready"] == [profile] and seen["recorder"] == [profile] and seen["toolbox"] == [profile]
    assert (toolbox._task.ready, toolbox._task.ready_read) == (({"name": "준비"},), "find")


def test_the_profile_is_read_by_the_knowledge_system_name(monkeypatch, tmp_path):
    """--system names the knowledge folder and the profile file alike, also for an exe the settings do not name."""
    folder = write_profile(tmp_path / "profiles", "공정", stable_ids=True)
    seen = watch_profiles(monkeypatch)

    wired(monkeypatch, tmp_path, "--exe", mes_exe(tmp_path), "--goal", "x", "--system", "공정", "--profile-dir", folder)

    [(_, system, profile)] = seen["loaded"]
    assert system == "공정" and profile.stable_ids is True


def test_a_baseline_run_reads_the_profile_too(monkeypatch, tmp_path):
    folder = write_profile(tmp_path / "profiles", "virtualmes", stable_ids=True)
    seen = watch_profiles(monkeypatch)

    wired(monkeypatch, tmp_path, write_task(tmp_path, mes_exe(tmp_path)), "--baseline", "--profile-dir", folder)

    [(_, _, profile)] = seen["loaded"]
    assert profile.stable_ids is True and seen["recorder"] == [profile] and seen["toolbox"] == [profile]


def test_a_model_run_makes_its_workflow_with_the_profiles_stable_ids(monkeypatch, tmp_path):
    seen = authoring_setup(monkeypatch, tmp_path)
    folder = write_profile(tmp_path / "profiles", "virtualmes", stable_ids=True)

    run.main(["--exe", str(mes_exe(tmp_path)), "--goal", "조회해서 저장해줘", "--profile-dir", str(folder)])
    assert seen["use_ids"] is True

    run.main(["--exe", str(mes_exe(tmp_path)), "--goal", "조회해서 저장해줘", "--profile-dir", str(tmp_path / "none")])
    assert seen["use_ids"] is False


@pytest.mark.parametrize("mode", ["model", "baseline", "replay", "make"])
def test_a_broken_profile_stops_before_anything_is_launched(monkeypatch, tmp_path, capsys, mode):
    folder = broken_profile(tmp_path / "profiles", "virtualmes")
    if mode == "replay":
        _, sessions, _ = replay_setup(monkeypatch, tmp_path)
        argv = ["--exe", str(mes_exe(tmp_path)), "--workflow", WORKFLOW_ID]
    elif mode == "make":
        seen, sessions = make_workflow_setup(monkeypatch, tmp_path)
        past_run(tmp_path, "20261119-090000")
        argv = ["--make-workflow", "latest"]
    else:
        calls = isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        sessions = []
        monkeypatch.setattr(run, "start_session", lambda *_a, **_k: sessions.append(1) or "session-1")
        argv = ([str(write_task(tmp_path, mes_exe(tmp_path))), "--baseline"] if mode == "baseline"
                else ["--exe", str(mes_exe(tmp_path)), "--goal", "x"])

    code = run.main([*argv, "--profile-dir", str(folder)])

    out = capsys.readouterr()
    assert code == 2
    assert "시스템 프로파일을 읽지 못했습니다: elements 1번째 항목: 모르는 키" in out.err
    assert ALIAS_MARK not in out.out + out.err and ID_MARK not in out.out + out.err
    assert sessions == []
    if mode == "make":
        assert seen == []
    else:
        assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())
    if mode in ("model", "baseline"):
        assert calls == []


def test_a_replay_reads_the_profile_too(monkeypatch, tmp_path):
    seen, _, _ = replay_setup(monkeypatch, tmp_path)
    exe = mes_settings(monkeypatch, tmp_path)
    folder = ready_profile(tmp_path / "profiles", "virtualmes", "준비", ready_read="find")
    watched = watch_profiles(monkeypatch)

    assert run.main(["--system", "virtualmes", "--workflow", WORKFLOW_ID, "--profile-dir", str(folder)]) == 0

    [(_, system, profile)] = watched["loaded"]
    task = seen[0]["task"]
    assert system == "virtualmes" and task.exe == str(exe)
    assert (task.ready, task.ready_read) == (({"name": "준비"},), "find")
    assert watched["recorder"] == [profile] and watched["toolbox"] == [profile]


def test_make_workflow_uses_the_record_systems_stable_ids(monkeypatch, tmp_path):
    seen, _ = make_workflow_setup(monkeypatch, tmp_path)
    folder = write_profile(tmp_path / "profiles", "mes", stable_ids=True)
    past_run(tmp_path, "20261119-090000", system="mes")
    past_run(tmp_path, "20261119-100000", system="virtualmes")

    assert run.main(["--make-workflow", "20261119-090000", "--profile-dir", str(folder)]) == 0
    assert run.main(["--make-workflow", "20261119-100000", "--profile-dir", str(folder)]) == 0

    assert [(call["store"].system, call["use_ids"]) for call in seen] == [("mes", True), ("virtualmes", False)]


def registered_scene():
    """A main window whose toolbar (only an id names it) holds a registered menu item that has a
    child, an id-only leaf, and a named button after them. Without the profile the registered item
    is no element, so every later number would be one lower."""
    def box(x, y, w=40, h=30):
        return {"x": 1000 + x, "y": 500 + y, "width": w, "height": h}

    def native(value):
        return {"kind": "automation_id", "value": value}

    from fake_cli import scene_window

    tree = {"role": "window", "name": "메인", "bounds": SCENE_BOUNDS, "children": [
        {"role": "text", "name": SIGNAL_MARK, "bounds": box(10, 10, 200, 20)},
        {"role": "toolbar", "native_id": native(AREA_ID), "bounds": box(0, 40, 400, 40), "children": [
            {"role": "image", "native_id": native(ID_MARK), "bounds": box(10, 45), "children": [
                {"role": "image", "bounds": box(12, 47, 20, 20)}]},
            {"role": "image", "native_id": native("ID-9001"), "bounds": box(60, 45)},
        ]},
        {"role": "button", "name": "조회", "ref_id": "@s1:e1", "available_actions": ["Click"],
         "bounds": box(300, 300, 80, 24)},
    ]}
    return {"windows": [scene_window("w-1", "메인", SCENE_BOUNDS)], "tree": tree}


def scene_run(monkeypatch, tmp_path, settings_extra=""):
    """run.main as a real model run on registered_scene with the real RunRecorder and Toolbox: the
    model only gives up after the first observation. Returns (exit code, the toolbox, run folder)."""
    from fake_cli import FakeCli
    from test_loop import GIVE_UP, ScriptedModel

    from agent.desktop import DesktopClient
    from agent.loop import run_agent

    isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    fake_clients(monkeypatch)
    exe = tmp_path / "Scene.exe"
    exe.write_text("", encoding="utf-8")
    settings_file(monkeypatch, tmp_path, f"systems:\n  scene: '{exe}'\n" + settings_extra)
    folder = write_profile(
        tmp_path / "profiles", "scene", ready=({"name": SIGNAL_MARK},),
        elements=({"id": ID_MARK, "role": "image", "in": {"role": "toolbar", "id": AREA_ID}, "name": ALIAS_MARK},))
    cli = FakeCli(scene=registered_scene())
    cli.process_alive = False
    monkeypatch.setattr(run, "DesktopClient",
                        lambda binary, session, window_handles=None, read_shortcut=None:
                        DesktopClient(binary, session, runner=cli))
    seen = {}

    def agent(_model, toolbox, task, log, gate=None, register=None):
        seen["toolbox"] = toolbox
        return run_agent(ScriptedModel([GIVE_UP]), toolbox, task, log, gate=gate, register=register)

    monkeypatch.setattr(run, "run_agent", agent)
    code = run.main(["--system", "scene", "--goal", "조회", "--profile-dir", str(folder)])
    [run_dir] = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    return code, seen["toolbox"], run_dir


def recorded_events(run_dir):
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]


def test_recorded_marks_match_the_toolbox_marks_with_a_registered_parent(monkeypatch, tmp_path):
    """The seam of plan K6: the recorder and the toolbox number the same elements when they are
    given the same profile, here with a registered element that has a child."""
    _, toolbox, run_dir = scene_run(monkeypatch, tmp_path)

    last = [event for event in recorded_events(run_dir) if event.get("type") == "observation"][-1]
    recorded = [(e["mark"], e["role"], e.get("name"), (e.get("native_id") or {}).get("value"), e.get("alias"))
                for e in last["elements"]]
    marked = [(e["mark"], e["role"], e.get("name"), (e.get("native_id") or {}).get("value"), e.get("alias"))
              for e in toolbox.marked_elements()]
    assert recorded == marked
    assert (2, "image", None, ID_MARK, ALIAS_MARK) in recorded
    assert [name for _, _, name, _, _ in recorded][-1] == "조회"


def test_no_profile_text_reaches_run_json_or_the_summary(monkeypatch, tmp_path, capsys):
    code, _, run_dir = scene_run(monkeypatch, tmp_path)

    events_text = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert ALIAS_MARK in events_text and ID_MARK in events_text
    for name in ("run.json", "summary.txt"):
        text = (run_dir / name).read_text(encoding="utf-8")
        for mark in (ALIAS_MARK, SIGNAL_MARK, ID_MARK, AREA_ID):
            assert mark not in text, (name, mark)
    out = capsys.readouterr()
    for mark in (ALIAS_MARK, SIGNAL_MARK, ID_MARK, AREA_ID):
        assert mark not in out.out + out.err
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["ready"]["status"] == "ready"
