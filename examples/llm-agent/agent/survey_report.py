"""The session's end: the summary a person reads on the company PC, the block, and the two
generated documents of the repository.

`finish_session` is what both the capture session's end and `survey.py --block` run, so the
two always agree. The summary is the only file that may show the original class and module
names, and only in its last section, when the session was run with `--show-names`.
"""

import json
from pathlib import Path

from agent.hypotheses import HYPOTHESES, judge_all, manual_codes
from agent.labels import load_labels
from agent.survey_block import render_block
from agent.survey_codes import MEANINGS, SURVEY_VERSION
from agent.survey_facts import (BLOCK_TXT, LABELS_YAML, NAMES_JSON, NOTES_MD, SUMMARY_MD, capture_codes, capture_dir,
                                load_session)

HYPOTHESES_DOC = Path("docs/company-survey/hypotheses.md")
CODES_DOC = Path("docs/company-survey/codes.md")
NAMES_HEADING = "## 원래 이름 (옮겨 적기 전에 확인)"
GENERATED_NOTE = "이 파일은 `python survey.py --write-docs`로 만듭니다. 손으로 고치지 마세요."
CODE_GROUPS = (("scene", "장면 코드 (labels.yaml의 captures)"), ("kind", "창 종류"), ("rel", "프로세스 관계"),
               ("state", "캡처 상태"), ("read", "창 읽기 결과"), ("size", "창 크기"), ("shot", "화면 그림"),
               ("slow", "느린 단계"), ("shallow", "깊게 읽기 대체"), ("app_seen", "앱을 본 방법"), ("failure", "실패와 건너뜀 이유"))


def _shown(verdict):
    return verdict.code + (f"({verdict.short})" if verdict.short else "")


def _hypothesis_section(hypothesis, verdict):
    lines = [f"### {hypothesis.id} {hypothesis.title}", "", f"- 판정: `{_shown(verdict)}`"]
    if verdict.evidence:
        lines.append(f"- 근거: {verdict.evidence}")
    lines.append(f"- 왜: {hypothesis.why}")
    if hypothesis.kind == "manual":
        lines += [f"- 답하는 방법: {hypothesis.how_to_answer}",
                  f"- 답은 `labels.yaml`의 `manual`에 `{hypothesis.id}: <코드>`로, 사람이 말한 답만 적습니다."]
    lines.append("- 판정별 다음 할 일:")
    lines += [f"  - `{code}`{' ← 이번 판정' if code == verdict.code else ''}: {text}"
              for code, text in hypothesis.next.items()]
    if hypothesis.status != "open":
        lines.append(f"- 닫힘: {hypothesis.closed_note or ''}")
    return lines + [""]


