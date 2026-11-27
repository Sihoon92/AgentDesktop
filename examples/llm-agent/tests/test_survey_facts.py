import json

import pytest

from agent.survey_facts import (EVENTS_JSONL, FACTS_JSON, SESSION_JSON, CaptureFacts, CheckFact, Event,
                                FamilyCount, Labels, ProcessFact, SessionFacts, UiaCounts, WindowFact,
                                append_event, capture_codes, capture_dir, capture_id, load_session,
                                write_json)


def window(win="W1", **changes):
    values = dict(win=win, proc="P1", instance="1234:5678", rel="APP", family="Delphi", kind="MAIN",
                  owned=None, modal=False, hung=False, size="L", read="OK", read_s=1.2,
                  kinds={"named_ref": 3, "text": 2}, id_keys=("a" * 16, "b" * 16))
    values.update(changes)
    return WindowFact(**values)


def capture(cid="C01", windows=None, main: str | None = "W1", shot="OK"):
    return CaptureFacts(id=cid, t=3.5, shot=shot, main=main, windows=tuple(windows or [window()]),
                        new_since_last=1, uia=UiaCounts(10, False, {"Win32": 10}, {"Invoke": 2}),
                        uia_code=None, monitor="1920x1080@1.25", monitor_count=1, slow="DONE")


def session(captures=("C01",)):
    process = ProcessFact(proc="P1", rel="APP", target_exe=True, started_in_session=True, bits=32,
                          modules=FamilyCount({"Delphi": 2}, 1), classes=FamilyCount({"Delphi": 5}, 0),
                          mdi=True, max_visible_top=1, first_window_t=2.0)
    return SessionFacts(version=2, system="연습", started="2026-11-27T10:00:00", duration_s=65.0,
                        head="6dbba10", changed=0, hotkey="OK", app_seen="STARTED",
                        first_app_window_t=2.0, unknown_paths=1,
                        checks=(CheckFact("바이너리", "통과", "OK"),), processes=(process,),
                        captures=tuple(captures), saved_ids=("c" * 16,), saved_ids_process="abc")


def write_session(folder, facts, captures):
    write_json(folder / SESSION_JSON, facts.to_dict())
    for item in captures:
        write_json(capture_dir(folder, item.id) / FACTS_JSON, item.to_dict())


def test_session_round_trip(tmp_path):
    facts = session(("C01", "C02"))
    captures = (capture("C01"), capture("C02", windows=[window("W2", kind="DIALOG", rel="SYSTEM")], main="W2"))
    write_session(tmp_path, facts, captures)
    events = (Event(0.5, "process_started", proc="P1", rel="APP"),
              Event(1.0, "window_shown", win="W1", proc="P1", rel="APP", family="?", kind="TOP", modal=False, size="L"))
    for event in events:
        append_event(tmp_path / EVENTS_JSONL, event)

    loaded = load_session(tmp_path)

    assert loaded.folder == tmp_path
    assert loaded.session == facts
    assert loaded.captures == captures
    assert loaded.events == events


def test_files_are_plain_json_lists_and_utf8(tmp_path):
    write_session(tmp_path, session(), [capture()])
    text = (tmp_path / SESSION_JSON).read_text(encoding="utf-8")
    assert "연습" in text
    assert json.loads(text)["captures"] == ["C01"]
    assert not (tmp_path / SESSION_JSON).read_bytes().startswith(b"\xef\xbb\xbf")


def test_unknown_keys_are_ignored_and_missing_keys_default():
    loaded = WindowFact.from_dict({"win": "W3", "future": 1})
    assert loaded.win == "W3" and loaded.id_keys == () and loaded.family == "?"


def test_missing_capture_facts_reads_as_abandoned(tmp_path):
    write_session(tmp_path, session(("C01", "C02")), [capture("C01")])

    loaded = load_session(tmp_path)

    assert loaded.captures[1] == CaptureFacts(id="C02", t=0.0, shot="SHOT_FAILED", main=None, windows=(),
                                              new_since_last=0, uia=None, uia_code=None, monitor=None,
                                              monitor_count=None, slow="QUEUE_ABANDONED")


def test_torn_last_event_line_is_skipped(tmp_path):
    write_session(tmp_path, session(), [capture()])
    append_event(tmp_path / EVENTS_JSONL, Event(1.0, "foreground", win="W1"))
    with open(tmp_path / EVENTS_JSONL, "a", encoding="utf-8") as handle:
        handle.write('{"t": 2.0, "eve')
    assert load_session(tmp_path).events == (Event(1.0, "foreground", win="W1"),)


def test_missing_session_json_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_session(tmp_path)


def test_event_dict_drops_none():
    assert Event(1.0, "foreground", win="W1").to_dict() == {"t": 1.0, "event": "foreground", "win": "W1"}


def test_capture_id_and_dir(tmp_path):
    assert capture_id(1) == "C01" and capture_id(12) == "C12"
    assert capture_dir(tmp_path, "C07") == tmp_path / "capture-07"


def test_labels_round_trip():
    labels = Labels({"C01": "MAIN"}, {"H12": "SOME"})
    assert Labels.from_dict(labels.to_dict()) == labels


@pytest.mark.parametrize("shot, read, hung, state", [
    ("SHOT_BLANK", "OK", False, "BLANK"), ("OK", "GONE", True, "GONE"), ("OK", "OK", True, "HUNG"),
    ("OK", "UNRESPONSIVE", False, "HUNG"), ("OK", "TIMEOUT", False, "TIMEOUT"), ("OK", "TRUNCATED", False, "OK")])
def test_capture_codes_state_order(shot, read, hung, state):
    item = capture(windows=[window(kind="DIALOG", rel="SYSTEM", read=read, hung=hung)], shot=shot)
    assert capture_codes(item) == ("DIALOG", "SYSTEM", state)


def test_capture_without_main_window():
    assert capture_codes(capture(windows=[], main=None)) == ("TOP", "UNKNOWN", "GONE")
    assert capture_codes(capture(main="W9")) == ("TOP", "UNKNOWN", "GONE")
