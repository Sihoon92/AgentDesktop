import base64
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

from viewer.export import build_export, write_export

HERE = Path(__file__).parent
SAMPLE = HERE / "fixtures" / "run_sample" / "20261107-120000"
REAL_STATIC = HERE.parent / "viewer" / "static"
NOW = datetime(2026, 11, 8, 1, 0, 0, tzinfo=timezone.utc)
SERVER_STRINGS = ("/static/", "/api/", "?t=", "127.0.0.1", "localhost", "X-Agent-Token")
STUB_INDEX = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>뷰어</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="/static/app.js" defer></script>
</head>
<body>
  <main id="stage"></main>
</body>
</html>
"""


class Scripts(HTMLParser):
    """Collects every <script> as (attributes, text) the way a browser splits the document."""

    def __init__(self):
        super().__init__()
        self.scripts = []
        self.styles = []
        self._open = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._open = (tag, dict(attrs), [])

    def handle_data(self, data):
        if self._open is not None:
            self._open[2].append(data)

    def handle_endtag(self, tag):
        if self._open is not None and tag == self._open[0]:
            kind, attrs, parts = self._open
            (self.scripts if kind == "script" else self.styles).append((attrs, "".join(parts)))
            self._open = None


def parsed(html):
    parser = Scripts()
    parser.feed(html)
    parser.close()
    return parser


def payload(html):
    [(_, text)] = [(a, t) for a, t in parsed(html).scripts if a.get("id") == "run-export"]
    return json.loads(text)


def copy_sample(tmp_path, run_id="20261107-120000"):
    target = tmp_path / "runs" / run_id
    shutil.copytree(SAMPLE, target)
    return target


def stub_static(tmp_path, js='console.log("export");', css="body { margin: 0; }", index=STUB_INDEX):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(index, encoding="utf-8")
    (static / "app.js").write_text(js, encoding="utf-8")
    (static / "style.css").write_text(css, encoding="utf-8")
    return static


def sample_events(run_dir):
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_export_carries_progress(tmp_path):
    run_dir = copy_sample(tmp_path)
    records = [{"id": 1, "phase": "start", "kind": "model", "t_ms": 0, "step": 1},
               {"id": 1, "phase": "end", "kind": "model", "t_ms": 900, "ok": True}]
    (run_dir / "progress.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    assert payload(build_export(run_dir, REAL_STATIC, NOW))["progress"] == records


def test_export_without_progress_has_an_empty_list(tmp_path):
    run_dir = copy_sample(tmp_path)

    assert payload(build_export(run_dir, REAL_STATIC, NOW))["progress"] == []


def test_payload_round_trips_run_events_and_frames(tmp_path):
    run_dir = copy_sample(tmp_path)

    html = build_export(run_dir, REAL_STATIC, NOW)

    data = payload(html)
    assert data["export"] == 1
    assert data["exported_at"] == "2026-11-08T01:00:00Z"
    assert data["run"] == json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert data["events"] == sample_events(run_dir)
    frame_names = sorted(p.name for p in (run_dir / "frames").iterdir())
    assert sorted(data["frames"]) == frame_names
    for name in frame_names:
        uri = data["frames"][name]
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == (run_dir / "frames" / name).read_bytes()


def test_real_viewer_page_keeps_no_server_reference_outside_its_code(tmp_path):
    html = build_export(copy_sample(tmp_path), REAL_STATIC, NOW)

    page = parsed(html)
    code = [text for attrs, text in page.scripts if not attrs]
    skeleton = html
    for text in code:
        skeleton = skeleton.replace(text, "")
    for text in SERVER_STRINGS:
        assert text not in skeleton, text
    assert not re.search(r"""(src|href)\s*=\s*["']?/""", skeleton)
    assert code[-1].strip().endswith("start();")
    [(_, css)] = page.styles
    assert css.strip() == (REAL_STATIC / "style.css").read_text(encoding="utf-8").strip()


