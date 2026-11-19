"""Starts agent runs from the viewer, one at a time, on a background thread.

The server process has no terminal, so nothing can confirm restart_target: the manager
hands every toolbox a confirm callback that always declines. Requests are validated
before a run folder exists, and a run that crashes still ends with a final status.
A run started with approval holds each app-changing action until decide() answers it.
Every run gets an ask gate, so a question from the agent waits until answer() replies, and the
knowledge store of its system, so a confirmed run can register what the person taught it.
Every run that ends leaves summary.txt in its folder, as a run.py run does.
stop() asks the active run to stop at its next safe place (agent.loop): each run gets its own
stop signal, so a stop never reaches the next run, and the target app is left open.

A model run that succeeds is followed by making a workflow from its record, before the record is
finished, as run.py does. workflows() lists a system's saved workflows and start_workflow()
replays one with no model: an ungated toolbox, no one to ask and no knowledge, its values
checked before a run folder exists and again with the real run id, and the outcome counted in
the store when it ends. Replays share the one-run-at-a-time slot and the stop signal.
"""

import functools
import threading
import time
from pathlib import Path, PureWindowsPath

from agent.approval import ApprovalGate, Decision
from agent.ask import Answer, AskGate, NoAsker
from agent.desktop import DesktopClient, start_session
from agent.knowledge import KnowledgeError, KnowledgeStore, knowledge_root, system_name
from agent.llm import ModelSetupError, Models
from agent.loop import RunResult, run_agent
from agent.recorder import RunRecorder
from agent.registration import register_after_run
from agent.summary import current_code, write_summary
from agent.systems import apply_ready, system_exe, system_names
from agent.systems import default_choice as settings_default_choice
from agent.task import Task, TaskError, task_from_args
from agent.tools import Toolbox
from agent.workflow_author import after_run
from agent.workflow_format import WorkflowError, resolve_values
from agent.workflow_run import record_outcome, run_workflow
from agent.workflow_store import WorkflowStore, workflow_root

WORKFLOW_RUN = "workflow"
"""started_by and model of a replay in run.json: no model takes part."""
VALUE_CHECK_RUN_ID = "check"


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


