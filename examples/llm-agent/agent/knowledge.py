"""Per-system knowledge: what a person answered once, kept so the agent need not ask again.

Each entry is one human-editable YAML file at `<root>/<system>/<id>.yaml`. Entries are text for
the model to read and apply to the current screen; the harness never replays them, so they hold
roles, names and relative positions, never screen coordinates.
"""

import os
import re
import time
from datetime import date
from pathlib import Path, PureWindowsPath

import yaml

KNOWLEDGE_ENV = "AGENT_KNOWLEDGE_DIR"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "knowledge"

TYPES = {"resolution": "r", "completion": "c"}
STATUSES = ("active", "disabled")
COMMON_KEYS = ("id", "type", "status")
COUNT_KEYS = ("uses", "failures")
CONTENT_KEYS = {"resolution": ("situation", "resolution"), "completion": ("task", "checks")}
TEXT_KEYS = ("situation", "resolution", "task")
SOURCE_KEYS = {"run_id", "question", "answer", "date"}
CHECK_KEYS = {"file": {"kind", "path", "pattern"}, "element": {"kind", "require"}, "human": {"kind"}}
REQUIRE_KEYS = {"role", "name", "name_contains", "description_contains", "states"}
FIELD_LABELS = {
    "id": "id", "type": "종류", "status": "상태", "situation": "상황", "resolution": "해결", "task": "작업",
    "checks": "확인 방법", "source": "근거", "uses": "사용 횟수", "failures": "실패 횟수",
}
TYPE_LABELS = "resolution(해결 기록), completion(완료 조건)"

SYSTEM_PATTERN = re.compile(r"[A-Za-z0-9가-힣._-]{1,64}")
SYSTEM_CHAR = re.compile(r"[^A-Za-z0-9가-힣._-]")
ID_PATTERN = re.compile(r"[rc]-[A-Za-z0-9._-]{1,62}")
COORDINATE_PATTERNS = (
    re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)"),
    re.compile(r"(?<![A-Za-z])[xyXY]\s*[=:]\s*-?\d+"),
)
MAX_DAILY = 999
REPLACE_ATTEMPTS = 5
REPLACE_PAUSE_S = 0.05


class KnowledgeError(ValueError):
    """An entry, id or system name the store refuses."""


