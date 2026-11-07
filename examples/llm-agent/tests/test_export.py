import base64
import json
import re
import shutil
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
    [app] = [text for attrs, text in page.scripts if not attrs]
    skeleton = html.replace(app, "")
    for text in SERVER_STRINGS:
        assert text not in skeleton, text
    assert not re.search(r"""(src|href)\s*=\s*["']?/""", skeleton)
    assert app.strip().endswith("start();")
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
