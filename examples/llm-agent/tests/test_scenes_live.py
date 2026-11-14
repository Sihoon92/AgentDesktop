"""Scene tests for observing more, asking a person and learning from the answers, on the real
VirtualMes window. They run only with VIRTUAL_MES_LIVE=1: they open windows and move the cursor.

Each test drives the real Toolbox, run_agent and register_after_run with a model that follows a
fixed script and a person who gives fixed answers. The script reads the harness's latest marks,
refs and captures to pick its targets, the way a model reads the observation text. Success is
judged from the app's own event log and the saved file, never from a tool result.
"""

import functools
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops
from virtual_mes_support import (
    LiveApp,
    find_agent_desktop,
    live,
    prepare_exe,
    read_events,
    read_xlsx,
    requires_exe,
    wait_for_logged,
)

from agent.ask import Answer
from agent.capture import BOX_COLOR
from agent.desktop import DesktopClient, start_session
from agent.frames import frame_origin
from agent.knowledge import KnowledgeStore
from agent.loop import run_agent
from agent.recorder import RunRecorder
from agent.registration import register_after_run
from agent.task import Task
from agent.tools import Toolbox

pytestmark = [requires_exe, live]

MAIN_TITLE = "MES (가상)"
OBSERVE = {"window_id": None, "root_ref": None, "surface": None, "include_bounds": False}
SYSTEM = "virtualmes"
ROW_TOLERANCE_PX = 3
DIALOG_WAIT_S = 15

RIGHT_END_ANSWER = "2번, 칸 오른쪽 끝의 화살표를 눌러야 열려요"
RESOLUTION_DRAFT = {
    "action": "add", "id": None, "exchange": 1, "type": "resolution",
    "situation": "필터 열의 선택 칸 가운데를 눌러도 목록이 열리지 않는다",
    "resolution": "선택 칸은 오른쪽 끝의 화살표를 눌러 연다",
    "task": None, "checks": None,
}
COMPLETION_DRAFT = {
    "action": "add", "id": None, "exchange": None, "type": "completion",
    "situation": None, "resolution": None, "task": "조회 결과를 엑셀 파일로 저장하는 작업",
    "checks": [{"kind": "file", "path": None, "pattern": "*.xlsx", "role": None, "name": None,
                "name_contains": None}],
}


def call(name, arguments, number):
    return SimpleNamespace(type="function_call", name=name, call_id=f"call_{number}",
                           arguments=json.dumps(arguments, ensure_ascii=False))


class ScriptedModel:
    """Each turn is a function of the scene that returns one (tool, arguments). A registration
    draft request is answered from `drafts` instead, one list of proposed items per request."""

    def __init__(self, scene, turns, drafts=()):
        self.scene = scene
        self.turns = list(turns)
        self.drafts = list(drafts)
        self.inputs = []
        self.draft_inputs = []
        self.count = 0

    def respond(self, instructions, items, tools, effort=None):
        self.count += 1
        if any(tool.get("name") == "propose_entries" for tool in tools):
            self.draft_inputs.append(list(items))
            proposed = self.drafts.pop(0) if self.drafts else []
            output = [call("propose_entries", {"items": proposed}, self.count)] if proposed else []
        else:
            self.inputs.append(list(items))
            assert self.turns, "the scripted model ran out of turns"
            name, arguments = self.turns.pop(0)(self.scene)
            output = [call(name, arguments, self.count)]
        return SimpleNamespace(output=output, usage=SimpleNamespace(total_tokens=10))


class ScriptedAsker:
    """Answers questions in order. An answer may be a function of (question, scene)."""

    interactive = True

    def __init__(self, scene, answers):
        self.scene = scene
        self.answers = list(answers)
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        assert self.answers, f"unexpected question: {question.get('kind')} {question.get('text')}"
        answer = self.answers.pop(0)
        return answer(question, self.scene) if callable(answer) else answer


class EventObserver:
    def __init__(self, events):
        self.events = events

    def on_observation(self, window, envelope, text, source=None, wait=None):
        pass

    def on_action(self, tool, arguments, target, result):
        pass

    def on_event(self, event):
        self.events.append(event)


