"""Runs the task with fixed steps and no model, as a reference for the agent run."""

from agent.loop import RunResult
from agent.verify import find_element

POLL_S = 0.5
TARGET_LABEL_KEYS = ("role", "name", "name_contains", "description_contains")


def run_baseline(toolbox, task, timeout_s=30):
    """With a ready signal the harness's ready wait launches the target instead of launch_target,
    and a wait that fails stops with its reason."""
    if task.ready_text:
        launched = toolbox.launch_ready()
        if not launched.get("ok"):
            return RunResult("stopped", 1, launched["message"])
    else:
        launched = toolbox.call("launch_target", {})
        if not launched.get("ok"):
            return RunResult("stopped", 1, f"Launch failed: {launched}")
    element = _wait_for_element(toolbox, task, timeout_s)
    if element is None:
        return RunResult("stopped", 2, f"Element never appeared: {task.baseline_target}")
    action = toolbox.call("check", {"ref": element["ref_id"]})
    if not action.get("ok"):
        return RunResult("stopped", 3, f"check failed: {action}")
    check = toolbox.verify()
    if check["met"]:
        return RunResult("verified", 4, f"Requirements met in {check['window_id']}")
    return RunResult("stopped", 4, f"Requirements unmet: {check['unmet']}")


def _wait_for_element(toolbox, task, timeout_s):
    """Polls quietly and records the wait as one observation: the tree the element was found
    in, or the last tree read when it never appeared."""
    started = toolbox.clock()
    deadline = started + timeout_s
    toolbox.forget_last_read()
    polls = 0
    while True:
        polls += 1
        element = _find_in_windows(toolbox, task.baseline_target)
        if element is not None or toolbox.clock() >= deadline:
            break
        toolbox.sleep(POLL_S)
    waited_ms = round((toolbox.clock() - started) * 1000)
    label = target_label(task.baseline_target)
    toolbox.report_wait({"text": label, "found": element is not None, "polls": polls, "waited_ms": waited_ms})
    return element


def _find_in_windows(toolbox, target):
    for tree in toolbox.observed_trees(source=None):
        element = find_element(target, tree)
        if element is not None and element.get("ref_id"):
            return element
    return None


def target_label(target):
    """A short description of the element condition, such as 'switch 설정 패널'."""
    words = [str(target[key]) for key in TARGET_LABEL_KEYS if target.get(key)]
    return " ".join(words + list(target.get("states") or []))
