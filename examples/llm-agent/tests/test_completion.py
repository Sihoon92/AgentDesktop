import os
import time

import pytest

from agent.completion import check_file, run_checks

NOW = time.time()


def missing(tmp_path):
    return str(tmp_path / "nothing.xlsx")


def empty_file(tmp_path):
    path = tmp_path / "empty.xlsx"
    path.write_bytes(b"")
    return str(path)


def old_file(tmp_path):
    path = tmp_path / "old.xlsx"
    path.write_bytes(b"data")
    os.utime(path, (NOW - 3600, NOW - 3600))
    return str(path)


def a_folder(tmp_path):
    return str(tmp_path)


def written(tmp_path, name, age_s=0.0, content=b"data"):
    path = tmp_path / name
    path.write_bytes(content)
    os.utime(path, (NOW - age_s, NOW - age_s))
    return path


def test_file_written_after_the_start_is_met(tmp_path):
    saved = tmp_path / "out.xlsx"
    saved.write_bytes(b"data")

    result = check_file(str(saved), started_at=saved.stat().st_mtime - 10)

    assert result["met"] is True and "out.xlsx" in result["detail"]


def test_a_file_written_just_before_the_start_still_counts(tmp_path):
    saved = written(tmp_path, "out.xlsx", age_s=1.5)

    assert check_file(str(saved), started_at=NOW)["met"] is True


@pytest.mark.parametrize("make,why", [(missing, "없음"), (empty_file, "비어 있음"), (old_file, "실행 전"), (a_folder, "폴더")])
def test_file_check_fails_with_a_reason(tmp_path, make, why):
    result = check_file(make(tmp_path), started_at=NOW)

    assert result["met"] is False and why in result["detail"]


@pytest.mark.parametrize("path", [None, ""])
def test_no_path_is_not_met(path):
    assert check_file(path, started_at=NOW)["met"] is False


def test_pattern_must_match_the_name(tmp_path):
    saved = written(tmp_path, "out.csv")

    result = check_file(str(saved), started_at=NOW - 10, pattern="*.xlsx")
    assert result["met"] is False and "*.xlsx" in result["detail"]
    assert check_file(str(saved), started_at=NOW - 10, pattern="*.csv")["met"] is True


def test_pattern_ignores_letter_case(tmp_path):
    saved = written(tmp_path, "OUT.XLSX")

    assert check_file(str(saved), started_at=NOW - 10, pattern="*.xlsx")["met"] is True


def test_folder_with_pattern_checks_the_newest_match(tmp_path):
    written(tmp_path, "a.xlsx", age_s=3600)
    newest = written(tmp_path, "b.xlsx", age_s=1)
    written(tmp_path, "c.csv", age_s=0)

    result = check_file(str(tmp_path), started_at=NOW - 10, pattern="*.xlsx")

    assert result["met"] is True and newest.name in result["detail"]


def test_folder_whose_newest_match_is_old_is_not_met(tmp_path):
    written(tmp_path, "a.xlsx", age_s=3600)

    result = check_file(str(tmp_path), started_at=NOW, pattern="*.xlsx")

    assert result["met"] is False and "실행 전" in result["detail"]


def test_folder_with_no_match_is_not_met(tmp_path):
    written(tmp_path, "a.csv")

    result = check_file(str(tmp_path), started_at=NOW - 10, pattern="*.xlsx")

    assert result["met"] is False and "*.xlsx" in result["detail"]


def test_checks_run_in_order_and_stop_at_the_first_unmet(tmp_path):
    asked = []

    result = run_checks(
        [{"kind": "file"}, {"kind": "human"}],
        verify_elements=None,
        saved_path=None,
        started_at=NOW,
        ask_human=lambda: asked.append(1) or True,
    )

    assert result["met"] is False and asked == []
    assert "saved_path가 필요함" in result["detail"]


def test_the_check_path_wins_over_the_saved_path(tmp_path):
    folder = tmp_path / "exports"
    folder.mkdir()
    written(folder, "report.xlsx")
    elsewhere = written(tmp_path, "other.csv")

    result = run_checks(
        [{"kind": "file", "path": str(folder), "pattern": "*.xlsx"}],
        verify_elements=None,
        saved_path=str(elsewhere),
        started_at=NOW - 10,
        ask_human=None,
    )

    assert result["met"] is True and "report.xlsx" in result["detail"]


def test_all_harness_checks_met_is_by_harness(tmp_path):
    saved = written(tmp_path, "out.xlsx")
    seen = []

    result = run_checks(
        [{"kind": "file", "pattern": "*.xlsx"}, {"kind": "element", "require": {"role": "statictext", "name": "저장 완료"}}],
        verify_elements=lambda require: seen.append(require) or {"met": True, "unmet": None},
        saved_path=str(saved),
        started_at=NOW - 10,
        ask_human=None,
    )

    assert result == {"met": True, "by": "harness", "detail": result["detail"]}
    assert "out.xlsx" in result["detail"]
    assert seen == [[{"role": "statictext", "name": "저장 완료"}]]


def test_a_human_check_makes_it_by_human(tmp_path):
    saved = written(tmp_path, "out.xlsx")

    result = run_checks(
        [{"kind": "file", "pattern": "*.xlsx"}, {"kind": "human"}],
        verify_elements=None,
        saved_path=str(saved),
        started_at=NOW - 10,
        ask_human=lambda: True,
    )

    assert result["met"] is True and result["by"] == "human"


def test_a_human_who_says_no_is_not_met():
    result = run_checks([{"kind": "human"}], verify_elements=None, saved_path=None, started_at=NOW, ask_human=lambda: False)

    assert result["met"] is False and result["by"] == "human"


def test_a_human_check_with_no_one_to_ask_is_not_met():
    result = run_checks([{"kind": "human"}], verify_elements=None, saved_path=None, started_at=NOW, ask_human=None)

    assert result["met"] is False and result["by"] == "harness"


def test_element_check_uses_the_given_verifier():
    requirement = [{"role": "button", "name": "닫기"}]
    unmet = {"role": "button", "name": "닫기"}

    result = run_checks(
        [{"kind": "element", "require": requirement}],
        verify_elements=lambda require: {"met": False, "unmet": unmet} if require == requirement else {"met": True},
        saved_path=None,
        started_at=NOW,
        ask_human=None,
    )

    assert result["met"] is False and result["by"] == "harness" and "닫기" in result["detail"]


def test_element_check_without_a_verifier_is_not_met():
    result = run_checks(
        [{"kind": "element", "require": {"role": "button"}}],
        verify_elements=None,
        saved_path=None,
        started_at=NOW,
        ask_human=None,
    )

    assert result["met"] is False


def test_unknown_check_kind_is_not_met():
    result = run_checks([{"kind": "screen"}], verify_elements=None, saved_path=None, started_at=NOW, ask_human=lambda: True)

    assert result["met"] is False and "screen" in result["detail"]


def test_empty_checks_are_not_met():
    assert run_checks([], verify_elements=None, saved_path=None, started_at=NOW, ask_human=None)["met"] is False
