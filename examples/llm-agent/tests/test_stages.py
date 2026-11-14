import json

import pytest

from agent.stages import MAX_REVISIONS, MAX_STAGES, SIGNALS, StageError, StagePlan

LIMIT = 8


def stages(count, prefix="단계"):
    return [{"goal": f"{prefix} {n} 결과", "done_when": f"{prefix} {n} 확인"} for n in range(1, count + 1)]


def planned(count=3, limit=LIMIT):
    plan = StagePlan(limit)
    plan.set(stages(count))
    return plan


def effects(opened=(), closed=(), switched=None, names=()):
    result = {
        "windows_opened": [{"id": window, "title": f"제목 {window}"} for window in opened],
        "windows_closed": [{"id": window, "title": f"제목 {window}"} for window in closed],
        "added": {"count": len(names), "names": list(names)},
        "removed": {"count": 0, "names": []},
        "changed": [],
        "focus": None,
    }
    if switched:
        result["observed_window"] = {"before": switched[0], "after": switched[1]}
    result["none"] = not (opened or closed or names or switched)
    return result


def act(plan, tool="click", expected_met=True, **effect):
    result = {"ok": True, "effects": effects(**effect), "observation": "화면 글", "expected_met": expected_met}
    plan.note_action(tool, {"ref": "@s1:e1", "intent": f"{tool} 하는 이유"}, result)


def look(plan, tool="observe", refused=False):
    result = {"ok": False, "refused": "막힘", "refused_code": "blocked"} if refused else {"ok": True, "observation": "글"}
    plan.note_action(tool, {}, result)


def signals_of(plan, n=1):
    return next(record for record in plan.records() if record["n"] == n)["signals"]


def test_a_first_plan_starts_stage_one():
    plan = StagePlan(LIMIT)
    assert not plan.exists
    assert plan.current() is None

    event = plan.set(stages(3))

    assert plan.exists
    assert plan.current() == {"n": 1, "goal": "단계 1 결과", "done_when": "단계 1 확인", "calls": 0, "limit": LIMIT}
    assert event["type"] == "plan"
    assert event["revision"] == 0
    assert [stage["status"] for stage in event["stages"]] == ["active", "pending", "pending"]
    assert [stage["n"] for stage in event["stages"]] == [1, 2, 3]
    assert event["stages"][1] == {"n": 2, "goal": "단계 2 결과", "done_when": "단계 2 확인", "status": "pending"}
    assert set(event) == {"type", "revision", "stages"}


def test_a_plan_outside_one_to_twelve_stages_is_refused():
    plan = StagePlan(LIMIT)
    with pytest.raises(StageError) as empty:
        plan.set([])
    assert empty.value.code == "bad_plan"
    with pytest.raises(StageError) as many:
        plan.set(stages(MAX_STAGES + 1))
    assert many.value.code == "too_many_stages"
    assert not plan.exists
    assert plan.set(stages(MAX_STAGES))["revision"] == 0


@pytest.mark.parametrize("broken", [
    {"goal": "", "done_when": "확인"},
    {"goal": "결과", "done_when": "  "},
    {"goal": "결과"},
    {"done_when": "확인"},
    {"goal": None, "done_when": "확인"},
    "결과",
])
def test_a_stage_needs_both_texts(broken):
    plan = StagePlan(LIMIT)
    with pytest.raises(StageError) as refused:
        plan.set([stages(1)[0], broken])
    assert refused.value.code == "bad_plan"
    assert not plan.exists


def test_a_revision_keeps_done_stages_and_replaces_the_rest():
    plan = planned(3)
    plan.count_call()
    plan.complete("text", "보였다")

    event = plan.set(stages(1) + stages(3, prefix="새"))

    assert event["revision"] == 1
    assert [stage["status"] for stage in event["stages"]] == ["done", "active", "pending", "pending"]
    assert event["stages"][1]["goal"] == "새 1 결과"
    first = plan.records()[0]
    assert (first["status"], first["calls"], first["kind"], first["note"]) == ("done", 1, "text", "보였다")
    assert plan.current()["n"] == 2
    assert plan.current()["goal"] == "새 1 결과"
    assert plan.unfinished() == [2, 3, 4]


def test_a_revision_carries_the_current_stages_calls_to_its_replacement():
    plan = planned(2)
    for _ in range(5):
        plan.count_call()
    act(plan, expected_met=False)

    plan.set(stages(3, prefix="새"))

    assert plan.current()["calls"] == 5
    assert plan.records()[0]["signals"] == ["mismatch"]
    assert plan.stats()["stage_calls"] == [5, 0, 0]