class Scene:
    """One app copy, one Toolbox, one scripted run."""

    def __init__(self, folder, session, app_args, goal, require=(), answers=(), knowledge=None,
                 image_mode="off"):
        self.folder = Path(folder)
        self.exe, self.app_log = prepare_exe(self.folder, *app_args)
        self.desktop = DesktopClient(str(session.binary), session.id)
        self.task = Task(goal, str(self.exe), 2, list(require), max_steps=40, stage_call_limit=40, image_mode=image_mode,
                         ask_timeout_s=60, system=SYSTEM)
        self.asker = ScriptedAsker(self, answers)
        self.knowledge = knowledge
        self.events = []
        self.memo = {}
        self.toolbox = Toolbox(self.desktop, self.task, confirm=lambda _prompt: False,
                               observer=EventObserver(self.events), asker=self.asker, knowledge=knowledge,
                               captures_dir=self.folder / "captures")
        self.model = None
        self.result = None

    def run(self, turns, drafts=(), run_id="scene"):
        self.model = ScriptedModel(self, turns, drafts)
        register = None
        if self.knowledge is not None:
            register = functools.partial(register_after_run, store=self.knowledge, run_id=run_id)
        self.result = run_agent(self.model, self.toolbox, self.task, self.events.append, register=register)
        return self.result

    def close(self):
        pid = self.toolbox._pid
        if pid is None:
            return
        subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self._alive(pid):
            time.sleep(0.1)
        if self._alive(pid):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

    @staticmethod
    def _alive(pid):
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                                encoding="oem", errors="replace")
        return str(pid) in listed.stdout

    def app_events(self, prefix=""):
        return [event for event in read_events(self.app_log) if event["event"].startswith(prefix)]

    def last_app_event(self, name):
        return [event for event in read_events(self.app_log) if event["event"] == name][-1]

    def tool_events(self, name=None):
        return [event for event in self.events
                if event.get("type") == "tool" and (name is None or event["tool"] == name)]

    def last_result(self):
        return self.tool_events()[-1]["result"]

    def events_of(self, kind):
        return [event for event in self.events if event.get("type") == kind]

    def mark(self, name, role=None):
        """The first element of the latest observation with exactly this name."""
        for element in self.toolbox._marks.values():
            if element.get("name") == name and (role is None or element.get("role") == role):
                return element
        raise AssertionError(f"no mark named {name!r} in the latest observation")

    def ref(self, name, role=None):
        for ref, element in self.toolbox._refs.items():
            if element.get("name") == name and (role is None or element.get("role") == role):
                return ref
        raise AssertionError(f"no ref named {name!r} in the latest observation")

    def ref_inside(self, element, name):
        """The ref named `name` whose bounds lie inside the element's bounds, such as a box's arrow."""
        outer = element["bounds"]
        for ref, inner in self.toolbox._refs.items():
            box = inner.get("bounds")
            if inner.get("name") == name and box and _inside(box, outer):
                return ref
        raise AssertionError(f"no ref named {name!r} inside {element.get('name')!r}")

    def row_under(self, label):
        """The elements on the first row below the label, left to right, such as a filter's boxes."""
        anchor = self.mark(label)
        top = anchor["bounds"]
        below = [e for e in self.toolbox._marks.values()
                 if e.get("bounds") and e["bounds"]["y"] >= top["y"] + top["height"] - ROW_TOLERANCE_PX
                 and e is not anchor]
        first = min(e["bounds"]["y"] for e in below)
        row = [e for e in below if abs(e["bounds"]["y"] - first) <= ROW_TOLERANCE_PX]
        outer = [e for e in row if not any(o is not e and _inside(e["bounds"], o["bounds"]) for o in row)]
        return sorted(outer, key=lambda e: e["bounds"]["x"])

    def main_window(self):
        return next(row for row in self.toolbox._rows() if row.get("title") == MAIN_TITLE)

    def new_window(self):
        """The window the last action opened, else any window of the app other than the main one."""
        opened = (self.last_result().get("effects") or {}).get("windows_opened") or []
        if opened:
            return opened[0]["id"]
        main = self.main_window()["id"]
        return next(row["id"] for row in self.toolbox._rows() if row["id"] != main)

    def window_titles(self):
        return [row.get("title") or "" for row in self.toolbox._rows()]

    def native_node(self, window_id, native_id, role=None):
        envelope = self.desktop.run("snapshot", "--window-id", window_id, "--compact", "--include-bounds",
                                    "--timeout-ms", "8000")
        if not envelope.get("ok"):
            return None
        return _find_native(envelope["data"]["tree"], native_id, role)

    def ref_by_native(self, window_id, native_id, role=None):
        """The ref in the latest observation of the element with this automation id, matched by bounds."""
        node = self.native_node(window_id, native_id, role)
        assert node is not None, f"no element with automation id {native_id!r} in {window_id}"
        for ref, element in self.toolbox._refs.items():
            if element.get("bounds") == node.get("bounds") and element.get("role") == node.get("role"):
                return ref
        raise AssertionError(f"automation id {native_id!r} is not in the latest observation; observe it first")

    def remember(self, key, value):
        self.memo[key] = value
        return value

    def image_point(self, capture_id, x, y):
        shot = self.toolbox._captures[capture_id]
        return int(x - shot.origin[0]), int(y - shot.origin[1])


def _find_native(node, native_id, role):
    if (node.get("native_id") or {}).get("value") == native_id and (role is None or node.get("role") == role):
        return node
    for child in node.get("children") or []:
        found = _find_native(child, native_id, role)
        if found is not None:
            return found
    return None


def _inside(inner, outer, slack=ROW_TOLERANCE_PX):
    """True when `inner` lies within `outer`, give or take the pixel or two that scaling rounds off."""
    return (outer["x"] - slack <= inner["x"]
            and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + slack
            and outer["y"] - slack <= inner["y"]
            and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + slack
            and inner["width"] * inner["height"] < outer["width"] * outer["height"])


def center(bounds):
    return bounds["x"] + bounds["width"] // 2, bounds["y"] + bounds["height"] // 2


def click(ref, expect, intent, knowledge_id=None):
    return "click", {"ref": ref, "intent": intent, "expect": expect, "knowledge_id": knowledge_id}


