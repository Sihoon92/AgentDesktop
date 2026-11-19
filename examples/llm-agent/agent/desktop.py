"""Runs the agent-desktop binary and always returns one JSON envelope.

A client given `window_handles` (agent.handles.process_window_handles for a real run) drops from
every successful snapshot the native ids that are only window handles of the snapshot window's
process, so readiness, observation, the record, workflow targets, the profile screen and the check
never take one for an id. Without it the answers are the CLI's own.

A client given `read_shortcut` (agent.shortcut.resolve_shortcut for a real run) launches a `.lnk`
the way the shell would: each `launch` of a path ending in `.lnk`, in any case, is read again and
sent as `launch <target> --arg=<value>... [--cwd <folder>]`, the caller's other arguments after.
A shortcut that cannot be launched never reaches the CLI; the answer is a failure in the CLI's
shape, safe to retry, with one of agent.shortcut.CODES and its fixed message, never a path or an
argument. Without it a `.lnk` goes to the CLI as it was given."""

import json
import subprocess

from agent.handles import window_number, without_handle_ids
from agent.shortcut import MESSAGES, guarded, is_shortcut, launch_args


def start_session(binary, name, runner=subprocess.run):
    """Creates a traced CLI session and returns its id, or None when the CLI refused."""
    try:
        completed = runner(
            [binary, "session", "start", f"--name={name}"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    envelope = _parse(completed)
    return (envelope.get("data") or {}).get("session_id") if envelope.get("ok") else None


class DesktopClient:
    def __init__(self, binary, session, runner=subprocess.run, timeout_s=90, window_handles=None,
                 read_shortcut=None):
        self._binary = binary
        self._session = session
        self._runner = runner
        self._timeout_s = timeout_s
        self._window_handles = window_handles
        self._read_shortcut = read_shortcut

    def run(self, *args, headed=False):
        args = [str(arg) for arg in args]
        if self._read_shortcut is not None and len(args) > 1 and args[0] == "launch" and is_shortcut(args[1]):
            found, code = guarded(args[1], self._read_shortcut)
            if code is not None:
                return _failure(code, MESSAGES[code], "safe")
            args = ["launch", *launch_args(found), *args[2:]]
        argv = [self._binary, "--session", self._session]
        if headed:
            argv.append("--headed")
        argv += args
        try:
            completed = self._execute(argv)
        except subprocess.TimeoutExpired:
            return _failure("HARNESS_TIMEOUT", "The command timed out and may have landed", "unsafe")
        except OSError as error:
            return _failure("HARNESS_SPAWN", f"Could not run the binary: {error}", "safe")
        envelope = _parse(completed)
        if self._window_handles is not None and args and args[0] == "snapshot":
            return self._without_handle_ids(envelope, args)
        return envelope

    def _without_handle_ids(self, envelope, args):
        """`envelope` with the window-handle ids taken out of its tree; as it was when it failed,
        names no window of the form w-<decimal>, or the handles cannot be had."""
        data = envelope.get("data")
        if not envelope.get("ok") or not isinstance(data, dict) or not isinstance(data.get("tree"), dict):
            return envelope
        number = window_number(_window_id(args, data))
        if number is None:
            return envelope
        try:
            tree = without_handle_ids(data["tree"], self._window_handles(number) or ())
        except Exception:
            return envelope
        return {**envelope, "data": {**data, "tree": tree}}

    def close_process(self, pid):
        """Asks one process to close without force, so the app can save or prompt."""
        try:
            return self._execute(["taskkill", "/PID", str(pid)]).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _execute(self, argv):
        return self._runner(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=self._timeout_s,
        )


def _window_id(args, data):
    """The snapshot's window: its --window-id, else the window its answer names."""
    if "--window-id" in args:
        at = args.index("--window-id") + 1
        return args[at] if at < len(args) else None
    window = data.get("window")
    return window.get("id") if isinstance(window, dict) else None


def _parse(completed):
    try:
        envelope = json.loads(completed.stdout)
    except (TypeError, ValueError):
        envelope = None
    if isinstance(envelope, dict) and "ok" in envelope:
        return envelope
    detail = (completed.stdout or completed.stderr or "").strip()[:300]
    return _failure("HARNESS_OUTPUT", f"Unreadable CLI output: {detail}", "unsafe")


def _failure(code, message, retry):
    delivery = "not_delivered" if retry == "safe" else "unknown"
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "disposition": {"delivery": delivery, "retry": retry},
        },
    }
