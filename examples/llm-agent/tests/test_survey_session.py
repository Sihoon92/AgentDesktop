"""The capture session with fake Win32, CLI, screen, hotkeys and button: no window, key or model."""

import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import check
from agent import settings as settings_module
from agent.survey_facts import EVENTS_JSONL, FACTS_JSON, NAMES_JSON, SESSION_JSON, capture_dir
from agent.survey_session import Session, SessionDeps, run_session
from agent.win32_facts import ProcessRow
from tests.capture_support import (APP, APP_EXE, DIALOG, FOREIGN, MAIN, MARKERS, OTHER, FakeDesktop, FakeGrab, FakeUia,
                                   FakeWin32, window)

FIXTURES = Path(__file__).parent / "fixtures"
ALLOWED = {"session", "list-windows", "snapshot", "version"}


class FakeHotkeys:
    def __init__(self, env, bindings, on_signal):
        self.env, self.bindings, self.on_signal = env, bindings, on_signal
        self.stopped = False

    def start(self):
        if self.env.hotkey_signals:
            threading.Timer(0.05, lambda: [self.on_signal(name) for name in self.env.hotkey_signals]).start()
        return self.env.hotkey_failed

    def stop(self):
        self.stopped = True


class FakeButton:
    def __init__(self, env, on_capture, on_end):
        self.env, self.on_capture, self.on_end = env, on_capture, on_end
        self.closed = threading.Event()
        self.counts = []

    def start(self):
        pass

    def set_count(self, n):
        self.counts.append(n)

    def set_busy(self, busy):
        pass

    def run(self):
        if self.env.button_interrupts:
            raise KeyboardInterrupt
        self.on_capture()
        self.on_end()
        assert self.closed.wait(10)

    def close(self):
        self.closed.set()


class Env:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings_module, "DEFAULT_PATH", tmp_path / "settings.local.yaml")
        self.tmp = tmp_path
        self.debug = tmp_path / "debug" / "agent-desktop.exe"
        self.debug.parent.mkdir()
        self.debug.write_bytes(b"")
        self.win32 = FakeWin32()
        self.desktop = FakeDesktop()
        self.grab = FakeGrab()
        self.commands: list[str] = []
        self.finished: list[Path] = []
        self.now_value = datetime(2026, 11, 27, 9, 0, 0)
        self.ticks = 0.0
        self.hotkey_failed: tuple = ()
        self.hotkey_signals: list[str] = []
        self.button = True
        self.button_interrupts = False

    def runner(self, argv, **kwargs):
        self.commands.append(argv[1])
        return subprocess.CompletedProcess(argv, 0, (FIXTURES / "version.json").read_text(encoding="utf-8"), "")

    def clock(self):
        self.ticks += 0.5
        return self.ticks

    def finish(self, folder):
        self.finished.append(Path(folder))
        return ["[전달] fake"]

    def deps(self):
        checked = check.Deps(runner=self.runner, environ={}, check_dir=self.tmp / ".check",
                             binaries=(self.tmp / "release" / "agent-desktop.exe", self.debug), env_files=())
        return SessionDeps(check=checked, win32=self.win32, grab=self.grab, uia_runner=FakeUia(),
                           code_state=lambda folder: SimpleNamespace(head="abc1234", changed=("x",)),
                           finish=self.finish, captures_dir=self.tmp / "captures", now=lambda: self.now_value,
                           clock=self.clock, make_hotkeys=lambda bindings, on_signal: FakeHotkeys(self, bindings,
                                                                                                    on_signal),
                           make_button=(lambda on_capture, on_end: FakeButton(self, on_capture, on_end))
                           if self.button else None,
                           make_desktop=lambda binary: self.desktop)

    def args(self, **changes):
        values = dict(exe=APP_EXE, system="연습", settings=None, profile=None, profile_dir=None, skip=["llm"],
                      show_names=False)
        values.update(changes)
        return SimpleNamespace(**values)

    def session(self, **changes):
        session = Session(self.args(**changes), self.deps())
        session.start()
        return session

    def cli_commands(self):
        return set(self.commands) | {argv[0] for argv in self.desktop.calls}


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def events_of(session):
    path = session.folder / EVENTS_JSONL
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_session_writes_events_captures_and_session_json(env):
    session = env.session()
    session.worker.start()
    popup = window(41, pid=OTHER, cls="#32770", rect=(0, 0, 200, 100))
    env.win32.windows = [popup, MAIN, DIALOG, FOREIGN]
    session.tick()
    assert session.capture() == "C01" and session.capture() == "C02"
    lines = session.end()
    facts = read_json(session.folder / SESSION_JSON)
    assert facts["captures"] == ["C01", "C02"] and facts["app_seen"] == "ALREADY_RUNNING"
    assert facts["head"] == "abc1234" and facts["changed"] == 1 and facts["system"] == "연습" and facts["version"] == 3
    assert [item["name"] for item in facts["checks"]][:2] == ["바이너리", "설정"]
    process = facts["processes"][0]
    assert process["proc"] == "P1" and process["rel"] == "APP" and process["target_exe"] is True
    assert process["modules"]["families"]["DevExpress"] == 1 and process["classes"]["families"]["Delphi"] == 1
    assert process["mdi"] is True and process["bits"] == 64 and process["max_visible_top"] == 1
    first = read_json(capture_dir(session.folder, "C01") / FACTS_JSON)
    assert first["slow"] == "DONE" and first["windows"][0]["kind"] == "MAIN" and first["new_since_last"] == 1
    assert [event["event"] for event in events_of(session)][:2] == ["foreground", "window_shown"]
    assert env.finished == [session.folder] and lines[-1] == "[전달] fake"
    assert env.win32.beeps == 2


