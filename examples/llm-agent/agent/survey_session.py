"""The capture session: stays up while the person uses the app, captures on a signal, ends on one.

`Session` is the core a test drives without threads: `start` runs the checks once and sets up
the watcher, `tick` records what changed among the windows, `capture` runs a fast phase and
hands the slow phase to the worker, `end` writes `session.json` and hands the folder to
`finish`. `run_session` ties the watcher thread, the worker thread, the hotkey thread and the
button (Tk on the main thread) together; Ctrl+C and the end signal end the same way.

The session never launches, closes or focuses the app and sends the CLI only `session`,
`list-windows`, `snapshot` and (through the checks) `version`. The console and every name-free
file hold codes, counts and family names only; raw class and module names go to `names.json`
only with --show-names.
"""

import os
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from agent import checks
from agent.app_processes import AppTracker
from agent.envfiles import ProfileError, load_env
from agent.knowledge import KnowledgeError, system_name
from agent.survey_capture import CaptureWorker, SlowContext, fast_capture
from agent.survey_codes import (DEFAULT_CAPTURE_HOTKEY, DEFAULT_END_HOTKEY, SURVEY_END_WAIT_S, SURVEY_POLL_S,
                                SURVEY_VERSION)
from agent.survey_facts import (EVENTS_JSONL, NAMES_JSON, SESSION_JSON, CheckFact, FamilyCount, ProcessFact,
                                SessionFacts, append_event, capture_id, write_json)
from agent.toolkits import class_family, count_families
from agent.survey_deps import HERE, NoDesktop, SessionDeps, SessionSetupError, session_folder
from agent.watch import Observation, Watcher, WatchThread

__all__ = ["Session", "SessionDeps", "SessionSetupError", "run_session", "session_folder"]

SHELL_HOST = "explorer.exe"
CLI_SESSION = "CLI 세션"


