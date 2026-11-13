"""Opens the local step viewer to look at past runs; it can also start new runs from the browser.

`python view.py --open latest` (or a run id) opens one run directly. Two options work without
the viewer: `--prune` lists old runs to delete (and deletes them with --yes), and
`--export <run_id|latest> <out.html>` writes one run as a file that opens without a server.
"""

import argparse
import os
import re
import secrets
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from agent.knowledge import KNOWLEDGE_ENV, knowledge_root
from agent.llm import OpenAIResponder
from run import ENV_FILES, RUNS_DIR, find_binary
from viewer.export import write_export
from viewer.history import RunIndex, is_run_id
from viewer.retention import delete_plan, format_plan, plan_prune
from viewer.run_manager import RunManager, RunStartFailed
from viewer.server import STATIC_DIR, make_server

DAYS = re.compile(r"([0-9]+)d")
COUNT = re.compile(r"[0-9]+")
EXPORT_WARNING = "이 파일에는 앱 화면과 UI 텍스트가 들어 있습니다. 공유 전에 확인하세요"


def openai_model(task):
    if not os.environ.get("OPENAI_API_KEY"):
        raise RunStartFailed("OPENAI_API_KEY가 없습니다. 환경변수나 .env에 넣고 뷰어를 다시 켜 주세요")
    return OpenAIResponder(task.model, task.reasoning_effort)


def build_run_manager(runs_dir, default_exe, knowledge_dir=None):
    """Returns a RunManager, or None with the reason when this viewer can only replay."""
    binary = find_binary()
    if not binary.exists():
        return None, f"agent-desktop 바이너리가 없어 기록 보기만 합니다: {binary}"
    return RunManager(runs_dir, binary, default_exe, openai_model, knowledge_dir=knowledge_dir), None


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
    parser.add_argument("--exe", help="Default absolute path of the app that new runs control")
    parser.add_argument("--knowledge-dir", type=Path,
                        help=f"Knowledge folder to show and to give viewer-started runs (default: ${KNOWLEDGE_ENV} or knowledge/)")
    parser.add_argument("--no-browser", action="store_true", help="Print the address without opening a browser")
    parser.add_argument("--open", metavar="RUN_ID", help="Open this run (or 'latest') instead of the list")
    parser.add_argument("--prune", action="store_true", help="List runs to delete; nothing is deleted without --yes")
    parser.add_argument("--older-than", type=days, metavar="Nd", help="With --prune: runs older than N days")
    parser.add_argument("--keep", type=count, metavar="N", help="With --prune: all but the newest N runs")
    parser.add_argument("--yes", action="store_true", help="With --prune: delete for good (no recycle bin)")
    parser.add_argument("--export", nargs=2, metavar=("RUN_ID", "OUT_HTML"),
                        help="Write this run (or 'latest') as one HTML file that opens without a server")
    args = parser.parse_args(argv)
    _check_modes(parser, args)
    runs_dir = args.runs.resolve()
    if args.prune:
        return prune(runs_dir, args.older_than, args.keep, args.yes)
    if args.export:
        return export(runs_dir, args.export[0], Path(args.export[1]))
    for path in ENV_FILES:
        load_dotenv(path)
    knowledge_dir = knowledge_root(args.knowledge_dir)
    run_manager, notice = build_run_manager(runs_dir, args.exe, knowledge_dir=knowledge_dir)
    token = secrets.token_urlsafe(32)
    try:
        server = make_server(runs_dir, token, port=args.port, run_manager=run_manager,
                             knowledge_dir=knowledge_dir)
    except OSError as error:
        print(f"오류: 127.0.0.1:{args.port}에서 서버를 열지 못했습니다: {error}", file=sys.stderr)
        return 2
    fragment, warning = open_fragment(runs_dir, args.open)
    if warning:
        print(warning, file=sys.stderr)
    url = f"http://127.0.0.1:{server.server_port}/?t={token}{fragment}"
    print(f"뷰어 주소: {url}")
    print(f"기록 폴더: {runs_dir}")
    print(f"지식 폴더: {knowledge_dir}")
    if notice:
        print(notice)
    print("끝내려면 Ctrl+C를 누르세요.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