def test_console_and_name_free_files_hold_no_marker(env, capsys):
    env.button = False
    env.hotkey_signals = ["capture", "end"]
    env.win32.child_classes = lambda hwnd: ("CompanyGridWnd", "TfrmSecretScreen")
    assert run_session(env.args(show_names=True), env.deps()) == 0
    console = capsys.readouterr()
    folder = env.finished[0]
    texts = [console.out, console.err] + [(folder / name).read_text(encoding="utf-8") for name in
                                         (SESSION_JSON, EVENTS_JSONL)]
    texts += [path.read_text(encoding="utf-8") for path in folder.glob("capture-*/facts.json")]
    for text in texts:
        assert not [marker for marker in MARKERS if marker in text]
    assert APP_EXE not in console.out and "C:\\" not in console.out


def test_show_names_writes_names_json_only_then(env):
    env.win32.child_classes = lambda hwnd: ("CompanyGridWnd",)
    plain = env.session()
    plain.capture()
    plain.worker.start()
    plain.end()
    assert not (plain.folder / NAMES_JSON).exists()
    named = env.session(show_names=True)
    named.worker.start()
    named.capture()
    named.end()
    names = read_json(named.folder / NAMES_JSON)
    assert names == {"classes": ["CompanyGridWnd"], "modules": ["CompanySecret.dll"]}


def test_hung_app_keeps_fast_captures_coming(env):
    session = env.session()
    env.desktop.snapshot_errors = {hwnd: "HARNESS_TIMEOUT" for hwnd in (11, 12, 21)}
    env.win32.hung = {11}
    ids = [session.capture() for _ in range(7)]
    assert ids == [f"C0{n}" for n in range(1, 8)]
    for cid in ids:
        assert (capture_dir(session.folder, cid) / "screen.png").is_file()
    for cid in ("C06", "C07"):
        assert read_json(capture_dir(session.folder, cid) / FACTS_JSON)["slow"] == "QUEUE_FULL"
    assert {"event": "hung_start", "win": "W1"}.items() <= next(
        event for event in events_of(session) if event["event"] == "hung_start").items()
    session.end()
    assert read_json(capture_dir(session.folder, "C01") / FACTS_JSON)["slow"] == "QUEUE_ABANDONED"
    assert read_json(session.folder / SESSION_JSON)["captures"] == ids


def test_restart_mid_session_gives_two_app_processes(env):
    session = env.session()
    env.win32.processes = [ProcessRow(APP, 1, APP_EXE, "restarted")]
    session.tick()
    session.worker.start()
    session.end()
    facts = read_json(session.folder / SESSION_JSON)
    assert [(item["proc"], item["started_in_session"]) for item in facts["processes"]] == [("P1", False), ("P2", True)]
    kinds = [(event["event"], event.get("proc")) for event in events_of(session) if "process" in event["event"]]
    assert kinds == [("process_exited", "P1"), ("process_started", "P2")]


def test_no_app_then_first_capture_picks_foreground(env):
    env.win32.processes = [ProcessRow(APP, 1, r"E:\Elsewhere\Real.exe", "a"),
                           ProcessRow(OTHER, 1, r"C:\Tools\Other.exe", "b")]
    session = env.session()
    session.worker.start()
    session.capture()
    session.end()
    facts = read_json(session.folder / SESSION_JSON)
    assert facts["app_seen"] == "PICKED_BY_CAPTURE"
    assert [item["rel"] for item in facts["processes"]] == ["APP"]
    assert read_json(capture_dir(session.folder, "C01") / FACTS_JSON)["windows"][0]["rel"] == "APP"


