"""Opens the local step viewer to look at past runs; it can also start new runs from the browser.

`python view.py --open latest` (or a run id) opens one run directly. Three options work without
the viewer: `--prune` lists old runs to delete (and deletes them with --yes),
`--export <run_id|latest> <out.html>` writes one run as a file that opens without a server, and
`--summary <run_id|latest>` prints a run's summary, which holds no screen content.

The viewer marks itself in the runs folder (`.viewer.json`), so opening it again takes over an
idle old viewer instead of sharing its port, and `--stop` shuts down the one left running.
Ctrl+C (and Ctrl+Break) stops a run in progress before closing; a second press closes at once.
"""

import argparse
import functools
import os
import re
import secrets
import signal
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from agent.envfiles import ProfileError, load_env
from agent.knowledge import KNOWLEDGE_ENV, knowledge_root
from agent.llm import ModelSetupError, build_models, make_client
from agent.settings import SettingsError, load_settings, settings_path
from run import ENV_FILES, RUNS_DIR, find_binary
from viewer.export import write_export
from viewer.history import RunIndex, is_run_id
from viewer.instance import (
    INSTANCE_FILE,
    clear_instance,
    instance_alive,
    open_viewer,
    read_instance,
    request_shutdown,
    write_instance,
)
from viewer.retention import delete_plan, format_plan, plan_prune
from viewer.run_manager import RunManager, RunStartFailed
from viewer.server import SHUTDOWN_WAIT_S, STATIC_DIR, make_server, summary_lines

DAYS = re.compile(r"([0-9]+)d")
COUNT = re.compile(r"[0-9]+")
EXPORT_WARNING = "이 파일에는 앱 화면과 UI 텍스트가 들어 있습니다. 공유 전에 확인하세요"


def openai_model(task, settings=None):
    """The models (agent.llm.Models) for a run started from the viewer; RunStartFailed says why not."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RunStartFailed("OPENAI_API_KEY가 없습니다. 환경변수나 .env에 넣고 뷰어를 다시 켜 주세요")
    try:
        return build_models(task, settings, os.environ, make_client=make_client)
    except ModelSetupError as error:
        raise RunStartFailed(str(error)) from error


def build_run_manager(runs_dir, default_exe, knowledge_dir=None, task_defaults=None, workflow_dir=None,
                      profile_dir=None):
    """Returns a RunManager, or None with the reason when this viewer can only replay.
    task_defaults (the settings file) give new runs their model and the other task keys,
    with OPENAI_MODEL below them. The workflow folder is workflow_dir, else the settings'
    workflow_dir, else AGENT_WORKFLOW_DIR, else the default; the profile folder is profile_dir,
    else the settings' profile_dir, else AGENT_PROFILE_DIR, else the default."""
    binary = find_binary()
    if not binary.exists():
        return None, f"agent-desktop 바이너리가 없어 기록 보기만 합니다: {binary}"
    model_factory = functools.partial(openai_model, settings=task_defaults)
    return RunManager(runs_dir, binary, default_exe, model_factory, knowledge_dir=knowledge_dir,
                      task_defaults=task_defaults, environ=os.environ, workflow_dir=workflow_dir,
                      profile_dir=profile_dir, print_progress=True), None


def open_fragment(runs_dir, value):
    """Returns (address fragment, warning) for --open: a run folder, an old log, or the list."""
    if value is None:
        return "", None
    runs_dir = Path(runs_dir)
    if value == "latest":
        latest = RunIndex(runs_dir).latest()
        if latest is None:
            return "", "경고: 열 실행이 없어 목록을 엽니다"
        return _fragment(latest["kind"], latest["run_id"]), None
    if is_run_id(value):
        if (runs_dir / value).is_dir():
            return _fragment("run", value), None
        if (runs_dir / f"{value}.jsonl").is_file():
            return _fragment("legacy", value), None
    return "", f"경고: 실행 {value!r}을(를) 찾지 못해 목록을 엽니다"


def _fragment(kind, run_id):
    return f"#{'legacy' if kind == 'legacy' else 'run'}={run_id}"


