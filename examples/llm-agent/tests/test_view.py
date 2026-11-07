import json
import os
import shutil
from pathlib import Path

import pytest

import view

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
    monkeypatch.setattr(view, "build_run_manager", lambda runs_dir, exe: (None, None))
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
