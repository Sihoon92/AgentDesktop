"""Company system capture session and the block it hands home.

    python survey.py --system <name>            capture session: keep it open while using the app
    python survey.py --block <session|latest>   check labels.yaml and print the block again
    python survey.py --read-block <file>        at home: check a copied block and explain its codes
    python survey.py --write-docs               regenerate docs/company-survey/hypotheses.md and codes.md

Console output holds codes, counts and folder names only.
"""

import argparse
import sys
from pathlib import Path

from agent.git_state import code_state
from agent.labels import LabelError
from agent.survey_block import explain, parse_block
from agent.survey_report import CODES_DOC, HYPOTHESES_DOC, codes_markdown, finish_session, hypotheses_markdown

HERE = Path(__file__).resolve().parent
CAPTURES = "captures"
LATEST = "latest"


def _parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--block", metavar="SESSION", help="Session folder name under captures/, or latest")
    mode.add_argument("--read-block", metavar="FILE", help="A file holding a copied block")
    mode.add_argument("--write-docs", action="store_true", help="Write the generated hypothesis and code documents")
    parser.add_argument("--exe", help="Absolute path of the target executable or shortcut")
    parser.add_argument("--system", help="System name; defaults to the settings' system, then the exe's name")
    parser.add_argument("--settings", help="Settings file; defaults to AGENT_SETTINGS, then settings.local.yaml")
    parser.add_argument("--profile", help="Load .env.<profile> before .env (default: AGENT_PROFILE)")
    parser.add_argument("--profile-dir", help="System profile folder")
    parser.add_argument("--skip", action="append", choices=("llm",), default=[], help="Skip the LLM checks")
    parser.add_argument("--show-names", action="store_true",
                        help="Keep unknown class and module names in summary.md on this PC only")
    return parser


def _safe_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _session_folder(name):
    root = HERE / CAPTURES
    if name == LATEST:
        folders = sorted(p for p in root.glob("*") if p.is_dir()) if root.is_dir() else []
        return folders[-1] if folders else None
    folder = root / name
    if Path(name).name != name or not folder.is_dir():
        return None
    return folder


def _block(name):
    folder = _session_folder(name)
    if folder is None:
        print(f"세션 없음: {name} (captures 폴더 안의 세션 이름이나 latest)")
        return 2
    try:
        lines = finish_session(folder, now=code_state(HERE))
    except LabelError as error:
        print(str(error))
        print("labels.yaml의 그 줄을 코드표(docs/company-survey/codes.md)의 코드로 고쳐 다시 실행하세요.")
        return 1
    except FileNotFoundError:
        print(f"세션 사실 없음: {folder.name}")
        return 2
    print(f"세션: {folder.name}")
    print()
    for line in lines:
        print(line)
    return 0


def _read_block(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        print("파일을 읽지 못함")
        return 2
    lines = parse_block(text)
    if not lines:
        print("[전달]로 시작하는 블록을 찾지 못함")
        return 1
    for line in explain(lines):
        print(line)
    typos = sum(not line.ok for line in lines)
    print()
    print(f"줄 {len(lines)} · 오타? {typos}")
    return 0


def _write_docs():
    for relative, text in ((HYPOTHESES_DOC, hypotheses_markdown()), (CODES_DOC, codes_markdown())):
        path = HERE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"씀: {relative.as_posix()}")
    return 0


def main(argv=None) -> int:
    _safe_output()
    args = _parser().parse_args(argv)
    if args.write_docs:
        return _write_docs()
    if args.read_block is not None:
        return _read_block(args.read_block)
    if args.block is not None:
        return _block(args.block)
    from agent.survey_session import run_session

    return run_session(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    sys.exit(main())
