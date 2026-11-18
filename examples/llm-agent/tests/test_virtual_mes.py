import json
import subprocess

import pytest

from virtual_mes_support import EXE, export, read_xlsx, requires_exe, run_exe

pytestmark = requires_exe


def parsed(*args):
    done = run_exe(*args, "--parse-only")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_default_profile_is_mes_like():
    assert parsed()["expose"] == {
        "menu": "ref", "filters": "text", "grid": "text", "icon": "pixel", "radial": "pixel", "arrow": "bare"}


def test_easy_and_hard_profiles():
    assert set(parsed("--profile", "easy")["expose"].values()) == {"ref"}
    assert parsed("--profile", "hard")["expose"] == {
        "menu": "text", "filters": "pixel", "grid": "pixel", "icon": "pixel", "radial": "pixel", "arrow": "pixel"}


@pytest.mark.parametrize("mode", ["ref", "bare", "pixel"])
def test_arrow_region_takes_ref_bare_or_pixel_apart_from_filters(mode):
    out = parsed("--expose", f"filters=pixel,arrow={mode}")
    assert out["expose"]["arrow"] == mode and out["expose"]["filters"] == "pixel"


def test_expose_overrides_profile():
    out = parsed("--profile", "easy", "--expose", "grid=pixel,radial=text")
    assert out["expose"]["grid"] == "pixel" and out["expose"]["radial"] == "text"
    assert out["expose"]["menu"] == "ref"


@pytest.mark.parametrize("args", [
    ["--expose", "icon=text"], ["--expose", "grid=shiny"], ["--expose", "nowhere=ref"],
    ["--expose", "arrow=text"], ["--expose", "arrow=shiny"], ["--expose", "filters=bare"],
    ["--expose", "menu=bare"], ["--expose", "icon=bare"],
    ["--profile", "medium"], ["--delay-ms", "soon"], ["--delay-ms", "-1"], ["--unknown"],
    ["--delay-ms"]])
def test_bad_options_exit_2_with_a_reason(args):
    done = run_exe(*args, "--parse-only")
    assert done.returncode == 2
    assert done.stderr.strip()


def test_combo_open_defaults_to_arrow_and_accepts_anywhere():
    assert parsed()["combo_open"] == "arrow"
    assert parsed("--combo-open", "arrow")["combo_open"] == "arrow"
    assert parsed("--combo-open", "anywhere")["combo_open"] == "anywhere"


@pytest.mark.parametrize("args", [["--combo-open", "sideways"], ["--combo-open"], ["--combo-open", "Arrow"]])
def test_bad_combo_open_exits_2(args):
    done = run_exe(*args, "--parse-only")
    assert done.returncode == 2
    assert done.stderr.strip()


def test_args_file_is_used_only_without_arguments(tmp_path):
    copy = tmp_path / EXE.name
    copy.write_bytes(EXE.read_bytes())
    (tmp_path / "VirtualMes.args").write_text(
        '--profile hard --parse-only --log "C:\\a b\\x.jsonl"', encoding="utf-8")
    from_file = json.loads(run_exe(exe=copy).stdout)
    assert from_file["profile"] == "hard" and from_file["log"] == "C:\\a b\\x.jsonl"
    assert json.loads(run_exe("--parse-only", exe=copy).stdout)["profile"] == "mes-like"


def rows(tmp_path, screen, filters):
    out = tmp_path / "out.xlsx"
    done = export(screen, filters, out)
    assert done.returncode == 0, done.stderr
    return read_xlsx(out)


def test_front_process_default_is_yesterday_all_shops(tmp_path):
    table = rows(tmp_path, "front-process", {})
    assert table[0] == ["일자", "Shop", "공정", "제품", "생산수", "불량수", "불량률"]
    assert len(table) == 1 + 16
    assert {row[0] for row in table[1:]} == {"2026-11-11"}


def test_shop_filter_keeps_only_that_shop(tmp_path):
    table = rows(tmp_path, "front-process", {"shop": ["WA2"]})
    assert [row[1:4] for row in table[1:]] == [
        ["조립", "권취", "모델 A"], ["조립", "권취", "모델 B"],
        ["조립", "용접", "모델 A"], ["조립", "용접", "모델 B"]]
    assert table[1][4:] == ["13,541", "35", "0.26%"]


def test_same_request_gives_same_cells(tmp_path):
    first = rows(tmp_path, "roller-check", {"shop": ["WA0", "WA2"], "period": "전주"})
    assert first == rows(tmp_path, "roller-check", {"shop": ["WA0", "WA2"], "period": "전주"})
    assert len(first) == 1 + 7 * 2 * 3


