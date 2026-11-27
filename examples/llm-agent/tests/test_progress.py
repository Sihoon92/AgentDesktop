import json

import pytest

from agent.progress import NULL_PROGRESS, ProgressLog, envelope_fields, result_fields


def clock_from(*seconds):
    values = iter(seconds)
    return lambda: next(values)


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_records_carry_parent_and_elapsed_ms(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl", clock=clock_from(10.0, 10.5, 11.0, 11.25, 12.0))
    outer = log.start("tool", name="click_part", step=3)
    inner = log.start("cli", name="mouse-click")
    log.end(inner, ok=True)
    log.end(outer, ok=True)
    assert read(tmp_path / "p.jsonl") == [
        {"id": 1, "phase": "start", "kind": "tool", "t_ms": 500, "name": "click_part", "step": 3},
        {"id": 2, "phase": "start", "kind": "cli", "t_ms": 1000, "parent": 1, "name": "mouse-click"},
        {"id": 2, "phase": "end", "kind": "cli", "t_ms": 1250, "ok": True},
        {"id": 1, "phase": "end", "kind": "tool", "t_ms": 2000, "ok": True}]


def test_none_fields_are_left_out(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    log.start("tool", name="observe", intent=None, read_only=None)
    assert "intent" not in read(tmp_path / "p.jsonl")[0] and "read_only" not in read(tmp_path / "p.jsonl")[0]


def test_tool_records_carry_events_seen(tmp_path):
    seen = iter([4, 6])
    log = ProgressLog(tmp_path / "p.jsonl", events_seen=lambda: next(seen))
    log.end(log.start("tool", name="observe"), ok=True)
    assert [record["events_seen"] for record in read(tmp_path / "p.jsonl")] == [4, 6]


def test_span_closes_on_exception(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    with pytest.raises(KeyError):
        with log.span("model", step=1):
            raise KeyError("x")
    last = read(tmp_path / "p.jsonl")[-1]
    assert (last["phase"], last["ok"], last["code"]) == ("end", False, "KeyError")
    assert log.innermost() is None


def test_span_finish_sets_end_fields(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    with log.span("model", step=1) as span:
        span.finish(ok=True, tokens=120)
    assert read(tmp_path / "p.jsonl")[-1]["tokens"] == 120


def test_span_without_finish_ends_ok(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    with log.span("ready"):
        pass
    assert read(tmp_path / "p.jsonl")[-1]["ok"] is True


def test_innermost_is_the_latest_open_start(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    log.start("tool", name="click")
    log.start("cli", name="click")
    assert log.innermost()["kind"] == "cli"


def test_ending_an_outer_item_closes_what_was_left_open(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    outer = log.start("tool", name="click")
    log.start("cli", name="click")
    log.end(outer, ok=True)
    ends = [record for record in read(tmp_path / "p.jsonl") if record["phase"] == "end"]
    assert [(r["id"], r["ok"], r.get("code")) for r in ends] == [(2, False, "UNCLOSED"), (1, True, None)]


def test_ending_an_unknown_id_is_ignored(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    log.end(99, ok=True)
    assert not (tmp_path / "p.jsonl").exists()


def test_close_open_closes_everything_inside_out(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    log.start("tool", name="click")
    log.start("cli", name="click")
    log.close_open("STOPPED")
    ends = [(r["id"], r["code"]) for r in read(tmp_path / "p.jsonl") if r["phase"] == "end"]
    assert ends == [(2, "STOPPED"), (1, "STOPPED")]


def test_a_failing_sink_is_dropped_and_the_run_goes_on(tmp_path):
    class Broken:
        calls = 0

        def on_record(self, record):
            Broken.calls += 1
            raise RuntimeError("console gone")

    log = ProgressLog(tmp_path / "p.jsonl")
    log.add_sink(Broken())
    log.end(log.start("cli", name="snapshot"), ok=True)
    assert Broken.calls == 1 and len(read(tmp_path / "p.jsonl")) == 2


def test_events_reach_sinks_that_take_them(tmp_path):
    seen = []

    class Listener:
        def on_event(self, event):
            seen.append(event)

    log = ProgressLog(tmp_path / "p.jsonl")
    log.add_sink(Listener())
    log.start("cli", name="snapshot")
    log.note_event({"type": "stage"})
    assert seen == [{"type": "stage"}]


def test_flush_sinks_calls_flush_where_present(tmp_path):
    flushed = []

    class Printer:
        def on_record(self, record):
            pass

        def flush(self):
            flushed.append(True)

    log = ProgressLog(tmp_path / "p.jsonl")
    log.add_sink(Printer())
    log.flush_sinks()
    assert flushed == [True]


def test_an_unwritable_file_does_not_raise(tmp_path):
    log = ProgressLog(tmp_path / "missing" / "p.jsonl")
    log.end(log.start("cli", name="snapshot"), ok=True)


def test_null_progress_writes_nothing():
    NULL_PROGRESS.end(NULL_PROGRESS.start("cli", name="snapshot"), ok=True)
    assert NULL_PROGRESS.innermost() is None


def test_result_fields_tell_refused_failed_mismatch_and_ok():
    assert result_fields({"ok": False, "refused": "x", "refused_code": "focus_failed"}) == {
        "ok": False, "code": "focus_failed"}
    assert result_fields({"ok": False, "refused": "x"}) == {"ok": False}
    assert result_fields({"ok": False, "code": "STALE_REF"}) == {"ok": False, "code": "STALE_REF"}
    assert result_fields({"ok": True, "expected_met": False}) == {"ok": True, "mismatch": True}
    assert result_fields({"ok": True, "verdict": "unconfirmed", "expected_met": None}) == {"ok": True}
    assert result_fields({"status": "verified"}) == {"ok": True}


def test_envelope_fields_take_the_error_code():
    assert envelope_fields({"ok": False, "error": {"code": "ACTION_FAILED"}}) == {"ok": False, "code": "ACTION_FAILED"}
    assert envelope_fields({"ok": True, "data": {}}) == {"ok": True}


def test_timed_model_wraps_respond_with_tokens(tmp_path):
    class Usage:
        total_tokens = 42

    class Reply:
        usage = Usage()

    class Model:
        name = "m"

        def respond(self, *args, **kwargs):
            return Reply()

    log = ProgressLog(tmp_path / "p.jsonl")
    timed = log.timed_model(Model(), "workflow")
    assert timed.respond("i", [], []).usage.total_tokens == 42 and timed.name == "m"
    first, last = read(tmp_path / "p.jsonl")
    assert (first["kind"], first["name"], last["tokens"]) == ("model", "workflow", 42)
