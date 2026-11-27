"""The registration screen's server side: launch, read, check and save a system profile
(viewer/profile_editor.py, plan task 9, K11)."""

import ast
import json
from pathlib import Path

import pytest
from PIL import Image

from agent.profile import Profile, as_dict, check_profile, load_profile, save_profile
from tests.profile_support import (
    EXE,
    MAIN_COLOR,
    ORDER,
    OTHER_TITLE,
    READING_COMMANDS,
    SYSTEM,
    TITLE,
    MainWindow,
    PlacedWindow,
    box,
    editor_setup,
    item,
    main_screen,
    main_window,
    other_app_window,
    toolbar,
)
from tests.ready_support import node
from viewer.profile_editor import EditorError, problems

ITEM_KEYS = {"n", "kind", "role", "name", "description", "id", "in", "leaf", "rect", "alias"}
TOOLBAR_12 = {"role": "toolbar", "id": "12"}
TOOLBAR_13 = {"role": "toolbar", "id": "13"}


@pytest.fixture(autouse=True)
def pictures_under_tmp_path(tmp_path, monkeypatch):
    """The registration screen's pictures go under tmp_path here, never into the system's temp folder."""
    import tempfile

    from viewer import profile_editor

    folder = tmp_path / "shots"
    folder.mkdir()
    real = tempfile.mkdtemp
    monkeypatch.setattr(profile_editor.tempfile, "mkdtemp", lambda prefix="": real(prefix=prefix, dir=folder))


def launched(tmp_path, windows=None, **options):
    editor, desktop, clock = editor_setup(tmp_path, windows, **options)
    editor.launch(SYSTEM)
    return editor, desktop, clock


def read_main(tmp_path, windows=None, **options):
    editor, desktop, clock = launched(tmp_path, windows, **options)
    return editor, desktop, editor.read(SYSTEM, "w-main")


def by_id(items, native, area=None):
    return [found for found in items if found["id"] == native and (area is None or found["in"] == area)]


def profile_dict(ready=(), elements=(), stable_ids=False, ready_read="snapshot"):
    """What the screen sends to save: the file's keys but format and system."""
    return {"stable_ids": stable_ids, "ready_read": ready_read, "ready": list(ready), "elements": list(elements)}


def no_path_in(answer):
    text = json.dumps(answer, ensure_ascii=False)
    return EXE not in text and EXE.replace("\\", "\\\\") not in text and "Apps" not in text


# --- get ---

def test_get_shows_an_empty_profile_when_there_is_no_file(tmp_path):
    editor, _, _ = editor_setup(tmp_path)

    answer = editor.get(SYSTEM)

    assert answer == {"system": SYSTEM, "exists": False, "profile": as_dict(Profile(system=SYSTEM))}
    assert no_path_in(answer)


def test_get_shows_the_saved_profile(tmp_path):
    editor, _, _ = editor_setup(tmp_path)
    saved = Profile(system=SYSTEM, ready=({"name": ORDER},), stable_ids=True)
    save_profile(tmp_path / "profiles", saved)

    assert editor.get(SYSTEM) == {"system": SYSTEM, "exists": True, "profile": as_dict(saved)}


def test_get_of_a_broken_file_says_where_and_no_value(tmp_path):
    editor, _, _ = editor_setup(tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / f"{SYSTEM}.yaml").write_text("format: 1\nsystem: MES\nready:\n  - {name: ''}\n",
                                                          encoding="utf-8")

    with pytest.raises(EditorError) as raised:
        editor.get(SYSTEM)

    assert raised.value.status == 409
    assert "error" in raised.value.payload


def test_a_system_the_settings_do_not_name_is_refused(tmp_path):
    editor, desktop, _ = editor_setup(tmp_path)

    for call in (lambda: editor.get("Unknown"), lambda: editor.launch("Unknown"),
                 lambda: editor.windows("Unknown"), lambda: editor.read("Unknown", "w-main")):
        with pytest.raises(EditorError) as raised:
            call()
        assert raised.value.status == 400
    assert desktop.calls == []


# --- launch and windows ---

