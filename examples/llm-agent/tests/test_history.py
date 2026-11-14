import copy
import json
import random
from types import SimpleNamespace

import pytest
from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem

from agent.history import Transcript
from agent.images import IMAGE_MODES, image_items

FIRST = {"role": "user", "content": "지시\n\n배운 것"}
SUMMARY = "단계 1 끝: 결과 · 메모 · 동작들"


class CountingPath:
    """Stands in for a capture's path and counts how often the image file is read."""

    def __init__(self, data=b"\x89PNG-test"):
        self.data = data
        self.reads = 0

    def read_bytes(self):
        self.reads += 1
        return self.data


def shot(capture_id):
    return SimpleNamespace(id=capture_id, width=40, height=30, drawn=True, cropped=False, path=CountingPath())


def call(name, call_id, as_dict=False):
    if as_dict:
        return {"type": "function_call", "name": name, "arguments": "{}", "call_id": call_id}
    return ResponseFunctionToolCall(type="function_call", name=name, arguments="{}", call_id=call_id)


def reasoning(item_id, as_dict=False):
    if as_dict:
        return {"type": "reasoning", "id": item_id, "summary": []}
    return ResponseReasoningItem(id=item_id, type="reasoning", summary=[])


def observed(text="줄 하나\n줄 둘\n줄 셋", **extra):
    return {"window_id": "w1", "complete": True, "observation": text, **extra}


def acted(text="새 화면", expected_met=True):
    return {"ok": True, "effects": {"windows_opened": [], "none": False}, "expect": "any_change",
            "expected_met": expected_met, **observed(text)}


REFUSED = {"ok": False, "refused": "막힘", "refused_code": "blocked_until_question"}


def turn(transcript, name, call_id, output, captures=(), extra=()):
    transcript.add_response([*extra, call(name, call_id)])
    transcript.add_result(call_id, name, output, list(captures))


def outputs(view):
    return [item for item in view if isinstance(item, dict) and item.get("type") == "function_call_output"]


def output_of(view, call_id):
    item = next(item for item in outputs(view) if item["call_id"] == call_id)
    return json.loads(item["output"] if isinstance(item["output"], str) else item["output"][0]["text"])


def images(view):
    found = []
    for item in view:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(item, dict) and item.get("type") == "function_call_output" and isinstance(item["output"], list):
            content = item["output"]
        if isinstance(content, list):
            found += [part for part in content if part.get("type") == "input_image"]
    return found


def image_owners(view):
    owners = []
    for index, item in enumerate(view):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call_output" and isinstance(item["output"], list):
            owners.append(item["call_id"])
        if item.get("role") == "user" and isinstance(item.get("content"), list):
            owners.append(view[index - 1]["call_id"])
    return owners


def kind(item):
    return item.get("type") if isinstance(item, dict) else getattr(item, "type", None)


def field(item, name):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


@pytest.mark.parametrize("mode", IMAGE_MODES)
def test_a_short_transcript_equals_what_the_loop_sends_today(mode):
    look_shot, question_shot = shot("c1"), shot("c2")
    script = [
        ([reasoning("rs_1"), call("launch_target", "a")], [("a", "launch_target", {"ok": True}, [])]),
        ([call("observe", "b", as_dict=True)], [("b", "observe", observed(), [])]),
        ([reasoning("rs_2", as_dict=True), call("look", "c"), call("find_cells", "d")],
         [("c", "look", {"ok": True, "capture": "c1"}, [look_shot]), ("d", "find_cells", {"cells": []}, [])]),
        ([call("ask", "e")], [("e", "ask", {"accepted": True, "text": "그거"}, [])]),
        ([call("click", "f")], [("f", "click", {**REFUSED, "capture": "c2"}, [question_shot])]),
    ]
    transcript = Transcript(FIRST, mode)
    items = [FIRST]
    for output, results in script:
        transcript.add_response(output)
        items += output
        for call_id, name, result, captures in results:
            transcript.add_result(call_id, name, result, captures)
            items += image_items(call_id, result, captures, mode)

    assert transcript.view() == items


def test_only_the_latest_observation_is_sent_in_full():
    transcript = Transcript(FIRST, "off")
    turn(transcript, "observe", "a", observed("가\n나\n다"))
    turn(transcript, "click", "b", acted("라\n마"))
    turn(transcript, "observe", "c", observed("최신 화면"))

    view = transcript.view()

    assert output_of(view, "a")["observation"] == "(earlier observation omitted: 3 lines)"
    assert output_of(view, "b")["observation"] == "(earlier observation omitted: 2 lines)"
    assert output_of(view, "c")["observation"] == "최신 화면"


