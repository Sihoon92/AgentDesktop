import pytest

from agent.envfiles import PROFILE_ENV, ProfileError, choose_profile, load_env


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_no_profile_loads_only_the_base_files(tmp_path):
    base = write(tmp_path / ".env", "A=1\n")
    loaded = []

    assert load_env(None, [base], environ={}, loader=loaded.append) is None
    assert loaded == [base]


def test_profile_file_is_loaded_before_the_base_file(tmp_path):
    base = write(tmp_path / ".env", "A=1\n")
    company = write(tmp_path / ".env.company", "A=2\n")
    loaded = []

    assert load_env("company", [base], environ={}, loader=loaded.append) == "company"
    assert loaded == [company, base]


def test_profile_values_beat_the_base_file_and_the_environment_beats_both(tmp_path, monkeypatch):
    base = write(tmp_path / ".env", "OPENAI_MODEL=home-model\nVISION_MODEL=base-vision\n")
    write(tmp_path / ".env.company", "OPENAI_MODEL=company-model\nOPENAI_BASE_URL=http://inside\n")
    for name in ("OPENAI_MODEL", "VISION_MODEL"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://shell")

    load_env("company", [base])

    import os
    assert os.environ["OPENAI_MODEL"] == "company-model"
    assert os.environ["VISION_MODEL"] == "base-vision"
    assert os.environ["OPENAI_BASE_URL"] == "http://shell"


def test_profile_comes_from_the_environment_then_the_base_file(tmp_path):
    base = write(tmp_path / ".env", f"{PROFILE_ENV}=company\n")

    assert choose_profile(None, [base], environ={PROFILE_ENV: "home"}) == "home"
    assert choose_profile(None, [base], environ={}) == "company"
    assert choose_profile("lab", [base], environ={PROFILE_ENV: "home"}) == "lab"


def test_blank_profile_counts_as_absent(tmp_path):
    base = write(tmp_path / ".env", f"{PROFILE_ENV}=\n")

    assert choose_profile("  ", [base], environ={PROFILE_ENV: " "}) is None


def test_a_chosen_profile_without_a_file_is_refused(tmp_path):
    base = write(tmp_path / ".env", "A=1\n")
    loaded = []

    with pytest.raises(ProfileError) as error:
        load_env("company", [base], environ={}, loader=loaded.append)
    assert ".env.company" in str(error.value)
    assert loaded == []


@pytest.mark.parametrize("name", ["../x", "a b", "x" * 33, "회사"])
def test_bad_profile_names_are_refused(tmp_path, name):
    with pytest.raises(ProfileError):
        choose_profile(name, [], environ={})
