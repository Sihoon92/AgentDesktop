import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent import summary
from agent.summary import code_version, summarize

QUESTIONS = Path(__file__).parent / "fixtures" / "run_questions"
CODE_KEYS = {"type", "tool", "kind", "status", "code", "refused_code", "expect", "source", "delivery", "retry",
             "by", "role", "action", "decision", "part", "format", "started_by"}
TEXT_KEYS = {"goal", "exe", "reason", "intent", "text", "name", "value", "description", "title", "label",
             "message", "detail", "file", "frame", "path", "situation", "resolution", "evidence", "saved_path"}


def load_run(folder=QUESTIONS):
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return run, events


def action(step, tool, **result):
    return {"type": "action", "step": step, "tool": tool, "intent": "누른다", "arguments": {"intent": "누른다"},
            "target": None, "before": None, "result": result}


def observation(**fields):
    return {"type": "observation", "id": 1, "elements": [], "text": "본문", **fields}


KINDS = {"named_ref": 12, "unnamed_ref": 3, "unnamed_ref_with_id": 1, "text": 40, "blank": 2, "nodes": 90, "depth": 7}


def detailed_events():
    return [
        {"type": "knowledge_loaded", "system": "s", "ids": ["a", "b", "c", "d"], "truncated": False},
        {"type": "model", "step": 1, "tokens": 1000},
        action(1, "click", ok=True, expect="content_changed", expected_met=True),
        action(2, "click", ok=True, expect="content_changed", expected_met=False),
        action(3, "click_part", ok=True),
        action(4, "click", ok=False, code="STALE_REF", message="화면의 글"),
        action(5, "click_point", ok=False, refused="이유", refused_code="needs_confirmation"),
        action(6, "type_text", ok=False, refused="막힘", refused_code="blocked_until_question"),
        action(7, "click", ok=False, code="TIMEOUT"),
        action(8, "click", ok=False, code="TIMEOUT"),
        {"type": "knowledge_used", "step": 9, "id": "a", "text": "지식"},
        {"type": "knowledge_used", "step": 9, "id": "b", "text": "지식"},
        {"type": "knowledge_failed", "step": 9, "id": "b"},
        {"type": "question", "id": "q1", "text": "물음"},
        {"type": "answer", "id": "q1", "text": "답", "timed_out": False},
        {"type": "question", "id": "q2", "text": "물음"},
        {"type": "answer", "id": "q2", "text": "", "timed_out": True},
        {"type": "question", "id": "q3", "text": "물음"},
        observation(kinds=dict(KINDS, text=1), complete=False),
        observation(kinds=KINDS, complete=True),
        {"type": "model", "step": 14, "tokens": 31200},
        action(14, "click_point", ok=False, refused="이유", refused_code="needs_confirmation"),
        {"type": "finish", "status": "gave_up", "reason": "포기한 이유", "steps": 14, "tokens": 31200},
    ]


def test_a_detailed_run_gives_every_line_of_the_contract():
    lines = summarize({"status": "gave_up", "max_steps": 40}, detailed_events(), code="284f163")

    assert lines == [
        "요약 (화면 내용 없음)",
        "  코드: 284f163",
        "  판정: gave_up · 스텝 14/40 · 토큰 31200",
        "  마지막 동작: [14] click_point → needs_confirmation",
        "  동작: 9회 · 기대 지정 2 · 일치 1 · 불일치 1 · 확인 못 함 0",
        "  거절: needs_confirmation 2 · blocked_until_question 1",
        "  오류 코드: STALE_REF 1 · TIMEOUT 2",
        "  질문: 3 · 답 1 · 무응답 2",
        "  지식: 불러옴 4 · 참고 2 · 참고 뒤 실패 1",
        "  마지막 관찰: 이름 있는 ref 12 · 이름 없는 ref 3(id 있음 1) · 글자 40 · 빈 영역 2 · 잘림 없음",
    ]