@pytest.mark.parametrize("revised", [
    [{"goal": "단계 1 바뀐 결과", "done_when": "단계 1 확인"}] + stages(3)[1:],
    [{"goal": "단계 1 결과", "done_when": "다른 확인"}] + stages(3)[1:],
    stages(3, prefix="새"),
    stages(3)[1:],
    stages(3)[:1],
])
def test_a_revision_that_rewrites_or_drops_a_done_stage_is_refused(revised):
    plan = planned(3)
    plan.complete("text", "보였다")
    plan.complete("picture", "그림에 보였다")
    with pytest.raises(StageError) as refused:
        plan.set(revised)
    assert refused.value.code == "changes_done_stage"
    assert plan.current()["n"] == 3
    assert plan.stats()["plan_revisions"] == 0


def test_a_fourth_revision_is_refused():
    plan = planned(2)
    for revision in range(1, MAX_REVISIONS + 1):
        assert plan.set(stages(2, prefix=f"고침{revision}"))["revision"] == revision
    with pytest.raises(StageError) as refused:
        plan.set(stages(2, prefix="또"))
    assert refused.value.code == "too_many_revisions"
    assert plan.current()["goal"] == f"고침{MAX_REVISIONS} 1 결과"


def test_more_revisions_can_be_allowed_after_asking():
    plan = planned(2)
    for revision in range(1, MAX_REVISIONS + 1):
        plan.set(stages(2, prefix=f"고침{revision}"))

    plan.allow_revisions()

    for revision in range(MAX_REVISIONS + 1, 2 * MAX_REVISIONS + 1):
        assert plan.set(stages(2, prefix=f"또{revision}"))["revision"] == revision
    with pytest.raises(StageError) as refused:
        plan.set(stages(2, prefix="한 번 더"))
    assert refused.value.code == "too_many_revisions"
    assert plan.stats()["plan_revisions"] == 2 * MAX_REVISIONS
    assert plan.stats()["limit_questions"] == 0


def test_calls_count_on_the_current_stage_only():
    plan = StagePlan(LIMIT)
    plan.count_call()
    plan.set(stages(2))
    plan.count_call()
    plan.count_call()
    plan.complete("text", "끝")
    plan.count_call()

    assert plan.stats()["stage_calls"] == [2, 1]
    plan.complete("text", "끝")
    plan.count_call()
    assert plan.stats()["stage_calls"] == [2, 1]


def test_the_limit_is_reached_at_the_call_limit():
    plan = planned(2)
    assert not StagePlan(LIMIT).at_limit()
    for _ in range(LIMIT - 1):
        plan.count_call()
    assert not plan.at_limit()
    plan.count_call()
    assert plan.at_limit()


def test_extend_adds_one_more_allowance_and_counts_a_limit_question():
    plan = planned(2)
    for _ in range(LIMIT):
        plan.count_call()

    event = plan.extend()

    assert event == {"type": "stage", "n": 1, "status": "limit", "calls": LIMIT, "limit": 2 * LIMIT}
    assert not plan.at_limit()
    assert plan.current()["limit"] == 2 * LIMIT
    assert plan.stats()["limit_questions"] == 1
    plan.complete("text", "끝")
    assert plan.current()["limit"] == LIMIT


def test_completing_a_stage_starts_the_next():
    plan = planned(2)
    plan.count_call()

    events = plan.complete("picture", "그림의 체크 표시")

    assert events == [
        {"type": "stage", "n": 1, "status": "done", "calls": 1, "limit": LIMIT, "kind": "picture",
         "note": "그림의 체크 표시"},
        {"type": "stage", "n": 2, "status": "started", "calls": 0, "limit": LIMIT},
    ]


def test_completing_the_last_stage_leaves_nothing_current():
    plan = planned(1)

    events = plan.complete("text", "끝")

    assert [event["status"] for event in events] == ["done"]
    assert plan.current() is None
    assert plan.all_done()
    assert plan.unfinished() == []
    assert not plan.at_limit()


def test_complete_without_a_plan_is_refused():
    with pytest.raises(StageError) as refused:
        StagePlan(LIMIT).complete("text", "끝")
    assert refused.value.code == "no_plan"


def test_complete_after_all_done_is_refused():
    plan = planned(1)
    plan.complete("text", "끝")
    with pytest.raises(StageError) as refused:
        plan.complete("text", "또")
    assert refused.value.code == "nothing_to_finish"


def test_complete_takes_text_or_picture_evidence_only():
    with pytest.raises(ValueError):
        planned(1).complete("guess", "끝")


def test_stop_marks_the_current_stage_stopped():
    plan = planned(2)
    plan.count_call()

    assert plan.stop() == {"type": "stage", "n": 1, "status": "stopped", "calls": 1, "limit": LIMIT}
    assert plan.current() is None
    assert plan.unfinished() == [1, 2]
    assert not plan.all_done()
    assert plan.stop() is None
    assert StagePlan(LIMIT).stop() is None


