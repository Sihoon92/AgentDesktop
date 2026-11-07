"""Plans and carries out pruning of the runs folder; nothing is deleted without an explicit call.

plan_prune() only reads. It picks runs older than a cutoff, or all but the newest N, and sets
aside what must never be deleted: runs marked with a `keep` file, active runs, runs whose
run.json cannot be read, links and junctions (and folders holding one), and names outside
the run id rule. delete_plan() deletes the planned candidates for good (no recycle bin),
checking each one again right before it goes.

Recorder run ids are UTC; old log names (<id>.jsonl) are this PC's local time, so both the
cutoff and the newest-first order compare the UTC moment each name stands for.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from viewer.history import is_run_id

LEGACY_SUFFIX = ".jsonl"
KEEP_FILE = "keep"
KEPT = "보관 표시가 있음(keep)"
ACTIVE = "실행 중"
UNREADABLE = "run.json을 읽을 수 없음"
LINK = "링크 또는 정션이라 건드리지 않음"
HOLDS_LINK = "안에 링크 또는 정션이 있음"
BAD_NAME = "실행 ID 이름 규칙(YYYYMMDD-HHMMSS 또는 YYYYMMDD-HHMMSS-N)에 맞지 않음"
DRY_RUN = "아무것도 지우지 않았습니다. 지우려면 --yes를 붙이세요"
PERMANENT = "휴지통을 거치지 않고 영구 삭제합니다"


@dataclass(frozen=True)
class Candidate:
    run_id: str
    status: str
    bytes: int
    name: str
    """The entry's file name in the runs folder: the run id, or <run id>.jsonl for an old log."""


@dataclass(frozen=True)
class Skipped:
    run_id: str
    reason: str


@dataclass(frozen=True)
class PrunePlan:
    candidates: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def total_bytes(self):
        return sum(candidate.bytes for candidate in self.candidates)


@dataclass(frozen=True)
class _Entry:
    run_id: str
    path: Path
    legacy: bool


def plan_prune(runs_dir, *, older_than=None, keep_newest=None, now, is_active, local_timezone=None):
    """Returns what a prune would delete and what it leaves alone, touching no file.

    Give exactly one of older_than (a timedelta, compared with the moment the run id names) or
    keep_newest (N: every run but the newest N is a candidate; kept, active and unreadable
    runs still count toward N). is_active(run_dir) says whether a run is still running.
    local_timezone reads old log names (None: this PC's time zone).
    """
    selected = _selector(older_than, keep_newest, now, lambda entry: _moment(entry, local_timezone))
    runs_dir = Path(runs_dir)
    entries, skipped = _scan(runs_dir)
    candidates = []
    for entry in selected(entries):
        reason, candidate = _judge(entry, is_active)
        if reason is not None:
            skipped.append(Skipped(entry.run_id, reason))
        else:
            candidates.append(candidate)
    return PrunePlan(candidates, skipped)


def format_plan(plan, will_delete):
    """The plan as lines for a person, ending with what happens next."""
    lines = []
    if plan.candidates:
        lines.append(f"지울 실행 {len(plan.candidates)}개, 합계 {_size(plan.total_bytes)}")
        lines += [f"  {c.run_id}  {c.status}  {_size(c.bytes)}" for c in plan.candidates]
    else:
        lines.append("지울 실행이 없습니다.")
    if plan.skipped:
        lines.append(f"건너뛴 항목 {len(plan.skipped)}개")
        lines += [f"  {s.run_id}  {s.reason}" for s in plan.skipped]
    lines.append(PERMANENT if will_delete else DRY_RUN)
    return "\n".join(lines)


def delete_plan(runs_dir, plan):
    """Deletes the plan's candidates and returns one error line per candidate left in place.

    Right before each deletion the candidate must still be a direct child of runs_dir, not a
    link or junction, of the planned kind, without a keep file and holding no link."""
    runs_dir = Path(runs_dir)
    errors = []
    for candidate in plan.candidates:
        try:
            reason = _refusal(runs_dir, candidate)
            if reason is None:
                _remove(runs_dir / candidate.name)
        except OSError as error:
            reason = f"{type(error).__name__}: {error}"
        if reason is not None:
            errors.append(f"{candidate.name}: {reason}")
    return errors


