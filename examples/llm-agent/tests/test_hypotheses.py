import json
import re
from pathlib import Path

import pytest

from agent import hypotheses
from agent.checks import process_mark
from agent.hypotheses import HYPOTHESES, Verdict, judge_all, main_windows
from agent.survey_facts import (CaptureFacts, CheckFact, Event, FamilyCount, Labels, LoadedSession, ProcessFact,
                                SessionFacts, UiaCounts, WindowFact, capture_dir)

NO_LABELS = Labels({}, {})


def kinds_of(named_ref=0, unnamed_ref=0, text=0, blank=0):
    return {"named_ref": named_ref, "unnamed_ref": unnamed_ref, "unnamed_ref_with_id": 0, "text": text,
            "blank": blank, "id_only": 0, "nodes": named_ref + unnamed_ref + text + blank, "depth": 3}


def app_window(win="W1", proc="P1", instance="i1", kind="MAIN", read="OK", kinds=None, keys=(), family="WinForms",
               read_s=1.0, rel="APP", hung=False):
    return WindowFact(win=win, proc=proc, instance=instance, rel=rel, family=family, kind=kind, read=read,
                      read_s=read_s, kinds=kinds if kinds is not None else kinds_of(named_ref=5, text=5),
                      id_keys=tuple(keys), hung=hung)


def capture(cid, *windows, uia=None, uia_code=None, monitor="1920x1080@1.25", monitor_count=1, main=None):
    return CaptureFacts(id=cid, t=1.0, shot="OK", main=main or (windows[0].win if windows else None),
                        windows=tuple(windows), uia=uia, uia_code=uia_code, monitor=monitor,
                        monitor_count=monitor_count, slow="DONE")


def process(proc="P1", rel="APP", classes=None, modules=None, target_exe=True, mdi=False, max_visible_top=1,
            first_window_t=1.0):
    return ProcessFact(proc=proc, rel=rel, target_exe=target_exe, started_in_session=True, bits=64,
                       modules=modules, classes=classes or FamilyCount({}, 0), mdi=mdi,
                       max_visible_top=max_visible_top, first_window_t=first_window_t)


def session_of(captures=(), processes=None, events=(), folder=Path("."), **session):
    processes = (process(),) if processes is None else processes
    facts = SessionFacts(version=2, system="SYS", started="2026-11-27T10:00:00", duration_s=600.0,
                         processes=tuple(processes), captures=tuple(c.id for c in captures),
                         **{"app_seen": "STARTED", "first_app_window_t": 1.0, **session})
    return LoadedSession(Path(folder), facts, tuple(captures), tuple(events))


def verdict(loaded, hid, labels=NO_LABELS):
    return dict((h.id, v) for h, v in judge_all(loaded, labels))[hid]


def read_one():
    return (capture("C01", app_window()),)


def test_registry_rules():
    assert [h.id for h in HYPOTHESES] == [f"H{n:02d}" for n in range(1, 19)]
    for h in HYPOTHESES:
        assert h.kind in ("auto", "manual")
        assert (h.judge is not None) == (h.kind == "auto")
        assert (h.how_to_answer is not None) == (h.kind == "manual")
        assert set(h.codes) <= set(h.next), h.id
        assert h.codes and h.title and h.why
        for text in (h.title, h.why, h.how_to_answer or "", h.closed_note or "", *h.next.values()):
            assert not re.search(r"\bMES\b", text)


def test_h01_delphi_wins_at_seventy_percent():
    loaded = session_of(read_one(), (process(classes=FamilyCount({"Delphi": 7, "WinForms": 3}, 0)),))
    assert verdict(loaded, "H01") == Verdict("DELPHI", "7/10", verdict(loaded, "H01").evidence)


def test_h01_below_seventy_is_mixed_and_nothing_is_unknown():
    mixed = session_of(read_one(), (process(classes=FamilyCount({"Delphi": 6, "WinForms": 4}, 0)),))
    assert verdict(mixed, "H01").code == "MIXED"
    nothing = session_of(read_one(), (process(classes=FamilyCount({}, 3)),))
    assert verdict(nothing, "H01").code == "UNKNOWN"


def test_h01_win32_only_counts_alone():
    with_other = session_of(read_one(), (process(classes=FamilyCount({"Win32": 40, "WinForms": 5}, 0)),))
    assert verdict(with_other, "H01").code == "WINFORMS"
    alone = session_of(read_one(), (process(classes=FamilyCount({"Win32": 40}, 0)),))
    assert verdict(alone, "H01").code == "WIN32"


