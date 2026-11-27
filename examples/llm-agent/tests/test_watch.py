import threading

from agent.app_processes import AppTracker
from agent.watch import Observation, Watcher, WatchThread, size_class
from agent.win32_facts import ProcessRow, WindowRow

SCREEN = (1000, 1000)
APP, OTHER, OWN = 100, 200, 300


def tracker():
    tracked = AppTracker(r"C:\Apps\Real\Real.exe", (), r"C:\Windows")
    tracked.update([ProcessRow(APP, 1, r"C:\Apps\Real\Real.exe", "a"),
                    ProcessRow(OTHER, 1, r"C:\Tools\Other.exe", "b"),
                    ProcessRow(OWN, 1, r"C:\Python\python.exe", "c")], 0.0)
    return tracked


def win(hwnd, pid=APP, cls="TfrmSecretScreen", owner=0, owner_enabled=True, rect=(0, 0, 900, 900),
        visible=True, minimized=False):
    return WindowRow(hwnd, pid, cls, visible, minimized, owner, owner_enabled, rect)


def obs(t, windows, foreground=None, hung=()):
    return Observation(t, tuple(windows), foreground, frozenset(hung))


def watcher():
    return Watcher(tracker(), frozenset({OWN}), SCREEN)


def test_size_class_boundaries():
    assert size_class((0, 0, 300, 300), SCREEN) == "S"
    assert size_class((0, 0, 400, 400), SCREEN) == "M"
    assert size_class((0, 0, 800, 750), SCREEN) == "L"
    assert size_class((0, 0, 10, 10), (0, 0)) == "M"


def test_first_observation_is_the_baseline_without_shown_events():
    watching = watcher()
    events = watching.step(obs(0.0, [win(1)], foreground=1))
    assert [event.event for event in events] == ["foreground"]
    assert watching.first_app_window_t == 0.0 and watching.first_window_t == {"P1": 0.0}


def test_shown_and_hidden_events_carry_codes_not_names():
    watching = watcher()
    watching.step(obs(0.0, []))
    shown = watching.step(obs(1.0, [win(7, cls="TfrmSecretScreen", rect=(0, 0, 200, 200))]))
    assert [event.to_dict() for event in shown] == [
        {"t": 1.0, "event": "window_shown", "win": "W1", "proc": "P1", "rel": "APP", "family": "Delphi",
         "kind": "TOP", "modal": False, "size": "S"}]
    unknown = watching.step(obs(2.0, [win(7), win(8, pid=OTHER, cls="SECRET_NAME")]))
    assert unknown[0].family == "?" and unknown[0].rel == "OTHER" and unknown[0].proc is None
    hidden = watching.step(obs(3.0, [win(8, pid=OTHER, cls="SECRET_NAME")]))
    assert [event.to_dict() for event in hidden] == [{"t": 3.0, "event": "window_hidden", "win": "W1"}]
    assert "SECRET" not in repr([event.to_dict() for event in shown + unknown + hidden])


def test_minimized_window_counts_as_hidden():
    watching = watcher()
    watching.step(obs(0.0, [win(1)]))
    assert [event.event for event in watching.step(obs(1.0, [win(1, minimized=True)]))] == ["window_hidden"]


def test_own_windows_never_make_events_or_foreground():
    watching = watcher()
    watching.step(obs(0.0, [win(1)], foreground=1))
    events = watching.step(obs(1.0, [win(1), win(9, pid=OWN, cls="TkTopLevel")], foreground=9, hung={9}))
    assert events == []
    assert watching.last_foreground == 1


