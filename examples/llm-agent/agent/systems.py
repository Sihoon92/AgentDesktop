"""Picks the target app from the settings file's `systems` (name -> exe path) and gives a task the
ready signal only when its exe is one the settings name.

The ready signal belongs to the apps written in the settings file: a `systems` path or the
settings `exe`. Another exe (from --exe, the viewer's typed path or a task file) gets none,
because waiting for a signal that app never shows would stop every run at the timeout. A task
file that sets ready_text itself keeps its own values.
"""

import os
from dataclasses import replace


def system_exe(settings, name):
    """The exe path of the named system, or None when the name is missing or unknown."""
    if name is None:
        return None
    return _systems(settings).get(name)


def system_names(settings):
    """The system names in the settings file's order."""
    return list(_systems(settings))


def default_choice(settings):
    """The system picked when nobody chose one: the settings `system` when it names an entry,
    else the first entry, else None."""
    systems = _systems(settings)
    if not systems:
        return None
    chosen = (settings or {}).get("system")
    return chosen if chosen in systems else next(iter(systems))


def apply_ready(task, settings):
    """`task` with the settings' ready signal when its exe is one the settings name; otherwise
    `task` itself. A task that already has ready_text (from its task file) is returned as is."""
    settings = settings or {}
    if task.ready_text is not None or settings.get("ready_text") is None:
        return task
    if _same_path(task.exe) not in _configured(settings):
        return task
    return replace(task, ready_text=settings["ready_text"],
                   ready_timeout_s=settings.get("ready_timeout_s", task.ready_timeout_s))


def _systems(settings):
    return (settings or {}).get("systems") or {}


def _configured(settings):
    paths = list(_systems(settings).values())
    if settings.get("exe"):
        paths.append(settings["exe"])
    return {_same_path(path) for path in paths}


def _same_path(path):
    return os.path.normcase(os.path.normpath(path))
