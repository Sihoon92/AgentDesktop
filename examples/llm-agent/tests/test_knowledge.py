from datetime import date
from pathlib import Path

import pytest
import yaml

from agent.knowledge import (
    KnowledgeError,
    KnowledgeStore,
    briefing,
    list_systems,
    relevant,
    system_name,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "knowledge.example"
DAY = date(2026, 11, 13)

RESOLUTION = {
    "type": "resolution",
    "situation": "선택 칸의 가운데를 눌러도 목록이 열리지 않는다",
    "resolution": "선택 칸은 오른쪽 끝의 화살표를 눌러 연다",
    "source": {"run_id": "20261113-101500", "question": "무엇을 눌러야 하나요?", "answer": "4번", "date": "2026-11-13"},
}

COMPLETION = {
    "type": "completion",
    "task": "조회 결과를 엑셀로 저장하는 작업",
    "checks": [{"kind": "file", "pattern": "*.xlsx"}, {"kind": "human"}],
}

VALID_YAML = """id: r-20261113-001
type: resolution
status: active
situation: 선택 칸이 열리지 않는다
resolution: 오른쪽 끝을 누른다
uses: 0
failures: 0
"""


def store_with_files(tmp_path, **files):
    folder = tmp_path / "mes"
    folder.mkdir()
    for stem, content in files.items():
        name = "r-20261113-001" if stem == "good" else stem
        (folder / f"{name}.yaml").write_text(content, encoding="utf-8")
    return KnowledgeStore(tmp_path, "mes")


def test_add_assigns_an_id_and_writes_one_readable_file(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")
    entry = store.add(RESOLUTION, today=DAY)

    assert entry["id"] == "r-20261113-001" and entry["uses"] == 0 and entry["status"] == "active"
    assert entry["failures"] == 0
    text = (tmp_path / "mes" / "r-20261113-001.yaml").read_text(encoding="utf-8")
    assert "오른쪽 끝의 화살표" in text
    assert yaml.safe_load(text)["resolution"] == RESOLUTION["resolution"]
    assert list((tmp_path / "mes").iterdir()) == [tmp_path / "mes" / "r-20261113-001.yaml"]


def test_add_does_not_change_the_given_entry(tmp_path):
    given = dict(RESOLUTION)
    KnowledgeStore(tmp_path, "mes").add(given, today=DAY)

    assert given == RESOLUTION


def test_second_entry_of_the_day_gets_the_next_number(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")

    first = store.add(RESOLUTION, today=DAY)
    second = store.add(RESOLUTION, today=DAY)
    completion = store.add(COMPLETION, today=DAY)
    next_day = store.add(RESOLUTION, today=date(2026, 11, 14))

    assert [first["id"], second["id"]] == ["r-20261113-001", "r-20261113-002"]
    assert completion["id"] == "c-20261113-001"
    assert next_day["id"] == "r-20261114-001"


def test_the_smallest_free_number_is_reused(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")
    for _ in range(3):
        store.add(RESOLUTION, today=DAY)
    store.delete("r-20261113-002")

    assert store.add(RESOLUTION, today=DAY)["id"] == "r-20261113-002"


def test_a_broken_file_still_holds_its_number(tmp_path):
    store = store_with_files(tmp_path, good=VALID_YAML)
    (tmp_path / "mes" / "r-20261113-002.yaml").write_text("id: [unclosed", encoding="utf-8")

    assert store.add(RESOLUTION, today=DAY)["id"] == "r-20261113-003"


def test_a_given_id_is_replaced_by_the_assigned_one(tmp_path):
    entry = KnowledgeStore(tmp_path, "mes").add({**RESOLUTION, "id": "r-mine"}, today=DAY)

    assert entry["id"] == "r-20261113-001"


def test_update_changes_only_the_given_keys_and_delete_removes_the_file(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")
    entry = store.add(RESOLUTION, today=DAY)

    updated = store.update(entry["id"], {"resolution": "칸의 오른쪽 끝을 누른다"})

    assert updated == {**entry, "resolution": "칸의 오른쪽 끝을 누른다"}
    assert store.get(entry["id"]) == updated
    assert store.delete(entry["id"]) is True
    assert not (tmp_path / "mes" / f"{entry['id']}.yaml").exists()
    assert store.delete(entry["id"]) is False
    assert store.get(entry["id"]) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "r-20261113-009"},
        {"type": "completion"},
        {"resolution": "(10, 20)을 누른다"},
        {"status": "paused"},
        {"bounds": {"x": 1}},
        {"uses": -1},
    ],
)
def test_bad_updates_are_rejected_and_the_file_is_kept(tmp_path, changes):
    store = KnowledgeStore(tmp_path, "mes")
    entry = store.add(RESOLUTION, today=DAY)

    with pytest.raises(KnowledgeError):
        store.update(entry["id"], changes)
    assert store.get(entry["id"]) == entry


def test_update_of_a_missing_entry_is_an_error(tmp_path):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").update("r-20261113-001", {"resolution": "x"})


@pytest.mark.parametrize("entry_id", ["..", "../x", "r-1/../../x", "a b", ""])
def test_odd_ids_are_not_paths(tmp_path, entry_id):
    store = KnowledgeStore(tmp_path, "mes")

    assert store.get(entry_id) is None
    with pytest.raises(KnowledgeError):
        store.update(entry_id, {"status": "disabled"})
    with pytest.raises(KnowledgeError):
        store.delete(entry_id)


def test_disabled_entries_are_hidden_unless_asked(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")
    active = store.add(RESOLUTION, today=DAY)
    disabled = store.add({**RESOLUTION, "status": "disabled"}, today=DAY)

    assert [e["id"] for e in store.entries()] == [active["id"]]
    assert [e["id"] for e in store.entries(include_disabled=True)] == [active["id"], disabled["id"]]
    assert store.get(disabled["id"])["status"] == "disabled"


def test_counts_survive_a_reload(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")
    entry = store.add(RESOLUTION, today=DAY)

    store.count_use(entry["id"])
    store.count_use(entry["id"])
    store.count_failure(entry["id"])

    reloaded = KnowledgeStore(tmp_path, "mes").get(entry["id"])
    assert reloaded["uses"] == 2 and reloaded["failures"] == 1


def test_counting_a_missing_entry_leaves_a_warning(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")

    store.count_use("r-20261113-001")
    store.count_failure("r-20261113-001")

    assert len(store.warnings) == 2 and "r-20261113-001" in store.warnings[0]


@pytest.mark.parametrize("text", ["(412, 233)을 누른다", "x=412 지점을 누른다", "y: 233", "X = 5", "좌표 (3,4)"])
def test_coordinates_in_the_text_are_rejected(tmp_path, text):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").add({**RESOLUTION, "resolution": text})


@pytest.mark.parametrize("field", ["situation", "resolution"])
def test_coordinates_are_rejected_in_every_text_field(tmp_path, field):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").add({**RESOLUTION, field: "(1, 2)"})
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").add({**COMPLETION, "task": "x: 10 에 저장"})


@pytest.mark.parametrize("text", ["Shop: 3번째 칸", "box 2", "index=3", "WA1, WA2 행"])
def test_ordinary_text_with_numbers_is_accepted(tmp_path, text):
    entry = KnowledgeStore(tmp_path, "mes").add({**RESOLUTION, "resolution": text}, today=DAY)

    assert entry["resolution"] == text


def test_unknown_keys_are_rejected(tmp_path):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").add({**RESOLUTION, "bounds": {"x": 1}})


@pytest.mark.parametrize(
    "entry",
    [
        {**RESOLUTION, "type": "skill"},
        {key: value for key, value in RESOLUTION.items() if key != "resolution"},
        {**RESOLUTION, "situation": "  "},
        {**RESOLUTION, "task": "섞인 키"},
        {**COMPLETION, "checks": []},
        {**COMPLETION, "checks": [{"kind": "screen"}]},
        {**COMPLETION, "checks": [{"kind": "file"}]},
        {**COMPLETION, "checks": [{"kind": "file", "pattern": "out/*.xlsx"}]},
        {**COMPLETION, "checks": [{"kind": "element"}]},
        {**COMPLETION, "checks": [{"kind": "element", "require": {"nam": "저장"}}]},
        {**COMPLETION, "checks": [{"kind": "human", "path": "a.xlsx"}]},
        {**RESOLUTION, "source": {"run_id": "x", "pin": [1, 2]}},
        {**RESOLUTION, "uses": True},
    ],
)
def test_invalid_entries_are_rejected(tmp_path, entry):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").add(entry, today=DAY)
    assert not (tmp_path / "mes").exists() or list((tmp_path / "mes").iterdir()) == []


def test_completion_checks_round_trip(tmp_path):
    checks = [
        {"kind": "file", "path": r"C:\out", "pattern": "*.xlsx"},
        {"kind": "element", "require": {"role": "statictext", "name_contains": "저장 완료"}},
        {"kind": "element", "require": [{"role": "button", "name": "닫기"}]},
        {"kind": "human"},
    ]
    store = KnowledgeStore(tmp_path, "mes")
    entry = store.add({**COMPLETION, "checks": checks}, today=DAY)

    assert KnowledgeStore(tmp_path, "mes").get(entry["id"])["checks"] == checks


@pytest.mark.parametrize(
    "content",
    [
        "id: [unclosed",
        "type: resolution\n",
        VALID_YAML + "extra: 1\n",
        VALID_YAML.replace("r-20261113-001", "r-20261113-009"),
        "- just\n- a list\n",
        VALID_YAML.replace("오른쪽 끝을 누른다", "(10, 20)을 누른다"),
    ],
)
def test_broken_files_are_skipped_with_a_warning(tmp_path, content):
    store = store_with_files(tmp_path, good=VALID_YAML, bad=content)

    assert [e["id"] for e in store.entries()] == ["r-20261113-001"]
    assert len(store.warnings) == 1 and "bad.yaml" in store.warnings[0]


def test_warnings_describe_the_latest_read_only(tmp_path):
    store = store_with_files(tmp_path, good=VALID_YAML, bad="id: [unclosed")
    store.entries()
    store.entries()

    assert len(store.warnings) == 1
    (tmp_path / "mes" / "bad.yaml").unlink()
    store.entries()
    assert store.warnings == []


def test_hand_written_file_without_counts_gets_defaults(tmp_path):
    minimal = "id: r-20261113-001\ntype: resolution\nsituation: 안 열림\nresolution: 끝을 누른다\n"
    entry = store_with_files(tmp_path, good=minimal).entries()[0]

    assert entry["status"] == "active" and entry["uses"] == 0 and entry["failures"] == 0


def test_yaml_dates_in_the_source_come_back_as_text(tmp_path):
    content = VALID_YAML + "source: {run_id: x, date: 2026-11-13}\n"
    entry = store_with_files(tmp_path, good=content).entries()[0]

    assert entry["source"]["date"] == "2026-11-13"


def test_missing_system_folder_has_no_entries(tmp_path):
    store = KnowledgeStore(tmp_path / "nowhere", "mes")

    assert store.entries() == [] and store.warnings == []


def test_files_other_than_yaml_are_ignored(tmp_path):
    store = store_with_files(tmp_path, good=VALID_YAML)
    (tmp_path / "mes" / "notes.txt").write_text("메모", encoding="utf-8")
    (tmp_path / "mes" / "r-20261113-002.yaml.tmp").write_text("id: [", encoding="utf-8")

    assert [e["id"] for e in store.entries()] == ["r-20261113-001"] and store.warnings == []


@pytest.mark.parametrize("name", ["..", ".", "a/b", "a\\b", "", "x" * 65, "a:b", None])
def test_odd_system_names_are_rejected(tmp_path, name):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, name)


@pytest.mark.parametrize("name", ["mes", "Virtual_MES-2", "생산.시스템", "x" * 64])
def test_ordinary_system_names_are_accepted(tmp_path, name):
    assert KnowledgeStore(tmp_path, name).system == name


def test_system_name_falls_back_to_the_exe_stem():
    assert system_name(None, r"C:\apps\Virtual Mes.exe") == "virtual_mes"
    assert system_name("", r"C:\apps\VirtualMes.exe") == "virtualmes"
    assert system_name("mes", r"C:\apps\VirtualMes.exe") == "mes"
    assert system_name(None, r"C:\apps\생산(신).exe") == "생산_신_"


def test_system_name_rejects_an_odd_explicit_name():
    with pytest.raises(KnowledgeError):
        system_name("../mes", r"C:\apps\VirtualMes.exe")


def test_list_systems_names_valid_folders_only(tmp_path):
    for folder in ("mes", "erp", "bad name"):
        (tmp_path / folder).mkdir()
    (tmp_path / "file.yaml").write_text("", encoding="utf-8")

    assert list_systems(tmp_path) == ["erp", "mes"]
    assert list_systems(tmp_path / "nowhere") == []


def test_relevant_returns_the_active_entries(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")
    active = store.add(RESOLUTION, today=DAY)
    store.add({**COMPLETION, "status": "disabled"}, today=DAY)

    assert relevant(store, "엑셀로 저장해줘") == [active]


def entry(entry_id, uses, **fields):
    base = {"id": entry_id, "status": "active", "uses": uses, "failures": 0}
    return {**base, **fields}


def test_briefing_lists_entries_most_used_first_and_reports_truncation():
    entries = [
        entry("r-1", 0, type="resolution", situation="안 열림", resolution="끝을 누른다"),
        entry("c-1", 5, type="completion", task="엑셀 저장", checks=[{"kind": "file", "pattern": "*.xlsx"}, {"kind": "human"}]),
        entry("r-2", 2, type="resolution", situation="셀 모호", resolution="소계 행을 뜻한다"),
    ]

    text, truncated = briefing(entries)

    assert text.splitlines() == [
        "- (c-1) 작업: 엑셀 저장 → 확인: file *.xlsx, human",
        "- (r-2) 상황: 셀 모호 → 해결: 소계 행을 뜻한다",
        "- (r-1) 상황: 안 열림 → 해결: 끝을 누른다",
    ]
    assert truncated is False

    short, truncated = briefing(entries, limit_chars=len(text.splitlines()[0]) + 5)
    assert short == text.splitlines()[0] and truncated is True


def test_briefing_stops_at_the_first_entry_that_does_not_fit():
    entries = [
        entry("r-1", 3, type="resolution", situation="가" * 50, resolution="나"),
        entry("r-2", 2, type="resolution", situation="다" * 500, resolution="라"),
        entry("r-3", 1, type="resolution", situation="마", resolution="바"),
    ]

    text, truncated = briefing(entries, limit_chars=200)

    assert "(r-1)" in text and "(r-2)" not in text and "(r-3)" not in text and truncated is True


def test_briefing_keeps_each_entry_on_one_line():
    multi = entry("r-1", 0, type="resolution", situation="첫 줄\n둘째 줄", resolution="해결\n  이어짐")

    assert briefing([multi])[0] == "- (r-1) 상황: 첫 줄 둘째 줄 → 해결: 해결 이어짐"


def test_briefing_describes_every_check_kind():
    checks = [
        {"kind": "file", "path": r"C:\out\a.xlsx"},
        {"kind": "element", "require": {"role": "statictext", "name_contains": "저장 완료"}},
        {"kind": "human"},
    ]
    text, _ = briefing([entry("c-1", 0, type="completion", task="저장", checks=checks)])

    assert text == r"- (c-1) 작업: 저장 → 확인: file C:\out\a.xlsx, element statictext '저장 완료', human"


def test_empty_briefing():
    assert briefing([]) == ("", False)


def test_example_entries_load():
    store = KnowledgeStore(EXAMPLES, "virtualmes")

    assert len(store.entries()) == 2 and store.warnings == []
    assert {e["type"] for e in store.entries()} == {"resolution", "completion"}


def test_check_entry_validates_without_saving(tmp_path):
    from agent.knowledge import check_entry

    assert check_entry(RESOLUTION)["status"] == "active"
    assert check_entry({**COMPLETION, "id": "c-20261113-004", "uses": 3})["uses"] == 3
    with pytest.raises(KnowledgeError):
        check_entry({**RESOLUTION, "resolution": "(1, 2)를 누른다"})
    with pytest.raises(KnowledgeError):
        check_entry({**RESOLUTION, "bounds": {}})
    assert not list(tmp_path.iterdir())


def test_knowledge_root_takes_the_first_value_given(tmp_path):
    from agent.knowledge import KNOWLEDGE_ENV, knowledge_root

    env = {KNOWLEDGE_ENV: str(tmp_path / "from-env")}
    assert KNOWLEDGE_ENV == "AGENT_KNOWLEDGE_DIR"
    assert knowledge_root(tmp_path / "cli", str(tmp_path / "task"), env) == tmp_path / "cli"
    assert knowledge_root(None, str(tmp_path / "task"), env) == tmp_path / "task"
    assert knowledge_root(None, None, env) == tmp_path / "from-env"
    assert knowledge_root(None, None, {}) == EXAMPLES.parent / "knowledge"


def test_knowledge_root_skips_empty_values_and_returns_an_absolute_path(tmp_path, monkeypatch):
    from agent.knowledge import KNOWLEDGE_ENV, knowledge_root

    monkeypatch.chdir(tmp_path)
    assert knowledge_root("", "  ", {KNOWLEDGE_ENV: ""}) == EXAMPLES.parent / "knowledge"
    relative = knowledge_root("mine", None, {})
    assert relative.is_absolute() and relative == tmp_path / "mine"


def test_knowledge_root_reads_the_process_environment_by_default(tmp_path, monkeypatch):
    from agent.knowledge import KNOWLEDGE_ENV, knowledge_root

    monkeypatch.setenv(KNOWLEDGE_ENV, str(tmp_path))
    assert knowledge_root() == tmp_path
    monkeypatch.delenv(KNOWLEDGE_ENV)
    assert knowledge_root() == EXAMPLES.parent / "knowledge"


def refusal(tmp_path, entry):
    with pytest.raises(KnowledgeError) as caught:
        KnowledgeStore(tmp_path, "mes").add(entry, today=DAY)
    return str(caught.value)


def test_refusals_are_written_for_the_person_in_korean(tmp_path):
    assert refusal(tmp_path, {**RESOLUTION, "resolution": "(120, 40)을 누른다"}) == (
        "해결에 화면 좌표로 보이는 글('(120, 40)')이 있습니다. 요소의 역할, 이름, 부분, 상대 위치로 적어 주세요")
    assert refusal(tmp_path, {**RESOLUTION, "situation": " "}) == "상황이 비어 있습니다"
    assert refusal(tmp_path, {**COMPLETION, "checks": []}) == "확인 방법이 하나 이상 있어야 합니다"
    assert "모르는 항목" in refusal(tmp_path, {**RESOLUTION, "bounds": {}})
    assert "bounds" in refusal(tmp_path, {**RESOLUTION, "bounds": {}})


def test_skipped_files_are_explained_in_korean(tmp_path):
    store = store_with_files(tmp_path, good=VALID_YAML, bad="id: [unclosed")
    store.entries()

    assert store.warnings == ["bad.yaml: 읽을 수 없는 YAML입니다"]


def test_messages_have_no_english_sentences(tmp_path):
    import re

    cases = [
        {**RESOLUTION, "type": "skill"}, {**RESOLUTION, "status": "paused"}, {**RESOLUTION, "uses": -1},
        {**COMPLETION, "checks": [{"kind": "file"}]}, {**COMPLETION, "checks": [{"kind": "screen"}]},
        {**COMPLETION, "checks": [{"kind": "file", "pattern": "a/*.x"}]},
        {**COMPLETION, "checks": [{"kind": "element", "require": {"nam": "x"}}]},
        {**RESOLUTION, "source": {"pin": 1}}, "not a mapping",
    ]
    for entry in cases:
        message = refusal(tmp_path, entry)
        assert re.search("[가-힣]", message) and not re.search(r"\b(must|unknown|needs|missing)\b", message), message
    with pytest.raises(KnowledgeError) as caught:
        KnowledgeStore(tmp_path, "a/b")
    assert re.search("[가-힣]", str(caught.value))