class KnowledgeStore:
    """The entries of one system. Reads the folder on every call, so hand edits show up at once."""

    def __init__(self, root, system):
        self.system = _checked_system(system)
        self.folder = Path(root) / self.system
        self.warnings = []

    def entries(self, include_disabled=False):
        loaded, self.warnings = self._load_all()
        if include_disabled:
            return loaded
        return [entry for entry in loaded if entry["status"] == "active"]

    def get(self, entry_id):
        if not _is_id(entry_id):
            return None
        path = self.folder / f"{entry_id}.yaml"
        if not path.is_file():
            return None
        try:
            return _read(path)
        except KnowledgeError:
            return None

    def add(self, entry, today=None):
        candidate = check_entry({key: value for key, value in _mapping(entry).items() if key != "id"})
        candidate["id"] = self._next_id(candidate["type"], today or date.today())
        self._write(candidate)
        return _ordered(candidate)

    def update(self, entry_id, changes):
        current = self._existing(entry_id)
        if not isinstance(changes, dict):
            raise KnowledgeError("고칠 내용은 키와 값의 묶음이어야 합니다")
        for key in ("id", "type"):
            if key in changes and changes[key] != current[key]:
                raise KnowledgeError(f"바꿀 수 없는 항목입니다: {FIELD_LABELS[key]}")
        updated = {**current, **changes}
        _validate(updated)
        self._write(updated)
        return _ordered(updated)

    def delete(self, entry_id):
        if not _is_id(entry_id):
            raise KnowledgeError(f"항목 id가 아닙니다: {entry_id!r}")
        path = self.folder / f"{entry_id}.yaml"
        if not path.is_file():
            return False
        path.unlink()
        return True

    def count_use(self, entry_id):
        self._increment(entry_id, "uses")

    def count_failure(self, entry_id):
        self._increment(entry_id, "failures")

    def _increment(self, entry_id, key):
        try:
            current = self._existing(entry_id)
        except KnowledgeError as error:
            self.warnings.append(f"{entry_id}: {FIELD_LABELS[key]}를 세지 못했습니다: {error}")
            return
        self._write({**current, key: current[key] + 1})

    def _existing(self, entry_id):
        if not _is_id(entry_id):
            raise KnowledgeError(f"항목 id가 아닙니다: {entry_id!r}")
        path = self.folder / f"{entry_id}.yaml"
        if not path.is_file():
            raise KnowledgeError(f"{self.system}에 {entry_id} 항목이 없습니다")
        return _read(path)

    def _load_all(self):
        if not self.folder.is_dir():
            return [], []
        loaded, warnings = [], []
        for path in sorted(self.folder.glob("*.yaml")):
            if not path.is_file():
                continue
            try:
                loaded.append(_read(path))
            except KnowledgeError as error:
                warnings.append(f"{path.name}: {error}")
        return loaded, warnings

    def _next_id(self, kind, today):
        stem = f"{TYPES[kind]}-{today:%Y%m%d}-"
        taken = {path.stem for path in self.folder.glob(f"{stem}*.yaml")} if self.folder.is_dir() else set()
        for number in range(1, MAX_DAILY + 1):
            candidate = f"{stem}{number:03d}"
            if candidate not in taken:
                return candidate
        raise KnowledgeError(f"{today:%Y-%m-%d}의 항목이 {MAX_DAILY}개를 넘었습니다")

    def _write(self, entry):
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"{entry['id']}.yaml"
        temporary = self.folder / f"{entry['id']}.yaml.tmp"
        text = yaml.safe_dump(_ordered(entry), allow_unicode=True, sort_keys=False, width=1000)
        temporary.write_text(text, encoding="utf-8")
        _replace_retrying(temporary, target)


def knowledge_root(explicit=None, task_dir=None, environ=None):
    """The knowledge folder, as an absolute path: the command-line value, else the task file's
    knowledge_dir, else AGENT_KNOWLEDGE_DIR, else examples/llm-agent/knowledge. Empty values
    are skipped; a relative path is taken from the current directory."""
    environ = os.environ if environ is None else environ
    for value in (explicit, task_dir, environ.get(KNOWLEDGE_ENV)):
        if value is not None and str(value).strip():
            return Path(value).resolve()
    return DEFAULT_ROOT


def check_entry(entry):
    """The entry with its defaults filled, validated as the store would save it, or
    KnowledgeError. Saves nothing. An entry without an id is checked as a new one."""
    candidate = dict(_mapping(entry))
    kind = candidate.get("type")
    if kind not in TYPES:
        raise KnowledgeError(f"종류는 {TYPE_LABELS} 중 하나여야 합니다")
    candidate.setdefault("id", f"{TYPES[kind]}-00000000-000")
    candidate.setdefault("status", "active")
    candidate.setdefault("uses", 0)
    candidate.setdefault("failures", 0)
    _validate(candidate)
    return _ordered(candidate)


def _mapping(entry):
    if not isinstance(entry, dict):
        raise KnowledgeError("항목은 키와 값의 묶음이어야 합니다")
    return entry


def system_name(explicit, exe):
    """The knowledge folder name: the explicit name when given, else the exe file name
    without its extension, lowercased, with characters a folder name cannot hold as `_`."""
    if explicit:
        return _checked_system(explicit)
    stem = SYSTEM_CHAR.sub("_", PureWindowsPath(exe).stem.lower())[:64]
    return _checked_system(stem)


def list_systems(root):
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir() and _is_system(path.name))


def relevant(store, goal, situation=None):
    """The entries worth showing the model for this goal. Every active entry for now; the
    single place to switch to a search once a system holds too many to send whole."""
    return store.entries()