class Session:
    def __init__(self, args, deps: SessionDeps):
        self.args = args
        self.deps = deps
        self.win32 = deps.win32
        self.hotkey = "OK"
        self.settings: dict = {}
        self.captures: list[str] = []
        self.on_change: Callable[[int, int], None] | None = None
        self._lock = threading.RLock()
        self._capture_lock = threading.Lock()
        self._pending = 0
        self._ended: list[str] | None = None

    def start(self) -> list[str]:
        import check
        from dotenv import load_dotenv

        deps, args = self.deps, self.args
        environ = deps.check.environ
        try:
            load_env(args.profile, deps.check.env_files, environ, loader=load_dotenv)
        except ProfileError as error:
            raise SessionSetupError(str(error)) from error
        settings_result, self.settings = check.check_settings(args.settings, environ)
        if settings_result.status == checks.FAILED:
            raise SessionSetupError(f"설정 파일을 쓸 수 없습니다: {settings_result.code} {settings_result.detail}")
        target = check.with_shortcut(check.pick_target(args.exe, args.system, self.settings, args.profile_dir,
                                                       environ), deps.check.read_shortcut)
        if target.unknown:
            raise SessionSetupError("--system 이름이 설정의 systems에 없습니다")
        exe = target.shortcut.target if target.shortcut is not None else (target.exe or "")
        self.system = args.system or self._system_name(target.system or self.settings.get("system"), exe)
        if not self.system:
            raise SessionSetupError("--system이나 --exe로 시스템을 알려 주세요")
        options = SimpleNamespace(exe=args.exe, system=args.system, settings=args.settings,
                                  profile_dir=args.profile_dir, skip=["app", *args.skip])
        self.results = check.run_checks(options, deps.check)
        try:
            state = deps.code(HERE)
            self.head, self.changed = state.head, len(state.changed)
        except Exception:
            self.head, self.changed = None, 0
        dpi = self.win32.set_dpi_awareness()
        binary = checks.binary_location(environ, deps.check.binaries)[0]
        desktop = deps.desktop(binary)
        reading = (checks.passed(CLI_SESSION, "창 읽기 가능") if desktop is not None
                   else checks.failed(CLI_SESSION, "SESSION_START", "창 읽기 없이 계속 · 결과의 H01–H11은 판정 못 함"))
        self.results = [*self.results, reading]
        self.desktop = desktop or NoDesktop()
        windows_dir = self.win32.windows_dir()
        self.tracker = AppTracker(exe, tuple(self.settings.get("survey_app_exes") or ()), windows_dir)
        rows = self.win32.process_rows()
        self.own_pids = self._own_pids(rows)
        self.started_at = deps.now()
        self._start = deps.clock()
        self.tracker.update(rows, 0.0)
        front = self.win32.monitor_of(self.win32.foreground() or 0)
        self.watcher = Watcher(self.tracker, self.own_pids, (front.width, front.height) if front else (0, 0))
        known = self._system_name(self.system, "")
        saved = checks.load_ids(checks.ids_path(deps.check.check_dir, known)) if known else None
        self.saved_ids = tuple(sorted(saved.keys)) if saved is not None else None
        self.saved_ids_process = saved.process if saved is not None else None
        self.folder = session_folder(deps.captures_dir, self.started_at, self.system)
        self.ctx = SlowContext(self.desktop, self.win32, self.tracker, self.watcher, deps.grab, deps.uia_runner,
                               windows_dir=windows_dir)
        self.worker = CaptureWorker(self.ctx, self.folder, self._slow_done)
        self.tick()
        lines = ["캡처 세션 (화면 내용 없음)"] + [f"  {checks.line(result)}" for result in self.results]
        return lines + [f"  DPI 인식: {dpi}", f"세션 폴더: {self.folder.name}",
                        f"앱 프로세스: {'이미 떠 있음' if self.tracker.already_running else '아직 없음 · 앱을 켜세요'}"]

    @staticmethod
    def _system_name(explicit, exe) -> str | None:
        try:
            return system_name(explicit, exe)
        except KnowledgeError:
            return None

    def _own_pids(self, rows) -> frozenset[int]:
        """This process and its parents up to the shell, so the session's console is never a capture."""
        parents = {row.pid: row for row in rows}
        own, pid = {os.getpid()}, os.getpid()
        while pid in parents and parents[pid].parent not in own and parents[pid].parent:
            parent = parents.get(parents[pid].parent)
            if parent is None or Path(str(parent.image or "")).name.lower() == SHELL_HOST:
                break
            own.add(parent.pid)
            pid = parent.pid
        return frozenset(own)

    def _t(self) -> float:
        return round(self.deps.clock() - self._start, 1)

    def record(self, events) -> None:
        for event in events:
            append_event(self.folder / EVENTS_JSONL, event)

    def observe(self) -> Observation:
        """Updates the process list, records its events and returns one window observation."""
        with self._lock:
            t = self._t()
            self.record(self.tracker.update(self.win32.process_rows(), t))
            windows = self.win32.top_windows()
            hung = frozenset(row.hwnd for row in windows
                             if row.visible and self.tracker.is_app(row.pid) and self.win32.is_hung(row.hwnd))
            return Observation(t, windows, self.win32.foreground(), hung)

    def tick(self, obs: Observation | None = None) -> None:
        with self._lock:
            if obs is None:
                obs = self.observe()
            else:
                self.record(self.tracker.update(self.win32.process_rows(), obs.t))
            self.record(self.watcher.step(obs))

    def _capture_observe(self) -> Observation:
        obs = self.observe()
        with self._lock:
            self.record(self.watcher.step(obs))
            if not self.captures[1:] and not any(item.rel == "APP" for item in self.tracker.numbered()):
                self._pick(obs)
        return obs

    def _pick(self, obs: Observation) -> None:
        hwnd = obs.foreground
        row = next((item for item in obs.windows if item.hwnd == hwnd), None)
        if row is None or row.pid in self.own_pids:
            hwnd = self.watcher.last_foreground
            row = next((item for item in obs.windows if item.hwnd == hwnd), None)
        if row is not None and self.tracker.rel(row.pid) != "SYSTEM":
            self.tracker.pick(row.pid)

    def capture(self) -> str:
        with self._capture_lock:
            self.win32.beep()
            cid = capture_id(len(self.captures) + 1)
            self.captures.append(cid)
            fast = fast_capture(cid, self._t(), self.folder, self.deps.grab, self.watcher, self._capture_observe,
                                self.own_pids)
            with self._lock:
                self._pending += 1
            self._changed()
            self.worker.submit(fast)
            return cid

    def _slow_done(self, _facts) -> None:
        with self._lock:
            self._pending = max(0, self._pending - 1)
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(len(self.captures), self._pending)
            except Exception:
                pass

    def end(self) -> list[str]:
        if self._ended is not None:
            return self._ended
        self.worker.drain(SURVEY_END_WAIT_S)
        facts = SessionFacts(
            version=SURVEY_VERSION, system=self.system, started=self.started_at.isoformat(timespec="seconds"),
            duration_s=self._t(), head=self.head, changed=self.changed, hotkey=self.hotkey,
            app_seen=self._app_seen(), first_app_window_t=self.watcher.first_app_window_t,
            unknown_paths=self.tracker.unknown_paths,
            checks=tuple(CheckFact(result.name, result.status, result.code) for result in self.results),
            processes=self._processes(), captures=tuple(self.captures), saved_ids=self.saved_ids,
            saved_ids_process=self.saved_ids_process)
        write_json(self.folder / SESSION_JSON, facts.to_dict())
        if self.args.show_names:
            classes = set()
            for names in self.ctx.class_tally.values():
                classes.update(count_families(names, class_family)[1])
            names = {"classes": sorted(classes), "modules": sorted(self.ctx.unknown_modules)}
            write_json(self.folder / NAMES_JSON, names)
        self._ended = [f"세션 끝 · 캡처 {len(self.captures)}"] + list(self.deps.finished(self.folder))
        return self._ended

    def _app_seen(self) -> str:
        if self.tracker.already_running:
            return "ALREADY_RUNNING"
        if self.tracker.picked:
            return "PICKED_BY_CAPTURE"
        if self.watcher.first_app_window_t is not None:
            return "STARTED"
        return "NOT_SEEN"

    def _processes(self) -> tuple[ProcessFact, ...]:
        facts = []
        for item in self.tracker.numbered():
            modules, bits = self.ctx.module_cache.get(item.proc, (None, None))
            classes: FamilyCount = count_families(self.ctx.class_tally.get(item.proc, []), class_family)[0]
            facts.append(ProcessFact(proc=item.proc, rel=item.rel, target_exe=item.target,
                                     started_in_session=item.started_in_session, bits=bits, modules=modules,
                                     classes=classes, mdi=item.proc in self.ctx.mdi,
                                     max_visible_top=self.watcher.max_visible_top.get(item.proc, 0),
                                     first_window_t=self.watcher.first_window_t.get(item.proc)))
        return tuple(facts)


