"""Reads the settings file: the values that differ per company and stay out of the repository.

The file is YAML with the keys in KEYS. It is found by the path given, else AGENT_SETTINGS,
else examples/llm-agent/settings.local.yaml. A missing default file means no settings; a
named file that is missing is an error, because a typo there would silently drop every value.
The API key and OPENAI_BASE_URL stay in .env.

Precedence where these values are used: command line > task file > settings file > code default.
"""

import os
from pathlib import Path, PureWindowsPath

import yaml

from agent.images import IMAGE_MODES
from agent.knowledge import KnowledgeError, system_name

HERE = Path(__file__).resolve().parent.parent
SETTINGS_ENV = "AGENT_SETTINGS"
DEFAULT_PATH = HERE / "settings.local.yaml"
KEYS = ("model", "reasoning_effort", "image_mode", "knowledge_dir", "exe", "system")


class SettingsError(Exception):
    """The settings file cannot be used; the message says why, in Korean."""


def settings_path(path=None, environ=None):
    """The settings file this process would read, whether or not it exists."""
    environ = os.environ if environ is None else environ
    named = _named(path, environ)
    return DEFAULT_PATH if named is None else named


def load_settings(path=None, environ=None):
    """The settings as a dict holding only the keys the file sets, {} without a file.
    A relative knowledge_dir is taken from the settings file's folder."""
    environ = os.environ if environ is None else environ
    named = _named(path, environ)
    target = DEFAULT_PATH if named is None else named
    if not target.is_file():
        if named is None:
            return {}
        raise SettingsError(f"설정 파일이 없습니다: {target}")
    try:
        return _parse(_read(target), target)
    except SettingsError as error:
        raise SettingsError(f"{error} (설정 파일: {target})") from error


def _named(path, environ):
    for value in (path, environ.get(SETTINGS_ENV)):
        if value is not None and str(value).strip():
            return Path(value).resolve()
    return None


def _read(target):
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise SettingsError("설정 파일은 UTF-8로 저장해야 합니다") from error
    except OSError as error:
        raise SettingsError(f"설정 파일을 읽지 못했습니다: {error.strerror or error}") from error
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" {mark.line + 1}행" if mark is not None else ""
        raise SettingsError(f"설정 파일의 YAML 형식이 틀렸습니다:{where}") from error


def _parse(raw, target):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SettingsError("설정 파일은 키와 값의 묶음('키: 값' 줄)이어야 합니다")
    unknown = [str(key) for key in raw if key not in KEYS]
    if unknown:
        raise SettingsError(f"모르는 키가 있습니다: {', '.join(unknown)}. 쓸 수 있는 키: {', '.join(KEYS)}")
    loaded = {}
    for key in KEYS:
        if raw.get(key) is None:
            continue
        value = "off" if key == "image_mode" and raw[key] is False else raw[key]
        if not isinstance(value, str) or not value.strip():
            raise SettingsError(f"{key} 값은 비어 있지 않은 글이어야 합니다")
        loaded[key] = _checked(key, value.strip(), target)
    return loaded


def _checked(key, value, target):
    if key == "image_mode" and value not in IMAGE_MODES:
        raise SettingsError(f"image_mode는 {', '.join(IMAGE_MODES)} 중 하나여야 합니다")
    if key == "exe" and not PureWindowsPath(value).is_absolute():
        raise SettingsError("exe는 절대 경로여야 합니다")
    if key == "system":
        try:
            system_name(value, "")
        except KnowledgeError as error:
            raise SettingsError("system은 영문, 숫자, 한글, '.', '_', '-'로 64자까지 쓸 수 있습니다") from error
    if key == "knowledge_dir":
        return str((target.parent / value).resolve())
    return value
