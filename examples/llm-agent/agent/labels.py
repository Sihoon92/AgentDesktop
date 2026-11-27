"""Checks the `labels.yaml` the company model writes before any block is made from it.

The file holds a scene code per capture and a person's answer per manual hypothesis, and
nothing else. Anything outside the code tables stops the block. A problem is reported by its
line and rule name only, never by the value, because a wrong value may be screen text.
A UTF-8 byte order mark and CRLF line endings are accepted.
"""

from pathlib import Path
from typing import NamedTuple

import yaml

from agent.survey_codes import SCENES
from agent.survey_facts import Labels

TOP_KEYS = ("version", "captures", "manual")
LABELS_VERSION = "1"
STR_TAG = "tag:yaml.org,2002:str"
INT_TAG = "tag:yaml.org,2002:int"
UNANSWERED = "?"


class LabelProblem(NamedTuple):
    line: int
    rule: str


class LabelError(Exception):
    """`problems` in line order; the message names lines and rules, never values."""

    def __init__(self, problems):
        self.problems = tuple(sorted(problems))
        super().__init__("LABEL_INVALID " + "; ".join(f"{p.line}행 {p.rule}" for p in self.problems))


def _line(node):
    return node.start_mark.line + 1


def _text(node):
    if isinstance(node, yaml.ScalarNode) and node.tag == STR_TAG:
        return node.value
    return None


def _entries(node, problems):
    """(key node, value node) pairs of a mapping node, or [] with NOT_TEXT when it is not one."""
    if isinstance(node, yaml.MappingNode):
        return node.value
    problems.append(LabelProblem(_line(node), "NOT_TEXT"))
    return []


def _section(node, allowed, codes_of, rules, problems):
    """Reads one `captures` or `manual` mapping. `rules` is (key rule, code rule)."""
    found = {}
    key_rule, code_rule = rules
    for key_node, value_node in _entries(node, problems):
        key = _text(key_node) if isinstance(key_node, yaml.ScalarNode) else None
        if key not in allowed:
            problems.append(LabelProblem(_line(key_node), key_rule))
            continue
        value = _text(value_node)
        if value is None:
            problems.append(LabelProblem(_line(value_node), "NOT_TEXT"))
        elif value not in codes_of(key):
            problems.append(LabelProblem(_line(value_node), code_rule))
        else:
            found[key] = value
    return found


def _compose(path):
    try:
        text = Path(path).read_bytes().decode("utf-8-sig")
        return yaml.compose(text), None
    except UnicodeDecodeError:
        return None, LabelProblem(1, "YAML")
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
        return None, LabelProblem(mark.line + 1 if mark is not None else 1, "YAML")


def load_labels(path: Path, capture_ids: tuple, manual_codes: dict) -> Labels:
    """The labels of one session. No file means Labels({}, {}). Raises LabelError."""
    if not Path(path).is_file():
        return Labels({}, {})
    root, broken = _compose(path)
    if broken is not None:
        raise LabelError([broken])
    if root is None:
        return Labels({}, {})
    if not isinstance(root, yaml.MappingNode):
        raise LabelError([LabelProblem(_line(root), "TOP_KEY")])
    problems, captures, manual = [], {}, {}
    for key_node, value_node in root.value:
        key = _text(key_node) if isinstance(key_node, yaml.ScalarNode) else None
        if key == "version":
            if not (isinstance(value_node, yaml.ScalarNode) and value_node.tag == INT_TAG
                    and value_node.value == LABELS_VERSION):
                problems.append(LabelProblem(_line(value_node), "VERSION"))
        elif key == "captures":
            captures = _section(value_node, set(capture_ids), lambda _: SCENES, ("CAPTURE_ID", "SCENE_CODE"), problems)
        elif key == "manual":
            manual = _section(value_node, set(manual_codes), lambda hid: (*manual_codes[hid], UNANSWERED),
                              ("MANUAL_ID", "MANUAL_CODE"), problems)
        else:
            problems.append(LabelProblem(_line(key_node), "TOP_KEY"))
    if problems:
        raise LabelError(problems)
    return Labels(captures, manual)
