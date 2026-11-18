"""A run summary a person can read aloud or paste anywhere, because it holds no screen content.

Run records can carry what was on the screen, the instruction, questions, answers, stored
knowledge and file paths. None of that may leave the machine the run happened on, so the
summary is built only from counts, codes, tool names and verdicts. Every printed word comes
from this module or passes CODE: a status, tool name or error code that is not a plain
identifier is printed as OTHER instead. The counts come from events.jsonl, except the stage
counts, which come from run.json's `stages` (numbers only, written when the run ends), and the
vision counts of a split-mode run, from run.json's `vision_calls` and `vision_tokens`; a value
the record does not hold is printed as UNKNOWN or its line is left out. The texts of a plan and
its stages are never read.

The run's own folder gets the same lines as summary.txt when it ends (write_summary); the
code line is the version recorded in run.json as `code` when the run started.
"""

import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

HEADER = "요약 (화면 내용 없음)"
UNKNOWN = "알 수 없음"
OTHER = "기타"
NONE = "없음"
NO_CODE = "코드 없음"
MODIFIED = "+수정"
UNCONFIRMED = "확인 못 함"
UNCONFIRMED_VERDICT = "unconfirmed"
SEPARATOR = " · "
CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,47}")
VERSION = re.compile(r"[0-9a-f]{4,40}(\+수정)?")
GIT_TIMEOUT_S = 5
SUMMARY_FILE = "summary.txt"
AGENT_HOME = Path(__file__).resolve().parent.parent
READ_ATTEMPTS = 5
READ_PAUSE_S = 0.02


def summarize(run, events, code=None):
    """Returns the summary lines of one run from its run.json and events.jsonl contents.

    `code` is what code_version returned for the code that made the run. The inputs are
    not changed.
    """
    actions = [event for event in events if event.get("type") == "action"]
    lines = [
        HEADER,
        f"  코드: {code if isinstance(code, str) and VERSION.fullmatch(code) else UNKNOWN}",
        "  판정: " + _verdict(run, events),
    ]
    if actions:
        lines.append("  마지막 동작: " + _last_action(actions[-1]))
    lines += [
        "  동작: " + _action_counts(actions),
        "  거절: " + _tally(_refusals(actions)),
        "  오류 코드: " + _tally(_errors(actions)),
        "  질문: " + _questions(events),
    ]
    stages = run.get("stages") if isinstance(run.get("stages"), dict) else {}
    if _count(stages.get("stage_count")):
        lines.append("  단계: " + _stages(stages))
    largest = _count(stages.get("max_call_tokens"))
    if largest is not None:
        lines.append(f"  호출당 토큰 최대: {largest}")
    vision_calls, vision_tokens = _count(run.get("vision_calls")), _count(run.get("vision_tokens"))
    if vision_calls is not None and vision_tokens is not None:
        lines.append(f"  비전 호출 {vision_calls}{SEPARATOR}비전 토큰 {vision_tokens}")
    knowledge = _knowledge(events)
    if knowledge:
        lines.append("  지식: " + knowledge)
    observed = _last_observation(events)
    if observed:
        lines.append("  마지막 관찰: " + observed)
    return lines


def read_records(run_dir):
    """Returns (run.json, events) of a run folder. A missing or broken run.json is {}; a missing
    events.jsonl is no events, and a line that is not a JSON object is skipped. While a run is
    live its recorder replaces run.json every few seconds, and Windows refuses to open it at that
    moment, so a refused read is tried again briefly before the PermissionError is raised."""
    run_dir = Path(run_dir)
    try:
        run = json.loads(_read_retrying(run_dir / "run.json"))
    except (FileNotFoundError, ValueError):
        run = {}
    if not isinstance(run, dict):
        run = {}
    try:
        text = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return run, events


def _read_retrying(path):
    for _ in range(READ_ATTEMPTS - 1):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(READ_PAUSE_S)
    return path.read_text(encoding="utf-8")


def summary_of(run_dir, status=None):
    """The summary lines of a run folder, with the code version recorded in its run.json.
    `status` stands in for run.json's (the viewer's reading of a run, such as interrupted)."""
    run, events = read_records(run_dir)
    if status is not None:
        run = {**run, "status": status}
    return summarize(run, events, code=run.get("code"))


