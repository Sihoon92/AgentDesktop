"""Run folders for the workflow tests: a few hand-made events (write_run), and one whole run
recorded by the real loop, toolbox and recorder against a FakeCli scene (list_run).

list_run is the shortened flow of the workflow design (section 6). Its action numbers, as
agent.run_record.read_record counts them (LIST_ACTIONS):

   1 launch_target
   2 click "전공정 생산현황"                       main screen opens
   3 click the nameless button of the 기간 row    list window opens (the value shows 전일)
   4 click_part "전주" in that list               the list closes, the value becomes 전주
   5 click the 기간 button again                  list window opens
   6 click_part "전일"                            the list closes, the value is 전일 again
   7 click the 3rd nameless button of the Shop row   list window: All, Select, WA0 to WA3
   8 mouse_click in the list                       refused: a raw point needs a question
   9 click_part "WA2"                              unconfirmed: only the picture changed
  10 click_part "WA3"                              unconfirmed: only the picture changed
  11 click_part "Select"                           the list closes
  12 click "조회(F5)"                              a busy window opens, the table fills;
                                                   the busy window is gone at the next observe
  13 click "엑셀"                                  a save dialog opens as its own window
  14 type the saved path into "파일 이름"           (observed in the dialog)
  15 click "저장"                                  the dialog closes, the file exists

Actions 3 to 6 are a detour that ends where it began: 전일 and 전주 are the value of the 기간
row, so each is missing from some main-window observation, while the label 기간 is always
there. The plan's params are shop="WA0, WA1" and period="전일"; the final screen still shows
전일, so a workflow keeps period fixed.
"""

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fake_cli import FakeCli, scene_window, strip_globals
from test_loop import ScriptedModel, call

from agent.ask import Answer
from agent.desktop import DesktopClient
from agent.loop import run_agent
from agent.recorder import RunRecorder
from agent.task import Task
from agent.tools import Toolbox

LIST_GOAL = "전공정 생산현황에서 기간은 전일, Shop은 WA0, WA1만 골라 조회하고 엑셀로 저장해줘"
LIST_SYSTEM = "mes"
LIST_EXE = r"C:\apps\Mes.exe"
LIST_PARAMS = [{"name": "shop", "value": "WA0, WA1"}, {"name": "period", "value": "전일"}]
LIST_RUN_ID = "20261119-101500"
LIST_ACTIONS = ("launch_target", "click", "click", "click_part", "click", "click_part", "click", "mouse_click",
                "click_part", "click_part", "click_part", "click", "click", "type", "click")
MAIN_ID = "w-main"
SAVE_ID = "w-save"
BUSY_ID = "w-busy"
MAIN_BOUNDS = {"x": 100, "y": 100, "width": 1200, "height": 900}
SAVE_BOUNDS = {"x": 400, "y": 300, "width": 600, "height": 300}
OBSERVE_MAIN = {"window_id": None, "root_ref": None, "surface": None, "include_bounds": False}
CHECKED = (40, 120, 200)
UNCHECKED = (250, 250, 250)
SHOP_ITEMS = ("WA0", "WA1", "WA2", "WA3")
AUTO_SETTLE_READS = 2
UNVERIFIED_COMMANDS = ("click", "type", "mouse-click")
RUN_KEYS = ("exe", "model", "started_by", "max_steps", "code", "vision")


def element(mark, role, name=None, scope=None, ref=None, rect=None, **extra):
    """One recorded element as RunRecorder writes it into an observation's elements."""
    return {"kind": "control" if ref else "text", "ref": ref, "role": role, "name": name,
            "description": extra.pop("description", None), "states": list(extra.pop("states", [])),
            "value": extra.pop("value", None), "mark": mark, "scope": scope, "rect": rect, **extra}


