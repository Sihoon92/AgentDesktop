import json

import pytest

from virtual_mes_support import EXE, export, read_xlsx, requires_exe, run_exe

pytestmark = requires_exe


def parsed(*args):
    done = run_exe(*args, "--parse-only")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_default_profile_is_mes_like():
    assert parsed()["expose"] == {
        "menu": "ref", "filters": "text", "grid": "text", "icon": "pixel", "radial": "pixel"}


def test_easy_and_hard_profiles():
    assert set(parsed("--profile", "easy")["expose"].values()) == {"ref"}
    assert parsed("--profile", "hard")["expose"] == {
        "menu": "text", "filters": "pixel", "grid": "pixel", "icon": "pixel", "radial": "pixel"}


def test_expose_overrides_profile():
    out = parsed("--profile", "easy", "--expose", "grid=pixel,radial=text")
    assert out["expose"]["grid"] == "pixel" and out["expose"]["radial"] == "text"
    assert out["expose"]["menu"] == "ref"


@pytest.mark.parametrize("args", [
    ["--expose", "icon=text"], ["--expose", "grid=shiny"], ["--expose", "nowhere=ref"],
    ["--profile", "medium"], ["--delay-ms", "soon"], ["--delay-ms", "-1"], ["--unknown"],
    ["--delay-ms"]])
def test_bad_options_exit_2_with_a_reason(args):
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
