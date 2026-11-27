from agent.float_button import (GWL_EXSTYLE, NO_ACTIVATE_STYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
                                FloatButton, apply_no_activate)


class FakeUser32:
    def __init__(self, style):
        self.style = style
        self.set_calls: list[tuple] = []

    def GetWindowLongPtrW(self, hwnd, index):
        assert index == GWL_EXSTYLE
        return self.style

    def SetWindowLongPtrW(self, hwnd, index, value):
        self.set_calls.append((hwnd, index, value))
        return self.style


def test_no_activate_ors_the_style():
    user32 = FakeUser32(0x100)
    apply_no_activate(77, user32)
    assert user32.set_calls == [(77, GWL_EXSTYLE, 0x100 | NO_ACTIVATE_STYLE)]
    assert NO_ACTIVATE_STYLE == WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW == 0x08000088


def test_updates_before_start_are_queued_without_a_window():
    button = FloatButton(lambda: None, lambda: None)
    button.set_count(3)
    button.set_busy(True)
    button.close()
    button.run()
    assert button._updates.qsize() == 3
    assert isinstance(FloatButton.available(), bool)
