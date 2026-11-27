import pytest

from agent.labels import LabelError, LabelProblem, load_labels
from agent.survey_facts import Labels

CAPTURES = ("C01", "C02")
MANUAL = {"H12": ("ALL", "SOME", "NONE"), "H14": ("CELLS", "TABLE_ONLY", "NONE", "NO_TABLE")}


def write(tmp_path, text, raw=None):
    path = tmp_path / "labels.yaml"
    path.write_bytes(raw if raw is not None else text.encode("utf-8"))
    return path


def load(path):
    return load_labels(path, CAPTURES, MANUAL)


def test_good_file(tmp_path):
    path = write(tmp_path, "version: 1\ncaptures:\n  C01: STARTUP_POPUP\n  C02: MAIN  # 주석은 괜찮음\nmanual:\n  H12: SOME\n")
    assert load(path) == Labels({"C01": "STARTUP_POPUP", "C02": "MAIN"}, {"H12": "SOME"})


def test_missing_file_is_empty(tmp_path):
    assert load(tmp_path / "labels.yaml") == Labels({}, {})


def test_empty_file_and_quoted_question_mark(tmp_path):
    assert load(write(tmp_path, "")) == Labels({}, {})
    assert load(write(tmp_path, 'captures:\n  C01: "?"\nmanual:\n  H12: "?"\n')) == Labels({"C01": "?"}, {"H12": "?"})


def test_bom_and_crlf_are_accepted(tmp_path):
    raw = "﻿version: 1\r\ncaptures:\r\n  C02: ERROR_DIALOG\r\nmanual:\r\n  H14: CELLS\r\n".encode("utf-8")
    assert load(write(tmp_path, "", raw)) == Labels({"C02": "ERROR_DIALOG"}, {"H14": "CELLS"})


def test_crlf_keeps_line_numbers(tmp_path):
    raw = "﻿version: 1\r\ncaptures:\r\n  C01: MAIN\r\n  C02: NOPE\r\n".encode("utf-8")
    with pytest.raises(LabelError) as caught:
        load(write(tmp_path, "", raw))
    assert caught.value.problems == (LabelProblem(4, "SCENE_CODE"),)


def test_sentence_value_is_rejected_without_echo(tmp_path):
    path = write(tmp_path, "version: 1\ncaptures:\n  C01: 출하지시 저장 실패\n")
    with pytest.raises(LabelError) as caught:
        load(path)
    assert caught.value.problems == (LabelProblem(3, "SCENE_CODE"),)
    assert "출하" not in str(caught.value) and str(caught.value) == "LABEL_INVALID 3행 SCENE_CODE"


@pytest.mark.parametrize("text, rule", [
    ("extra: 1\n", "TOP_KEY"), ("version: 2\n", "VERSION"), ('version: "1"\n', "VERSION"),
    ("captures:\n  C99: MAIN\n", "CAPTURE_ID"), ("manual:\n  H01: RICH\n", "MANUAL_ID"),
    ("manual:\n  H12: MAYBE\n", "MANUAL_CODE"), ("manual:\n  H12: CELLS\n", "MANUAL_CODE"),
    ("captures:\n  C01: [MAIN]\n", "NOT_TEXT"), ("captures:\n  C01:\n", "NOT_TEXT"), ("captures:\n  C01: 3\n", "NOT_TEXT"),
    ("captures:\n  C01: {a: MAIN}\n", "NOT_TEXT"), ("captures: MAIN\n", "NOT_TEXT"), ("- MAIN\n", "TOP_KEY"),
    ("captures: [\n", "YAML"), ("captures:\n  C01: ?\n", "YAML"), ("a: 1\n---\nb: 2\n", "YAML")])
def test_each_rule(tmp_path, text, rule):
    with pytest.raises(LabelError) as caught:
        load(write(tmp_path, text))
    assert [p.rule for p in caught.value.problems] == [rule]


def test_not_utf8_is_yaml_problem(tmp_path):
    with pytest.raises(LabelError) as caught:
        load(write(tmp_path, "", "captures:\n  C01: 저장\n".encode("cp949")))
    assert caught.value.problems == (LabelProblem(1, "YAML"),)


def test_every_problem_is_reported_in_line_order(tmp_path):
    path = write(tmp_path, "version: 1\ncaptures:\n  C01: 저장 실패\n  C02: MAIN\nmanual:\n  H12: 거의 다\nother: x\n")
    with pytest.raises(LabelError) as caught:
        load(path)
    assert str(caught.value) == "LABEL_INVALID 3행 SCENE_CODE; 6행 MANUAL_CODE; 7행 TOP_KEY"
    assert "저장" not in str(caught.value) and "거의" not in str(caught.value)
