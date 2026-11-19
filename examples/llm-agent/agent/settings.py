"""Reads the settings file: the values that differ per company and stay out of the repository.

The file is YAML with the keys in KEYS. It is found by the path given, else AGENT_SETTINGS,
else examples/llm-agent/settings.local.yaml. A missing default file means no settings; a
named file that is missing is an error, because a typo there would silently drop every value.
The API keys and addresses (OPENAI_*, VISION_*) stay in .env.

Precedence where these values are used: command line > task file > settings file > code default.
The text model's name has OPENAI_MODEL between the settings file and the code default, and
vision_model beats VISION_MODEL. VISION_POINT_MODES repeats agent.vision.POINT_MODES so the two
modules load independently; a test keeps them equal.

`systems` is the one key whose value is a mapping (name -> exe path); agent.systems picks from it.
ready_text and ready_timeout_s reach a task only through agent.systems.apply_ready, for an exe the
settings name, never as task defaults.
"""

import os
import re
from pathlib import Path, PureWindowsPath

import yaml

from agent.images import IMAGE_MODES
from agent.knowledge import KnowledgeError, system_name

HERE = Path(__file__).resolve().parent.parent
SETTINGS_ENV = "AGENT_SETTINGS"
DEFAULT_PATH = HERE / "settings.local.yaml"
KEYS = ("model", "reasoning_effort", "image_mode", "knowledge_dir", "exe", "system", "stage_call_limit",
        "plan_reasoning_effort", "vision_model", "vision_reasoning_effort", "vision_max_side", "vision_points",
        "systems", "ready_text", "ready_timeout_s", "workflow_dir")
NUMBER_KEYS = ("stage_call_limit", "vision_max_side", "ready_timeout_s")
VISION_POINT_MODES = ("pixel", "normalized_1000")
PATH_KEYS = ("knowledge_dir", "exe", "workflow_dir")
FOLDER_KEYS = ("knowledge_dir", "workflow_dir")
"""Folders a relative value of is taken from the settings file's folder."""
QUOTE_HINT = "경로는 따옴표 없이 적거나 작은따옴표로 감싸세요"
QUOTED_BACKSLASH = re.compile(r'"[^"]*\\')
CONTROL_CHARACTERS = re.compile("[\x00-\x1f\x7f\x85  ]")


class SettingsError(Exception):
    """The settings file cannot be used. The message says why in Korean and names the file;
    reason says why in a few words with no path, no value and no key the user typed."""

    def __init__(self, message, reason=""):
        super().__init__(message)
        self.reason = reason


def settings_path(path=None, environ=None):
    """The settings file this process would read, whether or not it exists."""
    environ = os.environ if environ is None else environ
    named = _named(path, environ)
    return DEFAULT_PATH if named is None else named


def load_settings(path=None, environ=None):
    """The settings as a dict holding only the keys the file sets, {} without a file.
    A relative knowledge_dir or workflow_dir is taken from the settings file's folder."""
    environ = os.environ if environ is None else environ
    named = _named(path, environ)
    target = DEFAULT_PATH if named is None else named
    if not target.is_file():
        if named is None:
            return {}
        raise SettingsError(f"설정 파일이 없습니다: {target}", "설정 파일 없음")
    try:
        return _parse(_read(target), target)
    except SettingsError as error:
        raise SettingsError(f"{error} (설정 파일: {target})", error.reason) from error


def _named(path, environ):
    for value in (path, environ.get(SETTINGS_ENV)):
        if value is not None and str(value).strip():
            return Path(value).resolve()
    return None


def _read(target):
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise SettingsError("설정 파일은 UTF-8로 저장해야 합니다", "UTF-8이 아님") from error
    except OSError as error:
        raise SettingsError(f"설정 파일을 읽지 못했습니다: {error.strerror or error}", "파일을 읽지 못함") from error
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise _yaml_error(error, text.splitlines()) from error


def _yaml_error(error, lines):
    """Names the line and, when that line holds a backslash inside double quotes, how to write a path.
    The line itself is never shown."""
    mark = getattr(error, "problem_mark", None)
    where = f" {mark.line + 1}행" if mark is not None else ""
    reason = f"YAML 형식 오류{where}"
    message = f"설정 파일의 YAML 형식이 틀렸습니다:{where}"
    if mark is not None and mark.line < len(lines) and QUOTED_BACKSLASH.search(lines[mark.line]):
        reason += f" · 큰따옴표 안의 역슬래시 · {QUOTE_HINT}"
        message += f". 큰따옴표 안에서는 역슬래시가 특수 문자입니다. {QUOTE_HINT}"
    return SettingsError(message, reason)