def _hotkey_lines(session: Session, bindings: dict, failed, button) -> list[str]:
    lines = [f"HOTKEY_TAKEN {name}" for name in failed]
    working = {name: text for name, text in bindings.items() if name not in failed}
    if working:
        lines.append(" · ".join(f"{'캡처' if name == 'capture' else '종료'}: {text}" for name, text in working.items()))
    lines.append("버튼: 왼쪽 클릭 캡처 · 오른쪽 클릭 종료" if button is not None else "버튼 없음 · 단축키로 계속")
    if button is None and "capture" in failed:
        lines.append("단축키와 버튼을 모두 쓸 수 없습니다. Ctrl+C로만 끝낼 수 있습니다")
    lines.append("Ctrl+C로도 끝낼 수 있습니다")
    return lines


def _dispatch(session: Session, signals: queue.Queue, button=None) -> None:
    """Runs captures until the end signal, then closes the button. Short waits keep Ctrl+C
    answered when this runs on the main thread."""
    while True:
        try:
            signal = signals.get(timeout=SURVEY_POLL_S)
        except queue.Empty:
            continue
        if signal == "end":
            break
        try:
            session.capture()
        except Exception:
            continue
    if button is not None:
        button.close()


def _start_button(deps: SessionDeps, signals: queue.Queue):
    if deps.make_button is None:
        return None
    try:
        button = deps.make_button(lambda: signals.put("capture"), lambda: signals.put("end"))
        if button is not None:
            button.start()
        return button
    except Exception:
        return None


def run_session(args, deps: SessionDeps | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    deps = deps or SessionDeps()
    session = Session(args, deps)
    try:
        lines = session.start()
    except SessionSetupError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    signals: queue.Queue = queue.Queue()
    bindings = {"capture": session.settings.get("survey_capture_hotkey", DEFAULT_CAPTURE_HOTKEY),
                "end": session.settings.get("survey_end_hotkey", DEFAULT_END_HOTKEY)}
    hotkeys = deps.make_hotkeys(bindings, signals.put)
    failed = tuple(hotkeys.start())
    if failed:
        session.hotkey = "HOTKEY_TAKEN"
    button = _start_button(deps, signals)
    if button is not None:
        session.on_change = lambda count, pending: (button.set_count(count), button.set_busy(pending > 0))
    print("\n".join(_hotkey_lines(session, bindings, failed, button)), flush=True)
    watch = WatchThread(session.watcher, session.observe, session.record, SURVEY_POLL_S)
    watch.start()
    session.worker.start()
    try:
        if button is not None:
            dispatcher = threading.Thread(target=_dispatch, args=(session, signals, button), daemon=True)
            dispatcher.start()
            button.run()
            signals.put("end")
            dispatcher.join(SURVEY_END_WAIT_S)
        else:
            _dispatch(session, signals)
    except KeyboardInterrupt:
        pass
    finally:
        signals.put("end")
        hotkeys.stop()
        if button is not None:
            button.close()
        watch.stop()
    print("\n".join(session.end()), flush=True)
    return 0