def test_launch_lists_the_windows_of_the_launched_process_and_new_ones_only(tmp_path):
    loading = MainWindow("w-new", 300, title="Loading", appear=0.0)
    editor, desktop, _ = editor_setup(tmp_path, [other_app_window(), main_window(), loading])

    answer = editor.launch(SYSTEM)

    assert answer == {"windows": [{"id": "w-main", "title": TITLE}, {"id": "w-new", "title": "Loading"}]}
    assert desktop.commands("launch") == [("launch", EXE)]
    assert OTHER_TITLE not in json.dumps(answer, ensure_ascii=False)
    assert no_path_in(answer)


def test_windows_lists_again_after_more_windows_open(tmp_path):
    later = MainWindow("w-later", 100, title="Search", appear=2.0)
    editor, desktop, clock = launched(tmp_path, [other_app_window(), main_window(), later])
    clock.advance(3.0)

    answer = editor.windows(SYSTEM)

    assert [window["id"] for window in answer["windows"]] == ["w-main", "w-later"]
    assert len(desktop.commands("launch")) == 1, "listing again launches nothing"


def test_windows_before_a_launch_is_refused(tmp_path):
    editor, desktop, _ = editor_setup(tmp_path)

    with pytest.raises(EditorError) as raised:
        editor.windows(SYSTEM)

    assert raised.value.status == 409
    assert desktop.calls == []


def test_launch_attaches_to_a_system_already_running(tmp_path):
    """launch answers with the process already open, and its windows are the ones to read."""
    running = main_window(appear=-10.0)
    editor, _, _ = editor_setup(tmp_path, [other_app_window(), running])

    assert editor.launch(SYSTEM) == {"windows": [{"id": "w-main", "title": TITLE}]}


def test_a_failed_launch_says_to_close_and_start_again(tmp_path):
    editor, _, _ = editor_setup(tmp_path, launch_error={"code": "AMBIGUOUS_TARGET", "message": f"two {EXE}"})

    with pytest.raises(EditorError) as raised:
        editor.launch(SYSTEM)

    assert raised.value.status == 409
    assert "닫고 다시" in json.dumps(raised.value.payload, ensure_ascii=False)
    assert no_path_in(raised.value.payload)
    assert "two" not in json.dumps(raised.value.payload, ensure_ascii=False)


# --- read ---

def test_read_gives_boxes_and_the_id_nodes_with_children(tmp_path):
    _, _, answer = read_main(tmp_path)

    assert set(answer) == {"read_id", "image", "size", "title", "items", "missing"}
    assert answer["title"] == TITLE
    assert answer["size"] == [800, 600]
    assert "/" not in answer["image"] and "\\" not in answer["image"]
    items = answer["items"]
    assert all(set(found) == ITEM_KEYS for found in items)
    assert sorted(found["n"] for found in items) == list(range(1, len(items) + 1))
    [button] = [found for found in items if found["name"] == ORDER]
    assert (button["kind"], button["id"], button["leaf"]) == ("control", None, True)
    [menu] = by_id(items, "56", TOOLBAR_12)
    assert (menu["kind"], menu["role"], menu["name"], menu["leaf"], menu["alias"]) == ("id", "image", None, True, None)
    assert menu["rect"] == {"x": 10.0, "y": 5.0, "width": 40.0, "height": 20.0}
    assert len(by_id(items, "56")) == 2 and by_id(items, "56", TOOLBAR_13)
    [bar] = by_id(items, "12")
    assert (bar["role"], bar["leaf"], bar["in"]) == ("toolbar", False, None)
    [group] = by_id(items, "71")
    assert (group["role"], group["leaf"]) == ("group", False)
    assert [found["name"] for found in items if found["kind"] == "text"] == ["상태: 준비"]
    assert TITLE not in [found["name"] for found in items], "the window at the root is no item"


def test_read_leaves_out_a_window_node_with_only_an_id(tmp_path):
    inner = node(role="window", native="99", bounds=box(0.0, 300.0), children=[item("57", at=(5.0, 305.0))])
    _, _, answer = read_main(tmp_path, [main_window(tree=main_screen(inner))])

    assert by_id(answer["items"], "99") == []
    assert by_id(answer["items"], "57")


