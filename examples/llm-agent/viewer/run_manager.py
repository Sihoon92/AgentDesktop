"""Starts agent runs from the viewer, one at a time, on a background thread.

The server process has no terminal, so nothing can confirm restart_target: the manager
hands every toolbox a confirm callback that always declines. Requests are validated
before a run folder exists, and a run that crashes still ends with a final status.
A run started with approval holds each app-changing action until decide() answers it.
Every run gets an ask gate, so a question from the agent waits until answer() replies, and the
knowledge store of its system, so a confirmed run can register what the person taught it.
Every run that ends leaves summary.txt in its folder, as a run.py run does.
"""

import functools
import threading
from pathlib import Path, PureWindowsPath

from agent.approval import ApprovalGate, Decision
from agent.ask import Answer, AskGate
from agent.desktop import DesktopClient, start_session
from agent.knowledge import KnowledgeError, KnowledgeStore, knowledge_root, system_name
from agent.llm import ModelSetupError, Models
from agent.loop import RunResult, run_agent
from agent.recorder import RunRecorder
from agent.registration import register_after_run
from agent.summary import current_code, write_summary
from agent.task import Task, TaskError, task_from_args
from agent.tools import Toolbox


class RunBusy(Exception):
    """Another run is still in progress."""


class InvalidRun(ValueError):
    """The request cannot start a run; the message says why, in Korean, for the viewer."""


class RunStartFailed(Exception):
    """The request was valid but the run could not start (no session, no model, ...)."""


def decline(_prompt):
    return False


def default_toolbox(desktop, task, confirm, observer, asker, knowledge, captures_dir, vision=None):
    return Toolbox(desktop, task, confirm=confirm, observer=observer, asker=asker, knowledge=knowledge,
                   captures_dir=captures_dir, vision=vision)


def _split_models(built):
    """(text model, vision) from what the model factory returned: a Models, or a bare text model."""
    if isinstance(built, Models):
        return built.text, built.vision
    return built, None


def _leave_summary(run_dir):
    """Writes summary.txt; nothing about it may change the run's result or free slot."""
    try:
        write_summary(run_dir)
    except Exception:
        pass


