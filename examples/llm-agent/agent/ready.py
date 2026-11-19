"""Launching the target and waiting until one of its windows shows every ready signal.

The harness runs this before the model's first call when a run has ready signals. It reads
the open windows, launches the exe, then reads the candidate windows every READY_POLL_S until
one shows all the signals. A signal is a name ({"name", "role"?}: an element named exactly that,
apart from outer spaces) or a native id ({"id", "role", "in"?}: role, id and, when written, the
area it sits in); agent.identity.count is the rule. A candidate is a window of the process
launch returned, or any window that was not open before the launch, so a splash process that
opens the main screen in another process is followed. A window that was open before is a
candidate only through the process launch returned, and when launch returns a process that
already had a window, the system was already running and nothing is touched. A wait that is
cancelled (the person stopped the run) ends before its next read as "cancelled"; the app it
launched stays open.

There are two ways to read a candidate. "snapshot" reads its whole tree and looks for every
signal in it; that read is the ready window's observation. "find" asks the CLI for one signal
at a time and stops at the first one missing; when all are found it reads the whole tree once,
checks the signals that write an area on it, and that read is the observation. A find gives no
native id back, so the role it answers is what is checked of an id signal; the CLI compares the
id letter for letter. Its name is checked again with the harness's rule, as `--exact` also
ignores case and folds inner spaces. A window whose title is like a name signal is read with
snapshot that round, because `--first` may answer with the window root itself. A find that
times out is "not yet", counted apart from the failed reads.

Every read is timed with `clock` and counted: the outcome and `on_round` carry numbers and
codes only. Only the desktop client is used, so the run loop, the baseline and the environment
check share it. No signal is ever written into a reason: reasons come from READY_MESSAGES.
"""

from dataclasses import dataclass

from agent import identity

NO_WINDOW_S = 30
READY_POLL_S = 0.3
RUNNING_HINT_MAX_WINDOWS = 30
SNAPSHOT_TIMEOUT_MS = "8000"
FIND_TIMEOUT_MS = "3000"
SNAPSHOT_READ = "snapshot"
FIND_READ = "find"
FIND_TIMEOUT_CODE = "TIMEOUT"
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
    """`reads` counts every snapshot and find of the wait (not the window lists, nor the hint
    after a failed wait); `timeouts` the finds that timed out and `failed_reads` every other read
    that failed. `max_read_ms` is the longest single read and `candidates` the candidate windows
    of the last round."""
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
    reads: int = 0
    failed_reads: int = 0
    timeouts: int = 0
    max_read_ms: int = 0
    candidates: int = 0
    read: str = SNAPSHOT_READ


def signals(task):
    """The ready signals of `task` (or of anything with its fields): its `ready`, else one name
    signal of its `ready_text`, else none. Every place asks this whether a run waits."""
    ready = tuple(getattr(task, "ready", None) or ())
    if ready:
        return ready
    text = getattr(task, "ready_text", None)
    return ({"name": text},) if text else ()


def has_named_element(tree, text):
    """Whether an element below the window root is named exactly `text`, apart from leading and
    trailing whitespace. A part of a longer name does not count, and neither does the root's
    own name, which is the window title: a splash titled like the signal is not ready."""
    return identity.count(tree, {"name": text}) > 0


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


def candidates(rows, launched, before_keys):
    """The rows a wait reads: a window of the `launched` (pid, process_instance), or one whose
    key was not among `before_keys`, in list order."""
    return [row for row in rows if _process(row) == launched or _key(row) not in before_keys]