def test_h01_sums_modules_and_uia_and_maps_web():
    uia = UiaCounts(10, False, {"WPF": 9, "Win32": 1}, {})
    loaded = session_of((capture("C01", app_window(), uia=uia),),
                        (process(classes=FamilyCount({"WPF": 1}, 0), modules=FamilyCount({"WPF": 1, "DevExpress": 4}, 0)),))
    assert verdict(loaded, "H01") == Verdict("WPF", "3/3", verdict(loaded, "H01").evidence)
    web = session_of(read_one(), (process(classes=FamilyCount({"WebView2": 8, "WinForms": 1}, 0)),))
    assert verdict(web, "H01").code == "WEB"


def test_h01_ignores_other_processes():
    loaded = session_of(read_one(), (process(classes=FamilyCount({"WinForms": 3}, 0)),
                                     process("P2", rel="CHILD_OTHER", classes=FamilyCount({"Java": 50}, 0))))
    assert verdict(loaded, "H01").code == "WINFORMS"


def test_h02_grid_family_native_none_and_unread():
    grid = session_of(read_one(), (process(modules=FamilyCount({"FarPoint": 2, "ActiveX": 1, "WinForms": 9}, 0)),))
    assert verdict(grid, "H02").code == "FARPOINT"
    activex = session_of(read_one(), (process(modules=FamilyCount({"ActiveX": 1}, 0)),))
    assert verdict(activex, "H02").code == "ACTIVEX_OTHER"
    native = session_of((capture("C01", app_window(), uia=UiaCounts(5, False, {}, {"Grid": 2})),),
                        (process(modules=FamilyCount({}, 0)),))
    assert verdict(native, "H02").code == "NATIVE"
    none_seen = session_of(read_one(), (process(modules=FamilyCount({"WinForms": 1}, 0)),))
    assert verdict(none_seen, "H02").code == "NONE_SEEN"
    unread = session_of(read_one(), (process(modules=None),))
    assert verdict(unread, "H02") == Verdict("SKIPPED", "MODULES_UNREAD", verdict(unread, "H02").evidence)


@pytest.mark.parametrize("counts, code", [
    (dict(named_ref=6, blank=4), "RICH"), (dict(named_ref=5, text=0, unnamed_ref=1, blank=4), "PARTIAL"),
    (dict(named_ref=2, blank=8), "PARTIAL"), (dict(named_ref=1, blank=9), "LOW"), (dict(), "LOW")])
def test_h03_boundaries(counts, code):
    loaded = session_of((capture("C01", app_window(kinds=kinds_of(**counts))),))
    assert verdict(loaded, "H03").code == code


def test_h03_uses_the_first_read_main_window():
    loaded = session_of((capture("C01", app_window(read="TIMEOUT", kinds=kinds_of(named_ref=9))),
                         capture("C02", app_window(kind="DIALOG", kinds=kinds_of(named_ref=9))),
                         capture("C03", app_window(kinds=kinds_of(named_ref=1, text=2, blank=97)))))
    assert verdict(loaded, "H03").short == "1/2/97"
    assert [c.id for c, _ in main_windows(loaded)] == ["C03"]


def test_h04_patterns():
    def h04(patterns):
        loaded = session_of((capture("C01", app_window()), capture("C02", app_window(), uia=UiaCounts(9, False, {}, patterns))))
        return verdict(loaded, "H04")
    assert h04({"Invoke": 3, "Toggle": 1}).code == "PATTERNS_OK"
    assert h04({"Invoke": 3}) == Verdict("INVOKE_ONLY", "I3/V0/T0", h04({"Invoke": 3}).evidence)
    assert h04({"Value": 3}).code == "NONE"
    failed = session_of((capture("C01", app_window(), uia_code="UIA_TIMEOUT"),))
    assert verdict(failed, "H04")[:2] == ("SKIPPED", "UIA_TIMEOUT")


def two_processes(keys1, keys2, total2=10, kind2="MAIN", family2="WinForms"):
    first = app_window("W1", "P1", "i1", keys=keys1, kinds=kinds_of(named_ref=5, text=5))
    second = app_window("W5", "P2", "i2", keys=keys2, kinds=kinds_of(named_ref=total2), kind=kind2, family=family2)
    return session_of((capture("C01", first), capture("C02", second)), (process(), process("P2")))