def briefing(entries, limit_chars=6000):
    """The entries as one line each, most used first. Stops before the first line that would
    exceed `limit_chars` and reports whether anything was left out."""
    ordered = sorted(entries, key=lambda entry: -entry.get("uses", 0))
    lines, used = [], 0
    for entry in ordered:
        line = _briefing_line(entry)
        cost = len(line) + (1 if lines else 0)
        if used + cost > limit_chars:
            return "\n".join(lines), True
        lines.append(line)
        used += cost
    return "\n".join(lines), False


def _briefing_line(entry):
    if entry["type"] == "completion":
        checks = ", ".join(_describe_check(check) for check in entry["checks"])
        return f"- ({entry['id']}) 작업: {_one_line(entry['task'])} → 확인: {checks}"
    return f"- ({entry['id']}) 상황: {_one_line(entry['situation'])} → 해결: {_one_line(entry['resolution'])}"


def _describe_check(check):
    if check["kind"] == "file":
        return " ".join(["file", *(check[key] for key in ("path", "pattern") if check.get(key))])
    if check["kind"] == "element":
        requirements = check["require"] if isinstance(check["require"], list) else [check["require"]]
        return " + ".join(_describe_requirement(requirement) for requirement in requirements)
    return check["kind"]


def _describe_requirement(requirement):
    parts = ["element"]
    if requirement.get("role"):
        parts.append(requirement["role"])
    for key in ("name", "name_contains", "description_contains"):
        if requirement.get(key):
            parts.append(f"'{requirement[key]}'")
    return " ".join(parts)


def _one_line(text):
    return " ".join(str(text).split())


def _read(path):
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise KnowledgeError("읽을 수 없는 YAML입니다") from error
    if not isinstance(raw, dict):
        raise KnowledgeError("항목 하나(키와 값의 묶음)가 아닙니다")
    entry = {key: value for key, value in raw.items()}
    entry.setdefault("status", "active")
    entry.setdefault("uses", 0)
    entry.setdefault("failures", 0)
    if isinstance(entry.get("source"), dict) and isinstance(entry["source"].get("date"), date):
        entry["source"] = {**entry["source"], "date": entry["source"]["date"].isoformat()}
    _validate(entry)
    if entry["id"] != path.stem:
        raise KnowledgeError(f"id {entry['id']}가 파일 이름과 다릅니다")
    return _ordered(entry)


def _validate(entry):
    kind = entry.get("type")
    if kind not in TYPES:
        raise KnowledgeError(f"종류는 {TYPE_LABELS} 중 하나여야 합니다")
    allowed = set(COMMON_KEYS) | set(COUNT_KEYS) | {"source"} | set(CONTENT_KEYS[kind])
    unknown = sorted(str(key) for key in entry if key not in allowed)
    if unknown:
        raise KnowledgeError(f"모르는 항목이 있습니다: {', '.join(unknown)}")
    missing = [key for key in (*COMMON_KEYS, *CONTENT_KEYS[kind]) if key not in entry]
    if missing:
        raise KnowledgeError(f"빠진 항목이 있습니다: {', '.join(FIELD_LABELS[key] for key in missing)}")
    entry_id = entry["id"]
    if not _is_id(entry_id) or not entry_id.startswith(f"{TYPES[kind]}-"):
        raise KnowledgeError(f"id {entry_id!r}는 '{TYPES[kind]}-'로 시작하고 영문자, 숫자, '.', '_', '-'만 써야 합니다")
    if entry["status"] not in STATUSES:
        raise KnowledgeError("상태는 active(사용 중)나 disabled(멈춤)여야 합니다")
    for key in COUNT_KEYS:
        if type(entry[key]) is not int or entry[key] < 0:
            raise KnowledgeError(f"{FIELD_LABELS[key]}는 0 이상의 정수여야 합니다")
    for key in TEXT_KEYS:
        if key in entry:
            _check_text(key, entry[key])
    if kind == "completion":
        _check_checks(entry["checks"])
    if entry.get("source") is not None:
        _check_source(entry["source"])


