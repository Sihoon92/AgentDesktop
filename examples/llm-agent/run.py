"""Runs a task file with the LLM agent, or with --baseline, the fixed no-model steps.

A successful model run is followed by making a workflow from its record. --workflow replays a
saved workflow with --param values and no model; --workflows lists a system's workflows;
--make-workflow makes one again from a past run and leaves that run's record as it was.
"""

import argparse
import functools
import os
import secrets
import sys
import threading
import webbrowser
from dataclasses import replace
from datetime import datetime
from pathlib import Path, PureWindowsPath

from dotenv import load_dotenv

from agent.approval import TerminalGate
from agent.ask import NoAsker, TerminalAsker
from agent.baseline import run_baseline
from agent.desktop import DesktopClient, start_session
from agent.envfiles import ProfileError, load_env
from agent.llm import ModelSetupError, build_models, make_client, start_line, text_responder
from agent.knowledge import KnowledgeError, KnowledgeStore, knowledge_root, system_name
from agent.loop import RunResult, run_agent
from agent.recorder import RunRecorder
from agent.registration import register_after_run
from agent.settings import SettingsError, load_settings, settings_path
from agent.summary import current_code, read_records, write_summary
from agent.systems import apply_ready, system_exe, system_names
from agent.task import TaskError, load_task, task_from_args
from agent.tools import Toolbox
from agent.workflow_author import after_run, make_workflow
from agent.workflow_format import WorkflowError, resolve_values
from agent.workflow_run import record_outcome, run_workflow
from agent.workflow_store import WorkflowStore, workflow_root
from viewer.server import make_server

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ENV_FILES = [HERE / ".env", REPO / ".env"]
RUNS_DIR = HERE / "runs"
EXIT_CODES = {"verified": 0, "confirmed": 0, "claimed": 3}
WORKFLOW_RUN = "workflow"
"""started_by and model of a replay in run.json: no model takes part."""
VALUE_CHECK_RUN_ID = "check"
LATEST = "latest"
CONFLICTS = {
    "workflow": ("task", "goal", "baseline", "approve", "make_workflow", "workflows"),
    "make_workflow": ("task", "goal", "exe", "baseline", "approve", "workflows"),
    "workflows": ("task", "goal", "baseline", "approve"),
}
FLAG_NAMES = {"task": "작업 파일", "goal": "--goal", "exe": "--exe", "baseline": "--baseline",
              "approve": "--approve", "workflow": "--workflow", "make_workflow": "--make-workflow",
              "workflows": "--workflows"}
open_browser = webbrowser.open
BINARIES = [
    REPO / "target" / "release" / "agent-desktop.exe",
    REPO / "target" / "debug" / "agent-desktop.exe",
]


def find_binary():
    configured = os.environ.get("AGENT_DESKTOP_BIN")
    if configured:
        return Path(configured)
    return next((path for path in BINARIES if path.exists()), BINARIES[-1])


def is_interactive():
    return sys.stdin.isatty()


def confirm(prompt):
    if not is_interactive():
        return False
    return input(f"{prompt}\n계속할까요? [y/N] ").strip().lower() == "y"


def make_logger(recorder):
    def log(event):
        recorder.on_event(event)
        if event.get("type") != "tool":
            return
        print(f"[{event['step']}] {event['tool']} {event['arguments']} -> {_outcome(event['result'])}")

    return log


def _outcome(result):
    for key in ("status", "code", "refused"):
        if result.get(key):
            return f"{key}={result[key]}"
    delivery = (result.get("disposition") or {}).get("delivery")
    return f"ok={result.get('ok')}" + (f" delivery={delivery}" if delivery else "")


