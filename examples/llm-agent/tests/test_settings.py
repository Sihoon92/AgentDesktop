from pathlib import Path

import pytest

from agent import settings
from agent.settings import DEFAULT_PATH, KEYS, SETTINGS_ENV, SettingsError, load_settings, settings_path

HERE = Path(__file__).resolve().parent.parent
FULL = """
model: house-model-1
reasoning_effort: medium
image_mode: tool_output
knowledge_dir: C:\\data\\knowledge
exe: C:\\apps\\Tool.exe
system: tool
"""


def write(tmp_path, text, name="settings.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_contract_names():
    assert SETTINGS_ENV == "AGENT_SETTINGS"
    assert DEFAULT_PATH == HERE / "settings.local.yaml"
    assert KEYS == ("model", "reasoning_effort", "image_mode", "knowledge_dir", "exe", "system", "stage_call_limit",
                    "plan_reasoning_effort", "vision_model", "vision_reasoning_effort", "vision_max_side",
                    "vision_points", "systems", "ready_text", "ready_timeout_s", "workflow_dir", "profile_dir")


def test_every_key_is_read(tmp_path):
    loaded = load_settings(write(tmp_path, FULL), environ={})

    assert loaded == {
        "model": "house-model-1",
        "reasoning_effort": "medium",
        "image_mode": "tool_output",
        "knowledge_dir": "C:\\data\\knowledge",
        "exe": "C:\\apps\\Tool.exe",
        "system": "tool",
    }


def test_missing_default_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_PATH", tmp_path / "settings.local.yaml")

    assert load_settings(environ={}) == {}


def test_default_file_is_read_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_PATH", write(tmp_path, "model: m1\n", "settings.local.yaml"))

    assert load_settings(environ={}) == {"model": "m1"}


def test_environment_variable_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_PATH", write(tmp_path, "model: from-default\n", "settings.local.yaml"))
    chosen = write(tmp_path, "model: from-env\n", "other.yaml")

    assert load_settings(environ={SETTINGS_ENV: str(chosen)}) == {"model": "from-env"}
    assert settings_path(environ={SETTINGS_ENV: str(chosen)}) == chosen.resolve()


def test_argument_beats_environment_variable(tmp_path):
    env_file = write(tmp_path, "model: from-env\n", "env.yaml")
    arg_file = write(tmp_path, "model: from-arg\n", "arg.yaml")

    assert load_settings(arg_file, environ={SETTINGS_ENV: str(env_file)}) == {"model": "from-arg"}


def test_empty_environment_variable_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_PATH", tmp_path / "absent.yaml")

    assert settings_path(environ={SETTINGS_ENV: "  "}) == tmp_path / "absent.yaml"
    assert load_settings(environ={SETTINGS_ENV: ""}) == {}


def test_uses_process_environment_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv(SETTINGS_ENV, str(write(tmp_path, "model: from-process\n")))

    assert load_settings() == {"model": "from-process"}


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_named_file_that_does_not_exist_is_an_error(tmp_path, source):
    missing = tmp_path / "typo.yaml"
    path, environ = (missing, {}) if source == "argument" else (None, {SETTINGS_ENV: str(missing)})

    with pytest.raises(SettingsError, match="설정 파일이 없습니다"):
        load_settings(path, environ=environ)


@pytest.mark.parametrize("text", ["", "# only a comment\n", "model:\n"])
def test_empty_file_or_empty_values_set_nothing(tmp_path, text):
    assert load_settings(write(tmp_path, text), environ={}) == {}


def test_values_are_trimmed(tmp_path):
    assert load_settings(write(tmp_path, "model: '  m1  '\n"), environ={}) == {"model": "m1"}


def test_unknown_key_is_rejected_by_name(tmp_path):
    with pytest.raises(SettingsError, match="모르는 키") as caught:
        load_settings(write(tmp_path, "model: m1\nmodle: m2\nbase_url: x\n"), environ={})

    assert "base_url" in str(caught.value) and "modle" in str(caught.value)


@pytest.mark.parametrize("mode", ["image", "USER_MESSAGE", "none"])
def test_wrong_image_mode_is_rejected(tmp_path, mode):
    with pytest.raises(SettingsError, match="image_mode"):
        load_settings(write(tmp_path, f"image_mode: {mode}\n"), environ={})


@pytest.mark.parametrize("mode", ["off", "user_message", "tool_output"])
def test_every_image_mode_is_accepted(tmp_path, mode):
    assert load_settings(write(tmp_path, f"image_mode: {mode}\n"), environ={}) == {"image_mode": mode}


@pytest.mark.parametrize("exe", ["Tool.exe", "apps\\Tool.exe", "./Tool.exe"])
def test_relative_exe_is_rejected(tmp_path, exe):
    with pytest.raises(SettingsError, match="exe"):
        load_settings(write(tmp_path, f"exe: '{exe}'\n"), environ={})


@pytest.mark.parametrize("line", ["model: 3", "model: [a, b]", "reasoning_effort: true", "system: {a: 1}",
                                  "knowledge_dir: 5", "exe: 1", "model: ''"])
def test_values_must_be_non_empty_text(tmp_path, line):
    with pytest.raises(SettingsError, match="글"):
        load_settings(write(tmp_path, line + "\n"), environ={})


@pytest.mark.parametrize("name", ["a/b", "a b", "x" * 65])
def test_system_follows_the_knowledge_folder_name_rule(tmp_path, name):
    with pytest.raises(SettingsError, match="system"):
        load_settings(write(tmp_path, f"system: '{name}'\n"), environ={})


def test_korean_system_name_is_accepted(tmp_path):
    assert load_settings(write(tmp_path, "system: 생산관리\n"), environ={}) == {"system": "생산관리"}


def test_relative_knowledge_dir_is_taken_from_the_settings_file_folder(tmp_path):
    folder = tmp_path / "conf"
    folder.mkdir()

    loaded = load_settings(write(folder, "knowledge_dir: kn\n"), environ={})

    assert loaded == {"knowledge_dir": str((folder / "kn").resolve())}


@pytest.mark.parametrize("text", ["- model\n- m1\n", "just text\n", "42\n"])
def test_file_must_hold_keys_and_values(tmp_path, text):
    with pytest.raises(SettingsError, match="키와 값"):
        load_settings(write(tmp_path, text), environ={})


def test_broken_yaml_is_rejected_with_its_line(tmp_path):
    with pytest.raises(SettingsError, match="3행"):
        load_settings(write(tmp_path, "model: m1\nsystem: s\nexe: a: b\n"), environ={})


def test_file_that_is_not_utf8_is_rejected(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_bytes("model: 모델\n".encode("cp949"))

    with pytest.raises(SettingsError, match="UTF-8"):
        load_settings(path, environ={})


def test_errors_name_the_file(tmp_path):
    path = write(tmp_path, "image_mode: bad\n")

    with pytest.raises(SettingsError) as caught:
        load_settings(path, environ={})

    assert str(path) in str(caught.value)


def test_example_file_holds_every_key_and_no_values():
    example = HERE / "settings.example.yaml"
    text = example.read_text(encoding="utf-8")

    for key in KEYS:
        assert f"# {key}:" in text
    assert load_settings(example, environ={}) == {}


def test_local_settings_and_check_folder_are_ignored_by_git():
    lines = (HERE / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/settings.local.yaml" in lines
    assert "/.check/" in lines


B = "\\"
PATH_MARK = f"C:{B}Users{B}secret-user{B}VirtualMes.exe"


def reason_of(tmp_path, text):
    with pytest.raises(SettingsError) as caught:
        load_settings(write(tmp_path, text), environ={})
    return caught.value


def test_unquoted_and_single_quoted_windows_paths_are_read_as_written(tmp_path):
    for line in (f"exe: {PATH_MARK}\n", f"exe: '{PATH_MARK}'\n"):
        assert load_settings(write(tmp_path, line), environ={}) == {"exe": PATH_MARK}


@pytest.mark.parametrize("folder", [f"D:{B}Users{B}team{B}knowledge", f"'D:{B}Users{B}team{B}knowledge'"])
def test_unquoted_and_single_quoted_knowledge_dirs_are_read_as_written(tmp_path, folder):
    loaded = load_settings(write(tmp_path, f"knowledge_dir: {folder}\n"), environ={})
    assert loaded == {"knowledge_dir": str(Path(f"D:{B}Users{B}team{B}knowledge").resolve())}


@pytest.mark.parametrize("line", [f'exe: "{PATH_MARK}"', f'exe: "C:{B}Apps{B}VirtualMes.exe"',
                                  f'model: m1\nsystem: s\nexe:   "{PATH_MARK}"  '])
def test_backslash_inside_double_quotes_gets_the_quote_hint_without_the_line(tmp_path, line):
    error = reason_of(tmp_path, line + "\n")
    row = line.count("\n") + 1
    assert f"YAML 형식이 틀렸습니다: {row}행" in str(error)
    assert settings.QUOTE_HINT in str(error)
    assert error.reason == f"YAML 형식 오류 {row}행 · 큰따옴표 안의 역슬래시 · {settings.QUOTE_HINT}"
    assert "secret-user" not in error.reason and "VirtualMes" not in error.reason


def test_other_yaml_errors_get_no_quote_hint(tmp_path):
    error = reason_of(tmp_path, 'model: m1\nexe: "C:/Apps/a.exe" "x"\n')
    assert error.reason == "YAML 형식 오류 2행"
    assert settings.QUOTE_HINT not in str(error)


def test_double_quoted_backslash_on_another_line_gives_no_hint(tmp_path):
    error = reason_of(tmp_path, f'exe: "C:{B}{B}Apps{B}{B}a.exe"\nsystem: a: b\n')
    assert error.reason == "YAML 형식 오류 2행"


def test_correctly_escaped_double_quoted_path_is_read(tmp_path):
    text = f'exe: "C:{B}{B}Apps{B}{B}a.exe"\n'
    assert load_settings(write(tmp_path, text), environ={}) == {"exe": f"C:{B}Apps{B}a.exe"}


@pytest.mark.parametrize("key", ["exe", "knowledge_dir"])
def test_double_quoted_path_that_yaml_reads_as_control_characters_is_rejected(tmp_path, key):
    error = reason_of(tmp_path, f'{key}: "C:{B}tools{B}new{B}a-secret.exe"\n')
    assert error.reason == f"{key} 경로에 제어 문자 · {settings.QUOTE_HINT}"
    assert settings.QUOTE_HINT in str(error)


@pytest.mark.parametrize(("text", "reason"), [
    ("model: m1\nmodle: secret-key-1\nsecret-key-2: x\n", "모르는 키 2개"),
    ("image_mode: secret-mode\n", "image_mode 값이 off, user_message, tool_output 중 하나가 아님"),
    ("exe: secret-tool.exe\n", "exe가 절대 경로가 아님"),
    ("system: 'secret name'\n", "system 이름 규칙에 맞지 않음"),
    ("model: [secret]\n", "model 값이 글이 아님"),
    ("- secret\n", "'키: 값' 줄이 아님"),
    ("model: m1\nsystem: s\nexe: secret: b\n", "YAML 형식 오류 3행"),
])
def test_reason_is_short_and_holds_no_value_or_typed_key(tmp_path, text, reason):
    error = reason_of(tmp_path, text)
    assert error.reason == reason
    assert "secret" not in error.reason and str(tmp_path) not in error.reason
    assert str(tmp_path) in str(error)


def test_reason_for_a_file_that_is_not_utf8_or_missing(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_bytes("model: 모델\n".encode("cp949"))
    with pytest.raises(SettingsError) as caught:
        load_settings(path, environ={})
    assert caught.value.reason == "UTF-8이 아님"
    with pytest.raises(SettingsError) as caught:
        load_settings(tmp_path / "absent.yaml", environ={})
    assert caught.value.reason == "설정 파일 없음"


def test_example_file_tells_how_to_write_paths():
    text = (HERE / "settings.example.yaml").read_text(encoding="utf-8")
    for key in ("exe", "knowledge_dir"):
        block = text.split(f"# {key}:")[0].rsplit("\n\n", 1)[-1]
        assert "따옴표 없이 적습니다. 큰따옴표 안에서는 역슬래시가 특수 문자입니다" in block
        assert f"#   예) {key}: " in block


def test_stage_keys_are_read(tmp_path):
    loaded = load_settings(write(tmp_path, "stage_call_limit: 5\nplan_reasoning_effort: high\n"), environ={})

    assert loaded == {"stage_call_limit": 5, "plan_reasoning_effort": "high"}


@pytest.mark.parametrize("value", ["0", "-1", "eight", "true", "2.5", "'8'"])
def test_a_stage_call_limit_must_be_a_whole_number_of_at_least_one(tmp_path, value):
    with pytest.raises(SettingsError) as raised:
        load_settings(write(tmp_path, f"stage_call_limit: {value}\n"), environ={})

    assert "stage_call_limit" in raised.value.reason and value not in raised.value.reason


def test_vision_keys_are_read(tmp_path):
    text = "vision_model: eye-1\nvision_reasoning_effort: low\nvision_max_side: 1024\nvision_points: normalized_1000\n"

    assert load_settings(write(tmp_path, text), environ={}) == {
        "vision_model": "eye-1", "vision_reasoning_effort": "low", "vision_max_side": 1024,
        "vision_points": "normalized_1000"}


def test_vision_points_must_be_a_known_mode(tmp_path):
    assert load_settings(write(tmp_path, "vision_points: pixel\n"), environ={}) == {"vision_points": "pixel"}
    with pytest.raises(SettingsError) as raised:
        load_settings(write(tmp_path, "vision_points: percent\n"), environ={})

    assert "vision_points" in raised.value.reason and "percent" not in raised.value.reason


@pytest.mark.parametrize("value", ["0", "-1", "big", "true", "2.5", "'1280'"])
def test_vision_max_side_must_be_a_positive_integer(tmp_path, value):
    with pytest.raises(SettingsError) as raised:
        load_settings(write(tmp_path, f"vision_max_side: {value}\n"), environ={})

    assert "vision_max_side" in raised.value.reason and value not in raised.value.reason
    assert "1 이상의 정수" in raised.value.reason


def test_vision_points_modes():
    assert settings.VISION_POINT_MODES == ("pixel", "normalized_1000")


def test_example_file_tells_about_omit_and_the_env_model():
    text = (HERE / "settings.example.yaml").read_text(encoding="utf-8")

    assert "omit" in text
    assert "OPENAI_MODEL" in text


SYSTEMS = f"systems:\n  울산: C:{B}A{B}MES.exe\n  천안소형: '  C:{B}B{B}MES.exe  '\n"
NAME_OR_PATH_MARKS = ("울산", "천안", "MES.exe", "C:\\", "a.exe", "rel.exe", "a b")


def test_systems_keep_their_order_and_paths(tmp_path):
    loaded = load_settings(write(tmp_path, SYSTEMS), environ={})

    assert loaded == {"systems": {"울산": f"C:{B}A{B}MES.exe", "천안소형": f"C:{B}B{B}MES.exe"}}
    assert list(loaded["systems"]) == ["울산", "천안소형"]


def test_systems_read_beside_the_other_keys(tmp_path):
    loaded = load_settings(write(tmp_path, FULL + SYSTEMS), environ={})

    assert loaded["exe"] == "C:\\apps\\Tool.exe" and list(loaded["systems"]) == ["울산", "천안소형"]


def test_ready_keys_are_read(tmp_path):
    loaded = load_settings(write(tmp_path, "ready_text: '  생산운영 '\nready_timeout_s: 90\n"), environ={})

    assert loaded == {"ready_text": "생산운영", "ready_timeout_s": 90}


@pytest.mark.parametrize(("body", "reason"), [
    (f"systems: C:{B}A.exe", "systems 값이 이름과 경로의 묶음이 아님"),
    ("systems: [a, b]", "systems 값이 이름과 경로의 묶음이 아님"),
    ("systems: {}", "systems 값이 비어 있음"),
    (f"systems: {{울산: D{B}rel.exe}}", "systems 1번째 항목: 경로가 절대 경로가 아님"),
    (f"systems: {{'a b': C:{B}A.exe}}", "systems 1번째 항목: 이름 규칙에 맞지 않음"),
    (f"systems: {{3: C:{B}A.exe}}", "systems 1번째 항목: 이름 규칙에 맞지 않음"),
    ("systems: {울산: 3}", "systems 1번째 항목: 경로가 글이 아님"),
    ("systems: {울산: }", "systems 1번째 항목: 경로가 글이 아님"),
    ("systems: {울산: '  '}", "systems 1번째 항목: 경로가 글이 아님"),
    (f"systems:\n  울산: C:{B}A{B}MES.exe\n  천안소형: D{B}rel.exe", "systems 2번째 항목: 경로가 절대 경로가 아님"),
    (f"systems:\n  울산: C:{B}A{B}MES.exe\n  천안소형: C:{B}B{B}MES.exe\n  'a b': C:{B}C{B}MES.exe",
     "systems 3번째 항목: 이름 규칙에 맞지 않음"),
    (f'systems:\n  울산: C:{B}A{B}MES.exe\n  천안소형: "C:{B}new{B}tools{B}a.exe"',
     f"systems 2번째 항목: 경로에 제어 문자 · {settings.QUOTE_HINT}"),
    (f'systems: {{울산: "C:{B}new{B}MES.exe"}}', f"YAML 형식 오류 1행 · 큰따옴표 안의 역슬래시 · {settings.QUOTE_HINT}"),
])
def test_bad_systems_are_refused_without_names_or_paths(tmp_path, body, reason):
    error = reason_of(tmp_path, body + "\n")

    assert error.reason == reason
    assert str(tmp_path) in str(error)
    message = str(error).replace(str(tmp_path), "")
    for mark in NAME_OR_PATH_MARKS:
        assert mark not in error.reason
        assert mark not in message


def test_control_characters_in_a_system_path_get_the_quote_hint_in_the_message(tmp_path):
    error = reason_of(tmp_path, f'systems:\n  울산: "C:{B}new{B}tools{B}a.exe"\n')

    assert "systems 1번째 항목" in str(error) and settings.QUOTE_HINT in str(error)
    assert "큰따옴표 안의 역슬래시" in str(error)


@pytest.mark.parametrize("value", ["0", "-1", "'90'", "soon", "true", "2.5"])
def test_ready_timeout_must_be_a_positive_whole_number(tmp_path, value):
    error = reason_of(tmp_path, f"ready_timeout_s: {value}\n")

    assert error.reason == "ready_timeout_s 값이 1 이상의 정수가 아님"


@pytest.mark.parametrize("value", ["''", "'   '", "[a]", "3"])
def test_ready_text_must_be_non_empty_text(tmp_path, value):
    error = reason_of(tmp_path, f"ready_text: {value}\n")

    assert error.reason == "ready_text 값이 글이 아님"


def test_example_file_shows_systems_with_made_up_names():
    text = (HERE / "settings.example.yaml").read_text(encoding="utf-8")
    block = text.split("# systems:")[1].split("\n\n")[0]

    assert "공장A" in block
    assert "따옴표 없이 적습니다. 큰따옴표 안에서는 역슬래시가 특수 문자입니다" in text.split("# systems:")[0].rsplit("\n\n", 1)[-1]
    for real in ("울산", "천안"):
        assert real not in text
    assert "# ready_text:" in text and "# ready_timeout_s:" in text


def test_uncommented_example_systems_block_is_valid(tmp_path):
    text = (HERE / "settings.example.yaml").read_text(encoding="utf-8")
    block = text.split("# systems:")[1].split("\n\n")[0]
    lines = ["systems:"] + [line[1:] for line in block.splitlines() if line.startswith("#   ")]

    loaded = load_settings(write(tmp_path, "\n".join(lines) + "\n"), environ={})

    assert "공장A" in loaded["systems"]




def test_workflow_dir_is_a_path_key(tmp_path):
    folder = tmp_path / "settings"
    folder.mkdir()

    assert "workflow_dir" in settings.PATH_KEYS
    assert load_settings(write(folder, "workflow_dir: wf\n"), environ={}) == {
        "workflow_dir": str((folder / "wf").resolve())}
    assert load_settings(write(folder, f"workflow_dir: D:{B}team{B}workflows\n"), environ={}) == {
        "workflow_dir": str(Path(f"D:{B}team{B}workflows").resolve())}


def test_a_double_quoted_workflow_dir_read_as_control_characters_is_rejected(tmp_path):
    error = reason_of(tmp_path, f'workflow_dir: "C:{B}tools{B}new"\n')

    assert error.reason == f"workflow_dir 경로에 제어 문자 · {settings.QUOTE_HINT}"


def test_example_file_tells_how_to_write_the_workflow_dir():
    text = (HERE / "settings.example.yaml").read_text(encoding="utf-8")
    block = text.split("# workflow_dir:")[0].rsplit("\n\n", 1)[-1]

    assert "따옴표 없이 적습니다. 큰따옴표 안에서는 역슬래시가 특수 문자입니다" in block
    assert "#   예) workflow_dir: " in block
    assert "AGENT_WORKFLOW_DIR" in block


def test_profile_dir_is_a_folder_key_taken_from_the_settings_folder(tmp_path):
    folder = tmp_path / "settings"
    folder.mkdir()

    assert "profile_dir" in settings.PATH_KEYS and "profile_dir" in settings.FOLDER_KEYS
    assert load_settings(write(folder, "profile_dir: profiles\n"), environ={}) == {
        "profile_dir": str((folder / "profiles").resolve())}
    assert load_settings(write(folder, f"profile_dir: D:{B}team{B}profiles\n"), environ={}) == {
        "profile_dir": str(Path(f"D:{B}team{B}profiles").resolve())}


def test_a_double_quoted_profile_dir_read_as_control_characters_is_rejected(tmp_path):
    error = reason_of(tmp_path, f'profile_dir: "C:{B}tools{B}new"\n')

    assert error.reason == f"profile_dir 경로에 제어 문자 · {settings.QUOTE_HINT}"


def test_example_file_tells_how_to_write_the_profile_dir():
    text = (HERE / "settings.example.yaml").read_text(encoding="utf-8")
    block = text.split("# profile_dir:")[0].rsplit("\n\n", 1)[-1]

    assert "따옴표 없이 적습니다. 큰따옴표 안에서는 역슬래시가 특수 문자입니다" in block
    assert "#   예) profile_dir: " in block
    assert "AGENT_PROFILE_DIR" in block
    assert "--profile-dir" in block
