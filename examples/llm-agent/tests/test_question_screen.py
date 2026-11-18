"""What answering a question does to the screen the harness judges by.

A person answers in the viewer, which takes activation from the app; a popup list that closes on
deactivation is gone by the time the answer arrives. The harness must notice that instead of
judging the next action against the marks it read before the question.
"""

import copy
import time

from fake_cli import FakeCli, scene_window
from test_tools import EXE, LIST_WINDOW, MAIN, MAIN_BOUNDS, OBSERVE, EventObserver, part_call

from agent.ask import Answer
from agent.desktop import DesktopClient
from agent.knowledge import KnowledgeStore
from agent.task import Task
from agent.tools import Toolbox

REOPENED = scene_window("w-3", "목록", LIST_WINDOW["bounds"])
ARROW_REF = "@s1:e7"
POPUP_NODE = {"role": "window", "name": "목록", "bounds": LIST_WINDOW["bounds"], "children": [
    {"role": "statictext", "name": "WA2", "bounds": {"x": 1110, "y": 600, "width": 60, "height": 20}},
    {"role": "statictext", "name": "WA3", "bounds": {"x": 1110, "y": 630, "width": 60, "height": 20}},
]}
SCENE = {
    "windows": [MAIN],
    "tree": {"role": "window", "name": "메인", "bounds": MAIN_BOUNDS, "children": [
        {"role": "statictext", "name": "Shop", "bounds": {"x": 1010, "y": 520, "width": 60, "height": 24}},
        {"role": "button", "name": "", "bounds": {"x": 1180, "y": 520, "width": 24, "height": 24},
         "ref_id": ARROW_REF, "available_actions": ["Click"]},
    ]},
}
STUCK = {"kind": "stuck", "text": "어떻게 할까요?", "candidates": [], "proposed": None, "basis": []}


class PopupScene:
    """The main window's tree holds the list's items only while the list window is open, as a UIA
    tree that reaches an owned popup does."""

    def __init__(self, cli):
        self.cli = cli

    def open(self, row=LIST_WINDOW):
        self.cli.open_window(row)
        self.cli.scene["tree"]["children"].append(copy.deepcopy(POPUP_NODE))

    def close(self):
        for row in list(self.cli.scene["windows"]):
            if row["title"] == "목록":
                self.cli.close_window(row["id"])
        children = self.cli.scene["tree"]["children"]
        children[:] = [node for node in children if node.get("role") != "window"]


class Asker:
    """Answers every question; `during` runs while the person is answering."""

    interactive = True

    def __init__(self, during=None):
        self.during = during
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        if self.during is not None:
            self.during()
        return Answer(text="계속", accepted=True)


def build(tmp_path, asker, knowledge=None, image_mode="off"):
    cli = FakeCli(scene=SCENE)
    popup = PopupScene(cli)
    observer = EventObserver()
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(
        DesktopClient("ad.exe", "run1", runner=cli),
        Task("goal", EXE, 2, [], image_mode=image_mode),
        confirm=lambda _prompt: True,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
        observer=observer,
        asker=asker,
        knowledge=knowledge,
        captures_dir=tmp_path / "captures",
        wall=lambda: time.time() - 60,
    )
    toolbox.call("launch_target", {})
    popup.open()
    toolbox.call("observe", OBSERVE)
    return toolbox, cli, popup, observer


def mark_named(toolbox, name):
    return next(number for number, element in toolbox._marks.items() if element.get("name") == name)