def test_h05_compares_two_processes_of_one_session():
    assert verdict(two_processes(("a", "b"), ("b", "a")), "H05")[:2] == ("STABLE", "1")
    assert verdict(two_processes(("a", "b"), ("a", "c")), "H05")[:2] == ("CHANGES", "1")
    assert verdict(two_processes((), ()), "H05").code == "NO_IDS"
    assert verdict(two_processes(("a",), ("a",), total2=12), "H05").code == "STABLE"


def test_h05_different_screens_stay_pending():
    assert verdict(two_processes(("a",), ("a",), total2=13), "H05").code == "PENDING"
    assert verdict(two_processes(("a",), ("a",), kind2="DIALOG"), "H05").code == "PENDING"
    assert verdict(two_processes(("a",), ("a",), family2="Delphi"), "H05").code == "PENDING"


def test_h05_same_process_is_not_compared():
    first = app_window("W1", keys=("a",))
    again = app_window("W1", keys=("z",))
    loaded = session_of((capture("C01", first), capture("C02", again)))
    assert verdict(loaded, "H05").code == "PENDING"


def write_map(folder, cid, win, pid):
    path = capture_dir(folder, cid) / "trees" / "map.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({win: {"hwnd": 100, "pid": pid}}), encoding="utf-8")


def test_h05_against_saved_check_ids(tmp_path):
    caps = (capture("C01", app_window("W1", instance="i1", keys=("a", "b"))),)
    write_map(tmp_path, "C01", "W1", 4321)
    other = session_of(caps, folder=tmp_path, saved_ids=("a", "b"), saved_ids_process="0" * 16)
    assert verdict(other, "H05")[:2] == ("STABLE", "1")
    changed = session_of(caps, folder=tmp_path, saved_ids=("a", "x"), saved_ids_process="0" * 16)
    assert verdict(changed, "H05").code == "CHANGES"
    same = session_of(caps, folder=tmp_path, saved_ids=("a", "x"), saved_ids_process=process_mark(4321, "i1"))
    assert verdict(same, "H05").code == "PENDING"


def test_h05_saved_ids_without_a_process_map_stay_pending(tmp_path):
    caps = (capture("C01", app_window("W1", keys=("a",))),)
    loaded = session_of(caps, folder=tmp_path, saved_ids=("a",), saved_ids_process="0" * 16)
    assert verdict(loaded, "H05").code == "PENDING"


@pytest.mark.parametrize("app_seen, target, code", [
    ("ALREADY_RUNNING", True, "ALREADY_RUNNING"), ("STARTED", True, "EXE"), ("STARTED", False, "LAUNCHER"),
    ("PICKED_BY_CAPTURE", True, "PICKED_BY_CAPTURE"), ("NOT_SEEN", False, "NOT_SEEN")])
def test_h06(app_seen, target, code):
    loaded = session_of(read_one(), (process(target_exe=target),), app_seen=app_seen)
    assert verdict(loaded, "H06").code == code


def test_h06_uses_the_process_whose_window_came_first():
    loaded = session_of(read_one(), (process("P1", target_exe=True, first_window_t=9.0),
                                     process("P2", target_exe=False, first_window_t=3.0)))
    assert verdict(loaded, "H06").code == "LAUNCHER"


def test_h07_and_h08():
    assert verdict(session_of(read_one(), (process(mdi=True, max_visible_top=3),)), "H07").code == "MDI"
    assert verdict(session_of(read_one(), (process(max_visible_top=2),)), "H07").code == "MULTI_TOP"
    assert verdict(session_of(read_one(), (process(),)), "H07").code == "SINGLE"
    web = session_of(read_one(), (process(classes=FamilyCount({"IE": 1}, 0), modules=FamilyCount({"CEF": 1}, 0)),))
    assert verdict(web, "H08").code == "CEF"
    assert verdict(session_of(read_one()), "H08").code == "NONE"


def test_h09_and_h11():
    loaded = session_of((capture("C01", app_window(read="TRUNCATED", read_s=2.44), monitor="2560x1440@1.5", monitor_count=2),
                         capture("C02", app_window("W2", read="TIMEOUT", kind="DIALOG"))))
    assert verdict(loaded, "H09")[:2] == ("2560x1440@1.5", "2")
    assert verdict(loaded, "H11")[:2] == ("TRUNCATED", "2.4s/T1")
    plain = session_of((capture("C01", app_window(read_s=0.5)),))
    assert verdict(plain, "H11")[:2] == ("COMPLETE", "0.5s")


