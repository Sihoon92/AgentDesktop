"""Runs the viewer's progress.js tests with Node when Node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_progress_js_passes_its_node_tests():
    completed = subprocess.run(["node", "--test", str(HERE / "js" / "progress.test.mjs")],
                               capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
