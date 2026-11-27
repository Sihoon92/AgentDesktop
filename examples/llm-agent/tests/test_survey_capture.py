import json
import threading

import pytest
from PIL import Image

from agent.app_processes import AppTracker
from agent.survey_capture import (CaptureWorker, FastCapture, SlowContext, fast_capture, is_blank, pick_targets,
                                  read_code, slow_capture, window_kind)
from agent.survey_facts import FACTS_JSON, capture_dir
from agent.watch import Observation, Watcher
from tests.capture_support import (APP, APP_EXE, BUTTON, DIALOG, FOREIGN, MAIN, MARKERS, OWN, FakeDesktop, FakeGrab,
                                  FakeUia, FakeWin32, window)


class Setup:
    def __init__(self, tmp_path, windows=(MAIN, DIALOG, FOREIGN)):
        self.folder = tmp_path
        self.win32 = FakeWin32(windows)
        self.desktop = FakeDesktop(windows)
        self.grab = FakeGrab()
        self.uia = FakeUia()
        self.tracker = AppTracker(APP_EXE, (), r"C:\Windows")
        self.tracker.update(self.win32.process_rows(), 0.0)
        self.own = frozenset({OWN})
        self.watcher = Watcher(self.tracker, self.own, (1920, 1080))
        self.watcher.step(self.observe())
        self.ctx = SlowContext(self.desktop, self.win32, self.tracker, self.watcher, self.grab, self.uia,
                               windows_dir=r"C:\Windows")

    def observe(self):
        rows = self.win32.top_windows()
        return Observation(1.0, rows, self.win32.foreground(), frozenset(h for h in self.win32.hung))

    def fast(self, cid="C01"):
        return fast_capture(cid, 2.5, self.folder, self.grab, self.watcher, self.observe, self.own)

    def capture(self, cid="C01"):
        return slow_capture(self.fast(cid), self.folder, self.ctx)


def test_pick_targets_order_dedup_and_limit():
    assert pick_targets(5, [7, 5, 8], [5, 9, 7]) == [5, 7, 8, 9]
    assert pick_targets(None, [1], [2]) == [1, 2]
    assert pick_targets(1, list(range(2, 20)), [], limit=8) == [1, 2, 3, 4, 5, 6, 7, 8]


@pytest.mark.parametrize("envelope, code", [
    ({"ok": True, "data": {"complete": True}}, "OK"), ({"ok": True, "data": {}}, "OK"),
    ({"ok": True, "data": {"complete": False}}, "TRUNCATED"),
    ({"ok": False, "error": {"code": "TIMEOUT"}}, "TIMEOUT"),
    ({"ok": False, "error": {"code": "HARNESS_TIMEOUT"}}, "TIMEOUT"),
    ({"ok": False, "error": {"code": "APP_UNRESPONSIVE"}}, "UNRESPONSIVE"),
    ({"ok": False, "error": {"code": "WINDOW_NOT_FOUND"}}, "GONE"),
    ({"ok": False, "error": {"code": "STALE_REF"}}, "GONE"),
    ({"ok": False, "error": {"code": "ELEMENT_NOT_FOUND"}}, "GONE"),
    ({"ok": False, "error": {"code": "INTERNAL"}}, "ERROR"), ({"ok": False}, "ERROR")])
def test_read_code(envelope, code):
    assert read_code(envelope) == code


def test_window_kind_main_is_the_largest_unowned_app_window():
    big, small = window(1, rect=(0, 0, 800, 600)), window(2, rect=(0, 0, 300, 200))
    rows = [big, small, DIALOG]
    assert window_kind(big, rows, True) == "MAIN"
    assert window_kind(small, rows, True) == "TOP"
    assert window_kind(big, rows, False) == "TOP"
    assert window_kind(DIALOG, rows, True) == "DIALOG"
    assert window_kind(window(3, owner=1, cls="TfrmX"), rows, True) == "OWNED"


def test_blank_picture_rule():
    assert is_blank(Image.new("RGB", (10, 10), (30, 30, 30)))
    noisy = Image.new("RGB", (10, 10), (30, 30, 30))
    noisy.putpixel((1, 1), (40, 30, 30))
    assert not is_blank(noisy)


