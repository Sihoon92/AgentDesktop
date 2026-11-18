"""Bundles one recorded run and the viewer page into a single HTML file that opens without a server.

The page is viewer/static/index.html with style.css inlined as <style> and every deferred viewer
script (chat.js, app.js) inlined as a <script> at the end of the body, in the order the page names
them (they ran deferred from the head before; at the end the page is already parsed). The record goes in <script id="run-export" type="application/json">: run.json,
the events, every frame as a data URI, and, only when vision events name them, those captures as
data URIs under "captures" (a record without vision events has no "captures" key). Nothing in the file points back at the viewer
server: no static or API path, no token, no address, nothing from other runs.
"""

import base64
import json
import re
from datetime import timezone
from pathlib import Path

EXPORT_FORMAT = 1
STYLE_TAG = '<link rel="stylesheet" href="/static/style.css">'
SCRIPT_TAG = '<script src="/static/app.js" defer></script>'
VIEWER_SCRIPT = re.compile(r'<script src="/static/([A-Za-z0-9_-]+\.js)" defer></script>')
BODY_END = "</body>"
HEAD_END = "</head>"
FRAME_NAME = re.compile(r"\d{4}\.png")
CAPTURE_ID = re.compile(r"c\d+")
SERVER_REFERENCE = re.compile(r"""(src|href)\s*=\s*["']?/""", re.IGNORECASE)


def build_export(run_dir, static_dir, now):
    """Returns the export HTML for the run folder. ValueError for an old log, a missing run,
    an unreadable run.json, or an index.html without the tags this module replaces."""
    run_dir, static_dir = Path(run_dir), Path(static_dir)
    record = _record(run_dir, now)
    page = (static_dir / "index.html").read_text(encoding="utf-8")
    for tag in (STYLE_TAG, SCRIPT_TAG, HEAD_END, BODY_END):
        if page.count(tag) != 1:
            raise ValueError(f"index.html에 {tag}가 정확히 한 번 있어야 내보낼 수 있습니다")
    names = VIEWER_SCRIPT.findall(page)
    page = VIEWER_SCRIPT.sub("", page.replace(STYLE_TAG, ""))
    if SERVER_REFERENCE.search(page):
        raise ValueError("index.html에 서버 경로를 가리키는 src·href가 남아 있어 내보낼 수 없습니다")
    style = _raw_text((static_dir / "style.css").read_text(encoding="utf-8"), "style")
    scripts = [_raw_text(_static_text(static_dir, name), "script") for name in names]
    page = page.replace(HEAD_END, f"<style>\n{style}\n</style>\n{HEAD_END}")
    code = "".join(f"<script>\n{script}\n</script>\n" for script in scripts)
    tail = f'<script id="run-export" type="application/json">{_json_text(record)}</script>\n{code}{BODY_END}'
    return page.replace(BODY_END, tail)


def _static_text(static_dir, name):
    try:
        return (static_dir / name).read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"index.html이 가리키는 {name}을(를) 읽을 수 없어 내보낼 수 없습니다: {error}") from error


def write_export(run_dir, static_dir, out_path, now):
    """Writes the export to out_path and returns its size in bytes. Never overwrites: an
    existing out_path raises FileExistsError, and a refused export writes nothing."""
    data = build_export(run_dir, static_dir, now).encode("utf-8")
    with open(out_path, "xb") as file:
        file.write(data)
    return len(data)


def _record(run_dir, now):
    if run_dir.suffix == ".jsonl":
        raise ValueError("옛 로그는 스크린샷과 run.json이 없어 내보낼 수 없습니다")
    if not run_dir.is_dir():
        raise ValueError(f"실행 기록이 없습니다: {run_dir.name}")
    try:
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"run.json을 읽을 수 없어 내보낼 수 없습니다: {error}") from error
    if not isinstance(run, dict):
        raise ValueError("run.json이 객체가 아니어서 내보낼 수 없습니다")
    events = _events(run_dir / "events.jsonl")
    record = {
        "export": EXPORT_FORMAT,
        "exported_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": run,
        "events": events,
        "frames": _frames(run_dir / "frames"),
    }
    captures = _vision_captures(run_dir / "captures", events)
    if captures:
        record["captures"] = captures
    return record


def _events(path):
    """Every complete event line; a half-written last line of a live run is left out."""
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _frames(folder):
    if not folder.is_dir():
        return {}
    names = sorted(path.name for path in folder.iterdir() if FRAME_NAME.fullmatch(path.name) and path.is_file())
    return {name: "data:image/png;base64," + base64.b64encode((folder / name).read_bytes()).decode("ascii")
            for name in names}


def _vision_captures(folder, events):
    """The captures a vision event names, as data URIs keyed by file name; other captures stay out."""
    ids = {event.get("capture") for event in events if isinstance(event, dict) and event.get("type") == "vision"}
    names = sorted(f"{capture_id}.png" for capture_id in ids
                   if isinstance(capture_id, str) and CAPTURE_ID.fullmatch(capture_id))
    captures = {}
    for name in names:
        path = folder / name
        if path.is_file():
            captures[name] = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return captures


def _json_text(record):
    """JSON safe inside a script element: '</' and '<!--' cannot end or switch its raw text."""
    text = json.dumps(record, ensure_ascii=False)
    return text.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def _raw_text(text, tag):
    """Code safe inside <tag>: a closing tag or comment opener in it no longer ends the element."""
    text = re.sub(rf"</({tag})", r"<\\/\1", text, flags=re.IGNORECASE)
    return text.replace("<!--", "<\\!--")