def _selector(older_than, keep_newest, now, moment):
    if (older_than is None) == (keep_newest is None):
        raise ValueError("older_than와 keep_newest 중 정확히 하나를 주세요")
    if older_than is not None:
        if not isinstance(older_than, timedelta) or older_than < timedelta(0):
            raise ValueError("older_than는 0 이상의 timedelta여야 합니다")
        cutoff = now.astimezone(timezone.utc) - older_than
        return lambda entries: [e for e in entries if moment(e) < cutoff]
    if type(keep_newest) is not int or keep_newest < 0:
        raise ValueError("keep_newest는 0 이상의 정수여야 합니다")
    def newest_first(entry):
        suffix = entry.run_id[16:]
        return (moment(entry), int(suffix) if suffix else 1, not entry.legacy)

    return lambda entries: sorted(entries, key=newest_first, reverse=True)[keep_newest:]


def _scan(runs_dir):
    """Splits the folder into run entries and skipped names; other plain files are ignored."""
    entries, skipped = [], []
    if not runs_dir.is_dir():
        return entries, skipped
    for path in sorted(runs_dir.iterdir()):
        if _is_link(path):
            skipped.append(Skipped(path.name, LINK))
        elif path.is_dir():
            _sort_name(path, path.name, False, entries, skipped)
        elif path.is_file() and path.name.endswith(LEGACY_SUFFIX):
            _sort_name(path, path.name[: -len(LEGACY_SUFFIX)], True, entries, skipped)
    return entries, skipped


def _sort_name(path, run_id, legacy, entries, skipped):
    if _valid_run_id(run_id):
        entries.append(_Entry(run_id, path, legacy))
    else:
        skipped.append(Skipped(path.name, BAD_NAME))


def _judge(entry, is_active):
    """Returns (reason, None) for an entry to leave alone, or (None, candidate)."""
    if entry.legacy:
        return None, Candidate(entry.run_id, "legacy", entry.path.stat().st_size, entry.path.name)
    if (entry.path / KEEP_FILE).exists():
        return KEPT, None
    try:
        run = json.loads((entry.path / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNREADABLE, None
    if not isinstance(run, dict):
        return UNREADABLE, None
    if is_active(entry.path):
        return ACTIVE, None
    if _holds_link(entry.path):
        return HOLDS_LINK, None
    status = run.get("status")
    status = "interrupted" if status == "running" else str(status or "unknown")
    return None, Candidate(entry.run_id, status, _folder_bytes(entry.path), entry.path.name)


def _refusal(runs_dir, candidate):
    """Why this candidate must not be deleted now, or None."""
    path = runs_dir / candidate.name
    if Path(candidate.name).name != candidate.name or candidate.name in ("", ".", ".."):
        return "실행 폴더의 직속 항목이 아님"
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(runs_dir):
        return "실행 폴더의 직속 항목이 아님"
    if _is_link(path):
        return LINK
    if not path.exists():
        return "이미 없음"
    if candidate.status == "legacy":
        return None if path.is_file() else "옛 로그 파일이 아님"
    if not path.is_dir():
        return "실행 폴더가 아님"
    if (path / KEEP_FILE).exists():
        return KEPT
    if _holds_link(path):
        return HOLDS_LINK
    return None


def _remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _valid_run_id(run_id):
    if not is_run_id(run_id):
        return False
    try:
        _run_time(run_id)
    except ValueError:
        return False
    return True


def _run_time(run_id):
    return datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S")


def _moment(entry, local_timezone):
    """The UTC moment an entry's name stands for: run ids are UTC, old log names local time."""
    written = _run_time(entry.run_id)
    if not entry.legacy:
        return written.replace(tzinfo=timezone.utc)
    if local_timezone is not None:
        return written.replace(tzinfo=local_timezone).astimezone(timezone.utc)
    try:
        return written.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        return written.replace(tzinfo=timezone.utc)


def _is_link(path):
    return os.path.islink(path) or getattr(os.path, "isjunction", lambda _p: False)(path)


def _holds_link(folder):
    for root, dirs, files in os.walk(folder):
        if any(_is_link(os.path.join(root, name)) for name in dirs + files):
            return True
    return False


def _folder_bytes(folder):
    return sum(
        os.lstat(os.path.join(root, name)).st_size for root, _dirs, files in os.walk(folder) for name in files
    )


def _size(count):
    size = float(count)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
