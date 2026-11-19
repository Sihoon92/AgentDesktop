"""The system profile file: what an administrator registered for one system (agent/profile.py)."""

import dataclasses
from pathlib import Path

import pytest
import yaml

from agent import profile as profile_module
from agent.profile import (
    ALIAS_MAX,
    FORMAT,
    PROFILE_ENV,
    READ_MODES,
    Profile,
    ProfileError,
    alias_of,
    as_dict,
    check_profile,
    entry_of,
    load_profile,
    profile_root,
    save_profile,
)

HERE = Path(__file__).resolve().parent.parent
TOOLBAR = {"role": "toolbar", "id": "12"}
DESIGN_EXAMPLE = """\
format: 1
system: GSCM
stable_ids: true
ready_read: snapshot
ready:
  - {name: Order Management}
  - {id: '56', role: image, in: {role: toolbar, id: '12'}}
elements:
  - {id: '56', role: image, in: {role: toolbar, id: '12'}, name: Shipment Plan}
  - {id: '60', role: image, in: {role: toolbar, id: '12'}, name: Execution Plan}
"""
EXAMPLE = Profile(
    system="GSCM",
    ready=({"name": "Order Management"}, {"id": "56", "role": "image", "in": TOOLBAR}),
    elements=({"id": "56", "role": "image", "in": TOOLBAR, "name": "Shipment Plan"},
              {"id": "60", "role": "image", "in": TOOLBAR, "name": "Execution Plan"}),
    stable_ids=True,
    ready_read="snapshot",
)


def write(root, system, text):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{system}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def base(**changes):
    raw = {"format": 1, "system": "MES", "stable_ids": False, "ready_read": "snapshot", "ready": [], "elements": []}
    raw.update(changes)
    return raw


def element(id_="4417", role="image", name="Shipment Plan", **extra):
    return {"id": id_, "role": role, "name": name, **extra}


def strings_in(value):
    """Every text a file holds, keys included, but not the keys this format knows."""
    known = {"format", "system", "stable_ids", "ready_read", "ready", "elements", "name", "role", "id", "in"}
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            if key not in known:
                found.add(str(key))
            found |= strings_in(item)
        return found
    if isinstance(value, list):
        return set().union(*(strings_in(item) for item in value)) if value else set()
    if isinstance(value, str):
        return {value, value.strip()} - {""}
    if isinstance(value, bool) or value is None:
        return set()
    return {str(value)}


def test_contract_names():
    assert PROFILE_ENV == "AGENT_PROFILE_DIR"
    assert FORMAT == 1
    assert READ_MODES == ("snapshot", "find")
    assert ALIAS_MAX == 80
    assert profile_module.DEFAULT_ROOT == HERE / "profiles"


def test_an_empty_profile():
    empty = Profile()

    assert (empty.system, empty.ready, empty.elements, empty.stable_ids, empty.ready_read) == \
        (None, (), (), False, "snapshot")


def test_a_profile_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        EXAMPLE.stable_ids = False
    assert isinstance(EXAMPLE.ready, tuple) and isinstance(EXAMPLE.elements, tuple)


def test_a_missing_file_is_an_empty_profile(tmp_path):
    assert load_profile(tmp_path, "MES") == Profile(system="MES")
    assert load_profile(tmp_path / "no-such-folder", "MES") == Profile(system="MES")


def test_a_system_name_the_knowledge_folder_would_refuse_is_refused(tmp_path):
    for bad in ("../MES", "", "a/b"):
        with pytest.raises(ProfileError) as caught:
            load_profile(tmp_path, bad)
        assert caught.value.reason == "시스템 이름 규칙에 맞지 않음"


def test_the_design_example_is_read(tmp_path):
    write(tmp_path, "GSCM", DESIGN_EXAMPLE)

    assert load_profile(tmp_path, "GSCM") == EXAMPLE


def test_save_then_load_gives_the_same_profile(tmp_path):
    path = save_profile(tmp_path, EXAMPLE)

    assert path == tmp_path / "GSCM.yaml"
    assert load_profile(tmp_path, "GSCM") == EXAMPLE


def test_a_saved_file_is_written_as_the_design_shows_it(tmp_path):
    """People fix this file by hand, so the keys come in the design's order, one line per item."""
    save_profile(tmp_path, EXAMPLE)

    assert (tmp_path / "GSCM.yaml").read_text(encoding="utf-8") == DESIGN_EXAMPLE
    assert [path.name for path in tmp_path.iterdir()] == ["GSCM.yaml"]


