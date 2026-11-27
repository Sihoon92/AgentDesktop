import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
COMPANY = HERE / "company"


def load_guard():
    spec = importlib.util.spec_from_file_location("company_guard", COMPANY / "hooks" / "guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load_guard()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    llm = root / "examples" / "llm-agent"
    company = llm / "company"
    company.mkdir(parents=True)
    ignored = [llm / "runs", llm / "captures", llm / "knowledge", company / ".claude" / "local"]

    def is_ignored(path):
        return any(Path(path).is_relative_to(folder) for folder in ignored)

    return type("Repo", (), {"root": root, "llm": llm, "company": company, "is_ignored": staticmethod(is_ignored)})


def edit(path, tool="Edit"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: str(path)}}


def bash(command, tool="Bash"):
    return {"tool_name": tool, "tool_input": {"command": command}}


def decide(payload, repo):
    return guard.decide(payload, repo.root, repo.company, repo.is_ignored)


@pytest.mark.parametrize("path, allowed", [("agent/loop.py", False), ("runs/x/run.json", True),
                                           ("company/hooks/guard.py", False), ("company/CLAUDE.md", False),
                                           ("company/notes.md", False), ("survey.py", False)])
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_edit_rules(path, allowed, tool, repo):
    assert (decide(edit(repo.llm / path, tool), repo) is None) == allowed


def test_captures_folder_is_writable(repo):
    assert decide(edit(repo.llm / "captures" / "x" / "labels.yaml"), repo) is None
    assert decide(edit(repo.llm / "captures" / "x" / "notes.md", "Write"), repo) is None


def test_relative_path_is_resolved_from_the_session_folder(repo):
    payload = {"tool_name": "Edit", "cwd": str(repo.company), "tool_input": {"file_path": "..\\agent\\loop.py"}}
    assert decide(payload, repo) is not None
    payload["tool_input"]["file_path"] = "../captures/s/labels.yaml"
    assert decide(payload, repo) is None


def test_ignored_file_under_company_is_still_blocked(repo):
    assert repo.is_ignored(repo.company / ".claude" / "local" / "x.json")
    assert decide(edit(repo.company / ".claude" / "local" / "x.json", "Write"), repo) is not None


def test_outside_repo_is_allowed(repo, tmp_path):
    assert decide(edit(tmp_path / "elsewhere.txt"), repo) is None


def test_edit_without_a_path_blocks(repo):
    assert decide({"tool_name": "Write", "tool_input": {}}, repo) is not None


@pytest.mark.parametrize("command", ["git commit -m x", "git -C .. push", "cd .. && git checkout main",
                                     "FOO=1 git reset --hard", "pip install x", "python -m pip install x", "cargo build",
                                     "git -c a=b add .", "git status; git stash", "echo x | git apply", "git switch -c b",
                                     "git restore a.py", "git rm a.py", "git config user.name x", "conda install x",
                                     "npm install", "C:\\Git\\bin\\git.exe commit -m x", "git --no-pager merge x",
                                     "pip3 install x", "py -m pip install x", "git status\ngit clean -fd",
                                     "git commit -m 'unbalanced"])
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_blocked_commands(command, tool, repo):
    assert decide(bash(command, tool), repo) is not None


@pytest.mark.parametrize("command", ["git pull", "git status", "git log -3", "python ..\\survey.py --block latest",
                                     "git diff", "git -C .. status", "python ..\\survey.py --system A", "git fetch",
                                     "git show HEAD", "python -m pytest -q", "dir ..\\captures", "pip list"])
def test_allowed_commands(command, repo):
    assert decide(bash(command), repo) is None


def test_other_tools_are_allowed(repo):
    assert decide({"tool_name": "Read", "tool_input": {"file_path": str(repo.llm / "agent" / "loop.py")}}, repo) is None


def run_main(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    return guard.main()


def test_broken_input_blocks(monkeypatch, capsys):
    assert run_main(monkeypatch, "not json") == 2
    assert run_main(monkeypatch, "[]") == 2
    assert capsys.readouterr().err


def test_main_against_this_repository(monkeypatch, capsys):
    assert run_main(monkeypatch, json.dumps(edit(HERE / "agent" / "loop.py"))) == 2
    assert "추적" in capsys.readouterr().err
    assert run_main(monkeypatch, json.dumps(edit(HERE / "captures" / "s" / "labels.yaml"))) == 0
    assert run_main(monkeypatch, json.dumps(bash("git status"))) == 0


def test_settings_runs_the_guard_for_every_writing_tool():
    settings = json.loads((COMPANY / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entry = settings["hooks"]["PreToolUse"][0]
    assert set(entry["matcher"].split("|")) == {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell"}
    assert "hooks/guard.py" in entry["hooks"][0]["command"]
    assert "Bash(git commit:*)" in settings["permissions"]["deny"]


def test_settings_file_is_not_ignored():
    out = subprocess.run(["git", "check-ignore", "-q", "company/.claude/settings.json"], cwd=HERE, capture_output=True)
    assert out.returncode == 1


def test_claude_md_points_to_codes_doc_and_block_command():
    text = (COMPANY / "CLAUDE.md").read_text(encoding="utf-8")
    first = text.splitlines()[0]
    assert "우선" in first
    for needed in ("codes.md", "--block", "labels.yaml", "notes.md", "summary.md", "session.json", "events.jsonl",
                   "H12", "그대로"):
        assert needed in text
    assert "MES" not in text