def test_stub_page_has_no_server_string_anywhere(tmp_path):
    html = build_export(copy_sample(tmp_path), stub_static(tmp_path), NOW)

    for text in SERVER_STRINGS:
        assert text not in html, text
    assert "<link" not in html


def test_app_code_runs_after_the_record_at_the_end_of_the_body(tmp_path):
    html = build_export(copy_sample(tmp_path), stub_static(tmp_path), NOW)

    head = html[: html.index("</head>")]
    assert "<script" not in head
    record = html.index('<script id="run-export" type="application/json">')
    app = html.index('console.log("export");')
    assert html.index('<main id="stage">') < record < app < html.index("</body>")


def test_closing_script_tags_inside_events_or_code_do_not_cut_the_document(tmp_path):
    run_dir = copy_sample(tmp_path)
    hostile = "</script><script>alert(1)</script><!-- <script>"
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps({"type": "model", "step": 99, "tokens": 1, "note": hostile}, ensure_ascii=False) + "\n")
    static = stub_static(tmp_path, js='const end = "</script>"; const css = "</STYLE>";',
                         css='body::after { content: "</style>"; }')

    html = build_export(run_dir, static, NOW)

    page = parsed(html)
    assert len(page.scripts) == 2
    [app] = [text for attrs, text in page.scripts if not attrs]
    [(_, css)] = page.styles
    assert app.strip() == 'const end = "<\\/script>"; const css = "</STYLE>";'
    assert css.strip().startswith("body::after") and css.strip().endswith("}")
    assert payload(html)["events"][-1]["note"] == hostile
    assert "alert(1)" not in app


def test_other_runs_stay_out_of_the_export(tmp_path):
    run_dir = copy_sample(tmp_path)
    other = copy_sample(tmp_path, "20261107-130000")
    (other / "run.json").write_text(json.dumps({"run_id": "20261107-130000", "goal": "다른 실행의 비밀 지시"}),
                                    encoding="utf-8")

    html = build_export(run_dir, stub_static(tmp_path), NOW)

    assert "20261107-130000" not in html
    assert "다른 실행의 비밀 지시" not in html