def test_unnamed_refs_show_how_many_carry_an_id_only_when_recorded():
    without_key = {key: value for key, value in KINDS.items() if key != "unnamed_ref_with_id"}
    not_a_count = dict(KINDS, unnamed_ref_with_id="둘")

    for kinds in (without_key, not_a_count):
        lines = summarize({}, [observation(kinds=kinds, complete=True)])
        assert lines[-1] == "  마지막 관찰: 이름 있는 ref 12 · 이름 없는 ref 3 · 글자 40 · 빈 영역 2 · 잘림 없음"


def test_the_recorded_question_run_is_summarized_from_its_events():
    run, events = load_run()

    assert summarize(run, events, code="284f163") == [
        "요약 (화면 내용 없음)",
        "  코드: 284f163",
        "  판정: confirmed · 스텝 8 · 토큰 15200",
        "  마지막 동작: [6] click → 성공",
        "  동작: 4회 · 기대 지정 0 · 일치 0 · 불일치 0 · 확인 못 함 0",
        "  거절: 코드 없음 1",
        "  오류 코드: 없음",
        "  질문: 4 · 답 4 · 무응답 0",
        "  지식: 불러옴 1 · 참고 1 · 참고 뒤 실패 0",
    ]


def free_text(value, key=None, found=None):
    """Every string in a record that is screen text, an instruction, a question, an answer, knowledge or a path."""
    found = set() if found is None else found
    if isinstance(value, dict):
        for child_key, child in value.items():
            free_text(child, child_key, found)
    elif isinstance(value, list):
        for child in value:
            free_text(child, key, found)
    elif isinstance(value, str) and key in TEXT_KEYS and len(value.strip()) >= 2 and not value.strip().isdigit():
        found.add(value.strip())
    return found


def test_no_line_holds_the_instruction_questions_answers_element_names_knowledge_or_paths():
    run, events = load_run()
    forbidden = free_text(run) | free_text(events)
    assert {run["goal"], "맞아", "칸 오른쪽 끝 화살표를 눌러", "WA1", "라인", run["exe"]} <= forbidden

    text = "\n".join(summarize(run, events, code="284f163"))

    assert [word for word in forbidden if word in text] == []


def marked(value, key=None, counter=None):
    """A copy with every string except codes replaced by a marker that must never be printed."""
    counter = [0] if counter is None else counter
    if isinstance(value, dict):
        return {child_key: marked(child, child_key, counter) for child_key, child in value.items()}
    if isinstance(value, list):
        return [marked(child, key, counter) for child in value]
    if isinstance(value, str) and key not in CODE_KEYS:
        counter[0] += 1
        return f"표식{counter[0]}MARK"
    return value


def test_marker_text_in_every_free_field_never_reaches_the_summary():
    for run, events in (load_run(), ({"status": "gave_up", "max_steps": 40}, detailed_events())):
        plain = summarize(run, events, code="284f163")

        lines = summarize(marked(run), marked(events), code="284f163")

        assert lines == plain
        assert not any("MARK" in line or "표식" in line for line in lines)


def test_values_that_are_not_codes_are_printed_as_other():
    events = [
        action(1, "click 누름", ok=False, code="화면 글자"),
        action(2, "click", ok=False, refused="r", refused_code="needs confirmation please"),
        {"type": "finish", "status": "끝남 — 이유", "steps": 2, "tokens": 5},
    ]

    lines = summarize({}, events, code="자유 글")

    assert "  코드: 알 수 없음" in lines
    assert "  판정: 기타 · 스텝 2 · 토큰 5" in lines
    assert "  거절: 기타 1" in lines and "  오류 코드: 기타 1" in lines
    assert "  마지막 동작: [2] click → 기타" in lines
    text = "\n".join(lines)
    assert [word for word in ("누름", "화면 글자", "please", "끝남", "이유", "자유 글") if word in text] == []


def test_an_interrupted_run_takes_steps_and_tokens_from_its_last_model_turn():
    events = [{"type": "model", "step": 1, "tokens": 900}, {"type": "model", "step": 2, "tokens": 2100}]

    lines = summarize({"status": "running", "max_steps": 40}, events)

    assert lines[1] == "  코드: 알 수 없음"
    assert lines[2] == "  판정: running · 스텝 2/40 · 토큰 2100"


