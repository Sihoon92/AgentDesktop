"""Loads the .env files of a run, with an optional profile such as home or company.

A profile names a second env file beside each `.env`: `.env.<profile>`. Its values come before
the plain `.env`, and a variable already set in the process environment comes before both, so
one PC can keep a home and a company setup and pick one with `--profile` or AGENT_PROFILE.
Nothing here prints a value; callers show the profile's name only.
"""

import os
import re
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROFILE_ENV = "AGENT_PROFILE"
PROFILE_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")


class ProfileError(Exception):
    """The profile cannot be used. The message is Korean and names the profile and file names only."""


def choose_profile(cli_value, base_files, environ=None):
    """The profile to use, or None: --profile, else AGENT_PROFILE in the environment, else
    AGENT_PROFILE in the first base file that sets it. A blank value counts as absent."""
    environ = os.environ if environ is None else environ
    for value in (cli_value, environ.get(PROFILE_ENV)):
        if isinstance(value, str) and value.strip():
            return _checked(value.strip())
    for path in base_files:
        if Path(path).is_file():
            value = dotenv_values(path).get(PROFILE_ENV)
            if isinstance(value, str) and value.strip():
                return _checked(value.strip())
    return None


def profile_files(profile, base_files):
    """`.env.<profile>` beside each base file, in the base files' order."""
    return [Path(path).with_name(f".env.{profile}") for path in base_files]


def load_env(cli_value, base_files, environ=None, loader=load_dotenv):
    """Loads the profile's files that exist and then the base files, and returns the profile or
    None. A chosen profile with no file at all is refused, so a typo never runs on the plain
    .env quietly."""
    profile = choose_profile(cli_value, base_files, environ)
    if profile is not None:
        found = [path for path in profile_files(profile, base_files) if path.is_file()]
        if not found:
            raise ProfileError(f"프로필 {profile}의 env 파일(.env.{profile})이 없습니다. "
                               f".env.example을 .env.{profile}로 복사해 채우거나 프로필을 고쳐 주세요")
        for path in found:
            loader(path)
    for path in base_files:
        loader(path)
    return profile


def _checked(value):
    if not PROFILE_NAME.fullmatch(value):
        raise ProfileError("프로필 이름은 영문, 숫자, '_', '-'로 32자까지 쓸 수 있습니다")
    return value