def part(element, where, expect, intent, knowledge_id=None, action="click"):
    return "click_part", {"mark": element["mark"], "part": where, "action": action, "intent": intent,
                          "expect": expect, "knowledge_id": knowledge_id}


def point(capture_id, xy, expect, intent):
    return "click_point", {"capture": capture_id, "x": xy[0], "y": xy[1], "action": "click", "intent": intent,
                           "expect": expect, "knowledge_id": None}


def candidate(label, element=None, capture_id=None, xy=(None, None)):
    return {"label": label, "mark": element["mark"] if element else None, "capture": capture_id,
            "x": xy[0], "y": xy[1]}


def ask(kind, text, candidates, proposed=None, basis=()):
    return "ask", {"kind": kind, "text": text, "candidates": candidates, "proposed": proposed, "basis": list(basis)}


def finish(evidence, saved_path=None):
    return "finish", {"success": True, "evidence": evidence, "saved_path": saved_path, "completion_id": None}


def planning(s):
    """A one-stage plan: these scenes test asking and learning, not how the model splits a goal."""
    return "plan", {"stages": [{"goal": s.task.goal, "done_when": "지시한 결과가 화면에 보인다"}]}


def finishing(evidence, saved_path=None):
    """Closes the one stage with a mark of the latest observation, then claims success."""
    return [
        lambda s: ("stage_done", {"marks": [min(s.toolbox._marks)], "capture": None, "note": evidence}),
        lambda s: finish(evidence, saved_path=saved_path),
    ]


def wait_for(text, timeout_ms=15000):
    return "wait_for_text", {"text": text, "timeout_ms": timeout_ms}


def opening(screen):
    """Launch, open the 생산운영 list, pick the screen, and wait until its query button shows."""
    return [
        lambda s: ("launch_target", {}),
        lambda s: ("observe", OBSERVE),
        planning,
        lambda s: click(s.ref("생산운영", "button"), "window_opened", "생산운영 메뉴 목록을 연다"),
        lambda s: ("observe", {**OBSERVE, "window_id": s.new_window()}),
        lambda s: click(s.ref(screen), "window_closed", f"{screen} 화면을 연다"),
        lambda s: wait_for("조회(F5)"),
    ]


def querying(first_row_text):
    return [
        lambda s: click(s.ref("조회(F5)", "button"), "any", "조회한다"),
        lambda s: wait_for(first_row_text),
    ]


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DESKTOP_HOME", str(tmp_path / "state"))
    binary = find_agent_desktop()
    session_id = start_session(str(binary), "observe-ask-learn scenes")
    assert session_id, "agent-desktop session start failed"
    return SimpleNamespace(binary=binary, id=session_id)


@pytest.fixture
def scenes(tmp_path, session):
    made = []

    def make(name, *args, **kwargs):
        scene = Scene(tmp_path / name, session, *args, **kwargs)
        made.append(scene)
        return scene

    yield make
    for scene in made:
        scene.close()


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "knowledge", SYSTEM)


def period_scene_turns():
    """Scene 2's run after the screen is open: the box's middle twice, a refused try, the stuck
    question, the right end, finish."""
    return [
        lambda s: part(s.mark("전일"), "center", "window_opened", "기간 칸을 눌러 목록을 연다"),
        lambda s: part(s.mark("전일"), "center", "window_opened", "기간 칸을 한 번 더 눌러 본다"),
        lambda s: part(s.mark("전일"), "right", "window_opened", "칸의 오른쪽 끝을 눌러 본다"),
        lambda s: ask("stuck", "기간 칸을 눌러도 목록이 열리지 않습니다. 무엇을 눌러야 하나요?",
                      [candidate("기간 칸의 가운데", s.mark("전일")), candidate("기간 칸의 오른쪽 끝 화살표")]),
        lambda s: part(s.mark("전일"), "right", "window_opened", "답대로 칸의 오른쪽 끝 화살표를 누른다"),
        *finishing("기간 목록이 열렸다"),
    ]


def test_scene_1_center_click_fails_then_the_arrow_opens_the_list(scenes):
    scene = scenes("scene1", ("--profile", "easy", "--combo-open", "arrow"), "Shop 3번째 칸의 목록을 열어줘",
                   require=[{"role": "checkbox", "name": "WA0"}])

    result = scene.run([
        *opening("전공정 생산현황"),
        lambda s: part(s.row_under("Shop")[2], "center", "window_opened", "Shop 3번째 칸을 눌러 목록을 연다"),
        lambda s: click(s.ref_inside(s.row_under("Shop")[2], "열기"), "window_opened",
                        "목록이 열리지 않아 칸 안의 화살표 버튼을 누른다"),
        *finishing("Shop 목록이 열렸다"),
    ])

    assert result.status == "verified", result.reason
    assert [e["event"] for e in scene.app_events("combo_")] == ["combo_body_clicked", "combo_arrow_clicked"]
    assert {e["filter"] for e in scene.app_events("combo_")} == {"shop-3"}
    body, arrow = scene.tool_events("click_part")[0]["result"], scene.tool_events("click")[-1]["result"]
    assert body["expected_met"] is False and arrow["expected_met"] is True
    assert scene.asker.questions == []


