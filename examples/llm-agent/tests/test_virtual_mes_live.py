"""Tests that open the real VirtualMes window. They run only with VIRTUAL_MES_LIVE=1."""

from virtual_mes_support import LiveApp, export, live, read_xlsx, requires_exe

pytestmark = [requires_exe, live]


def names(app, window, role=None):
    return [n["name"] for n in app.nodes(window) if n.get("name") and (role is None or n.get("role") == role)]


def test_menu_as_ref_has_named_buttons_with_refs(tmp_path):
    with LiveApp(tmp_path, "--expose", "menu=ref") as app:
        buttons = [n for n in app.nodes(app.main()) if n.get("role") == "button" and n.get("ref")]
        menu = [n["name"] for n in buttons if (n.get("id") or "").startswith("menu-")]
        assert menu == ["기준정보", "생산운영", "품질관리", "설비관리", "자재관리"]


def test_menu_as_text_has_text_without_refs(tmp_path):
    with LiveApp(tmp_path, "--expose", "menu=text") as app:
        node = next(n for n in app.nodes(app.main()) if n.get("name") == "생산운영")
        assert node["role"] == "statictext" and "ref" not in node and node.get("bounds")


def test_menu_as_pixel_shows_no_menu_text(tmp_path):
    with LiveApp(tmp_path, "--expose", "menu=pixel") as app:
        assert "생산운영" not in names(app, app.main())