@pytest.mark.parametrize("statuses, code, short", [
    ({"LLM 도구 호출": ("통과", ""), "LLM 그림 입력": ("통과", "")}, "TOOLS_IMAGE", ""),
    ({"LLM 도구 호출": ("통과", ""), "LLM 그림 입력": ("실패", "IMAGE_X")}, "TOOLS_ONLY", ""),
    ({"LLM 도구 호출": ("통과", ""), "LLM 그림 입력": ("실패", "X"), "비전 호출": ("통과", "")}, "TOOLS_IMAGE", "비전"),
    ({"LLM 도구 호출": ("실패", "LLM_TIMEOUT")}, "FAIL", "LLM_TIMEOUT"),
    ({"LLM 도구 호출": ("건너뜀", "")}, "SKIPPED", ""), ({}, "SKIPPED", "")])
def test_h10(statuses, code, short):
    checks = tuple(CheckFact(name, status, c) for name, (status, c) in statuses.items())
    assert verdict(session_of(read_one(), checks=checks), "H10")[:2] == (code, short)


def shown(t, win, rel, proc=None, owned=None, modal=None, kind="TOP"):
    return Event(t, "window_shown", win=win, proc=proc, rel=rel, owned=owned, modal=modal, kind=kind)


def test_h15_counts_owned_or_modal_app_windows_and_foreground_takers():
    events = (shown(1.0, "W1", "APP", "P1"), shown(2.0, "W2", "APP", "P1"),
              shown(5.0, "W3", "APP", "P1", owned="W1", modal=True, kind="DIALOG"),
              shown(6.0, "W4", "OTHER", "P9"), Event(6.5, "foreground", win="W4", rel="OTHER", fg_within="APP"),
              shown(7.0, "W5", "SYSTEM", "P8", owned="W1"),
              shown(8.0, "W6", "OTHER", "P7"),
              shown(9.0, "W7", "OTHER", "P6"), Event(15.0, "foreground", win="W7", rel="OTHER"),
              shown(90.0, "W8", "APP", "P1", owned="W1"))
    loaded = session_of(read_one(), events=events, first_app_window_t=1.0)
    assert verdict(loaded, "H15")[:2] == ("BOTH", "1/2/pre2")


def test_h15_single_sides_and_none():
    same = session_of(read_one(), events=(shown(1.0, "W1", "APP"), shown(3.0, "W2", "APP", owned="W1")))
    assert verdict(same, "H15").code == "SAME_PROC"
    other = session_of(read_one(), events=(shown(1.0, "W1", "APP"), shown(3.0, "W2", "SYSTEM", owned="W1")))
    assert verdict(other, "H15").code == "OTHER_PROC"
    nothing = session_of(read_one(), events=(shown(1.0, "W1", "APP"),))
    assert verdict(nothing, "H15")[:2] == ("NONE", "0/0/pre1")


def test_h15_not_seen_when_already_running():
    loaded = session_of(read_one(), events=(shown(0.0, "W1", "APP"), shown(3.0, "W2", "APP", owned="W1")),
                        app_seen="ALREADY_RUNNING")
    assert verdict(loaded, "H15").code == "NOT_SEEN"


def test_h16_outside_startup_system_vs_other():
    events = (shown(1.0, "W1", "APP"), shown(10.0, "W2", "SYSTEM", owned="W1"),
              shown(100.0, "W3", "SYSTEM", owned="W1"), shown(200.0, "W4", "CHILD_OTHER"),
              Event(201.0, "foreground", win="W4", rel="CHILD_OTHER", fg_within="APP"))
    assert verdict(session_of(read_one(), events=events), "H16")[:2] == ("BOTH", "2")
    only_system = session_of(read_one(), events=events[:3])
    assert verdict(only_system, "H16")[:2] == ("SYSTEM", "1")
    assert verdict(session_of(read_one(), events=events[:2]), "H16")[:2] == ("NONE", "0")


def test_h16_counts_the_whole_session_when_the_start_was_not_seen():
    events = (shown(0.0, "W1", "APP"), shown(10.0, "W2", "OTHER", owned="W1"))
    loaded = session_of(read_one(), events=events, app_seen="ALREADY_RUNNING")
    assert verdict(loaded, "H16")[:2] == ("OTHER", "1")


def test_h17_counts_hung_and_longest_seconds():
    events = (shown(1.0, "W1", "APP"), Event(10.0, "hung_start", win="W1"), Event(14.0, "hung_end", win="W1"),
              Event(100.0, "hung_start", win="W1"), Event(112.0, "hung_end", win="W1"))
    loaded = session_of((capture("C01", app_window()), capture("C02", app_window(read="UNRESPONSIVE"))), events=events)
    assert verdict(loaded, "H17")[:2] == ("SEEN", "3/12s")
    open_end = session_of(read_one(), events=(Event(590.0, "hung_start", win="W1"),))
    assert verdict(open_end, "H17")[:2] == ("SEEN", "1/10s")
    assert verdict(session_of(read_one()), "H17")[:2] == ("NONE", "")


