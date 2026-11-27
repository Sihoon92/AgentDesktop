import subprocess
from pathlib import Path

import pytest

from agent.survey_codes import UIA_MAX_NODES, UIA_TIMEOUT_S
from agent.survey_facts import UiaCounts
from agent.uia_facts import count_uia, parse_counts

AGENT = Path(__file__).resolve().parent.parent / "agent"
GOOD = '{"nodes":1,"truncated":false,"frameworks":{},"patterns":{}}'


def test_parse_keeps_only_listed_patterns():
    counts = parse_counts('{"nodes":12,"truncated":false,"frameworks":{"WinForm":11,"(none)":1},'
                          '"patterns":{"Invoke":3,"Text":2}}')
    assert counts == UiaCounts(12, False, {"WinForm": 11, "(none)": 1}, {"Invoke": 3})


@pytest.mark.parametrize("text", ["", "not json", '{"nodes":"x"}', "[]",
                                  '{"nodes":1,"truncated":"no","frameworks":{},"patterns":{}}',
                                  '{"nodes":1,"truncated":false,"frameworks":{"a":"b"},"patterns":{}}',
                                  '{"nodes":1,"truncated":false,"frameworks":{}}'])
def test_broken_output_is_none(text):
    assert parse_counts(text) is None


def test_runner_failures_become_codes():
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("powershell", UIA_TIMEOUT_S)
    assert count_uia(1, timeout) == (None, "UIA_TIMEOUT")

    def exits(*a, **k):
        return subprocess.CompletedProcess(a, 1, "", "denied")
    assert count_uia(1, exits) == (None, "UIA_EXIT")

    def missing(*a, **k):
        raise FileNotFoundError("powershell")
    assert count_uia(1, missing) == (None, "UIA_SPAWN")

    def garbage(*a, **k):
        return subprocess.CompletedProcess(a, 0, "warning\nnot json\n", "")
    assert count_uia(1, garbage) == (None, "UIA_OUTPUT")


def test_last_output_line_is_read():
    def run(argv, **k):
        return subprocess.CompletedProcess(argv, 0, "noise\n" + GOOD + "\n\n", "")
    assert count_uia(5, run) == (UiaCounts(1, False, {}, {}), None)


def test_command_uses_bypass_the_repo_script_and_the_limits():
    seen = {}

    def run(argv, **k):
        seen["argv"], seen["kwargs"] = argv, k
        return subprocess.CompletedProcess(argv, 0, GOOD, "")

    count_uia(42, run)
    argv = seen["argv"]
    assert "-ExecutionPolicy" in argv and "Bypass" in argv
    assert argv[argv.index("-Hwnd") + 1] == "42"
    assert argv[argv.index("-MaxNodes") + 1] == str(UIA_MAX_NODES) == "3000"
    assert Path(argv[argv.index("-File") + 1]) == AGENT / "uia_count.ps1"
    assert seen["kwargs"]["timeout"] == UIA_TIMEOUT_S == 30
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL


def test_script_never_reads_names_or_values():
    text = (AGENT / "uia_count.ps1").read_text(encoding="utf-8")
    for forbidden in ("Current.Name", "AutomationId", "ValuePattern", "HelpText", "ClassName"):
        assert forbidden not in text
