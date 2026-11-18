"""Finds the viewer that view.py left running, so a new one does not end up beside it.

view.py writes `.viewer.json` (pid, that process's start time, port, token) into the runs folder
when it opens and removes it when it closes. The runs list reads only folders and files named
like run ids, so the file never shows as a run. A pid counts as the old viewer only while the
process with that pid has the recorded start time; a reused pid is someone else and gets no
request. An old viewer with a run in progress is left alone and the new one moves to the next
port. The token in the file is never printed.
"""

import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from agent.process_identity import process_started_at

INSTANCE_FILE = ".viewer.json"
TAKEOVER_WAIT_S = 10
TAKEOVER_POLL_S = 0.25
PORT_TRIES = 20
MAX_PORT = 65535


def write_instance(runs_dir, port, token, pid=None, started_at=None):
    """Writes the marker for this viewer (or the given pid) and returns its path. The file is
    written beside itself and then swapped in, so a reader never sees half of it."""
    pid = os.getpid() if pid is None else pid
    started_at = process_started_at(pid) if started_at is None else started_at
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / INSTANCE_FILE
    partial = runs_dir / f"{INSTANCE_FILE}.{pid}.tmp"
    payload = {"pid": pid, "started_at": started_at, "port": port, "token": token}
    partial.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(partial, path)
    return path


def read_instance(runs_dir):
    """The marker as {"pid", "started_at", "port", "token"}, or None when it is missing,
    unreadable or not in that shape."""
    try:
        payload = json.loads((Path(runs_dir) / INSTANCE_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    pid, port, token, started_at = (payload.get(key) for key in ("pid", "port", "token", "started_at"))
    if type(pid) is not int or pid <= 0 or type(port) is not int or not 0 < port <= MAX_PORT:
        return None
    if not isinstance(token, str) or not token or not (started_at is None or isinstance(started_at, str)):
        return None
    return {"pid": pid, "started_at": started_at, "port": port, "token": token}


def clear_instance(runs_dir, token):
    """Removes the marker only when it is this viewer's (same token), so a viewer that closes
    late never removes the marker of the one that took over."""
    info = read_instance(runs_dir)
    if info is None or not hmac.compare_digest(info["token"].encode(), token.encode()):
        return
    try:
        (Path(runs_dir) / INSTANCE_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def instance_alive(info, started_at_of=process_started_at):
    """True only when the pid still names the process that wrote the marker: same start time.
    Without a recorded or a live start time (another OS, a gone process) it is not the viewer."""
    if not info or not info.get("started_at"):
        return False
    live = started_at_of(info["pid"])
    return live is not None and live == info["started_at"]


def _direct_open(request, timeout):
    """Opens without the proxy settings of the environment: 127.0.0.1 must never go to a proxy."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)


def request_shutdown(info, stop_run, timeout_s=5, opener=_direct_open):
    """Asks the viewer in `info` to shut down. Returns (HTTP status, body), or (None, {"error"})
    when it could not be reached."""
    port = info["port"]
    body = json.dumps({"stop_run": bool(stop_run)}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown", data=body, method="POST",
        headers={"Host": f"127.0.0.1:{port}", "X-Agent-Token": info["token"],
                 "Content-Type": "application/json"},
    )
    try:
        with opener(request, timeout=timeout_s) as response:
            return response.status, _json_body(response.read())
    except urllib.error.HTTPError as error:
        with error:
            return error.code, _json_body(error.read())
    except (urllib.error.URLError, OSError, ValueError) as error:
        return None, {"error": type(error).__name__}


def _json_body(raw):
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def open_viewer(runs_dir, port, make, *, read=read_instance, alive=instance_alive,
                shutdown=request_shutdown, wait=TAKEOVER_WAIT_S, sleep=time.sleep, clock=time.monotonic):
    """Opens the viewer with make(port) from the requested port on, trying PORT_TRIES ports.

    A port that is busy because of the idle old viewer of this runs folder is taken over: the
    old viewer is asked to shut down and the port is tried again for up to `wait` seconds. An
    old viewer with a run in progress, or anything else on the port, is left alone and the
    next port is tried. Returns (server, notice for the person or None); raises the last
    OSError when every port is taken. Port 0 lets the system pick and needs none of this."""
    if port == 0:
        return make(0), None
    skipped = []
    last_error = None
    for candidate in range(port, min(port + PORT_TRIES, MAX_PORT + 1)):
        try:
            server = make(candidate)
        except OSError as error:
            last_error = error
        else:
            return server, _notice(skipped, candidate, port)
        taken, old_viewer, reason = _take_over(runs_dir, candidate, make, read, alive, shutdown, wait, sleep, clock)
        if taken is not None:
            return taken, reason
        skipped.append((old_viewer, reason))
    raise last_error if last_error is not None else OSError(f"포트 {port}부터 열 수 있는 포트가 없습니다")


def _take_over(runs_dir, candidate, make, read, alive, shutdown, wait, sleep, clock):
    """(server, True, notice) when the old viewer gave up the port, else (None, whether the old
    viewer was on that port, why the port was skipped)."""
    info = read(runs_dir)
    if info is None or info.get("port") != candidate or not alive(info):
        return None, False, f"포트 {candidate}은(는) 다른 프로그램이 쓰고 있습니다"
    status, _ = shutdown(info, stop_run=False)
    if status == 409:
        return None, True, f"다른 뷰어에서 실행 중입니다(포트 {candidate}). 그 실행을 끊지 않으려고 그 뷰어는 그대로 둡니다"
    if status != 202:
        return None, True, f"포트 {candidate}의 이전 뷰어가 종료 요청에 답하지 않았습니다"
    deadline = clock() + wait
    while True:
        try:
            return make(candidate), True, f"이전 뷰어를 끄고 포트 {candidate}에서 엽니다"
        except OSError:
            if clock() >= deadline:
                return None, True, f"포트 {candidate}의 이전 뷰어가 {wait}초 안에 꺼지지 않았습니다"
            sleep(TAKEOVER_POLL_S)


def _notice(skipped, opened, requested):
    """What the old viewer did, or else why the first port was busy, then the port opened."""
    if opened == requested:
        return None
    reasons = [reason for old_viewer, reason in skipped if old_viewer] or [skipped[0][1]]
    return ". ".join(reasons + [f"포트 {opened}에서 엽니다"])