def test_custom_period_is_inclusive(tmp_path):
    table = rows(tmp_path, "front-process", {
        "shop": ["WA0"], "process": "계량", "product": "모델 A",
        "period": "기간", "from": "2026-11-03", "to": "2026-11-05"})
    assert [row[0] for row in table[1:]] == ["2026-11-03", "2026-11-04", "2026-11-05"]


def test_defect_table_sums_front_process(tmp_path):
    summary = rows(tmp_path, "defect-by-process", {})
    assert [row[0] for row in summary[1:]] == ["믹싱", "극판", "조립", "화성"]
    detail = rows(tmp_path, "defect-detail", {"row": "조립"})
    assert detail[0] == ["불량 유형", "수량", "비율"]
    total = sum(int(row[1].replace(",", "")) for row in detail[1:])
    assert f"{total:,}" == summary[3][2]


def test_no_shop_selected_saves_header_only(tmp_path):
    assert rows(tmp_path, "front-process", {"shop": []}) == [
        ["일자", "Shop", "공정", "제품", "생산수", "불량수", "불량률"]]


@pytest.mark.parametrize("screen,filters", [
    ("front-process", {"period": "기간"}),
    ("front-process", {"period": "기간", "from": "2026-11-05", "to": "2026-11-03"}),
    ("front-process", {"period": "기간", "from": "11/05/2026", "to": "2026-11-06"}),
    ("front-process", {"shop": ["ZZ9"]}), ("front-process", {"colour": "red"}),
    ("no-such-screen", {}), ("defect-detail", {})])
def test_bad_requests_exit_2(tmp_path, screen, filters):
    done = export(screen, filters, tmp_path / "out.xlsx")
    assert done.returncode == 2 and done.stderr.strip()
    assert not (tmp_path / "out.xlsx").exists()


def test_unwritable_path_exits_1(tmp_path):
    done = export("front-process", {}, tmp_path / "missing-folder" / "out.xlsx")
    assert done.returncode == 1 and done.stderr.strip()


SUMMARY_HEADER = ["SHOP", "그룹공정", "공정", "계획", "실적", "달성률", "작업수", "양품수", "양품율",
                  "수율(Loss제외)", "불량수", "불량율(PPM)", "LOSS수", "LOSS율(PPM)", "이탈수", "이탈율(PPM)"]
SHOP_ORDER = ["믹싱", "극판", "조립", "화성"]
GROUPS = {"믹싱": ["계량", "혼합"], "극판": ["코팅", "압연"], "조립": ["권취", "용접"], "화성": ["충전", "선별"]}


def number(text):
    return int(text.replace(",", ""))


def test_process_summary_has_the_lower_header_and_subtotal_rows(tmp_path):
    table = rows(tmp_path, "process-summary", {})
    assert table[0] == SUMMARY_HEADER
    shops = [row[0] for row in table[1:]]
    assert shops == sorted(shops, key=SHOP_ORDER.index)
    first_of_shop = next(row for row in table[1:] if row[0] == "극판")
    assert first_of_shop[1:3] == ["소계", ""]
    assert all(row[2] == "소계" for row in table[1:] if row[1] != "소계")


def test_process_summary_rows_follow_the_shops_and_their_groups(tmp_path):
    table = rows(tmp_path, "process-summary", {})
    expected = []
    for shop in SHOP_ORDER:
        expected.append([shop, "소계", ""])
        expected += [[shop, group, "소계"] for group in GROUPS[shop]]
    assert [row[:3] for row in table[1:]] == expected
    assert all(len(row) == len(SUMMARY_HEADER) for row in table)


def test_shop_subtotal_is_the_sum_of_its_group_rows(tmp_path):
    table = rows(tmp_path, "process-summary", {"period": "전주"})
    for shop in SHOP_ORDER:
        subtotal = next(r for r in table[1:] if r[0] == shop and r[1] == "소계")
        groups = [r for r in table[1:] if r[0] == shop and r[1] != "소계"]
        for column in (3, 4, 6, 7, 10, 12, 14):
            assert number(subtotal[column]) == sum(number(r[column]) for r in groups), (shop, SUMMARY_HEADER[column])


def test_work_count_is_the_front_process_quantity_and_good_count_subtracts_the_losses(tmp_path):
    table = rows(tmp_path, "process-summary", {"shop": ["WA1"]})
    front = rows(tmp_path, "front-process", {"shop": ["WA1"], "process": "코팅"})
    coating = next(r for r in table[1:] if r[1] == "코팅")
    assert number(coating[6]) == sum(number(r[4]) for r in front[1:])
    assert number(coating[10]) == sum(number(r[5]) for r in front[1:])
    assert number(coating[7]) == number(coating[6]) - number(coating[10]) - number(coating[12]) - number(coating[14])
    assert coating[11] == f"{round(number(coating[10]) * 1_000_000 / number(coating[6])):,}"