def write_run(tmp_path, events, status="confirmed", goal="g", system="sys", level=2):
    """A run folder under tmp_path/runs with run.json (RunRecorder's keys) and events.jsonl
    holding `events` one per line. Each call makes a new folder."""
    runs = Path(tmp_path) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    n = 0
    while (runs / f"20261119-1000{n:02d}").exists():
        n += 1
    run_id = f"20261119-1000{n:02d}"
    path = runs / run_id
    path.mkdir()
    run = {"format": 2, "run_id": run_id, "goal": goal, **{key: None for key in RUN_KEYS},
           "permission_level": level, "system": system, "started_at": "2026-11-19T10:00:00Z",
           "finished_at": "2026-11-19T10:05:00Z", "heartbeat_at": "2026-11-19T10:05:00Z", "pid": 1,
           "process_started_at": None, "status": status, "reason": "r", "tokens": 0, "steps": len(events)}
    (path / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    lines = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    (path / "events.jsonl").write_text(lines, encoding="utf-8")
    return path


def list_run(tmp_path, before_finish=None, later_turns=()):
    """Records the list flow (module docstring) with the real loop, toolbox and recorder, and
    returns the run folder. The saved file is written under tmp_path/out. The run ends
    confirmed; the recorder must have written it without errors.

    before_finish(model, result, run_dir, log), when given, runs where run.py makes a workflow:
    after the loop and before recorder.finish, with the run's model and the recorder's log, and
    returns the result to finish with. later_turns are the model's answers after the loop's."""
    tmp_path = Path(tmp_path)
    saved = tmp_path / "out" / "front.xlsx"
    app = ListApp(saved)
    desktop = DesktopClient("ad.exe", "run1", runner=app.run)
    meta = {"goal": LIST_GOAL, "exe": LIST_EXE, "model": "gpt-test", "permission_level": 2,
            "started_by": "run.py", "system": LIST_SYSTEM, "max_steps": 60}
    now = datetime(2026, 11, 19, 10, 15, 0, tzinfo=timezone.utc)
    recorder = RunRecorder(tmp_path / "runs", desktop, meta, run_id=LIST_RUN_ID, now=lambda: now)
    task = Task(LIST_GOAL, LIST_EXE, 2, [], max_steps=60, system=LIST_SYSTEM)
    toolbox = Toolbox(desktop, task, confirm=lambda _p: False, sleep=lambda _s: None, observer=recorder,
                      asker=_Accepting(), captures_dir=recorder.path / "captures", wall=lambda: 0.0)
    try:
        model = ScriptedModel(_turns(app, str(saved)) + list(later_turns))
        result = run_agent(model, toolbox, task, recorder.on_event)
        if before_finish is not None:
            result = before_finish(model, result, recorder.path, recorder.on_event)
        recorder.finish(result)
    finally:
        recorder.close()
    if recorder.errors or result.status != "confirmed":
        raise AssertionError(f"list_run did not record cleanly: {result.status} {recorder.errors}")
    return recorder.path


class _Accepting:
    interactive = True

    def ask(self, _question):
        return Answer(text="", accepted=True)


def _node(role, name, x, y, width, height, **extra):
    return {"role": role, "name": name, "bounds": {"x": x, "y": y, "width": width, "height": height}, **extra}


def _button(name, ref, x, y, width=30, height=30):
    return _node("button", name, x, y, width, height, ref_id=ref, available_actions=["Click"])


class ListApp:
    """The app behind list_run: a FakeCli scene whose main tree and window list change as
    each command lands, the way the virtual MES shows its popups (a window element inside the
    main tree and a window of its own in the window list).

    The Shop list keeps its own check marks, shown only in the picture (CHECKED or UNCHECKED
    painted on each item), never in the tree: an item click toggles one, All checks every one,
    Select applies them (`applied`) and closes the list, and the list opens again with the
    applied ones checked. Every Shop item click is kept in `item_clicks`. Save writes the typed
    path, else `saved`. With auto_settle the busy window closes by itself after it has been
    listed AUTO_SETTLE_READS times, as a replay finds it; list_run closes it from its script."""

    def __init__(self, saved, auto_settle=False):
        self.saved = saved
        self.auto_settle = auto_settle
        self.period = "전일"
        self.popup = None
        self.popups = 0
        self.screen = "start"
        self.table = False
        self.busy = False
        self.busy_reads = 0
        self.typed = None
        self.applied = set(SHOP_ITEMS)
        self.checked = set(SHOP_ITEMS)
        self.item_clicks = []
        main = scene_window(MAIN_ID, "MES", MAIN_BOUNDS)
        self.cli = FakeCli(scene={"windows": [main], "tree": self._tree(), "trees": {}})
        self.cli.on_command = self._on_command
        self.cli.on_click = self._on_click

    def run(self, argv, **kwargs):
        """The CLI runner: FakeCli's reply, except that a click, a key-typed text and a
        mouse click answer as the real CLI does, delivered without a postcondition read."""
        name = strip_globals(argv)[0] if argv and argv[0] != "taskkill" else None
        if name == "list-windows" and self.busy and self.auto_settle:
            self.busy_reads += 1
            if self.busy_reads > AUTO_SETTLE_READS:
                self.settle()
        completed = self.cli(argv, **kwargs)
        if name in UNVERIFIED_COMMANDS and completed.returncode == 0:
            envelope = {"version": "2.6", "ok": True, "command": name,
                        "data": {"action": name, "disposition": {"delivery": "delivered_unverified", "retry": "unsafe"}}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(envelope), "")
        return completed

    def _on_command(self, name):
        words = strip_globals(self.cli.calls[-1])
        if name == "click":
            self._clicked(words[1])
        elif name == "type":
            self.typed = words[-1]
            self.cli.scene["trees"][SAVE_ID] = self._dialog(self.typed)
        self._refresh()

    def _clicked(self, ref):
        if ref == "@m:e1":
            self.screen = "status"
        elif ref == "@m:e2":
            self._open_popup("period")
        elif ref == "@m:e5":
            self._open_popup("shop")
        elif ref == "@m:e6":
            self.table = True
            self.busy = True
            self.busy_reads = 0
            self.cli.open_window(scene_window(BUSY_ID, "조회 중", {"x": 500, "y": 400, "width": 300, "height": 120}))
        elif ref == "@m:e7":
            self.cli.open_window(scene_window(SAVE_ID, "다른 이름으로 저장", SAVE_BOUNDS))
            self.cli.scene["trees"][SAVE_ID] = self._dialog("")
        elif ref == "@d:e2":
            target = Path(self.typed) if self.typed else self.saved
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"xlsx")
            self.cli.close_window(SAVE_ID)

    def _on_click(self, x, y):
        hit = next((item for item in self._popup_items() if _inside(item["bounds"], x, y)), None)
        if hit is None:
            return
        name = hit["name"]
        if self.popup == "period":
            self.period = name
            self._close_popup()
        elif name == "Select":
            self.applied = set(self.checked)
            self._close_popup()
        else:
            self.item_clicks.append(name)
            if name == "All":
                self.checked = set(SHOP_ITEMS)
            else:
                self.checked ^= {name}
            self._paint_checks()
        self._refresh()

    def _paint_checks(self):
        items = [item for item in self._popup_items() if item["name"] in SHOP_ITEMS]
        self.cli.screenshot_patches[self._popup_id()] = [
            (item["bounds"], CHECKED if item["name"] in self.checked else UNCHECKED) for item in items]

    def settle(self):
        """The busy window closes by itself before the model observes again."""
        if self.busy:
            self.busy = False
            self.cli.close_window(BUSY_ID)
            self._refresh()

    def _open_popup(self, kind):
        self.popup = kind
        self.popups += 1
        bounds = self._popup_bounds()
        self.cli.open_window(scene_window(self._popup_id(), "목록", bounds))
        if kind == "shop":
            self.checked = set(self.applied)
            self._paint_checks()

    def _close_popup(self):
        self.cli.close_window(self._popup_id())
        self.popup = None

    def _popup_id(self):
        return f"w-list-{self.popups}"

    def _popup_bounds(self):
        if self.popup == "period":
            return {"x": 210, "y": 255, "width": 160, "height": 100}
        return {"x": 290, "y": 305, "width": 200, "height": 160}

    def _popup_items(self):
        if self.popup is None:
            return []
        box = self._popup_bounds()
        left, top = box["x"] + 5, box["y"] + 5
        if self.popup == "period":
            return [_node("statictext", name, left, top + 30 * n, 150, 28) for n, name in enumerate(("당일", "전일", "전주"))]
        items = [_node("statictext", "All", left, top, 80, 28), _node("statictext", "Select", left + 90, top, 90, 28)]
        return items + [_node("statictext", f"WA{n}", left, top + 30 * (n + 1), 150, 28) for n in range(4)]

    def _refresh(self):
        self.cli.scene["tree"] = self._tree()

    def _tree(self):
        children = []
        if self.popup is not None:
            children.append({**_node("window", "목록", **self._popup_bounds()), "children": self._popup_items()})
        if self.busy:
            children.append(_node("window", "조회 중", 500, 400, 300, 120))
        children.append(_button("전공정 생산현황", "@m:e1", 120, 120, 200, 30))
        if self.screen == "status":
            children += [
                _node("statictext", "생산현황 조회", 120, 170, 300, 30),
                _node("statictext", "기간", 120, 220, 80, 30),
                _node("statictext", self.period, 210, 220, 120, 30),
                _button(None, "@m:e2", 335, 220),
                _node("statictext", "Shop", 120, 270, 80, 30),
                _button(None, "@m:e3", 210, 270),
                _button(None, "@m:e4", 250, 270),
                _button(None, "@m:e5", 290, 270),
                _button("조회(F5)", "@m:e6", 120, 330, 150, 40),
                _button("엑셀", "@m:e7", 290, 330, 80, 40),
            ]
        if self.table:
            for row, cells in enumerate((("일자", "Shop", "생산수"), ("2026-11-18", "WA0", "120"),
                                         ("2026-11-18", "WA1", "95"))):
                children += [_node("statictext", text, 120 + 160 * column, 400 + 30 * row, 150, 28)
                             for column, text in enumerate(cells)]
        children.append(_node("statictext", "준비", 120, 960, 300, 30))
        return {"role": "window", "name": "MES", "bounds": dict(MAIN_BOUNDS), "children": children}

    def _dialog(self, typed):
        box = SAVE_BOUNDS
        return {"role": "window", "name": "다른 이름으로 저장", "bounds": dict(box), "children": [
            _node("edit", "파일 이름", box["x"] + 20, box["y"] + 60, 400, 30, ref_id="@d:e1",
                  value=typed or None, available_actions=["SetValue"]),
            _button("저장", "@d:e2", box["x"] + 300, box["y"] + 200, 120, 40),
            _button("취소", "@d:e3", box["x"] + 440, box["y"] + 200, 120, 40),
        ]}


