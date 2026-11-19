import ast
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.desktop import DesktopClient, start_session
from agent import handles
from agent.handles import process_window_handles

ROOT = Path(__file__).resolve().parent.parent


def runner_returning(stdout, code=0):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, stdout, "")

    return run, calls


def test_session_is_sent_on_every_call_without_headed_by_default():
    run, calls = runner_returning('{"ok": true}')
    client = DesktopClient("ad.exe", "run1", runner=run)

    client.run("click", "@s1:e1")

    assert calls[0][0] == ["ad.exe", "--session", "run1", "click", "@s1:e1"]


def test_start_session_returns_the_id_the_cli_created():
    run, calls = runner_returning('{"ok": true, "data": {"session_id": "abc123", "trace": "on"}}')

    assert start_session("ad.exe", "my run", runner=run) == "abc123"
    assert calls[0][0] == ["ad.exe", "session", "start", "--name=my run"]


def test_start_session_failure_returns_none():
    run, _ = runner_returning('{"ok": false, "error": {"code": "INTERNAL"}}', code=1)

    assert start_session("ad.exe", "my run", runner=run) is None


def test_headed_is_only_sent_when_requested():
    run, calls = runner_returning('{"ok": true}')
    client = DesktopClient("ad.exe", "run1", runner=run)

    client.run("type", "@s1:e1", headed=True)

    assert "--headed" in calls[0][0]


def test_structured_error_on_exit_1_is_returned_as_is():
    run, _ = runner_returning('{"ok": false, "error": {"code": "STALE_REF"}}', code=1)

    result = DesktopClient("ad.exe", "run1", runner=run).run("click", "@s1:e1")

    assert result["error"]["code"] == "STALE_REF"


def test_unparseable_output_is_an_unsafe_failure_because_the_action_may_have_landed():
    run, _ = runner_returning("panic: boom", code=101)

    result = DesktopClient("ad.exe", "run1", runner=run).run("click", "@s1:e1")

    assert result["ok"] is False
    assert result["error"]["disposition"]["retry"] == "unsafe"