def test_scene_2_stuck_question_then_the_right_end_opens_the_list(scenes):
    scene = scenes("scene2", ("--combo-open", "arrow"), "기간 목록을 열어줘",
                   require=[{"role": "statictext", "name": "전주"}], answers=[Answer(text=RIGHT_END_ANSWER)])

    result = scene.run([*opening("전공정 생산현황"), *period_scene_turns()])

    assert result.status == "verified", result.reason
    parts = [event["result"] for event in scene.tool_events("click_part")]
    assert [p.get("expected_met") for p in parts[:2]] == [False, False]
    assert parts[2]["refused_code"] == "blocked_until_question"
    assert parts[3]["expected_met"] is True
    assert [q["kind"] for q in scene.asker.questions] == ["stuck"]
    assert [e["event"] for e in scene.app_events("combo_")] == [
        "combo_body_clicked", "combo_body_clicked", "combo_arrow_clicked"]
    assert scene.last_app_event("combo_arrow_clicked")["filter"] == "period"


def period_box_offset(folder):
    """Where the period box sits inside the main window. The screen is laid out the same in every
    exposure, so a copy that exposes the filters as refs measures it for a pixel-exposed copy."""
    with LiveApp(folder, "--expose", "filters=ref") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("전공정 생산현황")["ref"])
        app.wait_for_event("screen_opened")
        box = app.wait_for_node(lambda n: n.get("id") == "filter-period", "the period box")["bounds"]
        main = next(w["bounds"] for w in app.windows() if w["id"] == app.main())
    return {"x": box["x"] - main["x"], "y": box["y"] - main["y"], "width": box["width"], "height": box["height"]}


def test_scene_2_pin_answer_on_a_pixel_filter(scenes, tmp_path):
    offset = period_box_offset(tmp_path / "reference")

    def box_on_screen(s):
        main = s.main_window()["bounds"]
        return {"x": main["x"] + offset["x"], "y": main["y"] + offset["y"],
                "width": offset["width"], "height": offset["height"]}

    def arrow_on_screen(s):
        box = box_on_screen(s)
        return box["x"] + box["width"] - box["height"] // 2, box["y"] + box["height"] // 2

    def pin_on_the_arrow(question, s):
        return Answer(text="여기를 누르세요", pin=arrow_on_screen(s))

    scene = scenes("scene2pin", ("--expose", "filters=pixel", "--combo-open", "arrow"), "기간 목록을 열어줘",
                   answers=[pin_on_the_arrow, Answer(text="맞아요", accepted=True)], image_mode="user_message")

    def capture_id(s):
        return s.memo["capture"]

    def look_result(s):
        s.memo["capture"] = s.last_result()["capture"]
        middle = center(box_on_screen(s))
        return point(capture_id(s), s.image_point(capture_id(s), *middle), "window_opened",
                     "캡처에서 기간 칸의 가운데를 누른다")

    result = scene.run([
        *opening("전공정 생산현황"),
        lambda s: ("look", {"mark": None}),
        look_result,
        lambda s: ask("confirm", "캡처에서 고른 이 지점이 기간 칸이 맞나요?",
                      [candidate("기간 칸의 가운데", capture_id=capture_id(s),
                                 xy=s.image_point(capture_id(s), *center(box_on_screen(s))))], proposed=1),
        lambda s: point(capture_id(s), s.image_point(capture_id(s), *arrow_on_screen(s)), "window_opened",
                        "답으로 찍은 지점을 누른다"),
        *finishing("기간 목록 창이 열렸다"),
    ])

    assert result.status == "confirmed", result.reason
    refused = scene.tool_events("click_point")[0]["result"]
    assert refused["refused_code"] == "needs_confirmation"
    assert scene.asker.questions[0]["kind"] == "confirm"
    assert [e["event"] for e in scene.app_events("combo_")] == ["combo_arrow_clicked"]
    assert scene.last_app_event("combo_arrow_clicked")["filter"] == "period"


def test_scene_3_confirm_among_three_boxes_then_open_the_chosen_one(scenes):
    scene = scenes("scene3", (), "Shop 칸에서 믹싱을 빼줘",
                   require=[{"role": "statictext", "name": "3개"}], answers=[Answer(text="3번")])

    result = scene.run([
        *opening("전공정 생산현황"),
        lambda s: ask("confirm", "Shop 칸이 셋입니다. 어느 칸에서 믹싱을 뺄까요?",
                      [candidate(f"Shop {n}번째 칸", box) for n, box in enumerate(s.row_under("Shop"), 1)],
                      proposed=3, basis=[s.mark("Shop")["mark"]]),
        lambda s: part(s.row_under("Shop")[2], "right", "window_opened", "답한 3번째 Shop 칸을 오른쪽 끝 화살표로 연다"),
        lambda s: ("observe", {**OBSERVE, "window_id": s.new_window()}),
        lambda s: part(s.mark("WA0"), "center", None, "WA0 행의 체크를 푼다. 글자만 보여 바뀐 것은 트리에 나오지 않는다"),
        lambda s: part(s.mark("Select"), "center", "window_closed", "Select로 선택을 적용한다"),
        *finishing("Shop 3번째 칸이 3개로 바뀌었다"),
    ])

    assert result.status == "verified", result.reason
    question = scene.asker.questions[0]
    assert question["kind"] == "confirm" and len(question["candidates"]) == 3 and question["proposed"] == 3
    changed = scene.last_app_event("filter_changed")
    assert (changed["filter"], changed["value"]) == ("shop-3", ["WA1", "WA2", "WA3"])


