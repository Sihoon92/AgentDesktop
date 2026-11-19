"""The workflow file: what a replay runs, checked before it is saved and again when it is read.

A workflow names its targets by window scope, role, name, description and a named
neighbour (agent/locator.py), never by coordinates, refs or window ids. A system whose ids
stay the same adds the target's native id ("id") and its area ("in", only with "id"). Only the keys
below are accepted, so a hand edit that adds a coordinate or misspells a key is refused
instead of replayed. Error messages say which step and which key, never the value: the
values are screen text and paths, and the messages reach the terminal and the viewer.
"""

import re
from pathlib import Path
from typing import NoReturn

from agent.geometry import PARTS
from agent.knowledge import KnowledgeError, system_name

FORMAT = 1
STEP_KINDS = ("act", "select_only", "check_file")
ACT_TOOLS = ("click", "toggle", "expand", "collapse", "set_value", "select", "check", "uncheck",
             "type", "double_click", "right_click", "click_part", "press")
VALUE_TOOLS = {"set_value": "value", "select": "value", "type": "text"}
EXPECTS = ("window_opened", "window_closed", "content_changed", "value_changed", "any", "picture_changed")
PARAM_KINDS = ("text", "list", "path")
STATUSES = ("unverified", "verified")
PERMISSION_LEVELS = (0, 1, 2, 3)
RELATIONS = ("same_row", "same_column")
CLICK_ACTIONS = ("click", "double_click", "right_click")

TOP_KEYS = ("format", "id", "system", "goal", "source", "status", "passes", "failures",
            "verified_with_other_values", "permission_level", "params", "fixed", "steps")
SOURCE_KEYS = ("run_id", "date")
PARAM_KEYS = ("kind", "example")
FIXED_KEYS = ("name", "value")
ACT_KEYS = ("n", "goal", "do", "tool", "args", "target", "value", "expect", "closes", "settle")
ACT_REQUIRED = ("n", "goal", "do", "tool", "target")
SELECT_KEYS = ("n", "goal", "do", "scope", "all", "item_role", "items")
CHECK_FILE_KEYS = ("n", "goal", "do", "path")
LOCATOR_KEYS = ("role", "name", "description", "id", "in", "near")
AREA_SHAPES = ({"role", "id"}, {"role", "name"})
NEAR_KEYS = ("name", "relation", "nth")
SCOPE_KEYS = ("opened_by", "top")
ARGS_KEYS = {"click_part": ("part", "action"), "press": ("combo",)}
FORBIDDEN_KEYS = frozenset({"x", "y", "bounds", "rect", "points", "mark", "ref", "window_id"})
PARAM_USES = {"text": ("name", "value"), "list": ("items",), "path": ("value", "path")}

PARAM_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
WORKFLOW_ID = re.compile(r"w-\d{8}-\d{3}")
INVALID = "WORKFLOW_INVALID"