def test_stats_hold_numbers_only():
    plan = planned(3)
    plan.count_call()
    plan.complete("text", "고유한 메모 글")
    plan.count_call()
    for _ in range(LIMIT):
        plan.count_call()
    plan.extend()

    stats = plan.stats()

    assert stats == {"stage_count": 3, "stages_done": 1, "stage_calls": [1, LIMIT + 1, 0],
                     "limit_questions": 1, "plan_revisions": 0}
    for value in stats.values():
        assert isinstance(value, int) or all(isinstance(item, int) for item in value)
    assert "단계" not in json.dumps(stats, ensure_ascii=False)
    assert StagePlan(LIMIT).stats() == {"stage_count": 0, "stages_done": 0, "stage_calls": [],
                                        "limit_questions": 0, "plan_revisions": 0}


def test_signals_are_the_four_named_ones():
    assert SIGNALS == ("mismatch", "abandoned_window", "looked_twice", "limit_question")


def test_a_mismatch_is_a_signal():
    plan = planned()
    act(plan, expected_met=False)
    assert signals_of(plan) == ["mismatch"]


def test_met_and_unconfirmed_results_are_no_mismatch():
    plan = planned()
    act(plan, expected_met=True)
    act(plan, expected_met=None)
    assert signals_of(plan) == []


def test_closing_an_opened_window_without_acting_in_it_is_abandoned():
    plan = planned()
    act(plan, opened=["w2"])
    look(plan)
    act(plan, closed=["w2"])
    assert signals_of(plan) == ["abandoned_window"]


def test_switching_away_from_an_opened_window_is_abandoned():
    plan = planned()
    act(plan, opened=["w2"])
    act(plan, closed=[], switched=("w2", "w1"))
    assert signals_of(plan) == ["abandoned_window"]


def test_closing_a_window_after_acting_in_it_is_not_abandoned():
    plan = planned()
    act(plan, opened=["w2"])
    act(plan, names=["항목"])
    act(plan, closed=["w2"])
    assert signals_of(plan) == []


def test_a_refused_action_between_opening_and_closing_is_not_acting_in_the_window():
    plan = planned()
    act(plan, opened=["w2"])
    plan.note_action("click", {"intent": "누름"}, {"ok": False, "refused": "막힘", "refused_code": "blocked"})
    act(plan, closed=["w2"])
    assert signals_of(plan) == ["abandoned_window"]


def test_closing_another_window_is_not_abandoned():
    plan = planned()
    act(plan, opened=["w2"])
    act(plan, closed=["w9"])
    assert signals_of(plan) == []


def test_two_looks_in_a_stage_are_a_signal():
    plan = planned()
    look(plan, "observe")
    look(plan, "look")
    assert signals_of(plan) == ["looked_twice"]


@pytest.mark.parametrize("tool", ["observe", "look", "find_cells", "wait_for_text"])
def test_one_look_in_a_stage_is_no_signal(tool):
    plan = planned()
    look(plan, tool)
    look(plan, tool, refused=True)
    act(plan)
    plan.complete("text", "끝")
    look(plan, tool)
    assert signals_of(plan, 1) == []
    assert signals_of(plan, 2) == []


def test_a_limit_question_is_a_signal():
    plan = planned()
    for _ in range(LIMIT):
        plan.count_call()
    plan.extend()
    assert signals_of(plan) == ["limit_question"]
    assert signals_of(plan, 2) == []


def test_actions_before_a_plan_are_not_recorded():
    plan = StagePlan(LIMIT)
    look(plan)
    look(plan)
    plan.set(stages(1))
    assert plan.records()[0]["actions"] == []
    assert signals_of(plan) == []


def test_records_describe_each_stage():
    plan = planned(2)
    plan.count_call()
    act(plan, expected_met=False, opened=["w2", "w3"], closed=["w1"])
    plan.note_action("click", {"intent": "다시 누름"}, {"ok": False, "refused": "막힘", "refused_code": "blocked"})
    plan.complete("text", "보였다")

    first, second = plan.records()

    assert first == {
        "n": 1, "goal": "단계 1 결과", "done_when": "단계 1 확인", "status": "done", "calls": 1,
        "kind": "text", "note": "보였다", "limit_questions": 0,
        "actions": [
            {"tool": "click", "intent": "click 하는 이유", "expected_met": False, "refused_code": None,
             "opened": 2, "closed": 1},
            {"tool": "click", "intent": "다시 누름", "expected_met": None, "refused_code": "blocked",
             "opened": 0, "closed": 0},
        ],
        "signals": ["mismatch"],
    }
    assert second["status"] == "active"
    assert (second["kind"], second["note"], second["actions"]) == (None, None, [])


def test_records_carry_no_screen_text():
    plan = planned(1)
    act(plan, opened=["w2"], names=["화면의 이름 하나", "화면의 이름 둘"])
    act(plan, closed=["w2"], switched=("w2", "w1"))

    actions = json.dumps([record["actions"] for record in plan.records()], ensure_ascii=False)

    for screen_text in ("화면의 이름", "제목", "화면 글", "@s1:e1", "w2"):
        assert screen_text not in actions
    assert all(set(action) == {"tool", "intent", "expected_met", "refused_code", "opened", "closed"}
               for record in plan.records() for action in record["actions"])