def cell_scene_turns():
    """Find the 극판 subtotal's 양품수, be refused, ask with the candidates, click the first."""
    def candidates(s):
        return s.memo["cells"]

    def remember_cells(s):
        s.memo["cells"] = s.last_result()["candidates"]
        return part(s.toolbox._marks[candidates(s)[0]["mark"]], "center", "window_opened",
                    "극판 소계 행의 양품수 셀을 누른다")

    return [
        lambda s: ("find_cells", {"rows": ["극판", "소계"], "columns": ["양품수"]}),
        remember_cells,
        lambda s: ask("confirm", "극판 양품수는 극판 소계 행의 이 셀이 맞나요?",
                      [candidate(" · ".join(c["row_context"]) or f"후보 {n}", s.toolbox._marks[c["mark"]])
                       for n, c in enumerate(candidates(s), 1)],
                      proposed=1, basis=[*candidates(s)[0]["row_marks"], *candidates(s)[0]["column_marks"]]),
        lambda s: part(s.toolbox._marks[candidates(s)[0]["mark"]], "center", "window_opened",
                       "답한 1번 셀을 눌러 상세 팝업을 연다"),
        *finishing("생산 상세 팝업이 열렸다"),
    ]


def test_scene_4_cell_is_confirmed_then_clicked(scenes):
    scene = scenes("scene4", ("--delay-ms", "200"), "극판 양품수를 눌러서 들어가줘",
                   require=[{"role": "statictext", "name": "일자"}], answers=[Answer(text="1번")])

    result = scene.run([*opening("공정별 생산집계"), *querying("극판"), *cell_scene_turns()])

    assert result.status == "verified", result.reason
    cells = scene.tool_events("find_cells")[0]["result"]["candidates"]
    assert len(cells) == 3, cells
    assert scene.tool_events("click_part")[0]["result"]["refused_code"] == "needs_confirmation"
    clicked = wait_for_logged(scene.app_log, "cell_clicked")
    assert clicked["row"] == ["극판", "소계", ""]
    assert clicked["column"] == ["생산", "양품수"]
    assert len(scene.app_events("cell_clicked")) == 1


def save_dialog(s):
    """Waits for the standard save dialog (its file-name box has automation id 1001 in every UI
    language) and observes it."""
    deadline = time.monotonic() + DIALOG_WAIT_S
    while time.monotonic() < deadline:
        main = s.main_window()["id"]
        for row in s.toolbox._rows():
            if row["id"] != main and s.native_node(row["id"], "1001", "textfield") is not None:
                s.memo["dialog"] = row["id"]
                return "observe", {**OBSERVE, "window_id": row["id"]}
        time.sleep(0.3)
    raise AssertionError("the save dialog did not open")


def saving_turns(path):
    return [
        lambda s: click(s.ref("엑셀 저장"), "window_opened", "엑셀 아이콘을 누른다"),
        lambda s: ("observe", {**OBSERVE, "window_id": s.remember("choice", s.new_window())}),
        lambda s: click(s.ref_by_native(s.memo["choice"], "choice-save"), "window_closed", "저장을 고른다"),
        save_dialog,
        lambda s: ("type", {"ref": s.ref_by_native(s.memo["dialog"], "1001", "textfield"), "text": str(path),
                            "intent": "저장할 경로를 입력한다", "expect": "value_changed", "knowledge_id": None}),
        lambda s: click(s.ref_by_native(s.memo["dialog"], "1", "button"), "window_closed", "저장 버튼을 누른다"),
        *finishing("엑셀 파일을 저장했다", saved_path=str(path)),
    ]


def test_scene_5_saved_file_is_confirmed_and_registered(scenes, store, tmp_path):
    target = tmp_path / "saved.xlsx"
    approve_all = Answer(items=({"n": 1, "decision": "approve"},))
    scene = scenes("scene5", ("--expose", "icon=ref", "--delay-ms", "200"), "전공정 생산현황을 조회해서 엑셀로 저장해줘",
                   answers=[Answer(text="맞아요", accepted=True), approve_all], knowledge=store)

    result = scene.run([*opening("전공정 생산현황"), *querying("2026-11-11"), *saving_turns(target)],
                       drafts=[[COMPLETION_DRAFT]], run_id="scene5")

    assert result.status == "confirmed", result.reason
    saved = wait_for_logged(scene.app_log, "export_saved")
    assert Path(saved["path"]) == target and target.stat().st_size > 0
    assert read_xlsx(target)[0][0] == "일자"
    assert [q["kind"] for q in scene.asker.questions] == ["completion", "registration"]
    [entry] = store.entries()
    assert entry["type"] == "completion" and entry["checks"] == [{"kind": "file", "pattern": "*.xlsx"}]
    assert entry["source"]["run_id"] == "scene5"
    assert scene.events_of("registration")[-1]["saved"][0]["action"] == "add"


