import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from viewer.retention import Candidate, delete_plan, format_plan, plan_prune

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
DRY_RUN = "아무것도 지우지 않았습니다. 지우려면 --yes를 붙이세요"
PERMANENT = "휴지통을 거치지 않고 영구 삭제합니다"


def never_active(_run_dir):
    return False


def make_run(runs, run_id, status="verified", keep=False, run_json="ok", frame_bytes=100):
    folder = runs / run_id
    (folder / "frames").mkdir(parents=True)
    (folder / "frames" / "0001.png").write_bytes(b"x" * frame_bytes)
    (folder / "events.jsonl").write_text('{"type": "finish"}\n', encoding="utf-8")
    if run_json == "ok":
        (folder / "run.json").write_text(json.dumps({"run_id": run_id, "status": status}), encoding="utf-8")
    elif run_json == "broken":
        (folder / "run.json").write_text('{"run_id": ', encoding="utf-8")
    if keep:
        (folder / "keep").write_bytes(b"")
    return folder


def plan(runs, **kwargs):
    kwargs.setdefault("is_active", never_active)
    return plan_prune(runs, now=NOW, **kwargs)


def candidate_ids(result):
    return [c.run_id for c in result.candidates]


def skipped_reasons(result):
    return {s.run_id: s.reason for s in result.skipped}


def snapshot(folder):
    return sorted(
        (str(path.relative_to(folder)), path.stat().st_size, path.stat().st_mtime_ns)
        for path in folder.rglob("*")
    )


def make_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError, AttributeError):
        pytest.skip("cannot create a symlink or junction here")


def test_older_than_lists_runs_before_the_cutoff_with_their_size(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "20261001-090000", frame_bytes=1000)
    make_run(runs, "20261101-090000")
    make_run(runs, "20261108-110000")

    result = plan(runs, older_than=timedelta(days=30))

    assert candidate_ids(result) == ["20261001-090000"]
    [candidate] = result.candidates
    assert candidate.status == "verified"
    assert candidate.bytes > 1000
    assert result.total_bytes == candidate.bytes
    assert result.skipped == []


def test_keep_newest_leaves_the_newest_runs_counting_suffixes(tmp_path):
    runs = tmp_path / "runs"
    for run_id in ("20261105-100000", "20261107-120000", "20261107-120000-2", "20261106-080000"):
        make_run(runs, run_id)

    result = plan(runs, keep_newest=2)

    assert sorted(candidate_ids(result)) == ["20261105-100000", "20261106-080000"]


def test_keep_newest_zero_lists_every_run(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "20261105-100000")

    assert candidate_ids(plan(runs, keep_newest=0)) == ["20261105-100000"]


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"older_than": timedelta(days=1), "keep_newest": 3}, {"keep_newest": -1}, {"keep_newest": True},
     {"older_than": 30}, {"older_than": timedelta(days=-1)}],
)
def test_exactly_one_valid_criterion_is_required(tmp_path, kwargs):
    with pytest.raises(ValueError):
        plan(tmp_path, **kwargs)


def test_kept_active_and_unreadable_runs_are_skipped_with_a_reason(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "20261001-000001", keep=True)
    active = make_run(runs, "20261001-000002", status="running")
    make_run(runs, "20261001-000003", run_json="broken")
    make_run(runs, "20261001-000004", run_json=None)
    make_run(runs, "20261001-000005", status="running")

    result = plan(runs, older_than=timedelta(days=1), is_active=lambda run_dir: run_dir == active)

    reasons = skipped_reasons(result)
    assert "보관" in reasons["20261001-000001"]
    assert "실행 중" in reasons["20261001-000002"]
    assert "run.json" in reasons["20261001-000003"]
    assert "run.json" in reasons["20261001-000004"]
    assert [(c.run_id, c.status) for c in result.candidates] == [("20261001-000005", "interrupted")]


def test_names_outside_the_run_id_rule_are_skipped_and_other_files_ignored(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "notes")
    make_run(runs, "20261399-000000")
    runs.joinpath("bad.jsonl").write_text("{}\n", encoding="utf-8")
    runs.joinpath("readme.txt").write_text("hi", encoding="utf-8")

    result = plan(runs, keep_newest=0)

    assert result.candidates == []
    reasons = skipped_reasons(result)
    assert set(reasons) == {"notes", "20261399-000000", "bad.jsonl"}
    assert all("이름" in reason for reason in reasons.values())


