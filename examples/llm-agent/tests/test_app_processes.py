import pytest

from agent.app_processes import AppTracker, classify
from agent.survey_facts import Event
from agent.win32_facts import ProcessRow

WINDOWS = r"C:\Windows"
TARGET = r"C:\Apps\Mes\Launcher.exe"


def row(pid, parent, image, started="s"):
    return ProcessRow(pid, parent, image, f"{started}{pid}")


EXPLORER = row(10, 1, r"C:\Windows\explorer.exe")


@pytest.mark.parametrize("image, parent_app, rel", [
    (r"C:\Windows\System32\WerFault.exe", True, "SYSTEM"), (r"C:\Apps\Mes\Real.exe", False, "APP"),
    (r"C:\Apps\Mes\sub\Helper.exe", True, "APP"), (r"C:\Program Files\Office\EXCEL.EXE", True, "CHILD_OTHER"),
    (r"C:\Other\x.exe", False, "OTHER"), (None, True, "UNKNOWN"), (r"C:\Apps\Mesh\x.exe", False, "OTHER")])
def test_classify(image, parent_app, rel):
    assert classify(image, parent_app, (r"C:\Apps\Mes",), r"C:\Windows") == rel


def test_launcher_child_in_the_same_folder_is_app_after_the_launcher_exits():
    tracker = AppTracker(TARGET, (), WINDOWS)
    assert tracker.update([EXPLORER], 0.0) == []
    launcher = row(20, 10, TARGET)
    real = row(21, 20, r"C:\Apps\Mes\Real.exe")
    events = tracker.update([EXPLORER, launcher, real], 1.0)
    assert events == [Event(1.0, "process_started", proc="P1", rel="APP"),
                      Event(1.0, "process_started", proc="P2", rel="APP")]
    events = tracker.update([EXPLORER, real], 2.0)
    assert events == [Event(2.0, "process_exited", proc="P1", rel="APP")]
    assert tracker.is_app(21) and tracker.proc(21) == "P2" and not tracker.is_app(20)
    assert tracker.is_target(20) is False and tracker.started_in_session(21)
    assert not tracker.already_running


def test_child_of_app_outside_the_folder_is_child_other_and_not_app():
    tracker = AppTracker(TARGET, (), WINDOWS)
    tracker.update([EXPLORER], 0.0)
    excel = row(31, 30, r"C:\Program Files\Office\EXCEL.EXE")
    report = row(30, 10, TARGET)
    werfault = row(32, 30, r"C:\Windows\System32\WerFault.exe")
    events = tracker.update([EXPLORER, excel, report, werfault], 1.0)
    assert tracker.rel(31) == "CHILD_OTHER" and not tracker.is_app(31) and tracker.proc(31)
    assert tracker.rel(32) == "SYSTEM" and tracker.proc(32) is None
    assert {event.rel for event in events} == {"APP", "CHILD_OTHER"}
    assert tracker.is_target(30)


def test_extra_exe_folder_counts_as_app():
    tracker = AppTracker(TARGET, (r"D:\Users\me\Copy\Real.exe",), WINDOWS)
    tracker.update([row(40, 10, r"D:\Users\me\Copy\Real.exe")], 0.0)
    assert tracker.is_app(40)
    assert r"D:\Users\me\Copy" in tracker.app_dirs


def test_running_at_first_update_is_already_running_without_event():
    tracker = AppTracker(TARGET, (), WINDOWS)
    assert tracker.update([EXPLORER, row(50, 10, TARGET)], 0.0) == []
    assert tracker.already_running and tracker.proc(50) == "P1" and not tracker.started_in_session(50)


def test_restarted_app_is_a_new_app_process():
    tracker = AppTracker(TARGET, (), WINDOWS)
    tracker.update([EXPLORER, row(60, 10, TARGET, "a")], 0.0)
    events = tracker.update([EXPLORER, row(60, 10, TARGET, "b")], 5.0)
    assert events == [Event(5.0, "process_exited", proc="P1", rel="APP"),
                      Event(5.0, "process_started", proc="P2", rel="APP")]
    assert tracker.proc(60) == "P2" and tracker.started_in_session(60)
    assert [item.proc for item in tracker.numbered()] == ["P1", "P2"]
    assert [item.alive for item in tracker.numbered()] == [False, True]


def test_pick_makes_that_folder_app():
    tracker = AppTracker("", (), WINDOWS)
    tracker.update([EXPLORER, row(70, 10, r"E:\Real\App.exe"), row(71, 10, r"E:\Real\Helper.exe")], 0.0)
    assert not tracker.is_app(70)
    tracker.pick(70)
    assert tracker.is_app(70) and tracker.is_app(71) and tracker.picked
    assert tracker.app_dirs == (r"E:\Real",)
    tracker.pick(999)
    assert tracker.app_dirs == (r"E:\Real",)


def test_unknown_paths_are_counted_once_per_process():
    tracker = AppTracker(TARGET, (), WINDOWS)
    tracker.update([row(0, 0, None), row(4, 0, None), row(80, 4, None)], 0.0)
    assert tracker.unknown_paths == 0
    tracker.update([row(81, 10, None)], 1.0)
    tracker.update([row(81, 10, None), row(82, 10, None)], 2.0)
    tracker.update([row(82, 10, None)], 3.0)
    assert tracker.unknown_paths == 2
    assert tracker.rel(82) == "UNKNOWN"


def test_unknown_pid_is_other():
    tracker = AppTracker(TARGET, (), WINDOWS)
    assert tracker.rel(12345) == "OTHER" and tracker.proc(12345) is None and not tracker.is_app(12345)
