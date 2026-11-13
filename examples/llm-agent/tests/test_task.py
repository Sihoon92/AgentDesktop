import pytest

from agent.task import TaskError, load_task, task_from_args

VALID = """
goal: open settings
exe: C:\\apps\\Tool.exe
permission_level: 3
success:
  require:
    - {role: switch, states: [checked]}
"""


def write(tmp_path, text):
    path = tmp_path / "task.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_applied(tmp_path):
    task = load_task(write(tmp_path, VALID))

    assert task.permission_level == 3
    assert task.max_steps == 12
    assert task.model == "gpt-6.1-terra"
    assert task.require == [{"role": "switch", "states": ["checked"]}]


@pytest.mark.parametrize(
    "broken",
    [
        VALID.replace("permission_level: 3", "permission_level: 4"),
        VALID.replace("permission_level: 3", 'permission_level: "3"'),
        VALID.replace("exe: C:\\apps\\Tool.exe", "exe: Tool.exe"),
        VALID.replace("    - {role: switch, states: [checked]}", "    []"),
        VALID.replace("goal: open settings\n", ""),
    ],
)
def test_broken_task_is_rejected(tmp_path, broken):
    with pytest.raises(TaskError):
        load_task(write(tmp_path, broken))


def test_task_from_args_has_no_requirements():
    task = task_from_args("열어", r"C:\a\b.exe")

    assert task.require == []
    assert task.goal == "열어"
    assert task.permission_level == 3


def test_task_from_args_rejects_a_relative_exe():
    with pytest.raises(TaskError):
        task_from_args("열어", "b.exe")


NO_SUCCESS = """
goal: 조회 결과를 엑셀로 저장
exe: C:\\\\apps\\\\Mes.exe
permission_level: 2
system: mes
knowledge_dir: D:\\\\knowledge
image_mode: tool_output
ask_timeout_s: 600
mismatch_limit: 3
"""


def test_a_task_without_success_is_read_with_no_requirements(tmp_path):
    task = load_task(write(tmp_path, NO_SUCCESS))

    assert task.require == []
    assert (task.system, task.knowledge_dir, task.image_mode) == ("mes", "D:\\\\knowledge", "tool_output")
    assert (task.ask_timeout_s, task.mismatch_limit) == (600, 3)


def test_new_keys_default_when_absent(tmp_path):
    task = load_task(write(tmp_path, VALID))
    assert (task.system, task.knowledge_dir, task.image_mode) == (None, None, "user_message")
    assert (task.ask_timeout_s, task.mismatch_limit) == (1800, 2)


@pytest.mark.parametrize("line", [
    "image_mode: inline", "ask_timeout_s: 0", "ask_timeout_s: soon", "mismatch_limit: 0",
    "mismatch_limit: true", "system: 12", "knowledge_dir: []"])
def test_bad_new_keys_are_rejected(tmp_path, line):
    with pytest.raises(TaskError):
        load_task(write(tmp_path, VALID + line + "\n"))


def test_task_from_args_allows_forty_steps():
    assert task_from_args("열어", r"C:\\a\\b.exe").max_steps == 40