def test_an_omitted_observation_keeps_its_effects():
    transcript = Transcript(FIRST, "off")
    first = acted("오래된 화면", expected_met=False)
    kept = copy.deepcopy(first)
    turn(transcript, "click", "a", first)
    turn(transcript, "observe", "b", observed())

    old = output_of(transcript.view(), "a")

    assert old == {**kept, "observation": "(earlier observation omitted: 1 lines)"}
    assert first == kept


def test_images_of_older_observations_are_not_sent():
    transcript = Transcript(FIRST, "user_message")
    turn(transcript, "click", "a", {**acted(expected_met=False), "capture": "c1"}, [shot("c1")])
    turn(transcript, "click", "b", {**acted(expected_met=None), "verdict": "unconfirmed", "capture": "c2"},
         [shot("c2")])

    view = transcript.view()

    assert image_owners(view) == ["b"]
    assert len(images(view)) == 1
    assert [kind(item) for item in view[1:]] == ["function_call", "function_call_output",
                                                  "function_call", "function_call_output", None]


def test_a_result_after_the_latest_observation_keeps_its_image():
    transcript = Transcript(FIRST, "tool_output")
    turn(transcript, "observe", "a", observed())
    turn(transcript, "click", "b", {**REFUSED, "capture": "c1"}, [shot("c1")])

    assert image_owners(transcript.view()) == ["b"]


def test_a_look_image_stays_until_the_app_changes():
    transcript = Transcript(FIRST, "user_message")
    turn(transcript, "observe", "a", observed())
    turn(transcript, "look", "b", {"ok": True, "capture": "c1"}, [shot("c1")])
    turn(transcript, "observe", "c", observed("다시 본 화면"))
    turn(transcript, "find_cells", "d", {"cells": []})

    assert image_owners(transcript.view()) == ["b"]

    turn(transcript, "click", "e", acted())

    assert image_owners(transcript.view()) == []


def test_a_refused_action_does_not_drop_the_look_image():
    transcript = Transcript(FIRST, "tool_output")
    turn(transcript, "observe", "a", observed())
    turn(transcript, "look", "b", {"ok": True, "capture": "c1"}, [shot("c1")])
    turn(transcript, "click", "c", REFUSED)
    turn(transcript, "observe", "d", observed("새로 봄"))

    assert image_owners(transcript.view()) == ["b"]


def test_questions_and_answers_are_never_trimmed():
    transcript = Transcript(FIRST, "off")
    question = {"accepted": True, "text": "두 번째 칸", "observation": "질문 때의 화면"}
    turn(transcript, "observe", "a", observed())
    turn(transcript, "ask", "b", question)
    asked = [item for item in transcript.view() if field(item, "call_id") == "b"]
    turn(transcript, "click", "c", acted())
    turn(transcript, "observe", "d", observed())
    transcript.close_stage(SUMMARY)
    turn(transcript, "observe", "e", observed())

    view = transcript.view()

    assert [item for item in view if field(item, "call_id") == "b"] == asked
    assert output_of(view, "b") == question
    assert {field(item, "call_id") for item in view} - {None} == {"b", "e"}


def test_the_plan_turn_survives_closing_a_stage():
    transcript = Transcript(FIRST, "off")
    turn(transcript, "launch_target", "a", {"ok": True})
    turn(transcript, "observe", "b", observed())
    turn(transcript, "plan", "c", {"ok": True, "revision": 0}, extra=[reasoning("rs_plan")])
    turn(transcript, "click", "d", acted())

    transcript.close_stage(SUMMARY)
    turn(transcript, "observe", "e", observed())
    view = transcript.view()

    assert [field(item, "call_id") for item in view if kind(item) == "function_call"] == ["c", "e"]
    assert view[1].id == "rs_plan"
    assert output_of(view, "c") == {"ok": True, "revision": 0}


def test_a_turn_mixing_a_plan_with_an_action_is_kept_whole():
    transcript = Transcript(FIRST, "off")
    transcript.add_response([call("plan", "a"), call("click", "b")])
    transcript.add_result("a", "plan", {"ok": True}, [])
    transcript.add_result("b", "click", acted(), [])

    transcript.close_stage(SUMMARY)

    assert {item["call_id"] for item in outputs(transcript.view())} == {"a", "b"}


