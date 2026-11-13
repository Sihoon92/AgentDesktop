"""The harness must not know any one application.

What an application's screen looks like is the model's to read, and what a person said
about one system lives in that system's knowledge folder. Code that names an application,
one of its screens, or a label on one of its tables is a special case that passes on the
practice app and fails on the next system, so this test fails when such a name appears in
the code of the agent, the viewer, or their entry points.

Comments and docstrings are not checked. Neither is SYSTEM_PROMPT, whose examples are
written in general words and are reviewed by reading.
"""

import io
import re
import tokenize
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
PYTHON_SOURCES = sorted(
    [*(HERE / "agent").glob("*.py"), *(HERE / "viewer").glob("*.py"), HERE / "run.py", HERE / "view.py",
     HERE / "check.py"]
)
PAGE_SOURCES = sorted((HERE / "viewer" / "static").glob("*.*"))

APP_NAMES = re.compile(
    r"virtual[\s_-]?mes|\bMES\b|footprint|agentdeskfixture|notepad|메모장"
    r"|전공정|후공정|생산현황|생산집계|불량|롤러|설비 가동"
    r"|소계|극판|믹싱|조립|화성|코팅|압연|양품|그룹공정"
    r"|combo_(body|arrow)_clicked|cell_clicked|--combo-open",
    re.IGNORECASE,
)


def code_text(path):
    """The names and string literals of a Python file, without comments or docstrings."""
    kept = []
    previous = tokenize.INDENT
    source = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT):
            previous = token.type
            continue
        if token.type not in (tokenize.NL, tokenize.ENCODING):
            previous = token.type
        if token.type in (tokenize.NAME, tokenize.STRING, tokenize.FSTRING_MIDDLE):
            kept.append((token.start[0], token.string))
    return kept


def without_system_prompt(tokens, path):
    if path.name != "loop.py":
        return tokens
    return [(line, text) for line, text in tokens if not text.lstrip("rR").startswith('"""\\\nYou operate one desktop')]


def test_sources_are_found():
    names = {path.name for path in PYTHON_SOURCES}
    assert {"tools.py", "asking.py", "table.py", "knowledge.py", "server.py", "run.py", "view.py"} <= names
    assert {"app.js", "chat.js", "knowledge.js"} <= {path.name for path in PAGE_SOURCES}


@pytest.mark.parametrize("path", PYTHON_SOURCES, ids=lambda path: path.name)
def test_python_code_names_no_application(path):
    found = [
        f"{path.name}:{line}: {text.strip()[:60]}"
        for line, text in without_system_prompt(code_text(path), path)
        if APP_NAMES.search(text)
    ]
    assert found == []


@pytest.mark.parametrize("path", PAGE_SOURCES, ids=lambda path: path.name)
def test_viewer_page_names_no_application(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    found = [f"{path.name}:{number}: {line.strip()[:60]}" for number, line in enumerate(lines, 1)
             if APP_NAMES.search(line)]
    assert found == []


def test_the_check_catches_a_planted_name(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text(
        '"""Docstrings may say 소계."""\n'
        "# comments may say VirtualMes\n"
        'ROW = "소계"\n'
        "def open_virtual_mes():\n"
        '    """Docstrings may say 극판."""\n'
        "    return ROW\n",
        encoding="utf-8",
    )
    hits = [text for _, text in code_text(planted) if APP_NAMES.search(text)]
    assert hits == ['"소계"', "open_virtual_mes"]
