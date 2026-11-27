"""UI Automation framework ids and pattern counts of one window, by a PowerShell script.

The script (`uia_count.ps1`) walks the raw view breadth first up to `UIA_MAX_NODES` and prints
one JSON line of counts. Framework ids are toolkit names (`WinForm`, `Win32`, `WPF`, ...), not
screen text, and only the listed pattern names are kept. A failure becomes a code, never an
exception.
"""

import json
import subprocess
import sys
from pathlib import Path

from agent.survey_codes import UIA_MAX_NODES, UIA_TIMEOUT_S
from agent.survey_facts import UiaCounts

SCRIPT = Path(__file__).resolve().parent / "uia_count.ps1"
PATTERNS = ("Invoke", "Value", "Toggle", "ExpandCollapse", "Selection", "Grid", "Table", "LegacyIAccessible")


def _counts(value) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    if not all(isinstance(key, str) and type(count) is int for key, count in value.items()):
        return None
    return dict(value)


def parse_counts(text: str) -> UiaCounts | None:
    """The counts of one script output line, or None when it is not that shape."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or type(data.get("nodes")) is not int or type(data.get("truncated")) is not bool:
        return None
    frameworks = _counts(data.get("frameworks"))
    patterns = _counts(data.get("patterns"))
    if frameworks is None or patterns is None:
        return None
    return UiaCounts(data["nodes"], data["truncated"], frameworks,
                     {name: count for name, count in patterns.items() if name in PATTERNS})


def command(hwnd: int) -> list[str]:
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
            "-Hwnd", str(int(hwnd)), "-MaxNodes", str(UIA_MAX_NODES)]


def count_uia(hwnd: int, runner=subprocess.run) -> tuple[UiaCounts | None, str | None]:
    """(counts, None) or (None, one of UIA_SPAWN, UIA_TIMEOUT, UIA_EXIT, UIA_OUTPUT)."""
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        done = runner(command(hwnd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                      timeout=UIA_TIMEOUT_S, stdin=subprocess.DEVNULL, **flags)
    except subprocess.TimeoutExpired:
        return None, "UIA_TIMEOUT"
    except (OSError, ValueError):
        return None, "UIA_SPAWN"
    if done.returncode != 0:
        return None, "UIA_EXIT"
    lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
    counts = parse_counts(lines[-1]) if lines else None
    if counts is None:
        return None, "UIA_OUTPUT"
    return counts, None
