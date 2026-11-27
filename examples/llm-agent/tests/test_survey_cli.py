import io
import sys
import types

import pytest

import survey
from agent.git_state import CodeState
from agent.survey_block import check_chars
from agent.survey_facts import BLOCK_TXT, LABELS_YAML
from survey_support import MARKERS, write_session


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(survey, "HERE", tmp_path)
    monkeypatch.setattr(survey, "code_state", lambda folder: CodeState("abc1234", ()))
    return tmp_path


def test_cli_block_latest_prints_block_and_exit_zero(home, capsys):
    write_session(home / "captures" / "20261127-090000-OLD", system="OLD")
    folder = write_session(home / "captures" / "20261127-100000-SYS")
    assert survey.main(["--block", "latest"]) == 0
    out = capsys.readouterr().out
    assert "세션: 20261127-100000-SYS" in out and "[전달] v3 · abc1234 · 변경없음 · SYS · 캡처2" in out
    assert (folder / BLOCK_TXT).is_file() and str(home) not in out


def test_cli_block_by_name_and_missing(home, capsys):
    write_session(home / "captures" / "a")
    assert survey.main(["--block", "a"]) == 0
    assert survey.main(["--block", "nope"]) == 2
    assert survey.main(["--block", "..\\captures\\a"]) == 2


def test_cli_block_latest_without_sessions(home, capsys):
    assert survey.main(["--block", "latest"]) == 2


def test_cli_block_label_invalid_exit_one_without_value(home, capsys):
    folder = write_session(home / "captures" / "s")
    (folder / LABELS_YAML).write_text("version: 1\ncaptures:\n  C01: 출하지시 저장 실패\n", encoding="utf-8")
    assert survey.main(["--block", "s"]) == 1
    out = capsys.readouterr().out
    assert "LABEL_INVALID 3행 SCENE_CODE" in out and "출하" not in out and "[전달]" not in out


def test_cli_read_block_marks_typo(home, tmp_path, capsys):
    write_session(home / "captures" / "s")
    survey.main(["--block", "s"])
    lines = (home / "captures" / "s" / BLOCK_TXT).read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("H01", "H07", 1)
    feedback = tmp_path / "2026-11-27-SYS.md"
    feedback.write_text("# 2026-11-27 SYS\n\n```\n" + "\n".join(lines) + "\n```\n\n## 말로 덧붙인 것\n", encoding="utf-8-sig")
    capsys.readouterr()
    assert survey.main(["--read-block", str(feedback)]) == 0
    out = capsys.readouterr().out
    assert out.count("오타?") == 2 and "오타? H07 " in out and f"줄 {len(lines)} · 오타? 1" in out


def test_cli_block_recomputes_the_code_state(home, capsys, monkeypatch):
    write_session(home / "captures" / "s")
    monkeypatch.setattr(survey, "code_state", lambda folder: CodeState("fff0000", ("a.py",)))
    assert survey.main(["--block", "s"]) == 0
    assert "abc1234→fff0000 · 변경있음 0→1" in capsys.readouterr().out


def test_cli_read_block_accepts_a_bom_before_the_block(home, tmp_path, capsys):
    write_session(home / "captures" / "s")
    survey.main(["--block", "s"])
    copied = tmp_path / "bom.md"
    copied.write_text((home / "captures" / "s" / BLOCK_TXT).read_text(encoding="utf-8"), encoding="utf-8-sig")
    capsys.readouterr()
    assert survey.main(["--read-block", str(copied)]) == 0
    assert "오타? 0" in capsys.readouterr().out


def test_cli_read_block_missing_file_and_no_block(tmp_path, capsys):
    assert survey.main(["--read-block", str(tmp_path / "gone.md")]) == 2
    empty = tmp_path / "x.md"
    empty.write_text("nothing\n", encoding="utf-8")
    assert survey.main(["--read-block", str(empty)]) == 1


def test_cli_korean_system_name_does_not_crash_on_cp949(home, monkeypatch):
    write_session(home / "captures" / "s", system="가나\U0001f600")
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="cp949"))
    assert survey.main(["--block", "s"]) == 0
    sys.stdout.flush()
    text = buffer.getvalue().decode("cp949")
    assert "가나?" in text and "[전달]" in text


def test_cli_session_mode_calls_run_session_with_args(monkeypatch):
    seen = {}

    def run_session(args, deps=None):
        seen["args"] = args
        return 7

    monkeypatch.setitem(sys.modules, "agent.survey_session", types.SimpleNamespace(run_session=run_session))
    code = survey.main(["--system", "GS", "--exe", "C:\\a.exe", "--settings", "s.yaml", "--profile", "home",
                        "--profile-dir", "p", "--skip", "llm", "--show-names"])
    args = seen["args"]
    assert code == 7
    assert (args.system, args.exe, args.settings, args.profile, args.profile_dir, args.skip, args.show_names) == (
        "GS", "C:\\a.exe", "s.yaml", "home", "p", ["llm"], True)
    survey.main([])
    assert (seen["args"].skip, seen["args"].show_names, seen["args"].system) == ([], False, None)


def test_cli_modes_are_exclusive(capsys):
    with pytest.raises(SystemExit) as caught:
        survey.main(["--block", "latest", "--write-docs"])
    assert caught.value.code == 2


def test_write_docs_touches_only_the_two_docs(home, capsys):
    assert survey.main(["--write-docs"]) == 0
    written = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    assert written == ["docs/company-survey/codes.md", "docs/company-survey/hypotheses.md"]


def test_console_holds_no_marker(home, capsys):
    folder = write_session(home / "captures" / "s")
    (folder / "names.json").write_text('{"classes": ["TfrmSecretScreen"], "modules": ["CompanySecret.dll"]}',
                                      encoding="utf-8")
    survey.main(["--block", "s"])
    out = capsys.readouterr().out
    assert not any(marker in out for marker in MARKERS)


def test_check_chars_are_stable_across_runs():
    assert check_chars("[전달] v2 · abc1234") == check_chars("[전달]  v2  ·  abc1234")
