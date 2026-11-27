"""Which commit the code is at and which tracked files under a folder differ from it.

The capture session records this at start, and `survey.py --block` records it again, so a
block shows when the code that made the captures was not the committed code, or when files
changed after the session. Only content changes count: line endings (through the repository's
`core.autocrlf`) and file modes are not changes. Untracked files do not count.
"""

import subprocess
from pathlib import Path
from typing import NamedTuple


class CodeState(NamedTuple):
    head: str | None
    changed: tuple[str, ...]


UNKNOWN = CodeState(None, ())


def _git(runner, folder, *args):
    try:
        done = runner(["git", *args], cwd=str(folder), capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def code_state(folder: Path, runner=subprocess.run) -> CodeState:
    """The short commit and the changed tracked files under `folder`, relative to it and sorted.
    Without git or outside a repository, CodeState(None, ())."""
    head = _git(runner, folder, "rev-parse", "--short=7", "HEAD")
    if head is None or not head.strip():
        return UNKNOWN
    listed = _git(runner, folder, "-c", "core.fileMode=false", "diff", "--name-only", "--relative", "HEAD", "--", ".")
    if listed is None:
        return CodeState(head.strip(), ())
    changed = sorted({line.strip() for line in listed.splitlines() if line.strip()})
    return CodeState(head.strip(), tuple(changed))