def _fail(message):
    print(f"오류: {message}", file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path, nargs="?", help="Task file; optional with --exe and --goal")
    parser.add_argument("--goal", help="Natural-language instruction; overrides the task file's goal")
    parser.add_argument("--exe", help="Absolute path of the target executable (without a task file)")
    parser.add_argument("--level", type=int, choices=range(4), default=3, help="Permission level for --exe")
    parser.add_argument("--baseline", action="store_true", help="Run fixed steps without the model")
    parser.add_argument("--approve", action="store_true", help="Ask on the terminal before every action")
    parser.add_argument("--view", action="store_true", help="Open the step viewer and follow this run live")
    parser.add_argument("--system", help="Target system: a name in the settings file's systems picks its exe; "
                                         "it is also the knowledge name (default: the exe name)")
    parser.add_argument("--knowledge-dir", help="Folder that holds the per-system knowledge")
    parser.add_argument("--profile", help="Load .env.<profile> before .env (default: AGENT_PROFILE)")
    parser.add_argument("--workflow", help="Replay a saved workflow by its id, with no model")
    parser.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                        help="A value for the replayed workflow; give it once per parameter")
    parser.add_argument("--workflows", action="store_true", help="List the system's saved workflows")
    parser.add_argument("--make-workflow", metavar="RUN", help="Make a workflow from a past run: its id or latest")
    parser.add_argument("--workflow-dir", help="Folder that holds the per-system workflows")
    args = parser.parse_args(argv)
    conflict = _conflict(args)
    if conflict:
        return _fail(conflict)
    if args.approve and args.baseline:
        return _fail("--approve는 모델 실행에서만 쓸 수 있습니다. --baseline과 함께 쓸 수 없습니다")
    if args.approve and not is_interactive():
        return _fail("--approve는 입력을 받을 수 있는 터미널에서만 쓸 수 있습니다")
    try:
        profile = load_env(args.profile, ENV_FILES, loader=load_dotenv)
    except ProfileError as error:
        return _fail(str(error))
    if profile:
        print(f"프로필: {profile}")
    try:
        settings = load_settings()
    except (SettingsError, OSError) as error:
        return _fail(str(error))
    if settings:
        print(f"설정 파일: {settings_path()} ({', '.join(settings)})")
    workflows_root = workflow_root(args.workflow_dir, settings.get("workflow_dir"), os.environ)
    if args.workflows:
        return _list_workflows(args, settings, workflows_root)
    if args.make_workflow:
        return _make_from_past(args, settings, workflows_root)
    if args.workflow:
        return _replay(args, settings, workflows_root)
    try:
        task = _resolve_task(args, settings)
    except (TaskError, OSError) as error:
        return _fail(str(error))
    if not Path(task.exe).exists():
        return _fail(f"실행 파일이 없습니다: {task.exe}")
    try:
        system = system_name(args.system or task.system, task.exe)
    except KnowledgeError as error:
        return _fail(f"시스템 이름을 쓸 수 없습니다: {error}")
    binary = find_binary()
    if not binary.exists():
        return _fail(f"agent-desktop 바이너리가 없습니다: {binary}")
    if args.baseline and not task.baseline_target:
        return _fail("작업 파일에 baseline_target이 없습니다")
    if not args.baseline and not os.environ.get("OPENAI_API_KEY"):
        return _fail("OPENAI_API_KEY가 없습니다. 환경변수나 .env에 넣어 주세요")
    models = None
    if not args.baseline:
        try:
            models = build_models(task, settings, os.environ, make_client=make_client)
        except ModelSetupError as error:
            return _fail(str(error))
        print(start_line(models))

    session = _start_session(binary)
    if session is None:
        return _fail("agent-desktop 세션을 시작하지 못했습니다")
    viewer = start_viewer(RUNS_DIR) if args.view else None
    try:
        run = _ModelRun(args, task, system, models, workflows_root)
        return _run_recorded(run, binary, session, viewer)
    finally:
        _close_viewer(viewer)


def _conflict(args):
    """Why the arguments cannot be used together, or None."""
    if args.param and not args.workflow:
        return "--param은 --workflow와 함께만 씁니다"
    for mode, others in CONFLICTS.items():
        if not getattr(args, mode):
            continue
        given = [FLAG_NAMES[name] for name in others if getattr(args, name)]
        if given:
            return f"{FLAG_NAMES[mode]}는 {', '.join(given)}와(과) 함께 쓸 수 없습니다"
    return None


def _start_session(binary):
    started = datetime.now().strftime("%Y%m%d-%H%M%S")
    return start_session(str(binary), f"llm-agent {started}")


def _close_viewer(viewer):
    if viewer is not None:
        viewer.shutdown()
        viewer.server_close()


def start_viewer(runs_dir):
    """Serves the run records read-only on a free loopback port, or warns and returns None."""
    try:
        server = make_server(runs_dir, secrets.token_urlsafe(32), port=0)
    except OSError as error:
        print(f"경고: 뷰어를 열지 못했습니다({error}). 실행은 계속합니다", file=sys.stderr)
        return None
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
    return server


def show_run(viewer, run_id):
    url = f"http://127.0.0.1:{viewer.server_port}/?t={viewer.token}#run={run_id}"
    print(f"뷰어 주소: {url}")
    try:
        open_browser(url)
    except webbrowser.Error as error:
        print(f"경고: 브라우저를 열지 못했습니다({error}). 위 주소를 직접 여세요", file=sys.stderr)


