import os
import re
import sys

import pytest

from agent import process_identity
from agent.process_identity import process_started_at

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="reads Windows process times")


@windows_only
def test_own_process_has_a_stable_utc_millisecond_start():
    first = process_started_at(os.getpid())

    assert STAMP.match(first)
    assert process_started_at(os.getpid()) == first


@pytest.mark.parametrize("pid", [0xFFFFFFFC, 2**40, -1, 0, "1234", None])
def test_missing_or_invalid_pid_is_none(pid):
    assert process_started_at(pid) is None


def test_other_platforms_return_none(monkeypatch):
    monkeypatch.setattr(process_identity.sys, "platform", "linux")

    assert process_started_at(os.getpid()) is None


class FailingTimes:
    def __init__(self, exit_time=0, times_ok=False):
        self.closed = []
        self._exit_time = exit_time
        self._times_ok = times_ok

    def OpenProcess(self, access, inherit, pid):
        return 4242

    def GetProcessTimes(self, handle, creation, exit_, kernel, user):
        creation._obj.dwLowDateTime = 1
        exit_._obj.dwLowDateTime = self._exit_time
        if not self._times_ok:
            raise OSError("times unavailable")
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


@pytest.mark.parametrize("api", [FailingTimes(), FailingTimes(exit_time=7, times_ok=True)])
def test_handle_is_closed_when_times_fail_or_the_process_exited(monkeypatch, api):
    monkeypatch.setattr(process_identity.sys, "platform", "win32")
    monkeypatch.setattr(process_identity, "_kernel32", lambda: api)

    assert process_started_at(1234) is None
    assert api.closed == [4242]