def test_a_null_step_limit_in_run_json_leaves_the_limit_out():
    events = [{"type": "finish", "status": "claimed", "steps": 3, "tokens": 700}]

    with_limit = summarize({"max_steps": 40}, events)
    without = summarize({"max_steps": None}, events)

    assert with_limit[2] == "  판정: claimed · 스텝 3/40 · 토큰 700"
    assert without[2] == "  판정: claimed · 스텝 3 · 토큰 700"


def test_unknown_values_are_said_to_be_unknown_and_empty_lines_are_left_out():
    lines = summarize({"max_steps": "many"}, [])

    assert lines == [
        "요약 (화면 내용 없음)",
        "  코드: 알 수 없음",
        "  판정: 알 수 없음 · 스텝 알 수 없음 · 토큰 알 수 없음",
        "  동작: 0회 · 기대 지정 0 · 일치 0 · 불일치 0 · 확인 못 함 0",
        "  거절: 없음",
        "  오류 코드: 없음",
        "  질문: 0 · 답 0 · 무응답 0",
    ]


def test_the_last_observation_line_needs_recorded_kinds():
    old = summarize({}, [observation(kinds=KINDS, complete=True), observation()])
    unknown = summarize({}, [observation(kinds=KINDS)])
    cut = summarize({}, [observation(kinds=KINDS, complete=False)])

    assert not any(line.startswith("  마지막 관찰") for line in old)
    assert unknown[-1].endswith("빈 영역 2 · 잘림 알 수 없음")
    assert cut[-1].endswith("빈 영역 2 · 잘림 있음")


def test_kinds_that_are_not_counts_leave_the_observation_line_out():
    lines = summarize({}, [observation(kinds=dict(KINDS, text="마흔"), complete=True)])
    assert not any(line.startswith("  마지막 관찰") for line in lines)


@pytest.mark.parametrize(
    "result, outcome",
    [
        ({"ok": False, "refused": "r", "refused_code": "stale_capture"}, "stale_capture"),
        ({"ok": False, "refused": "r"}, "거절"),
        ({"ok": False, "code": "ELEMENT_NOT_FOUND"}, "ELEMENT_NOT_FOUND"),
        ({"ok": False}, "실패"),
        ({"ok": True, "expect": "window_opened", "expected_met": False}, "불일치"),
        ({"ok": True, "expect": "window_opened", "expected_met": True}, "일치"),
        ({"ok": True, "expect": "value_changed", "expected_met": None}, "확인 못 함"),
        ({"ok": True}, "성공"),
        ({}, "알 수 없음"),
    ],
)
def test_the_last_action_says_how_it_ended(result, outcome):
    lines = summarize({}, [action(3, "click", **result)])
    assert f"  마지막 동작: [3] click → {outcome}" in lines


def test_unconfirmed_actions_are_counted_apart_from_mismatches():
    events = [
        action(1, "click_part", ok=True, expect="value_changed", expected_met=None,
               effects={"none": True, "picture": {"changed": True, "pixels": 71}}),
        action(2, "click_part", ok=True, expect="value_changed", expected_met=False),
        action(3, "click_part", ok=True, expect="value_changed", expected_met=None),
        action(4, "click", ok=True),
    ]

    lines = summarize({}, events)

    assert "  동작: 4회 · 기대 지정 3 · 일치 0 · 불일치 1 · 확인 못 함 2" in lines


def test_a_recorded_verdict_decides_unconfirmed_before_the_old_rule():
    events = [
        action(1, "click_part", ok=True, expected_met=None, verdict="unconfirmed"),
        action(2, "click_part", ok=True, expect="value_changed", expected_met=None, verdict="other"),
        action(3, "click_part", ok=True, expect="value_changed", expected_met=None),
    ]

    lines = summarize({}, events)

    assert "  동작: 3회 · 기대 지정 2 · 일치 0 · 불일치 0 · 확인 못 함 2" in lines
    assert "  마지막 동작: [3] click_part → 확인 못 함" in lines
    assert summarize({}, events[:2])[3] == "  마지막 동작: [2] click_part → 성공"


