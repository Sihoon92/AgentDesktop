"""Serves recorded runs to the browser viewer over a token-guarded loopback API.

Every request must name this server in its Host header, which defeats DNS
rebinding. Every /api/ request must also carry the per-launch token in
X-Agent-Token, which a page from another origin cannot read or forge.
Run ids, frame names and capture names are matched against fixed patterns before any file
is touched, so a request can never name a path outside the runs folder.
"""

import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from agent.frames import frame_origin
from agent.knowledge import KnowledgeError, KnowledgeStore, list_systems
from agent.summary import summary_of
from viewer import history
from viewer.history import BadQuery, RunIndex, is_run_id, parse_query
from viewer.run_manager import InvalidRun, RunBusy, RunStartFailed

STATIC_DIR = Path(__file__).resolve().parent / "static"
FRAME_NAME = re.compile(r"\d{4}\.png")
CAPTURE_NAME = re.compile(r"c\d+\.png")
STATIC_NAME = re.compile(r"[A-Za-z0-9_-]+\.(html|js|css)")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
}
MAX_BODY_BYTES = 64 * 1024
MAX_COMMENT_CHARS = 2000
MAX_ANSWER_ITEMS = 50
KNOWLEDGE_NAME = re.compile(r"[A-Za-z0-9가-힣._-]{1,64}")
ITEM_DECISIONS = ("approve", "edit", "reject")
PAGE_POLICY = "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, runs_dir, token, run_manager, history=None, knowledge_dir=None):
        self.runs_dir = Path(runs_dir)
        self.knowledge_dir = Path(knowledge_dir) if knowledge_dir is not None else None
        self.token = token
        self.run_manager = run_manager
        self.history = history or RunIndex(self.runs_dir)
        super().__init__(("127.0.0.1", port), ViewerHandler)


def is_frame_name(name):
    return FRAME_NAME.fullmatch(name) is not None


def is_capture_name(name):
    return CAPTURE_NAME.fullmatch(name) is not None


def is_static_name(name):
    return STATIC_NAME.fullmatch(name) is not None


def summary_lines(history, run_dir):
    """The run summary of one run folder, built from its records. A run whose run.json still says
    running but whose process is gone is summarized as interrupted, as the run list shows it."""
    shown = history.run(Path(run_dir).name)
    status = "interrupted" if shown is not None and shown.get("status") == "interrupted" else None
    return summary_of(run_dir, status=status)


def make_server(runs_dir, token, port=0, run_manager=None, history=None, knowledge_dir=None):
    """Returns a server bound to 127.0.0.1; call serve_forever() to start it.
    `history` replaces the RunIndex, so tests can fix the clock and the process lookup."""
    return ViewerServer(port, runs_dir, token, run_manager, history, knowledge_dir)


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


class BadAnswer(ValueError):
    """The answer body cannot be used; the message says why, in Korean, for the viewer."""


def pin_to_screen(events_path, pin):
    """Turns a pin on a recorded frame ({"observation", "x", "y"} in image pixels) into
    screen pixels, using that observation's window bounds and image size."""
    if not isinstance(pin, dict):
        raise BadAnswer("pin은 observation, x, y를 가진 객체여야 합니다")
    observation_id, x, y = pin.get("observation"), pin.get("x"), pin.get("y")
    if not all(type(value) is int for value in (observation_id, x, y)):
        raise BadAnswer("pin의 observation, x, y는 정수여야 합니다")
    events, _ = read_events(events_path, 0)
    observation = next((event for event in events
                        if event.get("type") == "observation" and event.get("id") == observation_id), None)
    if observation is None:
        raise BadAnswer("찍은 위치의 관찰을 찾을 수 없습니다. 화면을 새로 보고 다시 찍어 주세요")
    image = observation.get("image") or {}
    bounds = (observation.get("window") or {}).get("bounds") or {}
    width, height = image.get("width"), image.get("height")
    numbers = [width, height] + [bounds.get(key) for key in ("x", "y", "width", "height")]
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in numbers):
        raise BadAnswer("찍은 관찰에 캡처나 창 위치가 없어 위치를 바꿀 수 없습니다")
    if not (0 <= x < width and 0 <= y < height):
        raise BadAnswer("찍은 위치가 캡처 밖입니다. 다시 찍어 주세요")
    origin = frame_origin(bounds, width, height)
    return (round(origin[0] + x), round(origin[1] + y))


