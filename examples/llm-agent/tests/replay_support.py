"""A small fake app for workflow replay tests, on top of FakeCli's scene mode.

FakeApp keeps its own state (which list rows are checked, which filter was applied, the
period, whether the query ran, what was saved, and every press in order) and rebuilds the
scene from it before each CLI reply. Tests check a replay by that state, never by the
replay's own verdict.

The main window ("w-1") holds, in its tree: a menu button, a "Shop" row with a nameless arrow
button, a period row whose current value is a text with the same name as an item of the
period list, a query button, a save button and, after a query, a result table. A popup
(menu, Shop list, period list) is both a row of the window list and a "window" element
inside the main window's tree, as the virtual MES shows them. The query opens a progress
window that closes on its own after query_s seconds of the fake clock; the save button
opens a save window that is read on its own (its own tree, not in the main tree).

The Shop list has "All" and "Select" side by side in a band at its top, as the virtual MES
draws them, and the rows below. The rows are statictexts by default: pressing one flips its
check and the picture of that row in the list window's screenshot, "All" checks every row,
"Select" applies the checked rows and closes the list. With checkboxes=True the rows are
checkboxes with refs whose "checked" state the tree shows.
"""

from dataclasses import dataclass, field
from pathlib import Path

from fake_cli import FakeCli, scene_window

from agent.desktop import DesktopClient
from agent.task import Task
from agent.tools import Toolbox

EXE = r"C:\apps\Mes.exe"
MAIN_ID = "w-1"
MAIN_ORIGIN = (1000, 500)
MAIN_SIZE = (800, 600)
ITEMS = ("WA0", "WA1", "WA2", "WA3")
PERIODS = ("전일", "전주")
CHECKED_COLOR = (20, 20, 20)
FILE_NAME_LABEL = "파일 이름"
STARTS = {"all": set(ITEMS), "none": set(), "some": {"WA1", "WA2"}}


RUNAWAY_S = 3600


class FakeClock:
    """A clock that moves only when the code under test sleeps. A wait past RUNAWAY_S fails the
    test instead of looping forever."""

    def __init__(self, start=100.0):
        self.time = start
        self.slept = 0.0

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds
        self.slept += seconds
        if self.slept > RUNAWAY_S:
            raise AssertionError(f"waited more than {RUNAWAY_S} s of the fake clock")


@dataclass
class AppOptions:
    """What makes a scene differ from the plain one. Each test turns on what it needs."""

    checked: set = field(default_factory=lambda: {"WA2"})
    checkboxes: bool = False
    dead_rows: bool = False
    alert_on_row: bool = False
    duplicate_menu: bool = False
    offscreen_menu: bool = False
    menu_after_s: float | None = None
    menu_open_delay_s: float = 0.0
    failing_menu: bool = False
    sticky_menu: bool = False
    tip_window: bool = False
    late_tip_s: float | None = None
    query_s: float | None = 0.0
    query_delay_s: float = 0.0
    query_changes_nothing: bool = False
    late_popup_element: bool = False
    save_writes_nothing: bool = False
    confirm_on_save: bool = False
    confirm_over_save: bool = False
    quit_on_period_button: bool = False
    move: tuple = (0, 0)
    scale: float = 1.0
    ids: dict = field(default_factory=dict)
    id_area: str | None = None