def test_scene_6_second_run_cites_knowledge_and_asks_nothing(scenes, store):
    approve_all = Answer(items=({"n": 1, "decision": "approve"},))
    first = scenes("scene6-first", ("--combo-open", "arrow"), "기간 목록을 열어줘",
                   require=[{"role": "statictext", "name": "전주"}],
                   answers=[Answer(text=RIGHT_END_ANSWER), approve_all], knowledge=store)
    assert first.run([*opening("전공정 생산현황"), *period_scene_turns()], drafts=[[RESOLUTION_DRAFT]],
                     run_id="scene6-first").status == "verified"
    first.close()
    [learned] = store.entries()
    assert learned["source"]["answer"] == RIGHT_END_ANSWER

    second = scenes("scene6-second", ("--combo-open", "arrow"), "기간 목록을 열어줘",
                    require=[{"role": "statictext", "name": "전주"}], knowledge=store)
    result = second.run([
        *opening("전공정 생산현황"),
        lambda s: part(s.mark("전일"), "right", "window_opened", "지난 답대로 칸의 오른쪽 끝 화살표를 누른다",
                       knowledge_id=learned["id"]),
        *finishing("기간 목록이 열렸다"),
    ], run_id="scene6-second")

    assert result.status == "verified", result.reason
    assert second.asker.questions == []
    assert learned["id"] in json.dumps(second.model.inputs[0], ensure_ascii=False, default=str)
    assert [e["id"] for e in second.events_of("knowledge_used")] == [learned["id"]]
    assert [e["event"] for e in second.app_events("combo_")] == ["combo_arrow_clicked"]
    assert store.get(learned["id"])["uses"] == 1


def test_scene_7_wrong_knowledge_is_counted_and_asked_again(scenes, store):
    wrong = store.add({"type": "resolution", "situation": "필터 열의 선택 칸이 열리지 않는다",
                       "resolution": "선택 칸은 가운데를 누르면 열린다"})
    fixed = {**RESOLUTION_DRAFT, "action": "update", "id": wrong["id"]}
    approve_all = Answer(items=({"n": 1, "decision": "approve"},))
    scene = scenes("scene7", ("--combo-open", "arrow"), "기간 목록을 열어줘",
                   require=[{"role": "statictext", "name": "전주"}],
                   answers=[Answer(text=RIGHT_END_ANSWER), approve_all], knowledge=store)

    result = scene.run([
        *opening("전공정 생산현황"),
        lambda s: part(s.mark("전일"), "center", "window_opened", "지난 답대로 칸의 가운데를 누른다",
                       knowledge_id=wrong["id"]),
        lambda s: part(s.mark("전일"), "right", "window_opened", "칸의 오른쪽 끝을 눌러 본다"),
        lambda s: ask("stuck", "지난 답대로 했는데 목록이 열리지 않습니다. 무엇을 눌러야 하나요?",
                      [candidate("기간 칸의 가운데", s.mark("전일")), candidate("기간 칸의 오른쪽 끝 화살표")]),
        lambda s: part(s.mark("전일"), "right", "window_opened", "답대로 칸의 오른쪽 끝 화살표를 누른다"),
        *finishing("기간 목록이 열렸다"),
    ], drafts=[[fixed]], run_id="scene7")

    assert result.status == "verified", result.reason
    parts = [event["result"] for event in scene.tool_events("click_part")]
    assert parts[0]["expected_met"] is False
    assert parts[1]["refused_code"] == "blocked_until_question"
    assert [e["id"] for e in scene.events_of("knowledge_failed")] == [wrong["id"]]
    corrected = store.get(wrong["id"])
    assert corrected["failures"] == 1 and corrected["resolution"] == RESOLUTION_DRAFT["resolution"]
    assert corrected["source"]["answer"] == RIGHT_END_ANSWER
    assert scene.events_of("registration")[-1]["saved"] == [
        {"id": wrong["id"], "type": "resolution", "action": "update"}]


def test_image_point_lands_on_the_cell_at_this_dpi(scenes):
    scene = scenes("dpi", ("--delay-ms", "200"), "극판 코팅 소계의 양품수를 눌러줘",
                   require=[{"role": "statictext", "name": "일자"}], answers=[Answer(text="1번")],
                   image_mode="user_message")

    def cell_point(s):
        return tuple(s.memo["cell"]["point"])

    def remember_cell(s):
        [s.memo["cell"]] = s.last_result()["candidates"]
        return "look", {"mark": None}

    def press_from_the_capture(s):
        s.memo["capture"] = s.last_result()["capture"]
        return point(s.memo["capture"], s.image_point(s.memo["capture"], *cell_point(s)), "window_opened",
                     "캡처에서 고른 셀을 누른다")

    result = scene.run([
        *opening("공정별 생산집계"),
        *querying("극판"),
        lambda s: ("find_cells", {"rows": ["코팅", "소계"], "columns": ["양품수"]}),
        remember_cell,
        press_from_the_capture,
        lambda s: ask("confirm", "캡처에서 고른 이 셀이 코팅 소계의 양품수가 맞나요?",
                      [candidate("코팅 소계 양품수", capture_id=s.memo["capture"],
                                 xy=s.image_point(s.memo["capture"], *cell_point(s)))], proposed=1),
        lambda s: point(s.memo["capture"], s.image_point(s.memo["capture"], *cell_point(s)), "window_opened",
                        "답한 1번 지점을 누른다"),
        *finishing("생산 상세 팝업이 열렸다"),
    ])

    assert result.status == "verified", result.reason
    assert scene.tool_events("click_point")[0]["result"]["refused_code"] == "needs_confirmation"
    clicked = wait_for_logged(scene.app_log, "cell_clicked")
    assert clicked["row"] == ["극판", "코팅", "소계"] and clicked["column"] == ["생산", "양품수"]