def test_closing_a_stage_replaces_its_turns_with_one_note():
    transcript = Transcript(FIRST, "user_message")
    turn(transcript, "observe", "a", observed("단계 일의 화면"), [shot("c1")])
    turn(transcript, "click", "b", acted("단계 일의 결과"), extra=[reasoning("rs_1")])
    transcript.add_response([call("stage_done", "c")])
    transcript.close_stage(SUMMARY)
    transcript.add_result("c", "stage_done", {"ok": True, "stage": 1}, [])

    turn(transcript, "observe", "d", observed("단계 이의 화면"))

    assert transcript.view()[:2] == [FIRST, {"role": "user", "content": SUMMARY}]
    transcript.close_stage("단계 2 끝")
    turn(transcript, "observe", "e", observed("단계 삼의 화면"))

    view = transcript.view()
    assert view[:3] == [FIRST, {"role": "user", "content": SUMMARY}, {"role": "user", "content": "단계 2 끝"}]
    assert output_of(view, "e")["observation"] == "단계 삼의 화면"
    assert "단계 일" not in json.dumps([item for item in view if isinstance(item, dict)], ensure_ascii=False)


def test_a_note_is_a_user_message_that_survives_closing():
    transcript = Transcript(FIRST, "off")
    turn(transcript, "observe", "a", observed())
    transcript.add_note("오른쪽 화살표를 누르세요")
    turn(transcript, "click", "b", acted())
    transcript.close_stage(SUMMARY)
    turn(transcript, "observe", "c", observed())

    assert transcript.view()[:3] == [FIRST, {"role": "user", "content": "오른쪽 화살표를 누르세요"},
                                     {"role": "user", "content": SUMMARY}]


def test_reasoning_items_leave_with_their_turn():
    transcript = Transcript(FIRST, "off")
    dropped, kept = reasoning("rs_drop"), reasoning("rs_keep", as_dict=True)
    turn(transcript, "click", "a", acted(), extra=[dropped])
    transcript.add_response([kept, call("ask", "b", as_dict=True)])
    transcript.add_result("b", "ask", {"accepted": True, "text": "네"}, [])
    turn(transcript, "observe", "c", observed(), extra=[reasoning("rs_later")])

    before = transcript.view()
    assert dropped in before and kept in before

    transcript.close_stage(SUMMARY)
    after = transcript.view()

    assert dropped not in after
    assert kept in after
    assert after.index(kept) + 1 == after.index(next(item for item in after if field(item, "call_id") == "b"))
    assert [field(item, "id") for item in after if kind(item) == "reasoning"] == ["rs_keep", "rs_later"]

    turn(transcript, "observe", "d", observed())

    assert [field(item, "id") for item in transcript.view() if kind(item) == "reasoning"] == ["rs_keep"]


def close_after_an_action(transcript):
    turn(transcript, "observe", "a", observed("처음 화면"))
    transcript.add_response([reasoning("rs_b"), call("click", "b"), call("look", "b2")])
    transcript.add_result("b", "click", acted("누른 뒤 화면\n둘째 줄"), [])
    transcript.add_result("b2", "look", {"ok": True, "capture": "c1"}, [shot("c1")])
    transcript.add_response([call("stage_done", "c")])
    transcript.close_stage(SUMMARY)
    transcript.add_result("c", "stage_done", {"ok": True, "stage": 1}, [])


def test_the_latest_observation_survives_closing_its_stage():
    transcript = Transcript(FIRST, "user_message")
    close_after_an_action(transcript)

    view = transcript.view()

    assert [field(item, "id") or field(item, "call_id") or item.get("content") for item in view[1:]
            if kind(item) != "function_call_output" and not isinstance(field(item, "content"), list)] == [
        "rs_b", "b", "b2", SUMMARY]
    assert [item["call_id"] for item in outputs(view)] == ["b", "b2"]
    assert output_of(view, "b")["observation"] == "누른 뒤 화면\n둘째 줄"
    assert image_owners(view) == ["b2"]
    assert view[-1] == {"role": "user", "content": SUMMARY}
    assert "처음 화면" not in json.dumps([item for item in view if isinstance(item, dict)], ensure_ascii=False)


def test_a_newer_observation_drops_the_closed_turn_that_was_kept():
    transcript = Transcript(FIRST, "user_message")
    close_after_an_action(transcript)
    turn(transcript, "observe", "d", observed("다음 단계의 화면"))

    view = transcript.view()

    assert view[:2] == [FIRST, {"role": "user", "content": SUMMARY}]
    assert [field(item, "call_id") for item in view if kind(item) == "function_call"] == ["d"]
    assert [field(item, "id") for item in view if kind(item) == "reasoning"] == []
    assert output_of(view, "d")["observation"] == "다음 단계의 화면"