class FakeApp(FakeCli):
    """FakeCli whose scene comes from the app state. See the module docstring."""

    def __init__(self, clock, options=None):
        self.clock = clock
        self.options = options or AppOptions()
        self.checked = set(self.options.checked)
        self.applied = None
        self.period = PERIODS[0]
        self.queried = False
        self.typed = ""
        self.saved = []
        self.presses = []
        self.open_popups = []
        self.top = []
        self._timers = []
        self._unseen = {}
        self._started = clock.now()
        super().__init__(scene={"windows": [], "tree": {"role": "window"}})
        if self.options.tip_window:
            self.top.append("w-tip")
        if self.options.late_tip_s is not None:
            self._later(self.options.late_tip_s, lambda: self.top.append("w-tip"))
        self._render()

    def _respond(self, words):
        self._run_timers()
        name = words[0]
        handled = self._handle(name, words)
        self._render()
        if handled is not None:
            return handled
        reply = super()._respond(words)
        if name == "snapshot" and self._unseen:
            self._unseen = {popup: left - 1 for popup, left in self._unseen.items() if left > 1}
            self._render()
        return reply

    def _handle(self, name, words):
        if name == "launch":
            envelope = FakeCli._respond(self, words)
            envelope["data"]["window"] = dict(self.scene["windows"][-1])
            return envelope
        if name in ("click", "check", "uncheck", "type", "set-value"):
            return self._press_ref(name, words)
        if name == "press":
            return self._press_key(words[words.index("--window-id") + 1], words[-1])
        if name == "mouse-click":
            xy = next(word for word in words if word.startswith("--xy="))[len("--xy="):]
            self._press_point(*(int(value) for value in xy.split(",")))
            return ok("mouse-click")
        return None

    def _run_timers(self):
        due = [timer for timer in self._timers if timer[0] <= self.clock.now()]
        self._timers = [timer for timer in self._timers if timer[0] > self.clock.now()]
        for _when, action in due:
            action()
        self._render()

    def _later(self, seconds, action):
        if seconds is None:
            return
        if seconds <= 0:
            action()
        else:
            self._timers.append((self.clock.now() + seconds, action))

    def _press_ref(self, name, words):
        ref = words[1]
        element = self._refs_now().get(ref)
        if element is None:
            return failure(name, "STALE_REF")
        label = element["label"]
        if name == "click" and label == "메뉴" and self.options.failing_menu:
            return failure(name, "ACTION_FAILED")
        self.presses.append((name, label))
        if name == "type":
            self.typed = words[-1]
        elif name in ("check", "uncheck") and not self.options.dead_rows:
            (self.checked.add if name == "check" else self.checked.discard)(label)
        elif name == "click":
            self._clicked(label)
        return ok(name)

    def _press_key(self, window_id, combo):
        self.presses.append(("press", combo))
        if window_id == "w-save" and combo == "enter" and "w-save" in self.top:
            if self.options.confirm_over_save:
                self.top.append("w-confirm")
                return ok("press")
            self.top.remove("w-save")
            if self.options.confirm_on_save:
                self.top.append("w-confirm")
            elif not self.options.save_writes_nothing and Path(self.typed).is_absolute():
                Path(self.typed).write_bytes(b"saved")
                self.saved.append(self.typed)
        return ok("press")

    def _press_point(self, x, y):
        for element in self._clickable():
            bounds = element["bounds"]
            if bounds["x"] <= x < bounds["x"] + bounds["width"] and bounds["y"] <= y < bounds["y"] + bounds["height"]:
                self.presses.append(("mouse", element["label"]))
                self._clicked(element["label"])
                return

    def _clicked(self, label):
        if label == "메뉴":
            self._later(self.options.menu_open_delay_s, lambda: self._open("menu"))
        elif label == "현황":
            if self.options.tip_window and "w-tip" in self.top:
                self.top.remove("w-tip")
            if not self.options.sticky_menu:
                self._close("menu")
        elif label == "Shop 열기":
            self._open("list")
        elif label == "All":
            if not self.options.dead_rows:
                self.checked = set(ITEMS)
        elif label in ITEMS:
            if self.options.alert_on_row:
                self.top.append("w-tip")
            if not self.options.dead_rows:
                self.checked ^= {label}
        elif label == "Select":
            self.applied = set(self.checked)
            self._close("list")
        elif label == "기간 선택":
            if self.options.quit_on_period_button:
                self.process_alive = False
            else:
                self._open("period")
        elif label in PERIODS and "period" in self.open_popups:
            self.period = label
            self._close("period")
        elif label == "조회":
            self._start_query()
        elif label == "저장":
            self.top.append("w-save")

    def _start_query(self):
        def finish():
            if "w-prog" in self.top:
                self.top.remove("w-prog")
            self.queried = not self.options.query_changes_nothing

        def show():
            self.top.append("w-prog")
            self._later(self.options.query_s, finish)

        if self.options.query_s == 0:
            finish()
        elif self.options.query_delay_s:
            self._later(self.options.query_delay_s, show)
        else:
            show()

    def _open(self, popup):
        if popup not in self.open_popups:
            self.open_popups.append(popup)
            if self.options.late_popup_element:
                self._unseen[popup] = 1

    def _close(self, popup):
        if popup in self.open_popups:
            self.open_popups.remove(popup)

    def _screen(self, x, y, width, height):
        scale = self.options.scale
        ox, oy = MAIN_ORIGIN[0] + self.options.move[0], MAIN_ORIGIN[1] + self.options.move[1]
        return {"x": round(ox + x * scale), "y": round(oy + y * scale),
                "width": round(width * scale), "height": round(height * scale)}

    def _main_elements(self):
        elements = []
        menu_shown = self.options.menu_after_s is None or self.clock.now() >= self._started + self.options.menu_after_s
        if menu_shown:
            states = ["offscreen"] if self.options.offscreen_menu else []
            elements.append(node("button", "메뉴", self._screen(10, 40, 60, 24), ref="@s1:e1", states=states))
            if self.options.duplicate_menu:
                elements.append(node("button", "메뉴", self._screen(80, 40, 60, 24), ref="@s1:e9"))
        elements += [
            node("button", "조회", self._screen(300, 40, 60, 24), ref="@s1:e4"),
            node("button", "저장", self._screen(370, 40, 60, 24), ref="@s1:e5"),
            node("statictext", "Shop", self._screen(10, 80, 50, 24)),
            node("statictext", None, self._screen(70, 80, 110, 24), value=", ".join(sorted(self.applied or []))),
            node("button", None, self._screen(190, 80, 20, 24), ref="@s1:e2", label="Shop 열기"),
            node("statictext", "기간", self._screen(10, 120, 50, 24)),
            node("statictext", self.period, self._screen(70, 120, 70, 24), label="기간 값"),
            node("button", "기간 선택", self._screen(150, 120, 20, 24), ref="@s1:e3"),
        ]
        if self.queried:
            elements += [node("statictext", "결과", self._screen(10, 400, 60, 24)),
                         node("statictext", "12", self._screen(80, 400, 60, 24))]
        return elements

    def _popup(self, popup):
        if popup == "menu":
            window = self._screen(10, 64, 150, 60)
            return "w-menu", "메뉴 목록", window, [node("button", "현황", self._screen(20, 76, 100, 24), ref="@s1:e6")]
        if popup == "list":
            window = self._screen(70, 104, 150, 200)
            rows = [node("statictext", "All", self._screen(80, 110, 55, 20)),
                    node("statictext", "Select", self._screen(145, 110, 55, 20))]
            for index, item in enumerate(ITEMS):
                bounds = self._screen(80, 134 + 24 * index, 120, 20)
                if self.options.checkboxes:
                    states = ["checked"] if item in self.checked else []
                    rows.append(node("checkbox", item, bounds, ref=f"@s1:e{20 + index}", states=states))
                else:
                    rows.append(node("statictext", item, bounds))
            return "w-list", "목록", window, rows
        window = self._screen(70, 144, 150, 80)
        rows = [node("statictext", period, self._screen(80, 154 + 24 * index, 120, 20))
                for index, period in enumerate(PERIODS)]
        return "w-period", "목록", window, rows

    def _top_window(self, window_id):
        if window_id == "w-save":
            window = self._screen(200, 150, 300, 200)
            children = [node("statictext", FILE_NAME_LABEL, self._screen(210, 200, 70, 24)),
                        node("edit", FILE_NAME_LABEL, self._screen(290, 200, 190, 24), ref="@s1:e30",
                             value=self.typed),
                        node("button", "저장", self._screen(400, 300, 80, 24), ref="@s1:e31", label="저장 창 저장")]
            return "저장", window, children
        if window_id == "w-prog":
            return "진행", self._screen(300, 250, 200, 80), [node("statictext", "조회 중", self._screen(320, 280, 100, 24))]
        if window_id == "w-confirm":
            return "확인", self._screen(250, 250, 200, 100), [node("statictext", "덮어쓸까요?", self._screen(260, 270, 150, 24))]
        return "알림", self._screen(600, 500, 150, 60), [node("statictext", "알림", self._screen(610, 510, 100, 24))]

    def _render(self):
        if not hasattr(self, "scene"):
            return
        children = self._main_children()
        rows = []
        for popup in self.open_popups:
            window_id, title, bounds, items = self._popup(popup)
            rows.insert(0, scene_window(window_id, title, bounds))
            if popup in self._unseen:
                continue
            children.append({"role": "window", "name": title, "bounds": bounds,
                             "children": [strip(item) for item in items]})
        trees = {}
        for window_id in self.top:
            title, bounds, items = self._top_window(window_id)
            trees[window_id] = {"role": "window", "name": title, "bounds": bounds,
                                "children": [strip(item) for item in items]}
            rows.insert(0, scene_window(window_id, title, bounds))
        main_bounds = self._screen(0, 0, *MAIN_SIZE)
        rows.append(scene_window(MAIN_ID, "메인", main_bounds))
        self.scene["windows"] = rows
        self.scene["tree"] = {"role": "window", "name": "메인", "bounds": main_bounds, "children": children}
        self.scene["trees"] = trees
        self._paint_rows()

    def _main_children(self):
        """The main window's children as the tree shows them. A label in options.ids carries that
        native id; with options.id_area those elements sit in a toolbar whose only mark is that id."""
        children, area = [], []
        for element in self._main_elements():
            shown = strip(element)
            native = self.options.ids.get(element["label"])
            if native is None:
                children.append(shown)
                continue
            shown["native_id"] = {"kind": "automation_id", "value": native}
            (area if self.options.id_area is not None else children).append(shown)
        if area:
            children.insert(0, {"role": "toolbar", "bounds": self._screen(0, 30, 260, 80), "children": area,
                                "native_id": {"kind": "automation_id", "value": self.options.id_area}})
        return children

    def _paint_rows(self):
        self.screenshot_patches = {}
        if "list" not in self.open_popups or self.options.checkboxes:
            return
        _window_id, _title, _bounds, items = self._popup("list")
        self.screenshot_patches["w-list"] = [(item["bounds"], CHECKED_COLOR) for item in items
                                             if item["label"] in self.checked]

    def _refs_now(self):
        found = {}
        for element in self._everything():
            if element.get("ref_id"):
                found[element["ref_id"]] = element
        return found

    def _everything(self):
        elements = list(self._main_elements())
        for popup in self.open_popups:
            elements += self._popup(popup)[3]
        for window_id in self.top:
            elements += self._top_window(window_id)[2]
        return elements

    def _clickable(self):
        """Elements a point can hit, topmost first: popups in the order they opened, newest first."""
        elements = []
        for popup in reversed(self.open_popups):
            elements += self._popup(popup)[3]
        return elements + self._main_elements()

    def list_presses(self):
        """The presses on Shop list rows ("All", items, "Select"), in order."""
        return [press for press in self.presses if press[1] in ("All", "Select", *ITEMS)]


