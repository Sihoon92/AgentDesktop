import json
import time

from fake_cli import FakeCli
from structure_support import EXE, OBSERVE, add_rows, company, data_rows, scene, set_values, table_node, toolbox_for

from agent.ask import Answer
from agent.desktop import DesktopClient
from agent.loop import RunResult
from agent.recorder import RunRecorder
from agent.structure import READ_ROWS, analyze, find_rows, render_table
from agent.summary import summarize
from agent.task import Task
from agent.tool_schemas import READ_ONLY_TOOLS, SCHEMAS, definitions_for
from agent.tools import Toolbox

TWO_SUBTOTALS = [["극판", "소계", "980"], ["조립", "소계", "640"], ["극판", "소계", "120"]]
WHERE = [{"column": "공정 row#", "value": "극판"}, {"column": "구분 row#", "value": "소계"}]


class ScriptedAsker:
    interactive = True

    def __init__(self, answers):
        self.answers = list(answers)
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return self.answers.pop(0)


def asking_toolbox(tmp_path, tree, answers=()):
    return toolbox_for(tree, tmp_path, asker=ScriptedAsker(answers), wall=lambda: time.time() - 60)


def press(mark):
    return {"mark": mark, "part": "center", "action": "click", "intent": "누른다", "knowledge_id": None}


def confirm(marks):
    return {"kind": "confirm", "text": "어느 칸인가요?", "proposed": 1, "basis": [],
            "candidates": [{"label": f"후보 {m}", "mark": m, "capture": None, "x": None, "y": None} for m in marks]}


def table(tree):
    return analyze(tree).tables[0]


def test_find_rows_returns_the_single_matching_row_with_cell_marks():
    found = find_rows(table(company()), {"공정 row#": " 조립 ", "구분 row#": "소계"}, ["양품수 row#"])
    assert found["reason"] is None and len(found["rows"]) == 1
    row = found["rows"][0]
    assert row["index"] == 2 and row["keys"] == {"공정 row#": "조립", "구분 row#": "소계"}
    assert list(row["cells"]) == ["양품수 row#"]
    cell = row["cells"]["양품수 row#"]
    assert cell["value"] == "640" and cell["offscreen"] is False
    assert cell["mark"] == table(company()).rows[2].cells[2].mark


def test_find_rows_keys_carry_the_key_candidate_columns_and_cells_default_to_all():
    tree = set_values(company(), [["극판", "A", "1"], ["극판", "B", "2"], ["조립", "C", "3"]])
    row = find_rows(table(tree), {"양품수 row#": "2"}, None)["rows"][0]
    assert row["keys"] == {"구분 row#": "B", "양품수 row#": "2"}
    assert list(row["cells"]) == ["공정 row#", "구분 row#", "양품수 row#"]


def test_find_rows_reports_no_such_column_and_no_match():
    assert find_rows(table(company()), {"공정": "극판"}, None) == {"rows": [], "reason": "no_such_column"}
    assert find_rows(table(company()), {"공정 row#": "극판"}, ["없는 열"])["reason"] == "no_such_column"
    assert find_rows(table(company()), {"공정 row#": "도포"}, None) == {"rows": [], "reason": "no_match"}


def test_find_rows_does_not_fill_blank_labels_down():
    found = find_rows(table(company()), {"공정 row#": "극판", "구분 row#": "소계"}, None)
    assert found == {"rows": [], "reason": "no_match"}
    blank = find_rows(table(company()), {"공정 row#": "", "구분 row#": "소계"}, None)["rows"]
    assert [row["index"] for row in blank] == [1]
    assert blank[0]["cells"]["공정 row#"]["value"] is None


def test_find_rows_tells_a_repeated_label_apart():
    tree = company()
    node = table_node(tree)
    node["children"].insert(0, {"role": "column", "name": "생산", "bounds": {"x": 160, "y": 175, "width": 300, "height": 18}})
    labels = [column.label for column in table(tree).columns]
    assert labels == ["공정 row#", "생산", "생산 (2)"]
    found = find_rows(table(tree), {"생산 (2)": "980"}, ["생산"])
    assert [row["cells"]["생산"]["value"] for row in found["rows"]] == ["소계"]


