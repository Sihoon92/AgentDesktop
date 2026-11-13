"""Loads and validates a task file."""

from dataclasses import dataclass
from pathlib import PureWindowsPath

import yaml

from agent.images import IMAGE_MODES


class TaskError(Exception):
    """The task file is invalid."""


@dataclass(frozen=True)
class Task:
    goal: str
    exe: str
    permission_level: int
    require: list
    max_steps: int = 12
    token_budget: int = 200_000
    model: str = "gpt-6.1-terra"
    reasoning_effort: str = "low"
    baseline_target: dict | None = None
    system: str | None = None
    knowledge_dir: str | None = None
    image_mode: str = "user_message"
    ask_timeout_s: int = 1800
    mismatch_limit: int = 2


ARGS_MAX_STEPS = 40
OPTIONAL_KEYS = ("max_steps", "token_budget", "model", "reasoning_effort", "baseline_target",
                 "system", "knowledge_dir", "image_mode", "ask_timeout_s", "mismatch_limit")
DEFAULT_KEYS = ("model", "reasoning_effort", "image_mode", "knowledge_dir", "system")


def task_from_args(goal, exe, permission_level=3, defaults=None):
    """A one-off instruction with no success conditions; the harness cannot verify it alone.

    It gets more steps than a task file's default, because observing, looking at captures and
    asking add turns that a scripted task does not need. `defaults` (the settings file) supplies
    the DEFAULT_KEYS in place of the code defaults."""
    if not PureWindowsPath(exe).is_absolute():
        raise TaskError("exe must be an absolute path")
    optional = _defaults(defaults)
    _check_optional(optional)
    return Task(goal, exe, permission_level, [], max_steps=ARGS_MAX_STEPS, **optional)


def load_task(path, defaults=None):
    """Reads a task file. success is optional: without it the run is judged by learned
    completion entries or by a person. A key the file sets beats the same key in `defaults`."""
    raw = yaml.safe_load(open(path, encoding="utf-8").read()) or {}
    for key in ("goal", "exe", "permission_level"):
        if key not in raw:
            raise TaskError(f"Task is missing '{key}'")
    level = raw["permission_level"]
    if type(level) is not int or not 0 <= level <= 3:
        raise TaskError("permission_level must be an integer from 0 to 3")
    if not PureWindowsPath(raw["exe"]).is_absolute():
        raise TaskError("exe must be an absolute path")
    require = []
    if "success" in raw:
        require = (raw["success"] or {}).get("require")
        if not require:
            raise TaskError("success.require must list at least one element")
    optional = {**_defaults(defaults), **{key: raw[key] for key in OPTIONAL_KEYS if key in raw}}
    _check_optional(optional)
    return Task(raw["goal"], raw["exe"], level, require, **optional)


def _defaults(defaults):
    defaults = defaults or {}
    return {key: defaults[key] for key in DEFAULT_KEYS if defaults.get(key) is not None}


def _check_optional(optional):
    if optional.get("image_mode", "user_message") not in IMAGE_MODES:
        raise TaskError(f"image_mode must be one of {', '.join(IMAGE_MODES)}")
    for key in ("ask_timeout_s", "mismatch_limit"):
        value = optional.get(key, 1)
        if type(value) is not int or value < 1:
            raise TaskError(f"{key} must be a whole number of at least 1")
    for key in ("model", "reasoning_effort", "system", "knowledge_dir"):
        if key in optional and not isinstance(optional[key], str):
            raise TaskError(f"{key} must be text")