def test_read_shows_the_saved_names(tmp_path):
    save_profile(tmp_path / "profiles", Profile(system=SYSTEM, elements=(
        {"id": "60", "role": "image", "in": TOOLBAR_12, "name": "Execution Plan"},
        {"id": "12", "role": "toolbar", "name": "Menu bar"},
    )))

    _, _, answer = read_main(tmp_path)

    [plan] = by_id(answer["items"], "60")
    [bar] = by_id(answer["items"], "12")
    assert plan["alias"] == "Execution Plan"
    assert (bar["kind"], bar["alias"], bar["leaf"]) == ("id", "Menu bar", False)


def test_read_waits_the_delay_on_the_server(tmp_path):
    editor, desktop, _ = launched(tmp_path)
    desktop.log.clear()

    editor.read(SYSTEM, "w-main", delay_s=3)

    first_snapshot = next(index for index, entry in enumerate(desktop.log)
                          if entry[0] == "run" and entry[1][0] == "snapshot")
    assert ("sleep", 3) in desktop.log[:first_snapshot]


@pytest.mark.parametrize("delay", [-1, 11, "3", True, None])
def test_a_bad_delay_is_refused(tmp_path, delay):
    editor, desktop, _ = launched(tmp_path)
    reads_before = len(desktop.commands("snapshot"))

    with pytest.raises(EditorError) as raised:
        editor.read(SYSTEM, "w-main", delay_s=delay)

    assert raised.value.status == 400
    assert len(desktop.commands("snapshot")) == reads_before


def test_reading_a_window_not_in_the_list_is_refused(tmp_path):
    editor, desktop, _ = launched(tmp_path)

    with pytest.raises(EditorError) as raised:
        editor.read(SYSTEM, "w-other")

    assert raised.value.status == 409
    assert desktop.snapshots_of("w-other") == []


def test_a_window_that_cannot_be_pictured_is_still_read(tmp_path):
    _, _, answer = read_main(tmp_path, unpictured={"w-main"})

    assert (answer["image"], answer["size"]) == (None, None)
    assert answer["items"] and all(found["rect"] is None for found in answer["items"])


MENU_COLOR = (220, 30, 30)


def menu_list(bounds, pid=100, window_id="w-menu", **options):
    """An open menu list: a top-level window of its own, listed before (above) the main window."""
    return PlacedWindow(window_id, pid, bounds, MENU_COLOR, title="", appear=0.0, **options)