def days(text):
    """argparse type for --older-than: '30d' is 30 days."""
    match = DAYS.fullmatch(text)
    if match is None:
        raise argparse.ArgumentTypeError(f"기간은 30d처럼 일 수로 적어 주세요: {text!r}")
    return timedelta(days=int(match.group(1)))


def count(text):
    """argparse type for --keep: a whole number, 0 or more."""
    if COUNT.fullmatch(text) is None:
        raise argparse.ArgumentTypeError(f"0 이상의 정수를 적어 주세요: {text!r}")
    return int(text)


def run_is_active(index):
    """is_active for plan_prune: a run whose run.json says running and that the viewer would not
    show as interrupted (its process lives and its heartbeat is fresh)."""

    def is_active(run_dir):
        summary = index.run(Path(run_dir).name)
        return summary is not None and summary["status"] == "running"

    return is_active


def prune(runs_dir, older_than, keep_newest, delete):
    """Prints the prune plan; with delete, deletes it. Returns the exit code."""
    plan = plan_prune(runs_dir, older_than=older_than, keep_newest=keep_newest, now=datetime.now(timezone.utc),
                      is_active=run_is_active(RunIndex(runs_dir)))
    print(format_plan(plan, will_delete=delete))
    if not delete:
        return 0
    errors = delete_plan(runs_dir, plan)
    print(f"지운 항목: {len(plan.candidates) - len(errors)}개")
    for error in errors:
        print(f"지우지 못함: {error}", file=sys.stderr)
    return 1 if errors else 0


def export(runs_dir, value, out_path):
    """Writes one run (or the newest) to out_path. Returns the exit code."""
    run_path, problem = _export_source(runs_dir, value)
    if problem is None:
        try:
            size = write_export(run_path, STATIC_DIR, out_path, datetime.now(timezone.utc))
        except FileExistsError:
            problem = f"이미 있는 파일이라 덮어쓰지 않습니다: {out_path}"
        except (ValueError, OSError) as error:
            problem = str(error)
    if problem is not None:
        print(f"오류: {problem}", file=sys.stderr)
        return 1
    print(f"내보낸 파일: {Path(out_path).resolve()} ({size:,}바이트)")
    print(EXPORT_WARNING)
    return 0


