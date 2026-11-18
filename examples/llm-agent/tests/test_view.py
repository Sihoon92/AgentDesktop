import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import view
from agent import settings
from agent.loop import RunResult
from viewer import run_manager as run_manager_module


@pytest.fixture(autouse=True)
def no_settings_file(monkeypatch, tmp_path):
    """A settings.local.yaml on this machine must not change what these tests see."""
    monkeypatch.delenv(settings.SETTINGS_ENV, raising=False)
    monkeypatch.setattr(settings, "DEFAULT_PATH", tmp_path / "absent-settings.yaml")

FIXTURE_RUNS = Path(__file__).parent / "fixtures" / "run_sample"


def make_runs(tmp_path):
    runs = tmp_path / "runs"
    for run_id in ("20261107-120000", "20261108-090000"):
        (runs / run_id).mkdir(parents=True)
        (runs / run_id / "run.json").write_text(json.dumps({"run_id": run_id, "status": "verified"}), encoding="utf-8")
    (runs / "20261107-090000.jsonl").write_text('{"step": 1, "tool": "observe", "arguments": "{}", "result": {}}\n',
                                                encoding="utf-8")
    return runs


def test_open_latest_picks_the_newest_run(tmp_path):
    assert view.open_fragment(make_runs(tmp_path), "latest") == ("#run=20261108-090000", None)


def test_open_names_a_run_folder_or_an_old_log(tmp_path):
    runs = make_runs(tmp_path)

    assert view.open_fragment(runs, "20261107-120000") == ("#run=20261107-120000", None)
    assert view.open_fragment(runs, "20261107-090000") == ("#legacy=20261107-090000", None)
    assert view.open_fragment(runs, None) == ("", None)


@pytest.mark.parametrize("value", ["20261101-000000", "..\\run.py", "20261107-120000\n", "latest-ish"])
def test_open_with_an_unknown_id_warns_and_opens_the_list(tmp_path, value):
    fragment, warning = view.open_fragment(make_runs(tmp_path), value)

    assert fragment == ""
    assert "찾지 못해" in warning


def test_open_latest_with_no_runs_warns(tmp_path):
    fragment, warning = view.open_fragment(tmp_path / "missing", "latest")

    assert fragment == ""
    assert warning


class FakeServer:
    server_port = 8765

    def __init__(self):
        self.closed = False

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


def run_main(monkeypatch, argv):
    server = FakeServer()
    opened = []
    monkeypatch.setattr(view, "make_server", lambda *args, **kwargs: server)
    monkeypatch.setattr(view, "build_run_manager", lambda runs_dir, exe, **_options: (None, None))
    monkeypatch.setattr(view.webbrowser, "open", opened.append)
    monkeypatch.setattr(view, "load_dotenv", lambda path: None)
    code = view.main(argv)
    return code, server, opened


def test_no_browser_prints_the_address_with_the_opened_run(monkeypatch, capsys):
    code, server, opened = run_main(monkeypatch, ["--runs", str(FIXTURE_RUNS), "--no-browser", "--open", "latest"])

    out = capsys.readouterr().out
    assert code == 0
    assert server.closed
    assert opened == []
    [address] = [line for line in out.splitlines() if line.startswith("뷰어 주소: ")]
    assert address.startswith("뷰어 주소: http://127.0.0.1:8765/?t=")
    assert address.endswith("#run=20261108-010000")


def test_unknown_open_id_warns_and_the_browser_gets_the_list(monkeypatch, capsys):
    code, _, opened = run_main(monkeypatch, ["--runs", str(FIXTURE_RUNS), "--open", "20261101-000000"])

    captured = capsys.readouterr()
    assert code == 0
    assert "20261101-000000" in captured.err
    assert len(opened) == 1 and "#" not in opened[0]


SAMPLE_RUN = FIXTURE_RUNS / "20261107-120000"
EXPORT_WARNING = "이 파일에는 앱 화면과 UI 텍스트가 들어 있습니다. 공유 전에 확인하세요"