def _inside(bounds, x, y):
    return bounds["x"] <= x < bounds["x"] + bounds["width"] and bounds["y"] <= y < bounds["y"] + bounds["height"]


STAGES = [
    {"goal": "생산현황 조회 화면이 열려 있다", "done_when": "기간과 Shop 줄이 보인다"},
    {"goal": "기간이 전일이다", "done_when": "기간 칸에 전일이 보인다"},
    {"goal": "Shop에 WA0, WA1만 선택되어 있다", "done_when": "목록이 닫히고 선택이 적용되어 있다"},
    {"goal": "조회 결과가 표에 나와 있다", "done_when": "표에 WA0, WA1의 줄이 있다"},
    {"goal": "결과가 엑셀 파일로 저장되어 있다", "done_when": "저장 창이 닫혀 있다"},
]
_ids = iter(range(1_000_000))


def _turn(name, arguments):
    return [call(name, arguments, f"lr{next(_ids)}")]


def _act(name, intent, expect="content_changed", **arguments):
    return _turn(name, {**arguments, "intent": intent, "expect": expect, "knowledge_id": None})


def _latest_observation(items):
    for item in reversed(items):
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            output = json.loads(item["output"])
            if output.get("observation"):
                return output["observation"]
    raise AssertionError("no observation in the conversation")