def test_h18_dialog_kinds():
    def h18(*kinds):
        windows = [app_window(f"W{n}", kind=kind) for n, kind in enumerate(kinds, start=2)]
        return verdict(session_of((capture("C01", app_window(), *windows),)), "H18").code
    assert h18("DIALOG") == "WIN32_DIALOG"
    assert h18("OWNED") == "OWNED_FORM"
    assert h18("DIALOG", "OWNED") == "MIXED"
    assert h18() == "NONE_SEEN"
    from_events = session_of(read_one(), events=(shown(4.0, "W9", "APP", kind="DIALOG"), shown(5.0, "W8", "OTHER", kind="OWNED")))
    assert verdict(from_events, "H18").code == "WIN32_DIALOG"


APP_IDS = ("H01", "H02", "H03", "H04", "H05", "H07", "H08", "H09", "H11", "H15", "H17", "H18")


def test_no_app_skips_app_hypotheses_but_not_h06_h10():
    out = dict((h.id, v) for h, v in judge_all(session_of((), (), app_seen="NOT_SEEN"), NO_LABELS))
    for hid in APP_IDS:
        assert out[hid][:2] == ("SKIPPED", "NO_APP"), hid
    assert out["H06"].code == "NOT_SEEN" and out["H10"].code == "SKIPPED" and out["H16"].code == "NONE"
    assert out["H12"].code == "?"


def test_no_read_skips_app_hypotheses_except_watch_ones():
    unread = (capture("C01", app_window(read="TIMEOUT")),)
    events = (shown(1.0, "W1", "APP"), shown(3.0, "W2", "APP", owned="W1"))
    out = dict((h.id, v) for h, v in judge_all(session_of(unread, events=events), NO_LABELS))
    for hid in ("H01", "H02", "H03", "H04", "H05", "H07", "H08", "H09", "H11"):
        assert out[hid][:2] == ("SKIPPED", "NO_READ"), hid
    assert out["H15"].code == "SAME_PROC" and out["H17"].code == "SEEN"


def test_manual_answers_come_from_labels():
    out = dict((h.id, v) for h, v in judge_all(session_of(read_one()), Labels({}, {"H12": "SOME", "H14": "CELLS"})))
    assert out["H12"].code == "SOME" and out["H14"].code == "CELLS" and out["H13"].code == "?"


def test_a_judge_that_raises_becomes_error(monkeypatch):
    def boom(loaded):
        raise KeyError("x")
    broken = tuple(h if h.id != "H03" else hypotheses.replace(h, judge=boom) for h in HYPOTHESES)
    monkeypatch.setattr(hypotheses, "HYPOTHESES", broken)
    out = dict((h.id, v) for h, v in judge_all(session_of(read_one()), NO_LABELS))
    assert out["H03"] == Verdict("ERROR", "KeyError", "") and out["H01"].code != "ERROR"


def test_verdict_texts_hold_no_names():
    events = (shown(1.0, "W1", "APP"),)
    loaded = session_of(read_one(), (process(classes=FamilyCount({"WinForms": 3}, 2)),), events=events)
    for _, v in judge_all(loaded, NO_LABELS):
        assert "Tfrm" not in v.evidence and "\\" not in v.evidence and ".dll" not in v.evidence


def test_h11_detail_carries_the_cut_count():
    read = WindowFact(win="W1", proc="P1", instance="i1", rel="APP", family="WinForms", kind="MAIN", read="OK",
                      read_s=2.5, kinds=kinds_of(named_ref=5, text=5), depth=30, cut=0)
    assert verdict(session_of((capture("C01", read),)), "H11")[:2] == ("COMPLETE", "2.5s/c0")


def test_h11_detail_marks_a_shallow_fallback():
    read = WindowFact(win="W1", proc="P1", instance="i1", rel="APP", family="WinForms", kind="MAIN", read="OK",
                      read_s=3.1, kinds=kinds_of(named_ref=5, text=5), depth=None, cut=2,
                      shallow="deep_read_too_large")
    timed_out = app_window("W2", read="TIMEOUT", kind="DIALOG")
    assert verdict(session_of((capture("C01", read), capture("C02", timed_out))), "H11")[:2] == \
        ("COMPLETE", "3.1s/T1/c2/S")
