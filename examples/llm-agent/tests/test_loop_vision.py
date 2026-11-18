"""The loop in split mode: the text model gets no picture and a paragraph on asking the vision
model, vision tokens count toward the token budget but not toward a stage's call limit, and the
result's stats carry the vision counts. The model is a script; the app is FakeCli."""

import json
import time

import pytest
from fake_cli import FakeCli
from test_loop import GIVE_UP, OBSERVE_SCENE, SCENE, ScriptedModel, call, free_task
from test_tools import LIST_WINDOW
from test_vision_tools import FakeAnswer, FakeVision

from agent.desktop import DesktopClient
from agent.loop import SYSTEM_PROMPT, VISION_PROMPT, run_agent
from agent.tools import Toolbox

QUESTION = "Which mark is the search button?"
STAGES = [{"goal": "조회 버튼이 보인다", "done_when": "버튼이 관찰에 있다"}]
_ids = iter(range(10_000))


def turn(name, arguments):
    return [call(name, arguments, f"v{next(_ids)}")]


def launch():
    return turn("launch_target", {})


def observe():
    return turn("observe", OBSERVE_SCENE)


def look():
    return turn("look", {"mark": None, "question": QUESTION})


def plan():
    return turn("plan", {"stages": STAGES})


def run(tmp_path, turns, vision=None, tokens=100, prepare=None, **task_fields):
    model = ScriptedModel(turns, tokens_per_turn=tokens)
    task = free_task(**task_fields)
    cli = FakeCli(scene=SCENE)
    cli.on_click = lambda x, y: cli.open_window(LIST_WINDOW) if x > 1180 else cli.focus(mark=3)
    log = []
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), task, confirm=lambda _p: False,
                      sleep=lambda _s: None, captures_dir=tmp_path / "captures", wall=lambda: time.time() - 60,
                      vision=vision)
    toolbox._observer.on_event = log.append
    if prepare is not None:
        prepare(toolbox)
    result = run_agent(model, toolbox, task, log=log.append)
    return result, model, log


def has_image(items):
    return "input_image" in json.dumps(items, default=lambda item: getattr(item, "__dict__", str(item)))


def test_split_mode_sends_no_image_to_the_text_model(tmp_path):
    result, model, _ = run(tmp_path, [launch(), observe(), look(), look(), GIVE_UP], vision=FakeVision())
    assert result.status == "gave_up"
    assert not any(has_image(items) for items in model.inputs)


def test_one_model_mode_still_sends_the_picture(tmp_path):
    _, model, _ = run(tmp_path, [launch(), observe(), look(), GIVE_UP])
    assert has_image(model.inputs[-1])


def test_split_mode_ignores_image_mode_for_the_transcript(tmp_path):
    result, model, _ = run(tmp_path, [launch(), observe(), look(), GIVE_UP], vision=FakeVision(),
                           image_mode="tool_output")
    assert result.status == "gave_up" and not any(has_image(items) for items in model.inputs)


def test_split_mode_transcript_drops_any_capture_handed_to_it(tmp_path):
    def hand_out_captures(toolbox):
        toolbox.take_attachments = lambda: list(toolbox._captures.values())

    result, model, _ = run(tmp_path, [launch(), observe(), look(), look(), GIVE_UP], vision=FakeVision(),
                           prepare=hand_out_captures)
    assert result.status == "gave_up" and not any(has_image(items) for items in model.inputs)


def test_split_mode_adds_the_vision_paragraph(tmp_path):
    _, split, _ = run(tmp_path / "split", [launch(), GIVE_UP], vision=FakeVision())
    _, single, _ = run(tmp_path / "single", [launch(), GIVE_UP])
    assert split.instructions == SYSTEM_PROMPT + VISION_PROMPT
    assert single.instructions == SYSTEM_PROMPT
    assert VISION_PROMPT not in SYSTEM_PROMPT and "look" in VISION_PROMPT and "question" in VISION_PROMPT


def test_vision_prompt_is_english_and_names_no_app():
    assert VISION_PROMPT.isascii()


def test_vision_prompt_puts_the_tree_first_and_keeps_the_judgement():
    assert "Read the tree first" in VISION_PROMPT
    assert "find_cells" in VISION_PROMPT
    assert "decide yourself" in VISION_PROMPT
    assert VISION_PROMPT.index("Read the tree first") < VISION_PROMPT.index("Ask the vision model")


def test_vision_tokens_count_toward_the_budget(tmp_path):
    vision = FakeVision(FakeAnswer(tokens=900))
    turns = [launch(), observe(), look(), GIVE_UP]
    result, model, log = run(tmp_path, turns, vision=vision, tokens=200, token_budget=1000)

    assert [tool["name"] for tool in model.tools] == ["finish"]
    assert len(model.inputs) == 4
    assert result.tokens == 4 * 200 + 900
    models = [event for event in log if event["type"] == "model"]
    assert models[-1]["tokens"] == 4 * 200 + 900


def test_without_vision_tokens_the_same_run_stays_under_the_budget(tmp_path):
    turns = [launch(), observe(), look(), GIVE_UP]
    _, model, _ = run(tmp_path, turns, tokens=200, token_budget=1000)
    assert "finish" in [tool["name"] for tool in model.tools] and len(model.tools) > 1


def test_vision_calls_do_not_count_toward_the_stage_limit(tmp_path):
    turns = [launch(), observe(), plan(), look(), look(), look(), GIVE_UP]
    split, _, _ = run(tmp_path / "split", turns, vision=FakeVision(FakeAnswer(tokens=10)))
    single, _, _ = run(tmp_path / "single", [list(t) for t in turns])
    assert split.stats["stage_calls"] == single.stats["stage_calls"]
    assert split.stats["vision_calls"] == 3


def test_stats_hold_vision_counts_only_in_split_mode(tmp_path):
    vision = FakeVision(FakeAnswer(tokens=700), FakeAnswer(answer="", marks=[], error="TimeoutError", tokens=0),
                        FakeAnswer(tokens=800))
    split, _, _ = run(tmp_path / "split", [launch(), observe(), look(), look(), look(), GIVE_UP], vision=vision)
    single, _, _ = run(tmp_path / "single", [launch(), observe(), look(), GIVE_UP])

    assert (split.stats["vision_calls"], split.stats["vision_tokens"]) == (3, 1500)
    assert "vision_calls" not in single.stats and "vision_tokens" not in single.stats


@pytest.mark.parametrize("budget", [10_000])
def test_split_mode_run_with_no_vision_read_reports_zero(tmp_path, budget):
    split, _, _ = run(tmp_path, [launch(), GIVE_UP], vision=FakeVision(), token_budget=budget)
    assert (split.stats["vision_calls"], split.stats["vision_tokens"]) == (0, 0)