def test_old_logs_and_missing_runs_are_refused(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    legacy = runs / "20261107-090000.jsonl"
    legacy.write_text('{"step": 1}\n', encoding="utf-8")
    static = stub_static(tmp_path)

    with pytest.raises(ValueError, match="옛 로그"):
        build_export(legacy, static, NOW)
    with pytest.raises(ValueError):
        build_export(runs / "20261107-100000", static, NOW)
    with pytest.raises(ValueError):
        write_export(legacy, static, tmp_path / "out.html", NOW)
    assert not (tmp_path / "out.html").exists()


def test_unreadable_run_json_is_refused(tmp_path):
    run_dir = copy_sample(tmp_path)
    (run_dir / "run.json").write_text('{"run_id": ', encoding="utf-8")

    with pytest.raises(ValueError, match="run.json"):
        build_export(run_dir, stub_static(tmp_path), NOW)


@pytest.mark.parametrize(
    "old, new",
    [('<script src="/static/app.js" defer></script>', '<script src="/static/main.js"></script>'),
     ("</head>", ""),
     ("<title>뷰어</title>", '<title>뷰어</title><img src="/api/runs/x/frames/0001.png">')],
)
def test_a_page_without_the_expected_tags_is_refused(tmp_path, old, new):
    index = STUB_INDEX.replace(old, new)

    with pytest.raises(ValueError, match="index.html"):
        build_export(copy_sample(tmp_path), stub_static(tmp_path, index=index), NOW)


def test_write_export_returns_bytes_and_never_overwrites(tmp_path):
    run_dir = copy_sample(tmp_path)
    static = stub_static(tmp_path)
    out = tmp_path / "out.html"

    written = write_export(run_dir, static, out, NOW)

    assert written == out.stat().st_size
    assert payload(out.read_text(encoding="utf-8"))["run"]["run_id"] == "20261107-120000"
    before = out.read_bytes()
    with pytest.raises(FileExistsError):
        write_export(run_dir, static, out, NOW)
    assert out.read_bytes() == before


QUESTIONS = HERE / "fixtures" / "run_questions"


def copy_questions(tmp_path):
    target = tmp_path / "runs" / "20261113-101500"
    shutil.copytree(QUESTIONS, target)
    return target


def test_export_carries_questions_and_answers(tmp_path):
    html = build_export(copy_questions(tmp_path), REAL_STATIC, NOW)

    events = payload(html)["events"]
    assert events == sample_events(QUESTIONS)
    assert [event["id"] for event in events if event["type"] == "question"] == ["q1", "q2", "q3", "q4"]
    assert "맞아" in html and "칸 오른쪽 끝 화살표를 눌러" in html
    assert payload(html)["run"]["status"] == "confirmed"


def test_export_inlines_every_viewer_script_in_page_order_after_the_record(tmp_path):
    html = build_export(copy_questions(tmp_path), REAL_STATIC, NOW)

    code = [text for attrs, text in parsed(html).scripts if not attrs]
    names = ["chat.js", "knowledge.js", "workflows.js", "profiles.js", "stages.js", "progress.js", "app.js"]
    assert [text.strip() for text in code] == [(REAL_STATIC / name).read_text(encoding="utf-8").strip() for name in names]
    record = html.index('<script id="run-export" type="application/json">')
    assert record < html.index("function renderChat") < html.index("function start()")


def test_export_has_no_answer_form(tmp_path):
    html = build_export(copy_questions(tmp_path), REAL_STATIC, NOW)

    skeleton = html
    for text in [text for attrs, text in parsed(html).scripts]:
        skeleton = skeleton.replace(text, "")
    assert "<textarea" not in skeleton
    assert '<div id="chat-composer" class="chat-composer"></div>' in skeleton
    assert "/answer" not in skeleton


class Elements(HTMLParser):
    """Collects every element as (tag, attributes, ids of its open ancestors)."""

    VOID = {"meta", "link", "input", "img", "br", "hr"}

    def __init__(self):
        super().__init__()
        self.elements = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs, [a.get("id") for _, a in self._stack]))
        if tag not in self.VOID:
            self._stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), [a.get("id") for _, a in self._stack]))

    def handle_endtag(self, tag):
        if all(open_tag != tag for open_tag, _ in self._stack):
            return
        while self._stack.pop()[0] != tag:
            pass


def page_elements(html):
    parser = Elements()
    parser.feed(html)
    parser.close()
    return parser.elements


@pytest.mark.parametrize("source", ["page", "export"])
def test_every_panel_folds_with_a_button_that_names_what_it_folds(tmp_path, source):
    html = ((REAL_STATIC / "index.html").read_text(encoding="utf-8") if source == "page"
            else build_export(copy_questions(tmp_path), REAL_STATIC, NOW))
    elements = page_elements(html)
    ids = {attrs.get("id") for _, attrs, _ in elements}

    toggles = {attrs["id"]: attrs for tag, attrs, _ in elements if tag == "button" and attrs.get("id", "").startswith("toggle-")
               and "aria-controls" in attrs}
    assert {"toggle-runs", "toggle-chat", "toggle-details", "toggle-settings"} <= set(toggles)
    for attrs in toggles.values():
        assert attrs["type"] == "button"
        assert attrs["aria-expanded"] in ("true", "false")
        assert attrs["aria-controls"] in ids


def test_the_chat_is_its_own_column_left_of_the_capture():
    elements = page_elements((REAL_STATIC / "index.html").read_text(encoding="utf-8"))
    order = [attrs.get("id") or attrs.get("class") for _, attrs, ancestors in elements if ancestors and ancestors[-1] == "layout"]
    chat = next(ancestors for _, attrs, ancestors in elements if attrs.get("id") == "chat-composer")

    assert order == ["runs-panel", "chat", "stage-area", "details-panel"]
    assert "details-panel" not in chat and "chat" in chat


