import pytest

from agent.task import DEFAULT_KEYS, MODEL_ENV, OPTIONAL_KEYS, TaskError, load_task, model_choice, task_from_args

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


def test_task_from_args_allows_sixty_steps():
    assert task_from_args("열어", r"C:\\a\\b.exe").max_steps == 60


def test_stage_defaults():
    task = task_from_args("열어", r"C:\a\b.exe")

    assert (task.stage_call_limit, task.plan_reasoning_effort, task.token_budget) == (8, "medium", 600_000)


def test_stage_keys_come_from_the_task_file(tmp_path):
    task = load_task(write(tmp_path, VALID + "stage_call_limit: 5\nplan_reasoning_effort: high\n"
                                             "token_budget: 300000\nmax_steps: 30\n"))

    assert (task.stage_call_limit, task.plan_reasoning_effort, task.token_budget, task.max_steps) == (
        5, "high", 300_000, 30)


def test_stage_keys_come_from_the_settings_and_the_task_file_beats_them(tmp_path):
    settings = {"stage_call_limit": 4, "plan_reasoning_effort": "high"}

    assert (task_from_args("열어", r"C:\a\b.exe", defaults=settings).stage_call_limit,
            task_from_args("열어", r"C:\a\b.exe", defaults=settings).plan_reasoning_effort) == (4, "high")
    from_file = load_task(write(tmp_path, VALID + "stage_call_limit: 6\n"), defaults=settings)
    assert (from_file.stage_call_limit, from_file.plan_reasoning_effort) == (6, "high")


@pytest.mark.parametrize("line", ["stage_call_limit: 0", "stage_call_limit: eight", "stage_call_limit: true",
                                  "plan_reasoning_effort: 3"])
def test_bad_stage_keys_are_rejected(tmp_path, line):
    with pytest.raises(TaskError):
        load_task(write(tmp_path, VALID + line + "\n"))


SETTINGS = {"model": "house-model", "reasoning_effort": "high", "image_mode": "off",
            "knowledge_dir": r"D:\kn", "system": "tool", "exe": r"C:\other\Other.exe"}


def test_task_from_args_takes_settings_in_place_of_code_defaults():
    task = task_from_args("열어", r"C:\a\b.exe", defaults=SETTINGS)

    assert (task.model, task.reasoning_effort, task.image_mode) == ("house-model", "high", "off")
    assert (task.knowledge_dir, task.system) == (r"D:\kn", "tool")
    assert task.exe == r"C:\a\b.exe"


def test_without_settings_the_code_defaults_stay():
    assert task_from_args("열어", r"C:\a\b.exe", defaults={}) == task_from_args("열어", r"C:\a\b.exe")
    assert task_from_args("열어", r"C:\a\b.exe").model == "gpt-6.1-terra"


def test_task_file_keys_beat_settings(tmp_path):
    task = load_task(write(tmp_path, NO_SUCCESS + "model: file-model\n"), defaults=SETTINGS)

    assert (task.model, task.system, task.image_mode) == ("file-model", "mes", "tool_output")
    assert task.knowledge_dir == r"D:\\knowledge"
    assert task.reasoning_effort == "high"
    assert task.exe == r"C:\\apps\\Mes.exe"


def test_settings_fill_what_the_task_file_leaves_out(tmp_path):
    task = load_task(write(tmp_path, VALID), defaults=SETTINGS)

    assert (task.model, task.reasoning_effort, task.image_mode) == ("house-model", "high", "off")
    assert (task.knowledge_dir, task.system) == (r"D:\kn", "tool")
    assert task.exe == r"C:\apps\Tool.exe"


def test_defaults_are_checked_like_task_keys():
    with pytest.raises(TaskError):
        task_from_args("열어", r"C:\a\b.exe", defaults={"image_mode": "inline"})


@pytest.mark.parametrize("line", ["model: 5", "reasoning_effort: [low]"])
def test_model_and_effort_must_be_text(tmp_path, line):
    with pytest.raises(TaskError):
        load_task(write(tmp_path, VALID + line + "\n"))


def test_model_env_name():
    assert MODEL_ENV == "OPENAI_MODEL"


def test_model_from_task_file_beats_settings_and_env():
    assert model_choice("a", {"model": "b"}, {"OPENAI_MODEL": "c"}) == ("a", "작업 파일")


def test_model_from_settings_beats_env():
    assert model_choice(None, {"model": "b"}, {"OPENAI_MODEL": "c"}) == ("b", "설정 파일")


def test_model_from_env_when_file_and_settings_lack_it():
    assert model_choice(None, {}, {"OPENAI_MODEL": "c"}) == ("c", "env")
    assert model_choice(None, None, {"OPENAI_MODEL": " c "}) == ("c", "env")


def test_blank_env_model_falls_to_the_default():
    assert model_choice(None, {}, {"OPENAI_MODEL": "  "}) == ("gpt-6.1-terra", "기본값")
    assert model_choice(None, None, None) == ("gpt-6.1-terra", "기본값")


def test_load_task_records_the_model_source(tmp_path):
    task = load_task(write(tmp_path, VALID), environ={"OPENAI_MODEL": "env-model"})

    assert (task.model, task.model_source) == ("env-model", "env")


