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


VISION_TEXT = "ZQX-비전글-4410"
VISION_EVENT = {"type": "vision", "step": 3, "capture": "c1", "asked_by": "look", "question": f"{VISION_TEXT} 질문",
                "answer": f"{VISION_TEXT} 답", "marks": [2], "points": [{"label": VISION_TEXT, "x": 1, "y": 2}],
                "unsure": False, "error": "TimeoutError", "dropped": 0, "tokens": 700, "seconds": 1.0}


def test_summary_has_a_vision_line_in_split_mode():
    lines = summarize({"status": "verified", "vision_calls": 2, "vision_tokens": 1500}, [VISION_EVENT])

    assert "  비전 호출 2 · 비전 토큰 1500" in lines
    assert VISION_TEXT not in "\n".join(lines)


@pytest.mark.parametrize("run", [{"status": "verified"}, {"status": "verified", "vision_calls": 2},
                                 {"status": "verified", "vision_calls": "2", "vision_tokens": 5},
                                 {"status": "verified", "vision_calls": -1, "vision_tokens": 5}])
def test_summary_has_no_vision_line_without_vision(run):
    lines = summarize(run, [VISION_EVENT])

    assert not any("비전" in line for line in lines)


READY_MARK = "준비신호글자"


def ready_line(lines):
    return [line for line in lines if line.startswith("  준비:")]


def test_summary_has_a_ready_line():
    run = {"status": "verified", "ready": {"status": "ready", "waited_s": 12, "switched": True}}
    lines = summarize(run, [])
    assert ready_line(lines) == ["  준비: ready · 12초 · 대상 바뀜"]
    assert lines.index("  준비: ready · 12초 · 대상 바뀜") == 3
    stopped = {"status": "stopped", "ready": {"status": "timeout", "waited_s": 180, "switched": False}}
    assert ready_line(summarize(stopped, [])) == ["  준비: timeout · 180초 · 대상 그대로"]


def test_summary_without_ready_has_no_line():
    assert ready_line(summarize({"status": "verified"}, [])) == []


@pytest.mark.parametrize("ready", [{"status": "x", "waited_s": 1, "switched": True}, "ready",
                                   {"status": "ready", "waited_s": "12", "switched": True},
                                   {"status": "ready", "waited_s": 12, "switched": "yes"}])
def test_odd_ready_values_leave_the_line_out_or_unknown(ready):
    lines = ready_line(summarize({"status": "stopped", "ready": ready}, []))
    assert lines == [] or "알 수 없음" in lines[0] or "기타" in lines[0]
    assert all("x" not in line and "yes" not in line for line in lines)


def test_summary_never_shows_the_ready_text():
    events = [{"type": "ready", "phase": "start", "text": READY_MARK},
              {"type": "ready", "phase": "end", "status": "ready", "waited_ms": 1000, "polls": 2,
               "switched": False, "running_hint": False, "note": READY_MARK}]
    run = {"status": "verified", "ready": {"status": "ready", "waited_s": 1, "switched": False, "text": READY_MARK}}
    summary_text = "\n".join(summarize(run, events))
    assert READY_MARK not in summary_text
    assert "  준비: ready · 1초 · 대상 그대로" in summary_text


NEW_READY = {"status": "ready", "waited_s": 95, "switched": True, "read": "snapshot", "polls": 12, "reads": 31,
             "failed_reads": 4, "timeouts": 0, "max_read_ms": 7900}


def test_ready_line_with_the_new_counts():
    """The run.json shape of plan K7: how the ready wait read the screen, in numbers and a mode name."""
    assert ready_line(summarize({"status": "verified", "ready": NEW_READY}, [])) == [
        "  준비: ready · 95초 · snapshot · 확인 12바퀴 · 읽기 31회(실패 4, 시간 초과 0, 최대 7.9초) · 대상 바뀜"]
    found = {**NEW_READY, "status": "timeout", "switched": False, "read": "find", "timeouts": 6, "max_read_ms": 3020}
    assert ready_line(summarize({"status": "stopped", "ready": found}, [])) == [
        "  준비: timeout · 95초 · find · 확인 12바퀴 · 읽기 31회(실패 4, 시간 초과 6, 최대 3.0초) · 대상 그대로"]


@pytest.mark.parametrize("ms, shown", [(0, "0.0"), (49, "0.0"), (50, "0.1"), (950, "1.0"), (7949, "7.9"),
                                       (7950, "8.0"), (12345, "12.3")])
def test_the_longest_read_is_shown_in_tenths_of_a_second(ms, shown):
    lines = ready_line(summarize({"status": "verified", "ready": {**NEW_READY, "max_read_ms": ms}}, []))

    assert f"최대 {shown}초)" in lines[0]


def test_ready_line_of_an_older_run_is_unchanged():
    old = {"status": "ready", "waited_s": 12, "switched": True}

    assert ready_line(summarize({"status": "verified", "ready": old}, [])) == ["  준비: ready · 12초 · 대상 바뀜"]


@pytest.mark.parametrize("change", [
    {"read": "fast"}, {"read": None}, {"polls": -1}, {"reads": "31"}, {"failed_reads": True}, {"timeouts": 1.5},
    {"max_read_ms": None},
])
def test_ready_values_out_of_shape_fall_back_to_the_older_line(change):
    ready = {**NEW_READY, **change}

    assert ready_line(summarize({"status": "verified", "ready": ready}, [])) == ["  준비: ready · 95초 · 대상 바뀜"]


