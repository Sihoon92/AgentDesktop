"""PreToolUse hook for Claude Code sessions started in `company/`: run, read and tidy only.

It blocks writing a file of this repository that Git does not ignore, writing anything under
`company/`, and shell commands that change the repository (git subcommands other than the
reading ones and `pull`), install packages, or build Rust. Input it cannot judge is blocked.
Standard library only; exit 2 with the reason on stderr blocks the tool call.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
SHELL_TOOLS = ("Bash", "PowerShell")
GIT_READING = {"pull", "status", "log", "diff", "show", "fetch", "rev-parse", "ls-files", "check-ignore", "blame",
               "describe", "shortlog", "grep", "version", "help"}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
INSTALLERS = {"pip", "pip3", "conda", "mamba", "npm", "pnpm", "yarn", "winget", "choco"}
INSTALL_WORDS = {"install", "i", "add", "update", "upgrade", "uninstall", "remove"}
PYTHONS = {"python", "python3", "py"}
BUILDERS = {"cargo", "rustup", "rustc"}
SEPARATORS = re.compile(r"&&|\|\||[;|\n&]")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _program(token):
    name = re.split(r"[\\/]", token)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _git_subcommand(words):
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in GIT_OPTIONS_WITH_VALUE else 1
    return words[index].lower() if index < len(words) else None


def _piece_reason(piece):
    try:
        words = shlex.split(piece.replace("\\", "/"), posix=True)
    except ValueError:
        return "명령을 해석하지 못함"
    while words and ASSIGNMENT.match(words[0]):
        words = words[1:]
    if not words:
        return None
    program, rest = _program(words[0]), words[1:]
    if program == "git":
        sub = _git_subcommand(rest)
        if sub is not None and sub not in GIT_READING:
            return f"저장소를 바꾸는 git 명령(git {sub})은 이 폴더에서 쓰지 않습니다"
    elif program in BUILDERS:
        return "Rust 빌드는 이 폴더에서 하지 않습니다"
    elif program in INSTALLERS and rest and rest[0].lower() in INSTALL_WORDS:
        return "패키지 설치는 이 폴더에서 하지 않습니다"
    elif program in PYTHONS and len(rest) >= 3 and rest[0] == "-m" and _program(rest[1]) in INSTALLERS \
            and rest[2].lower() in INSTALL_WORDS:
        return "패키지 설치는 이 폴더에서 하지 않습니다"
    return None


def _shell_reason(command):
    if not isinstance(command, str):
        return "명령을 읽지 못함"
    for piece in SEPARATORS.split(command):
        reason = _piece_reason(piece.strip())
        if reason:
            return reason
    return None


def _inside(path, folder):
    path, folder = os.path.normcase(str(path)), os.path.normcase(str(folder))
    return path == folder or path.startswith(folder.rstrip("\\/") + os.sep)


def _edit_reason(payload, key, repo_root, company_dir, is_ignored):
    raw = (payload.get("tool_input") or {}).get(key)
    if not isinstance(raw, str) or not raw:
        return "고칠 파일을 알 수 없음"
    path = Path(raw)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or company_dir) / path
    path = Path(os.path.abspath(path))
    if _inside(path, Path(os.path.abspath(company_dir))):
        return "company 폴더의 규칙과 훅은 고치지 않습니다"
    if _inside(path, Path(os.path.abspath(repo_root))) and not is_ignored(path):
        return "Git이 추적하는 파일은 고치지 않습니다. 현상은 captures의 notes.md에 적으세요"
    return None


def decide(payload: dict, repo_root: Path, company_dir: Path, is_ignored) -> str | None:
    """The reason to block this tool call, or None to allow it."""
    tool = payload.get("tool_name")
    if tool in EDIT_TOOLS:
        return _edit_reason(payload, EDIT_TOOLS[tool], repo_root, company_dir, is_ignored)
    if tool in SHELL_TOOLS:
        return _shell_reason((payload.get("tool_input") or {}).get("command"))
    return None


def _git(company_dir, *args):
    return subprocess.run(["git", *args], cwd=str(company_dir), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    company_dir = Path(__file__).resolve().parent.parent
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        top = _git(company_dir, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            raise RuntimeError("git rev-parse failed")
        repo_root = Path(top.stdout.strip())

        def is_ignored(path):
            return _git(company_dir, "check-ignore", "-q", str(path)).returncode == 0

        reason = decide(payload, repo_root, company_dir, is_ignored)
    except Exception as error:
        print(f"훅이 입력을 판단하지 못해 막음: {type(error).__name__}", file=sys.stderr)
        return 2
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