def test_a_script_named_in_the_page_but_missing_is_refused(tmp_path):
    index = STUB_INDEX.replace('<script src="/static/app.js" defer></script>',
                               '<script src="/static/extra.js" defer></script>\n  <script src="/static/app.js" defer></script>')

    with pytest.raises(ValueError, match="extra.js"):
        build_export(copy_sample(tmp_path), stub_static(tmp_path, index=index), NOW)


def test_extra_scripts_are_inlined_before_the_app(tmp_path):
    index = STUB_INDEX.replace('<script src="/static/app.js" defer></script>',
                               '<script src="/static/extra.js" defer></script>\n  <script src="/static/app.js" defer></script>')
    static = stub_static(tmp_path, index=index)
    (static / "extra.js").write_text('console.log("extra");', encoding="utf-8")

    html = build_export(copy_sample(tmp_path), static, NOW)

    code = [text.strip() for attrs, text in parsed(html).scripts if not attrs]
    assert code == ['console.log("extra");', 'console.log("export");']
    assert "/static/" not in html


PLAN_EVENTS = [
    {"type": "plan", "step": 1, "revision": 0,
     "stages": [{"n": 1, "goal": "첫 화면을 연다", "done_when": "제목이 보인다", "status": "active"},
                {"n": 2, "goal": "값을 고른다", "done_when": "칸에 값이 보인다", "status": "pending"}]},
    {"type": "model", "step": 2, "tokens": 900, "call_tokens": 900, "stage": 1, "stage_calls": 1, "stage_limit": 8},
    {"type": "stage", "step": 2, "n": 1, "status": "done", "calls": 1, "limit": 8, "kind": "text", "note": "제목(표시 1)"},
    {"type": "stage", "step": 2, "n": 2, "status": "started", "calls": 0, "limit": 8},
    {"type": "question", "id": "q9", "step": 3, "kind": "stage_limit", "observation": 1, "window_id": "w-1",
     "text": "한도", "stage": 2, "calls": 8, "intents": ["값을 고른다"], "candidates": [], "proposed": None,
     "basis": [], "items": []},
    {"type": "answer", "id": "q9", "text": "", "pin": None, "accepted": True, "items": [], "timed_out": False},
]


VISION_EVENTS = [
    {"type": "capture", "id": "c1", "step": 1, "observation": 1, "mark": None, "reason": "look",
     "file": "captures/c1.png"},
    {"type": "vision", "step": 1, "capture": "c1", "asked_by": "look", "question": "</script><b>which</b>?",
     "answer": "mark 3", "marks": [3], "points": [{"label": "dot", "x": 10, "y": 20}], "unsure": False,
     "error": None, "dropped": 0, "tokens": 812, "seconds": 2.4},
]


def test_export_keeps_vision_events(tmp_path):
    run_dir = copy_sample(tmp_path)
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        for event in VISION_EVENTS:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    html = build_export(run_dir, REAL_STATIC, NOW)

    events = payload(html)["events"]
    assert events[-len(VISION_EVENTS):] == VISION_EVENTS
    assert "</script><b>" not in html


def write_captures(run_dir, *names):
    folder = run_dir / "captures"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())


def test_export_bundles_only_the_captures_vision_events_name(tmp_path):
    run_dir = copy_sample(tmp_path)
    write_captures(run_dir, "c1.png", "c2.png", "c3.png")
    odd = {**VISION_EVENTS[1], "capture": "../run"}
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        for event in [*VISION_EVENTS, VISION_EVENTS[1], odd, {**VISION_EVENTS[1], "capture": "c9"}]:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    captures = payload(build_export(run_dir, REAL_STATIC, NOW))["captures"]

    assert sorted(captures) == ["c1.png"]
    assert base64.b64decode(captures["c1.png"].split(",", 1)[1]) == (run_dir / "captures" / "c1.png").read_bytes()


