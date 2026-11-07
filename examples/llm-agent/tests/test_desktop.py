import subprocess

from agent.desktop import DesktopClient, start_session


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