def wait_until_ready(desktop, exe, wanted, timeout_s, clock, sleep, no_window_s=NO_WINDOW_S, cancelled=None,
                     read=SNAPSHOT_READ, on_round=None):
    """Launches `exe` and waits until a candidate window shows every signal of `wanted` (a list of
    signal dicts, or one text as one name signal), reading the way `read` names (see the module
    docstring). Gives up after `timeout_s`, or after `no_window_s` when no new window showed at
    all. Each round is followed by READY_POLL_S of `sleep`. When `cancelled()` is true before the
    launch or before a round of reads, ends as "cancelled" without reading the hint. After each
    round, `on_round(info)` gets {"round", "list_ms", "candidates", "reads": [{"window",
    "signal", "found", "ms", "code"}]}: positions in that round from 1 and codes, never a text.
    Never raises for what the desktop answers: every end is a ReadyOutcome."""
    wanted = _as_signals(wanted)
    read = FIND_READ if read == FIND_READ else SNAPSHOT_READ
    start = clock()
    if cancelled is not None and cancelled():
        return ReadyOutcome("cancelled", read=read)
    before = _windows(desktop)
    before_keys = {_key(row) for row in before}
    before_processes = {_process(row) for row in before}
    counts = _Counts(clock)

    def end(status, **fields):
        return ReadyOutcome(status, waited_ms=round((clock() - start) * 1000), read=read, **counts.fields(), **fields)

    envelope = desktop.run("launch", exe)
    if not envelope.get("ok"):
        return end("launch_failed", running_hint=_running_hint(desktop, before, wanted),
                   launch_error=envelope.get("error"))
    data = envelope.get("data") or {}
    launched = (data.get("pid"), data.get("process_instance"))
    if launched in before_processes:
        return end("already_running")
    launch_window = data.get("window")
    seen_new = isinstance(launch_window, dict) and _key(launch_window) not in before_keys
    polls = 0
    while True:
        if cancelled is not None and cancelled():
            return end("cancelled", polls=polls)
        listed_at = clock()
        rows = _windows(desktop)
        list_ms = _ms(clock() - listed_at)
        polls += 1
        seen_new = seen_new or any(_key(row) not in before_keys for row in rows)
        rows = candidates(rows, launched, before_keys)
        counts.begin_round(len(rows))
        ready = None
        for position, row in enumerate(rows, start=1):
            counts.window = position
            found = _judge(desktop, row, wanted, read, counts)
            if found is not None:
                ready = (row, found)
                break
        if on_round is not None:
            on_round({"round": polls, "list_ms": list_ms, "candidates": len(rows), "reads": counts.round_reads})
        if ready is not None:
            row, found = ready
            return end("ready", pid=row.get("pid"), instance=row.get("process_instance"), window=row,
                       tree_envelope=found, polls=polls, switched=_process(row) != launched)
        waited = clock() - start
        if not seen_new and waited >= no_window_s:
            status = "no_new_window"
        elif waited >= timeout_s:
            status = "timeout"
        else:
            sleep(READY_POLL_S)
            continue
        return end(status, polls=polls, running_hint=_running_hint(desktop, before, wanted))


def measure(desktop, row, wanted, clock):
    """Both ways of reading `row` for `wanted`, timed: {"snapshot": {"ms", "ok", "found": [bool per
    signal]}, "find": {"ms", "ok", "timeouts", "found": [...]}}. The find side asks every signal
    (it does not stop at a missing one) and its ms is the sum of those finds; the one tree read a
    find wait makes when ready is left out, as it is paid once and not every round. `ok` is false
    when a read failed; a find that timed out is not a failure but a signal not found.

    A find side "found" is what a find wait would accept: find asks by role and id only, and the
    wait then checks the area of an id signal that names one in a tree read (_judge_by_find), so
    such a signal counts as found only when the snapshot read here shows it in that area too.
    When that snapshot failed it is not found, as the wait could not accept it either; a name
    signal and an id signal without an area are find's answer alone."""
    wanted = _as_signals(wanted)
    counts = _Counts(clock)
    envelope = _snapshot(desktop, row, counts)
    tree = (envelope.get("data") or {}).get("tree") if envelope.get("ok") else None
    in_tree = [tree is not None and identity.count(tree, signal) > 0 for signal in wanted]
    snapshot = {"ms": counts.total_ms, "ok": bool(envelope.get("ok")), "found": in_tree}
    counts = _Counts(clock)
    found = [_found_by_find(desktop, row, signal, counts) and (not _placed(signal) or shown)
             for signal, shown in zip(wanted, in_tree)]
    return {"snapshot": snapshot,
            "find": {"ms": counts.total_ms, "ok": counts.failed_reads == 0, "timeouts": counts.timeouts,
                     "found": found}}


def _placed(signal):
    """Whether `signal` is an id signal that names its area, which find does not check (the same
    test _judge_by_find makes before its tree read)."""
    return "id" in signal and "in" in signal


def recommended_read(measured):
    """The faster way when both read without failing and found every signal (snapshot when they
    take the same time), else None."""
    snapshot, find = measured.get("snapshot") or {}, measured.get("find") or {}
    for side in (snapshot, find):
        if not side.get("ok") or not side.get("found") or not all(side["found"]):
            return None
    return FIND_READ if find["ms"] < snapshot["ms"] else SNAPSHOT_READ


class _Counts:
    """Times and counts every read through one place, so max_read_ms and on_round's ms agree."""

    def __init__(self, clock):
        self.clock = clock
        self.reads = 0
        self.failed_reads = 0
        self.timeouts = 0
        self.max_read_ms = 0
        self.total_ms = 0
        self.candidates = 0
        self.window: int | None = None
        self.round_reads = []

    def begin_round(self, candidates):
        self.candidates = candidates
        self.round_reads = []

    def run(self, desktop, signal, *args):
        began = self.clock()
        envelope = desktop.run(*args)
        ms = _ms(self.clock() - began)
        self.reads += 1
        self.max_read_ms = max(self.max_read_ms, ms)
        self.total_ms += ms
        code = None
        if not envelope.get("ok"):
            code = (envelope.get("error") or {}).get("code") or "UNKNOWN"
            if args[0] == FIND_READ and code == FIND_TIMEOUT_CODE:
                self.timeouts += 1
            else:
                self.failed_reads += 1
        entry = {"window": self.window, "signal": signal, "found": False, "ms": ms, "code": code}
        self.round_reads.append(entry)
        return envelope, entry

    def fields(self):
        return {"reads": self.reads, "failed_reads": self.failed_reads, "timeouts": self.timeouts,
                "max_read_ms": self.max_read_ms, "candidates": self.candidates}