def _same_share(picture, part, offset, drawn=None):
    """The share of `part`'s pixels that `picture` shows unchanged when `part` sits at `offset`.
    A capture has mark boxes drawn over it: pixels the picture shows in the `drawn` colour are
    left out of the count."""
    left, top = round(offset[0]), round(offset[1])
    region = picture.crop((left, top, left + part.width, top + part.height)).convert("RGB")
    differs = ImageChops.difference(region, part.convert("RGB")).convert("L").point(lambda v: 255 if v > 8 else 0)
    total = part.width * part.height
    if drawn is not None:
        boxes = ImageChops.difference(region, Image.new("RGB", region.size, drawn)).convert("L")
        boxes = boxes.point(lambda v: 255 if v <= 8 else 0)
        total -= boxes.histogram()[255]
        differs = ImageChops.subtract(differs, boxes)
    return (total - differs.histogram()[255]) / total


def test_an_open_popup_window_is_in_the_capture_and_in_the_frame(scenes, tmp_path):
    scene = scenes("popup", (), "생산운영 메뉴 목록을 열어줘", image_mode="user_message",
                   require=[{"role": "button", "name": "전공정 생산현황"}])

    result = scene.run([
        lambda s: ("launch_target", {}),
        lambda s: ("observe", OBSERVE),
        planning,
        lambda s: click(s.ref("생산운영", "button"), "window_opened", "생산운영 메뉴 목록을 연다"),
        lambda s: (s.remember("popup", s.new_window()), ("look", {"mark": None}))[1],
        *finishing("메뉴 목록이 열렸다"),
    ])

    assert result.status == "verified", result.reason
    popup_id = scene.memo["popup"]
    main = scene.main_window()
    popup = next(row for row in scene.toolbox._rows() if row["id"] == popup_id)
    own = tmp_path / "popup-alone.png"
    assert scene.desktop.run("screenshot", "--window-id", popup_id, str(own)).get("ok")
    with Image.open(own) as alone:
        alone = alone.convert("RGB")
    popup_origin = frame_origin(popup["bounds"], alone.width, alone.height)

    shot = scene.toolbox._captures[scene.tool_events("look")[-1]["result"]["capture"]]
    assert [window_id for window_id, _ in shot.windows] == [main["id"], popup_id] and shot.missing == 0
    with Image.open(shot.path) as picture:
        offset = (popup_origin[0] - shot.origin[0], popup_origin[1] - shot.origin[1])
        assert shot.drawn and _same_share(picture, alone, offset, drawn=BOX_COLOR) > 0.95

    snapshot = scene.desktop.run("snapshot", "--window-id", main["id"], "--compact", "--include-bounds",
                                 "--timeout-ms", "8000")
    recorder = RunRecorder(tmp_path / "runs", scene.desktop, {"goal": "popup"})
    try:
        recorder.on_observation(main, snapshot, "t")
    finally:
        recorder.close()
    observation = [json.loads(line) for line in (recorder.path / "events.jsonl").read_text(encoding="utf-8").splitlines()][0]
    assert recorder.errors == [] and observation["warning"] is None and observation["frame"]
    item = next(e for e in observation["elements"] if e["name"] == "전공정 생산현황")
    assert item["rect"] is not None
    with Image.open(recorder.path / "frames" / observation["frame"]) as frame:
        assert (frame.width, frame.height) == (observation["image"]["width"], observation["image"]["height"])
        main_origin = frame_origin(main["bounds"], *_screenshot_size(scene, main["id"], tmp_path))
        frame_left = main_origin[0] + min(0, round(popup_origin[0] - main_origin[0]))
        frame_top = main_origin[1] + min(0, round(popup_origin[1] - main_origin[1]))
        offset = (popup_origin[0] - frame_left, popup_origin[1] - frame_top)
        assert _same_share(frame, alone, offset) > 0.95