def test_foreground_within_two_seconds_records_previous_rel():
    watching = watcher()
    watching.step(obs(9.0, [win(1)]))
    watching.step(obs(10.0, [win(1)], foreground=1))
    watching.step(obs(11.0, [win(1), win(2, pid=OTHER, cls="#32770", rect=(0, 0, 100, 100))], foreground=1))
    events = watching.step(obs(12.0, [win(1), win(2, pid=OTHER, cls="#32770", rect=(0, 0, 100, 100))],
                               foreground=2))
    assert [event.to_dict() for event in events] == [
        {"t": 12.0, "event": "foreground", "win": "W2", "rel": "OTHER", "fg_within": "APP"}]


def test_alt_tab_to_an_old_window_has_no_fg_within():
    watching = watcher()
    watching.step(obs(0.0, [win(1), win(2, pid=OTHER)], foreground=1))
    events = watching.step(obs(10.0, [win(1), win(2, pid=OTHER)], foreground=2))
    assert events[0].fg_within is None
    watching.step(obs(11.0, [win(1), win(2, pid=OTHER), win(3, pid=OTHER)], foreground=2))
    late = watching.step(obs(13.5, [win(1), win(2, pid=OTHER), win(3, pid=OTHER)], foreground=3))
    assert late[0].fg_within is None


def test_hung_start_and_end_only_for_app_windows():
    watching = watcher()
    watching.step(obs(0.0, [win(1), win(2, pid=OTHER)]))
    started = watching.step(obs(1.0, [win(1), win(2, pid=OTHER)], hung={1, 2}))
    assert [(event.event, event.win) for event in started] == [("hung_start", "W1")]
    assert watching.step(obs(2.0, [win(1), win(2, pid=OTHER)], hung={1, 2})) == []
    ended = watching.step(obs(3.0, [win(1), win(2, pid=OTHER)]))
    assert [(event.event, event.win) for event in ended] == [("hung_end", "W1")]
    watching.step(obs(4.0, [win(1)], hung={1}))
    closed = watching.step(obs(5.0, []))
    assert [event.event for event in closed] == ["hung_end", "window_hidden"]


def test_shown_since_mark_lists_only_still_visible_new_windows():
    watching = watcher()
    watching.step(obs(0.0, [win(1)]))
    mark = watching.mark()
    watching.step(obs(1.0, [win(1), win(2), win(3)]))
    watching.step(obs(2.0, [win(1), win(3), win(4)]))
    assert watching.shown_since(mark) == [3, 4]
    later = watching.mark()
    assert watching.shown_since(later) == []
    assert watching.hwnd_of(watching.number(4)) == 4 and watching.hwnd_of("W99") is None


def test_modal_and_owned_dialog():
    watching = watcher()
    watching.step(obs(0.0, [win(1)]))
    events = watching.step(obs(1.0, [win(1), win(2, cls="#32770", owner=1, owner_enabled=False,
                                                 rect=(0, 0, 300, 200))]))
    assert events[0].kind == "DIALOG" and events[0].modal is True and events[0].owned == "W1"
    assert events[0].family == "Win32"
    owned = watching.step(obs(2.0, [win(1), win(2, cls="#32770", owner=1, owner_enabled=False),
                                    win(3, cls="TfrmPopup", owner=1)]))
    assert owned[0].kind == "OWNED" and owned[0].modal is False


def test_max_visible_top_counts_unowned_app_windows():
    watching = watcher()
    watching.step(obs(0.0, [win(1)]))
    watching.step(obs(1.0, [win(1), win(2), win(3, owner=1), win(4, pid=OTHER)]))
    watching.step(obs(2.0, [win(1)]))
    assert watching.max_visible_top == {"P1": 2}


def test_watch_thread_survives_an_error_and_stops():
    watching = watcher()
    calls = []
    seen = threading.Event()

    def observe():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("first round fails")
        if len(calls) >= 3:
            seen.set()
        return obs(float(len(calls)), [win(len(calls))])

    got = []
    thread = WatchThread(watching, observe, got.append, poll_s=0.01)
    thread.start()
    assert seen.wait(5)
    thread.stop()
    assert len(calls) >= 3 and got and all(isinstance(batch, list) and batch for batch in got)