def test_legacy_logs_follow_the_same_rule(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    runs.joinpath("20261001-000000.jsonl").write_bytes(b'{"step": 1}\n')
    runs.joinpath("20261108-110000.jsonl").write_text('{"step": 1}\n', encoding="utf-8")

    result = plan(runs, older_than=timedelta(days=30))

    [candidate] = result.candidates
    assert (candidate.run_id, candidate.status, candidate.name) == ("20261001-000000", "legacy", "20261001-000000.jsonl")
    assert candidate.bytes == len(b'{"step": 1}\n')


def test_a_linked_run_folder_is_skipped(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    outside = make_run(tmp_path / "elsewhere", "20261001-000000")
    make_link(runs / "20261001-000000", outside)

    result = plan(runs, keep_newest=0)

    assert result.candidates == []
    assert "링크" in skipped_reasons(result)["20261001-000000"]


def test_a_run_folder_holding_a_link_is_skipped(tmp_path):
    runs = tmp_path / "runs"
    folder = make_run(runs, "20261001-000000")
    outside = tmp_path / "precious"
    outside.mkdir()
    make_link(folder / "frames" / "more", outside)

    result = plan(runs, keep_newest=0)

    assert result.candidates == []
    assert "링크" in skipped_reasons(result)["20261001-000000"]


def test_planning_and_formatting_change_nothing(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "20261001-000000")
    make_run(runs, "20261002-000000", keep=True)
    runs.joinpath("20261001-000001.jsonl").write_text("{}\n", encoding="utf-8")
    before = snapshot(runs)

    result = plan(runs, older_than=timedelta(days=1))
    text = format_plan(result, will_delete=False)

    assert snapshot(runs) == before
    assert DRY_RUN in text
    assert PERMANENT not in text


def test_format_plan_lists_runs_total_and_skips(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "20261001-000000", frame_bytes=2048)
    make_run(runs, "20261002-000000", keep=True)

    result = plan(runs, older_than=timedelta(days=1))
    dry = format_plan(result, will_delete=False)
    real = format_plan(result, will_delete=True)

    assert "20261001-000000" in dry and "verified" in dry
    assert "1개" in dry
    assert "KB" in dry
    assert "20261002-000000" in dry and "보관" in dry
    assert PERMANENT in real
    assert DRY_RUN not in real


def test_format_plan_says_when_there_is_nothing_to_delete(tmp_path):
    result = plan(tmp_path / "missing", keep_newest=0)

    text = format_plan(result, will_delete=False)

    assert "지울 실행이 없습니다" in text
    assert DRY_RUN in text


def test_delete_plan_removes_only_the_candidates(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "20261001-000000")
    runs.joinpath("20261001-000001.jsonl").write_text("{}\n", encoding="utf-8")
    make_run(runs, "20261002-000000", keep=True)
    make_run(runs, "20261108-110000")
    runs.joinpath("readme.txt").write_text("hi", encoding="utf-8")
    result = plan(runs, older_than=timedelta(days=1))

    errors = delete_plan(runs, result)

    assert errors == []
    assert sorted(p.name for p in runs.iterdir()) == ["20261002-000000", "20261108-110000", "readme.txt"]
    assert (runs / "20261002-000000" / "frames" / "0001.png").exists()


def test_delete_plan_rechecks_each_candidate_and_keeps_going(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs, "20261001-000000")
    make_run(runs, "20261001-000001")
    make_run(runs, "20261001-000002")
    outside = make_run(tmp_path, "20261001-000009")
    result = plan(runs, keep_newest=0)
    (runs / "20261001-000000" / "keep").write_bytes(b"")
    forged = Candidate("20261001-000009", "verified", 1, "../20261001-000009")
    missing = Candidate("20261001-000003", "verified", 1, "20261001-000003")
    result = replace(result, candidates=[*result.candidates, forged, missing])

    errors = delete_plan(runs, result)

    assert len(errors) == 3
    assert any("20261001-000000" in e for e in errors)
    assert any("20261001-000009" in e for e in errors)
    assert any("20261001-000003" in e for e in errors)
    assert (runs / "20261001-000000").exists()
    assert outside.exists()
    assert not (runs / "20261001-000001").exists()
    assert not (runs / "20261001-000002").exists()


def test_delete_plan_refuses_a_candidate_that_became_a_link(tmp_path):
    runs = tmp_path / "runs"
    folder = make_run(runs, "20261001-000000")
    result = plan(runs, keep_newest=0)
    outside = make_run(tmp_path / "elsewhere", "20261001-000000")
    for path in sorted(folder.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    folder.rmdir()
    make_link(folder, outside)

    errors = delete_plan(runs, result)

    assert len(errors) == 1 and "링크" in errors[0]
    assert (outside / "frames" / "0001.png").exists()


KST = timezone(timedelta(hours=9))


def test_old_log_names_are_local_time_for_the_cutoff(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    runs.joinpath("20261008-110000.jsonl").write_bytes(b"{}\n")
    make_run(runs, "20261008-030000")

    result = plan(runs, older_than=timedelta(days=31, hours=9, minutes=30), local_timezone=KST)

    assert candidate_ids(result) == ["20261008-110000"]


def test_keep_newest_orders_old_logs_and_runs_by_their_utc_moment(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    runs.joinpath("20261107-210000.jsonl").write_bytes(b"{}\n")
    make_run(runs, "20261107-130000")

    result = plan(runs, keep_newest=1, local_timezone=KST)

    assert [(c.run_id, c.status) for c in result.candidates] == [("20261107-210000", "legacy")]