def _mark(items, role, name):
    """The first [n] of a line with that role and name in the latest observation: a popup's
    lines come first, as its window element leads the main tree."""
    pattern = rf'\[(\d+)\] {role} "{re.escape(name)}"'
    found = re.search(pattern, _latest_observation(items))
    if found is None:
        raise AssertionError(f"{role} {name} is not in the latest observation")
    return int(found.group(1))


def _part(name, intent, expect="content_changed"):
    return lambda items: _act("click_part", intent, expect, mark=_mark(items, "statictext", name),
                              part="center", action="click")


def _done(role, name, note):
    return lambda items: _turn("stage_done", {"marks": [_mark(items, role, name)], "capture": None, "note": note})


def _settled_observe(app):
    def turn(_items):
        app.settle()
        return _turn("observe", OBSERVE_MAIN)
    return turn


def _turns(app, saved):
    return [
        _turn("launch_target", {"intent": "앱을 연다"}),
        _turn("observe", OBSERVE_MAIN),
        _turn("plan", {"stages": STAGES, "params": LIST_PARAMS}),
        _act("click", "전공정 생산현황 화면을 연다", ref="@m:e1"),
        _done("statictext", "Shop", "기간과 Shop 줄이 보인다"),
        _act("click", "기간 목록을 연다", ref="@m:e2"),
        _part("전주", "기간을 고른다"),
        _act("click", "기간 목록을 다시 연다", ref="@m:e2"),
        _part("전일", "기간을 전일로 되돌린다"),
        _done("statictext", "전일", "기간 칸에 전일이 보인다"),
        _act("click", "Shop 셋째 칸의 목록을 연다", ref="@m:e5"),
        _act("mouse_click", "WA2 자리를 누른다", window_id=MAIN_ID, x=300, y=400, button="left"),
        _part("WA2", "WA2를 선택에서 뺀다", "value_changed"),
        _part("WA3", "WA3를 선택에서 뺀다", "value_changed"),
        _part("Select", "선택을 적용한다"),
        _done("statictext", "Shop", "목록이 닫히고 선택이 적용되었다"),
        _act("click", "조회한다", ref="@m:e6"),
        _settled_observe(app),
        _done("statictext", "생산수", "표에 결과가 있다"),
        _act("click", "엑셀로 저장한다", "window_opened", ref="@m:e7"),
        _turn("observe", {**OBSERVE_MAIN, "window_id": SAVE_ID}),
        _act("type", "저장 경로를 넣는다", "value_changed", ref="@d:e1", text=saved),
        _act("click", "저장한다", "window_closed", ref="@d:e2"),
        _done("statictext", "생산수", "저장 창이 닫혔다"),
        _turn("finish", {"success": True, "evidence": "조회 결과를 저장함", "saved_path": saved,
                         "completion_id": None}),
    ]