def show_summary(runs_dir, value):
    """Prints the summary of one run (or the newest), rebuilt from its records. Returns the exit code."""
    index = RunIndex(runs_dir)
    run_id = value
    if value == "latest":
        latest = index.latest()
        if latest is None:
            print("오류: 요약할 실행이 없습니다", file=sys.stderr)
            return 1
        if latest["kind"] == "legacy":
            print(f"오류: 가장 최근 기록 {latest['run_id']}은(는) 옛 로그라 요약할 수 없습니다", file=sys.stderr)
            return 1
        run_id = latest["run_id"]
    if not is_run_id(run_id) or not (runs_dir / run_id).is_dir():
        print(f"오류: 실행 {value!r}을(를) 찾지 못했습니다", file=sys.stderr)
        return 1
    try:
        lines = summary_lines(index, runs_dir / run_id)
    except OSError as error:
        print(f"오류: 요약을 만들지 못했습니다({type(error).__name__})", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


def stop_viewer(runs_dir):
    """Asks the viewer marked in the runs folder to stop its run and shut down. A marker whose
    process is gone (or that cannot be read) is removed. Returns the exit code."""
    info = read_instance(runs_dir)
    if info is None or not instance_alive(info):
        try:
            (runs_dir / INSTANCE_FILE).unlink(missing_ok=True)
        except OSError:
            pass
        print("떠 있는 뷰어가 없습니다")
        return 0
    status, body = request_shutdown(info, stop_run=True)
    if status != 202:
        reason = f"응답 {status}" if status is not None else "연결하지 못함"
        print(f"오류: 포트 {info['port']}의 뷰어가 종료 요청을 받지 않았습니다({reason})", file=sys.stderr)
        return 1
    running = " 진행 중인 실행을 멈춘 뒤 닫힙니다" if body.get("run_stopping") else ""
    print(f"포트 {info['port']}의 뷰어를 종료합니다.{running}")
    return 0


def wind_down(run_manager):
    """After Ctrl+C: stops the run in progress and waits for its records, up to SHUTDOWN_WAIT_S.
    A second Ctrl+C ends only the waiting. The target app stays open either way."""
    if run_manager is None or run_manager.active() is None:
        return
    print("실행을 멈추는 중입니다. 바로 끄려면 Ctrl+C를 한 번 더 누르세요")
    try:
        finished = run_manager.stop_and_wait(SHUTDOWN_WAIT_S)
    except KeyboardInterrupt:
        print("기다리지 않고 끕니다. 그 실행은 나중에 끊김으로 보입니다")
        return
    if not finished:
        print(f"실행이 {SHUTDOWN_WAIT_S}초 안에 끝나지 않아 기다리지 않고 끕니다")


def _break_as_interrupt():
    """Makes Ctrl+Break raise KeyboardInterrupt like Ctrl+C. Returns how to put it back."""
    if not hasattr(signal, "SIGBREAK"):
        return lambda: None
    previous = signal.signal(signal.SIGBREAK, signal.default_int_handler)
    return lambda: signal.signal(signal.SIGBREAK, previous)


def _export_source(runs_dir, value):
    """(path to export, None), or (None, why not)."""
    if value == "latest":
        latest = RunIndex(runs_dir).latest()
        if latest is None:
            return None, "내보낼 실행이 없습니다"
        if latest["kind"] == "legacy":
            return None, f"가장 최근 기록 {latest['run_id']}은(는) 옛 로그라 내보낼 수 없습니다"
        value = latest["run_id"]
    if not is_run_id(value):
        return None, f"실행 {value!r}을(를) 찾지 못했습니다"
    if (runs_dir / value).is_dir():
        return runs_dir / value, None
    if (runs_dir / f"{value}.jsonl").is_file():
        return runs_dir / f"{value}.jsonl", None
    return None, f"실행 {value!r}을(를) 찾지 못했습니다"


def _check_modes(parser, args):
    if args.stop and (args.summary or args.prune or args.export or args.open):
        parser.error("--stop은 --summary·--prune·--export·--open과 함께 쓸 수 없습니다")
    if args.summary and (args.prune or args.export or args.open):
        parser.error("--summary는 --prune·--export·--open과 함께 쓸 수 없습니다")
    if args.prune and args.export:
        parser.error("--prune과 --export는 함께 쓸 수 없습니다")
    if (args.prune or args.export) and args.open:
        parser.error("--prune·--export는 --open과 함께 쓸 수 없습니다")
    if not args.prune and (args.older_than is not None or args.keep is not None or args.yes):
        parser.error("--older-than, --keep, --yes는 --prune과 함께만 씁니다")
    if args.prune and (args.older_than is None) == (args.keep is None):
        parser.error("--prune에는 --older-than이나 --keep 중 하나만 주세요")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (0 picks a free one)")
    parser.add_argument("--runs", type=Path, default=RUNS_DIR, help="Folder that holds the run records")
    parser.add_argument("--exe", help="Default absolute path of the app that new runs control (default: exe in the settings file)")
    parser.add_argument("--knowledge-dir", type=Path,
                        help=f"Knowledge folder to show and to give viewer-started runs (default: the settings file's knowledge_dir, ${KNOWLEDGE_ENV} or knowledge/)")
    parser.add_argument("--workflow-dir", type=Path,
                        help="Workflow folder to list and replay from (default: the settings file's workflow_dir, "
                             "$AGENT_WORKFLOW_DIR or workflows/)")
    parser.add_argument("--profile-dir", type=Path,
                        help="Folder of the per-system profiles to register and to give viewer-started runs "
                             "(default: the settings file's profile_dir, $AGENT_PROFILE_DIR or profiles/)")
    parser.add_argument("--no-browser", action="store_true", help="Print the address without opening a browser")
    parser.add_argument("--open", metavar="RUN_ID", help="Open this run (or 'latest') instead of the list")
    parser.add_argument("--prune", action="store_true", help="List runs to delete; nothing is deleted without --yes")
    parser.add_argument("--older-than", type=days, metavar="Nd", help="With --prune: runs older than N days")
    parser.add_argument("--keep", type=count, metavar="N", help="With --prune: all but the newest N runs")
    parser.add_argument("--yes", action="store_true", help="With --prune: delete for good (no recycle bin)")
    parser.add_argument("--export", nargs=2, metavar=("RUN_ID", "OUT_HTML"),
                        help="Write this run (or 'latest') as one HTML file that opens without a server")
    parser.add_argument("--summary", metavar="RUN_ID",
                        help="Print this run's (or 'latest') summary of counts and codes, with no screen content")
    parser.add_argument("--stop", action="store_true",
                        help="Shut down the viewer left running for this runs folder, stopping its run first")
    parser.add_argument("--profile", help="Load .env.<profile> before .env (default: AGENT_PROFILE)")
    args = parser.parse_args(argv)
    _check_modes(parser, args)
    runs_dir = args.runs.resolve()
    if args.prune:
        return prune(runs_dir, args.older_than, args.keep, args.yes)
    if args.export:
        return export(runs_dir, args.export[0], Path(args.export[1]))
    if args.summary:
        return show_summary(runs_dir, args.summary)
    if args.stop:
        return stop_viewer(runs_dir)
    try:
        profile = load_env(args.profile, ENV_FILES, loader=lambda path: load_dotenv(path))
    except ProfileError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2
    if profile:
        print(f"프로필: {profile}")
    try:
        settings = load_settings()
    except SettingsError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2
    knowledge_dir = knowledge_root(args.knowledge_dir, settings.get("knowledge_dir"))
    workflow_option = {"workflow_dir": args.workflow_dir} if args.workflow_dir else {}
    if args.profile_dir:
        workflow_option["profile_dir"] = args.profile_dir
    run_manager, notice = build_run_manager(runs_dir, args.exe or settings.get("exe"), knowledge_dir=knowledge_dir,
                                            task_defaults=settings, **workflow_option)
    token = secrets.token_urlsafe(32)

    def make(port):
        return make_server(runs_dir, token, port=port, run_manager=run_manager,
                           knowledge_dir=knowledge_dir, allow_shutdown=True)

    try:
        server, takeover = open_viewer(runs_dir, args.port, make)
    except OSError as error:
        print(f"오류: 127.0.0.1:{args.port}에서 서버를 열지 못했습니다: {error}", file=sys.stderr)
        return 2
    if takeover:
        print(takeover)
    try:
        write_instance(runs_dir, server.server_port, token)
    except OSError as error:
        print(f"경고: 뷰어 표시 파일을 쓰지 못해 다음에 이 뷰어를 알아보지 못합니다({type(error).__name__})",
              file=sys.stderr)
    fragment, warning = open_fragment(runs_dir, args.open)
    if warning:
        print(warning, file=sys.stderr)
    url = f"http://127.0.0.1:{server.server_port}/?t={token}{fragment}"
    print(f"뷰어 주소: {url}")
    print(f"기록 폴더: {runs_dir}")
    print(f"지식 폴더: {knowledge_dir}")
    if getattr(run_manager, "workflow_root", None) is not None:
        print(f"workflow 폴더: {run_manager.workflow_root}")
    if getattr(run_manager, "profile_root", None) is not None:
        print(f"프로파일 폴더: {run_manager.profile_root}")
    if settings:
        print(f"설정 파일: {settings_path()} ({', '.join(settings)})")
    if notice:
        print(notice)
    print("끝내려면 Ctrl+C를 누르거나 뷰어의 종료 버튼을 누르세요.")
    if not args.no_browser:
        webbrowser.open(url)
    restore_break = _break_as_interrupt()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        try:
            wind_down(run_manager)
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
        clear_instance(runs_dir, token)
        restore_break()
        close_registration(run_manager)
    return 0


def close_registration(run_manager):
    """Removes the pictures the registration screen read; they never outlive the viewer."""
    editor = getattr(run_manager, "profile_editor", None)
    if editor is not None:
        editor.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    sys.exit(main())