def default_replay_toolbox(desktop, task, observer, captures_dir):
    """The toolbox of a replay: no question policy or repeat guard, no one to ask, no knowledge."""
    return Toolbox(desktop, task, confirm=decline, observer=observer, asker=NoAsker(), captures_dir=captures_dir,
                   gated=False)


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
                 task_defaults=None, environ=None, workflow_dir=None, replay_toolbox_factory=default_replay_toolbox):
        """task_defaults are the settings file's values that every new run's task starts from; their
        systems give the runs a target by name and their ready signal reaches only the exes they
        name. environ supplies OPENAI_MODEL below them (None reads no env). model_factory(task) returns
        an agent.llm.Models, or a bare text model, and may raise ModelSetupError. The workflow folder
        is workflow_dir, else the settings' workflow_dir, else AGENT_WORKFLOW_DIR from environ, else
        the default (agent.workflow_store.workflow_root)."""
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
        self._stop = None
        self._recorder = None
        self._replay_toolbox_factory = replay_toolbox_factory
        self.workflow_root = workflow_root(workflow_dir, self._task_defaults.get("workflow_dir"),
                                           {} if environ is None else environ)

    def active(self):
        with self._lock:
            return self._active

    @property
    def default_system(self):
        """The settings file's system name that a form left empty falls back to, or None."""
        return self._task_defaults.get("system") or None

    @property
    def systems(self):
        """The settings file's system names in its order. The viewer lists these, never their paths."""
        return system_names(self._task_defaults)

    @property
    def default_choice(self):
        """The system the viewer's list starts on: the settings system when it names an entry, else the first."""
        return settings_default_choice(self._task_defaults)

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

    def stop(self, run_id):
        """Asks the active run `run_id` to stop: sets its stop signal, answers what its gates hold
        with no answer, and records stop_requested once. False when that run is not active."""
        with self._lock:
            if run_id != self._active or self._stop is None:
                return False
            first = not self._stop.is_set()
            self._stop.set()
            gates = [gate for gate in (self._gate, self._ask_gate) if gate is not None]
            recorder = self._recorder
        for gate in gates:
            gate.close()
        if first and recorder is not None:
            recorder.on_event({"type": "stop_requested"})
        return True

    def stop_and_wait(self, timeout_s):
        """Stops the active run, if any, and waits up to `timeout_s` for its thread to end. True
        when no run is active at the end. A run still starting is waited for first."""
        deadline = time.monotonic() + timeout_s
        active = self.active()
        while active == "starting" and time.monotonic() < deadline:
            time.sleep(0.05)
            active = self.active()
        if active is not None and active != "starting":
            self.stop(active)
            thread = self._thread
            if thread is not None:
                thread.join(max(0.0, deadline - time.monotonic()))
        return self.active() is None

    def stopping(self):
        """True once stop() was asked of the active run and until that run has ended."""
        with self._lock:
            return self._active is not None and self._stop is not None and self._stop.is_set()

    def can_answer(self):
        """True while a run is active and its questions can be answered here."""
        with self._lock:
            return self._ask_gate is not None

    def start(self, goal, exe=None, level=3, approve=False, system=None, stage_call_limit=None):
        """Returns the new run id once its folder exists; the run continues in the background.
        With approve, every app-changing action waits for decide() or times out as a stop.
        `system` names the knowledge folder; empty means the exe file name. Without `exe`, a
        `system` the settings file's systems hold picks its exe. `stage_call_limit` is the calls
        each stage gets; None means the settings file's value or the default."""
        exe, system = self._target(exe, system)
        task = apply_ready(self.build_task(goal, exe, level, stage_call_limit), self._task_defaults)
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
            workflows = WorkflowStore(self.workflow_root, system)
        except Exception as error:
            recorder.finish(RunResult("stopped", 0, f"실행을 시작하지 못했습니다: {error}"))
            _leave_summary(recorder.path)
            self._release()
            raise RunStartFailed(f"실행을 시작하지 못했습니다: {error}") from error
        stop = threading.Event()
        run = functools.partial(self._run, model, toolbox, task, recorder, (gate, register, stop), workflows)
        return self._begin(recorder, run, stop, gate=gate, ask_gate=ask_gate)

    def _begin(self, recorder, run, stop, gate=None, ask_gate=None):
        """Makes the run active and starts `run` on its thread; returns the run id."""
        with self._lock:
            self._active = recorder.run_id
            self._gate = gate
            self._ask_gate = ask_gate
            self._stop = stop
            self._recorder = recorder
            self._thread = threading.Thread(target=run, name=f"run-{recorder.run_id}", daemon=True)
            self._thread.start()
        return recorder.run_id

    def workflows(self, system=None):
        """The readable workflows of `system` (else the system a form left empty falls back to), in
        id order, each as {id, goal, status, passes, failures, verified_with_other_values, params:
        [{name, kind, example}], fixed}. A file that does not pass the format check is left out."""
        store = WorkflowStore(self.workflow_root, self._workflow_system(system))
        return [_listed(workflow) for workflow in store.entries()]

    def start_workflow(self, workflow_id, values, system=None):
        """Replays a saved workflow of `system` with `values` ({name: text}) and returns the new run
        id once its folder exists. Every value is checked before anything starts: a bad request
        raises InvalidRun whose message starts with the code."""
        if not isinstance(values, dict) or not all(
                isinstance(name, str) and isinstance(value, str) for name, value in values.items()):
            raise InvalidRun("values는 이름과 글 값을 담은 객체여야 합니다")
        exe, system = self._target(None, system)
        store = WorkflowStore(self.workflow_root, self._workflow_system(system))
        workflow = self._stored_workflow(store, workflow_id)
        try:
            resolve_values(workflow, values, VALUE_CHECK_RUN_ID)
        except WorkflowError as error:
            raise InvalidRun(f"{error.code}: {error}") from error
        task = apply_ready(self.build_task(workflow["goal"], exe, workflow["permission_level"]), self._task_defaults)
        with self._lock:
            if self._active is not None:
                raise RunBusy(f"실행 중인 작업이 있습니다: {self._active}")
            self._active = "starting"
        try:
            desktop = self._desktop_factory()
            meta = {**self._meta(task, store.system), "model": WORKFLOW_RUN, "started_by": WORKFLOW_RUN}
            recorder = RunRecorder(self.runs_dir, desktop, meta)
        except RunStartFailed:
            self._release()
            raise
        except Exception as error:
            self._release()
            raise RunStartFailed(f"실행을 시작하지 못했습니다: {error}") from error
        try:
            toolbox = self._replay_toolbox_factory(desktop, task, recorder, recorder.path / "captures")
        except Exception as error:
            recorder.finish(RunResult("stopped", 0, f"실행을 시작하지 못했습니다: {error}"))
            _leave_summary(recorder.path)
            self._release()
            raise RunStartFailed(f"실행을 시작하지 못했습니다: {error}") from error
        stop = threading.Event()
        run = functools.partial(self._replay, toolbox, task, (store, workflow, values), recorder, stop)
        return self._begin(recorder, run, stop)

    def _workflow_system(self, system):
        """The workflow folder name of `system`, else of the settings' system, else of the default exe."""
        if system is not None and not isinstance(system, str):
            raise InvalidRun("시스템 이름은 글이어야 합니다")
        name = (system or "").strip() or self.default_choice or self.default_system
        try:
            return system_name(name, self.default_exe or "")
        except KnowledgeError as error:
            raise InvalidRun("시스템 이름은 영문, 숫자, 한글, '.', '_', '-'로 64자까지 쓸 수 있습니다") from error

    @staticmethod
    def _stored_workflow(store, workflow_id):
        try:
            workflow = store.get(workflow_id)
        except WorkflowError as error:
            raise InvalidRun(f"{error.code}: 읽을 수 없거나 형식이 틀린 workflow입니다") from error
        if workflow is None:
            raise InvalidRun(f"WORKFLOW_NOT_FOUND: 그 id의 workflow가 없습니다 (시스템: {store.system})")
        return workflow

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

    def _target(self, exe, system):
        """(exe, system) for a request: the form's exe; else the entry of the system it names; else
        the default exe; else, when it names no system, the settings' default choice. A name with
        no entry stays only the knowledge name, and is refused when no exe is left to run."""
        if exe or not self.systems:
            return exe, system
        if system is not None and not isinstance(system, str):
            return exe, system
        name = (system or "").strip()
        if name:
            entry = system_exe(self._task_defaults, name)
            if entry:
                return entry, name
            if not self.default_exe:
                raise InvalidRun(f"설정 파일의 systems에 없는 시스템입니다: {name}")
            return exe, system
        if self.default_exe:
            return exe, system
        choice = self.default_choice
        return system_exe(self._task_defaults, choice), choice

    def build_system(self, system, exe, fallback=None):
        """The form's system name, else `fallback` (the settings file's), else the exe name."""
        if system is not None and not isinstance(system, str):
            raise InvalidRun("시스템 이름은 글이어야 합니다")
        try:
            return system_name((system or "").strip() or fallback, exe)
        except KnowledgeError as error:
            raise InvalidRun("시스템 이름은 영문, 숫자, 한글, '.', '_', '-'로 64자까지 쓸 수 있습니다") from error

    def _run(self, model, toolbox, task, recorder, hooks, workflows):
        gate, register, stop = hooks
        try:
            result = run_agent(model, toolbox, task, recorder.on_event, gate=gate, register=register, stop=stop)
        except Exception as error:
            result = RunResult("stopped", 0, f"실행 중 오류: {type(error).__name__}: {error}")
        try:
            result = after_run(model, result, recorder.path, workflows, recorder.on_event)
        except Exception:
            pass
        self._end(recorder, result)

    def _replay(self, toolbox, task, chosen, recorder, stop):
        store, workflow, given = chosen
        try:
            values = resolve_values(workflow, given, recorder.run_id)
        except WorkflowError as error:
            result = RunResult("stopped", 0, error.code)
        else:
            try:
                result = run_workflow(toolbox, task, workflow, values, log=recorder.on_event, stop=stop)
            except Exception as error:
                result = RunResult("stopped", 0, f"실행 중 오류: {type(error).__name__}: {error}")
            try:
                record_outcome(store, workflow, values, result)
            except Exception:
                pass
        self._end(recorder, result)

    def _end(self, recorder, result):
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
            self._stop = None
            self._recorder = None

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


def _listed(workflow):
    """What the viewer shows of a workflow: no steps, and never a path of the system."""
    params = [{"name": name, "kind": spec["kind"], "example": spec["example"]}
              for name, spec in workflow["params"].items()]
    keys = ("id", "goal", "status", "passes", "failures", "verified_with_other_values")
    return {**{key: workflow[key] for key in keys}, "params": params, "fixed": list(workflow["fixed"])}
