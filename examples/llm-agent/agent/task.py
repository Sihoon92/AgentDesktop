"""Loads and validates a task file."""

from dataclasses import dataclass
from pathlib import PureWindowsPath

import yaml


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


def task_from_args(goal, exe, permission_level=3):
    """A one-off instruction with no success conditions; the harness cannot verify it."""
    if not PureWindowsPath(exe).is_absolute():
        raise TaskError("exe must be an absolute path")
    return Task(goal, exe, permission_level, [])


def load_task(path):
    raw = yaml.safe_load(open(path, encoding="utf-8").read()) or {}
    for key in ("goal", "exe", "permission_level", "success"):
        if key not in raw:
            raise TaskError(f"Task is missing '{key}'")
    level = raw["permission_level"]
    if type(level) is not int or not 0 <= level <= 3:
        raise TaskError("permission_level must be an integer from 0 to 3")
    if not PureWindowsPath(raw["exe"]).is_absolute():
        raise TaskError("exe must be an absolute path")
    require = (raw["success"] or {}).get("require")
    if not require:
        raise TaskError("success.require must list at least one element")
    optional = {
        key: raw[key]
        for key in ("max_steps", "token_budget", "model", "reasoning_effort", "baseline_target")
        if key in raw
    }
    return Task(raw["goal"], raw["exe"], level, require, **optional)