def test_a_click_inside_an_open_list_keeps_observing_the_main_window_and_shows_the_list(scenes, tmp_path):
    scene = scenes("popup-click", ("--profile", "easy", "--combo-open", "arrow"), "Shop 3번째 칸에서 WA0을 골라줘",
                   image_mode="user_message", require=[{"role": "checkbox", "name": "WA0"}])

    result = scene.run([
        *opening("전공정 생산현황"),
        lambda s: click(s.ref_inside(s.row_under("Shop")[2], "열기"), "window_opened", "Shop 3번째 칸의 목록을 연다"),
        lambda s: (s.remember("popup", s.new_window()),
                   part(s.mark("WA0"), "center", "value_changed", "목록 안의 WA0을 누른다"))[1],
        lambda s: ("look", {"mark": None}),
        *finishing("WA0을 눌렀다"),
    ])

    assert result.status == "verified", result.reason
    main, popup_id = scene.main_window(), scene.memo["popup"]
    popup = next(row for row in scene.toolbox._rows() if row["id"] == popup_id)
    pressed = scene.tool_events("click_part")[-1]["result"]
    assert pressed["ok"] and pressed["window_id"] == main["id"], pressed
    assert "observed_window" not in pressed["effects"] and "WA0" in pressed["observation"], pressed
    assert pressed["effects"]["removed"]["count"] == 0, pressed["effects"]
    assert scene.toolbox._marks_view == (main["id"], True)

    own = tmp_path / "list-alone.png"
    assert scene.desktop.run("screenshot", "--window-id", popup_id, str(own)).get("ok")
    with Image.open(own) as alone:
        alone = alone.convert("RGB")
    popup_origin = frame_origin(popup["bounds"], alone.width, alone.height)
    shot = scene.toolbox._captures[scene.tool_events("look")[-1]["result"]["capture"]]
    assert shot.window_id == main["id"] and [window_id for window_id, _ in shot.windows] == [main["id"], popup_id]
    with Image.open(shot.path) as picture:
        offset = (popup_origin[0] - shot.origin[0], popup_origin[1] - shot.origin[1])
        assert _same_share(picture, alone, offset, drawn=BOX_COLOR) > 0.95

    snapshot = scene.desktop.run("snapshot", "--window-id", popup_id, "--compact", "--include-bounds",
                                 "--timeout-ms", "8000")
    recorder = RunRecorder(tmp_path / "runs", scene.desktop, {"goal": "popup-click"})
    try:
        recorder.on_observation(popup, snapshot, "t")
    finally:
        recorder.close()
    observation = [json.loads(line) for line in (recorder.path / "events.jsonl").read_text(encoding="utf-8").splitlines()][0]
    assert recorder.errors == [] and observation["frame"]
    with Image.open(recorder.path / "frames" / observation["frame"]) as frame:
        main_origin = frame_origin(main["bounds"], *_screenshot_size(scene, main["id"], tmp_path))
        frame_left = popup_origin[0] + min(0, round(main_origin[0] - popup_origin[0]))
        frame_top = popup_origin[1] + min(0, round(main_origin[1] - popup_origin[1]))
        assert _same_share(frame, alone, (popup_origin[0] - frame_left, popup_origin[1] - frame_top)) > 0.95


def _unnamed_ref_inside(scene, element):
    """The ref of an unnamed element inside `element`, such as a filter box's bare arrow."""
    for ref, inner in scene.toolbox._refs.items():
        box = inner.get("bounds")
        if not inner.get("name") and box and _inside(box, element["bounds"]):
            return ref
    raise AssertionError(f"no unnamed ref inside {element.get('name')!r}")


def test_unchecking_a_list_row_that_shows_no_check_state_is_unconfirmed_not_a_mismatch(scenes):
    scene = scenes("picture-verdict", ("--combo-open", "arrow"), "Shop 3번째 칸에서 WA2와 WA3을 빼줘",
                   image_mode="user_message", require=[{"role": "button", "name": "조회(F5)"}])

    result = scene.run([
        *opening("전공정 생산현황"),
        lambda s: click(_unnamed_ref_inside(s, s.row_under("Shop")[2]), "window_opened", "Shop 3번째 칸의 목록을 연다"),
        lambda s: part(s.mark("WA2"), "center", "value_changed", "WA2의 체크를 해제한다"),
        lambda s: part(s.mark("WA3"), "center", "value_changed", "WA3의 체크를 해제한다"),
        lambda s: part(s.mark("Select"), "center", "window_closed", "고른 대로 적용하고 목록을 닫는다"),
        *finishing("WA2와 WA3의 체크를 해제하고 적용했다"),
    ])

    assert result.status == "verified", result.reason
    pressed = [event["result"] for event in scene.tool_events("click_part")]
    assert pressed[2]["expected_met"] is True, pressed[2]
    for outcome in pressed[:2]:
        effects = outcome["effects"]
        assert not (effects["windows_opened"] or effects["windows_closed"] or effects["changed"]), effects
        assert effects["added"]["count"] == 0 and effects["removed"]["count"] == 0, effects
        assert outcome["expected_met"] is None and outcome["verdict"] == "unconfirmed", outcome
        assert outcome["effects"]["picture"]["changed"] is True, outcome["effects"]
    assert [e["reason"] for e in scene.events_of("capture")].count("unconfirmed") == 2
    assert scene.asker.questions == []
    assert not [e for e in scene.tool_events() if (e["result"] or {}).get("refused_code") == "blocked_until_question"]
    changed = wait_for_logged(scene.app_log, "filter_changed")
    assert changed["filter"] == "shop-3" and changed["closed_by"] == "select_button", changed
    assert not {"WA2", "WA3"} & set(changed["value"]), changed


def _screenshot_size(scene, window_id, folder):
    path = folder / f"{window_id}-alone.png"
    shot = scene.desktop.run("screenshot", "--window-id", window_id, str(path))
    assert shot.get("ok")
    return shot["data"]["width"], shot["data"]["height"]
