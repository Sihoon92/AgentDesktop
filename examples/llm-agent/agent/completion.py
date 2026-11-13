"""Runs the checks of a completion entry: did the work the model says it finished really land?

The kinds of check are code; which checks a task needs is data (a knowledge entry). A file
check reads the file system itself rather than trusting the path the model reports.
"""

from fnmatch import fnmatch
from pathlib import Path

CLOCK_SLACK_S = 2.0


def check_file(path, started_at, pattern=None):
    """Met when `path` is a non-empty file written after the run started (with a little clock
    slack) whose name fits `pattern`. A folder with a pattern stands for its newest match."""
    if not path:
        return _unmet("저장 경로 없음")
    target = Path(path)
    if not target.exists():
        return _unmet(f"파일 없음: {target}")
    if target.is_dir():
        if not pattern:
            return _unmet(f"파일이 아니라 폴더임: {target}")
        newest = _newest_match(target, pattern)
        if newest is None:
            return _unmet(f"{target} 폴더에 {pattern}에 맞는 파일 없음")
        target = newest
    if pattern and not _name_fits(target.name, pattern):
        return _unmet(f"파일 이름 {target.name}이 {pattern}에 맞지 않음")
    status = target.stat()
    if status.st_size <= 0:
        return _unmet(f"파일이 비어 있음: {target}")
    if status.st_mtime < started_at - CLOCK_SLACK_S:
        return _unmet(f"실행 전에 쓰인 파일임: {target}")
    return {"met": True, "detail": f"파일 확인: {target} ({status.st_size:,} bytes)"}


def run_checks(checks, *, verify_elements, saved_path, started_at, ask_human):
    """Runs the checks in order and stops at the first unmet one.

    `verify_elements(require_list)` returns {"met": bool, "unmet": ...}; `ask_human()` returns
    whether the person says the result is right. Either may be None when unavailable, which
    leaves its checks unmet. `by` is "human" when a person's answer decided the result.
    """
    if not checks:
        return {"met": False, "by": "harness", "detail": "확인 방법이 없음"}
    details = []
    asked_human = False
    for check in checks:
        kind = check.get("kind") if isinstance(check, dict) else None
        if kind == "human":
            asked_human = ask_human is not None
            result = _check_human(ask_human)
        elif kind == "file":
            result = _check_file_entry(check, saved_path, started_at)
        elif kind == "element":
            result = _check_elements(check.get("require"), verify_elements)
        else:
            result = _unmet(f"모르는 확인 방법: {kind}")
        if not result["met"]:
            return {"met": False, "by": "human" if asked_human else "harness", "detail": result["detail"]}
        details.append(result["detail"])
    return {"met": True, "by": "human" if asked_human else "harness", "detail": "; ".join(details)}


def _check_file_entry(check, saved_path, started_at):
    path = check.get("path") or saved_path
    if not path:
        return _unmet("saved_path가 필요함: 이 확인에는 경로가 없어 finish의 saved_path를 봄")
    return check_file(path, started_at, check.get("pattern"))


def _check_elements(require, verify_elements):
    if verify_elements is None:
        return _unmet("화면 요소를 확인할 수 없음")
    requirements = require if isinstance(require, list) else [require]
    result = verify_elements(requirements)
    if result.get("met"):
        return {"met": True, "detail": "화면 요소 확인"}
    return _unmet(f"화면에 없는 요소: {result.get('unmet')}")


def _check_human(ask_human):
    if ask_human is None:
        return _unmet("물어볼 사람이 없음")
    if ask_human():
        return {"met": True, "detail": "사람이 결과를 확인함"}
    return _unmet("사람이 결과가 맞지 않다고 답함")


def _newest_match(folder, pattern):
    matches = [path for path in folder.iterdir() if path.is_file() and _name_fits(path.name, pattern)]
    return max(matches, key=lambda path: path.stat().st_mtime, default=None)


def _name_fits(name, pattern):
    return fnmatch(name.lower(), pattern.lower())


def _unmet(detail):
    return {"met": False, "detail": detail}
