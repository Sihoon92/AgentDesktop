"""Runs the agent-desktop binary and always returns one JSON envelope."""

import json
import subprocess


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
    def __init__(self, binary, session, runner=subprocess.run, timeout_s=90):
        self._binary = binary
        self._session = session
        self._runner = runner
        self._timeout_s = timeout_s

    def run(self, *args, headed=False):
        argv = [self._binary, "--session", self._session]
        if headed:
            argv.append("--headed")
        argv += [str(arg) for arg in args]
        try:
            completed = self._execute(argv)
        except subprocess.TimeoutExpired:
            return _failure("HARNESS_TIMEOUT", "The command timed out and may have landed", "unsafe")
        except OSError as error:
            return _failure("HARNESS_SPAWN", f"Could not run the binary: {error}", "safe")
        return _parse(completed)

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