def test_read_table_pages_by_30_rows():
    tree = add_rows(company(), [[f"공정{k}", "소계", str(k)] for k in range(67)])
    big = table(tree)
    assert READ_ROWS == 30 and len(big.rows) == 70
    first = render_table(big, None)
    assert (first["start"], first["shown"], first["total"], first["next"]) == (0, 30, 70, 30)
    lines = first["text"].splitlines()
    assert lines[0] == "T1 열: 공정 row# | 구분 row# | 양품수 row#"
    assert lines[1] == f"row0: 공정 row#=극판[{big.rows[0].cells[0].mark}] 구분 row#=A[{big.rows[0].cells[1].mark}] " \
                       f"양품수 row#=1,200[{big.rows[0].cells[2].mark}]"
    assert lines[2].startswith("row1: 공정 row#=[") and len(lines) == 31
    last = render_table(big, ["양품수 row#"], start=60)
    assert (last["shown"], last["next"]) == (10, None)
    assert last["text"].splitlines()[1] == f"row60: 양품수 row#=57[{big.rows[60].cells[2].mark}]"


def test_read_table_marks_cells_off_the_screen_and_missing_cells():
    tree = company()
    data_rows(tree)[2]["children"][2].pop("bounds")
    data_rows(tree)[1]["children"].pop(0)
    text = render_table(table(tree), None)["text"].splitlines()
    assert text[2].startswith("row1: 공정 row#=- ")
    assert text[3].endswith(" 화면 밖]")


def test_read_table_tool_pages_and_refuses_unknown_columns(tmp_path):
    toolbox, _ = toolbox_for(add_rows(company(), [["x", "y", "1"]] * 40), tmp_path)
    result = toolbox.call("read_table", {"table": "T1", "columns": None, "start": None})
    assert result["ok"] and result["shown"] == 30 and result["next"] == 30
    assert toolbox.call("read_table", {"table": "T1", "columns": None, "start": 30})["next"] is None
    refused = toolbox.call("read_table", {"table": "T1", "columns": ["양품수"], "start": None})
    assert "refused" in refused and "양품수 row#" in refused["refused"]


def test_open_area_lists_lines_left_to_right(tmp_path):
    tree = company()
    combos = [{"role": "combobox", "ref_id": f"@s:f{i}", "bounds": {"x": x, "y": 12, "width": 60, "height": 20}}
              for i, x in ((1, 230), (2, 90), (3, 160))]
    tree["children"].insert(0, {"role": "pane", "native_id": "pn_Filter", "children": [
        {"role": "statictext", "name": "Shop", "bounds": {"x": 10, "y": 10, "width": 60, "height": 24}}, *combos]})
    toolbox, _ = toolbox_for(tree, tmp_path)
    result = toolbox.call("open_area", {"area": "A1"})
    assert result["ok"]
    assert result["text"].splitlines() == ['A1 영역 id="pn_Filter"',
                                           '  줄1: [1] statictext "Shop" · [3] combobox · [4] combobox · [2] combobox']
    main = toolbox.call("open_area", {"area": "A2"})["text"].splitlines()
    assert main[0] == 'A2 영역 id="pn_Main"' and main[1].startswith("  T1 표")
    assert "refused" in toolbox.call("open_area", {"area": "A3"})


def test_handles_from_an_older_observation_are_refused(tmp_path):
    toolbox, cli = toolbox_for(company(), tmp_path)
    assert toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": None})["ok"]
    cli.scene["tree"] = {"role": "window", "name": "메인", "children": [
        {"role": "button", "name": "닫기", "ref_id": "@s:e1", "bounds": {"x": 0, "y": 0, "width": 40, "height": 20}}]}
    toolbox.call("observe", OBSERVE)
    for name, arguments in (("find_rows", {"table": "T1", "where": WHERE, "columns": None}),
                            ("read_table", {"table": "T1", "columns": None, "start": None}),
                            ("open_area", {"area": "A1"})):
        result = toolbox.call(name, arguments)
        assert "Observe again" in result["refused"]


def test_find_rows_refuses_a_column_given_twice(tmp_path):
    toolbox, _ = toolbox_for(company(), tmp_path)
    twice = [{"column": "구분 row#", "value": "A"}, {"column": "구분 row#", "value": "소계"}]
    assert "refused" in toolbox.call("find_rows", {"table": "T1", "where": twice, "columns": None})


