"""Serves recorded runs to the browser viewer over a token-guarded loopback API.

Every request must name this server in its Host header, which defeats DNS
rebinding. Every /api/ request must also carry the per-launch token in
X-Agent-Token, which a page from another origin cannot read or forge.
Run ids and frame names are matched against fixed patterns before any file
is touched, so a request can never name a path outside the runs folder.
"""

import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from viewer import history
from viewer.history import BadQuery, RunIndex, is_run_id, parse_query
from viewer.run_manager import InvalidRun, RunBusy, RunStartFailed

STATIC_DIR = Path(__file__).resolve().parent / "static"
FRAME_NAME = re.compile(r"\d{4}\.png")
STATIC_NAME = re.compile(r"[A-Za-z0-9_-]+\.(html|js|css)")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
}
MAX_BODY_BYTES = 64 * 1024
MAX_COMMENT_CHARS = 2000
PAGE_POLICY = "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, runs_dir, token, run_manager, history=None):
        self.runs_dir = Path(runs_dir)
        self.token = token
        self.run_manager = run_manager
        self.history = history or RunIndex(self.runs_dir)
        super().__init__(("127.0.0.1", port), ViewerHandler)


def is_frame_name(name):
    return FRAME_NAME.fullmatch(name) is not None


def is_static_name(name):
    return STATIC_NAME.fullmatch(name) is not None


def make_server(runs_dir, token, port=0, run_manager=None, history=None):
    """Returns a server bound to 127.0.0.1; call serve_forever() to start it.
    `history` replaces the RunIndex, so tests can fix the clock and the process lookup."""
    return ViewerServer(port, runs_dir, token, run_manager, history)


def read_events(events_path, since):
    """Returns complete event lines after the first `since` and the next cursor.

    A trailing line without a newline is still being written and is left for
    the next call.
    """
    try:
        data = events_path.read_bytes()
    except FileNotFoundError:
        return [], 0
    lines = data.split(b"\n")[:-1]
    events = []
    for line in lines[since:]:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events, max(len(lines), since)


