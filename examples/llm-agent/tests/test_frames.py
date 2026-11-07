from agent.frames import frame_origin, to_image_rect

NORMAL_WINDOW = {"x": 1920, "y": 0, "width": 1916, "height": 2070}
NORMAL_IMAGE = (1896, 2060)


def test_origin_of_a_normal_window_at_175_percent():
    assert frame_origin(NORMAL_WINDOW, 1896, 2060) == (1930, 0)


def test_settings_switch_lands_on_the_measured_spot():
    origin = frame_origin(NORMAL_WINDOW, *NORMAL_IMAGE)
    switch = {"x": 3705, "y": 118, "width": 108, "height": 45}

    assert to_image_rect(switch, origin, NORMAL_IMAGE) == {
        "x": 1775,
        "y": 118,
        "width": 108,
        "height": 45,
    }


def test_origin_of_a_maximized_window():
    window = {"x": -12, "y": -12, "width": 3864, "height": 2100}

    assert frame_origin(window, 3840, 2076) == (0, 0)


def test_window_on_the_left_monitor_has_negative_origin():
    window = {"x": -2570, "y": 0, "width": 1300, "height": 900}

    assert frame_origin(window, 1280, 890) == (-2560, 0)


def test_element_outside_the_image_is_dropped():
    origin = (-2560, 0)
    image = (1280, 890)
    right_of_image = {"x": -1200, "y": 10, "width": 50, "height": 20}
    above_image = {"x": -2500, "y": -40, "width": 50, "height": 20}
    touching_left_edge = {"x": -2600, "y": 10, "width": 40, "height": 20}

    assert to_image_rect(right_of_image, origin, image) is None
    assert to_image_rect(above_image, origin, image) is None
    assert to_image_rect(touching_left_edge, origin, image) is None


def test_element_crossing_the_edge_is_clipped():
    origin = (-2560, 0)
    image = (1280, 890)
    crossing_left = {"x": -2580, "y": 100, "width": 50, "height": 20}
    crossing_bottom_right = {"x": -1300, "y": 880, "width": 40, "height": 30}

    assert to_image_rect(crossing_left, origin, image) == {
        "x": 0,
        "y": 100,
        "width": 30,
        "height": 20,
    }
    assert to_image_rect(crossing_bottom_right, origin, image) == {
        "x": 1260,
        "y": 880,
        "width": 20,
        "height": 10,
    }


def test_zero_size_element_is_dropped():
    origin = frame_origin(NORMAL_WINDOW, *NORMAL_IMAGE)

    assert to_image_rect({"x": 2000, "y": 100, "width": 0, "height": 20}, origin, NORMAL_IMAGE) is None
    assert to_image_rect({"x": 2000, "y": 100, "width": 30, "height": 0}, origin, NORMAL_IMAGE) is None


def test_window_on_the_125_percent_monitor_measured_2026_11_07():
    window = {"x": -2400, "y": 40, "width": 2000, "height": 1300}
    origin = frame_origin(window, 1984, 1292)
    switch = {"x": -494, "y": 127, "width": 77, "height": 30}

    assert origin == (-2392, 40)
    assert to_image_rect(switch, origin, (1984, 1292)) == {"x": 1898, "y": 87, "width": 77, "height": 30}