def write_summary(run_dir):
    """Writes summary.txt into a finished run's folder and returns (lines, problem).

    Never raises, so a summary can never change a run's result: lines is None when the
    summary could not be made, and problem names only the kind of error (no path, no text).
    """
    try:
        lines = summary_of(run_dir)
    except Exception as error:
        return None, type(error).__name__
    try:
        (Path(run_dir) / SUMMARY_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as error:
        return lines, type(error).__name__
    return lines, None


def current_code():
    """The code version of this agent folder, recorded in run.json when a run starts."""
    return code_version(AGENT_HOME)


def code_version(repo, run=None):
    """Returns the short commit of `repo`, with +수정 when tracked files under it have changes.

    Only changes inside the `repo` folder count; untracked files do not. Without git, outside
    a repository, or when git fails, returns 알 수 없음.
    """
    run = run or subprocess.run
    try:
        head = run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True,
                   timeout=GIT_TIMEOUT_S)
        if head.returncode != 0 or not head.stdout.strip():
            return UNKNOWN
        status = run(["git", "status", "--porcelain", "--untracked-files=no", "--", "."], cwd=repo,
                     capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        if status.returncode != 0:
            return UNKNOWN
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    version = head.stdout.strip()
    if not VERSION.fullmatch(version):
        return UNKNOWN
    return version + (MODIFIED if status.stdout.strip() else "")


def _verdict(run, events):
    finish = next((event for event in reversed(events) if event.get("type") == "finish"), None)
    model = next((event for event in reversed(events) if event.get("type") == "model"), None)
    source = finish or model or {}
    status = (finish or {}).get("status") or run.get("status")
    steps = _count(source.get("steps", source.get("step")))
    tokens = _count(source.get("tokens"))
    limit = _count(run.get("max_steps"))
    step_text = UNKNOWN if steps is None else f"{steps}/{limit}" if limit else str(steps)
    return SEPARATOR.join([
        _code(status) if status is not None else UNKNOWN,
        f"스텝 {step_text}",
        f"토큰 {UNKNOWN if tokens is None else tokens}",
    ])


def _last_action(event):
    step = _count(event.get("step"))
    prefix = f"[{step}] " if step is not None else ""
    return f"{prefix}{_code(event.get('tool'))} → {_outcome(event.get('result') or {})}"


def _outcome(result):
    if "refused" in result:
        return _code(result["refused_code"]) if result.get("refused_code") else "거절"
    if result.get("ok") is False:
        return _code(result["code"]) if result.get("code") else "실패"
    if result.get("expected_met") is True:
        return "일치"
    if result.get("expected_met") is False:
        return "불일치"
    if _unconfirmed(result):
        return UNCONFIRMED
    if result.get("ok") is True:
        return "성공"
    return UNKNOWN


def _action_counts(actions):
    results = [action.get("result") or {} for action in actions]
    expected = sum(1 for result in results if result.get("expect"))
    met = sum(1 for result in results if result.get("expected_met") is True)
    missed = sum(1 for result in results if result.get("expected_met") is False)
    unconfirmed = sum(1 for result in results if _unconfirmed(result))
    return SEPARATOR.join([f"{len(actions)}회", f"기대 지정 {expected}", f"일치 {met}", f"불일치 {missed}",
                           f"{UNCONFIRMED} {unconfirmed}"])


def _unconfirmed(result):
    """An action judged neither met nor unmet: the text did not meet its expectation and the
    picture around its target changed. A record with a verdict says so by verdict "unconfirmed";
    an older record without one by an expectation with a null expected_met."""
    if "verdict" in result:
        return result["verdict"] == UNCONFIRMED_VERDICT
    return bool(result.get("expect")) and "expected_met" in result and result["expected_met"] is None


def _refusals(actions):
    found = Counter()
    for action in actions:
        result = action.get("result") or {}
        if "refused" in result:
            found[_code(result["refused_code"]) if result.get("refused_code") else NO_CODE] += 1
    return found


def _errors(actions):
    found = Counter()
    for action in actions:
        result = action.get("result") or {}
        if result.get("ok") is False and "refused" not in result:
            found[_code(result["code"]) if result.get("code") else NO_CODE] += 1
    return found


def _tally(found):
    if not found:
        return NONE
    return SEPARATOR.join(f"{name} {count}" for name, count in found.items())


def _questions(events):
    asked = sum(1 for event in events if event.get("type") == "question")
    answered = sum(1 for event in events if event.get("type") == "answer" and event.get("timed_out") is not True)
    return SEPARATOR.join([str(asked), f"답 {answered}", f"무응답 {max(asked - answered, 0)}"])


def _stages(stages):
    calls = stages.get("stage_calls")
    listed = isinstance(calls, list) and all(_count(value) is not None for value in calls)

    def counted(key):
        value = _count(stages.get(key))
        return UNKNOWN if value is None else value

    return SEPARATOR.join([
        str(counted("stage_count")),
        f"완료 {counted('stages_done')}",
        f"단계별 호출 {'/'.join(str(value) for value in calls) if listed else UNKNOWN}",
        f"한도 질문 {counted('limit_questions')}",
        f"계획 고침 {counted('plan_revisions')}",
    ])


def _knowledge(events):
    loaded = [event for event in events if event.get("type") == "knowledge_loaded"]
    used = sum(1 for event in events if event.get("type") == "knowledge_used")
    failed = sum(1 for event in events if event.get("type") == "knowledge_failed")
    if not loaded and not used and not failed:
        return None
    shown = sum(len(event["ids"]) for event in loaded if isinstance(event.get("ids"), list))
    return SEPARATOR.join([f"불러옴 {shown}", f"참고 {used}", f"참고 뒤 실패 {failed}"])


def _last_observation(events):
    last = next((event for event in reversed(events) if event.get("type") == "observation"), {})
    kinds = last.get("kinds")
    if not isinstance(kinds, dict):
        return None
    counts = [_count(kinds.get(key)) for key in ("named_ref", "unnamed_ref", "text", "blank")]
    if any(count is None for count in counts):
        return None
    complete = last.get("complete")
    cut = "잘림 없음" if complete is True else "잘림 있음" if complete is False else f"잘림 {UNKNOWN}"
    named, unnamed, text, blank = counts
    with_id = _count(kinds.get("unnamed_ref_with_id"))
    unnamed_text = f"이름 없는 ref {unnamed}" + (f"(id 있음 {with_id})" if with_id is not None else "")
    return SEPARATOR.join([f"이름 있는 ref {named}", unnamed_text, f"글자 {text}", f"빈 영역 {blank}", cut])


def _code(value):
    return value if isinstance(value, str) and CODE.fullmatch(value) else OTHER


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