def node(role, name, bounds, ref=None, states=None, value=None, label=None):
    element = {"role": role, "bounds": bounds, "label": label or name}
    if name is not None:
        element["name"] = name
    if ref is not None:
        element["ref_id"] = ref
        element["available_actions"] = ["Click"]
    if states:
        element["states"] = list(states)
    if value:
        element["value"] = value
    return element


def strip(element):
    return {key: value for key, value in element.items() if key != "label"}


def ok(command):
    return {"version": "2.6", "ok": True, "command": command,
            "data": {"disposition": {"delivery": "delivered_unverified", "retry": "unsafe"}}}


def failure(command, code):
    return {"version": "2.6", "ok": False, "command": command,
            "error": {"code": code, "message": "failed",
                      "disposition": {"delivery": "unknown", "retry": "unsafe"}}}


class RecordingObserver:
    def __init__(self):
        self.actions = []
        self.observations = []
        self.events = []

    def on_observation(self, window, envelope, text, source=None, wait=None):
        self.observations.append((window["id"], source))

    def on_action(self, tool, arguments, target, result):
        self.actions.append((tool, dict(arguments), result))

    def on_event(self, event):
        self.events.append(event)


@dataclass
class Replay:
    toolbox: Toolbox
    app: FakeApp
    clock: FakeClock
    observer: RecordingObserver
    task: Task


