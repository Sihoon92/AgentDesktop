from agent.policy import TOLERANCE_PX, AskPolicy

CELL_A = {"x": 560, "y": 96, "width": 80, "height": 24}
CELL_B = {"x": 560, "y": 120, "width": 80, "height": 24}


def center(bounds):
    return (bounds["x"] + bounds["width"] // 2, bounds["y"] + bounds["height"] // 2)


def test_element_action_passes_without_a_question():
    assert AskPolicy().refusal(False, (10, 10), False) is None
    assert AskPolicy().refusal(False, None, False) is None


def test_raw_point_needs_confirmation_until_answered():
    policy = AskPolicy()
    assert policy.refusal(True, (500, 300), False) == "needs_confirmation"
    policy.note_answer([], [(505, 302)])
    assert policy.refusal(True, (500, 300), False) is None
    assert policy.refusal(True, (500, 300), False) == "needs_confirmation"


def test_tolerance_is_twelve_pixels_on_each_axis():
    assert TOLERANCE_PX == 12
    policy = AskPolicy()
    policy.note_answer([], [(500, 300)])
    assert policy.refusal(True, (513, 300), False) == "needs_confirmation"
    assert policy.refusal(True, (512, 288), False) is None


def test_answer_for_another_place_does_not_clear():
    policy = AskPolicy()
    policy.note_answer([], [(900, 900)])
    assert policy.refusal(True, (500, 300), False) == "needs_confirmation"


def test_raw_point_inside_an_answered_candidate_box_passes():
    policy = AskPolicy()
    policy.note_answer([CELL_A], [])
    assert policy.refusal(True, center(CELL_A), False) is None


def test_ambiguous_cells_need_confirmation_even_by_ref():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"
    assert policy.refusal(False, (5, 5), False) is None
    assert policy.refusal(False, None, False) is None
    policy.note_answer([CELL_A, CELL_B], [])
    assert policy.refusal(False, center(CELL_B), False) is None
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"


def test_single_candidate_is_not_ambiguous():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    policy.note_ambiguous([CELL_A])
    assert policy.refusal(False, center(CELL_A), False) is None


def test_cited_knowledge_passes_confirmation():
    assert AskPolicy().refusal(True, (500, 300), True) is None
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    assert policy.refusal(False, center(CELL_A), True) is None


def test_two_mismatches_in_a_row_block_everything_until_a_question():
    policy = AskPolicy()
    policy.note_result(False, False)
    assert not policy.blocked
    policy.note_result(False, False)
    assert policy.blocked
    assert policy.refusal(False, (10, 10), True) == "blocked_until_question"
    policy.note_answer([], [])
    assert not policy.blocked
    assert policy.refusal(False, (10, 10), False) is None


def test_the_limit_is_configurable():
    policy = AskPolicy(mismatch_limit=3)
    policy.note_result(False, False)
    policy.note_result(False, False)
    assert not policy.blocked
    policy.note_result(False, False)
    assert policy.blocked


def test_a_met_expectation_resets_the_streak():
    policy = AskPolicy()
    policy.note_result(False, False)
    policy.note_result(True, False)
    policy.note_result(False, False)
    assert not policy.blocked


def test_the_streak_restarts_after_an_answer():
    policy = AskPolicy()
    policy.note_result(False, False)
    policy.note_answer([], [])
    policy.note_result(False, False)
    assert not policy.blocked


def test_mismatch_after_cited_knowledge_blocks_at_once():
    policy = AskPolicy()
    policy.note_result(False, True)
    assert policy.blocked


def test_a_dry_run_does_not_use_up_the_permission():
    policy = AskPolicy()
    policy.note_answer([], [(500, 300)])
    assert policy.refusal(True, (500, 300), False, consume=False) is None
    assert policy.refusal(True, (500, 300), False) is None
    assert policy.refusal(True, (500, 300), False) == "needs_confirmation"


def test_an_ambiguous_action_that_ran_clears_the_ambiguity():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    policy.note_answer([CELL_A], [])
    assert policy.refusal(False, center(CELL_A), False) is None
    policy.note_ran()
    assert policy.refusal(False, center(CELL_B), False) is None
    assert policy.refusal(False, center(CELL_A), False) is None


def test_a_cited_ambiguous_action_that_ran_clears_the_ambiguity():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    assert policy.refusal(False, center(CELL_A), True) is None
    policy.note_ran()
    assert policy.refusal(False, center(CELL_B), False) is None


def test_a_dry_run_or_a_refused_run_keeps_the_ambiguity():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    assert policy.refusal(False, center(CELL_A), True, consume=False) is None
    policy.note_ran()
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"
    assert policy.refusal(False, center(CELL_A), True) is None
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"
    policy.note_ran()
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"


def test_an_action_outside_the_candidates_keeps_the_ambiguity():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    assert policy.refusal(False, (5, 5), False) is None
    policy.note_ran()
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"


def test_a_confirmed_point_outside_the_candidates_keeps_the_ambiguity():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    policy.note_answer([], [(500, 300)])
    assert policy.refusal(True, (500, 300), False) is None
    policy.note_ran()
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"
