"""Launching the target and waiting until one of its windows shows the ready signal.

The harness runs this before the model's first call when a run has a ready signal. It reads
the open windows, launches the exe, then reads the candidate windows about once a second
until one holds an element whose name is exactly the signal. A candidate is a window of the
process launch returned, or any window that was not open before the launch, so a splash
process that opens the main screen in another process is followed. A window that was open
before is a candidate only through the process launch returned, and when launch returns a
process that already had a window, the system was already running and nothing is touched.
A wait that is cancelled (the person stopped the run) ends before its next read as "cancelled";
the app it launched stays open.

Only the desktop client is used, so the run loop, the baseline and the environment check
share it. The signal is never written into a reason: reasons come from READY_MESSAGES.
"""

from dataclasses import dataclass

NO_WINDOW_S = 30
READY_POLL_S = 1.0
RUNNING_HINT_MAX_WINDOWS = 30
SNAPSHOT_TIMEOUT_MS = "8000"
READY_STATUSES = ("ready", "already_running", "no_new_window", "timeout", "launch_failed", "cancelled")
READY_MESSAGES = {
    "already_running": "같은 시스템이 이미 떠 있습니다. 닫고 다시 실행해 주세요",
    "no_new_window_hint": "같은 시스템이 이미 떠 있는 것 같습니다. 닫고 다시 실행해 주세요",
    "no_new_window": "실행했지만 창이 뜨지 않았습니다",
    "timeout": "준비 신호가 정한 시간 안에 보이지 않았습니다",
    "launch_failed": "대상 앱을 띄우지 못했습니다",
    "cancelled": "사용자가 실행을 멈췄습니다",
}
"""`cancelled` is the same text as agent.loop.USER_STOP_REASON."""


@dataclass(frozen=True)
class ReadyOutcome:
    status: str
    pid: int | None = None
    instance: str | None = None
    window: dict | None = None
    tree_envelope: dict | None = None
    waited_ms: int = 0
    polls: int = 0
    switched: bool = False
    running_hint: bool = False
    launch_error: dict | None = None


def has_named_element(tree, text):
    """Whether an element below the window root is named exactly `text`, apart from leading and
    trailing whitespace. A part of a longer name does not count, and neither does the root's
    own name, which is the window title: a splash titled like the signal is not ready."""
    wanted = text.strip()
    if not isinstance(tree, dict):
        return False
    pending = list(tree.get("children") or []) if tree.get("role") == "window" else [tree]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        if isinstance(name, str) and name.strip() == wanted:
            return True
        pending.extend(node.get("children") or [])
    return False


def ready_message(outcome):
    """The reason a run stops for `outcome`, in the words of READY_MESSAGES. Empty when ready.
    A timeout or a failed launch with a signalled window from before adds the hint's words:
    a launcher of a system already running may end without a window."""
    if outcome.status == "no_new_window" and outcome.running_hint:
        return READY_MESSAGES["no_new_window_hint"]
    message = READY_MESSAGES.get(outcome.status, "")
    if outcome.status in ("timeout", "launch_failed") and outcome.running_hint:
        return f"{message}. {READY_MESSAGES['no_new_window_hint']}"
    return message


def wait_until_ready(desktop, exe, ready_text, timeout_s, clock, sleep, no_window_s=NO_WINDOW_S, cancelled=None):
    """Launches `exe` and waits until a candidate window shows `ready_text` (see the module
    docstring). Gives up after `timeout_s`, or after `no_window_s` when no new window showed at
    all. Each read is followed by READY_POLL_S of `sleep`. When `cancelled()` is true before the
    launch or before a round of reads, ends as "cancelled" without reading the hint. Never raises
    for what the desktop answers: every end is a ReadyOutcome."""
    start = clock()
    if cancelled is not None and cancelled():
        return ReadyOutcome("cancelled")
    before = _windows(desktop)
    before_keys = {_key(row) for row in before}
    before_processes = {_process(row) for row in before}

    def elapsed_ms():
        return round((clock() - start) * 1000)

    envelope = desktop.run("launch", exe)
    if not envelope.get("ok"):
        hint = _running_hint(desktop, before, ready_text)
        return ReadyOutcome("launch_failed", waited_ms=elapsed_ms(), running_hint=hint,
                            launch_error=envelope.get("error"))
    data = envelope.get("data") or {}
    launched = (data.get("pid"), data.get("process_instance"))
    if launched in before_processes:
        return ReadyOutcome("already_running", waited_ms=elapsed_ms())
    launch_window = data.get("window")
    seen_new = isinstance(launch_window, dict) and _key(launch_window) not in before_keys
    polls = 0
    while True:
        if cancelled is not None and cancelled():
            return ReadyOutcome("cancelled", waited_ms=elapsed_ms(), polls=polls)
        rows = _windows(desktop)
        polls += 1
        fresh = [row for row in rows if _key(row) not in before_keys]
        seen_new = seen_new or bool(fresh)
        for row in rows:
            if _process(row) != launched and _key(row) in before_keys:
                continue
            found = _signalled(desktop, row, ready_text)
            if found is not None:
                return ReadyOutcome("ready", pid=row.get("pid"), instance=row.get("process_instance"), window=row,
                                    tree_envelope=found, waited_ms=elapsed_ms(), polls=polls,
                                    switched=_process(row) != launched)
        waited = clock() - start
        if not seen_new and waited >= no_window_s:
            status = "no_new_window"
        elif waited >= timeout_s:
            status = "timeout"
        else:
            sleep(READY_POLL_S)
            continue
        hint = _running_hint(desktop, before, ready_text)
        return ReadyOutcome(status, waited_ms=elapsed_ms(), polls=polls, running_hint=hint)


def _windows(desktop):
    envelope = desktop.run("list-windows")
    if not envelope.get("ok"):
        return []
    return [row for row in envelope.get("data") or [] if isinstance(row, dict)]


def _key(row):
    return row.get("id"), row.get("pid"), row.get("process_instance")


def _process(row):
    return row.get("pid"), row.get("process_instance")


def _signalled(desktop, row, ready_text):
    """The snapshot envelope of `row` when it shows the signal, else None (a failed read too).
    It carries bounds, so the ready window is recorded as an observation without reading it again."""
    envelope = desktop.run("snapshot", "--window-id", row.get("id"), "--compact", "--timeout-ms", SNAPSHOT_TIMEOUT_MS,
                           "--include-bounds")
    if not envelope.get("ok"):
        return None
    tree = (envelope.get("data") or {}).get("tree")
    return envelope if has_named_element(tree, ready_text) else None


def _running_hint(desktop, before, ready_text):
    """Whether a window open before the launch shows the signal: the system may already be
    running under another process. Read only when the wait fails. Reads at most RUNNING_HINT_MAX_WINDOWS, in list order."""
    return any(_signalled(desktop, row, ready_text) is not None for row in before[:RUNNING_HINT_MAX_WINDOWS])