def test_timeout_is_an_unsafe_failure():
    def run(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    result = DesktopClient("ad.exe", "run1", runner=run).run("click", "@s1:e1")

    assert result["ok"] is False
    assert result["error"]["disposition"]["retry"] == "unsafe"


def test_missing_binary_is_reported_as_not_delivered():
    def run(_argv, **_kwargs):
        raise FileNotFoundError("nope")

    result = DesktopClient("ad.exe", "run1", runner=run).run("click", "@s1:e1")

    assert result["ok"] is False
    assert result["error"]["disposition"]["delivery"] == "not_delivered"


HANDLE = {"kind": "automation_id", "value": "175311172"}
FIXED = {"kind": "automation_id", "value": "query-button"}


def snapshot_answer(window_id="w-4242", ok=True):
    tree = {"role": "window", "name": "Main", "children": [
        {"role": "group", "native_id": HANDLE, "children": [
            {"role": "button", "name": "Query", "native_id": FIXED, "ref": "@s1:e1"}]}]}
    if not ok:
        return {"ok": False, "command": "snapshot", "error": {"code": "TIMEOUT"}, "data": {"tree": tree}}
    data = {"snapshot_id": "s1", "ref_count": 1, "complete": True, "tree": tree}
    if window_id is not None:
        data["window"] = {"id": window_id, "title": "Main"}
    return {"version": "2.6", "ok": True, "command": "snapshot", "data": data}


def handle_client(answer, handles=frozenset({175311172}), error=None):
    asked = []

    def window_handles(hwnd):
        asked.append(hwnd)
        if error is not None:
            raise error
        return handles

    run, calls = runner_returning(json.dumps(answer))
    return DesktopClient("ad.exe", "run1", runner=run, window_handles=window_handles), asked, calls


def group_of(envelope):
    return envelope["data"]["tree"]["children"][0]


def test_a_snapshot_by_window_id_drops_the_handle_ids_of_that_window():
    client, asked, calls = handle_client(snapshot_answer(window_id="w-9"))

    out = client.run("snapshot", "--window-id", "w-4242", "--compact")

    assert asked == [4242]
    assert calls[0][0][3:] == ["snapshot", "--window-id", "w-4242", "--compact"]
    assert "native_id" not in group_of(out)
    assert group_of(out)["children"][0] == {"role": "button", "name": "Query", "native_id": FIXED, "ref": "@s1:e1"}
    expected = snapshot_answer(window_id="w-9")
    del group_of(expected)["native_id"]
    assert out == expected


def test_a_snapshot_without_window_id_uses_the_window_of_its_answer():
    client, asked, _ = handle_client(snapshot_answer(window_id="w-777"))

    out = client.run("snapshot", "--app", "Main")

    assert asked == [777]
    assert "native_id" not in group_of(out)


@pytest.mark.parametrize("command", [
    ("list-windows",), ("find", "--window-id", "w-4242", "--role", "group"), ("launch", "Main.exe"),
    ("click", "@s1:e1"), ("screenshot", "--window-id", "w-4242", "a.png")])
def test_answers_of_other_commands_are_left_as_they_are(command):
    answer = snapshot_answer()
    answer["command"] = command[0]
    client, asked, _ = handle_client(answer)

    assert client.run(*command) == answer
    assert asked == []


def test_a_failed_snapshot_is_left_as_it_is():
    client, asked, _ = handle_client(snapshot_answer(ok=False))

    assert client.run("snapshot", "--window-id", "w-4242") == snapshot_answer(ok=False)
    assert asked == []


@pytest.mark.parametrize("args, window_id", [
    (("--window-id", "4242"), "w-4242"), (("--window-id", "w-abc"), "w-4242"), (("--window-id", "w-"), None),
    ((), None), ((), "4242"), ((), "w-0x10"), (("--window-id",), "w-4242")])
def test_a_window_id_not_of_the_form_w_number_drops_nothing(args, window_id):
    client, asked, _ = handle_client(snapshot_answer(window_id=window_id))

    assert client.run("snapshot", *args) == snapshot_answer(window_id=window_id)
    assert asked == []


@pytest.mark.parametrize("handles, error", [
    (frozenset(), None), (None, None), (frozenset({175311172}), OSError("denied")),
    (frozenset({175311172}), RuntimeError("boom")), (frozenset({175311172}), ValueError("odd"))])
def test_handles_that_cannot_be_had_leave_the_tree_as_it_is(handles, error):
    client, asked, _ = handle_client(snapshot_answer(), handles=handles, error=error)

    assert client.run("snapshot", "--window-id", "w-4242") == snapshot_answer()
    assert asked == [4242]


def test_a_tree_that_is_not_a_tree_is_left_as_it_is():
    answer = snapshot_answer()
    answer["data"]["tree"] = ["odd"]
    client, _, _ = handle_client(answer)

    assert client.run("snapshot", "--window-id", "w-4242") == answer


def test_a_client_without_window_handles_answers_as_before(monkeypatch):
    """Even where Win32 would call that window's group a handle: only the clients a real run makes
    ask it, so a fake CLI's window id that happens to be a real window changes nothing."""
    monkeypatch.setattr(handles.sys, "platform", "win32")
    monkeypatch.setattr(handles, "_win32_handles", lambda _hwnd: frozenset({175311172}))
    run, _ = runner_returning(json.dumps(snapshot_answer()))
    client = DesktopClient("ad.exe", "run1", runner=run)

    assert client.run("snapshot", "--window-id", "w-4242") == snapshot_answer()


def _desktop_calls(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return [call for call in ast.walk(tree)
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "DesktopClient"]


@pytest.mark.parametrize("path, count", [("run.py", 2), ("check.py", 1), ("viewer/run_manager.py", 1)])
def test_every_real_client_is_given_the_window_handles(path, count):
    """The clients a real run, the check and the viewer make: each is handed the Win32 handles, so
    every snapshot they read drops handle ids the same way."""
    calls = _desktop_calls(ROOT / path)

    assert len(calls) == count
    for call in calls:
        given = [keyword.value for keyword in call.keywords if keyword.arg == "window_handles"]
        assert len(given) == 1
        named = {node.id for node in ast.walk(given[0]) if isinstance(node, ast.Name)}
        assert "process_window_handles" in named


class _Made(Exception):
    pass


def _client_check_makes(monkeypatch, tmp_path, deps):
    import check

    made = {}

    def recording(*args, **kwargs):
        made.update(kwargs, args=args)
        raise _Made

    monkeypatch.setattr(check, "DesktopClient", recording)
    monkeypatch.setattr(check, "start_session", lambda *_args: "s1")
    monkeypatch.setattr(check, "signals", lambda _target: ())
    exe = tmp_path / "App.exe"
    exe.write_text("", encoding="utf-8")
    with pytest.raises(_Made):
        check.check_app(deps, exe, SimpleNamespace(unknown=False, exe=str(exe), shortcut_error=None), {})
    return made


def test_the_check_with_the_real_cli_drops_handle_ids(monkeypatch, tmp_path):
    import check

    made = _client_check_makes(monkeypatch, tmp_path, check.Deps())

    assert made["window_handles"] is process_window_handles


def test_the_check_with_a_fake_cli_never_asks_win32(monkeypatch, tmp_path):
    import check

    made = _client_check_makes(monkeypatch, tmp_path, check.Deps(runner=lambda *_a, **_k: None))

    assert made["window_handles"] is None


# Launching a shortcut

import sys  # noqa: E402

import yaml  # noqa: E402

from agent import shortcut as shortcut_module  # noqa: E402
from agent.shortcut import CODES, MESSAGES, UNREADABLE, Shortcut, ShortcutError, resolve_shortcut  # noqa: E402

SECRET = Shortcut(target="C:\\비밀 폴더\\target.exe", arguments=("-site=SECRET", "a b", "key=value"),
                  working_dir="C:\\비밀 폴더\\work")
SECRET_WORDS = ("비밀", "SECRET", "target.exe", "key=value")


def reading(found=SECRET):
    asked = []

    def read(path):
        asked.append(path)
        if isinstance(found, BaseException):
            raise found
        return found

    return read, asked


def shortcut_client(found=SECRET, stdout='{"ok": true, "data": {"pid": 7}}'):
    run, calls = runner_returning(stdout)
    read, asked = reading(found)
    return DesktopClient("ad.exe", "run1", runner=run, read_shortcut=read), calls, asked


def test_a_shortcut_is_launched_with_its_target_arguments_and_working_folder():
    client, calls, asked = shortcut_client()

    assert client.run("launch", "C:\\links\\앱.lnk") == {"ok": True, "data": {"pid": 7}}
    assert asked == ["C:\\links\\앱.lnk"]
    assert calls[0][0] == [
        "ad.exe", "--session", "run1", "launch", "C:\\비밀 폴더\\target.exe",
        "--arg=-site=SECRET", "--arg=a b", "--arg=key=value", "--cwd", "C:\\비밀 폴더\\work",
    ]


def test_a_shortcut_with_no_arguments_and_no_folder_is_launched_with_its_target_only():
    client, calls, _ = shortcut_client(Shortcut(target="C:\\app\\tool.exe"))

    client.run("launch", "C:\\links\\TOOL.LNK")

    assert calls[0][0] == ["ad.exe", "--session", "run1", "launch", "C:\\app\\tool.exe"]


def test_the_callers_other_launch_arguments_follow_the_shortcuts_and_headed_stays_before():
    client, calls, _ = shortcut_client()

    client.run("launch", "C:\\links\\app.lnk", "--timeout", 5000, headed=True)

    assert calls[0][0] == [
        "ad.exe", "--session", "run1", "--headed", "launch", "C:\\비밀 폴더\\target.exe",
        "--arg=-site=SECRET", "--arg=a b", "--arg=key=value", "--cwd", "C:\\비밀 폴더\\work", "--timeout", "5000",
    ]


def test_the_shortcut_is_read_again_on_every_launch():
    found = [Shortcut(target="C:\\one.exe"), Shortcut(target="C:\\two.exe", arguments=("-x",))]
    run, calls = runner_returning('{"ok": true}')
    client = DesktopClient("ad.exe", "run1", runner=run, read_shortcut=lambda _path: found.pop(0))

    client.run("launch", "C:\\links\\app.lnk")
    client.run("launch", "C:\\links\\app.lnk")

    assert [call[0][3:] for call in calls] == [["launch", "C:\\one.exe"], ["launch", "C:\\two.exe", "--arg=-x"]]


@pytest.mark.parametrize("code", CODES)
def test_a_shortcut_that_cannot_be_launched_never_reaches_the_cli(code):
    client, calls, _ = shortcut_client(ShortcutError(code))

    answer = client.run("launch", "C:\\비밀 폴더\\app.lnk")

    assert calls == []
    assert answer == {"ok": False, "error": {"code": code, "message": MESSAGES[code],
                                              "disposition": {"delivery": "not_delivered", "retry": "safe"}}}
    assert not any(word in json.dumps(answer, ensure_ascii=False) for word in ("비밀", "app.lnk", "C:\\"))


@pytest.mark.parametrize("found", [RuntimeError("boom"), OSError("C:\\비밀"), ShortcutError("ELSE"), None,
                                   ("C:\\app\\tool.exe", (), "")])
def test_a_reader_that_raises_or_answers_wrongly_is_unreadable_and_never_raises(found):
    client, calls, _ = shortcut_client(found)

    answer = client.run("launch", "C:\\links\\app.lnk")

    assert calls == []
    assert answer["error"]["code"] == UNREADABLE
    assert answer["error"]["message"] == MESSAGES[UNREADABLE]


@pytest.mark.parametrize("args", [
    ("launch", "C:\\app\\tool.exe"),
    ("launch", "Notepad"),
    ("launch", "C:\\links\\app.lnk.exe"),
    ("snapshot", "--app", "C:\\links\\app.lnk"),
    ("close-app", "C:\\links\\app.lnk"),
    ("launch",),
])
def test_only_a_launch_of_a_shortcut_is_read(args):
    client, calls, asked = shortcut_client()

    client.run(*args)

    assert asked == []
    assert calls[0][0] == ["ad.exe", "--session", "run1", *args]


def test_a_client_without_a_shortcut_reader_sends_a_shortcut_as_it_was():
    run, calls = runner_returning('{"ok": false, "error": {"code": "ACTION_FAILED"}}')
    client = DesktopClient("ad.exe", "run1", runner=run)

    answer = client.run("launch", "C:\\links\\app.lnk")

    assert calls[0][0] == ["ad.exe", "--session", "run1", "launch", "C:\\links\\app.lnk"]
    assert answer == {"ok": False, "error": {"code": "ACTION_FAILED"}}


@pytest.mark.skipif(sys.platform != "win32", reason="real shortcuts are Windows only")
def test_a_real_shortcut_reaches_the_cli_with_everything_it_names(tmp_path):
    from test_shortcut import make_exe, make_link

    exe = make_exe(tmp_path / "앱 폴더" / "대상 앱.exe")
    link = make_link(tmp_path / "바로 가기" / "가상 앱.lnk", target=exe, arguments='"사이트 KR" -mode=fast',
                     working_dir=tmp_path / "앱 폴더")
    run, calls = runner_returning('{"ok": true}')

    DesktopClient("ad.exe", "run1", runner=run, read_shortcut=resolve_shortcut).run("launch", str(link))

    assert calls[0][0][3:] == ["launch", str(exe), "--arg=사이트 KR", "--arg=-mode=fast",
                               "--cwd", str(tmp_path / "앱 폴더")]


@pytest.mark.parametrize("path, count", [("run.py", 2), ("viewer/run_manager.py", 1)])
def test_every_real_run_client_is_given_the_shortcut_reader(path, count):
    calls = _desktop_calls(ROOT / path)

    assert len(calls) == count
    for call in calls:
        given = [keyword.value for keyword in call.keywords if keyword.arg == "read_shortcut"]
        assert len(given) == 1
        assert isinstance(given[0], ast.Name) and given[0].id == "resolve_shortcut"


def test_the_check_client_launches_with_the_checks_shortcut_reader(monkeypatch, tmp_path):
    import check

    assert check.Deps().read_shortcut is resolve_shortcut
    read, _ = reading()
    made = _client_check_makes(monkeypatch, tmp_path, check.Deps(runner=lambda *_a, **_k: None, read_shortcut=read))

    assert made["read_shortcut"] is read


def test_a_run_of_a_shortcut_records_the_shortcut_and_never_what_it_names(monkeypatch, tmp_path):
    """run.json's exe is the .lnk the user wrote; the target, the arguments and the folder are in
    neither run.json nor the summary, though the CLI was given all three."""
    import run
    from agent import settings as settings_module
    from agent.loop import RunResult

    monkeypatch.delenv(settings_module.SETTINGS_ENV, raising=False)
    monkeypatch.setattr(settings_module, "DEFAULT_PATH", tmp_path / "absent-settings.yaml")
    monkeypatch.setattr(run, "ENV_FILES", [])
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.delenv("AGENT_PROFILE", raising=False)
    monkeypatch.setenv("AGENT_PROFILE_DIR", str(tmp_path / "profiles"))
    binary = tmp_path / "agent-desktop.exe"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_DESKTOP_BIN", str(binary))
    monkeypatch.setattr(run, "start_session", lambda *_a, **_k: "session-1")
    target = tmp_path / "비밀 폴더" / "target.exe"
    target.parent.mkdir()
    target.write_bytes(b"MZ")
    secret = Shortcut(target=str(target), arguments=SECRET.arguments, working_dir=SECRET.working_dir)
    monkeypatch.setattr(shortcut_module, "read_link", lambda _path: secret)
    cli, calls = runner_returning('{"ok": true, "data": {"pid": 7}}')
    made = []

    def client(*args, **kwargs):
        made.append(DesktopClient(*args, runner=cli, **kwargs))
        return made[-1]

    def baseline(_toolbox, task):
        made[0].run("launch", task.exe)
        return RunResult("failed", 0, "test")

    monkeypatch.setattr(run, "DesktopClient", client)
    monkeypatch.setattr(run, "run_baseline", baseline)
    link = tmp_path / "가상 앱.lnk"
    link.write_bytes(b"")
    task = tmp_path / "task.yaml"
    task.write_text(yaml.safe_dump({"goal": "g", "exe": str(link), "permission_level": 3,
                                    "success": {"require": [{"role": "switch"}]},
                                    "baseline_target": {"role": "switch"}}, allow_unicode=True), encoding="utf-8")

    run.main([str(task), "--baseline"])

    launches = [argv for argv, _ in calls if "launch" in argv]
    assert launches == [[str(binary), "--session", "session-1", "launch", str(target),
                         "--arg=-site=SECRET", "--arg=a b", "--arg=key=value", "--cwd", SECRET.working_dir]]
    run_dir = next((tmp_path / "runs").iterdir())
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["exe"] == str(link)
    written = (run_dir / "run.json").read_text(encoding="utf-8") + (run_dir / "summary.txt").read_text(encoding="utf-8")
    assert not any(word in written for word in SECRET_WORDS)
