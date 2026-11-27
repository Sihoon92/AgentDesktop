"""Reading a window's tree deep enough to reach table cells, and falling back when the CLI refuses.

The CLI's snapshot stops at --max-depth 10 by default and leaves only children_count where it cut.
Cells in a company grid sat twelve levels down, so every reader that shows a window to the model or
an administrator asks for DEEP_DEPTH. The Windows adapter walks the whole window either way to prove
ref identity, so a deep read costs output and refmap size, not a second walk. When that size passes
the CLI's refmap limit, or the read times out, the window is read once more at the default depth
and the reason is kept as a code."""

from dataclasses import dataclass

DEEP_DEPTH = 30
DEPTH_RANGE = (10, 50)
"""The depths a setting may ask for instead of DEEP_DEPTH."""
TOO_LARGE = "deep_read_too_large"
TIMED_OUT = "deep_read_timeout"


@dataclass(frozen=True)
class WindowRead:
    """One read of a window: the envelope used, the --max-depth asked for it (None when it fell
    back to the CLI default), the fallback code if any, and how many nodes the depth still cut."""

    envelope: dict
    depth: int | None
    fallback: str | None
    cut: int


def read_window(desktop, window_id, timeout_ms, extra=(), include_bounds=True, depth=DEEP_DEPTH):
    """Reads `window_id` at `depth`; on a refmap-size or timeout refusal, once more without
    --max-depth. Any other failure comes back as it is."""
    head = ["snapshot", "--window-id", window_id, "--compact", "--timeout-ms", str(timeout_ms)]
    tail = [*extra, *(["--include-bounds"] if include_bounds else [])]
    envelope = desktop.run(*head, "--max-depth", str(depth), *tail)
    fallback = _fallback_code(envelope)
    if fallback is None:
        return WindowRead(envelope, depth, None, _cut_of(envelope))
    envelope = desktop.run(*head, *tail)
    return WindowRead(envelope, None, fallback, _cut_of(envelope))


def deep_read_counts(events):
    """(shallow reads, the most nodes one read still cut) from a run's deep_read events, or None
    when there are none. Only counts: the window and the fallback code stay in events.jsonl."""
    reads = [event for event in events if event.get("type") == "deep_read"]
    if not reads:
        return None
    cuts = [event.get("cut") for event in reads]
    return (sum(1 for event in reads if event.get("fallback")),
            max((cut for cut in cuts if type(cut) is int and cut >= 0), default=0))


def count_cut(tree):
    """The number of nodes in `tree` that carry children_count: children the depth did not read."""
    own = 1 if isinstance(tree, dict) and "children_count" in tree else 0
    children = tree.get("children") if isinstance(tree, dict) else None
    return own + sum(count_cut(child) for child in children or ())


def _fallback_code(envelope):
    if envelope.get("ok"):
        return None
    error = envelope.get("error") or {}
    code = error.get("code")
    if code == "TIMEOUT":
        return TIMED_OUT
    if code == "INTERNAL" and "RefMap exceeds" in str(error.get("message") or ""):
        return TOO_LARGE
    return None


def _cut_of(envelope):
    data = envelope.get("data") if envelope.get("ok") else None
    tree = data.get("tree") if isinstance(data, dict) else None
    return count_cut(tree) if isinstance(tree, dict) else 0