def resolution(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge", "scene")
    entry = store.add({"type": "resolution", "situation": "목록에서 필요 없는 항목이 선택돼 있다",
                       "resolution": "목록을 열고 필요 없는 항목의 체크를 해제한다"})
    return store, entry


def answers(observer):
    return [event for event in observer.events if event["type"] == "answer"]


def test_a_list_that_closes_while_the_person_answers_is_reported_to_the_model(tmp_path):
    asker = Asker()
    toolbox, _, popup, observer = build(tmp_path, asker)
    asker.during = popup.close

    result = toolbox.call("ask", STUCK)

    assert result["screen_changed"]["windows_closed"] == [{"id": "w-2", "title": "목록"}]
    assert result["screen_changed"]["windows_opened"] == []
    assert "Observe again" in result["screen_changed"]["note"]
    assert answers(observer)[-1]["windows_changed"] == {"windows_opened": [], "windows_closed": ["w-2"]}
    assert toolbox.take_question_changes() is None


def test_marks_from_before_the_question_are_dropped_when_a_window_closed(tmp_path):
    asker = Asker()
    toolbox, cli, popup, _ = build(tmp_path, asker, image_mode="user_message")
    stale_mark = mark_named(toolbox, "WA2")
    asker.during = popup.close

    toolbox.call("ask", STUCK)
    clicks_before = cli.command_names().count("mouse-click")

    refused = toolbox.call("click_part", part_call(mark=stale_mark, part="center", expect="value_changed"))
    look = toolbox.call("look", {"mark": None})

    assert "not in the latest observation" in refused["refused"]
    assert cli.command_names().count("mouse-click") == clicks_before
    assert look["ok"] and look["drawn"] is False


def test_a_question_that_changes_no_window_keeps_the_marks_but_the_next_action_reads_afresh(tmp_path):
    asked, _, _, observer = build(tmp_path / "asked", Asker())
    plain, plain_cli, _, _ = build(tmp_path / "plain", Asker())
    asked_cli = asked._desktop._runner

    result = asked.call("ask", STUCK)
    asked_before = asked_cli.command_names().count("snapshot")
    plain_before = plain_cli.command_names().count("snapshot")
    for toolbox in (asked, plain):
        toolbox.call("click_part", part_call(mark=mark_named(toolbox, "Shop"), part="center", expect="any"))

    assert "screen_changed" not in result and "windows_changed" not in answers(observer)[-1]
    assert mark_named(asked, "WA2")
    assert asked_cli.command_names().count("snapshot") - asked_before == (
        plain_cli.command_names().count("snapshot") - plain_before + 1)


def test_reopening_the_list_after_the_question_is_judged_against_a_fresh_read(tmp_path):
    store, entry = resolution(tmp_path)
    asker = Asker()
    toolbox, cli, popup, observer = build(tmp_path, asker, knowledge=store)
    asker.during = popup.close
    toolbox.call("ask", STUCK)
    cli.on_command = lambda name: popup.open(REOPENED) if name == "click" else None

    result = toolbox.call("click", {"ref": ARROW_REF, "intent": "목록을 다시 연다", "expect": "content_changed",
                                    "knowledge_id": entry["id"]})

    assert result["expected_met"] is True, result
    assert result["effects"]["windows_opened"] == [{"id": "w-3", "title": "목록"}]
    assert result["effects"]["added"]["count"] > 0
    assert store.get(entry["id"])["failures"] == 0
    assert not [event for event in observer.events if event["type"] == "knowledge_failed"]


def test_a_click_that_closes_the_observed_list_is_unconfirmed_and_no_knowledge_failure(tmp_path):
    store, entry = resolution(tmp_path)
    toolbox, cli, popup, observer = build(tmp_path, Asker(), knowledge=store)
    cli.scene["trees"] = {"w-2": copy.deepcopy(POPUP_NODE)}
    toolbox.call("observe", {**OBSERVE, "window_id": "w-2"})
    cli.on_click = lambda _x, _y: popup.close()

    result = toolbox.call("click_part", part_call(mark=mark_named(toolbox, "WA2"), part="center",
                                                  expect="value_changed", knowledge_id=entry["id"]))

    assert result["effects"]["observed_window"] == {"before": "w-2", "after": "w-1"}
    assert result["expected_met"] is None and result["verdict"] == "unconfirmed"
    assert store.get(entry["id"])["failures"] == 0
    assert not [event for event in observer.events if event["type"] == "knowledge_failed"]
    assert toolbox.precheck("click", {"ref": ARROW_REF, "intent": "연다", "expect": "any",
                                      "knowledge_id": None}) is None
