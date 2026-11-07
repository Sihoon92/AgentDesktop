from agent.verify import first_unmet

PANEL_OPEN = [
    {"role": "switch", "description_contains": "설정 패널", "states": ["checked"]},
    {"role": "statictext", "name": "지표"},
]


def test_real_open_panel_satisfies_requirements(fixture_json):
    tree = fixture_json("snapshot_settings_open")["data"]["tree"]

    assert first_unmet(PANEL_OPEN, tree) is None


def test_real_closed_panel_fails_on_the_unchecked_switch(fixture_json):
    tree = fixture_json("snapshot_settings_closed")["data"]["tree"]

    assert first_unmet(PANEL_OPEN, tree) == PANEL_OPEN[0]


def test_name_contains_must_match():
    tree = {"role": "button", "name": "저장하기"}

    assert first_unmet([{"role": "button", "name_contains": "저장"}], tree) is None
    assert first_unmet([{"role": "button", "name_contains": "삭제"}], tree) is not None
