"""What the capture session reaches outside itself, and where its folder goes.

Tests replace every part of SessionDeps. The defaults import the PIL grabber, the button, the
code state and the report lazily, so this module loads without them.
"""

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent import win32_facts as default_win32
from agent.desktop import DesktopClient, start_session
from agent.handles import process_window_handles
from agent.hotkey import HotkeyThread

HERE = Path(__file__).resolve().parent.parent
UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SessionSetupError(Exception):
    """An argument, env or settings problem; the session ends with exit code 2."""


class NoDesktop:
    """Stands in for the CLI when no session could be started: every read fails safely."""

    def run(self, *args, headed=False):
        return {"ok": False, "error": {"code": "HARNESS_SPAWN"}}


def _grab(**kwargs):
    from PIL import ImageGrab

    return ImageGrab.grab(**kwargs)


def _default_button(on_capture, on_end):
    from agent.float_button import FloatButton

    return FloatButton(on_capture, on_end) if FloatButton.available() else None


def _check_deps():
    import check

    return check.Deps()


@dataclass
class SessionDeps:
    check: Any = field(default_factory=_check_deps)
    win32: Any = default_win32
    grab: Callable = _grab
    uia_runner: Callable = subprocess.run
    code_state: Callable | None = None
    finish: Callable | None = None
    captures_dir: Path = HERE / "captures"
    now: Callable = datetime.now
    clock: Callable = time.monotonic
    make_hotkeys: Callable = HotkeyThread
    make_button: Callable | None = _default_button
    make_desktop: Callable | None = None

    def desktop(self, binary):
        if self.make_desktop is not None:
            return self.make_desktop(binary)
        session = start_session(str(binary), "survey")
        if session is None:
            return None
        return DesktopClient(str(binary), session, window_handles=process_window_handles,
                             read_shortcut=self.check.read_shortcut)

    def code(self, folder):
        if self.code_state is not None:
            return self.code_state(folder)
        from agent.git_state import code_state

        return code_state(folder)

    def finished(self, folder):
        if self.finish is not None:
            return self.finish(folder)
        from agent.survey_report import finish_session

        return finish_session(folder)


def session_folder(root: Path, started: datetime, system: str) -> Path:
    base = f"{started:%Y%m%d-%H%M%S}-{UNSAFE_NAME.sub('_', system)}"
    Path(root).mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        folder = Path(root) / (base if number == 1 else f"{base}-{number}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise SessionSetupError("세션 폴더를 만들지 못했습니다")