def wait_for_enter():
    input()


def hold_viewer():
    """Keeps the viewer up until Enter or Ctrl+C, only when someone can answer."""
    if not is_interactive():
        return
    print("뷰어를 닫으려면 Enter(또는 Ctrl+C)를 누르세요.")
    try:
        wait_for_enter()
    except (EOFError, KeyboardInterrupt):
        pass


def started_by(args):
    """How this run was started, for run.json: baseline, run.py --view or run.py."""
    if args.baseline:
        return "baseline"
    return "run.py --view" if args.view else "run.py"


class _ModelRun:
    """What a model or baseline run needs once its arguments are checked. `models` is None for a
    baseline run, which calls no model."""

    def __init__(self, args, task, system, models, workflows_root):
        self.args = args
        self.task = task
        self.system = system
        self.models = models
        self.workflows_root = workflows_root


def _run_recorded(run, binary, session, viewer):
    args, task, system, models = run.args, run.task, run.system, run.models
    desktop = DesktopClient(str(binary), session)
    meta = {
        "goal": task.goal,
        "exe": task.exe,
        "model": "baseline" if args.baseline else task.model,
        "permission_level": task.permission_level,
        "max_steps": task.max_steps,
        "started_by": started_by(args),
        "system": system,
        "code": current_code(),
    }
    if models is not None:
        meta["vision"] = models.vision is not None
    try:
        recorder = RunRecorder(RUNS_DIR, desktop, meta)
    except OSError as error:
        return _fail(f"실행 기록 폴더를 만들지 못했습니다: {error}")
    if viewer is not None:
        show_run(viewer, recorder.run_id)
    captures = recorder.path / "captures"
    made = []
    if args.baseline:
        toolbox = Toolbox(desktop, task, confirm=confirm, observer=recorder, captures_dir=captures)
        result = run_baseline(toolbox, task)
    else:
        store = KnowledgeStore(knowledge_root(args.knowledge_dir, task.knowledge_dir), system)
        asker = TerminalAsker() if is_interactive() else NoAsker()
        toolbox = Toolbox(desktop, task, confirm=confirm, observer=recorder, asker=asker, knowledge=store,
                          captures_dir=captures, vision=models.vision)
        gate = TerminalGate() if args.approve else None
        register = functools.partial(register_after_run, store=store, run_id=recorder.run_id)
        log = make_logger(recorder)
        result = run_agent(models.text, toolbox, task, log, gate=gate, register=register)
        workflows = WorkflowStore(run.workflows_root, system)
        result = after_run(models.text, result, recorder.path, workflows, _keeping_workflows(log, made))
    return _finish_recorded(recorder, result, (binary, session), viewer, made)


def _keeping_workflows(log, made):
    """`log`, also keeping each workflow event in `made` for the line at the end."""
    def keep(event):
        if event.get("type") == "workflow":
            made.append(event)
        log(event)

    return keep


def _finish_recorded(recorder, result, cli, viewer, made=()):
    binary, session = cli
    recorder.finish(result)
    print(f"결과: {result.status} (스텝 {result.steps}, 토큰 {result.tokens}) - {result.reason}")
    for outcome in made:
        print(workflow_line(outcome))
    print(f"기록: {recorder.path}")
    print(f"나중에 보기: python view.py --open {recorder.run_id}")
    print(f"trace: {binary} --session {session} trace export")
    print_summary(recorder.path)
    if viewer is not None:
        hold_viewer()
    return EXIT_CODES.get(result.status, 1)


def workflow_line(outcome):
    """The one line that says what making a workflow gave: its id and counts, or its code."""
    if outcome.get("created"):
        return f"workflow: {outcome.get('id')} (미확인, 단계 {outcome.get('steps')}, 파라미터 {outcome.get('params')})"
    return f"workflow를 만들지 못했습니다: {outcome.get('code')}"


def print_summary(run_dir):
    """Prints the run's summary and leaves it as summary.txt; a failure only warns."""
    lines, problem = write_summary(run_dir)
    if lines:
        print("\n".join(lines))
    if problem:
        print(f"경고: 요약을 {'남기지' if lines else '만들지'} 못했습니다({problem})", file=sys.stderr)