class RunManager:
    def __init__(self, runs_dir, binary, default_exe, model_factory, toolbox_factory=default_toolbox,
                 desktop_factory=None, gate_factory=ApprovalGate, ask_gate_factory=AskGate, knowledge_dir=None,
                 task_defaults=None, environ=None):
        """task_defaults are the settings file's values that every new run's task starts from;
        environ supplies OPENAI_MODEL below them (None reads no env). model_factory(task) returns
        an agent.llm.Models, or a bare text model, and may raise ModelSetupError."""
        self.runs_dir = Path(runs_dir)
        self.binary = str(binary)
        self.default_exe = default_exe
        self._model_factory = model_factory
        self._toolbox_factory = toolbox_factory
        self._desktop_factory = desktop_factory or self._new_session
        self._gate_factory = gate_factory
        self._ask_gate_factory = ask_gate_factory
        self._knowledge_dir = knowledge_dir
        self._task_defaults = dict(task_defaults or {})
        self._environ = environ
        self._lock = threading.Lock()
        self._active = None
        self._gate = None
        self._ask_gate = None
        self._thread = None

    def active(self):
        with self._lock:
            return self._active

    @property
    def default_system(self):
        """The settings file's system name that a form left empty falls back to, or None."""
        return self._task_defaults.get("system") or None

    @property
    def default_stage_call_limit(self):
        """The calls a stage gets when the form leaves the limit empty: the settings file's, else the code's."""
        return self._task_defaults.get("stage_call_limit") or Task.stage_call_limit

    def decide(self, run_id, proposal_id, approved, comment=""):
        """Answers a proposal of the active run. False when the run is not active, it was
        started without approval, or the proposal is unknown or already answered."""
        with self._lock:
            gate = self._gate if run_id == self._active else None
        if gate is None:
            return False
        return gate.decide(proposal_id, Decision(approved, comment))

    def answer(self, run_id, question_id, text, accepted=None, pin=None, items=()):
        """Answers a question of the active run. False when the run is not active or the
        question is unknown, already answered or timed out. `pin` is in screen pixels."""
        with self._lock:
            gate = self._ask_gate if run_id == self._active else None
        if gate is None:
            return False
        reply = Answer(text=text, pin=tuple(pin) if pin is not None else None, accepted=accepted,
                       items=tuple(items))
        return gate.answer(question_id, reply)

    def can_answer(self):
        """True while a run is active and its questions can be answered here."""
        with self._lock:
            return self._ask_gate is not None

    def start(self, goal, exe=None, level=3, approve=False, system=None, stage_call_limit=None):
        """Returns the new run id once its folder exists; the run continues in the background.
        With approve, every app-changing action waits for decide() or times out as a stop.
        `system` names the knowledge folder; empty means the exe file name. `stage_call_limit`
        is the calls each stage gets; None means the settings file's value or the default."""
        task = self.build_task(goal, exe, level, stage_call_limit)
        if type(approve) is not bool:
            raise InvalidRun("approve는 true 또는 false여야 합니다")
        system = self.build_system(system, task.exe, task.system)
        with self._lock:
            if self._active is not None:
                raise RunBusy(f"실행 중인 작업이 있습니다: {self._active}")
            self._active = "starting"
        try:
            built = self._model_factory(task)
            model, vision = _split_models(built)
            meta = self._meta(task, system)
            if isinstance(built, Models):
                meta["vision"] = vision is not None
            desktop = self._desktop_factory()
            recorder = RunRecorder(self.runs_dir, desktop, meta)
        except RunStartFailed:
            self._release()
            raise
        except ModelSetupError as error:
            self._release()
            raise RunStartFailed(str(error)) from error
        except Exception as error:
            self._release()
            raise RunStartFailed(f"실행을 시작하지 못했습니다: {error}") from error
        try:
            gate = self._gate_factory() if approve else None
            ask_gate = self._ask_gate_factory(timeout_s=getattr(task, "ask_timeout_s", 1800))
            store = KnowledgeStore(knowledge_root(self._knowledge_dir, task.knowledge_dir), system)
            extra = {} if vision is None else {"vision": vision}
            toolbox = self._toolbox_factory(desktop, task, decline, recorder, ask_gate, store, recorder.path / "captures",
                                            **extra)
            register = functools.partial(register_after_run, store=store, run_id=recorder.run_id)
        except Exception as error:
            recorder.finish(RunResult("stopped", 0, f"실행을 시작하지 못했습니다: {error}"))
            _leave_summary(recorder.path)
            self._release()
            raise RunStartFailed(f"실행을 시작하지 못했습니다: {error}") from error
        with self._lock:
            self._active = recorder.run_id
            self._gate = gate
            self._ask_gate = ask_gate
            self._thread = threading.Thread(
                target=self._run, args=(model, toolbox, task, recorder, gate, register), name=f"run-{recorder.run_id}",
                daemon=True,
            )
            self._thread.start()
        return recorder.run_id

    def join(self, timeout=None):
        """Waits for the current run's thread; for tests and shutdown."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def build_task(self, goal, exe, level, stage_call_limit=None):
        if not isinstance(goal, str) or not goal.strip():
            raise InvalidRun("지시를 입력해 주세요")
        exe = exe or self.default_exe
        if not exe:
            raise InvalidRun("실행 파일 경로를 입력해 주세요")
        if not isinstance(exe, str) or not PureWindowsPath(exe).is_absolute():
            raise InvalidRun("실행 파일 경로는 절대 경로여야 합니다")
        if not Path(exe).is_file():
            raise InvalidRun(f"실행 파일이 없습니다: {exe}")
        if type(level) is not int or not 0 <= level <= 3:
            raise InvalidRun("권한 단계는 0부터 3까지의 정수여야 합니다")
        defaults = dict(self._task_defaults)
        if stage_call_limit is not None:
            if type(stage_call_limit) is not int or stage_call_limit < 1:
                raise InvalidRun("단계별 호출 한도는 1 이상의 정수여야 합니다(비우면 기본값)")
            defaults["stage_call_limit"] = stage_call_limit
        try:
            return task_from_args(goal.strip(), exe, level, defaults=defaults, environ=self._environ)
        except TaskError as error:
            raise InvalidRun(str(error)) from error

    def build_system(self, system, exe, fallback=None):
        """The form's system name, else `fallback` (the settings file's), else the exe name."""
        if system is not None and not isinstance(system, str):
            raise InvalidRun("시스템 이름은 글이어야 합니다")
        try:
            return system_name((system or "").strip() or fallback, exe)
        except KnowledgeError as error:
            raise InvalidRun("시스템 이름은 영문, 숫자, 한글, '.', '_', '-'로 64자까지 쓸 수 있습니다") from error

    def _run(self, model, toolbox, task, recorder, gate, register):
        try:
            result = run_agent(model, toolbox, task, recorder.on_event, gate=gate, register=register)
        except Exception as error:
            result = RunResult("stopped", 0, f"실행 중 오류: {type(error).__name__}: {error}")
        try:
            recorder.finish(result)
        finally:
            _leave_summary(recorder.path)
            self._release()

    def _release(self):
        with self._lock:
            self._active = None
            self._gate = None
            self._ask_gate = None

    def _meta(self, task, system):
        return {
            "goal": task.goal,
            "exe": task.exe,
            "model": task.model,
            "permission_level": task.permission_level,
            "max_steps": task.max_steps,
            "started_by": "viewer",
            "system": system,
            "code": current_code(),
        }

    def _new_session(self):
        session = start_session(self.binary, "llm-agent viewer")
        if session is None:
            raise RunStartFailed("agent-desktop 세션을 시작하지 못했습니다")
        return DesktopClient(self.binary, session)
