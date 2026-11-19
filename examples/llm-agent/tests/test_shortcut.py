"""Reading shortcuts: real .lnk files made in a temporary folder with the same shell COM, never
launched; and the pure part that says whether what was read can be launched."""

import ctypes
import sys
import threading

import pytest

from agent import shortcut
from agent.shortcut import (
    CODES,
    MESSAGES,
    NO_TARGET,
    NOT_EXE,
    TARGET_MISSING,
    UNREADABLE,
    Shortcut,
    ShortcutError,
    is_shortcut,
    launch_args,
    launch_problem,
    read_link,
    resolve_shortcut,
    split_arguments,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the shell's shortcut reader is Windows only")

LINK_SET_ID_LIST, LINK_SET_WORKING_DIRECTORY, LINK_SET_ARGUMENTS, LINK_SET_PATH = 5, 9, 11, 20
PERSIST_SAVE = 6
CONTROL_PANEL = "::{26EE0668-A00A-44D7-9371-BEB064C98683}"
COINIT_MULTITHREADED = 0x0


def make_link(path, target=None, arguments=None, working_dir=None, id_list=None):
    """Writes a shortcut at `path` with the shell's own ShellLink object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with shortcut._shell_link() as (link, persist):
        if target is not None:
            _ok(shortcut._method(link, LINK_SET_PATH, ctypes.c_wchar_p)(str(target)))
        if id_list is not None:
            _ok(shortcut._method(link, LINK_SET_ID_LIST, ctypes.c_void_p)(id_list))
        if arguments is not None:
            _ok(shortcut._method(link, LINK_SET_ARGUMENTS, ctypes.c_wchar_p)(arguments))
        if working_dir is not None:
            _ok(shortcut._method(link, LINK_SET_WORKING_DIRECTORY, ctypes.c_wchar_p)(str(working_dir)))
        _ok(shortcut._method(persist, PERSIST_SAVE, ctypes.c_wchar_p, ctypes.c_int)(str(path), 1))
    return path


def make_control_panel_link(path):
    """A shortcut to the Control Panel: an item with no file path."""
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHParseDisplayName.argtypes = [
        ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong, ctypes.c_void_p,
    ]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    pidl = ctypes.c_void_p()
    ole32.CoInitializeEx(None, 0x2)
    try:
        _ok(shell32.SHParseDisplayName(CONTROL_PANEL, None, ctypes.byref(pidl), 0, None))
        try:
            return make_link(path, id_list=pidl)
        finally:
            ole32.CoTaskMemFree(pidl)
    finally:
        ole32.CoUninitialize()


def _ok(result):
    assert result >= 0, hex(result & 0xFFFFFFFF)


def make_exe(path):
    """A file named like an exe. It is never run, so it need not be one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


def on_thread(function):
    """Runs `function` on a new thread and gives back what it returned, or raises what it raised."""
    outcome = {}

    def body():
        try:
            outcome["value"] = function()
        except BaseException as error:
            outcome["error"] = error

    worker = threading.Thread(target=body)
    worker.start()
    worker.join(30)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def co_initialize(model):
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    return ole32.CoInitializeEx(None, model)


def co_uninitialize():
    ctypes.WinDLL("ole32").CoUninitialize()


def code_of(call):
    with pytest.raises(ShortcutError) as caught:
        call()
    return caught.value.code


# Reading real shortcuts


@windows_only
def test_a_shortcut_with_only_a_target_has_no_arguments_and_no_working_folder(tmp_path):
    exe = make_exe(tmp_path / "app" / "tool.exe")
    link = make_link(tmp_path / "tool.lnk", target=exe)

    assert read_link(link) == Shortcut(target=str(exe), arguments=(), working_dir="")
    assert resolve_shortcut(link) == Shortcut(target=str(exe))


@windows_only
def test_the_arguments_are_split_by_the_windows_rules(tmp_path):
    exe = make_exe(tmp_path / "tool.exe")
    text = '"C:\\My Files\\a b.txt" -site=KR --mode fast /x="y z" key=value'
    link = make_link(tmp_path / "tool.lnk", target=exe, arguments=text)

    assert read_link(link).arguments == (
        "C:\\My Files\\a b.txt", "-site=KR", "--mode", "fast", "/x=y z", "key=value",
    )


@windows_only
def test_the_working_folder_has_its_environment_variables_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTCUT_TEST_HOME", str(tmp_path))
    exe = make_exe(tmp_path / "tool.exe")
    link = make_link(tmp_path / "tool.lnk", target=exe, working_dir="%SHORTCUT_TEST_HOME%\\작업 폴더")

    assert read_link(link).working_dir == f"{tmp_path}\\작업 폴더"


@windows_only
def test_a_target_written_with_an_environment_variable_is_read_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTCUT_TEST_HOME", str(tmp_path))
    exe = make_exe(tmp_path / "tool.exe")
    link = make_link(tmp_path / "tool.lnk", target="%SHORTCUT_TEST_HOME%\\tool.exe")

    assert resolve_shortcut(link).target.lower() == str(exe).lower()


@windows_only
def test_a_path_with_hangul_and_spaces_is_read_in_full(tmp_path):
    exe = make_exe(tmp_path / "앱 폴더" / "대상 앱.exe")
    link = make_link(
        tmp_path / "바로 가기" / "가상 앱.lnk", target=exe, arguments='"한글 인자" 둘', working_dir=tmp_path / "앱 폴더",
    )

    assert resolve_shortcut(link) == Shortcut(
        target=str(exe), arguments=("한글 인자", "둘"), working_dir=str(tmp_path / "앱 폴더"),
    )


@windows_only
def test_a_target_that_is_not_an_exe_is_not_launched(tmp_path):
    script = tmp_path / "start.bat"
    script.write_text("@echo off\n", encoding="ascii")
    link = make_link(tmp_path / "start.lnk", target=script)

    assert read_link(link).target == str(script)
    assert code_of(lambda: resolve_shortcut(link)) == NOT_EXE


@windows_only
def test_a_target_exe_that_is_not_there_is_not_launched(tmp_path):
    exe = make_exe(tmp_path / "gone.exe")
    link = make_link(tmp_path / "gone.lnk", target=exe)
    exe.unlink()

    assert read_link(link).target == str(exe)
    assert code_of(lambda: resolve_shortcut(link)) == TARGET_MISSING


@windows_only
def test_a_shortcut_with_no_file_path_has_no_target(tmp_path):
    link = make_control_panel_link(tmp_path / "panel.lnk")

    assert read_link(link).target == ""
    assert code_of(lambda: resolve_shortcut(link)) == NO_TARGET


@windows_only
def test_a_missing_shortcut_file_is_unreadable(tmp_path):
    assert code_of(lambda: read_link(tmp_path / "nothing.lnk")) == UNREADABLE
    assert code_of(lambda: resolve_shortcut(tmp_path / "nothing.lnk")) == UNREADABLE


@windows_only
@pytest.mark.parametrize("content", [
    b"", b"not a shortcut\r\n", b"MZ" + bytes(200), shortcut.LINK_HEADER, shortcut.LINK_HEADER + b"\xff" * 200,
])
def test_a_file_that_is_not_a_shortcut_but_is_named_like_one_is_unreadable(tmp_path, content):
    fake = tmp_path / "fake.lnk"
    fake.write_bytes(content)

    assert code_of(lambda: read_link(fake)) == UNREADABLE


@windows_only
def test_a_real_shortcut_begins_with_the_link_header(tmp_path):
    exe = make_exe(tmp_path / "tool.exe")
    link = make_link(tmp_path / "tool.lnk", target=exe)

    assert shortcut.has_link_header(link.read_bytes())


@pytest.mark.parametrize("head, expected", [
    (shortcut.LINK_HEADER + b"rest", True),
    (shortcut.LINK_HEADER, True),
    (shortcut.LINK_HEADER[:-1], False),
    (b"", False),
    (b"\x4c\x00\x00\x00" + bytes(16), False),
])
def test_has_link_header_needs_the_size_and_the_class_id(head, expected):
    assert shortcut.has_link_header(head) is expected


@windows_only
def test_a_relative_path_is_read_from_the_current_folder(tmp_path, monkeypatch):
    exe = make_exe(tmp_path / "tool.exe")
    make_link(tmp_path / "tool.lnk", target=exe)
    monkeypatch.chdir(tmp_path)

    assert read_link("tool.lnk").target == str(exe)


def test_off_windows_every_shortcut_is_unreadable(tmp_path, monkeypatch):
    link = tmp_path / "tool.lnk"
    if sys.platform == "win32":
        make_link(link, target=make_exe(tmp_path / "tool.exe"))
        assert resolve_shortcut(link).target == str(tmp_path / "tool.exe")
    monkeypatch.setattr(shortcut.sys, "platform", "linux")

    assert code_of(lambda: read_link(link)) == UNREADABLE
    assert code_of(lambda: resolve_shortcut(link)) == UNREADABLE


# COM on the calling thread


@windows_only
def test_a_thread_with_no_com_reads_and_is_left_with_none(tmp_path):
    exe = make_exe(tmp_path / "tool.exe")
    link = make_link(tmp_path / "tool.lnk", target=exe)

    def read_then_probe():
        target = read_link(link).target
        again = co_initialize(COINIT_MULTITHREADED)
        co_uninitialize()
        return target, again

    assert on_thread(read_then_probe) == (str(exe), shortcut.S_OK)


@windows_only
@pytest.mark.parametrize("model", [shortcut.COINIT_APARTMENTTHREADED, COINIT_MULTITHREADED])
def test_a_thread_already_initialized_reads_and_keeps_its_com(tmp_path, model):
    exe = make_exe(tmp_path / "tool.exe")
    link = make_link(tmp_path / "tool.lnk", target=exe)

    def read_inside():
        assert co_initialize(model) == shortcut.S_OK
        try:
            target = read_link(link).target
            again = co_initialize(model)
            co_uninitialize()
            return target, again
        finally:
            co_uninitialize()

    assert on_thread(read_inside) == (str(exe), shortcut.S_FALSE)


@windows_only
def test_a_failed_read_also_leaves_the_thread_with_no_com(tmp_path):
    def fail_then_probe():
        code = code_of(lambda: read_link(tmp_path / "nothing.lnk"))
        again = co_initialize(COINIT_MULTITHREADED)
        co_uninitialize()
        return code, again

    assert on_thread(fail_then_probe) == (UNREADABLE, shortcut.S_OK)


# The pure part


def present(*paths):
    return lambda path: path in paths


@pytest.mark.parametrize("found, code", [
    (Shortcut(target=""), NO_TARGET),
    (Shortcut(target="   "), NO_TARGET),
    (Shortcut(target=None), NO_TARGET),
    (Shortcut(target="C:\\app\\start.bat"), NOT_EXE),
    (Shortcut(target="C:\\app\\tool.exe.txt"), NOT_EXE),
    (Shortcut(target="C:\\app\\gone.exe"), TARGET_MISSING),
    (Shortcut(target="C:\\app\\tool.exe"), None),
    (Shortcut(target="C:\\APP\\TOOL.EXE"), None),
])
def test_launch_problem_names_what_keeps_a_shortcut_from_launching(found, code):
    assert launch_problem(found, present("C:\\app\\tool.exe", "C:\\APP\\TOOL.EXE", "C:\\app\\start.bat")) == code


def test_resolve_gives_the_read_shortcut_when_it_can_be_launched():
    found = Shortcut(target="C:\\app\\tool.exe", arguments=("-a",), working_dir="C:\\app")

    assert resolve_shortcut("x.lnk", read=lambda path: found, is_file=present("C:\\app\\tool.exe")) == found


@pytest.mark.parametrize("found, code", [
    (Shortcut(target=""), NO_TARGET),
    (Shortcut(target="C:\\app\\start.cmd"), NOT_EXE),
    (Shortcut(target="C:\\app\\gone.exe"), TARGET_MISSING),
])
def test_resolve_fails_with_the_launch_problem(found, code):
    assert code_of(lambda: resolve_shortcut("x.lnk", read=lambda path: found, is_file=present())) == code


def raise_(error):
    def read(path):
        raise error
    return read


@pytest.mark.parametrize("read", [
    raise_(OSError("boom")),
    raise_(RuntimeError("boom")),
    raise_(ShortcutError("SOMETHING_ELSE")),
    lambda path: None,
    lambda path: ("C:\\app\\tool.exe", "", ""),
])
def test_anything_a_reader_raises_or_returns_wrongly_is_unreadable(read):
    assert code_of(lambda: resolve_shortcut("x.lnk", read=read, is_file=present("C:\\app\\tool.exe"))) == UNREADABLE


def test_a_reader_code_is_kept():
    assert code_of(lambda: resolve_shortcut("x.lnk", read=raise_(ShortcutError(NO_TARGET)))) == NO_TARGET


def test_launch_args_pass_each_argument_as_one_token_and_the_folder_when_there_is_one():
    found = Shortcut(target="C:\\앱\\tool.exe", arguments=("-site=KR", "a b", "--mode", ""), working_dir="C:\\앱 폴더")

    assert launch_args(found) == [
        "C:\\앱\\tool.exe", "--arg=-site=KR", "--arg=a b", "--arg=--mode", "--arg=", "--cwd", "C:\\앱 폴더",
    ]
    assert launch_args(Shortcut(target="C:\\tool.exe")) == ["C:\\tool.exe"]


@pytest.mark.parametrize("path, expected", [
    ("C:\\links\\app.lnk", True),
    ("C:\\links\\APP.LNK", True),
    ("app.Lnk", True),
    ("C:\\app\\tool.exe", False),
    ("C:\\links\\app.lnk.exe", False),
    ("Notepad", False),
])
def test_a_shortcut_is_named_by_its_lnk_ending_in_any_case(path, expected):
    assert is_shortcut(path) is expected


@windows_only
@pytest.mark.parametrize("text, expected", [
    ("", []),
    ("   ", []),
    ("a b", ["a", "b"]),
    ('"a b" c', ["a b", "c"]),
    ('a\\\\"b c"', ['a\\b c']),
    ('a\\"b', ['a"b']),
    ("C:\\dir\\ x", ["C:\\dir\\", "x"]),
])
def test_split_arguments_follows_command_line_to_argv(text, expected):
    assert split_arguments(text) == expected


def test_every_code_has_a_fixed_message_with_no_path_in_it():
    assert set(MESSAGES) == set(CODES)
    for code in CODES:
        assert ShortcutError(code).message == MESSAGES[code]
        assert str(ShortcutError(code)) == code
        assert "\\" not in MESSAGES[code] and "%" not in MESSAGES[code]