class WorkflowError(ValueError):
    """A workflow or a value the replay refuses. `.code` is one of the codes in the design's section 7."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def check_workflow(workflow):
    """Returns `workflow` unchanged when it is a valid workflow, else raises WorkflowError(WORKFLOW_INVALID)."""
    if not isinstance(workflow, dict):
        _invalid("workflow", "키와 값의 묶음이 아닙니다")
    _refuse_forbidden(workflow, "workflow")
    _exact(workflow, TOP_KEYS, TOP_KEYS, "맨 위")
    _check_header(workflow)
    params = _check_params(workflow["params"])
    _check_fixed(workflow["fixed"], params)
    used = _check_steps(workflow["steps"], params)
    unused = sorted(set(params) - used)
    if unused:
        _invalid("params", f"쓰이지 않는 파라미터가 {len(unused)}개 있습니다")
    return workflow


def param_name(value):
    """`"$shop"` → `"shop"`; anything else, including an invalid name after `$`, → None."""
    if isinstance(value, str) and value.startswith("$") and PARAM_NAME.fullmatch(value[1:]):
        return value[1:]
    return None


def resolve_values(workflow, given, run_id):
    """The value of every parameter for one replay: the given one checked, else the example.

    An omitted path becomes `<example stem>-<run_id><suffix>` in the example's folder.
    Raises WorkflowError with PARAM_UNKNOWN, PARAM_INVALID or FILE_EXISTS.
    """
    params = workflow["params"]
    fixed = {item["name"] for item in workflow["fixed"]}
    for name in given:
        if name in fixed or name not in params:
            raise WorkflowError("PARAM_UNKNOWN", f"이 workflow가 받지 않는 파라미터입니다: {name}")
    values = {}
    for name, spec in params.items():
        raw = given.get(name)
        if raw is not None and not isinstance(raw, str):
            raise WorkflowError("PARAM_INVALID", f"파라미터 {name}의 값이 글이 아닙니다")
        if spec["kind"] == "path":
            values[name] = _path_value(name, raw, spec["example"], run_id)
        elif raw is None:
            values[name] = list(spec["example"]) if spec["kind"] == "list" else spec["example"]
        elif spec["kind"] == "list":
            values[name] = _list_value(name, raw)
        else:
            values[name] = _text_value(name, raw)
    return values


def other_values(workflow, values):
    """True when a parameter other than a path differs from its example. List order does not count."""
    for name, spec in workflow["params"].items():
        if spec["kind"] == "path":
            continue
        value = values.get(name, spec["example"])
        if spec["kind"] == "list":
            if set(value) != set(spec["example"]):
                return True
        elif value != spec["example"]:
            return True
    return False


def _text_value(name, raw):
    text = raw.strip()
    if not text:
        raise WorkflowError("PARAM_INVALID", f"파라미터 {name}의 값이 비어 있습니다")
    return text


def _list_value(name, raw):
    pieces = [piece.strip() for piece in raw.split(",")]
    if not pieces or any(not piece for piece in pieces):
        raise WorkflowError("PARAM_INVALID", f"파라미터 {name}의 값에 빈 조각이 있습니다")
    return list(dict.fromkeys(pieces))


def _path_value(name, raw, example, run_id):
    if raw is None:
        sample = Path(example)
        candidate = sample.with_name(f"{sample.stem}-{run_id}{sample.suffix}")
    else:
        candidate = Path(_text_value(name, raw))
    if not candidate.is_absolute():
        raise WorkflowError("PARAM_INVALID", f"파라미터 {name}의 경로가 절대 경로가 아닙니다")
    if not candidate.parent.is_dir():
        raise WorkflowError("PARAM_INVALID", f"파라미터 {name}의 경로에 폴더가 없습니다")
    if candidate.exists():
        raise WorkflowError("FILE_EXISTS", f"파라미터 {name}의 경로에 이미 파일이 있습니다")
    return str(candidate)


def _check_header(workflow):
    if type(workflow["format"]) is not int or workflow["format"] != FORMAT:
        _invalid("format", f"{FORMAT}이어야 합니다")
    if not isinstance(workflow["id"], str) or not WORKFLOW_ID.fullmatch(workflow["id"]):
        _invalid("id", "w-YYYYMMDD-NNN 꼴이어야 합니다")
    try:
        system_name(workflow["system"], "")
    except KnowledgeError:
        _invalid("system", "시스템 이름으로 쓸 수 없습니다")
    _require_text(workflow["goal"], "goal")
    source = workflow["source"]
    if not isinstance(source, dict):
        _invalid("source", "키와 값의 묶음이 아닙니다")
    _exact(source, SOURCE_KEYS, SOURCE_KEYS, "source")
    for key in SOURCE_KEYS:
        _require_text(source[key], f"source.{key}")
    if workflow["status"] not in STATUSES:
        _invalid("status", "unverified나 verified여야 합니다")
    for key in ("passes", "failures"):
        if type(workflow[key]) is not int or workflow[key] < 0:
            _invalid(key, "0 이상의 정수여야 합니다")
    if type(workflow["verified_with_other_values"]) is not bool:
        _invalid("verified_with_other_values", "true나 false여야 합니다")
    if type(workflow["permission_level"]) is not int or workflow["permission_level"] not in PERMISSION_LEVELS:
        _invalid("permission_level", "0부터 3까지의 정수여야 합니다")


def _check_params(params):
    if not isinstance(params, dict):
        _invalid("params", "키와 값의 묶음이 아닙니다")
    for name, spec in params.items():
        if not isinstance(name, str) or not PARAM_NAME.fullmatch(name):
            _invalid("params", "파라미터 이름은 영문 소문자로 시작하는 a-z, 0-9, _ 32자까지입니다")
        where = f"params.{name}"
        if not isinstance(spec, dict):
            _invalid(where, "키와 값의 묶음이 아닙니다")
        _exact(spec, PARAM_KEYS, PARAM_KEYS, where)
        if spec["kind"] not in PARAM_KINDS:
            _invalid(f"{where}.kind", f"{', '.join(PARAM_KINDS)} 중 하나여야 합니다")
        _check_example(spec["kind"], spec["example"], f"{where}.example")
    return {name: spec["kind"] for name, spec in params.items()}


def _check_example(kind, example, where):
    if kind == "list":
        if not isinstance(example, list) or not example:
            _invalid(where, "비어 있지 않은 목록이어야 합니다")
        for item in example:
            _require_text(item, where)
        if len(set(example)) != len(example):
            _invalid(where, "같은 값이 두 번 있습니다")
        return
    _require_text(example, where)
    if kind == "path" and not Path(example).is_absolute():
        _invalid(where, "절대 경로여야 합니다")


def _check_fixed(fixed, params):
    if not isinstance(fixed, list):
        _invalid("fixed", "목록이어야 합니다")
    seen = set()
    for index, item in enumerate(fixed, 1):
        where = f"fixed[{index}]"
        if not isinstance(item, dict):
            _invalid(where, "키와 값의 묶음이 아닙니다")
        _exact(item, FIXED_KEYS, FIXED_KEYS, where)
        if not isinstance(item["name"], str) or not PARAM_NAME.fullmatch(item["name"]):
            _invalid(f"{where}.name", "파라미터 이름 규칙에 맞지 않습니다")
        _require_text(item["value"], f"{where}.value")
        if item["name"] in params or item["name"] in seen:
            _invalid(f"{where}.name", "params나 다른 fixed와 이름이 겹칩니다")
        seen.add(item["name"])


def _check_steps(steps, params):
    if not isinstance(steps, list) or not steps:
        _invalid("steps", "단계가 하나 이상 있어야 합니다")
    used = set()
    for index, step in enumerate(steps, 1):
        where = f"단계 {index}"
        if not isinstance(step, dict):
            _invalid(where, "키와 값의 묶음이 아닙니다")
        if type(step.get("n")) is not int or step["n"] != index:
            _invalid(f"{where}.n", "단계 번호는 1부터 차례로 붙여야 합니다")
        kind = step.get("do")
        checker = _CHECKERS.get(kind) if isinstance(kind, str) else None
        if checker is None:
            _invalid(f"{where}.do", f"{', '.join(STEP_KINDS)} 중 하나여야 합니다")
        uses = checker(step, where, steps[: index - 1])
        for name, use in uses:
            _check_use(name, use, params, where)
            used.add(name)
    return used


def _check_act(step, where, earlier):
    _exact(step, ACT_KEYS, ACT_REQUIRED, where)
    _require_text(step["goal"], f"{where}.goal")
    tool = step["tool"]
    if tool not in ACT_TOOLS:
        _invalid(f"{where}.tool", "workflow가 다시 할 수 있는 도구가 아닙니다")
    _check_args(tool, step.get("args"), "args" in step, f"{where}.args")
    target = step["target"]
    if not isinstance(target, dict) or "scope" not in target:
        _invalid(f"{where}.target", "scope가 있는 키와 값의 묶음이어야 합니다")
    _check_scope(target["scope"], step["n"], earlier, f"{where}.target.scope")
    uses = []
    locator = {key: value for key, value in target.items() if key != "scope"}
    if tool == "press":
        if locator:
            _invalid(f"{where}.target", "press의 대상에는 scope만 적습니다")
    else:
        uses += _check_locator(locator, f"{where}.target", name_may_be_param=True)
    uses += _check_value(step, tool, where)
    expect = step.get("expect")
    if expect is not None and expect not in EXPECTS:
        _invalid(f"{where}.expect", f"{', '.join(EXPECTS)} 중 하나나 null이어야 합니다")
    if "closes" in step:
        if expect != "window_closed":
            _invalid(f"{where}.closes", "expect가 window_closed일 때만 적습니다")
        closes = step["closes"]
        if type(closes) is not int or not 1 <= closes < step["n"]:
            _invalid(f"{where}.closes", "앞선 단계의 번호여야 합니다")
    if "settle" in step and step["settle"] is not True:
        _invalid(f"{where}.settle", "있으면 true여야 합니다")
    return uses


def _check_args(tool, args, present, where):
    if tool not in ARGS_KEYS:
        if present and args != {}:
            _invalid(where, "이 도구에는 인자를 적지 않습니다")
        return
    if not isinstance(args, dict):
        _invalid(where, "이 도구에는 인자가 있어야 합니다")
    _exact(args, ARGS_KEYS[tool], ARGS_KEYS[tool], where)
    if tool == "click_part":
        if args["part"] not in PARTS:
            _invalid(f"{where}.part", f"{', '.join(PARTS)} 중 하나여야 합니다")
        if args["action"] not in CLICK_ACTIONS:
            _invalid(f"{where}.action", f"{', '.join(CLICK_ACTIONS)} 중 하나여야 합니다")
    else:
        _require_text(args["combo"], f"{where}.combo")


def _check_value(step, tool, where):
    if tool not in VALUE_TOOLS:
        if "value" in step:
            _invalid(f"{where}.value", "값을 넣는 도구에만 적습니다")
        return []
    if "value" not in step:
        _invalid(f"{where}.value", "값을 넣는 도구에는 값이 있어야 합니다")
    _require_text(step["value"], f"{where}.value")
    name = param_name(step["value"])
    return [(name, "value")] if name else []


def _check_select_only(step, where, earlier):
    _exact(step, SELECT_KEYS, SELECT_KEYS, where)
    _require_text(step["goal"], f"{where}.goal")
    if step["scope"] == "main":
        _invalid(f"{where}.scope", "앞선 단계가 연 창이어야 합니다")
    _check_scope(step["scope"], step["n"], earlier, f"{where}.scope")
    if not isinstance(step["all"], dict):
        _invalid(f"{where}.all", "키와 값의 묶음이 아닙니다")
    _check_locator(step["all"], f"{where}.all", name_may_be_param=False)
    _require_text(step["item_role"], f"{where}.item_role")
    return [(_param_ref(step["items"], f"{where}.items"), "items")]


def _check_check_file(step, where, _earlier):
    _exact(step, CHECK_FILE_KEYS, CHECK_FILE_KEYS, where)
    _require_text(step["goal"], f"{where}.goal")
    return [(_param_ref(step["path"], f"{where}.path"), "path")]


_CHECKERS = {"act": _check_act, "select_only": _check_select_only, "check_file": _check_check_file}


def _check_scope(scope, n, earlier, where):
    if scope == "main":
        return
    if not isinstance(scope, dict):
        _invalid(where, "main이나 {opened_by: N}이어야 합니다")
    _exact(scope, SCOPE_KEYS, ("opened_by",), where)
    opener = scope["opened_by"]
    if type(opener) is not int or not 1 <= opener < n:
        _invalid(f"{where}.opened_by", "앞선 단계의 번호여야 합니다")
    if earlier[opener - 1].get("do") != "act":
        _invalid(f"{where}.opened_by", "창을 여는 단계는 act여야 합니다")
    if "top" in scope and scope["top"] is not True:
        _invalid(f"{where}.top", "있으면 true여야 합니다")


def _check_locator(locator, where, name_may_be_param):
    _exact(locator, LOCATOR_KEYS, ("role",), where)
    _require_text(locator["role"], f"{where}.role")
    uses = []
    if "name" in locator:
        _require_text(locator["name"], f"{where}.name")
        name = param_name(locator["name"])
        if name and not name_may_be_param:
            _invalid(f"{where}.name", "여기에는 파라미터를 쓸 수 없습니다")
        if name:
            uses.append((name, "name"))
    if "description" in locator:
        _require_text(locator["description"], f"{where}.description")
    if "id" in locator:
        _check_id(locator, where)
    elif "in" in locator:
        _invalid(f"{where}.in", "id가 있을 때만 쓸 수 있습니다")
    if "near" in locator:
        near = locator["near"]
        if not isinstance(near, dict):
            _invalid(f"{where}.near", "키와 값의 묶음이 아닙니다")
        _exact(near, NEAR_KEYS, NEAR_KEYS, f"{where}.near")
        _require_text(near["name"], f"{where}.near.name")
        if near["relation"] not in RELATIONS:
            _invalid(f"{where}.near.relation", f"{', '.join(RELATIONS)} 중 하나여야 합니다")
        if type(near["nth"]) is not int or near["nth"] < 1:
            _invalid(f"{where}.near.nth", "1 이상의 정수여야 합니다")
    return uses


def _check_id(locator, where):
    """A target's own id (not the workflow's): non-empty text, not a parameter, and never on a
    target whose name is a parameter, which must find another element for another value."""
    _require_text(locator["id"], f"{where}.id")
    if param_name(locator["id"]):
        _invalid(f"{where}.id", "여기에는 파라미터를 쓸 수 없습니다")
    if param_name(locator.get("name")):
        _invalid(f"{where}.id", "이름이 파라미터인 대상에는 id를 쓸 수 없습니다")
    if "in" not in locator:
        return
    area = locator["in"]
    if not isinstance(area, dict) or set(area) not in AREA_SHAPES:
        _invalid(f"{where}.in", "role과 id, 또는 role과 name이어야 합니다")
    for key in area:
        _require_text(area[key], f"{where}.in.{key}")


def _param_ref(value, where):
    name = param_name(value)
    if name is None:
        _invalid(where, "$이름 꼴의 파라미터여야 합니다")
    return name


def _check_use(name, use, params, where):
    if name not in params:
        _invalid(where, "params에 없는 파라미터를 씁니다")
    if use not in PARAM_USES[params[name]]:
        _invalid(where, f"{params[name]} 파라미터를 쓸 수 없는 자리입니다({use})")


def _refuse_forbidden(value, where):
    if isinstance(value, dict):
        for key, item in value.items():
            if where == "workflow.params":
                _refuse_forbidden(item, f"{where}.*")
                continue
            if key in FORBIDDEN_KEYS:
                _invalid(where, f"좌표나 표시 번호, ref, 창 id는 적지 않습니다({key})")
            _refuse_forbidden(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value, 1):
            _refuse_forbidden(item, f"{where}[{index}]")


def _exact(mapping, allowed, required, where):
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        _invalid(where, f"모르는 키가 있습니다: {', '.join(unknown)}")
    missing = [key for key in required if key not in mapping]
    if missing:
        _invalid(where, f"빠진 키가 있습니다: {', '.join(missing)}")


def _require_text(value, where):
    if not isinstance(value, str) or not value.strip():
        _invalid(where, "비어 있지 않은 글이어야 합니다")


def _invalid(where, problem) -> NoReturn:
    raise WorkflowError(INVALID, f"{where}: {problem}")