def screen_box(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


def shots_of(desktop):
    return [call[call.index("--window-id") + 1] for call in desktop.commands("screenshot")]


def pixel(editor, answer, x, y):
    with Image.open(editor.shot_path(answer["image"])) as picture:
        return picture.convert("RGB").getpixel((x, y))


def with_image_size(editor, answer):
    with Image.open(editor.shot_path(answer["image"])) as picture:
        return picture.size


def center(rect):
    return int(rect["x"] + rect["width"] / 2), int(rect["y"] + rect["height"] / 2)


def test_an_open_menu_list_is_pasted_into_the_picture(tmp_path):
    menu_item = item("8101", role="menuitem", at=(60.0, 40.0))
    windows = [other_app_window(), menu_list(screen_box(150.0, 80.0, 120.0, 100.0)),
               main_window(tree=main_screen(menu_item))]
    editor, desktop, _ = launched(tmp_path, windows)

    answer = editor.read(SYSTEM, "w-main")

    assert shots_of(desktop) == ["w-main", "w-menu"], "the menu list is screenshot after the main window"
    assert answer["size"] == [800, 600]
    [entry] = by_id(answer["items"], "8101")
    assert entry["rect"] == {"x": 60.0, "y": 40.0, "width": 40.0, "height": 20.0}
    assert pixel(editor, answer, *center(entry["rect"])) == MENU_COLOR
    [button] = [found for found in answer["items"] if found["name"] == ORDER]
    assert pixel(editor, answer, *center(button["rect"])) == MAIN_COLOR
    assert answer["missing"] == 0


def test_a_menu_list_that_hangs_out_of_the_window_grows_the_picture(tmp_path):
    menu_item = item("8101", role="menuitem", at=(-50.0, -20.0))
    windows = [menu_list(screen_box(40.0, 20.0, 200.0, 150.0)), main_window(tree=main_screen(menu_item))]
    editor, _, _ = launched(tmp_path, windows)

    answer = editor.read(SYSTEM, "w-main")

    assert answer["size"] == [860, 630], "the picture starts at the menu list's top-left corner"
    [entry] = by_id(answer["items"], "8101")
    assert entry["rect"] == {"x": 10.0, "y": 10.0, "width": 40.0, "height": 20.0}
    assert pixel(editor, answer, *center(entry["rect"])) == MENU_COLOR
    [button] = [found for found in answer["items"] if found["name"] == ORDER]
    assert button["rect"] == {"x": 560.0, "y": 40.0, "width": 40.0, "height": 20.0}
    assert pixel(editor, answer, *center(button["rect"])) == MAIN_COLOR
    assert with_image_size(editor, answer) == (860, 630)


def test_another_process_window_is_never_pasted(tmp_path):
    stranger = menu_list(screen_box(150.0, 80.0, 120.0, 100.0), pid=300, window_id="w-stranger")
    editor, desktop, _ = launched(tmp_path, [stranger, main_window()])

    answer = editor.read(SYSTEM, "w-main")

    assert "w-stranger" not in shots_of(desktop)
    assert pixel(editor, answer, 80, 50) == MAIN_COLOR
    assert answer["size"] == [800, 600]


def test_a_menu_list_that_cannot_be_pictured_is_left_out(tmp_path):
    menu_item = item("8101", role="menuitem", at=(60.0, 40.0))
    windows = [menu_list(screen_box(150.0, 80.0, 120.0, 100.0)), main_window(tree=main_screen(menu_item))]
    editor, desktop, _ = launched(tmp_path, windows, unpictured={"w-menu"})

    answer = editor.read(SYSTEM, "w-main")

    assert shots_of(desktop) == ["w-main", "w-menu"]
    assert answer["size"] == [800, 600] and answer["image"]
    assert by_id(answer["items"], "8101")
    assert pixel(editor, answer, 80, 50) == MAIN_COLOR
    assert answer["missing"] == 1


def test_the_menu_list_is_named_by_the_window_list_after_the_screenshot(tmp_path):
    windows = [menu_list(screen_box(150.0, 80.0, 120.0, 100.0)), main_window()]
    editor, desktop, _ = launched(tmp_path, windows)
    desktop.log.clear()

    editor.read(SYSTEM, "w-main")

    words = [entry[1][0] if entry[1][0] != "screenshot" else ("screenshot", entry[1][2])
             for entry in desktop.log if entry[0] == "run"]
    main_shot = words.index(("screenshot", "w-main"))
    assert "list-windows" in words[main_shot + 1:words.index(("screenshot", "w-menu"))]


def test_the_picture_is_served_from_the_temporary_folder_only(tmp_path):
    editor, _, answer = read_main(tmp_path)

    path = editor.shot_path(answer["image"])

    assert path is not None and path.is_file()
    assert tmp_path / "profiles" not in path.parents
    assert editor.shot_path("../" + answer["image"]) is None
    assert editor.shot_path("nothing.png") is None
    outside = tmp_path / "outside.png"
    outside.write_bytes(bytes([137]) + b"PNG")
    assert editor.shot_path(str(outside)) is None, "an absolute path never reaches a file outside the folder"
    assert editor.shot_path(path.name.replace(".png", ".txt")) is None


def test_every_read_gets_a_new_read_id(tmp_path):
    editor, _, first = read_main(tmp_path)

    second = editor.read(SYSTEM, "w-main")

    assert first["read_id"] != second["read_id"]


def test_only_reading_commands_reach_the_desktop(tmp_path):
    editor, desktop, _ = read_main(tmp_path)
    editor.windows(SYSTEM)
    editor.read(SYSTEM, "w-main", delay_s=1)
    editor.check(SYSTEM, "w-main", [{"name": ORDER}, {"id": "60", "role": "image", "in": TOOLBAR_12}])
    latest = editor.read(SYSTEM, "w-main")
    editor.save(SYSTEM, latest["read_id"], profile_dict(ready=[{"name": ORDER}]))

    assert desktop.words() <= READING_COMMANDS
    assert {"launch", "list-windows", "snapshot", "find", "screenshot"} == desktop.words()
    assert all("--headed" not in call for call in desktop.calls)


def test_the_answers_hold_no_exe_path(tmp_path):
    editor, _, answer = read_main(tmp_path)

    assert no_path_in(answer)
    assert no_path_in(editor.windows(SYSTEM))
    assert no_path_in(editor.check(SYSTEM, "w-main", [{"name": ORDER}]))
    assert no_path_in(editor.get(SYSTEM))


# --- check ---

def test_check_times_both_ways_and_says_which_signal_is_missing(tmp_path):
    editor, _, _ = launched(tmp_path, snapshot_s=2.0, find_s=0.5)

    missing = editor.check(SYSTEM, "w-main", [{"name": ORDER}, {"name": "없는 글"}])
    present = editor.check(SYSTEM, "w-main", [{"name": ORDER}, {"id": "60", "role": "image", "in": TOOLBAR_12}])

    assert missing["snapshot"]["found"] == [True, False]
    assert missing["find"]["found"] == [True, False]
    assert missing["recommended"] is None
    assert (present["snapshot"]["ms"], present["find"]["ms"]) == (2000, 1000)
    assert present["snapshot"]["found"] == present["find"]["found"] == [True, True]
    assert present["recommended"] == "find"


@pytest.mark.parametrize("ready", [[], [{"name": ""}], [{"id": "56"}], [{"name": ORDER, "id": "1", "role": "x"}], "x", None])
def test_check_refuses_signals_the_profile_would_refuse(tmp_path, ready):
    editor, desktop, _ = launched(tmp_path)

    with pytest.raises(EditorError) as raised:
        editor.check(SYSTEM, "w-main", ready)

    assert raised.value.status == 400
    assert desktop.commands("find") == []


# --- problems (pure) ---

PROBLEM_CASES = {
    "an alias two elements fit": (profile_dict(elements=[{"id": "56", "role": "image", "name": "Shipment"}]),
                                  "elements"),
    "an id signal two elements fit": (profile_dict(ready=[{"id": "56", "role": "image"}]), "ready"),
    "a name signal not on the window": (profile_dict(ready=[{"name": "다른 화면 글"}]), "ready"),
    "a signal like the title only": (profile_dict(ready=[{"name": TITLE}]), "ready"),
}


@pytest.mark.parametrize("case", sorted(PROBLEM_CASES))
def test_problems_name_the_row_and_no_value(tmp_path, case):
    _, _, answer = read_main(tmp_path)
    raw, where = PROBLEM_CASES[case]
    profile = check_profile({"format": 1, "system": SYSTEM, **raw}, SYSTEM)

    found = problems(profile, Profile(system=SYSTEM), answer["items"], answer["title"])

    assert [(problem["where"], problem["index"]) for problem in found] == [(where, 0)]
    text = json.dumps(found, ensure_ascii=False)
    for value in ("56", "Shipment", "다른 화면 글", TITLE, "image"):
        assert value not in text


def test_a_title_only_signal_is_told_apart_from_an_absent_one(tmp_path):
    _, _, answer = read_main(tmp_path)

    def problem_of(raw):
        profile = check_profile({"format": 1, "system": SYSTEM, **raw}, SYSTEM)
        return problems(profile, Profile(system=SYSTEM), answer["items"], answer["title"])[0]["problem"]

    assert problem_of(PROBLEM_CASES["a signal like the title only"][0]) != \
        problem_of(PROBLEM_CASES["a name signal not on the window"][0])


def test_good_entries_have_no_problems(tmp_path):
    _, _, answer = read_main(tmp_path)
    profile = check_profile({"format": 1, "system": SYSTEM, **profile_dict(
        ready=[{"name": ORDER}, {"id": "60", "role": "image"}, {"name": ORDER, "role": "button"}],
        elements=[{"id": "56", "role": "image", "in": TOOLBAR_12, "name": "Shipment"},
                  {"id": "90", "role": "image", "in": {"role": "toolbar", "id": "77"}, "name": "다른 화면"}])}, SYSTEM)

    assert problems(profile, Profile(system=SYSTEM), answer["items"], answer["title"]) == []


def test_the_index_is_the_position_in_its_list(tmp_path):
    _, _, answer = read_main(tmp_path)
    profile = check_profile({"format": 1, "system": SYSTEM, **profile_dict(
        ready=[{"name": ORDER}, {"name": "없는 글"}],
        elements=[{"id": "60", "role": "image", "name": "Plan"}, {"id": "56", "role": "image", "name": "Ship"}])},
        SYSTEM)

    found = problems(profile, Profile(system=SYSTEM), answer["items"], answer["title"])

    assert sorted((problem["where"], problem["index"]) for problem in found) == [("elements", 1), ("ready", 1)]


def test_an_unchanged_ready_list_is_not_checked_again(tmp_path):
    """Plan decision 7: reading a popup to add names must not be stopped by the main screen's signals."""
    _, _, answer = read_main(tmp_path)
    saved = Profile(system=SYSTEM, ready=({"name": "메인 화면 글"},))
    same = check_profile({"format": 1, "system": SYSTEM, **profile_dict(ready=[{"name": "메인 화면 글"}])}, SYSTEM)
    changed = check_profile({"format": 1, "system": SYSTEM, **profile_dict(
        ready=[{"name": "메인 화면 글"}, {"name": "또 다른 글"}])}, SYSTEM)

    assert problems(same, saved, answer["items"], answer["title"]) == []
    assert [(problem["where"], problem["index"]) for problem in problems(changed, saved, answer["items"],
                                                                          answer["title"])] == \
        [("ready", 0), ("ready", 1)]


# --- save ---

def test_save_writes_the_profile_and_keeps_names_from_other_screens(tmp_path):
    other_screen = {"id": "90", "role": "image", "in": {"role": "toolbar", "id": "77"}, "name": "다른 화면 메뉴"}
    save_profile(tmp_path / "profiles", Profile(system=SYSTEM, elements=(other_screen,)))
    editor, _, answer = read_main(tmp_path)
    plan = {"id": "60", "role": "image", "in": TOOLBAR_12, "name": "Execution Plan"}

    result = editor.save(SYSTEM, answer["read_id"], profile_dict(
        ready=[{"name": ORDER}], elements=[other_screen, plan], stable_ids=True, ready_read="find"))

    assert result == {"saved": True}
    assert load_profile(tmp_path / "profiles", SYSTEM) == Profile(
        system=SYSTEM, ready=({"name": ORDER},), elements=(other_screen, plan), stable_ids=True, ready_read="find")


def test_save_takes_the_system_it_was_asked_for(tmp_path):
    editor, _, answer = read_main(tmp_path)

    editor.save(SYSTEM, answer["read_id"], {**profile_dict(ready=[{"name": ORDER}]), "system": "GSCM", "format": 1})

    assert load_profile(tmp_path / "profiles", SYSTEM).ready == ({"name": ORDER},)
    assert not (tmp_path / "profiles" / "GSCM.yaml").exists()


def test_save_with_an_older_read_is_refused(tmp_path):
    editor, _, first = read_main(tmp_path)
    editor.read(SYSTEM, "w-main")

    with pytest.raises(EditorError) as raised:
        editor.save(SYSTEM, first["read_id"], profile_dict(ready=[{"name": ORDER}]))

    assert raised.value.status == 409
    assert "다시 읽어" in json.dumps(raised.value.payload, ensure_ascii=False)
    assert not (tmp_path / "profiles" / f"{SYSTEM}.yaml").exists()


def test_save_before_any_read_is_refused(tmp_path):
    editor, _, _ = launched(tmp_path)

    with pytest.raises(EditorError) as raised:
        editor.save(SYSTEM, "r-1", profile_dict(ready=[{"name": ORDER}]))

    assert raised.value.status == 409


def test_save_with_problems_writes_nothing_and_names_the_rows(tmp_path):
    editor, _, answer = read_main(tmp_path)

    with pytest.raises(EditorError) as raised:
        editor.save(SYSTEM, answer["read_id"], profile_dict(
            ready=[{"name": ORDER}], elements=[{"id": "56", "role": "image", "name": "Shipment"}]))

    assert raised.value.status == 400
    assert [(problem["where"], problem["index"]) for problem in raised.value.payload["problems"]] == [("elements", 0)]
    assert not (tmp_path / "profiles" / f"{SYSTEM}.yaml").exists()


@pytest.mark.parametrize("broken", [
    profile_dict(elements=[{"id": "60", "role": "image", "in": TOOLBAR_12, "name": "가" * 81}]),
    profile_dict(elements=[{"id": "60", "role": "image", "in": TOOLBAR_12, "name": "두\n줄"}]),
    profile_dict(ready=[{"name": ORDER, "unknown": 1}]),
    profile_dict(ready_read="fast"),
    "not a profile",
])
def test_a_profile_the_check_refuses_is_not_saved(tmp_path, broken):
    editor, _, answer = read_main(tmp_path)

    with pytest.raises(EditorError) as raised:
        editor.save(SYSTEM, answer["read_id"], broken)

    assert raised.value.status == 400
    assert not (tmp_path / "profiles" / f"{SYSTEM}.yaml").exists()
    text = json.dumps(raised.value.payload, ensure_ascii=False)
    assert "가" * 81 not in text and "두\n줄" not in text and "fast" not in text


def test_two_containers_with_the_same_id_can_both_be_saved(tmp_path):
    editor, _, answer = read_main(tmp_path)
    first = {"id": "56", "role": "image", "in": TOOLBAR_12, "name": "Shipment Plan"}
    second = {"id": "56", "role": "image", "in": TOOLBAR_13, "name": "Shipment History"}

    editor.save(SYSTEM, answer["read_id"], profile_dict(ready=[{"name": ORDER}], elements=[first, second]))

    assert load_profile(tmp_path / "profiles", SYSTEM).elements == (first, second)


def test_an_unchanged_ready_list_saves_from_another_screen(tmp_path):
    save_profile(tmp_path / "profiles", Profile(system=SYSTEM, ready=({"name": "메인 화면 글"},)))
    popup = node("Popup", role="window", bounds={"x": 100.0, "y": 50.0, "width": 800.0, "height": 600.0},
                 children=[toolbar("40", item("41", at=(10.0, 5.0)))])
    editor, _, answer = read_main(tmp_path, [main_window(tree=popup, title="Popup")])
    entry = {"id": "41", "role": "image", "in": {"role": "toolbar", "id": "40"}, "name": "Popup item"}

    editor.save(SYSTEM, answer["read_id"], profile_dict(ready=[{"name": "메인 화면 글"}], elements=[entry]))

    assert load_profile(tmp_path / "profiles", SYSTEM).elements == (entry,)


# --- close ---

def test_close_removes_the_pictures(tmp_path):
    editor, desktop, answer = read_main(tmp_path)
    path = editor.shot_path(answer["image"])
    assert path.is_file() and desktop.shots

    editor.close()

    assert not path.exists()
    assert not path.parent.exists()
    assert editor.shot_path(answer["image"]) is None
    editor.close()


def test_nothing_read_is_left_under_the_profile_folder(tmp_path):
    editor, _, answer = read_main(tmp_path)
    editor.save(SYSTEM, answer["read_id"], profile_dict(ready=[{"name": ORDER}]))

    assert sorted(path.name for path in (tmp_path / "profiles").iterdir()) == [f"{SYSTEM}.yaml"]



def test_the_editor_never_imports_the_model():
    tree = ast.parse((Path(__file__).resolve().parent.parent / "viewer" / "profile_editor.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

    assert not {name for name in imported if name and name.startswith(("agent.llm", "agent.loop", "openai"))}


def test_items_follow_the_tree_order(tmp_path):
    _, _, answer = read_main(tmp_path)

    order = [(found["role"], found["id"]) for found in answer["items"]]

    assert order == [("button", None), ("toolbar", "12"), ("image", "56"), ("image", "60"), ("toolbar", "13"),
                     ("image", "56"), ("group", "71"), ("text", None)]


def test_profile_editor_reads_deep(tmp_path):
    _, desktop, _ = read_main(tmp_path)

    [read] = desktop.commands("snapshot")
    assert read[read.index("--max-depth") + 1] == "30"
