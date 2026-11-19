"""The system profile: what an administrator registered for one system, one YAML file per system.

`<root>/<system>.yaml` holds the ready signals (all must show for the main screen to count as
ready), the names given to elements whose label is only a picture, keyed by native id, whether
the system's ids stay the same when it restarts, and how the ready wait reads the screen. The
system name follows the knowledge folder's rule. A missing file is an empty profile; a file that
fails the check stops the run, because a profile half used would look like a slow app.

The file holds screen text, so it stays out of Git, and an error never repeats a value from it:
reasons say which item and which key, and a YAML error names only the line. Files are written to
a temporary name and swapped in, as the workflow store does, in the key order the design shows,
so that a person can fix one by hand.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent.identity import container_key, fits
from agent.knowledge import system_name

PROFILE_ENV = "AGENT_PROFILE_DIR"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "profiles"
FORMAT = 1
READ_MODES = ("snapshot", "find")
ALIAS_MAX = 80
TOP_KEYS = ("format", "system", "stable_ids", "ready_read", "ready", "elements")
NAME_SIGNAL_KEYS = ("name", "role")
ID_SIGNAL_KEYS = ("id", "role", "in")
ELEMENT_KEYS = ("id", "role", "in", "name")
AREA_SHAPES = ({"role", "id"}, {"role", "name"})
REPLACE_ATTEMPTS = 5
REPLACE_PAUSE_S = 0.05


class ProfileError(Exception):
    """A profile that cannot be used. `reason` (also the message) says where in a few words,
    never with a value from the file."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Profile:
    """One system's profile. ready holds signal dicts ({"name", "role"?} or {"id", "role", "in"?})
    and elements holds {"id", "role", "in"?, "name"}; callers read these dicts and never change them."""

    system: str | None = None
    ready: tuple = ()
    elements: tuple = ()
    stable_ids: bool = False
    ready_read: str = "snapshot"


def profile_root(explicit=None, settings_dir=None, environ=None):
    """The profile folder, as an absolute path: the command-line value, else the settings'
    profile_dir, else AGENT_PROFILE_DIR, else examples/llm-agent/profiles. Empty values are
    skipped; a relative path is taken from the current directory."""
    environ = os.environ if environ is None else environ
    for value in (explicit, settings_dir, environ.get(PROFILE_ENV)):
        if value is not None and str(value).strip():
            return Path(value).resolve()
    return DEFAULT_ROOT


def load_profile(root, system):
    """The profile of `system` from `root`: Profile(system=system) without a file, else the
    checked file. Raises ProfileError when the name or the file cannot be used."""
    system = _system(system)
    path = Path(root) / f"{system}.yaml"
    if not path.is_file():
        return Profile(system=system)
    return check_profile(_read(path), system)


def check_profile(raw, system=None):
    """The Profile `raw` (a loaded file) describes, or ProfileError. With `system`, the file's
    system must be that one: a file copied from another system would wait for signals that never show."""
    if not isinstance(raw, dict):
        raise ProfileError("키와 값의 묶음이 아님")
    unknown = [key for key in raw if key not in TOP_KEYS]
    if unknown:
        raise ProfileError(f"맨 위: 모르는 키 {len(unknown)}개")
    if raw.get("format") is None:
        raise ProfileError("format이 없음")
    if type(raw["format"]) is not int or raw["format"] != FORMAT:
        raise ProfileError(f"format은 {FORMAT}이어야 함")
    if raw.get("system") is None:
        raise ProfileError("system이 없음")
    named = _system(raw["system"], "system 이름 규칙에 맞지 않음")
    if system is not None and named != system:
        raise ProfileError("system 값이 파일 이름과 다름")
    stable_ids = raw.get("stable_ids", False)
    if stable_ids is None:
        stable_ids = False
    if not isinstance(stable_ids, bool):
        raise ProfileError("stable_ids는 참이나 거짓이어야 함")
    ready_read = raw.get("ready_read")
    if ready_read is None:
        ready_read = "snapshot"
    if ready_read not in READ_MODES:
        raise ProfileError(f"ready_read는 {', '.join(READ_MODES)} 중 하나여야 함")
    return Profile(
        system=named,
        ready=_entries(raw, "ready", _signal, _signal_key),
        elements=_entries(raw, "elements", _element, _element_key),
        stable_ids=stable_ids,
        ready_read=ready_read,
    )


def save_profile(root, profile):
    """Checks `profile`, writes it to `<root>/<system>.yaml` through a temporary file, and returns
    the path. Nothing is written when the check fails."""
    data = as_dict(profile)
    check_profile(data, profile.system)
    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{profile.system}.yaml"
    temporary = folder / f"{profile.system}.yaml.tmp"
    text = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000)
    temporary.write_text(text, encoding="utf-8")
    _replace_retrying(temporary, target)
    return target


def as_dict(profile):
    """The profile in the file's shape and key order: top keys, then one mapping per item."""
    return {
        "format": FORMAT,
        "system": profile.system,
        "stable_ids": profile.stable_ids,
        "ready_read": profile.ready_read,
        "ready": [_ordered(signal, ID_SIGNAL_KEYS if "id" in signal else NAME_SIGNAL_KEYS)
                  for signal in profile.ready],
        "elements": [_ordered(entry, ELEMENT_KEYS) for entry in profile.elements],
    }