def test_load_task_sources_for_file_settings_and_default(tmp_path):
    env = {"OPENAI_MODEL": "env-model"}
    from_file = load_task(write(tmp_path, VALID + "model: file-model\n"), defaults=SETTINGS, environ=env)
    from_settings = load_task(write(tmp_path, VALID), defaults=SETTINGS, environ=env)
    from_code = load_task(write(tmp_path, VALID))

    assert (from_file.model, from_file.model_source) == ("file-model", "작업 파일")
    assert (from_settings.model, from_settings.model_source) == ("house-model", "설정 파일")
    assert (from_code.model, from_code.model_source) == ("gpt-6.1-terra", "기본값")


def test_task_from_args_takes_env_model():
    task = task_from_args("열어", r"C:\a\b.exe", environ={"OPENAI_MODEL": "env-model"})

    assert (task.model, task.model_source) == ("env-model", "env")
    with_settings = task_from_args("열어", r"C:\a\b.exe", defaults=SETTINGS, environ={"OPENAI_MODEL": "env-model"})
    assert (with_settings.model, with_settings.model_source) == ("house-model", "설정 파일")


def test_without_environ_the_process_env_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "env-model")

    assert task_from_args("열어", r"C:\a\b.exe").model_source == "기본값"
    assert load_task(write(tmp_path, VALID)).model == "gpt-6.1-terra"


@pytest.mark.parametrize("key", ["reasoning_effort", "plan_reasoning_effort"])
def test_omit_is_a_valid_effort(tmp_path, key):
    task = load_task(write(tmp_path, VALID + f"{key}: omit\n"))

    assert getattr(task, key) == "omit"
    assert getattr(task_from_args("열어", r"C:\a\b.exe", defaults={key: "omit"}), key) == "omit"


def test_ready_defaults():
    task = task_from_args("열어", r"C:\a\b.exe")

    assert (task.ready_text, task.ready_timeout_s) == (None, 180)
    assert "ready_text" in OPTIONAL_KEYS and "ready_timeout_s" in OPTIONAL_KEYS
    assert "ready_text" not in DEFAULT_KEYS and "ready_timeout_s" not in DEFAULT_KEYS


def test_task_file_may_set_ready_text_and_timeout(tmp_path):
    task = load_task(write(tmp_path, VALID + "ready_text: 생산운영\nready_timeout_s: 60\n"))

    assert (task.ready_text, task.ready_timeout_s) == ("생산운영", 60)


def test_settings_ready_values_do_not_enter_through_defaults(tmp_path):
    settings = {"ready_text": "생산운영", "ready_timeout_s": 60}

    from_file = load_task(write(tmp_path, VALID), defaults=settings)
    from_args = task_from_args("열어", r"C:\a\b.exe", defaults=settings)

    assert (from_file.ready_text, from_file.ready_timeout_s) == (None, 180)
    assert (from_args.ready_text, from_args.ready_timeout_s) == (None, 180)


@pytest.mark.parametrize("line", ["ready_text: ''", "ready_text: '   '", "ready_text: 3", "ready_text: [a]",
                                  "ready_timeout_s: 0", "ready_timeout_s: -5", "ready_timeout_s: '90'",
                                  "ready_timeout_s: true", "ready_timeout_s: 2.5"])
def test_bad_ready_values_in_a_task_file_are_refused(tmp_path, line):
    with pytest.raises(TaskError):
        load_task(write(tmp_path, VALID + line + "\n"))



def test_profile_ready_fields_default_and_are_not_task_file_keys(tmp_path):
    task = load_task(write(tmp_path, VALID + "ready:\n  - {name: 생산운영}\nready_read: find\n"))

    assert (task.ready, task.ready_read) == ((), "snapshot")
    assert "ready" not in OPTIONAL_KEYS and "ready_read" not in OPTIONAL_KEYS
    assert "ready" not in DEFAULT_KEYS and "ready_read" not in DEFAULT_KEYS
    assert (task_from_args("열어", r"C:\a\b.exe").ready, task_from_args("열어", r"C:\a\b.exe").ready_read) == \
        ((), "snapshot")


def test_observe_deep_depth_defaults_to_30_and_comes_from_settings_or_the_task_file(tmp_path):
    exe = r"C:\apps\Tool.exe"
    assert task_from_args("goal", exe).observe_deep_depth == 30
    assert "observe_deep_depth" in DEFAULT_KEYS and "observe_deep_depth" in OPTIONAL_KEYS
    path = tmp_path / "task.yaml"
    path.write_text("goal: g\nexe: 'C:\\apps\\Tool.exe'\npermission_level: 2\nobserve_deep_depth: 12\n",
                    encoding="utf-8")
    assert load_task(path, defaults={"observe_deep_depth": 40}).observe_deep_depth == 12
    assert task_from_args("goal", exe, defaults={"observe_deep_depth": 40}).observe_deep_depth == 40


@pytest.mark.parametrize("value", [9, 51, "30", True])
def test_observe_deep_depth_outside_10_to_50_is_refused_in_a_task(value):
    with pytest.raises(TaskError):
        task_from_args("goal", r"C:\apps\Tool.exe", defaults={"observe_deep_depth": value})