def _capture_rows(loaded, labels):
    rows = ["| 번호 | 시각(초) | 구조 코드 | 장면 | 창 수 | 그림 | 느린 단계 | 폴더 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for c in loaded.captures:
        rows.append(f"| {c.id} | {c.t:.1f} | {'/'.join(capture_codes(c))} | {labels.captures.get(c.id, '?')} | "
                    f"{len(c.windows)} | {c.shot} | {c.slow} | {capture_dir(Path('.'), c.id).name} |")
    return rows


def _names_section(names):
    lines = [NAMES_HEADING, "", "회사 PC에만 남기는 이름입니다. 옮겨 적기 전에 회사 정보가 아닌지 확인하세요.", ""]
    for key, title in (("classes", "### 창 클래스"), ("modules", "### 모듈")):
        lines += [title, ""] + [f"- {name}" for name in names.get(key, [])] + [""]
    return lines


def summary_markdown(loaded, judged, labels, names, changed_files) -> str:
    s = loaded.session
    block = render_block(loaded, judged, labels)
    lines = ["# 캡처 세션 정리", "",
             f"- 시작: {s.started} · 길이 {round(s.duration_s / 60)}분 · 시스템 {s.system}",
             f"- 조사 버전: v{s.version} (지금 코드 v{SURVEY_VERSION})",
             f"- 커밋: {s.head or '커밋?'} · 세션 시작 때 바뀐 추적 파일 {s.changed}개",
             f"- 바뀐 추적 파일: {', '.join(changed_files) if changed_files else '없음'}",
             f"- 단축키: {s.hotkey} · 앱을 본 방법: {s.app_seen} · 경로를 못 읽은 프로세스 {s.unknown_paths}", "",
             "## 전달 블록", "", "세션 시작 때의 코드 상태 기준입니다. `--block`이 코드 상태를 다시 잰 블록은 "
             f"`{BLOCK_TXT}`와 콘솔에 있습니다.", "", "```", *block, "```", "",
             f"장면 코드는 회사 Claude Code가 `{LABELS_YAML}`에 적고, 자유로운 정리는 `{NOTES_MD}`(회사 PC 전용)에 씁니다.", "",
             "## 가설", ""]
    for hypothesis, verdict in judged:
        lines += _hypothesis_section(hypothesis, verdict)
    lines += ["## 캡처", "", *_capture_rows(loaded, labels), ""]
    if names is not None:
        lines += _names_section(names)
    return "\n".join(lines).rstrip() + "\n"


def _read_names(folder):
    try:
        names = json.loads((Path(folder) / NAMES_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return names if isinstance(names, dict) else None


def finish_session(folder: Path, now=None) -> list:
    """Judges the session folder, writes `summary.md` and `block.txt`, and returns the block.
    A `LabelError` propagates before anything is written."""
    folder = Path(folder)
    loaded = load_session(folder)
    labels = load_labels(folder / LABELS_YAML, tuple(c.id for c in loaded.captures), manual_codes())
    judged = judge_all(loaded, labels)
    block = render_block(loaded, judged, labels, now)
    changed = tuple(now.changed) if now is not None else ()
    summary = summary_markdown(loaded, judged, labels, _read_names(folder), changed)
    (folder / SUMMARY_MD).write_text(summary, encoding="utf-8", newline="\n")
    (folder / BLOCK_TXT).write_text("\n".join(block) + "\n", encoding="utf-8", newline="\n")
    return block


def hypotheses_markdown() -> str:
    lines = ["# 회사 시스템 가설", "", GENERATED_NOTE, "",
             "캡처 세션이 판정하는 질문입니다. 정의는 `agent/hypotheses.py` 한 곳에 있습니다. "
             "닫힌 가설은 판정하지만 전달 블록에서 빠지고, 번호는 다시 쓰지 않습니다.", ""]
    for h in HYPOTHESES:
        lines += [f"## {h.id} {h.title}", "", f"- 종류: {'자동 판정' if h.kind == 'auto' else '사람이 답함'}",
                  f"- 상태: {h.status}", f"- 왜: {h.why}"]
        if h.how_to_answer:
            lines.append(f"- 답하는 방법: {h.how_to_answer}")
        if h.closed_note:
            lines.append(f"- 닫힌 까닭: {h.closed_note}")
        lines += ["- 판정 코드와 집에서 할 일:"] + [f"  - `{code}`: {text}" for code, text in h.next.items()] + [""]
    lines += ["## 모든 가설에 공통", "", "- `SKIPPED(<이유>)`: 판정하지 못함. 이유 코드는 `codes.md`의 실패 코드.",
              "- `ERROR(<예외>)`: 판정 중 오류. 집에서 판정 코드를 고칩니다.",
              "- `?`: 사람이 답하지 않음.", ""]
    return "\n".join(lines).rstrip() + "\n"


def codes_markdown() -> str:
    lines = ["# 캡처 세션 코드표", "", GENERATED_NOTE, "",
             "캡처 세션, `labels.yaml`, 전달 블록이 쓰는 코드입니다. 정의는 `agent/survey_codes.py` 한 곳에 있습니다.",
             "`labels.yaml`의 값에는 아래 코드만 씁니다. 판단하지 않을 때는 그 캡처를 빼거나 `\"?\"`처럼 따옴표로 감쌉니다.", ""]
    for group, title in CODE_GROUPS:
        lines += [f"## {title}", "", "| 코드 | 뜻 |", "| --- | --- |"]
        prefix = f"{group}:"
        lines += [f"| `{key[len(prefix):]}` | {text} |" for key, text in MEANINGS.items() if key.startswith(prefix)]
        lines.append("")
    lines += ["## 사람이 답하는 가설 (labels.yaml의 manual)", "", "| 가설 | 코드 |", "| --- | --- |"]
    lines += [f"| {hid} | {', '.join(f'`{code}`' for code in codes)} |" for hid, codes in manual_codes().items()]
    return "\n".join(lines).rstrip() + "\n"
