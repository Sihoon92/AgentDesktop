"""The whole capture path with fakes: session -> block -> labels -> --block -> --read-block at home.

The session side (W1) and the reading side (W2) were built against the task-0 contract only;
this test runs the real `finish_session` at the end of a real `Session` and then the real
entry point, so a mismatch between the two sides fails here. No window, key, screen or model.
"""

import dataclasses
from types import SimpleNamespace

import pytest

import survey
from agent.survey_session import Session
from agent.survey_facts import BLOCK_TXT, EVENTS_JSONL, LABELS_YAML, SESSION_JSON, SUMMARY_MD
from agent.win32_facts import ProcessRow
from tests.capture_support import APP, APP_EXE, FOREIGN, MAIN, MARKERS, OTHER, OWN, window
from tests.test_survey_session import Env

POPUP = window(41, pid=OTHER, cls="#32770", rect=(300, 300, 600, 450))
CODE = SimpleNamespace(head="abc1234", changed=("x",))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(survey, "HERE", tmp_path)
    monkeypatch.setattr(survey, "code_state", lambda folder: CODE)
    return Env(tmp_path, monkeypatch)


def run_session_with_app_start_and_popup(env):
    """App not running at start; it starts; another process pops a dialog that takes the
    foreground from the app; one capture on the dialog, one on the main window; end."""
    env.win32.processes = [ProcessRow(OTHER, 1, r"C:\Tools\Other.exe", "b"),
                           ProcessRow(OWN, 1, r"C:\Python\python.exe", "c")]
    env.win32.windows, env.win32.fg = [FOREIGN], 21
    env.desktop.windows = [MAIN, POPUP, FOREIGN]
    deps = dataclasses.replace(env.deps(), finish=None, code_state=lambda folder: CODE)
    session = Session(env.args(), deps)
    console = session.start()

    env.win32.processes.append(ProcessRow(APP, 1, APP_EXE, "a"))
    env.win32.windows, env.win32.fg = [MAIN, FOREIGN], 11
    session.tick()
    env.win32.windows = [POPUP, MAIN, FOREIGN]
    session.tick()
    env.win32.fg = 41
    session.tick()

    session.worker.start()
    assert session.capture() == "C01"
    env.win32.windows, env.win32.fg = [MAIN, FOREIGN], 11
    session.tick()
    assert session.capture() == "C02"
    return session, console + session.end()


def test_session_to_block_to_read_block(env, capsys, tmp_path):
    session, console = run_session_with_app_start_and_popup(env)
    folder = session.folder
    block = console[console.index(next(line for line in console if line.startswith("[전달]"))):]

    assert block[0].startswith("[전달] v2 · abc1234 · 변경있음 1 · 연습 · 캡처2 · ")
    text = "\n".join(block)
    assert "H06 EXE" in text and "H15 OTHER_PROC" in text
    assert any(line.startswith("C01 ? DIALOG/OTHER/OK ·") for line in block)
    assert any(line.startswith("C02 ? MAIN/APP/OK ·") for line in block)
    assert (folder / BLOCK_TXT).read_text(encoding="utf-8").splitlines() == block

    (folder / LABELS_YAML).write_text("version: 1\ncaptures:\n  C01: ERROR_DIALOG\n  C02: MAIN\n", encoding="utf-8")
    capsys.readouterr()
    assert survey.main(["--block", "latest"]) == 0
    printed = capsys.readouterr().out
    relabeled = [line for line in printed.splitlines() if line.startswith(("[전달]", "H", "C"))]
    assert relabeled[0] == block[0]
    assert any(line.startswith("C01 ERROR_DIALOG DIALOG/OTHER/OK ·") for line in relabeled)
    assert any(line.startswith("C02 MAIN MAIN/APP/OK ·") for line in relabeled)

    copied = tmp_path / "copied.md"
    copied.write_text("# 2026-11-27 연습\n\n```\n" + "\n".join(relabeled) + "\n```\n", encoding="utf-8")
    assert survey.main(["--read-block", str(copied)]) == 0
    explained = capsys.readouterr().out
    assert f"줄 {len(relabeled)} · 오타? 0" in explained
    assert "오타? H" not in explained and "모르는 코드" not in explained

    name_free = [SESSION_JSON, EVENTS_JSONL, SUMMARY_MD, BLOCK_TXT]
    texts = ["\n".join(console), printed, explained] + [(folder / name).read_text(encoding="utf-8") for name in name_free]
    texts += [path.read_text(encoding="utf-8") for path in folder.glob("capture-*/facts.json")]
    for item in texts:
        assert not [marker for marker in MARKERS if marker in item]
        assert APP_EXE not in item


def test_typo_in_copied_block_is_caught_at_home(env, capsys, tmp_path):
    _session, console = run_session_with_app_start_and_popup(env)
    block = [line for line in console if line.startswith(("[전달]", "H", "C"))]
    damaged = list(block)
    damaged[1] = damaged[1].replace("H0", "H9", 1)
    copied = tmp_path / "copied.txt"
    copied.write_text("\n".join(damaged) + "\n", encoding="utf-8")
    capsys.readouterr()
    assert survey.main(["--read-block", str(copied)]) == 0
    assert f"줄 {len(block)} · 오타? 1" in capsys.readouterr().out
