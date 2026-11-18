from dataclasses import replace

from agent.systems import apply_ready, default_choice, system_exe, system_names
from agent.task import Task

ULSAN = "C:\\MES\\Ulsan\\MES.exe"
CHEONAN = "C:\\MES\\Cheonan\\MES.exe"
SETTINGS = {"systems": {"울산": ULSAN, "천안소형": CHEONAN}, "ready_text": "생산운영", "ready_timeout_s": 90}


def task(exe, **extra):
    return Task("열어", exe, 3, [], **extra)


def test_system_exe_and_names():
    assert system_exe(SETTINGS, "천안소형") == CHEONAN
    assert system_exe(SETTINGS, "없는곳") is None
    assert system_exe(SETTINGS, None) is None
    assert system_exe({}, "울산") is None
    assert system_names(SETTINGS) == ["울산", "천안소형"]
    assert system_names({}) == []


def test_default_choice_prefers_the_settings_system_then_the_first():
    assert default_choice({**SETTINGS, "system": "천안소형"}) == "천안소형"
    assert default_choice({**SETTINGS, "system": "다른곳"}) == "울산"
    assert default_choice(SETTINGS) == "울산"
    assert default_choice({"system": "울산"}) is None
    assert default_choice({}) is None


def test_apply_ready_only_for_a_configured_exe():
    same_but_spelled_differently = apply_ready(task("c:/mes/ulsan/MES.EXE"), SETTINGS)
    other = apply_ready(task("C:\\Tools\\Other.exe"), SETTINGS)
    settings_exe = apply_ready(task("C:\\Apps\\Tool.exe"), {"exe": "c:\\apps\\tool.exe", "ready_text": "생산운영"})

    assert (same_but_spelled_differently.ready_text, same_but_spelled_differently.ready_timeout_s) == ("생산운영", 90)
    assert (other.ready_text, other.ready_timeout_s) == (None, 180)
    assert (settings_exe.ready_text, settings_exe.ready_timeout_s) == ("생산운영", 180)


def test_apply_ready_does_not_match_a_path_that_only_shares_the_file_name():
    assert apply_ready(task("C:\\Elsewhere\\MES.exe"), SETTINGS).ready_text is None
    assert apply_ready(task("C:\\MES\\Ulsan\\MES.exe.bak"), SETTINGS).ready_text is None


def test_apply_ready_keeps_the_task_files_own_value():
    own = task(ULSAN, ready_text="메뉴", ready_timeout_s=30)
    own_text_only = task("C:\\Tools\\Other.exe", ready_text="메뉴")

    assert apply_ready(own, SETTINGS) is own
    assert apply_ready(own_text_only, SETTINGS) is own_text_only


def test_apply_ready_without_ready_text_changes_nothing():
    plain = task(ULSAN)
    no_text = {key: value for key, value in SETTINGS.items() if key != "ready_text"}

    assert apply_ready(plain, no_text) is plain
    assert apply_ready(plain, {}) is plain
    assert apply_ready(plain, None) is plain


def test_apply_ready_returns_a_new_task_and_keeps_everything_else():
    original = task(CHEONAN, system="천안소형", max_steps=30)
    applied = apply_ready(original, SETTINGS)

    assert applied == replace(original, ready_text="생산운영", ready_timeout_s=90)
    assert original.ready_text is None