def test_button_press_uses_last_foreground_when_own_window_is_in_front(tmp_path):
    setup = Setup(tmp_path, windows=(MAIN, DIALOG, FOREIGN, BUTTON))
    setup.win32.fg = 31
    fast = setup.fast()
    assert fast.foreground == 11 and fast.targets[0] == 11 and 31 not in fast.targets


def test_new_windows_since_the_last_capture_come_after_the_foreground(tmp_path):
    setup = Setup(tmp_path, windows=(MAIN,))
    setup.fast("C01")
    popup = window(40, pid=200, cls="#32770", rect=(0, 0, 100, 100))
    setup.win32.windows = [popup, MAIN]
    setup.watcher.step(setup.observe())
    second = setup.fast("C02")
    assert second.targets == (11, 40) and second.new_since_last == 1
    assert setup.fast("C03").new_since_last == 0


def test_blank_screen_is_shot_blank(tmp_path):
    setup = Setup(tmp_path)
    setup.grab.blank = True
    assert setup.fast().shot == "SHOT_BLANK"
    setup.grab.fail = True
    assert setup.fast("C02").shot == "SHOT_FAILED"


def test_slow_capture_writes_facts_trees_window_and_overlay(tmp_path):
    setup = Setup(tmp_path)
    facts = setup.capture()
    folder = capture_dir(tmp_path, "C01")
    for name in ("screen.png", "window.png", "overlay.png", FACTS_JSON, "trees/map.json"):
        assert (folder / name).is_file(), name
    main = facts.windows[0]
    assert facts.main == main.win and main.kind == "MAIN" and main.rel == "APP" and main.read == "OK"
    assert main.family == "Delphi" and main.kinds and main.kinds["named_ref"] == 1 and len(main.id_keys) == 1
    assert main.instance == "100:inst" and facts.monitor == "1920x1080@1.5" and facts.monitor_count == 2
    assert facts.uia is not None and facts.uia.patterns == {"Invoke": 2} and facts.slow == "DONE"
    dialog = facts.windows[1]
    assert dialog.kind == "DIALOG" and dialog.modal and dialog.owned == main.win
    mapping = json.loads((folder / "trees" / "map.json").read_text(encoding="utf-8"))
    assert mapping[main.win] == {"hwnd": 11, "pid": APP}
    assert (folder / "trees" / f"{main.win}.json").is_file()
    assert json.loads((folder / FACTS_JSON).read_text(encoding="utf-8")) == facts.to_dict()
    assert setup.grab.calls[-1]["bbox"] == (0, 0, 900, 700)
    assert setup.ctx.class_tally["P1"] == ["TfrmSecretScreen", "MDIClient", "Edit", "#32770"]
    assert setup.ctx.mdi == {"P1"}


def test_facts_hold_no_marker(tmp_path):
    setup = Setup(tmp_path)
    setup.capture()
    text = (capture_dir(tmp_path, "C01") / FACTS_JSON).read_text(encoding="utf-8")
    assert not [marker for marker in MARKERS if marker in text]
    assert "CompanySecret.dll" in setup.ctx.unknown_modules
    assert "kernel32.dll" not in setup.ctx.unknown_modules


def test_modules_count_families_and_keep_system_modules_out_of_unknown(tmp_path):
    setup = Setup(tmp_path)
    setup.capture()
    modules, bits = setup.ctx.module_cache["P1"]
    assert modules.families == {"IE": 1, "DevExpress": 1, "Delphi": 1} and modules.unknown == 1 and bits == 64
    setup.win32.modules = None
    setup.ctx.module_cache.clear()
    setup.capture("C02")
    assert setup.ctx.module_cache["P1"] == (None, 64)


def test_closed_window_is_gone_and_has_no_overlay(tmp_path):
    setup = Setup(tmp_path)
    setup.desktop.gone.add(11)
    facts = setup.capture()
    folder = capture_dir(tmp_path, "C01")
    assert facts.windows[0].read == "GONE" and not (folder / "overlay.png").exists()
    assert not (folder / "window.png").exists()
    assert not any(argv[0] == "snapshot" and "w-11" in argv for argv in setup.desktop.calls)


