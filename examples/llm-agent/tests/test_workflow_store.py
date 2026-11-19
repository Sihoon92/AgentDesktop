"""Saving and reading workflow files (agent/workflow_store.py)."""

from datetime import date

import pytest
import yaml

from agent.knowledge import KnowledgeStore
from agent.workflow_format import TOP_KEYS, WorkflowError, check_workflow
from agent.workflow_store import DEFAULT_ROOT, WORKFLOW_ENV, WorkflowStore, workflow_root
from tests.test_workflow_format import sample

DAY = date(2026, 11, 19)


def fresh():
    """A workflow as the author builds it: no id, status or counts."""
    workflow = sample()
    for key in ("id", "status", "passes", "failures", "verified_with_other_values"):
        workflow.pop(key)
    return workflow


def test_add_gives_an_id_and_starts_unverified(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    first = store.add(fresh(), today=DAY)
    assert first["id"] == "w-20261119-001"
    assert (first["status"], first["passes"], first["failures"], first["verified_with_other_values"]) == (
        "unverified", 0, 0, False)
    assert store.add(fresh(), today=DAY)["id"] == "w-20261119-002"
    assert store.add(fresh(), today=date(2026, 11, 20))["id"] == "w-20261120-001"
    assert (tmp_path / "virtualmes" / "w-20261119-001.yaml").is_file()
    assert check_workflow(store.get("w-20261119-001")) == first


def test_add_replaces_an_id_status_and_counts_it_was_given(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    given = {**sample(), "id": "w-19990101-007", "status": "verified", "passes": 4}
    saved = store.add(given, today=DAY)
    assert (saved["id"], saved["status"], saved["passes"]) == ("w-20261119-001", "unverified", 0)


def test_add_refuses_an_invalid_workflow(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    broken = fresh()
    broken["steps"][0]["target"]["bounds"] = {"x": 1}
    with pytest.raises(WorkflowError) as caught:
        store.add(broken, today=DAY)
    assert caught.value.code == "WORKFLOW_INVALID"
    assert not (tmp_path / "virtualmes").exists() or list((tmp_path / "virtualmes").iterdir()) == []


def test_add_refuses_a_workflow_of_another_system(tmp_path):
    store = WorkflowStore(tmp_path, "other")
    with pytest.raises(WorkflowError) as caught:
        store.add(fresh(), today=DAY)
    assert caught.value.code == "SYSTEM_MISMATCH"


def test_the_file_is_readable_yaml_in_the_contract_order(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    store.add(fresh(), today=DAY)
    text = (tmp_path / "virtualmes" / "w-20261119-001.yaml").read_text(encoding="utf-8")
    assert "전공정 생산현황" in text and "\\u" not in text
    loaded = yaml.safe_load(text)
    assert list(loaded) == list(TOP_KEYS)
    assert list(loaded["steps"][0]) == ["n", "goal", "do", "tool", "target", "expect"]
    assert list(loaded["steps"][2]["target"]) == ["scope", "role", "near"]
    assert list(loaded["steps"][2]["target"]["near"]) == ["name", "relation", "nth"]
    assert list(loaded["steps"][3]) == ["n", "goal", "do", "scope", "all", "item_role", "items"]
    assert list(loaded["params"]["shop"]) == ["kind", "example"]
    assert list(loaded["source"]) == ["run_id", "date"]
    assert not list((tmp_path / "virtualmes").glob("*.tmp"))


def test_an_unquoted_date_written_by_hand_is_read_as_text(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    store.add(fresh(), today=DAY)
    path = tmp_path / "virtualmes" / "w-20261119-001.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("'2026-11-19'", "2026-11-19"), encoding="utf-8")
    assert store.get("w-20261119-001")["source"]["date"] == "2026-11-19"


def test_get_returns_none_for_an_unknown_id(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    assert store.get("w-20261119-001") is None
    store.add(fresh(), today=DAY)
    assert store.get("w-20261119-002") is None
    (tmp_path / "w-20261119-001.yaml").write_text(
        (tmp_path / "virtualmes" / "w-20261119-001.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert store.get("../w-20261119-001") is None
    assert store.get("nope") is None


def test_a_broken_file_is_skipped_by_entries_and_refused_by_get(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    store.add(fresh(), today=DAY)
    store.add(fresh(), today=DAY)
    store.add(fresh(), today=DAY)
    folder = tmp_path / "virtualmes"
    (folder / "w-20261119-002.yaml").write_text("format: 1\nsteps: [\n", encoding="utf-8")
    third = yaml.safe_load((folder / "w-20261119-003.yaml").read_text(encoding="utf-8"))
    third.pop("goal")
    (folder / "w-20261119-003.yaml").write_text(yaml.safe_dump(third, allow_unicode=True), encoding="utf-8")
    (folder / "w-20261119-004.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    assert [entry["id"] for entry in store.entries()] == ["w-20261119-001"]
    for workflow_id in ("w-20261119-002", "w-20261119-003", "w-20261119-004"):
        with pytest.raises(WorkflowError) as caught:
            store.get(workflow_id)
        assert caught.value.code == "WORKFLOW_INVALID"


def test_a_file_whose_id_or_system_differs_from_its_place_is_refused(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    store.add(fresh(), today=DAY)
    folder = tmp_path / "virtualmes"
    (folder / "w-20261119-001.yaml").rename(folder / "w-20261119-009.yaml")
    with pytest.raises(WorkflowError):
        store.get("w-20261119-009")
    other = WorkflowStore(tmp_path, "other")
    (tmp_path / "other").mkdir()
    (folder / "w-20261119-009.yaml").rename(tmp_path / "other" / "w-20261119-001.yaml")
    with pytest.raises(WorkflowError):
        other.get("w-20261119-001")
    assert other.entries() == [] and store.entries() == []


def test_entries_are_in_id_order(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    store.add(fresh(), today=date(2026, 11, 21))
    store.add(fresh(), today=DAY)
    assert [entry["id"] for entry in store.entries()] == ["w-20261119-001", "w-20261121-001"]
    assert WorkflowStore(tmp_path, "empty").entries() == []


def test_note_pass_and_failure(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    workflow_id = store.add(fresh(), today=DAY)["id"]
    failed = store.note_failure(workflow_id)
    assert (failed["failures"], failed["status"]) == (1, "unverified")
    passed = store.note_pass(workflow_id, other=False)
    assert (passed["status"], passed["passes"], passed["verified_with_other_values"]) == ("verified", 1, False)
    store.note_pass(workflow_id, other=True)
    again = store.note_pass(workflow_id, other=False)
    assert (again["passes"], again["verified_with_other_values"]) == (3, True)
    failed = store.note_failure(workflow_id)
    assert (failed["failures"], failed["status"]) == (2, "verified")
    assert store.get(workflow_id) == failed


def test_counting_an_unknown_workflow_fails(tmp_path):
    store = WorkflowStore(tmp_path, "virtualmes")
    with pytest.raises(WorkflowError) as caught:
        store.note_pass("w-20261119-001", other=False)
    assert caught.value.code == "WORKFLOW_NOT_FOUND"
    with pytest.raises(WorkflowError) as caught:
        store.note_failure("w-20261119-001")
    assert caught.value.code == "WORKFLOW_NOT_FOUND"


def test_workflow_root_order(tmp_path):
    env = {WORKFLOW_ENV: str(tmp_path / "env")}
    assert workflow_root(tmp_path / "cli", tmp_path / "settings", env) == (tmp_path / "cli").resolve()
    assert workflow_root(None, tmp_path / "settings", env) == (tmp_path / "settings").resolve()
    assert workflow_root("  ", "", env) == (tmp_path / "env").resolve()
    assert workflow_root(None, None, {}) == DEFAULT_ROOT
    assert DEFAULT_ROOT.name == "workflows" and DEFAULT_ROOT.parent.name == "llm-agent"
    assert workflow_root("relative", None, {}).is_absolute()


@pytest.mark.parametrize("name", ["virtualmes", "가상_mes-1.0", "a" * 64, "", ".", "..", "a/b", "a b", "a" * 65])
def test_the_system_name_follows_the_knowledge_rule(tmp_path, name):
    try:
        KnowledgeStore(tmp_path, name)
        accepted_by_knowledge = True
    except ValueError:
        accepted_by_knowledge = False
    try:
        WorkflowStore(tmp_path, name)
        accepted = True
    except ValueError:
        accepted = False
    assert accepted == accepted_by_knowledge
