import json
from pathlib import Path

import pytest

from agent.git_state import CodeState
from agent.hypotheses import judge_all
from agent.labels import LabelError
from agent.survey_facts import BLOCK_TXT, LABELS_YAML, NAMES_JSON, SUMMARY_MD, Labels
from agent.survey_report import (CODES_DOC, HYPOTHESES_DOC, NAMES_HEADING, codes_markdown, finish_session,
                                 hypotheses_markdown, summary_markdown)
from survey_support import MARKERS, make_session, write_session

HERE = Path(__file__).resolve().parent.parent


def test_finish_writes_summary_and_block_and_returns_lines(tmp_path):
    folder = write_session(tmp_path / "s")
    (folder / LABELS_YAML).write_text("version: 1\ncaptures:\n  C02: ERROR_DIALOG\nmanual:\n  H12: SOME\n", encoding="utf-8")
    lines = finish_session(folder)
    assert lines[0].startswith("[전달] v3 · abc1234 · 변경없음 · SYS · 캡처2 · 32분 ·")
    assert any(line.startswith("C02 ERROR_DIALOG DIALOG/SYSTEM/OK ·") for line in lines)
    assert (folder / BLOCK_TXT).read_text(encoding="utf-8") == "\n".join(lines) + "\n"
    summary = (folder / SUMMARY_MD).read_text(encoding="utf-8")
    assert "\n".join(lines) in summary and "### H12 " in summary and "`SOME`" in summary
    assert "| C02 | 20.0 | DIALOG/SYSTEM/OK | ERROR_DIALOG | 2 | OK | DONE | capture-02 |" in summary


def test_finish_with_now_lists_changed_files_and_both_values(tmp_path):
    folder = write_session(tmp_path / "s")
    lines = finish_session(folder, now=CodeState("abc1234", ("agent/loop.py",)))
    assert "변경있음 0→1" in lines[0]
    assert "- 바뀐 추적 파일: agent/loop.py" in (folder / SUMMARY_MD).read_text(encoding="utf-8")


def test_names_appear_only_in_their_section(tmp_path):
    folder = write_session(tmp_path / "s")
    (folder / NAMES_JSON).write_text(json.dumps({"classes": ["TfrmSecretScreen"], "modules": ["CompanySecret.dll"]}),
                                     encoding="utf-8")
    lines = finish_session(folder)
    head, tail = (folder / SUMMARY_MD).read_text(encoding="utf-8").split(NAMES_HEADING)
    assert "TfrmSecretScreen" in tail and "CompanySecret.dll" in tail
    for marker in MARKERS:
        assert marker not in head
        assert marker not in "\n".join(lines)
        assert marker not in (folder / BLOCK_TXT).read_text(encoding="utf-8")


def test_without_names_there_is_no_names_section(tmp_path):
    folder = write_session(tmp_path / "s")
    finish_session(folder)
    assert NAMES_HEADING not in (folder / SUMMARY_MD).read_text(encoding="utf-8")


def test_label_error_writes_no_block(tmp_path):
    folder = write_session(tmp_path / "s")
    (folder / LABELS_YAML).write_text("captures:\n  C01: 저장 실패 화면\n", encoding="utf-8")
    with pytest.raises(LabelError):
        finish_session(folder)
    assert not (folder / BLOCK_TXT).exists() and not (folder / SUMMARY_MD).exists()


def test_label_error_keeps_the_previous_block(tmp_path):
    folder = write_session(tmp_path / "s")
    finish_session(folder)
    before = (folder / BLOCK_TXT).read_text(encoding="utf-8")
    (folder / LABELS_YAML).write_text("captures:\n  C01: [MAIN]\n", encoding="utf-8")
    with pytest.raises(LabelError):
        finish_session(folder)
    assert (folder / BLOCK_TXT).read_text(encoding="utf-8") == before


def test_summary_shows_manual_help_and_every_hypothesis():
    loaded = make_session()
    labels = Labels({}, {})
    md = summary_markdown(loaded, judge_all(loaded, labels), labels, None, ())
    for n in range(1, 19):
        assert f"### H{n:02d} " in md
    assert "답하는 방법:" in md and "overlay.png" in md and "← 이번 판정" in md


def test_committed_docs_are_current():
    assert (HERE / HYPOTHESES_DOC).read_text(encoding="utf-8") == hypotheses_markdown()
    assert (HERE / CODES_DOC).read_text(encoding="utf-8") == codes_markdown()


def test_docs_say_they_are_generated_and_hold_every_code():
    from agent.survey_codes import MEANINGS
    codes = codes_markdown()
    assert "python survey.py --write-docs" in codes and "python survey.py --write-docs" in hypotheses_markdown()
    for key in MEANINGS:
        assert f"`{key.split(':', 1)[1]}`" in codes