def test_as_dict_is_the_file_shape():
    assert as_dict(EXAMPLE) == yaml.safe_load(DESIGN_EXAMPLE)
    assert list(as_dict(EXAMPLE)) == ["format", "system", "stable_ids", "ready_read", "ready", "elements"]
    assert list(as_dict(EXAMPLE)["elements"][0]) == ["id", "role", "in", "name"]
    assert list(as_dict(EXAMPLE)["ready"][1]) == ["id", "role", "in"]


def test_korean_names_are_written_as_utf8_text(tmp_path):
    korean = Profile(system="공장A", ready=({"name": "생산 운영", "role": "text"},),
                     elements=({"id": "71", "role": "image", "in": {"role": "pane", "name": "메뉴"},
                                "name": "출하 계획"},))

    save_profile(tmp_path, korean)
    text = (tmp_path / "공장A.yaml").read_text(encoding="utf-8")

    assert "system: 공장A" in text and "출하 계획" in text and "\\u" not in text
    assert load_profile(tmp_path, "공장A") == korean


def test_saving_replaces_the_file_and_leaves_no_temporary(tmp_path):
    save_profile(tmp_path, EXAMPLE)
    save_profile(tmp_path, dataclasses.replace(EXAMPLE, stable_ids=False))

    assert load_profile(tmp_path, "GSCM").stable_ids is False
    assert [path.name for path in tmp_path.iterdir()] == ["GSCM.yaml"]


def test_saving_a_broken_profile_writes_nothing(tmp_path):
    broken = dataclasses.replace(EXAMPLE, elements=({"id": "56", "role": "image", "name": "x" * (ALIAS_MAX + 1)},))

    with pytest.raises(ProfileError):
        save_profile(tmp_path, broken)
    with pytest.raises(ProfileError):
        save_profile(tmp_path, Profile())
    assert list(tmp_path.iterdir()) == []


def test_an_unquoted_number_id_is_read_as_text(tmp_path):
    write(tmp_path, "MES", "format: 1\nsystem: MES\nready:\n  - {id: 56, role: image, in: {role: toolbar, id: 12}}\n"
                           "elements:\n  - {id: 60, role: image, name: Execution Plan}\n")

    loaded = load_profile(tmp_path, "MES")

    assert loaded.ready == ({"id": "56", "role": "image", "in": TOOLBAR},)
    assert loaded.elements == ({"id": "60", "role": "image", "name": "Execution Plan"},)


def test_optional_keys_take_their_defaults(tmp_path):
    write(tmp_path, "MES", "format: 1\nsystem: MES\nready:\nelements: []\n")

    assert load_profile(tmp_path, "MES") == Profile(system="MES")


def test_names_lose_their_outer_spaces_and_ids_keep_theirs():
    checked = check_profile(base(ready=[{"name": "  조회  "}],
                                 elements=[element(name=" 출하 ", **{"in": {"role": "pane", "name": " 메뉴 "}})]))

    assert checked.ready == ({"name": "조회"},)
    assert checked.elements == ({"id": "4417", "role": "image", "in": {"role": "pane", "name": "메뉴"},
                                 "name": "출하"},)


def test_a_file_copied_from_another_system_is_refused(tmp_path):
    write(tmp_path, "MES", DESIGN_EXAMPLE)

    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path, "MES")

    assert caught.value.reason == "system 값이 파일 이름과 다름"
    assert "GSCM" not in str(caught.value) and "MES" not in str(caught.value)


def test_check_profile_without_a_system_takes_the_files():
    assert check_profile(yaml.safe_load(DESIGN_EXAMPLE)) == EXAMPLE
    with pytest.raises(ProfileError):
        check_profile(yaml.safe_load(DESIGN_EXAMPLE), system="MES")


