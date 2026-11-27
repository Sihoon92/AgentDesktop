"""Fake capture sessions built from the contract types, for the reading-side tests.

`make_session` returns a LoadedSession in memory; `write_session` writes the same facts as a
session folder with `write_json` and `append_event`, the way the collecting side does.
"""

from pathlib import Path

from agent.survey_facts import (EVENTS_JSONL, FACTS_JSON, SESSION_JSON, CaptureFacts, CheckFact, Event, FamilyCount,
                                LoadedSession, ProcessFact, SessionFacts, UiaCounts, WindowFact, append_event,
                                capture_dir, capture_id, write_json)

MARKERS = ("TfrmSecretScreen", "CompanySecret.dll", "SECRET_TITLE", "SECRET_NAME")

KINDS = {"named_ref": 30, "unnamed_ref": 4, "unnamed_ref_with_id": 0, "text": 20, "blank": 6, "id_only": 0,
         "nodes": 60, "depth": 7}


def window(win="W1", proc="P1", kind="MAIN", rel="APP", read="OK", family="WinForms", keys=("k1",)):
    return WindowFact(win=win, proc=proc, instance="i1", rel=rel, family=family, kind=kind, read=read,
                      read_s=1.2, kinds=dict(KINDS), id_keys=tuple(keys))


def capture(n, *windows, t=None, uia=None, shot="OK"):
    windows = windows or (window(),)
    return CaptureFacts(id=capture_id(n), t=float(t if t is not None else n * 10), shot=shot, main=windows[0].win,
                        windows=tuple(windows), uia=uia, monitor="1920x1080@1.25", monitor_count=1, slow="DONE")


def session_facts(captures, system="SYS", head="abc1234", changed=0, duration_s=1920.0, **extra):
    processes = (ProcessFact(proc="P1", rel="APP", target_exe=True, started_in_session=True, bits=64,
                             modules=FamilyCount({"WinForms": 3, "DevExpress": 2}, 5),
                             classes=FamilyCount({"WinForms": 12}, 2), max_visible_top=1, first_window_t=2.0),)
    checks = (CheckFact("LLM 도구 호출", "통과", ""), CheckFact("LLM 그림 입력", "실패", "IMAGE_NOT_READ"))
    values: dict = dict(version=2, system=system, started="2026-11-27T09:00:00", duration_s=duration_s, head=head,
                  changed=changed, app_seen="STARTED", first_app_window_t=2.0, checks=checks, processes=processes,
                  captures=tuple(c.id for c in captures))
    values.update(extra)
    return SessionFacts(**values)


def default_captures():
    return (capture(1, window(), uia=UiaCounts(80, False, {"WinForm": 78}, {"Invoke": 9, "Value": 4})),
            capture(2, window("W2", kind="DIALOG", rel="SYSTEM", proc=None, family="Win32"), window()))


def default_events():
    return (Event(2.0, "window_shown", win="W1", proc="P1", rel="APP", family="WinForms", kind="TOP", size="L"),
            Event(15.0, "window_shown", win="W2", proc="P2", rel="SYSTEM", family="Win32", kind="DIALOG",
                  owned="W1", modal=True, size="S"))


def make_session(captures=None, events=None, folder=Path("."), **extra):
    captures = default_captures() if captures is None else tuple(captures)
    events = default_events() if events is None else tuple(events)
    return LoadedSession(Path(folder), session_facts(captures, **extra), captures, events)


def write_session(folder, captures=None, events=None, **extra):
    """Writes a session folder like the collecting side does and returns its path."""
    folder = Path(folder)
    loaded = make_session(captures, events, folder, **extra)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / SESSION_JSON, loaded.session.to_dict())
    for c in loaded.captures:
        write_json(capture_dir(folder, c.id) / FACTS_JSON, c.to_dict())
    for e in loaded.events:
        append_event(folder / EVENTS_JSONL, e)
    return folder