class ViewerHandler(BaseHTTPRequestHandler):
    server: ViewerServer
    server_version = "AgentViewer"
    sys_version = ""

    def log_message(self, format, *args):
        """Stays silent because request lines carry the token in ?t=."""

    def do_GET(self):
        segments, query = self._parse()
        if not self._authorized(segments):
            return
        if segments in ([""], ["index.html"]):
            return self._send_static("index.html")
        if len(segments) == 2 and segments[0] == "static":
            return self._send_static(segments[1])
        if segments == ["favicon.ico"]:
            return self._send(204, "image/x-icon", b"")
        if segments == ["api", "status"]:
            return self._send_json(200, self._status())
        if segments == ["api", "runs"]:
            return self._send_run_list(query)
        if len(segments) == 3 and segments[:2] == ["api", "runs"]:
            return self._send_run(segments[2])
        if len(segments) == 3 and segments[:2] == ["api", "legacy"]:
            return self._send_legacy(segments[2])
        if len(segments) == 4 and segments[:2] == ["api", "runs"] and segments[3] == "events":
            return self._send_events(segments[2], query)
        if len(segments) == 5 and segments[:2] == ["api", "runs"] and segments[3] == "frames":
            return self._send_frame(segments[2], segments[4])
        self._send_json(404, {"error": "없는 경로입니다"})

    def do_POST(self):
        self.close_connection = True
        segments, _ = self._parse()
        if not self._authorized(segments):
            return
        if segments == ["api", "runs"]:
            return self._start_run()
        if len(segments) == 4 and segments[:2] == ["api", "runs"] and segments[3] == "decision":
            return self._decide(segments[2])
        if len(segments) == 4 and segments[:2] == ["api", "runs"] and segments[3] == "keep":
            return self._set_keep(segments[2])
        self._send_json(404, {"error": "없는 경로입니다"})

    def _set_keep(self, run_id):
        """Creates or removes runs/<id>/keep: the only write the viewer makes in the runs folder."""
        if not is_run_id(run_id):
            return self._send_json(404, {"error": "없는 실행입니다"})
        run_dir = self.server.runs_dir / run_id
        if not run_dir.is_dir():
            if (self.server.runs_dir / f"{run_id}.jsonl").is_file():
                return self._send_json(400, {"error": "옛 로그는 폴더가 없어 보관할 수 없습니다"})
            return self._send_json(404, {"error": "없는 실행입니다"})
        if os.path.islink(run_dir) or run_dir.is_junction():
            return self._send_json(400, {"error": "실행 폴더가 다른 위치를 가리켜 보관 표시를 바꿀 수 없습니다"})
        payload, problem = self._read_json()
        if problem:
            return self._send_json(*problem)
        keep = payload.get("keep")
        if type(keep) is not bool:
            return self._send_json(400, {"error": "keep은 true 또는 false여야 합니다"})
        mark = run_dir / history.KEEP_FILE
        try:
            if keep:
                with open(mark, "x", encoding="utf-8"):
                    pass
            else:
                mark.unlink(missing_ok=True)
        except FileExistsError:
            pass
        except OSError as error:
            return self._send_json(409, {"error": f"보관 표시를 바꾸지 못했습니다: {error.strerror or error}"})
        if mark.is_file() != keep:
            return self._send_json(409, {"error": "보관 표시 자리에 파일이 아닌 것이 있어 바꾸지 못했습니다"})
        self._send_json(200, {"keep": keep})

    def _decide(self, run_id):
        if not is_run_id(run_id):
            return self._send_json(404, {"error": "없는 실행입니다"})
        manager = self.server.run_manager
        if manager is None:
            return self._send_json(503, {"error": "이 뷰어는 실행을 관리하지 않습니다"})
        payload, problem = self._read_json()
        if problem:
            return self._send_json(*problem)
        proposal_id = payload.get("proposal_id")
        approved = payload.get("approved")
        comment = payload.get("comment", "")
        if not isinstance(proposal_id, str) or not 0 < len(proposal_id) <= 64:
            return self._send_json(400, {"error": "proposal_id가 필요합니다"})
        if type(approved) is not bool:
            return self._send_json(400, {"error": "approved는 true 또는 false여야 합니다"})
        if not isinstance(comment, str) or len(comment) > MAX_COMMENT_CHARS:
            return self._send_json(400, {"error": f"의견은 {MAX_COMMENT_CHARS}자 이하의 글이어야 합니다"})
        if not manager.decide(run_id, proposal_id, approved, comment.strip()):
            return self._send_json(409, {"error": "답할 수 있는 제안이 아닙니다. 이미 답했거나 시간이 지났거나 실행이 끝났습니다"})
        self._send_json(200, {"accepted": True})

    def _status(self):
        manager = self.server.run_manager
        if manager is None:
            return {"can_start": False, "active": None, "default_exe": None}
        return {"can_start": True, "active": manager.active(), "default_exe": manager.default_exe}

    def _start_run(self):
        manager = self.server.run_manager
        if manager is None:
            return self._send_json(503, {"error": "이 뷰어는 실행을 시작할 수 없습니다. 기록 보기 전용입니다"})
        payload, problem = self._read_json()
        if problem:
            return self._send_json(*problem)
        try:
            run_id = manager.start(
                payload.get("goal"), payload.get("exe") or None, payload.get("level", 3), payload.get("approve", False)
            )
        except InvalidRun as error:
            return self._send_json(400, {"error": str(error)})
        except RunBusy as error:
            return self._send_json(409, {"error": str(error)})
        except RunStartFailed as error:
            return self._send_json(503, {"error": str(error)})
        self._send_json(201, {"run_id": run_id})

    def _read_json(self):
        """Returns (object, None) or ({}, (status, error body))."""
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return {}, (415, {"error": "Content-Type은 application/json이어야 합니다"})
        length_text = self.headers.get("Content-Length") or ""
        if not length_text.isdigit():
            return {}, (411, {"error": "Content-Length가 필요합니다"})
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            return {}, (413, {"error": "요청 본문이 너무 큽니다"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}, (400, {"error": "요청 본문이 올바른 JSON이 아닙니다"})
        if not isinstance(payload, dict):
            return {}, (400, {"error": "요청 본문은 JSON 객체여야 합니다"})
        return payload, None

    def _parse(self):
        parts = urlsplit(self.path)
        segments = [unquote(segment) for segment in parts.path.split("/")[1:]]
        return segments, parse_qs(parts.query)

    def _authorized(self, segments):
        port = self.server.server_port
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._send_json(403, {"error": "허용되지 않은 Host입니다"})
            return False
        if segments[:1] == ["api"]:
            supplied = (self.headers.get("X-Agent-Token") or "").encode()
            if not hmac.compare_digest(supplied, self.server.token.encode()):
                self._send_json(403, {"error": "토큰이 없거나 틀렸습니다"})
                return False
        return True

    def _run_dir(self, run_id):
        if not is_run_id(run_id):
            return None
        path = self.server.runs_dir / run_id
        return path if path.is_dir() else None

    def _send_run_list(self, query):
        try:
            filters = parse_query(query)
        except BadQuery as error:
            return self._send_json(400, {"error": str(error)})
        runs, next_before = self.server.history.page(filters)
        self._send_json(200, {"runs": runs, "next_before": next_before})

    def _send_run(self, run_id):
        summary = self.server.history.run(run_id)
        if summary is None:
            return self._send_json(404, {"error": "없는 실행입니다"})
        self._send_json(200, {"run": summary})

    def _send_legacy(self, run_id):
        opened = self.server.history.legacy(run_id)
        if opened is None:
            return self._send_json(404, {"error": "없는 옛 로그입니다"})
        self._send_json(200, opened)

    def _send_events(self, run_id, query):
        run_dir = self._run_dir(run_id)
        if run_dir is None:
            return self._send_json(404, {"error": "없는 실행입니다"})
        since_text = query.get("since", ["0"])[0]
        if not since_text.isdigit():
            return self._send_json(400, {"error": "since는 0 이상의 정수여야 합니다"})
        events, cursor = read_events(run_dir / "events.jsonl", int(since_text))
        self._send_json(200, {"events": events, "next": cursor})

    def _send_frame(self, run_id, name):
        run_dir = self._run_dir(run_id)
        if run_dir is None or not is_frame_name(name):
            return self._send_json(404, {"error": "없는 프레임입니다"})
        self._send_file(run_dir / "frames" / name)

    def _send_static(self, name):
        if not is_static_name(name):
            return self._send_json(404, {"error": "없는 파일입니다"})
        self._send_file(STATIC_DIR / name)

    def _send_file(self, path):
        try:
            body = path.read_bytes()
        except OSError:
            return self._send_json(404, {"error": "없는 파일입니다"})
        self._send(200, CONTENT_TYPES.get(path.suffix, "application/octet-stream"), body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", PAGE_POLICY)
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)