def _list_workflows(args, settings, root):
    """Prints the system's readable workflows: id, status, counts, parameters, fixed values, goal."""
    try:
        system = _chosen_system(args, settings)
    except (TaskError, KnowledgeError) as error:
        return _fail(str(error))
    entries = WorkflowStore(root, system).entries()
    if not entries:
        print(f"workflow가 없습니다 (시스템: {system})")
        return 0
    for workflow in entries:
        params = ", ".join(f"{name}({spec['kind']})" for name, spec in workflow["params"].items()) or "없음"
        fixed = ", ".join(item["name"] for item in workflow["fixed"]) or "없음"
        print(f"{workflow['id']} {workflow['status']} · 통과 {workflow['passes']} · 실패 {workflow['failures']}"
              f" · 파라미터 {params} · 바꿀 수 없는 값 {fixed}")
        print(f"  {workflow['goal']}")
    return 0


def _chosen_system(args, settings):
    """The system named by --system, else by the settings, else by the exe the settings choose."""
    if args.system:
        return system_name(args.system, "")
    exe = args.exe or system_exe(settings, settings.get("system")) or settings.get("exe")
    if not settings.get("system") and not exe:
        raise TaskError("--system으로 시스템을 고르거나 --exe를 주세요")
    return system_name(settings.get("system"), exe or "")


def _make_from_past(args, settings, root):
    """Makes a workflow from a past run's record with the text model. The record is only read.
    A missing OPENAI_API_KEY is refused by text_responder before any client exists."""
    run_dir = _past_run(args.make_workflow)
    if run_dir is None:
        return _fail(f"실행 기록이 없습니다: {args.make_workflow}")
    run, _ = read_records(run_dir)
    try:
        system = system_name(run.get("system"), run.get("exe") or "")
        task = task_from_args(run.get("goal") or "workflow", _absolute(run.get("exe"), run_dir),
                              run.get("permission_level") or 0, defaults=settings, environ=os.environ)
        model = text_responder(task, os.environ, make_client)
    except (KnowledgeError, TaskError, ModelSetupError) as error:
        return _fail(str(error))
    outcome, tokens = make_workflow(model, run_dir, WorkflowStore(root, system), log=lambda _event: None)
    print(workflow_line(outcome))
    print(f"토큰: {tokens}")
    return 0 if outcome.get("created") else 1


def _past_run(which):
    """The folder of a past run with a run.json: the newest for latest, else the one named."""
    if which == LATEST:
        folders = sorted(path for path in RUNS_DIR.glob("*") if (path / "run.json").is_file()) \
            if RUNS_DIR.is_dir() else []
        return folders[-1] if folders else None
    if Path(which).name != which:
        return None
    folder = RUNS_DIR / which
    return folder if (folder / "run.json").is_file() else None


def _absolute(exe, fallback):
    """The recorded exe when it is an absolute path, else `fallback`: making a workflow never
    starts the app, it only needs a task to pick the text model."""
    return exe if isinstance(exe, str) and PureWindowsPath(exe).is_absolute() else str(fallback)


def _replay(args, settings, root):
    """Replays a workflow: every value is checked before a record folder or a session exists."""
    given = _given_values(args.param)
    if isinstance(given, str):
        return _fail(given)
    exe = args.exe or system_exe(settings, args.system) or system_exe(settings, settings.get("system")) \
        or settings.get("exe")
    if not exe:
        return _fail(_no_exe_reason(args, settings))
    try:
        system = system_name(args.system or settings.get("system"), exe)
    except KnowledgeError as error:
        return _fail(f"시스템 이름을 쓸 수 없습니다: {error}")
    store = WorkflowStore(root, system)
    try:
        workflow = _stored_workflow(store, root, args.workflow)
        resolve_values(workflow, given, VALUE_CHECK_RUN_ID)
        task = apply_ready(task_from_args(workflow["goal"], exe, workflow["permission_level"],
                                          defaults=settings, environ=os.environ), settings)
    except WorkflowError as error:
        return _fail(f"{error.code}: {error}")
    except TaskError as error:
        return _fail(str(error))
    if not Path(task.exe).exists():
        return _fail(f"실행 파일이 없습니다: {task.exe}")
    binary = find_binary()
    if not binary.exists():
        return _fail(f"agent-desktop 바이너리가 없습니다: {binary}")
    session = _start_session(binary)
    if session is None:
        return _fail("agent-desktop 세션을 시작하지 못했습니다")
    viewer = start_viewer(RUNS_DIR) if args.view else None
    try:
        return _replay_recorded(_Replay(task, system, store, workflow, given), binary, session, viewer)
    finally:
        _close_viewer(viewer)


def _given_values(params):
    """{name: value} from the --param arguments, or the reason they cannot be read."""
    given = {}
    for item in params:
        name, sep, value = item.partition("=")
        name = name.strip()
        if not sep or not name:
            return f"--param은 이름=값 꼴로 씁니다: {name or '(이름 없음)'}"
        if name in given:
            return f"--param {name}을(를) 두 번 주었습니다"
        given[name] = value
    return given


