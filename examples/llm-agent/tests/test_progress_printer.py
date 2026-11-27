from agent.progress_printer import ProgressPrinter


def printed(records, events=()):
    lines = []
    printer = ProgressPrinter(write=lines.append)
    for event in events:
        printer.on_event(event)
    for record in records:
        printer.on_record(record)
    printer.flush()
    return lines


def s(item_id, kind, t, **fields):
    return {"id": item_id, "phase": "start", "kind": kind, "t_ms": t, **fields}


def e(item_id, kind, t, ok=True, **fields):
    return {"id": item_id, "phase": "end", "kind": kind, "t_ms": t, "ok": ok, **fields}


def test_a_tool_with_its_commands():
    assert printed([s(1, "tool", 0, name="click_part", step=7, intent="메뉴를 연다"),
                    s(2, "cli", 10, parent=1, name="focus-window"), e(2, "cli", 310),
                    s(3, "cli", 320, parent=1, name="mouse-click"), e(3, "cli", 720),
                    e(1, "tool", 2800)]) == [
        "▶ click_part · 메뉴를 연다", "   ○ focus-window · 0.3초", "   ○ mouse-click · 0.4초", "○ click_part · 2.8초"]


def test_model_lines_carry_step_and_tokens():
    assert printed([s(1, "model", 0, step=7), e(1, "model", 14200, tokens=3120)]) == [
        "▶ 모델 생각 중 · 스텝 7", "○ 모델 응답 · 14.2초 · 토큰 3,120"]


def test_a_named_model_call_and_a_failed_one():
    assert printed([s(1, "model", 0, name="vision"), e(1, "model", 3000, ok=False, code="APIError")]) == [
        "▶ 모델(vision) 생각 중", "× 모델(vision) 응답 · 3.0초 · APIError"]


def test_repeated_reads_print_one_line():
    records = [s(1, "ready", 0)]
    for i in range(24):
        records += [s(2 + i, "cli", i * 500, parent=1, name="snapshot"),
                    e(2 + i, "cli", i * 500 + 500, ok=i % 12 != 0)]
    records.append(e(1, "ready", 12_500))
    assert printed(records) == ["▶ 대상 앱 준비 중", "   ○ snapshot ×24 · 12.0초 · 실패 2", "○ 대상 앱 준비 · 12.5초"]


def test_a_group_where_every_read_failed_is_marked_failed():
    records = [s(1, "cli", 0, name="snapshot"), e(1, "cli", 100, ok=False, code="TIMEOUT"),
               s(2, "cli", 100, name="snapshot"), e(2, "cli", 200, ok=False, code="TIMEOUT")]
    assert printed(records) == ["× snapshot ×2 · 0.2초 · 실패 2"]


def test_a_different_command_breaks_the_group():
    records = [s(1, "cli", 0, name="snapshot"), e(1, "cli", 100),
               s(2, "cli", 100, name="snapshot"), e(2, "cli", 200),
               s(3, "cli", 200, name="list-windows"), e(3, "cli", 300)]
    assert printed(records) == ["○ snapshot ×2 · 0.2초", "○ list-windows · 0.1초"]


def test_read_only_tools_group_and_hide_children():
    records = []
    for i in range(3):
        base = i * 10
        records += [s(base + 1, "tool", i * 1000, name="observe", read_only=True),
                    s(base + 2, "cli", i * 1000 + 1, parent=base + 1, name="snapshot"),
                    e(base + 2, "cli", i * 1000 + 400),
                    e(base + 1, "tool", i * 1000 + 500)]
    assert printed(records) == ["○ observe ×3 · 1.5초"]


def test_failure_and_mismatch_marks():
    assert printed([s(1, "tool", 0, name="click_part"), e(1, "tool", 400, ok=False, code="focus_failed"),
                    s(2, "tool", 500, name="toggle"), e(2, "tool", 900, mismatch=True)]) == [
        "▶ click_part", "× click_part · 0.4초 · focus_failed", "▶ toggle", "△ toggle · 0.4초 · 기대와 다름"]


def test_a_failed_command_shows_its_code():
    assert printed([s(1, "tool", 0, name="click_part"), s(2, "cli", 0, parent=1, name="mouse-click"),
                    e(2, "cli", 400, ok=False, code="ACTION_FAILED"), e(1, "tool", 500, ok=False)]) == [
        "▶ click_part", "   × mouse-click · 0.4초 · ACTION_FAILED", "× click_part · 0.5초"]


def test_long_intent_is_cut():
    line = printed([s(1, "tool", 0, name="toggle", intent="가" * 70)])[0]
    assert line == "▶ toggle · " + "가" * 59 + "…"


def test_commands_nest_by_printed_ancestors():
    assert printed([s(1, "tool", 0, name="look"), s(2, "model", 0, parent=1, name="vision"),
                    e(2, "model", 1000), e(1, "tool", 1200)]) == [
        "▶ look", "   ▶ 모델(vision) 생각 중", "   ○ 모델(vision) 응답 · 1.0초", "○ look · 1.2초"]


def test_stage_line_counts_from_the_plan():
    events = [{"type": "plan", "stages": [{}, {}, {}, {}]}, {"type": "stage", "n": 2, "status": "started"}]
    assert printed([], events) == ["[단계 2/4]"]


def test_stage_line_without_a_plan_has_no_total():
    assert printed([], [{"type": "stage", "n": 1, "status": "started"}]) == ["[단계 1]"]


def test_a_stage_that_ends_prints_nothing():
    assert printed([], [{"type": "stage", "n": 1, "status": "done"}]) == []


def test_every_mark_survives_cp949():
    for mark in "▶○△×…":
        mark.encode("cp949")


def test_alternating_reads_fold_by_name():
    records = [s(1, "ready", 0)]
    t = 0
    for i in range(10):
        records += [s(10 + 2 * i, "cli", t, parent=1, name="list-windows"), e(10 + 2 * i, "cli", t + 100),
                    s(11 + 2 * i, "cli", t + 100, parent=1, name="snapshot"), e(11 + 2 * i, "cli", t + 400)]
        t += 500
    records.append(e(1, "ready", t))
    assert printed(records) == ["▶ 대상 앱 준비 중", "   ○ list-windows ×10 · 1.0초", "   ○ snapshot ×10 · 3.0초",
                                "○ 대상 앱 준비 · 5.0초"]


def test_alternating_read_only_tools_fold_by_name():
    records = []
    for i in range(4):
        records += [s(10 + 2 * i, "tool", i * 100, name="list_windows", read_only=True), e(10 + 2 * i, "tool", i * 100 + 20),
                    s(11 + 2 * i, "tool", i * 100 + 20, name="observe", read_only=True), e(11 + 2 * i, "tool", i * 100 + 70)]
    assert printed(records) == ["○ list_windows ×4 · 0.1초", "○ observe ×4 · 0.2초"]
