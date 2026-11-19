"""A native id that is only a window handle of the snapshot's process is not an id: it changes every
time the app starts. without_handle_ids drops such ids from a tree; process_window_handles asks
Win32 for the handles."""

import copy
import os
import sys

import pytest

from agent import handles
from agent.handles import process_window_handles, window_number, without_handle_ids


def node(role, name=None, native=None, ref=None, children=None):
    out = {"role": role}
    if name is not None:
        out["name"] = name
    if native is not None:
        out["native_id"] = native
    if ref is not None:
        out["ref"] = ref
    if children is not None:
        out["children"] = children
    return out


def auto(value):
    return {"kind": "automation_id", "value": value}


def scene():
    return node("window", "Main", auto("175311000"), children=[
        node("group", native=auto("175311172"), children=[
            node("button", "Query", auto("query-button"), ref="@s1:e1"),
            node("group", native="175311999", children=[
                node("pane", children=[node("image", native=auto("175312000"), ref="@s1:e2")]),
            ]),
        ]),
        node("menuitem", native=auto("8001"), ref="@s1:e3"),
        node("image", native=auto("56")),
    ])


HANDLES = frozenset({175311000, 175311172, 175311999, 175312000})


def test_handle_ids_are_dropped_and_everything_else_stays():
    tree = scene()

    out = without_handle_ids(tree, HANDLES)

    expected = scene()
    del expected["native_id"]
    del expected["children"][0]["native_id"]
    del expected["children"][0]["children"][1]["native_id"]
    del expected["children"][0]["children"][1]["children"][0]["children"][0]["native_id"]
    assert out == expected
    assert list(out["children"][0]["children"][0]) == ["role", "name", "native_id", "ref"]
    assert [child["role"] for child in out["children"]] == ["group", "menuitem", "image"]


def test_the_given_tree_is_not_changed():
    tree = scene()

    without_handle_ids(tree, HANDLES)

    assert tree == scene()


@pytest.mark.parametrize("native", [auto("8001"), auto("56"), "56", auto("query-button"), auto("0175311172"),
                                    auto(" 175311172"), auto("175311172 "), auto("+175311172"), auto(""), auto(None),
                                    175311172, {"kind": "automation_id"}])
def test_ids_that_are_not_the_exact_text_of_a_handle_stay(native):
    tree = node("window", children=[node("group", native=native, ref="@s1:e1")])

    assert without_handle_ids(tree, HANDLES) == tree


def test_no_handles_leave_the_tree_as_it_is():
    assert without_handle_ids(scene(), frozenset()) == scene()


def test_a_deep_tree_does_not_reach_the_recursion_limit():
    tree = node("group", native=auto("175311172"))
    for _ in range(sys.getrecursionlimit() + 100):
        tree = {"role": "group", "children": [tree]}

    out = without_handle_ids(tree, HANDLES)

    while out.get("children"):
        out = out["children"][0]
    assert out == {"role": "group"}


def test_odd_children_are_kept_as_they_are():
    tree = {"role": "window", "children": ["text", None, node("group", native=auto("175311172"))]}

    out = without_handle_ids(copy.deepcopy(tree), HANDLES)

    assert out == {"role": "window", "children": ["text", None, {"role": "group"}]}


@pytest.mark.parametrize("window_id, number", [("w-3412692", 3412692), ("w-1", 1)])
def test_a_window_id_names_its_handle(window_id, number):
    assert window_number(window_id) == number


@pytest.mark.parametrize("window_id", [None, "", "w-", "w-12a", "w--1", "W-12", "12", "w-+12", " w-12", "w-12 ",
                                       "w-１２", 12, ["w-12"]])
def test_other_window_ids_name_no_handle(window_id):
    assert window_number(window_id) is None


def test_off_windows_there_are_no_handles(monkeypatch):
    monkeypatch.setattr(handles.sys, "platform", "linux")
    monkeypatch.setattr(handles, "_win32_handles", lambda _hwnd: frozenset({12345}))

    assert process_window_handles(12345) == frozenset()


@pytest.mark.skipif(sys.platform != "win32", reason="asks Win32")
@pytest.mark.parametrize("hwnd", [0, 1, 0x7FFFFFF0])
def test_a_handle_that_is_no_window_has_no_handles(hwnd):
    assert process_window_handles(hwnd) == frozenset()


@pytest.mark.skipif(sys.platform != "win32", reason="asks Win32")
def test_a_window_of_this_process_is_among_its_handles():
    """A message-free hidden window of this test process: no window is shown."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    def owner(handle):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return pid.value

    top = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 10, 10, None, None, None, None)
    assert top
    child = user32.CreateWindowExW(0, "STATIC", None, 0x40000000, 0, 0, 5, 5, top, None, None, None)
    try:
        assert child
        found = process_window_handles(top)
        assert {top, child} <= found
        assert process_window_handles(child) == found
        assert all(isinstance(handle, int) for handle in found)
        assert {owner(handle) for handle in found} == {os.getpid()}
    finally:
        user32.DestroyWindow(top)