def test_find_rows_with_two_matches_blocks_both_cells_until_asked(tmp_path):
    toolbox, _ = asking_toolbox(tmp_path, set_values(company(), TWO_SUBTOTALS), [Answer(text="1번")])
    found = toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": ["양품수 row#"]})
    assert found["matched"] == 2 and "note" in found
    first, second = (row["cells"]["양품수 row#"]["mark"] for row in found["rows"])
    names = {row["index"]: row["cells"] for row in found["rows"]}
    assert sorted(names) == [0, 2]
    process_mark = table(set_values(company(), TWO_SUBTOTALS)).rows[0].cells[0].mark
    for mark in (first, second, process_mark):
        assert toolbox.call("click_part", press(mark))["refused_code"] == "needs_confirmation"
    toolbox.call("ask", confirm([first, second]))
    assert toolbox.call("click_part", press(first))["ok"]


def test_an_offscreen_candidate_blocks_the_whole_table(tmp_path):
    tree = set_values(company(), TWO_SUBTOTALS)
    for cell in data_rows(tree)[2]["children"]:
        cell.pop("bounds")
        cell["states"] = ["offscreen"]
    toolbox, _ = asking_toolbox(tmp_path, tree)
    found = toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": None})
    assert found["matched"] == 2
    assert found["rows"][1]["cells"]["양품수 row#"]["offscreen"] is True
    other_row = table(tree).rows[1].cells[2].mark
    assert toolbox.call("click_part", press(other_row))["refused_code"] == "needs_confirmation"


def test_an_offscreen_candidate_without_a_table_box_holds_the_cells_on_the_screen(tmp_path):
    tree = set_values(company(), TWO_SUBTOTALS)
    table_node(tree).pop("bounds")
    for cell in data_rows(tree)[2]["children"]:
        cell.pop("bounds")
    toolbox, _ = asking_toolbox(tmp_path, tree)
    toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": None})
    assert toolbox.call("click_part", press(table(tree).rows[1].cells[2].mark))["refused_code"] == \
        "needs_confirmation"


def test_one_match_clicks_without_a_question(tmp_path):
    toolbox, _ = asking_toolbox(tmp_path, set_values(company(), TWO_SUBTOTALS))
    found = toolbox.call("find_rows", {"table": "T1", "where": [{"column": "공정 row#", "value": "조립"}],
                                       "columns": ["양품수 row#"]})
    assert found["matched"] == 1 and "note" not in found
    assert toolbox.call("click_part", press(found["rows"][0]["cells"]["양품수 row#"]["mark"]))["ok"]


def test_a_single_match_clears_an_earlier_ambiguity(tmp_path):
    toolbox, _ = asking_toolbox(tmp_path, set_values(company(), TWO_SUBTOTALS))
    found = toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": None})
    toolbox.call("find_rows", {"table": "T1", "where": [{"column": "공정 row#", "value": "조립"}], "columns": None})
    assert toolbox.call("click_part", press(found["rows"][0]["cells"]["양품수 row#"]["mark"]))["ok"]


def test_no_such_column_lists_the_labels(tmp_path):
    toolbox, _ = toolbox_for(company(), tmp_path)
    result = toolbox.call("find_rows", {"table": "T1", "where": [{"column": "공정", "value": "극판"}], "columns": None})
    assert result["reason"] == "no_such_column" and result["columns"] == ["공정 row#", "구분 row#", "양품수 row#"]


def test_table_tools_are_level_0_and_read_only():
    levels = {s["definition"]["name"]: s["level"] for s in SCHEMAS}
    for name in ("open_area", "read_table", "find_rows"):
        assert levels[name] == 0 and name in READ_ONLY_TOOLS
        assert name in [d["name"] for d in definitions_for(0, vision=False)]
        definition = next(s["definition"] for s in SCHEMAS if s["definition"]["name"] == name)
        assert "intent" not in definition["parameters"]["properties"]