def _check_text(key, value):
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeError(f"{FIELD_LABELS[key]}이 비어 있습니다")
    for pattern in COORDINATE_PATTERNS:
        found = pattern.search(value)
        if found:
            raise KnowledgeError(
                f"{FIELD_LABELS[key]}에 화면 좌표로 보이는 글('{found.group(0)}')이 있습니다. "
                "요소의 역할, 이름, 부분, 상대 위치로 적어 주세요"
            )


def _check_checks(checks):
    if not isinstance(checks, list) or not checks:
        raise KnowledgeError("확인 방법이 하나 이상 있어야 합니다")
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in CHECK_KEYS:
            raise KnowledgeError("확인 방법마다 kind가 file(파일), element(화면 요소), human(사람 확인) 중 하나여야 합니다")
        kind = check["kind"]
        unknown = sorted(str(key) for key in check if key not in CHECK_KEYS[kind])
        if unknown:
            raise KnowledgeError(f"{kind} 확인에 모르는 항목이 있습니다: {', '.join(unknown)}")
        if kind == "file":
            _check_file_check(check)
        if kind == "element":
            _check_requirements(check.get("require"))


def _check_file_check(check):
    if not check.get("path") and not check.get("pattern"):
        raise KnowledgeError("파일 확인에는 path(경로)나 pattern(파일 이름 패턴)이 있어야 합니다")
    for key in ("path", "pattern"):
        if key in check and (not isinstance(check[key], str) or not check[key].strip()):
            raise KnowledgeError(f"파일 확인의 {key}가 비어 있습니다")
    if "pattern" in check and re.search(r"[\\/]", check["pattern"]):
        raise KnowledgeError("파일 확인의 pattern은 파일 이름에만 맞춥니다. 폴더는 path에 적어 주세요")


def _check_requirements(require):
    requirements = require if isinstance(require, list) else [require]
    if not requirements or not all(isinstance(item, dict) and item for item in requirements):
        raise KnowledgeError("화면 요소 확인에는 require(요소 조건)가 하나 이상 있어야 합니다")
    for requirement in requirements:
        unknown = sorted(str(key) for key in requirement if key not in REQUIRE_KEYS)
        if unknown:
            raise KnowledgeError(f"모르는 요소 조건이 있습니다: {', '.join(unknown)}")


def _check_source(source):
    if not isinstance(source, dict):
        raise KnowledgeError("근거는 키와 값의 묶음이어야 합니다")
    unknown = sorted(str(key) for key in source if key not in SOURCE_KEYS)
    if unknown:
        raise KnowledgeError(f"근거에 모르는 항목이 있습니다: {', '.join(unknown)}")
    for key, value in source.items():
        if value is not None and not isinstance(value, (str, int)):
            raise KnowledgeError(f"근거의 {key}는 글이어야 합니다")


def _ordered(entry):
    order = [*COMMON_KEYS, *CONTENT_KEYS.get(entry.get("type"), ()), "source", *COUNT_KEYS]
    ordered = {key: entry[key] for key in order if key in entry}
    ordered.update({key: value for key, value in entry.items() if key not in ordered})
    return ordered


def _is_id(value):
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def _is_system(value):
    return isinstance(value, str) and value not in (".", "..") and SYSTEM_PATTERN.fullmatch(value) is not None


def _checked_system(value):
    if not _is_system(value):
        raise KnowledgeError(f"시스템 이름으로 쓸 수 없습니다: {value!r} (영문자, 숫자, 한글, '.', '_', '-'로 64자까지)")
    return value


def _replace_retrying(source, target):
    """os.replace, retried briefly: on Windows it fails while another process (the viewer)
    has the target open for reading."""
    for _ in range(REPLACE_ATTEMPTS - 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(REPLACE_PAUSE_S)
    os.replace(source, target)