def _as_signals(wanted):
    if isinstance(wanted, str):
        return ({"name": wanted},)
    return tuple(wanted or ())


def _ms(seconds):
    return max(0, round(seconds * 1000))


def _windows(desktop):
    envelope = desktop.run("list-windows")
    if not envelope.get("ok"):
        return []
    return [row for row in envelope.get("data") or [] if isinstance(row, dict)]


def _key(row):
    return row.get("id"), row.get("pid"), row.get("process_instance")


def _process(row):
    return row.get("pid"), row.get("process_instance")


def _judge(desktop, row, wanted, read, counts):
    """The snapshot envelope of `row` when it shows every signal this round, else None."""
    if read == FIND_READ and not _titled_like_a_signal(row, wanted):
        return _judge_by_find(desktop, row, wanted, counts)
    return _judge_by_snapshot(desktop, row, wanted, counts)


def _snapshot(desktop, row, counts, signal=None):
    envelope, _ = _snapshot_read(desktop, row, counts, signal)
    return envelope


def _snapshot_read(desktop, row, counts, signal=None):
    """One snapshot with bounds, so the ready window is recorded as an observation without
    reading it again."""
    return counts.run(desktop, signal, "snapshot", "--window-id", row.get("id"), "--compact", "--timeout-ms",
                      SNAPSHOT_TIMEOUT_MS, "--include-bounds")


def _shows_all(envelope, wanted):
    if not envelope.get("ok"):
        return False
    tree = (envelope.get("data") or {}).get("tree")
    return all(identity.count(tree, signal) > 0 for signal in wanted)


def _judge_by_snapshot(desktop, row, wanted, counts):
    envelope, entry = _snapshot_read(desktop, row, counts)
    entry["found"] = _shows_all(envelope, wanted)
    return envelope if entry["found"] else None


def _judge_by_find(desktop, row, wanted, counts):
    for position, signal in enumerate(wanted, start=1):
        if not _found_by_find(desktop, row, signal, counts, position):
            return None
    envelope, entry = _snapshot_read(desktop, row, counts)
    placed = [signal for signal in wanted if "id" in signal and "in" in signal]
    entry["found"] = _shows_all(envelope, placed)
    return envelope if entry["found"] else None


def _found_by_find(desktop, row, signal, counts, position=None):
    if "id" in signal:
        args = ["--native-id", str(signal["id"]), "--role", str(signal.get("role"))]
    else:
        args = ["--name", str(signal.get("name"))]
        if signal.get("role") is not None:
            args += ["--role", str(signal["role"])]
        args.append("--exact")
    envelope, entry = counts.run(desktop, position, "find", "--window-id", row.get("id"), *args, "--first",
                                 "--timeout-ms", FIND_TIMEOUT_MS)
    match = (envelope.get("data") or {}).get("match") if envelope.get("ok") else None
    entry["found"] = _is_signal(match, signal)
    return entry["found"]


def _is_signal(match, signal):
    """Whether the element a find answered is the signal by the harness's own rule. The window
    root (no ancestors in its path) is never a signal, as in agent.identity.walk."""
    if not isinstance(match, dict):
        return False
    if match.get("role") == "window" and not match.get("path"):
        return False
    if signal.get("role") is not None and match.get("role") != signal["role"]:
        return False
    if "id" in signal:
        return True
    name = match.get("name")
    return isinstance(name, str) and name.strip() == str(signal.get("name") or "").strip()


def _folded(text):
    return " ".join(str(text or "").split()).casefold()


def _titled_like_a_signal(row, wanted):
    """Whether the window title is a name signal as loosely as `find --exact` matches."""
    title = _folded(row.get("title"))
    return bool(title) and any("id" not in signal and _folded(signal.get("name")) == title for signal in wanted)


def _running_hint(desktop, before, wanted):
    """Whether a window open before the launch shows every signal: the system may already be
    running under another process. Read only when the wait fails, always with snapshot, and not
    counted. Reads at most RUNNING_HINT_MAX_WINDOWS, in list order."""
    counts = _Counts(lambda: 0.0)
    return any(_shows_all(_snapshot(desktop, row, counts), wanted) for row in before[:RUNNING_HINT_MAX_WINDOWS])
