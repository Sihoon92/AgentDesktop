import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.git_state import CodeState, code_state

IDENTITY = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    return subprocess.run(["git", *IDENTITY, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def tmp_repo(tmp_path):
    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    (sub / "a.py").write_bytes(b"one\ntwo\n")
    (sub / "b.py").write_bytes(b"x = 1\n")
    (root / "outside.py").write_bytes(b"out\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "first")
    head7 = git(root, "rev-parse", "--short=7", "HEAD").strip()
    return SimpleNamespace(root=root, sub=sub, head7=head7)


def test_clean_repo(tmp_repo):
    assert code_state(tmp_repo.sub) == CodeState(tmp_repo.head7, ())


def test_edited_tracked_file_counts(tmp_repo):
    (tmp_repo.sub / "b.py").write_bytes(b"x = 2\n")
    (tmp_repo.sub / "a.py").write_bytes(b"one\nchanged\n")
    assert code_state(tmp_repo.sub) == CodeState(tmp_repo.head7, ("a.py", "b.py"))


def test_untracked_file_does_not_count(tmp_repo):
    (tmp_repo.sub / "new.py").write_bytes(b"new\n")
    assert code_state(tmp_repo.sub).changed == ()


def test_change_outside_the_folder_does_not_count(tmp_repo):
    (tmp_repo.root / "outside.py").write_bytes(b"changed\n")
    assert code_state(tmp_repo.sub).changed == ()


def test_line_ending_only_change_does_not_count(tmp_repo):
    git(tmp_repo.root, "config", "core.autocrlf", "true")
    (tmp_repo.sub / "a.py").write_bytes(b"one\r\ntwo\r\n")
    assert code_state(tmp_repo.sub).changed == ()


def test_no_git_gives_unknown(tmp_path):
    def raises_oserror(*args, **kwargs):
        raise OSError("no git")

    assert code_state(tmp_path, runner=raises_oserror) == CodeState(None, ())


def test_not_a_repository_gives_unknown(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert code_state(folder) == CodeState(None, ())


def test_nested_path_is_relative_to_the_folder(tmp_repo):
    deeper = tmp_repo.sub / "pkg"
    deeper.mkdir()
    (deeper / "c.py").write_bytes(b"c\n")
    git(tmp_repo.root, "add", ".")
    git(tmp_repo.root, "commit", "-q", "-m", "second")
    (deeper / "c.py").write_bytes(b"d\n")
    assert code_state(tmp_repo.sub).changed == (str(Path("pkg") / "c.py").replace("\\", "/"),)