def answer_items(items):
    """Checks the registration decisions and returns them as a tuple of plain dicts."""
    if not isinstance(items, list) or len(items) > MAX_ANSWER_ITEMS:
        raise BadAnswer(f"items는 {MAX_ANSWER_ITEMS}개 이하의 목록이어야 합니다")
    checked = []
    for item in items:
        if not isinstance(item, dict) or type(item.get("n")) is not int or item.get("decision") not in ITEM_DECISIONS:
            raise BadAnswer("items의 항목마다 정수 n과 approve, edit, reject 중 하나인 decision이 필요합니다")
        decided = {"n": item["n"], "decision": item["decision"]}
        if item["decision"] == "edit":
            if not isinstance(item.get("entry"), dict):
                raise BadAnswer("고쳐서 승인하는 항목에는 고친 내용(entry)이 필요합니다")
            decided["entry"] = item["entry"]
        checked.append(decided)
    return tuple(checked)


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
        if segments == ["api", "knowledge"]:
            return self._send_systems()
        if len(segments) == 3 and segments[:2] == ["api", "knowledge"]:
            return self._send_knowledge(segments[2])
        if len(segments) == 3 and segments[:2] == ["api", "runs"]:
            return self._send_run(segments[2])
        if len(segments) == 3 and segments[:2] == ["api", "legacy"]:
            return self._send_legacy(segments[2])
        if len(segments) == 4 and segments[:2] == ["api", "runs"] and segments[3] == "events":
            return self._send_events(segments[2], query)
        if len(segments) == 4 and segments[:2] == ["api", "runs"] and segments[3] == "summary":
            return self._send_summary(segments[2])
        if len(segments) == 5 and segments[:2] == ["api", "runs"] and segments[3] == "frames":
            return self._send_frame(segments[2], segments[4])
        if len(segments) == 5 and segments[:2] == ["api", "runs"] and segments[3] == "captures":
            return self._send_capture(segments[2], segments[4])
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
        if len(segments) == 4 and segments[:2] == ["api", "runs"] and segments[3] == "answer":
            return self._answer(segments[2])
        if len(segments) == 4 and segments[:2] == ["api", "knowledge"]:
            return self._update_knowledge(segments[2], segments[3])
        if len(segments) == 5 and segments[:2] == ["api", "knowledge"] and segments[4] == "delete":
            return self._delete_knowledge(segments[2], segments[3])
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

    def _answer(self, run_id):
        manager = self.server.run_manager
        if manager is None:
            return self._send_json(503, {"error": "이 뷰어는 실행을 관리하지 않아 답할 수 없습니다"})
        if not is_run_id(run_id):
            return self._send_json(404, {"error": "없는 실행입니다"})
        payload, problem = self._read_json()
        if problem:
            return self._send_json(*problem)
        question_id = payload.get("question_id")
        text = payload.get("text")
        accepted = payload.get("accepted")
        if not isinstance(question_id, str) or not 0 < len(question_id) <= 64:
            return self._send_json(400, {"error": "question_id가 필요합니다"})
        if not isinstance(text, str) or len(text) > MAX_COMMENT_CHARS:
            return self._send_json(400, {"error": f"답은 {MAX_COMMENT_CHARS}자 이하의 글이어야 합니다"})
        if accepted is not None and type(accepted) is not bool:
            return self._send_json(400, {"error": "accepted는 true, false 또는 null이어야 합니다"})
        try:
            items = answer_items(payload.get("items", []))
            pin = payload.get("pin")
            screen_pin = None if pin is None else pin_to_screen(self.server.runs_dir / run_id / "events.jsonl", pin)
        except BadAnswer as error:
            return self._send_json(400, {"error": str(error)})
        if not manager.answer(run_id, question_id, text.strip(), accepted, screen_pin, items):
            return self._send_json(409, {"error": "답할 수 있는 질문이 아닙니다. 이미 답했거나 시간이 지났거나 실행이 끝났습니다"})
        self._send_json(200, {"accepted": True})

    def _knowledge_store(self, system):
        """Returns the store of a listed system, or None after sending 503 or 404. Only names
        that list_systems reports are served, so no request can reach outside the folder."""
        root = self.server.knowledge_dir
        if root is None:
            self._send_json(503, {"error": "이 뷰어는 지식 폴더를 모릅니다"})
            return None
        if KNOWLEDGE_NAME.fullmatch(system) is None or system not in list_systems(root):
            self._send_json(404, {"error": "없는 시스템입니다"})
            return None
        return KnowledgeStore(root, system)

    def _known_entry(self, store, entry_id):
        if KNOWLEDGE_NAME.fullmatch(entry_id) is None or store.get(entry_id) is None:
            self._send_json(404, {"error": "없는 지식 항목입니다"})
            return False
        return True

    def _send_systems(self):
        root = self.server.knowledge_dir
        if root is None:
            return self._send_json(503, {"error": "이 뷰어는 지식 폴더를 모릅니다"})
        self._send_json(200, {"systems": list_systems(root)})

    def _send_knowledge(self, system):
        store = self._knowledge_store(system)
        if store is None:
            return
        entries = store.entries(include_disabled=True)
        self._send_json(200, {"entries": entries, "warnings": store.warnings})

    def _update_knowledge(self, system, entry_id):
        store = self._knowledge_store(system)
        if store is None or not self._known_entry(store, entry_id):
            return
        payload, problem = self._read_json()
        if problem:
            return self._send_json(*problem)
        changes = payload.get("changes")
        if not isinstance(changes, dict):
            return self._send_json(400, {"error": "changes는 고칠 키와 값을 담은 객체여야 합니다"})
        try:
            entry = store.update(entry_id, changes)
        except KnowledgeError as error:
            return self._send_json(400, {"error": f"고칠 수 없습니다: {error}"})
        self._send_json(200, {"entry": entry})

    def _delete_knowledge(self, system, entry_id):
        store = self._knowledge_store(system)
        if store is None or not self._known_entry(store, entry_id):
            return
        try:
            deleted = store.delete(entry_id)
        except (KnowledgeError, OSError) as error:
            return self._send_json(409, {"error": f"지우지 못했습니다: {error}"})
        if not deleted:
            return self._send_json(404, {"error": "없는 지식 항목입니다"})
        self._send_json(200, {"deleted": True})

    def _status(self):
        manager = self.server.run_manager
        if manager is None:
            return {"can_start": False, "active": None, "default_exe": None, "can_answer": False,
                    "default_system": None, "default_stage_call_limit": None}
        return {"can_start": True, "active": manager.active(), "default_exe": manager.default_exe,
                "can_answer": manager.can_answer(), "default_system": manager.default_system,
                "default_stage_call_limit": getattr(manager, "default_stage_call_limit", None)}

    def _start_run(self):
        manager = self.server.run_manager
        if manager is None:
            return self._send_json(503, {"error": "이 뷰어는 실행을 시작할 수 없습니다. 기록 보기 전용입니다"})
        payload, problem = self._read_json()
        if problem:
            return self._send_json(*problem)
        try:
            run_id = manager.start(
                payload.get("goal"), payload.get("exe") or None, payload.get("level", 3), payload.get("approve", False),
                system=payload.get("system"), stage_call_limit=payload.get("stage_call_limit"),
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

    def _send_summary(self, run_id):
        run_dir = self._run_dir(run_id)
        if run_dir is None:
            return self._send_json(404, {"error": "없는 실행입니다"})
        try:
            lines = summary_lines(self.server.history, run_dir)
        except OSError:
            return self._send_json(500, {"error": "요약을 만들지 못했습니다"})
        self._send_json(200, {"lines": lines})

    def _send_frame(self, run_id, name):
        run_dir = self._run_dir(run_id)
        if run_dir is None or not is_frame_name(name):
            return self._send_json(404, {"error": "없는 프레임입니다"})
        self._send_file(run_dir / "frames" / name)

    def _send_capture(self, run_id, name):
        run_dir = self._run_dir(run_id)
        if run_dir is None or not is_capture_name(name):
            return self._send_json(404, {"error": "없는 캡처입니다"})
        self._send_file(run_dir / "captures" / name)

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
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
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