def test_system_window_is_never_picked(env):
    env.win32.processes = [ProcessRow(APP, 1, r"C:\Windows\System32\WerFault.exe", "a")]
    session = env.session()
    session.capture()
    session.end()
    assert read_json(session.folder / SESSION_JSON)["app_seen"] == "NOT_SEEN"


def test_hotkey_taken_continues_with_button(env, capsys):
    env.hotkey_failed = ("capture",)
    assert run_session(env.args(), env.deps()) == 0
    console = capsys.readouterr().out
    assert "HOTKEY_TAKEN capture" in console and "버튼: 왼쪽 클릭 캡처" in console
    facts = read_json(env.finished[0] / SESSION_JSON)
    assert facts["hotkey"] == "HOTKEY_TAKEN" and facts["captures"] == ["C01"]


def test_no_button_continues_with_hotkey(env, capsys):
    env.button = False
    env.hotkey_signals = ["capture", "end"]
    assert run_session(env.args(), env.deps()) == 0
    console = capsys.readouterr().out
    assert "버튼 없음" in console and "캡처: ctrl+alt+f9" in console
    facts = read_json(env.finished[0] / SESSION_JSON)
    assert facts["hotkey"] == "OK" and facts["captures"] == ["C01"]


def test_nothing_to_capture_with_says_only_ctrl_c(env, capsys):
    env.button = False
    deps = env.deps()

    def hotkeys(bindings, on_signal):
        return SimpleNamespace(start=lambda: (threading.Timer(0.05, on_signal, ("end",)).start(), ("capture", "end"))[1],
                               stop=lambda: None)

    deps.make_hotkeys = hotkeys
    assert run_session(env.args(), deps) == 0
    console = capsys.readouterr().out
    assert "HOTKEY_TAKEN capture" in console and "HOTKEY_TAKEN end" in console
    assert "Ctrl+C로만 끝낼 수 있습니다" in console


def test_interrupt_before_any_capture_still_finishes(env):
    env.button_interrupts = True
    assert run_session(env.args(), env.deps()) == 0
    folder = env.finished[0]
    assert read_json(folder / SESSION_JSON)["captures"] == []


def test_session_folder_name_is_sanitized_and_unique(env):
    first = env.session(system="가 나/다")
    second = env.session(system="가 나/다")
    assert first.folder.name == "20261127-090000-가 나_다"
    assert second.folder.name == "20261127-090000-가 나_다-2"


def test_cli_session_that_cannot_start_is_said_on_the_console_and_kept(env):
    """Seen live: a state folder the CLI refuses made every read fail with no word on the
    console, and the block only showed SKIPPED(NO_READ)."""
    ok = env.session()
    assert any(result.name == "CLI 세션" and result.status == "통과" for result in ok.results)
    deps = env.deps()
    deps.make_desktop = lambda binary: None
    session = Session(env.args(), deps)
    lines = session.start()
    assert "  CLI 세션: 실패(SESSION_START) (창 읽기 없이 계속 · 결과의 H01–H11은 판정 못 함)" in lines
    session.end()
    checked = read_json(session.folder / SESSION_JSON)["checks"]
    assert {"name": "CLI 세션", "status": "실패", "code": "SESSION_START"} in checked


def test_only_read_commands_reach_the_cli(env):
    session = env.session()
    session.worker.start()
    session.capture()
    session.end()
    assert env.cli_commands() <= ALLOWED and "version" in env.commands


def test_setup_errors_exit_2(env, capsys):
    assert run_session(env.args(exe=None, system="없는시스템"), env.deps()) == 2
    (env.tmp / "bad.yaml").write_text("survey_capture_hotkey: SECRET_KEY\n", encoding="utf-8")
    assert run_session(env.args(settings=str(env.tmp / "bad.yaml")), env.deps()) == 2
    assert "SECRET_KEY" not in capsys.readouterr().err
    assert env.finished == []


def test_end_twice_finishes_once(env):
    session = env.session()
    session.end()
    session.end()
    assert len(env.finished) == 1


def test_shortcut_target_folder_is_the_app_folder(env):
    from agent.shortcut import Shortcut

    deps = env.deps()
    deps.check.read_shortcut = lambda path: Shortcut(APP_EXE)
    session = Session(env.args(exe=r"C:\Links\Real.lnk"), deps)
    session.start()
    assert session.tracker.already_running and session.tracker.is_app(APP)