def test_viewer_code_shows_vision_answers_as_text(tmp_path):
    app = (REAL_STATIC / "app.js").read_text(encoding="utf-8")
    html = build_export(copy_sample(tmp_path), REAL_STATIC, NOW)

    assert "function visionSection" in app and "function captureUrl" in app
    assert "innerHTML" not in app and "insertAdjacentHTML" not in app
    assert "function visionSection" in html


def test_export_without_vision_events_is_unchanged_by_captures(tmp_path):
    plain = copy_sample(tmp_path)
    before = build_export(plain, REAL_STATIC, NOW)
    write_captures(plain, "c1.png", "c2.png")
    with (plain / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(VISION_EVENTS[0], ensure_ascii=False) + "\n")
    after = payload(build_export(plain, REAL_STATIC, NOW))

    assert "captures" not in payload(before)
    assert "captures" not in after
    assert after["events"][-1] == VISION_EVENTS[0]


def test_export_carries_plan_stage_and_limit_events_and_the_stage_code(tmp_path):
    run_dir = copy_sample(tmp_path)
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        for event in PLAN_EVENTS:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    html = build_export(run_dir, REAL_STATIC, NOW)

    events = payload(html)["events"]
    assert events[-len(PLAN_EVENTS):] == PLAN_EVENTS
    assert "function renderStageBar" in html and "function lockedLimitAnswer" in html
    assert html.index('<script id="run-export"') < html.index("function renderStageBar") < html.index("function start()")


def page_skeleton(html):
    for text in [text for attrs, text in parsed(html).scripts]:
        html = html.replace(text, "")
    return html


@pytest.mark.parametrize("source", ["page", "export"])
def test_the_stage_bar_sits_hidden_between_the_toolbar_and_the_capture(tmp_path, source):
    html = ((REAL_STATIC / "index.html").read_text(encoding="utf-8") if source == "page"
            else build_export(copy_questions(tmp_path), REAL_STATIC, NOW))
    elements = page_elements(page_skeleton(html))
    order = [attrs.get("class") for tag, attrs, _ in elements
             if tag in ("div", "section") and attrs.get("class") in ("stage-toolbar", "stage-bar", "stage-scroll")]
    bar, bar_ancestors = next((attrs, ancestors) for _, attrs, ancestors in elements if attrs.get("id") == "stage-bar")

    assert order == ["stage-toolbar", "stage-bar", "stage-scroll"]
    assert bar_ancestors[-2:] == ["layout", None]
    assert "hidden" in bar and bar.get("aria-label") == "단계"
    assert {"stage-list", "stage-detail", "stage-detail-toggle"} <= {attrs.get("id") for _, attrs, _ in elements}


def test_timeline_cells_are_called_steps_and_plan_parts_stages():
    page = (REAL_STATIC / "index.html").read_text(encoding="utf-8")
    app = (REAL_STATIC / "app.js").read_text(encoding="utf-8")

    for old in ("단계 이동", "단계 정보", "단계 타임라인", "단계 뷰어"):
        assert old not in page and old not in app
    assert "← → 스텝 이동" in page and "<h2>스텝 정보</h2>" in page and 'aria-label="스텝 타임라인"' in page
    assert 'details: "스텝 정보"' in app


# --- workflows in an export (plan task 9) ------------------------------------------------------

REPLAY_EVENTS = [
    {"type": "observation", "id": 1, "source": "ready"},
    {"type": "action", "step": None, "tool": "click", "intent": "메뉴 목록이 열림", "before": 1},
    {"type": "observation", "id": 2, "source": "action"},
    {"type": "workflow_step", "n": 1, "kind": "act", "ok": True, "code": None, "ms": 120, "goal": "메뉴 목록이 열림"},
    {"type": "action", "step": None, "tool": "click_part", "intent": "Shop이 고른 값만 선택됨", "before": 2},
    {"type": "workflow_step", "n": 2, "kind": "select_only", "ok": False, "code": "EXPECT_UNMET", "ms": 15000,
     "goal": "Shop이 고른 값만 선택됨"},
    {"type": "finish", "status": "stopped", "steps": 1, "reason": "EXPECT_UNMET", "tokens": 0},
]