def _parse(raw, target):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SettingsError("설정 파일은 키와 값의 묶음('키: 값' 줄)이어야 합니다", "'키: 값' 줄이 아님")
    unknown = [str(key) for key in raw if key not in KEYS]
    if unknown:
        raise SettingsError(f"모르는 키가 있습니다: {', '.join(unknown)}. 쓸 수 있는 키: {', '.join(KEYS)}",
                            f"모르는 키 {len(unknown)}개")
    loaded = {}
    for key in KEYS:
        if raw.get(key) is None:
            continue
        if key in NUMBER_KEYS:
            loaded[key] = _number(key, raw[key])
            continue
        if key == "systems":
            loaded[key] = _systems(raw[key])
            continue
        value = "off" if key == "image_mode" and raw[key] is False else raw[key]
        if not isinstance(value, str) or not value.strip():
            raise SettingsError(f"{key} 값은 비어 있지 않은 글이어야 합니다", f"{key} 값이 글이 아님")
        loaded[key] = _checked(key, value.strip(), target)
    return loaded


def _systems(value):
    """The systems as name -> exe path, in the file's order. A name follows the `system` rule and a
    path the `exe` rule. Errors name the entry's place, never its name or path."""
    if not isinstance(value, dict):
        raise SettingsError("systems 값은 '이름: 실행 파일 경로' 줄의 묶음이어야 합니다",
                            "systems 값이 이름과 경로의 묶음이 아님")
    if not value:
        raise SettingsError("systems 값이 비어 있습니다. 시스템을 하나 이상 적거나 systems 줄을 지우세요",
                            "systems 값이 비어 있음")
    return {name: _system_path(place, name, path) for place, (name, path) in enumerate(value.items(), start=1)}


def _system_path(place, name, path):
    entry = f"systems {place}번째 항목"
    try:
        system_name(name, "")
    except KnowledgeError as error:
        raise SettingsError(f"{entry}: 이름은 영문, 숫자, 한글, '.', '_', '-'로 64자까지 쓸 수 있습니다",
                            f"{entry}: 이름 규칙에 맞지 않음") from error
    if not isinstance(path, str) or not path.strip():
        raise SettingsError(f"{entry}: 경로는 비어 있지 않은 글이어야 합니다", f"{entry}: 경로가 글이 아님")
    path = path.strip()
    if CONTROL_CHARACTERS.search(path):
        raise SettingsError(f"{entry}: 경로에 제어 문자가 있습니다. 큰따옴표 안의 역슬래시가 특수 문자로 읽혔을 수 있습니다. "
                            f"{QUOTE_HINT}", f"{entry}: 경로에 제어 문자 · {QUOTE_HINT}")
    if not PureWindowsPath(path).is_absolute():
        raise SettingsError(f"{entry}: 경로는 절대 경로여야 합니다", f"{entry}: 경로가 절대 경로가 아님")
    return path


def _number(key, value):
    if type(value) is not int or value < 1:
        raise SettingsError(f"{key} 값은 1 이상의 정수여야 합니다", f"{key} 값이 1 이상의 정수가 아님")
    return value


def _checked(key, value, target):
    if key in PATH_KEYS and CONTROL_CHARACTERS.search(value):
        raise SettingsError(f"{key} 경로에 제어 문자가 있습니다. 큰따옴표 안의 역슬래시가 특수 문자로 읽혔을 수 있습니다. "
                            f"{QUOTE_HINT}", f"{key} 경로에 제어 문자 · {QUOTE_HINT}")
    if key == "image_mode" and value not in IMAGE_MODES:
        raise SettingsError(f"image_mode는 {', '.join(IMAGE_MODES)} 중 하나여야 합니다",
                            f"image_mode 값이 {', '.join(IMAGE_MODES)} 중 하나가 아님")
    if key == "vision_points" and value not in VISION_POINT_MODES:
        raise SettingsError(f"vision_points는 {', '.join(VISION_POINT_MODES)} 중 하나여야 합니다",
                            f"vision_points 값이 {', '.join(VISION_POINT_MODES)} 중 하나가 아님")
    if key == "exe" and not PureWindowsPath(value).is_absolute():
        raise SettingsError("exe는 절대 경로여야 합니다", "exe가 절대 경로가 아님")
    if key == "system":
        try:
            system_name(value, "")
        except KnowledgeError as error:
            raise SettingsError("system은 영문, 숫자, 한글, '.', '_', '-'로 64자까지 쓸 수 있습니다",
                                "system 이름 규칙에 맞지 않음") from error
    if key in FOLDER_KEYS:
        return str((target.parent / value).resolve())
    return value