# A run on menus drawn as pictures (system profile, plan task 6): menu_run and MenuApp.

MENU_SYSTEM = "orders"
MENU_EXE = r"C:\apps\Orders.exe"
MENU_GOAL = "실행 계획 화면을 열어줘"
MENU_RUN_ID = "20261119-111500"
MENU_ID = "w-orders"
MENU_BOUNDS = {"x": 100, "y": 100, "width": 900, "height": 600}
MENU_AREA = {"role": "toolbar", "id": "12"}
MENU_ITEMS = {"56": ("출하 계획 화면", 110), "60": ("실행 계획 화면", 160)}
"""Each menu item's id: the screen it opens and its left edge. Neither has a name, a ref or text nearby."""


class MenuApp:
    """A main window with a toolbar (named only by its id) of two menu items that are pictures:
    image nodes with a native id and nothing else, the way an app may draw its menu. Pressing one
    (a real click inside its box) opens its screen, shown as a text. Every pressed id is kept in
    `pressed`."""

    def __init__(self):
        self.screen = None
        self.pressed = []
        self.cli = FakeCli(scene={"windows": [scene_window(MENU_ID, "Orders", MENU_BOUNDS)], "tree": self._tree()})
        self.cli.on_click = self._on_click

    def run(self, argv, **kwargs):
        name = strip_globals(argv)[0] if argv and argv[0] != "taskkill" else None
        completed = self.cli(argv, **kwargs)
        if name in UNVERIFIED_COMMANDS and completed.returncode == 0:
            envelope = {"version": "2.6", "ok": True, "command": name,
                        "data": {"action": name, "disposition": {"delivery": "delivered_unverified", "retry": "unsafe"}}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(envelope), "")
        return completed

    def _on_click(self, x, y):
        for native, (screen, left) in MENU_ITEMS.items():
            if _inside(self._item_box(left), x, y):
                self.pressed.append(native)
                self.screen = screen
                self.cli.scene["tree"] = self._tree()
                return

    @staticmethod
    def _item_box(left):
        return {"x": left, "y": 145, "width": 40, "height": 30}

    def _tree(self):
        items = [{"role": "image", "native_id": {"kind": "automation_id", "value": native},
                  "bounds": self._item_box(left)} for native, (_screen, left) in MENU_ITEMS.items()]
        children = [
            _node("statictext", "주문 관리", 110, 105, 200, 30),
            {"role": "toolbar", "native_id": {"kind": "automation_id", "value": MENU_AREA["id"]},
             "bounds": {"x": 100, "y": 140, "width": 400, "height": 40}, "children": items},
        ]
        if self.screen is not None:
            children.append(_node("statictext", self.screen, 110, 300, 300, 30))
        return {"role": "window", "name": "Orders", "bounds": dict(MENU_BOUNDS), "children": children}