def no_server(monkeypatch):
    """Fails the test if view.main starts a server, a run manager or a browser."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no server, run manager or browser for this option")

    monkeypatch.setattr(view, "make_server", forbidden)
    monkeypatch.setattr(view, "build_run_manager", forbidden)
    monkeypatch.setattr(view.webbrowser, "open", forbidden)
    monkeypatch.setattr(view, "load_dotenv", lambda path: None)


def write_run(runs, run_id, **fields):
    folder = runs / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"run_id": run_id, "status": "verified", **fields}), encoding="utf-8")
    (folder / "events.jsonl").write_text("", encoding="utf-8")
    return folder


def live_running_fields():
    from datetime import datetime, timezone

    from agent.process_identity import process_started_at

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"format": 2, "status": "running", "pid": os.getpid(), "process_started_at": process_started_at(os.getpid()),
            "heartbeat_at": now, "started_at": now}


def prune_runs(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "20261001-000000")
    write_run(runs, "20261002-000000")
    (write_run(runs, "20261003-000000") / "keep").write_bytes(b"")
    write_run(runs, "20261004-000000", **live_running_fields())
    write_run(runs, "20261005-000000", format=2, status="running", pid=None)
    (runs / "20261006-000000.jsonl").write_bytes(b'{"step": 1}\n')
    return runs


def names(runs):
    return sorted(path.name for path in runs.iterdir())


def test_prune_alone_lists_and_deletes_nothing(monkeypatch, tmp_path, capsys):
    no_server(monkeypatch)
    runs = prune_runs(tmp_path)
    before = names(runs)

    code = view.main(["--runs", str(runs), "--prune", "--keep", "0"])

    out = capsys.readouterr().out
    assert code == 0
    assert names(runs) == before
    assert "아무것도 지우지 않았습니다. 지우려면 --yes를 붙이세요" in out
    assert "20261001-000000" in out and "20261006-000000" in out


def test_prune_yes_deletes_only_candidates_and_keeps_kept_and_running_runs(monkeypatch, tmp_path, capsys):
    no_server(monkeypatch)
    runs = prune_runs(tmp_path)

    code = view.main(["--runs", str(runs), "--prune", "--older-than", "1d", "--yes"])

    out = capsys.readouterr().out
    assert code == 0
    assert names(runs) == ["20261003-000000", "20261004-000000"]
    assert "휴지통을 거치지 않고 영구 삭제합니다" in out


def test_prune_yes_with_a_failed_deletion_exits_1(monkeypatch, tmp_path, capsys):
    no_server(monkeypatch)
    runs = prune_runs(tmp_path)
    real_delete = view.delete_plan
    monkeypatch.setattr(view, "delete_plan", lambda runs_dir, plan: real_delete(runs_dir, plan) + ["x: 실패"])

    code = view.main(["--runs", str(runs), "--prune", "--keep", "0", "--yes"])

    assert code == 1
    assert "x: 실패" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--prune"],
        ["--prune", "--keep", "3", "--older-than", "30d"],
        ["--prune", "--older-than", "30"],
        ["--prune", "--older-than", "-1d"],
        ["--prune", "--keep", "-1"],
        ["--prune", "--keep", "many"],
        ["--keep", "3"],
        ["--yes"],
        ["--prune", "--keep", "3", "--export", "latest", "out.html"],
        ["--prune", "--keep", "3", "--open", "latest"],
        ["--export", "latest", "out.html", "--open", "latest"],
        ["--export", "latest"],
    ],
)
def test_bad_option_combinations_exit_2_before_anything_happens(monkeypatch, tmp_path, argv):
    no_server(monkeypatch)
    runs = prune_runs(tmp_path)
    before = names(runs)

    with pytest.raises(SystemExit) as stopped:
        view.main(["--runs", str(runs), *argv])

    assert stopped.value.code == 2
    assert names(runs) == before


def test_export_writes_the_file_then_refuses_to_overwrite(monkeypatch, tmp_path, capsys):
    no_server(monkeypatch)
    runs = tmp_path / "runs"
    shutil.copytree(SAMPLE_RUN, runs / "20261107-120000")
    out = tmp_path / "share.html"

    code = view.main(["--runs", str(runs), "--export", "20261107-120000", str(out)])

    printed = capsys.readouterr().out
    assert code == 0
    assert out.is_file()
    assert str(out.resolve()) in printed
    assert f"{out.stat().st_size:,}" in printed
    assert EXPORT_WARNING in printed
    before = out.read_bytes()
    assert view.main(["--runs", str(runs), "--export", "20261107-120000", str(out)]) == 1
    assert "이미 있" in capsys.readouterr().err
    assert out.read_bytes() == before


def test_export_latest_uses_the_newest_run(monkeypatch, tmp_path, capsys):
    no_server(monkeypatch)
    runs = tmp_path / "runs"
    shutil.copytree(SAMPLE_RUN, runs / "20261107-120000")
    write_run(runs, "20261106-000000")
    out = tmp_path / "latest.html"

    assert view.main(["--runs", str(runs), "--export", "latest", str(out)]) == 0
    assert '"run_id": "20261107-120000"' in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["20261101-000000", "20261107-090000", "latest", "..\20261107-120000"])
def test_export_of_a_missing_run_or_an_old_log_exits_1(monkeypatch, tmp_path, capsys, value):
    no_server(monkeypatch)
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "20261107-090000.jsonl").write_bytes(b'{"step": 1}\n')
    out = tmp_path / "out.html"

    code = view.main(["--runs", str(runs), "--export", value, str(out)])

    assert code == 1
    assert capsys.readouterr().err.strip()
    assert not out.exists()


def test_open_names_a_run_a_person_confirmed(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(Path(__file__).parent / "fixtures" / "run_questions", runs / "20261113-101500")

    assert view.open_fragment(runs, "20261113-101500") == ("#run=20261113-101500", None)
    assert view.open_fragment(runs, "latest") == ("#run=20261113-101500", None)


def test_the_viewer_uses_the_shared_knowledge_folder_rule(monkeypatch, tmp_path):
    from agent import knowledge

    assert view.knowledge_root is knowledge.knowledge_root
    assert not hasattr(view, "KNOWLEDGE_DIR")


def test_the_viewer_serves_the_knowledge_folder(monkeypatch, tmp_path, capsys):
    seen = {}
    server = FakeServer()

    def fake_make_server(*args, **kwargs):
        seen.update(kwargs)
        return server

    monkeypatch.delenv("AGENT_KNOWLEDGE_DIR", raising=False)
    managers = []
    monkeypatch.setattr(view, "make_server", fake_make_server)
    monkeypatch.setattr(view, "build_run_manager",
                        lambda runs_dir, exe, **options: managers.append(options) or (None, None))
    monkeypatch.setattr(view, "load_dotenv", lambda path: None)

    code = view.main(["--runs", str(FIXTURE_RUNS), "--no-browser", "--knowledge-dir", str(tmp_path / "k")])

    assert code == 0
    assert seen["knowledge_dir"] == (tmp_path / "k").resolve()
    assert managers == [{"knowledge_dir": (tmp_path / "k").resolve(), "task_defaults": {}}]
    assert f"지식 폴더: {(tmp_path / 'k').resolve()}" in capsys.readouterr().out


def test_the_knowledge_dir_from_dotenv_is_read_after_loading_it(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.delenv("AGENT_KNOWLEDGE_DIR", raising=False)
    monkeypatch.setattr(view, "make_server", lambda *args, **kwargs: seen.update(kwargs) or FakeServer())
    monkeypatch.setattr(view, "build_run_manager", lambda runs_dir, exe, **_options: (None, None))
    monkeypatch.setattr(view, "load_dotenv", lambda path: monkeypatch.setenv("AGENT_KNOWLEDGE_DIR", str(tmp_path / "dot")))

    view.main(["--runs", str(FIXTURE_RUNS), "--no-browser"])

    assert seen["knowledge_dir"] == (tmp_path / "dot").resolve()


def settings_file(monkeypatch, tmp_path, text):
    path = tmp_path / "settings.local.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(settings, "DEFAULT_PATH", path)
    return path


def served(monkeypatch, argv):
    """Runs view.main with the server and the run manager replaced; returns (code, server kwargs, manager calls)."""
    seen, managers = {}, []
    monkeypatch.delenv("AGENT_KNOWLEDGE_DIR", raising=False)
    monkeypatch.setattr(view, "make_server", lambda *args, **kwargs: seen.update(kwargs) or FakeServer())
    monkeypatch.setattr(view, "build_run_manager",
                        lambda runs_dir, exe, **options: managers.append({"exe": exe, **options}) or (None, None))
    monkeypatch.setattr(view, "load_dotenv", lambda path: None)
    code = view.main(["--runs", str(FIXTURE_RUNS), "--no-browser", *argv])
    return code, seen, managers


def test_the_viewer_gives_new_runs_the_settings(monkeypatch, tmp_path, capsys):
    path = settings_file(monkeypatch, tmp_path, "model: house-model\nexe: 'C:\\apps\\Tool.exe'\n"
                                                "knowledge_dir: kn\n")

    code, seen, managers = served(monkeypatch, [])

    assert code == 0
    assert managers == [{"exe": "C:\\apps\\Tool.exe", "knowledge_dir": (tmp_path / "kn").resolve(),
                         "task_defaults": {"model": "house-model", "exe": "C:\\apps\\Tool.exe",
                                           "knowledge_dir": str((tmp_path / "kn").resolve())}}]
    assert seen["knowledge_dir"] == (tmp_path / "kn").resolve()
    out = capsys.readouterr().out
    assert f"설정 파일: {path} (model, knowledge_dir, exe)" in out
    assert "house-model" not in out


def test_viewer_arguments_beat_the_settings(monkeypatch, tmp_path):
    settings_file(monkeypatch, tmp_path, "exe: 'C:\\apps\\Tool.exe'\nknowledge_dir: kn\n")

    _, seen, managers = served(monkeypatch, ["--exe", "D:\\other\\Other.exe", "--knowledge-dir", str(tmp_path / "arg")])

    assert managers[0]["exe"] == "D:\\other\\Other.exe"
    assert managers[0]["knowledge_dir"] == seen["knowledge_dir"] == (tmp_path / "arg").resolve()


def test_settings_knowledge_dir_comes_before_the_environment_in_the_viewer(monkeypatch, tmp_path):
    settings_file(monkeypatch, tmp_path, "knowledge_dir: kn\n")
    monkeypatch.setattr(view, "make_server", lambda *args, **kwargs: seen.update(kwargs) or FakeServer())
    monkeypatch.setattr(view, "build_run_manager", lambda runs_dir, exe, **_options: (None, None))
    monkeypatch.setattr(view, "load_dotenv", lambda path: monkeypatch.setenv("AGENT_KNOWLEDGE_DIR", str(tmp_path / "env")))
    seen = {}

    view.main(["--runs", str(FIXTURE_RUNS), "--no-browser"])

    assert seen["knowledge_dir"] == (tmp_path / "kn").resolve()


def test_without_a_settings_file_the_viewer_is_unchanged(monkeypatch, capsys):
    _, _, managers = served(monkeypatch, [])

    assert managers == [{"exe": None, "knowledge_dir": view.knowledge_root(None), "task_defaults": {}}]
    assert "설정 파일" not in capsys.readouterr().out


@pytest.mark.parametrize("text, words", [("model: m\nbase_url: x\n", "모르는 키"), ("image_mode: big\n", "image_mode"),
                                         ("exe: Tool.exe\n", "절대 경로")])
def test_bad_settings_stop_the_viewer_in_korean_before_it_serves(monkeypatch, tmp_path, capsys, text, words):
    path = settings_file(monkeypatch, tmp_path, text)
    no_server(monkeypatch)

    code = view.main(["--runs", str(FIXTURE_RUNS), "--no-browser"])

    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("오류: ") and words in err and str(path) in err


def test_prune_and_export_do_not_read_the_settings(monkeypatch, tmp_path, capsys):
    settings_file(monkeypatch, tmp_path, "unknown_key: x\n")
    no_server(monkeypatch)
    runs = make_runs(tmp_path)

    assert view.main(["--runs", str(runs), "--prune", "--keep", "5"]) == 0
    assert view.main(["--runs", str(FIXTURE_RUNS), "--export", "latest", str(tmp_path / "out.html")]) == 0


class FakeClient:
    """Stands in for the OpenAI client and records every Responses API call."""

    def __init__(self):
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output=[], usage=SimpleNamespace(total_tokens=1))


def test_a_run_started_from_the_viewer_calls_the_model_named_in_the_settings_file(monkeypatch, tmp_path):
    exe = tmp_path / "Tool.exe"
    exe.write_bytes(b"")
    binary = tmp_path / "agent-desktop.exe"
    binary.write_bytes(b"")
    settings_file(monkeypatch, tmp_path, f"model: house-model-7\nreasoning_effort: medium\nexe: '{exe}'\n")
    monkeypatch.setenv("AGENT_DESKTOP_BIN", str(binary))
    clear_model_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    client = FakeClient()
    monkeypatch.setattr(view, "make_client", lambda api_key, base_url: client)
    monkeypatch.setattr(run_manager_module, "start_session", lambda *_args, **_kwargs: "session-1")

    def agent(model, _toolbox, _task, _log, gate=None, register=None):
        model.respond("instructions", [], [])
        return RunResult("gave_up", 1, "done")

    monkeypatch.setattr(run_manager_module, "run_agent", agent)
    seen = {}
    monkeypatch.setattr(view, "make_server", lambda *args, **kwargs: seen.update(kwargs) or FakeServer())
    monkeypatch.setattr(view, "load_dotenv", lambda path: None)
    runs = tmp_path / "runs"

    assert view.main(["--runs", str(runs), "--no-browser"]) == 0
    manager = seen["run_manager"]
    run_id = manager.start("g")
    manager.join(5)

    assert [(call["model"], call["reasoning"]) for call in client.calls] == [("house-model-7", {"effort": "medium"})]
    meta = json.loads((runs / run_id / "run.json").read_text(encoding="utf-8"))
    assert (meta["model"], meta["exe"], meta["status"]) == ("house-model-7", str(exe), "gave_up")


MODEL_ENV_NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "VISION_MODEL", "VISION_API_KEY",
                   "VISION_BASE_URL")


def clear_model_env(monkeypatch):
    """The model env names of this machine or its .env must not change what these tests see."""
    for name in MODEL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def viewer_manager(monkeypatch, tmp_path, env, settings_text=""):
    """Starts view.main with fake clients and a recording agent; returns (manager, seen, made)."""
    exe = tmp_path / "Tool.exe"
    exe.write_bytes(b"")
    binary = tmp_path / "agent-desktop.exe"
    binary.write_bytes(b"")
    settings_file(monkeypatch, tmp_path, f"{settings_text}exe: '{exe}'\n")
    monkeypatch.setenv("AGENT_DESKTOP_BIN", str(binary))
    clear_model_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    made = []
    monkeypatch.setattr(view, "make_client", lambda api_key, base_url: made.append((api_key, base_url)) or FakeClient())
    monkeypatch.setattr(run_manager_module, "start_session", lambda *_args, **_kwargs: "session-1")
    seen = {}

    def agent(model, toolbox, task, _log, gate=None, register=None):
        seen.update(model=model, toolbox=toolbox, task=task)
        return RunResult("gave_up", 1, "done")

    monkeypatch.setattr(run_manager_module, "run_agent", agent)
    served = {}
    monkeypatch.setattr(view, "make_server", lambda *args, **kwargs: served.update(kwargs) or FakeServer())
    monkeypatch.setattr(view, "load_dotenv", lambda path: None)
    assert view.main(["--runs", str(tmp_path / "runs"), "--no-browser"]) == 0
    return served["run_manager"], seen, made


def test_a_viewer_run_takes_the_env_model_and_gives_the_toolbox_the_vision(monkeypatch, tmp_path):
    manager, seen, made = viewer_manager(monkeypatch, tmp_path, {
        "OPENAI_API_KEY": "test", "OPENAI_MODEL": "env-model", "VISION_MODEL": "eye-model"})

    manager.start("g")
    manager.join(5)

    assert (seen["task"].model, seen["task"].model_source) == ("env-model", "env")
    assert seen["toolbox"].vision_enabled is True
    assert made == [("test", None), ("test", None)]


def test_a_viewer_run_without_vision_gives_the_toolbox_none(monkeypatch, tmp_path):
    manager, seen, _made = viewer_manager(monkeypatch, tmp_path, {"OPENAI_API_KEY": "test"})

    manager.start("g")
    manager.join(5)

    assert seen["toolbox"].vision_enabled is False
    assert seen["task"].model_source == "기본값"


def test_a_broken_vision_setup_fails_the_viewer_start(monkeypatch, tmp_path):
    manager, seen, made = viewer_manager(monkeypatch, tmp_path, {
        "OPENAI_API_KEY": "test", "VISION_API_KEY": "vk"})

    with pytest.raises(run_manager_module.RunStartFailed) as raised:
        manager.start("g")

    assert "VISION_MODEL" in str(raised.value) and "vk" not in str(raised.value).split()
    assert (seen, made) == ({}, [])
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())
    assert manager.active() is None


def test_the_viewer_still_needs_the_text_key(monkeypatch, tmp_path):
    manager, _seen, _made = viewer_manager(monkeypatch, tmp_path, {})

    with pytest.raises(run_manager_module.RunStartFailed) as raised:
        manager.start("g")

    assert "OPENAI_API_KEY" in str(raised.value)


QUESTIONS_RUN = "20261113-101500"


def questions_runs(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(Path(__file__).parent / "fixtures" / "run_questions", runs / QUESTIONS_RUN)
    return runs


@pytest.mark.parametrize("value", [QUESTIONS_RUN, "latest"])
def test_summary_rebuilds_a_past_run_summary_from_its_records(monkeypatch, tmp_path, capsys, value):
    settings_file(monkeypatch, tmp_path, "unknown_key: x\n")
    no_server(monkeypatch)
    runs = questions_runs(tmp_path)
    (runs / QUESTIONS_RUN / "summary.txt").write_text("stale\n", encoding="utf-8")

    code = view.main(["--runs", str(runs), "--summary", value])

    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert out[:3] == ["요약 (화면 내용 없음)", "  코드: 알 수 없음", "  판정: confirmed · 스텝 8 · 토큰 15200"]
    assert "stale" not in out and not any(word in "\n".join(out) for word in ("극판", "WA1", "맞아"))


def test_summary_uses_the_code_recorded_when_the_run_started(monkeypatch, tmp_path, capsys):
    no_server(monkeypatch)
    runs = tmp_path / "runs"
    write_run(runs, "20261107-120000", code="abc1234+수정")

    assert view.main(["--runs", str(runs), "--summary", "20261107-120000"]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "  코드: abc1234+수정"


def test_summary_of_a_dead_running_run_says_interrupted(monkeypatch, tmp_path, capsys):
    no_server(monkeypatch)
    runs = tmp_path / "runs"
    write_run(runs, "20261107-120000", status="running", started_at="2026-11-07T12:00:00Z")

    assert view.main(["--runs", str(runs), "--summary", "latest"]) == 0
    assert capsys.readouterr().out.splitlines()[2].startswith("  판정: interrupted")


@pytest.mark.parametrize("value", ["20261101-000000", "20261107-090000", "latest", "../20261107-120000", ".."])
def test_summary_of_a_missing_run_or_an_old_log_exits_1(monkeypatch, tmp_path, capsys, value):
    no_server(monkeypatch)
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "20261107-090000.jsonl").write_bytes(b'{"step": 1}\n')

    code = view.main(["--runs", str(runs), "--summary", value])

    captured = capsys.readouterr()
    assert code == 1 and captured.err.strip() and not captured.out
    assert ("옛 로그" in captured.err) == (value == "latest")


@pytest.mark.parametrize(
    "argv",
    [
        ["--summary", "latest", "--open", "latest"],
        ["--summary", "latest", "--export", "latest", "out.html"],
        ["--summary", "latest", "--prune", "--keep", "3"],
    ],
)
def test_summary_does_not_mix_with_the_other_modes(monkeypatch, tmp_path, argv):
    no_server(monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        view.main(["--runs", str(tmp_path), *argv])
    assert stopped.value.code == 2