def replay_run(tmp_path, events=REPLAY_EVENTS):
    run_dir = copy_sample(tmp_path)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    meta.update(started_by="workflow", model="workflow", status="stopped")
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                                          encoding="utf-8")
    return run_dir


def exported_script(html, marker):
    return next(text for attrs, text in parsed(html).scripts if not attrs and marker in text)


def run_marks(script, run, events, calls):
    """Runs the exported workflows.js in node with `state` set to the run and returns what `calls` give."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    program = (f"const state = {{ run: {json.dumps(run)}, events: {json.dumps(events, ensure_ascii=False)} }};\n"
               f"{script}\nprocess.stdout.write(JSON.stringify([{', '.join(calls)}]));\n")
    out = subprocess.run([node, "-e", program], capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


def test_the_export_hides_the_workflow_list_and_never_starts_it(tmp_path):
    html = build_export(replay_run(tmp_path), REAL_STATIC, NOW)
    elements = {attrs.get("id"): attrs for _, attrs, _ in page_elements(page_skeleton(html)) if attrs.get("id")}
    app = (REAL_STATIC / "app.js").read_text(encoding="utf-8")
    workflows = (REAL_STATIC / "workflows.js").read_text(encoding="utf-8")

    assert "hidden" in elements["show-workflows"] and "hidden" in elements["workflow-view"]
    assert "hidden" in elements["view-switch"]
    start = app[app.index("function start()"):]
    assert start.index("startExport(exported);\n    return;") < start.index("initWorkflows")
    init = workflows[workflows.index("async function initWorkflows()"):]
    assert init.index("state.exportMode") < init.index("knowledgeRequest(")
    assert "innerHTML" not in workflows and "insertAdjacentHTML" not in workflows


def test_the_export_shows_a_replays_steps_and_where_it_stopped(tmp_path):
    html = build_export(replay_run(tmp_path), REAL_STATIC, NOW)
    record = payload(html)
    script = exported_script(html, "function workflowTag")

    tags, first, second, summary = run_marks(script, record["run"], record["events"], [
        "[1, 4, 6].map((at) => workflowTag({ at }))",
        "workflowStepText({ at: 1 })",
        "workflowStepText({ at: 4 })",
        "workflowSummaryPart(state.run, state.events)",
    ])

    assert [event["type"] for event in record["events"]].count("workflow_step") == 2
    assert tags == ["W1 ", "W2 ", ""]
    assert first == "1. 메뉴 목록이 열림 (동작 · 통과)"
    assert second == "2. Shop이 고른 값만 선택됨 (이것만 선택 · 멈춤 EXPECT_UNMET)"
    assert summary == "workflow 멈춘 단계 2 · EXPECT_UNMET"
    app = exported_script(html, "function start()")
    assert "workflowTag(entry) + entry.action.tool" in app and '["workflow 단계", workflowStepText(entry)]' in app
    assert "workflowSummaryPart(run, events)" in app


@pytest.mark.parametrize(("events", "expected"), [
    ([{"type": "workflow", "created": True, "id": "w-20261119-001", "code": None, "steps": 9, "params": 2}],
     "workflow w-20261119-001 미확인 · 단계 9 · 파라미터 2"),
    ([{"type": "workflow", "created": False, "id": None, "code": "LOCATOR_NOT_UNIQUE", "steps": 0, "params": 0}],
     "workflow 못 만듦 · LOCATOR_NOT_UNIQUE"),
    ([{"type": "finish", "status": "verified", "steps": 3, "reason": "done", "tokens": 9}], None),
])
def test_the_run_summary_says_what_making_a_workflow_gave(tmp_path, events, expected):
    html = build_export(replay_run(tmp_path, events), REAL_STATIC, NOW)
    script = exported_script(html, "function workflowTag")

    [summary] = run_marks(script, {"started_by": "run.py"}, events, ["workflowSummaryPart(state.run, state.events)"])

    assert summary == expected


def test_a_replay_that_stopped_before_any_step_says_its_code(tmp_path):
    events = [{"type": "finish", "status": "stopped", "steps": 0, "reason": "READY_ALREADY_RUNNING", "tokens": 0}]
    html = build_export(replay_run(tmp_path, events), REAL_STATIC, NOW)
    record = payload(html)

    [summary] = run_marks(exported_script(html, "function workflowTag"), record["run"], record["events"],
                          ["workflowSummaryPart(state.run, state.events)"])

    assert summary == "workflow 멈춤 · READY_ALREADY_RUNNING"


# --- system profile: no registration view in an export, id and alias in the box tooltip --------

def test_the_exported_html_has_no_register_view(tmp_path):
    html = build_export(replay_run(tmp_path), REAL_STATIC, NOW)
    elements = {attrs.get("id"): attrs for _, attrs, _ in page_elements(page_skeleton(html)) if attrs.get("id")}
    app = exported_script(html, "function start()")
    profiles = exported_script(html, "function initProfiles")

    assert "hidden" in elements["show-profiles"] and "hidden" in elements["profile-view"]
    start = app[app.index("function start()"):]
    assert start.index("startExport(exported);\n    return;") < start.index("initProfiles")
    init = profiles[profiles.index("function initProfiles()"):profiles.index("\n}\n", profiles.index("function initProfiles()"))]
    assert init.index("state.exportMode") < init.index("button.hidden = false") < init.index("addEventListener")
    assert "innerHTML" not in profiles and "insertAdjacentHTML" not in profiles


def tooltip_rows(html, items):
    """Runs the exported app.js's tooltipRows in node for each item."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    app = exported_script(html, "function start()")
    begin = app.index("function elementKind(")
    kind = app[begin:app.index("\n}\n", begin) + 3]
    rows = app[app.index("function tooltipRows("):app.index("\nfunction showTooltip(")]
    calls = json.dumps(items, ensure_ascii=False)
    program = f"{kind}\n{rows}\nprocess.stdout.write(JSON.stringify({calls}.map(tooltipRows)));\n"
    out = subprocess.run([node, "-e", program], capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


def test_the_box_tooltip_shows_id_and_alias_and_an_older_record_is_unchanged(tmp_path):
    html = build_export(replay_run(tmp_path), REAL_STATIC, NOW)
    registered = {"kind": "id", "role": "image", "name": None, "native_id": {"kind": "automation_id", "value": "60"},
                  "in": {"role": "toolbar", "id": "12"}, "alias": "Execution Plan", "ref": None}
    named = {"kind": "control", "role": "button", "name": "조회", "ref": "@s1:e1",
             "native_id": {"kind": "automation_id", "value": "7001"}}
    older = {"kind": "control", "role": "button", "name": "조회", "description": "", "ref": "@s1:e1"}
    text = {"kind": "text", "role": "statictext", "name": "라인"}

    rows = tooltip_rows(html, [registered, named, older, text])

    assert rows[0] == [["종류", "ID만 있음 (ref·이름 없음)"], ["역할", "image"], ["ID", "60"],
                       ["등록 이름", "Execution Plan"]]
    assert rows[1][-1] == ["ID", "7001"]
    assert rows[2] == [["종류", "컨트롤"], ["역할", "button"], ["이름", "조회"], ["ref", "@s1:e1"]]
    assert rows[3] == [["종류", "텍스트 (ref 없음)"], ["역할", "statictext"], ["이름", "라인"]]