MENU_STAGES = [{"goal": "실행 계획 화면이 열려 있다", "done_when": "실행 계획 화면 글이 보인다"}]


def _id_mark(items, role, native):
    found = re.search(rf'\[(\d+)\] {role} id="{re.escape(native)}"', _latest_observation(items))
    if found is None:
        raise AssertionError(f"{role} id {native} is not in the latest observation")
    return int(found.group(1))


def _menu_turns():
    return [
        _turn("launch_target", {"intent": "앱을 연다"}),
        _turn("observe", OBSERVE_MAIN),
        _turn("plan", {"stages": MENU_STAGES, "params": []}),
        lambda items: _act("click_part", "실행 계획 메뉴를 누른다", mark=_id_mark(items, "image", "60"),
                           part="center", action="click"),
        _done("statictext", "실행 계획 화면", "실행 계획 화면 글이 보인다"),
        _turn("finish", {"success": True, "evidence": "실행 계획 화면이 열림", "saved_path": None,
                         "completion_id": None}),
    ]


def menu_run(tmp_path, profile, before_finish=None, later_turns=()):
    """Records the menu flow with the real loop, toolbox and recorder, both given `profile`, and
    returns the run folder: launch, observe, plan, click_part on the menu item with id 60, stage
    done, finish. before_finish and later_turns are as list_run's. The run ends confirmed."""
    app = MenuApp()
    desktop = DesktopClient("ad.exe", "run1", runner=app.run)
    meta = {"goal": MENU_GOAL, "exe": MENU_EXE, "model": "gpt-test", "permission_level": 2,
            "started_by": "run.py", "system": MENU_SYSTEM, "max_steps": 20}
    now = datetime(2026, 11, 19, 11, 15, 0, tzinfo=timezone.utc)
    recorder = RunRecorder(Path(tmp_path) / "runs", desktop, meta, run_id=MENU_RUN_ID, now=lambda: now,
                           profile=profile)
    task = Task(MENU_GOAL, MENU_EXE, 2, [], max_steps=20, system=MENU_SYSTEM)
    toolbox = Toolbox(desktop, task, confirm=lambda _p: False, sleep=lambda _s: None, observer=recorder,
                      asker=_Accepting(), captures_dir=recorder.path / "captures", wall=lambda: 0.0, profile=profile)
    try:
        model = ScriptedModel(_menu_turns() + list(later_turns))
        result = run_agent(model, toolbox, task, recorder.on_event)
        if before_finish is not None:
            result = before_finish(model, result, recorder.path, recorder.on_event)
        recorder.finish(result)
    finally:
        recorder.close()
    if recorder.errors or result.status != "confirmed" or app.pressed != ["60"]:
        raise AssertionError(f"menu_run did not record cleanly: {result.status} {recorder.errors} {app.pressed}")
    return recorder.path
