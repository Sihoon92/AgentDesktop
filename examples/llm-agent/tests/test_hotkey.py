import queue

import pytest

from agent.hotkey import (MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, VK_F1, WM_HOTKEY, WM_QUIT,
                          HotkeyThread, parse_hotkey)


@pytest.mark.parametrize("text, expected", [
    ("ctrl+alt+f9", (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_F1 + 8)),
    ("Ctrl+Shift+A", (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("A"))),
    ("win+alt+0", (MOD_WIN | MOD_ALT | MOD_NOREPEAT, ord("0"))),
    (" ctrl + f24 ", (MOD_CONTROL | MOD_NOREPEAT, VK_F1 + 23))])
def test_parse_valid(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize("text", ["f9", "ctrl+alt", "ctrl+alt+printscreen", "", "ctrl+f25", "ctrl+a+b", "ctrl+f0"])
def test_parse_invalid(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


class FakeUser32:
    """RegisterHotKey fails for the ids in `taken`; GetMessageW serves `messages` until WM_QUIT."""

    def __init__(self, taken=()):
        self.taken = set(taken)
        self.registered: list[tuple] = []
        self.unregistered: list[int] = []
        self.messages: queue.Queue = queue.Queue()

    def RegisterHotKey(self, hwnd, number, modifiers, key):
        if number in self.taken:
            return 0
        self.registered.append((number, modifiers, key))
        return 1

    def UnregisterHotKey(self, hwnd, number):
        self.unregistered.append(number)
        return 1

    def GetMessageW(self, message_ref, hwnd, low, high):
        message, wparam = self.messages.get(timeout=5)
        if message == WM_QUIT:
            return 0
        message_ref._obj.message = message
        message_ref._obj.wParam = wparam
        return 1

    def PostThreadMessageW(self, thread_id, message, wparam, lparam):
        self.messages.put((message, wparam))
        return 1


def test_taken_hotkey_is_reported():
    user32 = FakeUser32(taken={2})
    thread = HotkeyThread({"capture": "ctrl+alt+f9", "end": "ctrl+alt+f10"}, lambda name: None, user32=user32)
    assert thread.start() == ("end",)
    thread.stop()
    assert [number for number, _m, _k in user32.registered] == [1] and user32.unregistered == [1]


def test_hotkey_message_calls_on_signal_with_its_name():
    user32 = FakeUser32()
    got = []
    thread = HotkeyThread({"capture": "ctrl+alt+f9", "end": "ctrl+alt+f10"}, got.append, user32=user32)
    assert thread.start() == ()
    user32.messages.put((WM_HOTKEY, 2))
    user32.messages.put((WM_HOTKEY, 1))
    user32.messages.put((WM_HOTKEY, 7))
    thread.stop()
    assert got == ["end", "capture"]
    assert sorted(user32.unregistered) == [1, 2]


def test_unparseable_binding_counts_as_failed():
    user32 = FakeUser32()
    thread = HotkeyThread({"capture": "printscreen"}, lambda name: None, user32=user32)
    assert thread.start() == ("capture",)
    thread.stop()
    assert user32.registered == []


def test_other_messages_are_not_signals():
    user32 = FakeUser32()
    got = []
    thread = HotkeyThread({"capture": "ctrl+alt+f9"}, got.append, user32=user32)
    thread.start()
    user32.messages.put((0x0100, 1))
    thread.stop()
    assert got == []


def test_start_waits_for_registration():
    import time

    class Slow(FakeUser32):
        def RegisterHotKey(self, hwnd, number, modifiers, key):
            time.sleep(0.2)
            return 1

    user32 = Slow()
    thread = HotkeyThread({"capture": "ctrl+alt+f9", "end": "ctrl+alt+f10"}, lambda name: None, user32=user32)
    assert thread.start() == ()
    thread.stop()