def test_find_rows_is_kept_in_events(tmp_path):
    cli = FakeCli(scene=scene(set_values(company(), TWO_SUBTOTALS)))
    desktop = DesktopClient("ad.exe", "run1", runner=cli)
    recorder = RunRecorder(tmp_path / "runs", desktop, {"goal": "g"}, heartbeat_s=60)
    try:
        toolbox = Toolbox(desktop, Task("goal", EXE, 2, []), confirm=lambda _p: False, sleep=lambda _s: None,
                          observer=recorder)
        toolbox.call("launch_target", {})
        toolbox.call("observe", OBSERVE)
        toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": ["양품수 row#"]})
    finally:
        recorder.finish(RunResult("verified", 1, "done"))
    lines = (recorder.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    kept = [event for event in map(json.loads, lines) if event["type"] == "find_rows"]
    assert len(kept) == 1
    event = kept[0]
    assert event["table"] == "T1" and event["matched"] == 2 and event["reason"] is None
    assert event["where"] == {"공정 row#": "극판", "구분 row#": "소계"} and len(event["marks"]) == 2
    assert "극판" not in (recorder.path / "run.json").read_text(encoding="utf-8")


def test_summary_counts_find_rows_without_values():
    events = [
        {"type": "find_rows", "table": "T1", "where": {"공정 row#": "극판"}, "matched": 2, "marks": [3, 9]},
        {"type": "find_rows", "table": "T1", "where": {"공정 row#": "도포"}, "matched": 0, "marks": []},
        {"type": "find_rows", "table": "T2", "where": {"구분 row#": "소계"}, "matched": 1, "marks": [5]},
    ]
    lines = summarize({}, events)
    assert "  표 찾기: 3회 · 없음 1 · 여럿 1" in lines
    assert not any("극판" in line or "T1" in line or "row#" in line for line in lines)
    assert not any(line.startswith("  표 찾기") for line in summarize({}, []))


def test_find_rows_keys_split_a_pair_of_key_columns():
    tree = set_values(company(), [["극판", "A", "1"], ["극판", "소계", "2"], ["조립", "소계", "3"]])
    assert table(tree).key_candidates == ("공정 row#+구분 row#",)
    row = find_rows(table(tree), {"양품수 row#": "3"}, [])["rows"][0]
    assert row["keys"] == {"공정 row#": "조립", "구분 row#": "소계", "양품수 row#": "3"} and row["cells"] == {}


def test_open_area_says_when_an_area_has_no_line_or_table(tmp_path):
    tree = company()
    tree["children"].insert(0, {"role": "pane", "native_id": "pn_Empty", "children": [
        {"role": "button", "name": "숨김", "ref_id": "@s:h1"}]})
    toolbox, _ = toolbox_for(tree, tmp_path)
    assert toolbox.call("open_area", {"area": "A1"})["text"].splitlines() == ['A1 영역 id="pn_Empty"',
                                                                             "  (줄도 표도 없음)"]


def test_a_cell_marked_offscreen_counts_as_off_the_screen_even_with_a_box(tmp_path):
    tree = set_values(company(), TWO_SUBTOTALS)
    for cell in data_rows(tree)[2]["children"]:
        cell["states"] = ["offscreen"]
    toolbox, _ = asking_toolbox(tmp_path, tree)
    toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": None})
    assert toolbox.call("click_part", press(table(tree).rows[1].cells[2].mark))["refused_code"] == \
        "needs_confirmation"


def test_the_whole_table_box_is_held_not_only_its_cells(tmp_path):
    tree = set_values(company(), TWO_SUBTOTALS)
    for cell in data_rows(tree)[2]["children"]:
        cell.pop("bounds")
    table_node(tree)["children"].insert(0, {"role": "button", "name": "정렬", "ref_id": "@s:sort",
                                            "available_actions": ["Click"],
                                            "bounds": {"x": 20, "y": 400, "width": 40, "height": 14}})
    toolbox, _ = asking_toolbox(tmp_path, tree)
    toolbox.call("find_rows", {"table": "T1", "where": WHERE, "columns": None})
    sort = next(e["mark"] for e in toolbox.marked_elements() if e["name"] == "정렬")
    assert toolbox.call("click_part", press(sort))["refused_code"] == "needs_confirmation"


def test_each_handle_names_its_own_table(tmp_path):
    tree = company()
    second = {"role": "grid", "bounds": {"x": 500, "y": 200, "width": 100, "height": 60}, "children": [
        {"role": "row", "children": [{"role": "cell", "name": "둘째", "ref_id": "@s:g1",
                                      "bounds": {"x": 500, "y": 200, "width": 90, "height": 20}}]},
        {"role": "row", "children": [{"role": "cell", "name": "표", "ref_id": "@s:g2",
                                      "bounds": {"x": 500, "y": 230, "width": 90, "height": 20}}]}]}
    tree["children"].append({"role": "pane", "native_id": "pn_Side", "children": [second]})
    toolbox, _ = toolbox_for(tree, tmp_path)
    result = toolbox.call("read_table", {"table": "T2", "columns": None, "start": None})
    assert result["table"] == "T2" and result["total"] == 2 and "둘째" in result["text"]