def _stored_workflow(store, root, workflow_id):
    """The workflow of this system, or WorkflowError: SYSTEM_MISMATCH when only another system
    has that id, WORKFLOW_NOT_FOUND when none does."""
    workflow = store.get(workflow_id)
    if workflow is not None:
        return workflow
    others = [path for path in Path(root).glob(f"*/{workflow_id}.yaml") if path.parent != store.folder] \
        if Path(workflow_id).name == workflow_id else []
    if others:
        raise WorkflowError("SYSTEM_MISMATCH", f"이 workflow는 다른 시스템의 것입니다(지금 시스템: {store.system})")
    raise WorkflowError("WORKFLOW_NOT_FOUND", f"그 id의 workflow가 없습니다 (시스템: {store.system})")


class _Replay:
    """A replay whose workflow and values passed every check before it starts."""

    def __init__(self, task, system, store, workflow, given):
        self.task = task
        self.system = system
        self.store = store
        self.workflow = workflow
        self.given = given


def _replay_recorded(replay, binary, session, viewer):
    task = replay.task
    desktop = DesktopClient(str(binary), session)
    meta = {"goal": task.goal, "exe": task.exe, "model": WORKFLOW_RUN, "permission_level": task.permission_level,
            "max_steps": task.max_steps, "started_by": WORKFLOW_RUN, "system": replay.system, "code": current_code()}
    try:
        recorder = RunRecorder(RUNS_DIR, desktop, meta)
    except OSError as error:
        return _fail(f"실행 기록 폴더를 만들지 못했습니다: {error}")
    if viewer is not None:
        show_run(viewer, recorder.run_id)
    try:
        values = resolve_values(replay.workflow, replay.given, recorder.run_id)
    except WorkflowError as error:
        result = RunResult("stopped", 0, error.code)
    else:
        toolbox = Toolbox(desktop, task, confirm=confirm, observer=recorder, asker=NoAsker(),
                          captures_dir=recorder.path / "captures", gated=False)
        result = run_workflow(toolbox, task, replay.workflow, values, log=make_logger(recorder))
        try:
            record_outcome(replay.store, replay.workflow, values, result)
        except (WorkflowError, OSError) as error:
            print(f"경고: workflow의 횟수를 고치지 못했습니다({type(error).__name__})", file=sys.stderr)
    return _finish_recorded(recorder, result, (binary, session), viewer)


def _resolve_task(args, settings):
    """The task from the command line, the task file and the settings, in that order of weight,
    with the settings' ready signal when its exe is one the settings name."""
    return apply_ready(_chosen_task(args, settings), settings)


def _chosen_task(args, settings):
    """The exe comes from --exe, else the --system entry of `systems`, else the task file, else the
    entry the settings' system names, else the settings' exe. A --system that names no entry is
    only the knowledge name."""
    named = system_exe(settings, args.system)
    if args.task is not None:
        if args.exe:
            raise TaskError("작업 파일과 --exe는 함께 쓸 수 없습니다. 작업 파일의 exe를 고쳐 주세요")
        task = load_task(args.task, defaults=settings, environ=os.environ)
        if named:
            task = replace(task, exe=named)
        return replace(task, goal=args.goal) if args.goal else task
    exe = args.exe or named or system_exe(settings, settings.get("system")) or settings.get("exe")
    if not exe:
        raise TaskError(_no_exe_reason(args, settings))
    if not args.goal:
        if args.exe:
            raise TaskError("--exe에는 --goal로 지시를 함께 주세요")
        raise TaskError("작업 파일이 없으면 --goal로 지시를 주세요(실행 파일은 설정 파일에서 고릅니다)")
    if args.baseline:
        raise TaskError("--baseline은 성공 조건이 있는 작업 파일에서만 쓸 수 있습니다")
    return task_from_args(args.goal, exe, args.level, defaults=settings, environ=os.environ)


def _no_exe_reason(args, settings):
    names = system_names(settings)
    if not names:
        return "작업 파일이나 --exe를 지정해 주세요"
    choices = ", ".join(names)
    if args.system:
        return (f"--system {args.system}은(는) 설정 파일의 systems에 없습니다(있는 이름: {choices}). "
                "그 이름을 쓰거나 작업 파일이나 --exe를 지정해 주세요")
    return f"--system으로 시스템을 고르거나(설정 파일의 systems: {choices}) 작업 파일이나 --exe를 지정해 주세요"


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    sys.exit(main())