def test_a_missing_new_count_falls_back_to_the_older_line():
    ready = {key: value for key, value in NEW_READY.items() if key != "timeouts"}

    assert ready_line(summarize({"status": "verified", "ready": ready}, [])) == ["  준비: ready · 95초 · 대상 바뀜"]


WORKFLOW_MADE = {"type": "workflow", "created": True, "id": "w-20261119-001", "code": None, "steps": 12, "params": 2}
WORKFLOW_RUN = {"id": "w-20261119-001", "steps": 12, "steps_done": 5, "code": "EXPECT_UNMET"}
TEXT_MARK = "ZQX-워크플로글-4417"


def lines_with(prefix, run, events):
    return [line for line in summarize(run, events) if line.startswith(prefix)]


def test_summary_of_a_made_workflow():
    assert lines_with("  workflow:", {"status": "confirmed"}, [WORKFLOW_MADE]) == [
        "  workflow: 만듦 w-20261119-001 · 단계 12 · 파라미터 2"]


def test_summary_of_a_failed_authoring():
    failed = {"type": "workflow", "created": False, "id": None, "code": "LOCATOR_NOT_UNIQUE", "steps": 0, "params": 0}

    assert lines_with("  workflow:", {"status": "confirmed"}, [failed]) == ["  workflow: 못 만듦 · LOCATOR_NOT_UNIQUE"]


def test_a_failed_authoring_without_a_code_says_so():
    failed = {"type": "workflow", "created": False, "id": None, "code": None, "steps": 0, "params": 0}

    assert lines_with("  workflow:", {}, [failed]) == ["  workflow: 못 만듦 · 코드 없음"]


def test_the_last_workflow_event_is_summarized():
    failed = {"type": "workflow", "created": False, "id": None, "code": "DRAFT_INVALID", "steps": 0, "params": 0}

    assert lines_with("  workflow:", {}, [failed, WORKFLOW_MADE]) == [
        "  workflow: 만듦 w-20261119-001 · 단계 12 · 파라미터 2"]


def test_summary_of_a_replay():
    stopped = {"status": "stopped", "workflow_run": WORKFLOW_RUN}
    passed = {"status": "verified", "workflow_run": {**WORKFLOW_RUN, "steps_done": 12, "code": None}}

    assert lines_with("  재실행:", stopped, []) == ["  재실행: w-20261119-001 · 단계 5/12 · EXPECT_UNMET"]
    assert lines_with("  재실행:", passed, []) == ["  재실행: w-20261119-001 · 단계 12/12 · 통과"]


def test_a_run_without_workflow_has_no_workflow_lines():
    lines = summarize({"status": "confirmed"}, [])

    assert not any(line.startswith(("  workflow:", "  재실행:")) for line in lines)


def test_odd_workflow_values_are_unknown_or_other():
    odd_event = {"type": "workflow", "created": True, "id": "보고서", "code": None, "steps": -1, "params": "2"}
    odd_run = {"workflow_run": {"id": 7, "steps": "12", "steps_done": None, "code": None}}

    assert lines_with("  workflow:", {}, [odd_event]) == ["  workflow: 만듦 기타 · 단계 알 수 없음 · 파라미터 알 수 없음"]
    assert lines_with("  재실행:", odd_run, []) == ["  재실행: 기타 · 단계 알 수 없음/알 수 없음 · 통과"]
    assert lines_with("  workflow:", {}, [{"type": "workflow", "created": "yes"}]) == []
    assert lines_with("  재실행:", {"workflow_run": "w-20261119-001"}, []) == []


def test_a_code_that_is_not_code_shaped_is_shown_as_other():
    failed = {"type": "workflow", "created": False, "id": None, "code": f"{TEXT_MARK} 화면", "steps": 0, "params": 0}
    stopped = {"workflow_run": {**WORKFLOW_RUN, "code": "C:\\out\\a.xlsx 없음"}}

    assert lines_with("  workflow:", {}, [failed]) == ["  workflow: 못 만듦 · 기타"]
    assert lines_with("  재실행:", stopped, []) == ["  재실행: w-20261119-001 · 단계 5/12 · 기타"]


def test_summary_never_shows_goal_param_or_path_text():
    events = [
        {"type": "plan", "revision": 0, "stages": [{"n": 1, "goal": f"{TEXT_MARK} 목표", "done_when": TEXT_MARK,
                                                    "status": "done"}],
         "params": [{"name": "shopname", "value": f"{TEXT_MARK} WA0"}], "step": 1},
        {"type": "finish_note", "by": "human", "saved_path": f"C:\\{TEXT_MARK}\\front.xlsx"},
        WORKFLOW_MADE,
        {"type": "workflow_step", "n": 1, "kind": "act", "ok": False, "code": "EXPECT_UNMET", "ms": 15000,
         "goal": f"{TEXT_MARK} 단계 목표"},
    ]
    run = {"status": "stopped", "goal": TEXT_MARK, "workflow_run": WORKFLOW_RUN}

    text = "\n".join(summarize(run, events))

    assert TEXT_MARK not in text and "shopname" not in text and "front.xlsx" not in text