def make_replay(tmp_path, options=None, ready_text=None, level=2):
    """An ungated Toolbox over a FakeApp with a fake clock. The app is not launched yet."""
    clock = FakeClock()
    app = FakeApp(clock, options)
    observer = RecordingObserver()
    task = Task("goal", EXE, level, [], ready_text=ready_text, ready_timeout_s=30)
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=app), task, confirm=lambda _prompt: False,
                      sleep=clock.sleep, clock=clock.now, observer=observer,
                      captures_dir=tmp_path / "captures", gated=False)
    return Replay(toolbox, app, clock, observer, task)


def act(n, goal, tool, target, expect=None, **extra):
    step = {"n": n, "goal": goal, "do": "act", "tool": tool, "args": extra.pop("args", {}),
            "target": target, "expect": expect}
    step.update(extra)
    return step


def full_steps(item_role="statictext"):
    """The steps a successful model run on FakeApp would give: menu, Shop list, period, query,
    save into the save window, and the file check."""
    return [
        act(1, "메뉴 목록이 열림", "click", {"scope": "main", "role": "button", "name": "메뉴"}, "window_opened"),
        act(2, "현황 화면이 열림", "click", {"scope": {"opened_by": 1}, "role": "button", "name": "현황"},
            "window_closed", closes=1),
        act(3, "Shop 목록이 열림", "click",
            {"scope": "main", "role": "button", "near": {"name": "Shop", "relation": "same_row", "nth": 1}},
            "window_opened"),
        {"n": 4, "goal": "Shop이 고른 값만 선택됨", "do": "select_only", "scope": {"opened_by": 3},
         "all": {"role": "statictext", "name": "All"}, "item_role": item_role, "items": "$shop"},
        act(5, "목록이 닫히고 선택이 적용됨", "click_part",
            {"scope": {"opened_by": 3}, "role": "statictext", "name": "Select"}, "window_closed",
            args={"part": "center", "action": "click"}, closes=3),
        act(6, "기간 목록이 열림", "click", {"scope": "main", "role": "button", "name": "기간 선택"}, "window_opened"),
        act(7, "기간이 정해짐", "click_part", {"scope": {"opened_by": 6}, "role": "statictext", "name": "$period"},
            "window_closed", args={"part": "center", "action": "click"}, closes=6),
        act(8, "조회가 끝나 표가 채워짐", "click", {"scope": "main", "role": "button", "name": "조회"},
            "content_changed", settle=True),
        act(9, "저장 창이 열림", "click", {"scope": "main", "role": "button", "name": "저장"}, "window_opened"),
        act(10, "저장 경로를 넣음", "type",
            {"scope": {"opened_by": 9, "top": True}, "role": "edit", "name": FILE_NAME_LABEL},
            "value_changed", value="$save_path"),
        act(11, "저장 창이 닫힘", "press", {"scope": {"opened_by": 9, "top": True}}, "window_closed",
            args={"combo": "enter"}, closes=9),
        {"n": 12, "goal": "파일이 저장됨", "do": "check_file", "path": "$save_path"},
    ]


def make_workflow(tmp_path, steps=None, **overrides):
    workflow = {
        "format": 1, "id": "w-20261119-001", "system": "mes", "goal": "Shop을 골라 조회하고 저장",
        "source": {"run_id": "20261119-101500", "date": "2026-11-19"}, "status": "unverified",
        "passes": 0, "failures": 0, "verified_with_other_values": False, "permission_level": 2,
        "params": {"shop": {"kind": "list", "example": ["WA0", "WA1"]},
                   "period": {"kind": "text", "example": "전일"},
                   "save_path": {"kind": "path", "example": str(tmp_path / "out" / "front.xlsx")}},
        "fixed": [],
        "steps": full_steps() if steps is None else steps,
    }
    workflow.update(overrides)
    return workflow


def make_values(tmp_path, **overrides):
    folder = tmp_path / "out"
    folder.mkdir(exist_ok=True)
    values = {"shop": ["WA0", "WA1"], "period": "전일", "save_path": str(folder / "a.xlsx")}
    values.update(overrides)
    return values


class StopAfter:
    """A stop signal that turns on once the app has seen `presses` presses."""

    def __init__(self, app, presses):
        self._app = app
        self._presses = presses

    def is_set(self):
        return len(self._app.presses) >= self._presses