BROKEN = [
    ("top-not-mapping", ["Order Management"], "키와 값의 묶음이 아님"),
    ("unknown-top-key", base(colour="Ultramarine"), "맨 위: 모르는 키 1개"),
    ("no-format", {"system": "MES"}, "format이 없음"),
    ("format-2", base(format=2), f"format은 {FORMAT}이어야 함"),
    ("no-system", {"format": 1}, "system이 없음"),
    ("bad-system", base(system="../Ultramarine"), "system 이름 규칙에 맞지 않음"),
    ("stable-ids-text", base(stable_ids="Ultramarine"), "stable_ids는 참이나 거짓이어야 함"),
    ("unknown-read", base(ready_read="Ultramarine"), "ready_read는 snapshot, find 중 하나여야 함"),
    ("ready-not-list", base(ready={"name": "Order Management"}), "ready는 목록이어야 함"),
    ("ready-item-text", base(ready=["Order Management"]), "ready 1번째 항목: 키와 값의 묶음이 아님"),
    ("unknown-ready-key", base(ready=[{"name": "Order Management", "colour": "Ultramarine"}]),
     "ready 1번째 항목: 모르는 키"),
    ("empty-signal-name", base(ready=[{"name": "   "}]), "ready 1번째 항목: name이 비어 있음"),
    ("mixed-signal", base(ready=[{"name": "Order Management", "id": "4417", "role": "image"}]),
     "ready 1번째 항목: name과 id를 함께 적음"),
    ("neither", base(ready=[{"role": "image"}]), "ready 1번째 항목: name이나 id가 없음"),
    ("id-signal-no-role", base(ready=[{"id": "4417"}]), "ready 1번째 항목: role이 없음"),
    ("name-signal-with-in", base(ready=[{"name": "Order Management", "in": {"role": "pane", "id": "9012"}}]),
     "ready 1번째 항목: 모르는 키"),
    ("in-role-only", base(ready=[{"id": "4417", "role": "image", "in": {"role": "toolbar"}}]),
     "ready 1번째 항목: in은 role과 id, 또는 role과 name이어야 함"),
    ("in-id-and-name", base(elements=[element(**{"in": {"role": "toolbar", "id": "9012", "name": "Navigator"}})]),
     "elements 1번째 항목: in은 role과 id, 또는 role과 name이어야 함"),
    ("in-not-mapping", base(elements=[element(**{"in": "Navigator"})]),
     "elements 1번째 항목: in은 role과 id, 또는 role과 name이어야 함"),
    ("in-empty-id", base(elements=[element(**{"in": {"role": "toolbar", "id": " "}})]),
     "elements 1번째 항목: in의 id가 비어 있음"),
    ("same-signal", base(ready=[{"name": "Order Management"}, {"name": " Order Management "}]),
     "ready 2번째 항목: 1번째 항목과 같음"),
    ("same-id-signal", base(ready=[{"id": "4417", "role": "image"}, {"id": 4417, "role": "image"}]),
     "ready 2번째 항목: 1번째 항목과 같음"),
    ("elements-not-list", base(elements="Shipment Plan"), "elements는 목록이어야 함"),
    ("element-no-name", base(elements=[{"id": "4417", "role": "image"}]), "elements 1번째 항목: name이 없음"),
    ("element-no-id", base(elements=[{"role": "image", "name": "Shipment Plan"}]), "elements 1번째 항목: id가 없음"),
    ("element-unknown-key", base(elements=[element(colour="Ultramarine")]), "elements 1번째 항목: 모르는 키"),
    ("element-bool-id", base(elements=[element(id_=True)]), "elements 1번째 항목: id가 글이 아님"),
    ("element-empty-role", base(elements=[element(role=" ")]), "elements 1번째 항목: role이 비어 있음"),
    ("alias-newline", base(elements=[element(name="Shipment\nPlan")]), "elements 1번째 항목: name이 한 줄이 아님"),
    ("alias-81", base(elements=[element(name="가" * (ALIAS_MAX + 1))]),
     f"elements 1번째 항목: name이 {ALIAS_MAX}자를 넘음"),
    ("same-element", base(elements=[element(**{"in": {"role": "toolbar", "id": "9012"}}),
                                    element(name="Execution Plan", **{"in": {"role": "toolbar", "id": 9012}})]),
     "elements 2번째 항목: 1번째 항목과 같음"),
]


@pytest.mark.parametrize("raw, expected", [case[1:] for case in BROKEN], ids=[case[0] for case in BROKEN])
def test_a_broken_profile_is_refused_without_its_values(tmp_path, raw, expected):
    write(tmp_path, "MES", yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))

    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path, "MES")

    assert caught.value.reason == expected
    assert str(caught.value) == expected
    leaked = [value for value in strings_in(raw) if len(value) > 1 and value in str(caught.value)]
    assert leaked == []