def entry_of(profile, role, native, container):
    """The elements entry a node with this role, native id and area fits, or None. An entry that
    writes the area comes before one that does not."""
    fitting = [entry for entry in profile.elements if fits(role, native, container, entry)]
    fitting.sort(key=lambda entry: "in" not in entry)
    return fitting[0] if fitting else None


def alias_of(profile, role, native, container):
    """The registered name of the element a node with this role, native id and area is, or None."""
    entry = entry_of(profile, role, native, container)
    return entry["name"] if entry else None


def _system(value, reason="시스템 이름 규칙에 맞지 않음"):
    if not isinstance(value, str) or not value:
        raise ProfileError(reason)
    try:
        return system_name(value, "")
    except ValueError as error:
        raise ProfileError(reason) from error


def _read(path):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ProfileError("UTF-8이 아님") from error
    except OSError as error:
        raise ProfileError("파일을 읽지 못함") from error
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        raise ProfileError("YAML 형식 오류" + (f" {mark.line + 1}행" if mark is not None else "")) from error


def _entries(raw, key, check, identify):
    items = raw.get(key)
    if items is None:
        return ()
    if not isinstance(items, list):
        raise ProfileError(f"{key}는 목록이어야 함")
    checked = []
    seen = {}
    for place, item in enumerate(items, start=1):
        where = f"{key} {place}번째 항목"
        entry = check(item, where)
        identity = identify(entry)
        if identity in seen:
            raise ProfileError(f"{where}: {seen[identity]}번째 항목과 같음")
        seen[identity] = place
        checked.append(entry)
    return tuple(checked)


def _signal(item, where):
    if not isinstance(item, dict):
        raise ProfileError(f"{where}: 키와 값의 묶음이 아님")
    if "name" in item and "id" in item:
        raise ProfileError(f"{where}: name과 id를 함께 적음")
    if "id" in item:
        _known(item, ID_SIGNAL_KEYS, where)
        signal: dict = {"id": _id(item, "id", where), "role": _text(item, "role", where)}
        if "in" in item:
            signal["in"] = _area(item["in"], where)
        return signal
    if "name" in item:
        _known(item, NAME_SIGNAL_KEYS, where)
        signal = {"name": _text(item, "name", where).strip()}
        if "role" in item:
            signal["role"] = _text(item, "role", where)
        return signal
    _known(item, ID_SIGNAL_KEYS, where)
    raise ProfileError(f"{where}: name이나 id가 없음")


def _element(item, where):
    if not isinstance(item, dict):
        raise ProfileError(f"{where}: 키와 값의 묶음이 아님")
    _known(item, ELEMENT_KEYS, where)
    entry: dict = {"id": _id(item, "id", where), "role": _text(item, "role", where)}
    if "in" in item:
        entry["in"] = _area(item["in"], where)
    name = _text(item, "name", where).strip()
    if len(name.splitlines()) != 1:
        raise ProfileError(f"{where}: name이 한 줄이 아님")
    if len(name) > ALIAS_MAX:
        raise ProfileError(f"{where}: name이 {ALIAS_MAX}자를 넘음")
    entry["name"] = name
    return entry


def _area(value, where):
    if not isinstance(value, dict) or set(value) not in AREA_SHAPES:
        raise ProfileError(f"{where}: in은 role과 id, 또는 role과 name이어야 함")
    area = {"role": _text(value, "role", f"{where}: in의")}
    if "id" in value:
        area["id"] = _id(value, "id", f"{where}: in의")
    else:
        area["name"] = _text(value, "name", f"{where}: in의").strip()
    return area


def _known(item, keys, where):
    if any(key not in keys for key in item):
        raise ProfileError(f"{where}: 모르는 키")


def _text(item, key, where):
    lead = where if where.endswith("의") else f"{where}:"
    if item.get(key) is None:
        raise ProfileError(f"{lead} {key}{_subject(key)} 없음")
    value = item[key]
    if not isinstance(value, str):
        raise ProfileError(f"{lead} {key}{_subject(key)} 글이 아님")
    if not value.strip():
        raise ProfileError(f"{lead} {key}{_subject(key)} 비어 있음")
    return value


def _id(item, key, where):
    """An id as text; a hand-written number without quotes counts as its text."""
    value = item.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _text(item, key, where)


def _subject(key):
    return "가" if key == "id" else "이"


def _signal_key(signal):
    if "id" in signal:
        return "id", signal["role"], signal["id"], container_key(signal.get("in"))
    return "name", signal["name"], signal.get("role")


def _element_key(entry):
    return entry["role"], entry["id"], container_key(entry.get("in"))


def _ordered(mapping, keys):
    ordered = _Flow((key, mapping[key]) for key in keys if key in mapping)
    if isinstance(ordered.get("in"), dict):
        area = ordered["in"]
        ordered["in"] = {key: area[key] for key in ("role", "id", "name") if key in area}
    return ordered


def _replace_retrying(source, target):
    """os.replace, retried briefly: on Windows it fails while another process has the target open."""
    for _ in range(REPLACE_ATTEMPTS - 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(REPLACE_PAUSE_S)
    os.replace(source, target)


class _Flow(dict):
    """An item written on one line, as {key: value, ...}."""


class _Dumper(yaml.SafeDumper):
    """Indents list items under their key, as the design's example does."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


_Dumper.add_representer(
    _Flow, lambda dumper, data: dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True))