def test_process_summary_respects_the_shop_filter(tmp_path):
    assert {row[0] for row in rows(tmp_path, "process-summary", {"shop": ["WA1"]})[1:]} == {"극판"}
    assert rows(tmp_path, "process-summary", {"shop": []}) == [SUMMARY_HEADER]


def test_process_summary_is_the_same_for_the_same_filters(tmp_path):
    first = rows(tmp_path, "process-summary", {"period": "기간", "from": "2026-11-02", "to": "2026-11-06"})
    again = rows(tmp_path, "process-summary", {"period": "기간", "from": "2026-11-02", "to": "2026-11-06"})
    assert first == again != rows(tmp_path, "process-summary", {})


STARTUP_OFF = {"splash_ms": None, "launcher": False, "single_instance": False, "menu_delay_ms": None,
               "main": False, "main_command_line": None}


def startup(out):
    return {key: out.get(key, "missing") for key in STARTUP_OFF}


def test_startup_options_parse():
    out = parsed("--splash-ms", "1500", "--launcher", "--single-instance", "--menu-delay-ms", "2000")
    assert (out["splash_ms"], out["launcher"], out["single_instance"], out["menu_delay_ms"], out["main"]) == (
        1500, True, True, 2000, False)
    assert startup(parsed()) == STARTUP_OFF


def test_splash_without_a_launcher_stays_in_one_process():
    out = parsed("--splash-ms", "0", "--menu-delay-ms", "0")
    assert (out["splash_ms"], out["launcher"], out["menu_delay_ms"], out["main_command_line"]) == (0, False, 0, None)


@pytest.mark.parametrize("args", [
    ["--launcher"], ["--single-instance"], ["--splash-ms", "-1"], ["--menu-delay-ms", "x"],
    ["--launcher", "--single-instance"], ["--splash-ms", "1000", "--single-instance"], ["--splash-ms"],
    ["--menu-delay-ms", "-5"]])
def test_bad_startup_options_exit_2(args):
    done = run_exe(*args, "--parse-only")
    assert done.returncode == 2
    assert done.stderr.strip()


def test_main_flag_ignores_the_args_file(tmp_path):
    copy = tmp_path / EXE.name
    copy.write_bytes(EXE.read_bytes())
    (tmp_path / "VirtualMes.args").write_text("--splash-ms 1000 --launcher --parse-only", encoding="utf-8")
    from_file = json.loads(run_exe(exe=copy).stdout)
    assert (from_file["splash_ms"], from_file["launcher"]) == (1000, True)
    out = parsed_with(copy, "--main")
    assert (out["splash_ms"], out["launcher"], out["main"]) == (None, False, True)


def parsed_with(exe, *args):
    done = run_exe(*args, "--parse-only", exe=exe)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_main_flag_ignores_startup_options_it_is_given():
    out = parsed("--main", "--splash-ms", "1000", "--launcher", "--single-instance", "--menu-delay-ms", "700")
    assert (out["splash_ms"], out["launcher"], out["single_instance"], out["main"]) == (None, False, False, True)
    assert out["menu_delay_ms"] == 700 and out["main_command_line"] is None


def run_command_line(command_line):
    """Runs the exe with a raw command line, so the app's own argument splitting reads it."""
    done = subprocess.run(f'"{EXE}" {command_line} --parse-only', capture_output=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("log", [r"C:\a b\x.jsonl", "C:\\a b\\", r"C:\plain.jsonl", r'C:\say "hi"\x.jsonl'])
def test_launcher_hands_the_main_process_every_option_but_the_launch_ones(log):
    out = parsed("--profile", "easy", "--splash-ms", "1500", "--launcher", "--single-instance",
                 "--menu-delay-ms", "2000", "--seed", "7", "--log", log)
    assert not {"--splash-ms", "1500", "--launcher", "--single-instance"} & set(out["main_command_line"].split())
    main = run_command_line(out["main_command_line"])
    assert main["main"] is True
    assert (main["splash_ms"], main["launcher"], main["single_instance"]) == (None, False, False)
    assert (main["profile"], main["menu_delay_ms"], main["seed"], main["log"]) == ("easy", 2000, 7, log)


def test_launcher_from_the_args_file_hands_those_options_on(tmp_path):
    copy = tmp_path / EXE.name
    copy.write_bytes(EXE.read_bytes())
    (tmp_path / "VirtualMes.args").write_text(
        r'--profile hard --splash-ms 500 --launcher --parse-only --log "C:\a b\x.jsonl"', encoding="utf-8")
    out = json.loads(run_exe(exe=copy).stdout)
    main = run_command_line(out["main_command_line"])
    assert (main["profile"], main["log"], main["main"], main["splash_ms"]) == ("hard", r"C:\a b\x.jsonl", True, None)