def test_the_same_id_in_two_areas_is_two_entries():
    checked = check_profile(base(elements=[element(**{"in": {"role": "toolbar", "id": "9012"}}),
                                           element(**{"in": {"role": "toolbar", "id": "9013"}}),
                                           element(**{"in": {"role": "toolbar", "name": "9012"}}),
                                           element()]))

    assert len(checked.elements) == 4


def test_a_name_signal_with_a_role_is_not_the_same_as_one_without():
    checked = check_profile(base(ready=[{"name": "조회"}, {"name": "조회", "role": "button"}]))

    assert checked.ready == ({"name": "조회"}, {"name": "조회", "role": "button"})


def test_a_yaml_error_names_the_line_only(tmp_path):
    write(tmp_path, "MES", "format: 1\nsystem: MES\nready:\n  - {name: Order Management\n")

    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path, "MES")

    assert caught.value.reason.startswith("YAML 형식 오류 ")
    assert caught.value.reason.endswith("행")
    assert "Order" not in str(caught.value)


def test_a_file_not_in_utf8_is_refused(tmp_path):
    tmp_path.joinpath("MES.yaml").write_bytes("format: 1\nsystem: MES\nready:\n  - {name: 조회}\n".encode("cp949"))

    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path, "MES")

    assert caught.value.reason == "UTF-8이 아님"


def test_an_empty_file_is_refused(tmp_path):
    write(tmp_path, "MES", "")

    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path, "MES")

    assert caught.value.reason == "키와 값의 묶음이 아님"


def test_an_alias_with_quotes_is_kept(tmp_path):
    quoted = Profile(system="MES", elements=(
        {"id": "56", "role": "image", "name": 'Plan "A"'},
        {"id": "57", "role": "image", "name": "Kim's {plan}, v2: \"final\""},
    ))

    save_profile(tmp_path, quoted)

    assert load_profile(tmp_path, "MES") == quoted


def test_alias_of_needs_the_container_when_the_entry_writes_one():
    assert alias_of(EXAMPLE, "image", "56", {"role": "toolbar", "id": "12"}) == "Shipment Plan"
    assert alias_of(EXAMPLE, "image", "60", {"role": "toolbar", "id": "12"}) == "Execution Plan"
    assert alias_of(EXAMPLE, "image", "56", {"role": "toolbar", "id": "13"}) is None
    assert alias_of(EXAMPLE, "image", "56", None) is None
    assert alias_of(EXAMPLE, "button", "56", {"role": "toolbar", "id": "12"}) is None
    assert alias_of(EXAMPLE, "image", None, {"role": "toolbar", "id": "12"}) is None
    assert alias_of(Profile(), "image", "56", None) is None


def test_alias_of_an_entry_without_a_container_fits_anywhere():
    loose = Profile(system="MES", elements=({"id": "56", "role": "image", "name": "Shipment Plan"},))

    assert alias_of(loose, "image", "56", None) == "Shipment Plan"
    assert alias_of(loose, "image", "56", {"role": "toolbar", "id": "12"}) == "Shipment Plan"


def test_the_entry_that_writes_the_container_comes_before_the_one_that_does_not():
    both = Profile(system="MES", elements=({"id": "56", "role": "image", "name": "Anywhere"},
                                           {"id": "56", "role": "image", "in": TOOLBAR, "name": "In the toolbar"}))

    assert alias_of(both, "image", "56", TOOLBAR) == "In the toolbar"
    assert alias_of(both, "image", "56", {"role": "toolbar", "id": "13"}) == "Anywhere"
    assert entry_of(both, "image", "56", TOOLBAR) is both.elements[1]


def test_profile_root_order(tmp_path):
    environ = {PROFILE_ENV: str(tmp_path / "from-env")}

    assert profile_root("given", str(tmp_path / "settings"), environ) == Path("given").resolve()
    assert profile_root(None, str(tmp_path / "settings"), environ) == (tmp_path / "settings").resolve()
    assert profile_root(" ", "", environ) == (tmp_path / "from-env").resolve()
    assert profile_root(None, None, {}) == HERE / "profiles"
    assert profile_root(None, None, {PROFILE_ENV: "  "}) == HERE / "profiles"


def test_the_profiles_folder_stays_out_of_git():
    assert "/profiles/" in (HERE / ".gitignore").read_text(encoding="utf-8").splitlines()