def test_summarize_does_not_change_its_inputs():
    run, events = {"status": "gave_up", "max_steps": 40}, detailed_events()
    before = copy.deepcopy((run, events))

    summarize(run, events, code="284f163")

    assert (run, events) == before


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo, check=True,
                   capture_output=True)


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@needs_git
def test_code_version_is_the_short_commit_and_marks_local_changes(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "add", "a.py")
    git(tmp_path, "commit", "-q", "-m", "first")
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=tmp_path, capture_output=True,
                          text=True, check=True).stdout.strip()

    clean = code_version(tmp_path)
    (tmp_path / "new.txt").write_text("untracked", encoding="utf-8")
    untracked = code_version(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")

    assert clean == head and untracked == head
    assert code_version(tmp_path) == f"{head}+수정"


@needs_git
def test_code_version_only_looks_at_changes_inside_the_folder(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("y = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "first")

    (tmp_path / "other.py").write_text("y = 2\n", encoding="utf-8")

    assert "+" not in code_version(tmp_path / "agent")
    assert code_version(tmp_path).endswith("+수정")


@needs_git
def test_code_version_is_unknown_outside_a_repository(tmp_path):
    assert code_version(tmp_path) == "알 수 없음"


def test_code_version_is_unknown_without_git(tmp_path, monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(summary.subprocess, "run", missing)

    assert code_version(tmp_path) == "알 수 없음"


def test_code_version_is_unknown_when_git_hangs(tmp_path, monkeypatch):
    def hangs(*args, **_kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(summary.subprocess, "run", hangs)

    assert code_version(tmp_path) == "알 수 없음"


def record(tmp_path, run, events, extra_lines=()):
    folder = tmp_path / "20261114-010203"
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    lines = [json.dumps(event, ensure_ascii=False) for event in events] + list(extra_lines)
    (folder / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


FINISHED = [{"type": "model", "step": 2, "tokens": 50}, {"type": "finish", "status": "claimed", "steps": 2, "tokens": 50}]


def test_summary_of_reads_the_records_and_the_code_the_run_was_made_with(tmp_path):
    folder = record(tmp_path, {"status": "claimed", "max_steps": 40, "code": "284f163+수정"}, FINISHED)

    lines = summary.summary_of(folder)

    assert lines == summarize({"max_steps": 40}, FINISHED, code="284f163+수정")
    assert lines[1] == "  코드: 284f163+수정"


def test_summary_of_says_unknown_for_a_run_without_a_code(tmp_path):
    assert summary.summary_of(record(tmp_path, {"status": "claimed"}, FINISHED))[1] == "  코드: 알 수 없음"


def test_summary_of_can_take_the_viewers_status_for_an_unfinished_run(tmp_path):
    folder = record(tmp_path, {"status": "running"}, [{"type": "model", "step": 1, "tokens": 9}])

    assert summary.summary_of(folder)[2] == "  판정: running · 스텝 1 · 토큰 9"
    assert summary.summary_of(folder, status="interrupted")[2] == "  판정: interrupted · 스텝 1 · 토큰 9"


def test_read_records_skips_broken_lines_and_missing_files(tmp_path):
    folder = record(tmp_path, {"status": "claimed"}, FINISHED, extra_lines=['{"type": "model"', "[1, 2]", ""])
    (tmp_path / "empty").mkdir()

    run, events = summary.read_records(folder)
    nothing = summary.read_records(tmp_path / "empty")
    (folder / "run.json").write_text("{broken", encoding="utf-8")

    assert run == {"status": "claimed"} and events == FINISHED
    assert nothing == ({}, [])
    assert summary.read_records(folder) == ({}, FINISHED)


def refusing_run_json(monkeypatch, refusals):
    """Path.read_text that refuses run.json `refusals` times, as Windows does while the heartbeat replaces it."""
    real = Path.read_text
    seen = []

    def read_text(self, *args, **kwargs):
        if self.name == "run.json":
            seen.append(self.name)
            if len(seen) <= refusals:
                raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(summary.time, "sleep", lambda _s: None)
    return seen


def test_read_records_waits_out_a_run_json_being_replaced(tmp_path, monkeypatch):
    folder = record(tmp_path, {"status": "running", "code": "abc1234"}, FINISHED)
    seen = refusing_run_json(monkeypatch, summary.READ_ATTEMPTS - 1)

    run, events = summary.read_records(folder)

    assert run["code"] == "abc1234" and events == FINISHED
    assert len(seen) == summary.READ_ATTEMPTS


def test_read_records_gives_up_on_a_run_json_that_stays_locked(tmp_path, monkeypatch):
    folder = record(tmp_path, {"status": "running"}, FINISHED)
    seen = refusing_run_json(monkeypatch, refusals=10**6)

    with pytest.raises(PermissionError):
        summary.read_records(folder)
    assert len(seen) == summary.READ_ATTEMPTS
    assert summary.write_summary(folder) == (None, "PermissionError")


def test_write_summary_leaves_summary_txt_and_returns_its_lines(tmp_path):
    folder = record(tmp_path, {"status": "claimed", "code": "284f163"}, FINISHED)

    lines, problem = summary.write_summary(folder)

    assert problem is None and lines == summary.summary_of(folder)
    assert (folder / "summary.txt").read_text(encoding="utf-8") == "\n".join(lines) + "\n"


def test_write_summary_never_raises_and_names_only_the_kind_of_error(tmp_path, monkeypatch):
    missing = tmp_path / "gone"
    lines, problem = summary.write_summary(missing)
    assert lines is not None and problem == "FileNotFoundError"

    def broken(*_args, **_kwargs):
        raise RuntimeError("화면의 글 C:\\secret")

    monkeypatch.setattr(summary, "summarize", broken)
    assert summary.write_summary(record(tmp_path, {}, FINISHED)) == (None, "RuntimeError")


def test_current_code_is_the_version_of_the_agent_folder(monkeypatch):
    seen = []
    monkeypatch.setattr(summary, "code_version", lambda repo: seen.append(repo) or "abc1234")

    assert summary.current_code() == "abc1234"
    assert seen == [Path(summary.__file__).resolve().parent.parent]


STAGES = {"stage_count": 4, "stages_done": 3, "stage_calls": [3, 7, 1, 0], "limit_questions": 1,
          "plan_revisions": 0, "max_call_tokens": 9120}


def test_the_summary_shows_stage_counts():
    lines = summarize({"status": "verified", "stages": STAGES}, [])

    assert "  단계: 4 · 완료 3 · 단계별 호출 3/7/1/0 · 한도 질문 1 · 계획 고침 0" in lines
    assert "  호출당 토큰 최대: 9120" in lines
    assert lines.index("  호출당 토큰 최대: 9120") == lines.index(
        "  단계: 4 · 완료 3 · 단계별 호출 3/7/1/0 · 한도 질문 1 · 계획 고침 0") + 1


@pytest.mark.parametrize("run", [{}, {"stages": None}, {"stages": "4"},
                                 {"stages": {**STAGES, "stage_count": 0, "stages_done": 0, "stage_calls": []}}])
def test_a_run_without_stages_has_no_stage_line(run):
    lines = summarize({"status": "verified", **run}, [])

    assert not any(line.strip().startswith("단계:") for line in lines)


def test_a_run_without_a_plan_still_shows_its_largest_call():
    stats = {"stage_count": 0, "stages_done": 0, "stage_calls": [], "limit_questions": 0, "plan_revisions": 0,
             "max_call_tokens": 5000}

    lines = summarize({"status": "verified", "stages": stats}, [])

    assert "  호출당 토큰 최대: 5000" in lines


def test_odd_stage_values_are_unknown():
    odd = {**STAGES, "stages_done": "3", "stage_calls": [3, "x"], "limit_questions": -1, "max_call_tokens": "많음"}

    lines = summarize({"status": "verified", "stages": odd}, [])

    assert "  단계: 4 · 완료 알 수 없음 · 단계별 호출 알 수 없음 · 한도 질문 알 수 없음 · 계획 고침 0" in lines
    assert not any(line.strip().startswith("호출당 토큰 최대") for line in lines)