def test_menu_opens_a_list_window_and_picks_a_screen(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        before = len(app.windows())
        app.click(app.find("생산운영", role="button")["ref"])
        app.wait_for_windows(before + 1)
        app.click(app.find("전공정 생산현황")["ref"])
        app.wait_for_event("screen_opened")
        app.wait_for_windows(before)
        events = app.events()
        assert events[0]["event"] == "app_started" and events[0]["profile"] == "easy"
        assert [e["event"] for e in events[1:]] == ["menu_opened", "screen_opened"]
        assert events[2]["screen"] == "front-process"
        assert [e["seq"] for e in events] == [1, 2, 3]


def test_similar_screen_name_opens_the_empty_screen(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("후공정 생산현황")["ref"])
        assert app.wait_for_event("screen_opened")["screen"] == "rear-process"


def test_list_window_closes_when_the_app_loses_focus(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        before = len(app.windows())
        app.focus(app.main())
        app.click(app.find("생산운영", role="button")["ref"])
        app.wait_for_windows(before + 1)
        app.focus_elsewhere()
        app.wait_for_windows(before)


def open_front(app):
    app.click(app.find("생산운영", role="button")["ref"])
    app.click(app.find("전공정 생산현황")["ref"])


def last(app, event):
    return [e for e in app.events() if e["event"] == event][-1]


def test_single_select_changes_on_item_click(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.click(app.by_id("filter-process")["ref"])
        app.click(app.find("권취")["ref"])
        changed = last(app, "filter_changed")
        assert (changed["filter"], changed["value"], changed["closed_by"]) == ("process", "권취", "item_click")


def test_period_enables_the_date_inputs(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        assert "disabled" in app.find("시작일").get("states", [])
        app.click(app.by_id("filter-period")["ref"])
        app.click(app.by_id("option-3")["ref"])
        assert "disabled" not in app.find("시작일").get("states", [])
        assert last(app, "filter_changed")["value"] == "기간"


def test_multi_select_applies_with_select_and_with_an_outside_click(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.click(app.by_id("filter-shop-3")["ref"])
        for code in ("WA0", "WA1", "WA3"):
            app.click(app.by_id(f"row-{code}")["ref"])
        app.click(app.by_id("multi-select")["ref"])
        assert last(app, "filter_changed")["value"] == ["WA2"]
        assert last(app, "filter_changed")["closed_by"] == "select_button"
        assert app.by_id("filter-shop-3")["name"] == "#울산 조립"
        app.click(app.by_id("filter-shop-3")["ref"])
        app.click(app.by_id("multi-all")["ref"])
        app.click_outside()
        assert last(app, "filter_changed")["value"] == ["WA0", "WA1", "WA2", "WA3"]
        assert last(app, "filter_changed")["closed_by"] == "outside_click"
        app.click(app.by_id("filter-shop-3")["ref"])
        for code in ("WA0", "WA1", "WA2", "WA3"):
            app.click(app.by_id(f"row-{code}")["ref"])
        app.click(app.by_id("multi-select")["ref"])
        assert app.by_id("filter-shop-3")["name"] == "선택 없음"


def test_disabled_shop_boxes_do_not_open(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("권취 Roller 점검일시")["ref"])
        assert "disabled" in app.by_id("filter-shop-1").get("states", [])
        assert "disabled" not in app.by_id("filter-shop-3").get("states", [])


def test_query_fills_the_grid_and_logs_the_filters(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "300") as app:
        open_front(app)
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        started, finished = last(app, "query_started"), last(app, "query_finished")
        assert started["started_by"] == "button" and started["delay_ms"] == 300
        assert started["filters"]["period"] == "전일" and finished["rows"] == 16
        assert app.find("조회 완료 · 16건")
        assert app.by_id("cell-1-0")["name"] == "2026-11-11"


def test_loading_window_is_visible_while_waiting(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "6000") as app:
        open_front(app)
        before = len(app.windows())
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_started")
        app.press("f5")
        assert len(app.windows()) == before + 1
        assert app.find("데이터를 조회하고 있습니다")
        app.wait_for_event("query_finished")
        assert len([e for e in app.events() if e["event"] == "query_started"]) == 1
        app.wait_for_windows(before)


def test_f5_starts_a_query(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.press("f5")
        app.wait_for_event("query_finished")
        assert last(app, "query_started")["started_by"] == "f5"


def test_default_delays_repeat_with_the_same_seed(tmp_path):
    delays = []
    for run in ("a", "b"):
        with LiveApp(tmp_path / run, "--profile", "easy") as app:
            open_front(app)
            for nth in (1, 2):
                app.click(app.by_id("query-button")["ref"])
                app.wait_for_event("query_finished", count=nth)
            delays.append([e["delay_ms"] for e in app.events() if e["event"] == "query_started"])
    assert delays[0] == delays[1] and all(1000 <= d <= 4000 for d in delays[0])


def test_custom_period_without_dates_is_rejected(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("filter-period")["ref"])
        app.click(app.by_id("option-3")["ref"])
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_rejected")
        assert not [e for e in app.events() if e["event"] == "query_started"]


def query(app):
    app.click(app.by_id("query-button")["ref"])
    app.wait_for_event("query_finished")


def test_icon_save_writes_the_same_cells_as_the_headless_export(tmp_path):
    target = tmp_path / "saved.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        app.click(app.by_id("excel-icon")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "choice-save", "the save choice")["ref"])
        app.save_dialog(target)
        app.wait_for_event("export_saved")
        saved = last(app, "export_saved")
        assert saved["path"] == str(target) and saved["source"] == "panel" and saved["rows"] == 16
        choice = last(app, "export_choice")
        assert (choice["route"], choice["choice"]) == ("icon", "save")
    expected = tmp_path / "expected.xlsx"
    assert export("front-process", {}, expected).returncode == 0
    assert read_xlsx(target) == read_xlsx(expected)


def test_icon_before_a_query_reports_no_data(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.click(app.by_id("excel-icon")["ref"])
        app.wait_for_event("export_rejected")
        assert last(app, "export_rejected")["reason"] == "no_data"
        assert app.wait_for_node(lambda n: n.get("name") == "조회된 데이터가 없습니다", "the no-data notice")


def test_icon_cancel_saves_nothing(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        app.click(app.by_id("excel-icon")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "choice-cancel", "the cancel choice")["ref"])
        app.wait_for_event("export_choice")
        assert last(app, "export_choice")["choice"] == "cancel"
        assert not [e for e in app.events() if e["event"] == "export_saved"]


def test_unwritable_target_is_reported_and_not_logged_as_saved(tmp_path):
    target = tmp_path / "locked.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app, open(target, "wb"):
        open_front(app)
        query(app)
        app.click(app.by_id("excel-icon")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "choice-save", "the save choice")["ref"])
        app.save_dialog(target, confirm_overwrite=True)
        app.wait_for_event("export_failed")
        assert not [e for e in app.events() if e["event"] == "export_saved"]


def test_screens_without_the_icon_do_not_have_it(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("권취 Roller 점검일시")["ref"])
        assert not [n for n in app.all_nodes() if n.get("id") == "excel-icon"]


def open_roller(app):
    app.click(app.find("생산운영", role="button")["ref"])
    app.click(app.find("권취 Roller 점검일시")["ref"])


def test_radial_export_saves_through_two_stages(tmp_path):
    target = tmp_path / "radial.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_roller(app)
        query(app)
        app.right_click(app.by_id("cell-1-0")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "radial-export", "the export slice")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "radial-excel", "the excel slice")["ref"])
        app.save_dialog(target)
        app.wait_for_event("export_saved")
        assert [e["stage"] for e in app.events() if e["event"] == "radial_opened"] == [1, 2]
        choice = last(app, "export_choice")
        assert (choice["route"], choice["choice"]) == ("radial", "excel")
        assert last(app, "export_saved")["source"] == "panel"
    expected = tmp_path / "expected.xlsx"
    assert export("roller-check", {}, expected).returncode == 0
    assert read_xlsx(target) == read_xlsx(expected)


def test_radial_in_pixel_mode_exposes_a_window_but_no_labels(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--expose", "radial=pixel", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        before = {w["id"] for w in app.windows()}
        app.right_click(app.by_id("cell-1-0")["ref"])
        app.wait_for_event("radial_opened")
        new = [w for w in app.windows() if w["id"] not in before]
        assert len(new) == 1
        assert not [n for n in app.nodes(new[0]["id"]) if n.get("name") in ("export", "excel")]


def test_radial_in_text_mode_exposes_labels_without_refs(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--expose", "radial=text", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        app.right_click(app.by_id("cell-1-0")["ref"])
        label = app.wait_for_node(lambda n: n.get("name") == "export", "the export label")
        assert label["role"] == "statictext" and "ref" not in label


def test_radial_closes_on_escape(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        app.right_click(app.by_id("cell-1-0")["ref"])
        app.wait_for_event("radial_opened")
        app.press("escape")
        app.wait_for_event("radial_closed")
        assert last(app, "radial_closed")["reason"] == "escape"


def test_radial_pdf_only_records_the_choice(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        app.right_click(app.by_id("cell-1-0")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "radial-export", "the export slice")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "radial-pdf", "the pdf slice")["ref"])
        app.wait_for_event("radial_closed")
        assert last(app, "export_choice")["choice"] == "pdf"
        assert not [e for e in app.events() if e["event"] == "export_saved"]


def open_defects(app):
    app.click(app.find("생산운영", role="button")["ref"])
    app.click(app.find("공정별 불량현황")["ref"])


def test_cell_popup_exports_its_own_table(tmp_path):
    target = tmp_path / "detail.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_defects(app)
        query(app)
        app.click(app.by_id("cell-3-2")["ref"])
        opened = app.wait_for_event("cell_popup_opened")
        assert opened["row"] == "조립" and opened["column"] == "불량수"
        popup = next(w for w in app.windows() if w["title"].startswith("불량 상세"))
        assert popup["title"] == "불량 상세 · 조립 · 불량수"
        detail_cell = next(n for n in app.nodes(popup["id"]) if n.get("id") == "cell-1-0")
        app.right_click(detail_cell["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "radial-export", "the export slice")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "radial-excel", "the excel slice")["ref"])
        app.save_dialog(target)
        app.wait_for_event("export_saved")
        assert last(app, "export_saved")["source"] == "cell_popup"
    expected = tmp_path / "expected.xlsx"
    assert export("defect-detail", {"row": "조립"}, expected).returncode == 0
    assert read_xlsx(target) == read_xlsx(expected)


def test_only_number_cells_open_the_popup(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_defects(app)
        query(app)
        app.click(app.by_id("cell-1-0")["ref"])
        app.click(app.by_id("cell-1-1")["ref"])
        app.wait_for_event("cell_popup_opened")
        opened = [e for e in app.events() if e["event"] == "cell_popup_opened"]
        assert [(e["row"], e["column"]) for e in opened] == [("믹싱", "생산수")]


def test_popup_closes_when_the_screen_changes(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_defects(app)
        query(app)
        app.click(app.by_id("cell-2-1")["ref"])
        app.wait_for_event("cell_popup_opened")
        open_front(app)
        assert not [w for w in app.windows() if w["title"].startswith("불량 상세")]


def test_mes_like_profile_matches_what_the_user_saw(tmp_path):
    with LiveApp(tmp_path, "--delay-ms", "200") as app:
        main = app.main()
        menu = next(n for n in app.nodes(main) if n.get("name") == "생산운영")
        assert menu.get("ref")
        app.click(menu["ref"])
        app.click(app.find("전공정 생산현황")["ref"])
        nodes = app.nodes(main)
        for text in ("Shop", "공정", "제품", "기간", "전일"):
            node = next(n for n in nodes if n.get("name") == text)
            assert "ref" not in node and node.get("bounds"), text
        assert not [n for n in nodes if n.get("name") == "엑셀 저장"]
        assert app.find("조회(F5)", role="button").get("ref")
        app.focus(main)
        app.click_at_center(next(n for n in nodes if n.get("name") == "전일"))
        app.wait_for_node(lambda n: n.get("name") == "전주", "the open period list")
        places = {(n["bounds"]["x"], n["bounds"]["y"]) for n in app.all_nodes() if n.get("name") == "기간"}
        assert len(places) == 2, places


def test_mes_like_radial_menu_shows_no_labels(tmp_path):
    with LiveApp(tmp_path, "--delay-ms", "200") as app:
        main = app.main()
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("전공정 생산현황")["ref"])
        query(app)
        cell = next(n for n in app.nodes(main) if n.get("name") == "2026-11-11")
        assert "ref" not in cell
        app.focus(main)
        bounds = cell["bounds"]
        app.cli("--headed", "mouse-click", "--button", "right",
                f"--xy={int(bounds['x'] + bounds['width'] / 2)},{int(bounds['y'] + bounds['height'] / 2)}")
        app.wait_for_event("radial_opened")
        assert not [n for n in app.all_nodes() if n.get("name") in ("export", "excel")]


def test_hard_profile_hides_filter_and_grid_text(tmp_path):
    with LiveApp(tmp_path, "--profile", "hard") as app:
        main = app.main()
        menu = next(n for n in app.nodes(main) if n.get("name") == "생산운영")
        assert "ref" not in menu
        app.focus(main)
        app.click_at_center(menu)
        app.click_at_center(app.wait_for_node(lambda n: n.get("name") == "전공정 생산현황", "the list entry"))
        app.wait_for_event("screen_opened")
        names = {n.get("name") for n in app.nodes(main)}
        assert not names & {"Shop", "공정", "제품", "전일", "전공정 생산현황"}
        assert "조회(F5)" in names


def test_coordinate_click_inside_a_popup_chooses_the_entry(tmp_path):
    with LiveApp(tmp_path, "--expose", "radial=text", "--delay-ms", "200") as app:
        main = app.main()
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("전공정 생산현황")["ref"])
        query(app)
        cell = next(n for n in app.nodes(main) if n.get("name") == "2026-11-11")
        bounds = cell["bounds"]
        app.focus(main)
        app.cli("--headed", "mouse-click", "--button", "right",
                f"--xy={int(bounds['x'] + bounds['width'] / 2)},{int(bounds['y'] + bounds['height'] / 2)}")
        app.click_at_center(app.wait_for_node(lambda n: n.get("name") == "export", "the export label"))
        app.click_at_center(app.wait_for_node(lambda n: n.get("name") == "pdf", "the pdf label"))
        app.wait_for_event("radial_closed")
        assert last(app, "radial_closed")["reason"] == "choice"
        assert last(app, "export_choice")["choice"] == "pdf"


def events_named(app, name):
    return [e for e in app.events() if e["event"] == name]


def test_outside_click_does_not_reach_the_control_underneath(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        cell = app.by_id("cell-12-5")
        app.click(app.by_id("filter-shop-3")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "row-WA0", "the WA0 row")["ref"])
        app.right_click_at_center(cell)
        app.wait_for_event("filter_changed")
        assert last(app, "filter_changed")["closed_by"] == "outside_click"
        app.wait_for_windows(1)
        assert not events_named(app, "radial_opened")


def test_click_after_an_outside_click_is_not_lost(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.click(app.by_id("filter-period")["ref"])
        app.wait_for_windows(2)
        app.click_outside()
        app.wait_for_windows(1)
        app.click(app.by_id("filter-period")["ref"])
        app.click_at_center(app.wait_for_node(lambda n: n.get("name") == "전주", "the 전주 entry"))
        assert app.wait_for_event("filter_changed")["value"] == "전주"


def test_refs_do_not_act_while_the_loading_window_is_up(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "5000") as app:
        open_front(app)
        menu = app.find("생산운영", role="button")["ref"]
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_started")
        try:
            app.click(menu)
        except AssertionError:
            pass
        app.wait_for_event("query_finished")
        assert len(events_named(app, "menu_opened")) == 1
        assert len(events_named(app, "screen_opened")) == 1


def test_cell_popup_uses_the_filters_the_table_was_queried_with(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_defects(app)
        query(app)
        app.click(app.by_id("filter-period")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "option-3", "the 기간 entry")["ref"])
        app.wait_for_event("filter_changed")
        app.click(app.by_id("cell-3-2")["ref"])
        assert app.wait_for_event("cell_popup_opened")["row"] == "조립"


def test_export_records_the_filters_the_table_was_queried_with(tmp_path):
    target = tmp_path / "saved.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        app.click(app.by_id("filter-process")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("name") == "권취", "the 권취 entry")["ref"])
        app.wait_for_event("filter_changed")
        app.click(app.by_id("excel-icon")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "choice-save", "the save choice")["ref"])
        app.save_dialog(target)
        saved = app.wait_for_event("export_saved")
        assert saved["filters"]["process"] == "전체" and saved["rows"] == 16


def test_cells_scrolled_out_of_view_are_reported_offscreen(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("filter-period")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("name") == "전주", "the 전주 entry")["ref"])
        app.wait_for_event("filter_changed")
        query(app)
        assert last(app, "query_finished")["rows"] == 112
        assert "offscreen" not in app.by_id("cell-1-0").get("states", [])
        assert "offscreen" in app.by_id("cell-110-0").get("states", [])


def test_closing_waits_for_a_running_query(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "4000") as app:
        open_front(app)
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_started")
        app.request_close()
        app.wait_for_event("query_finished")
        assert app.is_running()


def test_multi_select_applies_when_another_app_takes_focus(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.focus(app.main())
        app.click(app.by_id("filter-shop-3")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "row-WA0", "the WA0 row")["ref"])
        app.focus_elsewhere()
        changed = app.wait_for_event("filter_changed")
        assert changed["value"] == ["WA1", "WA2", "WA3"] and changed["closed_by"] == "deactivated"
        app.wait_for_windows(1)


def test_radial_closes_when_another_app_takes_focus(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        query(app)
        app.right_click(app.by_id("cell-1-0")["ref"])
        app.wait_for_event("radial_opened")
        app.focus_elsewhere()
        assert app.wait_for_event("radial_closed")["reason"] == "deactivated"


def test_typed_dates_filter_the_query(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("filter-period")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "option-3", "the 기간 entry")["ref"])
        app.wait_for_event("filter_changed")
        app.cli("set-value", app.find("시작일")["ref"], "2026-11-03")
        app.cli("set-value", app.find("종료일")["ref"], "2026-11-04")
        query(app)
        started = last(app, "query_started")
        assert (started["filters"]["from"], started["filters"]["to"]) == ("2026-11-03", "2026-11-04")
        assert last(app, "query_finished")["rows"] == 32
        typed = [e for e in events_named(app, "filter_changed") if e["closed_by"] == "typed"]
        assert [(e["filter"], e["value"]) for e in typed] == [("from", "2026-11-03"), ("to", "2026-11-04")]


def test_empty_selection_queries_and_saves_zero_rows(tmp_path):
    target = tmp_path / "empty.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("filter-shop-3")["ref"])
        for code in ("WA0", "WA1", "WA2", "WA3"):
            app.click(app.wait_for_node(lambda n, c=code: n.get("id") == f"row-{c}", "a shop row")["ref"])
        app.click(app.by_id("multi-select")["ref"])
        query(app)
        assert last(app, "query_finished")["rows"] == 0
        app.click(app.by_id("excel-icon")["ref"])
        app.click(app.wait_for_node(lambda n: n.get("id") == "choice-save", "the save choice")["ref"])
        app.save_dialog(target)
        assert app.wait_for_event("export_saved")["rows"] == 0
    assert read_xlsx(target) == [["일자", "Shop", "공정", "제품", "생산수", "불량수", "불량률"]]


def combo_events(app):
    return [e["event"] for e in app.events() if e["event"].startswith("combo_")]


def tree_node(app, window, automation_id):
    """The snapshot node with this automation id, children included."""
    def visit(node):
        if (node.get("native_id") or {}).get("value") == automation_id:
            return node
        for child in node.get("children") or []:
            found = visit(child)
            if found is not None:
                return found
        return None

    return visit(app.snapshot(window)["tree"])


def right_end(bounds):
    """A point inside the box's arrow square: the right end, one box height wide."""
    return int(bounds["x"] + bounds["width"] - bounds["height"] / 2), int(bounds["y"] + bounds["height"] / 2)


def test_arrow_mode_body_click_shows_no_list(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--combo-open", "arrow") as app:
        open_front(app)
        app.wait_for_event("screen_opened")
        before = len(app.windows())
        app.click(app.by_id("filter-shop-3")["ref"])
        body = app.wait_for_event("combo_body_clicked")
        assert (body["screen"], body["filter"]) == ("front-process", "shop-3")
        assert len(app.windows()) == before
        assert not [w for w in app.windows() if w["title"] == "목록"]
        assert combo_events(app) == ["combo_body_clicked"]


def test_arrow_mode_arrow_click_opens_the_list(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--combo-open", "arrow") as app:
        open_front(app)
        app.wait_for_event("screen_opened")
        before = len(app.windows())
        app.click(app.by_id("filter-shop-3-arrow")["ref"])
        arrow = app.wait_for_event("combo_arrow_clicked")
        assert (arrow["screen"], arrow["filter"]) == ("front-process", "shop-3")
        assert [w["title"] for w in app.wait_for_windows(before + 1)].count("목록") == 1
        assert combo_events(app) == ["combo_arrow_clicked"]


def test_arrow_mode_text_filters_open_only_at_the_right_end(tmp_path):
    with LiveApp(tmp_path, "--combo-open", "arrow", "--delay-ms", "200") as app:
        open_front(app)
        app.wait_for_event("screen_opened")
        before = len(app.windows())
        period = next(n for n in app.nodes(app.main()) if n.get("name") == "전일")
        app.click_at_center(period)
        app.wait_for_event("combo_body_clicked")
        assert len(app.windows()) == before
        app.focus(app.main())
        x, y = right_end(period["bounds"])
        app.cli("--headed", "mouse-click", f"--xy={x},{y}")
        assert app.wait_for_event("combo_arrow_clicked")["filter"] == "period"
        app.wait_for_node(lambda n: n.get("name") == "전주", "the open period list")


def test_arrow_is_a_child_button_only_in_ref_mode(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--combo-open", "arrow") as app:
        open_front(app)
        app.wait_for_event("screen_opened")
        box = tree_node(app, app.main(), "filter-shop-3")
        children = [child for child in box.get("children") or []
                    if (child.get("native_id") or {}).get("value") == "filter-shop-3-arrow"]
        assert len(children) == 1
        assert children[0]["role"] == "button" and children[0]["name"] == "열기" and children[0].get("ref_id")
        arrow, body = children[0]["bounds"], box["bounds"]
        assert arrow["x"] + arrow["width"] == body["x"] + body["width"] and arrow["width"] == body["height"]
    for options in (("--combo-open", "arrow"), ("--expose", "filters=pixel", "--combo-open", "arrow")):
        with LiveApp(tmp_path / options[1].replace("=", "-"), *options) as app:
            open_front(app)
            app.wait_for_event("screen_opened")
            assert not [n for n in app.all_nodes() if n.get("name") == "열기"]


def test_arrow_mode_disabled_boxes_do_not_react(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--combo-open", "arrow") as app:
        open_roller(app)
        app.wait_for_event("screen_opened")
        assert "disabled" in app.by_id("filter-shop-1-arrow").get("states", [])
        before = len(app.windows())
        app.click_at_center(app.by_id("filter-shop-1"))
        x, y = right_end(app.by_id("filter-shop-1")["bounds"])
        app.cli("--headed", "mouse-click", f"--xy={x},{y}")
        assert len(app.windows()) == before and combo_events(app) == []


def test_anywhere_mode_still_opens_on_a_body_click(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.wait_for_event("screen_opened")
        assert app.events()[0]["combo_open"] == "anywhere"
        before = len(app.windows())
        app.click(app.by_id("filter-shop-3")["ref"])
        app.wait_for_windows(before + 1)
        assert not [n for n in app.all_nodes() if n.get("id") == "filter-shop-3-arrow"]
        assert combo_events(app) == []


def open_summary(app):
    app.click(app.find("생산운영", role="button")["ref"])
    app.click(app.find("공정별 생산집계")["ref"])
    app.wait_for_event("screen_opened")


def test_menu_lists_six_screens(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        before = len(app.windows())
        app.click(app.find("생산운영", role="button")["ref"])
        listing = next(w for w in app.wait_for_windows(before + 1) if w["title"] == "메뉴 목록")
        entries = [n["name"] for n in app.nodes(listing["id"]) if (n.get("id") or "").startswith("screen-")]
        assert entries == ["전공정 생산현황", "후공정 생산현황", "공정별 불량현황", "권취 Roller 점검일시",
                           "설비 가동현황", "공정별 생산집계"]


def test_grouped_header_cells_span_their_columns(tmp_path):
    with LiveApp(tmp_path, "--delay-ms", "200") as app:
        open_summary(app)
        query(app)
        nodes = app.nodes(app.main())
        def text(name):
            return [n for n in nodes if n.get("name") == name and n.get("role") == "statictext"]
        production = text("생산")[0]["bounds"]
        work, yield_rate = text("작업수")[0]["bounds"], text("수율(Loss제외)")[0]["bounds"]
        assert production["x"] <= work["x"] and production["x"] + production["width"] >= yield_rate["x"] + yield_rate["width"]
        assert production["y"] + production["height"] <= work["y"]
        plans = sorted(text("계획"), key=lambda n: n["bounds"]["width"])
        assert len(plans) == 2 and plans[0]["bounds"]["y"] > plans[1]["bounds"]["y"]
        shop = text("SHOP")[0]["bounds"]
        assert shop["height"] >= 2 * work["height"]


def test_clicking_a_number_cell_logs_row_and_column_and_opens_the_popup(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_summary(app)
        query(app)
        before = len(app.windows())
        result = app.events()
        table = [n for n in app.nodes(app.main()) if (n.get("id") or "").startswith("cell-")]
        row = next(n for n in table if n.get("name") == "극판")
        subtotal = next(n for n in table if n.get("name") == "소계"
                        and abs(n["bounds"]["y"] - row["bounds"]["y"]) < 2)
        cell = next(n for n in table if n["id"] == subtotal["id"].rsplit("-", 1)[0] + "-7")
        app.click(cell["ref"])
        clicked = app.wait_for_event("cell_clicked")
        assert clicked["row"] == ["극판", "소계", ""] and clicked["column"] == ["생산", "양품수"]
        assert app.wait_for_event("cell_popup_opened")["column"] == "양품수"
        app.wait_for_windows(before + 1)
        assert len(result) < len(app.events())
        label = next(n for n in table if n.get("name") == "코팅")
        app.click(label["ref"])
        assert len([e for e in app.events() if e["event"] == "cell_clicked"]) == 1
