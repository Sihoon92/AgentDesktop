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
    assert KEYS == ("model", "reasoning_effort", "image_mode", "knowledge_dir", "exe", "system")


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