def test_slow_read_codes_for_timeout_and_unresponsive(tmp_path):
    setup = Setup(tmp_path)
    setup.win32.fg = 21
    setup.desktop.snapshot_errors = {21: "HARNESS_TIMEOUT", 11: "APP_UNRESPONSIVE"}
    setup.desktop.incomplete = {12}
    facts = setup.capture()
    assert [item.read for item in facts.windows] == ["TIMEOUT", "UNRESPONSIVE", "TRUNCATED"]
    assert facts.windows[0].kinds is None and facts.windows[1].kinds is None and facts.windows[2].kinds
    folder = capture_dir(tmp_path, "C01")
    assert (folder / "window.png").exists() and not (folder / "overlay.png").exists()


def test_uia_runs_once_per_app_process(tmp_path):
    setup = Setup(tmp_path)
    first, second = setup.capture("C01"), setup.capture("C02")
    assert len(setup.uia.calls) == 1 and first.uia is not None and second.uia is None
    setup.win32.fg = 21
    assert setup.capture("C03").uia is None and len(setup.uia.calls) == 1


def test_modules_read_once_per_process(tmp_path):
    setup = Setup(tmp_path)
    setup.capture("C01")
    setup.capture("C02")
    assert setup.win32.module_calls == [APP]


def test_only_read_commands_reach_the_cli(tmp_path):
    setup = Setup(tmp_path)
    setup.capture()
    assert {argv[0] for argv in setup.desktop.calls} <= {"list-windows", "snapshot"}
    snapshot = next(argv for argv in setup.desktop.calls if argv[0] == "snapshot")
    assert snapshot == ("snapshot", "--window-id", "w-11", "--compact", "--include-bounds", "--timeout-ms", "5000")


def fast_of(cid):
    return FastCapture(cid, 1.0, "OK", 11, (11,), 0, frozenset(), (MAIN,))


def test_full_queue_writes_queue_full_facts_at_once(tmp_path):
    setup = Setup(tmp_path)
    done = []
    worker = CaptureWorker(setup.ctx, tmp_path, done.append, queue_max=2)
    assert worker.submit(fast_of("C01")) and worker.submit(fast_of("C02"))
    assert worker.submit(fast_of("C03")) is False
    facts = json.loads((capture_dir(tmp_path, "C03") / FACTS_JSON).read_text(encoding="utf-8"))
    assert facts["slow"] == "QUEUE_FULL" and facts["shot"] == "OK" and [item.id for item in done] == ["C03"]


def test_drain_marks_leftovers_abandoned(tmp_path):
    setup = Setup(tmp_path)
    worker = CaptureWorker(setup.ctx, tmp_path, lambda facts: None)
    worker.submit(fast_of("C01"))
    worker.submit(fast_of("C02"))
    assert worker.drain(0.1) == ["C01", "C02"]
    facts = json.loads((capture_dir(tmp_path, "C02") / FACTS_JSON).read_text(encoding="utf-8"))
    assert facts["slow"] == "QUEUE_ABANDONED"


def test_worker_runs_captures_in_order_and_drains_empty(tmp_path):
    setup = Setup(tmp_path)
    done = []
    finished = threading.Event()

    def record(facts):
        done.append(facts.id)
        if len(done) == 2:
            finished.set()

    worker = CaptureWorker(setup.ctx, tmp_path, record)
    worker.start()
    worker.submit(setup.fast("C01"))
    worker.submit(setup.fast("C02"))
    assert finished.wait(10)
    assert worker.drain(5) == [] and done == ["C01", "C02"]


def test_worker_turns_a_crash_into_abandoned_facts(tmp_path):
    setup = Setup(tmp_path)
    setup.ctx.desktop = None
    done = []
    worker = CaptureWorker(setup.ctx, tmp_path, done.append)
    worker.start()
    worker.submit(fast_of("C01"))
    assert worker.drain(5) == []
    assert done[0].slow == "QUEUE_ABANDONED"


def test_hung_window_is_marked_from_the_fast_phase(tmp_path):
    setup = Setup(tmp_path)
    setup.win32.hung = {11}
    facts = setup.capture()
    assert facts.windows[0].hung is True and facts.windows[1].hung is False


def test_unreadable_window_list_reads_as_error(tmp_path):
    setup = Setup(tmp_path)
    setup.desktop.run = lambda *argv, headed=False: {"ok": False, "error": {"code": "HARNESS_SPAWN"}}
    facts = setup.capture()
    assert {item.read for item in facts.windows} == {"ERROR"} and facts.slow == "DONE"
