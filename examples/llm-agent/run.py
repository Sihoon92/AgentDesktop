"""Runs a task file with the LLM agent, or with --baseline, the fixed no-model steps."""

import argparse
import functools
import os
import secrets
import sys
import threading
import webbrowser
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agent.approval import TerminalGate
from agent.ask import NoAsker, TerminalAsker
from agent.baseline import run_baseline
from agent.desktop import DesktopClient, start_session
from agent.envfiles import ProfileError, load_env
from agent.llm import ModelSetupError, build_models, make_client, start_line
from agent.knowledge import KnowledgeError, KnowledgeStore, knowledge_root, system_name
from agent.loop import RunResult, run_agent
from agent.recorder import RunRecorder
from agent.registration import register_after_run
from agent.settings import SettingsError, load_settings, settings_path
from agent.summary import current_code, write_summary
from agent.task import TaskError, load_task, task_from_args
from agent.tools import Toolbox
from viewer.server import make_server

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ENV_FILES = [HERE / ".env", REPO / ".env"]
RUNS_DIR = HERE / "runs"
EXIT_CODES = {"verified": 0, "confirmed": 0, "claimed": 3}
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
    parser.add_argument("--system", help="Knowledge name of the target system; defaults to the exe name")
    parser.add_argument("--knowledge-dir", help="Folder that holds the per-system knowledge")
    parser.add_argument("--profile", help="Load .env.<profile> before .env (default: AGENT_PROFILE)")
    args = parser.parse_args(argv)
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
        task = _resolve_task(args, settings)
    except (SettingsError, TaskError, OSError) as error:
        return _fail(str(error))
    if settings:
        print(f"설정 파일: {settings_path()} ({', '.join(settings)})")
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

    started = datetime.now().strftime("%Y%m%d-%H%M%S")
    session = start_session(str(binary), f"llm-agent {started}")
    if session is None:
        return _fail("agent-desktop 세션을 시작하지 못했습니다")
    viewer = start_viewer(RUNS_DIR) if args.view else None
    try:
        return _run_recorded(args, task, binary, session, viewer, system, models)
    finally:
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


def _run_recorded(args, task, binary, session, viewer, system, models):
    """`models` is None for a baseline run, which calls no model."""
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
        result = run_agent(models.text, toolbox, task, make_logger(recorder), gate=gate, register=register)
    recorder.finish(result)
    print(f"결과: {result.status} (스텝 {result.steps}, 토큰 {result.tokens}) - {result.reason}")
    print(f"기록: {recorder.path}")
    print(f"나중에 보기: python view.py --open {recorder.run_id}")
    print(f"trace: {binary} --session {session} trace export")
    print_summary(recorder.path)
    if viewer is not None:
        hold_viewer()
    return EXIT_CODES.get(result.status, 1)


def print_summary(run_dir):
    """Prints the run's summary and leaves it as summary.txt; a failure only warns."""
    lines, problem = write_summary(run_dir)
    if lines:
        print("\n".join(lines))
    if problem:
        print(f"경고: 요약을 {'남기지' if lines else '만들지'} 못했습니다({problem})", file=sys.stderr)


def _resolve_task(args, settings):
    """The task from the command line, the task file and the settings, in that order of weight.
    The settings' exe stands in for --exe only when there is no task file."""
    if args.task is not None:
        if args.exe:
            raise TaskError("작업 파일과 --exe는 함께 쓸 수 없습니다. 작업 파일의 exe를 고쳐 주세요")
        task = load_task(args.task, defaults=settings, environ=os.environ)
        return replace(task, goal=args.goal) if args.goal else task
    exe = args.exe or settings.get("exe")
    if not exe:
        raise TaskError("작업 파일이나 --exe를 지정해 주세요")
    if not args.goal:
        if args.exe:
            raise TaskError("--exe에는 --goal로 지시를 함께 주세요")
        raise TaskError("작업 파일이 없으면 --goal로 지시를 주세요(실행 파일은 설정 파일의 exe를 씁니다)")
    if args.baseline:
        raise TaskError("--baseline은 성공 조건이 있는 작업 파일에서만 쓸 수 있습니다")
    return task_from_args(args.goal, exe, args.level, defaults=settings, environ=os.environ)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    sys.exit(main())
