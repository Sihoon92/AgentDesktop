"""Saved workflows: one human-readable YAML file per workflow at `<root>/<system>/<id>.yaml`.

The folder is read on every call, so a hand edit shows up at once, and a file that no
longer passes the format check is skipped by the list and refused by id. Files are
written to a temporary name and swapped in, as the knowledge store does.
"""

import os
from datetime import date
from pathlib import Path

import yaml

from agent.knowledge import _replace_retrying, system_name
from agent.workflow_format import (
    ACT_KEYS,
    CHECK_FILE_KEYS,
    FIXED_KEYS,
    LOCATOR_KEYS,
    NEAR_KEYS,
    PARAM_KEYS,
    SCOPE_KEYS,
    SELECT_KEYS,
    SOURCE_KEYS,
    TOP_KEYS,
    WORKFLOW_ID,
    WorkflowError,
    check_workflow,
)

WORKFLOW_ENV = "AGENT_WORKFLOW_DIR"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "workflows"
MAX_DAILY = 999
STEP_ORDER = {"act": ACT_KEYS, "select_only": SELECT_KEYS, "check_file": CHECK_FILE_KEYS}


def workflow_root(explicit=None, settings_dir=None, environ=None):
    """The workflow folder, as an absolute path: the command-line value, else the settings'
    workflow_dir, else AGENT_WORKFLOW_DIR, else examples/llm-agent/workflows. Empty values
    are skipped; a relative path is taken from the current directory."""
    environ = os.environ if environ is None else environ
    for value in (explicit, settings_dir, environ.get(WORKFLOW_ENV)):
        if value is not None and str(value).strip():
            return Path(value).resolve()
    return DEFAULT_ROOT


class WorkflowStore:
    """The workflows of one system. The system name follows the knowledge folder's rule."""

    def __init__(self, root, system):
        self.system = system_name(system, "")
        self.folder = Path(root) / self.system

    def add(self, workflow, today=None):
        """Saves `workflow` under a new id as unverified with zero counts, and returns what was saved."""
        if not isinstance(workflow, dict):
            raise WorkflowError("WORKFLOW_INVALID", "workflow: 키와 값의 묶음이 아닙니다")
        if workflow.get("system", self.system) != self.system:
            raise WorkflowError("SYSTEM_MISMATCH", "workflow의 시스템이 저장할 시스템과 다릅니다")
        candidate = {
            **workflow,
            "system": self.system,
            "id": "w-00000000-000",
            "status": "unverified",
            "passes": 0,
            "failures": 0,
            "verified_with_other_values": False,
        }
        check_workflow(candidate)
        candidate["id"] = self._next_id(today or date.today())
        self._write(candidate)
        return _ordered(candidate)

    def get(self, workflow_id):
        """The workflow, or None when there is no such file. A file that cannot be read or
        fails the format check raises WorkflowError(WORKFLOW_INVALID)."""
        if not isinstance(workflow_id, str) or not WORKFLOW_ID.fullmatch(workflow_id):
            return None
        path = self.folder / f"{workflow_id}.yaml"
        if not path.is_file():
            return None
        return self._read(path)

    def entries(self):
        """Every readable workflow of this system, in id order."""
        if not self.folder.is_dir():
            return []
        found = []
        for path in sorted(self.folder.glob("w-*.yaml")):
            try:
                found.append(self._read(path))
            except WorkflowError:
                continue
        return sorted(found, key=lambda workflow: workflow["id"])

    def note_pass(self, workflow_id, other):
        current = self._existing(workflow_id)
        updated = {
            **current,
            "status": "verified",
            "passes": current["passes"] + 1,
            "verified_with_other_values": current["verified_with_other_values"] or bool(other),
        }
        self._write(updated)
        return _ordered(updated)

    def note_failure(self, workflow_id):
        current = self._existing(workflow_id)
        updated = {**current, "failures": current["failures"] + 1}
        self._write(updated)
        return _ordered(updated)

    def _existing(self, workflow_id):
        current = self.get(workflow_id)
        if current is None:
            raise WorkflowError("WORKFLOW_NOT_FOUND", "그 id의 workflow가 없습니다")
        return current

    def _read(self, path):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise WorkflowError("WORKFLOW_INVALID", f"{path.name}: 읽을 수 없는 YAML입니다") from error
        if isinstance(raw, dict) and isinstance(raw.get("source"), dict):
            source_date = raw["source"].get("date")
            if isinstance(source_date, date):
                raw["source"] = {**raw["source"], "date": source_date.isoformat()}
        check_workflow(raw)
        if raw["id"] != path.stem or raw["system"] != self.system:
            raise WorkflowError("WORKFLOW_INVALID", f"{path.name}: id나 시스템이 파일의 자리와 다릅니다")
        return _ordered(raw)

    def _next_id(self, today):
        stem = f"w-{today:%Y%m%d}-"
        taken = {path.stem for path in self.folder.glob(f"{stem}*.yaml")} if self.folder.is_dir() else set()
        for number in range(1, MAX_DAILY + 1):
            candidate = f"{stem}{number:03d}"
            if candidate not in taken:
                return candidate
        raise WorkflowError("WORKFLOW_INVALID", f"하루의 workflow가 {MAX_DAILY}개를 넘었습니다")

    def _write(self, workflow):
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"{workflow['id']}.yaml"
        temporary = self.folder / f"{workflow['id']}.yaml.tmp"
        text = yaml.safe_dump(_ordered(workflow), allow_unicode=True, sort_keys=False, width=1000)
        temporary.write_text(text, encoding="utf-8")
        _replace_retrying(temporary, target)


def _ordered(workflow):
    ordered = _keys(workflow, TOP_KEYS)
    ordered["source"] = _keys(workflow["source"], SOURCE_KEYS)
    ordered["params"] = {name: _keys(spec, PARAM_KEYS) for name, spec in workflow["params"].items()}
    ordered["fixed"] = [_keys(item, FIXED_KEYS) for item in workflow["fixed"]]
    ordered["steps"] = [_ordered_step(step) for step in workflow["steps"]]
    return ordered


def _ordered_step(step):
    ordered = _keys(step, STEP_ORDER[step["do"]])
    for key in ("target", "all"):
        if key in ordered:
            ordered[key] = _ordered_locator(ordered[key])
    if isinstance(ordered.get("scope"), dict):
        ordered["scope"] = _keys(ordered["scope"], SCOPE_KEYS)
    return ordered


def _ordered_locator(locator):
    ordered = _keys(locator, ("scope", *LOCATOR_KEYS))
    if isinstance(ordered.get("scope"), dict):
        ordered["scope"] = _keys(ordered["scope"], SCOPE_KEYS)
    if "near" in ordered:
        ordered["near"] = _keys(ordered["near"], NEAR_KEYS)
    return ordered


def _keys(mapping, order):
    return {key: mapping[key] for key in order if key in mapping}
