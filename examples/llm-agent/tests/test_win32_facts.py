import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent import win32_facts
from agent.win32_facts import (ProcessRow, WindowRow, child_classes, foreground, is_hung, modules_of,
                               monitor_of, process_bits, process_image, process_rows, set_dpi_awareness,
                               top_windows, windows_dir)

AGENT = Path(__file__).resolve().parent.parent / "agent"
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")


@windows_only
def test_process_rows_include_a_child_with_its_parent():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        rows = {row.pid: row for row in process_rows()}
        assert child.pid in rows
        row = rows[child.pid]
        assert isinstance(row, ProcessRow) and row.parent == os.getpid()
        assert row.image and Path(row.image).name.lower().startswith("python")
        assert row.started and row.started.endswith("Z")
    finally:
        child.kill()
        child.wait()


@windows_only
def test_own_image_bits_and_modules():
    assert Path(process_image(os.getpid())).name.lower().startswith("python")
    modules = modules_of(os.getpid())
    assert modules and any(Path(m).name.lower() == "kernel32.dll" for m in modules)
    assert process_bits(os.getpid()) in (32, 64)
    assert Path(windows_dir()).is_dir()


@windows_only
def test_denied_process_reads_as_none(monkeypatch):
    monkeypatch.setattr(win32_facts, "_open_process", lambda pid: None)
    assert modules_of(os.getpid()) is None
    assert process_image(os.getpid()) is None
    assert process_bits(os.getpid()) is None


def test_dead_handles_read_as_empty():
    assert child_classes(0) == ()
    assert monitor_of(0) is None
    assert is_hung(0) is False
    assert process_image(0) is None and modules_of(-1) is None


@windows_only
def test_top_windows_rows_are_well_formed():
    rows = top_windows()
    assert rows
    for row in rows:
        assert isinstance(row, WindowRow) and isinstance(row.class_name, str)
        assert len(row.rect) == 4 and all(isinstance(value, int) for value in row.rect)
        assert row.owner_enabled or row.owner
    assert foreground() is None or isinstance(foreground(), int)


def test_errors_never_escape(monkeypatch):
    def broken():
        raise OSError("no api")
    monkeypatch.setattr(win32_facts, "_api", broken)
    assert top_windows() == () and process_rows() == () and foreground() is None
    assert windows_dir() == "" and win32_facts.beep() is None


def test_dpi_awareness_falls_back_in_order(monkeypatch):
    if sys.platform != "win32":
        assert set_dpi_awareness() == "NONE"
        return
    calls = []

    def step(name, result):
        def run():
            calls.append(name)
            if isinstance(result, Exception):
                raise result
            return result
        return (name, run)

    monkeypatch.setattr(win32_facts, "_DPI_STEPS", (step("PER_MONITOR_V2", OSError()), step("PER_MONITOR", False),
                                                    step("SYSTEM", True)))
    assert set_dpi_awareness() == "SYSTEM" and calls == ["PER_MONITOR_V2", "PER_MONITOR", "SYSTEM"]
    monkeypatch.setattr(win32_facts, "_DPI_STEPS", (step("PER_MONITOR_V2", False),))
    assert set_dpi_awareness() == "NONE"


def test_module_never_reads_window_text():
    assert "GetWindowText" not in (AGENT / "win32_facts.py").read_text(encoding="utf-8")