def test_image_mode_off_sends_no_image():
    transcript = Transcript(FIRST, "off")
    capture = shot("c1")
    turn(transcript, "observe", "a", observed())
    turn(transcript, "look", "b", {"ok": True, "capture": "c1"}, [capture])

    view = transcript.view()

    assert images(view) == []
    assert all(not (isinstance(item, dict) and item.get("role") == "user") for item in view[1:])
    assert capture.path.reads == 0


def test_view_is_the_same_every_time_and_reads_each_image_once():
    transcript = Transcript(FIRST, "user_message")
    first, second = shot("c1"), shot("c2")
    turn(transcript, "observe", "a", observed())
    turn(transcript, "look", "b", {"ok": True, "capture": "c1"}, [first])
    turn(transcript, "click", "c", {**REFUSED, "capture": "c2"}, [second])

    views = [transcript.view() for _ in range(3)]

    assert views[0] == views[1] == views[2]
    assert (first.path.reads, second.path.reads) == (1, 1)
    views[0].clear()
    assert transcript.view() == views[1]


def test_results_are_not_changed_by_trimming():
    transcript = Transcript(FIRST, "user_message")
    first = acted("원래 화면")
    kept = copy.deepcopy(first)
    turn(transcript, "click", "a", first, [shot("c1")])
    turn(transcript, "observe", "b", observed())
    transcript.view()
    transcript.close_stage(SUMMARY)
    transcript.view()

    assert first == kept


def test_a_result_without_its_call_is_refused():
    transcript = Transcript(FIRST, "off")
    transcript.add_response([call("observe", "a")])
    with pytest.raises(ValueError):
        transcript.add_result("zzz", "observe", observed(), [])


def test_an_unknown_image_mode_is_refused():
    with pytest.raises(ValueError):
        Transcript(FIRST, "inline")


NAMES = ("observe", "click", "refused", "look", "find_cells", "ask", "plan", "stage_done", "launch_target")


def random_result(rng, name, call_id):
    captures = [shot(f"cap-{call_id}")] if rng.random() < 0.4 else []
    if name == "refused":
        return "click", dict(REFUSED), captures
    if name in ("observe", "find_cells"):
        return name, observed(f"{call_id}\n" * rng.randint(1, 4)), captures
    if name == "click":
        return name, acted(f"{call_id} 화면", rng.choice([True, False, None])), captures
    if name == "look":
        return name, {"ok": True, "capture": f"cap-{call_id}"}, captures
    return name, {"ok": True}, captures


def assert_paired(view):
    calls = [field(item, "call_id") for item in view if kind(item) == "function_call"]
    answered = [item["call_id"] for item in outputs(view)]
    assert sorted(calls) == sorted(answered)
    assert len(set(calls)) == len(calls)
    positions = {field(item, "call_id"): index for index, item in enumerate(view) if kind(item) == "function_call"}
    for index, item in enumerate(view):
        if kind(item) == "function_call_output":
            assert positions[item["call_id"]] < index
        if isinstance(item, dict) and item.get("role") == "user" and isinstance(item.get("content"), list):
            assert kind(view[index - 1]) == "function_call_output"


@pytest.mark.parametrize("seed", range(200))
def test_every_call_keeps_its_output_and_every_output_its_call(seed):
    rng = random.Random(seed)
    transcript = Transcript(FIRST, rng.choice(IMAGE_MODES))
    number = 0
    for _ in range(rng.randint(1, 30)):
        roll = rng.random()
        if roll < 0.12:
            transcript.close_stage(f"요약 {number}")
            assert_paired(transcript.view())
            continue
        if roll < 0.17:
            transcript.add_note("사람의 안내")
            continue
        planned = []
        for _ in range(rng.choice([1, 1, 1, 2, 3])):
            number += 1
            planned.append((rng.choice(NAMES), f"call_{number}"))
        output = [call("click" if name == "refused" else name, call_id, as_dict=rng.random() < 0.3)
                  for name, call_id in planned]
        if rng.random() < 0.5:
            output.insert(0, reasoning(f"rs_{number}", as_dict=rng.random() < 0.3))
        transcript.add_response(output)
        for name, call_id in planned:
            if name == "stage_done" and rng.random() < 0.7:
                transcript.close_stage(f"단계 끝 {call_id}")
            tool, result, captures = random_result(rng, name, call_id)
            transcript.add_result(call_id, tool, result, captures)
        assert_paired(transcript.view())
