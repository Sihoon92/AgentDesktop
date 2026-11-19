"""The registration screen's server side: read a system's screen and save its profile.

An administrator picks a system the settings file names, launches it (or attaches to it), picks
one of its windows and reads it: the whole tree and a picture, with the windows of the same
process over it pasted in (an open menu list is a window of its own). The answer lists the elements the
harness sees there (agent.observe.visible_elements under the saved profile) and, apart from them,
every node that has only a native id and children, so a picture-only menu item that holds parts
can be registered too. Then the administrator checks the ready signals both ways
(agent.ready.measure) and saves: the profile is checked as a file would be, and then against the
latest read of the window (problems()). A save from an older read is refused, as the screen may
have changed since.

Only reading commands reach the desktop: launch, list-windows, snapshot, find and screenshot.
No model is called. The windows listed are those of the process launch returned and those that
opened after it (agent.ready.candidates), never another app's. Pictures stay in a temporary
folder until close(); nothing read goes into the runs folder, the profile folder or a log. No
answer names an exe path.
"""

import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from agent import identity
from agent.capture import add_windows, stacked
from agent.frames import frame_origin, to_image_rect
from agent.observe import visible_elements
from agent.profile import FORMAT, Profile, ProfileError, check_profile, load_profile, save_profile
from agent.profile import as_dict as profile_as_dict
from agent.ready import SNAPSHOT_TIMEOUT_MS, candidates, measure, recommended_read

MAX_DELAY_S = 10
SHOT_NAME = re.compile(r"r\d+\.png")
TEXT_KEYS = ("name", "description", "value")
MATCH_KEYS = ("role", "name", "description", "value", "bounds")
LAUNCH_FAILED = "시스템을 띄우지 못했습니다. 시스템을 닫고 다시 띄워 주세요"
NOT_LAUNCHED = "먼저 앱 띄우기를 눌러 주세요"
NO_WINDOW = "그 창이 목록에 없습니다. 창 목록을 다시 받아 주세요"
READ_AGAIN = "읽은 뒤 화면이 바뀌었을 수 있습니다. 화면을 다시 읽어 주세요"
NO_SESSION = "agent-desktop 세션을 시작하지 못했습니다"
PROBLEM_TWO = "이 창에서 둘 이상의 요소와 맞음"
PROBLEM_ABSENT = "읽은 창에 없음"
PROBLEM_TITLE = "창 제목과만 같음"


class EditorError(Exception):
    """A request the editor refuses: `status` is the HTTP status (400 for a bad request, 409 for
    one the state does not allow, 503 when no desktop session starts) and `payload` the body,
    {"error": text} or {"problems": [...]}. No payload holds a path or a value of the profile."""

    def __init__(self, status, payload):
        super().__init__(payload.get("error") or "problems")
        self.status = status
        self.payload = payload


class ProfileEditor:
    def __init__(self, desktop_factory, systems, root, sleep=time.sleep, clock=time.monotonic):
        """`systems` maps the settings' system names to their exes; `root` is the profile folder.
        `desktop_factory()` gives the desktop client, made once on first use."""
        self._desktop_factory = desktop_factory
        self._systems = dict(systems or {})
        self._root = Path(root)
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._desktop = None
        self._session = None
        self._latest = {}
        self._reads = 0
        self._folder = None

    def get(self, system):
        """{"system", "exists", "profile"}: the saved profile in the file's shape."""
        self._known(system)
        return {"system": system, "exists": (self._root / f"{system}.yaml").is_file(),
                "profile": profile_as_dict(self._saved(system))}

    def launch(self, system):
        """Launches the system's exe (launch attaches to one already running) and lists its windows."""
        exe = self._known(system)
        desktop = self._desk()
        before = _rows(desktop)
        envelope = desktop.run("launch", exe)
        if not envelope.get("ok"):
            raise EditorError(409, {"error": LAUNCH_FAILED})
        data = envelope.get("data") or {}
        with self._lock:
            self._session = {"system": system, "launched": (data.get("pid"), data.get("process_instance")),
                             "before": {_key(row) for row in before}}
            self._latest.pop(system, None)
        return self.windows(system)

    def windows(self, system):
        """{"windows": [{"id", "title"}]}: the windows of the launch, listed again."""
        return {"windows": [{"id": row.get("id"), "title": row.get("title") or ""} for row in self._rows(system)]}

    def read(self, system, window_id, delay_s=0):
        """Reads one listed window after `delay_s` seconds (0 to MAX_DELAY_S, waited here so a
        hidden browser tab cannot slow it) and returns {"read_id", "image", "size", "title",
        "items", "missing"}. The picture holds the window and the windows of its process over it
        (see _picture); "missing" counts those left out because their screenshot failed. A window
        that cannot be pictured is still read, with no image and no rects."""
        if type(delay_s) not in (int, float) or not 0 <= delay_s <= MAX_DELAY_S:
            raise EditorError(400, {"error": f"기다리는 시간은 0초부터 {MAX_DELAY_S}초까지입니다"})
        self._window(system, window_id)
        if delay_s:
            self._sleep(delay_s)
        row = self._window(system, window_id)
        desktop = self._desk()
        envelope = desktop.run("snapshot", "--window-id", window_id, "--compact", "--timeout-ms", SNAPSHOT_TIMEOUT_MS,
                               "--include-bounds")
        if not envelope.get("ok"):
            code = (envelope.get("error") or {}).get("code") or "UNKNOWN"
            raise EditorError(409, {"error": f"화면을 읽지 못했습니다 ({code}). 다시 읽어 주세요"})
        tree = (envelope.get("data") or {}).get("tree") or {}
        with self._lock:
            self._reads += 1
            read_id = f"r{self._reads}"
            folder = self._shots_folder()
        image, size, frame, missing = self._picture(desktop, row, folder / f"{read_id}.png")
        items = read_items(tree, self._saved(system), frame)
        title = row.get("title") or ""
        with self._lock:
            self._latest[system] = {"read_id": read_id, "items": items, "title": title}
        return {"read_id": read_id, "image": image, "size": size, "title": title, "items": items,
                "missing": missing}

    def check(self, system, window_id, ready):
        """Times both ways of reading `ready` on one listed window: ready.measure's answer with
        "recommended", the faster way that found every signal, or None."""
        if not isinstance(ready, list) or not ready:
            raise EditorError(400, {"error": "준비 신호를 하나 이상 두세요"})
        try:
            checked = check_profile({"format": FORMAT, "system": system, "ready": ready}, system)
        except ProfileError as error:
            raise EditorError(400, {"error": error.reason}) from error
        row = self._window(system, window_id)
        measured = measure(self._desk(), row, checked.ready, self._clock)
        return {**measured, "recommended": recommended_read(measured)}

    def save(self, system, read_id, profile):
        """Saves `profile` (the file's keys; format and system are this editor's) for `system` when
        it passes the file check and problems() against the latest read. {"saved": True}."""
        self._known(system)
        with self._lock:
            latest = self._latest.get(system)
        if latest is None or latest["read_id"] != read_id:
            raise EditorError(409, {"error": READ_AGAIN})
        if not isinstance(profile, dict):
            raise EditorError(400, {"error": "프로파일은 키와 값의 묶음이어야 합니다"})
        try:
            checked = check_profile({**profile, "format": FORMAT, "system": system}, system)
        except ProfileError as error:
            raise EditorError(400, {"error": error.reason}) from error
        try:
            saved = load_profile(self._root, system)
        except ProfileError:
            saved = Profile(system=system)
        found = problems(checked, saved, latest["items"], latest["title"])
        if found:
            raise EditorError(400, {"problems": found})
        save_profile(self._root, checked)
        return {"saved": True}

    def shot_path(self, name):
        """The path of a picture this editor took, or None for any other name."""
        with self._lock:
            folder = self._folder
        if folder is None or not isinstance(name, str) or SHOT_NAME.fullmatch(name) is None:
            return None
        path = folder / name
        return path if path.is_file() else None

    def close(self):
        """Removes the pictures and forgets what was read."""
        with self._lock:
            folder, self._folder = self._folder, None
            self._latest = {}
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)

    def _known(self, system):
        if not isinstance(system, str) or system not in self._systems:
            raise EditorError(400, {"error": "설정 파일의 systems에 없는 시스템입니다"})
        return self._systems[system]

    def _saved(self, system):
        try:
            return load_profile(self._root, system)
        except ProfileError as error:
            raise EditorError(409, {"error": f"시스템 프로파일을 읽지 못했습니다: {error.reason}"}) from error

    def _desk(self):
        with self._lock:
            if self._desktop is None:
                try:
                    self._desktop = self._desktop_factory()
                except Exception as error:
                    raise EditorError(503, {"error": NO_SESSION}) from error
            return self._desktop

    def _rows(self, system):
        self._known(system)
        with self._lock:
            session = self._session
        if session is None or session["system"] != system:
            raise EditorError(409, {"error": NOT_LAUNCHED})
        return candidates(_rows(self._desk()), session["launched"], session["before"])

    def _window(self, system, window_id):
        row = next((row for row in self._rows(system) if row.get("id") == window_id), None)
        if row is None:
            raise EditorError(409, {"error": NO_WINDOW})
        return row

    def _shots_folder(self):
        if self._folder is None:
            self._folder = Path(tempfile.mkdtemp(prefix="agent-profile-shots-"))
        return self._folder

    @staticmethod
    def _picture(desktop, row, path):
        """(image name, [w, h], (origin, size), missing) of a picture of `row`, or Nones and 0 when
        its screenshot fails. As a run's capture does (agent.recorder), the other windows of the
        same process over it (an open menu list is a top-level window of its own) are pasted where
        they sit, named by the window list read after the screenshot; `origin` and `size` are those
        of the combined image, and `missing` counts the other windows whose screenshot failed."""
        shot = desktop.run("screenshot", "--window-id", row.get("id"), str(path))
        if not shot.get("ok") or not row.get("bounds"):
            return None, None, None, 0
        size = (shot["data"]["width"], shot["data"]["height"])
        origin = frame_origin(row["bounds"], *size)
        layout = add_windows(desktop, path, row, origin, stacked(row, _rows(desktop)))
        size = layout.size or size
        origin = (origin[0] + layout.shift[0], origin[1] + layout.shift[1])
        return path.name, list(size), (origin, size), layout.missing


def read_items(tree, profile, frame):
    """The rows of a read, in tree order: every element the harness sees under `profile`, and
    every node with only a native id and children that is not a window. Each is {"n", "kind",
    "role", "name", "description", "id", "in", "leaf", "rect", "alias"}; "in" is the area of a
    node with an id. The elements come from agent.observe.visible_elements and are laid on the
    tree walk in their order, so the element rule is observe's alone."""
    elements = visible_elements(tree, profile)
    position = 0
    items = []
    for node, container in _walk(tree):
        element = elements[position] if position < len(elements) else None
        native = identity.native_value(node)
        if element is not None and _same(node, element):
            position += 1
            items.append(_item(len(items) + 1, element["kind"], node, native, element.get("in"), frame,
                               element.get("alias")))
        elif native is not None and node.get("role") != "window" and not identity.is_leaf(node) \
                and not node.get("ref_id") and not any(node.get(key) for key in TEXT_KEYS):
            items.append(_item(len(items) + 1, "id", node, native, container, frame, None))
    return items


def problems(profile, saved, items, title):
    """What keeps `profile` from being saved against one read window (`items` from read_items and
    its `title`), as [{"where": "ready" | "elements", "index": position in that list from 0,
    "problem": text}], with no value in any text. An entry or an id signal that two elements
    fit; a name signal no element shows, or that only the window title shows. The ready signals
    are checked only when they differ from the `saved` ones, so a popup read to add names is not
    held to the main screen's signals."""
    found = []
    if tuple(profile.ready) != tuple(saved.ready):
        for index, signal in enumerate(profile.ready):
            problem = _signal_problem(signal, items, title)
            if problem is not None:
                found.append({"where": "ready", "index": index, "problem": problem})
    for index, entry in enumerate(profile.elements):
        if _fitting(entry, items) > 1:
            found.append({"where": "elements", "index": index, "problem": PROBLEM_TWO})
    return found


def _signal_problem(signal, items, title):
    if "id" in signal:
        return PROBLEM_TWO if _fitting(signal, items) > 1 else None
    wanted = str(signal.get("name") or "").strip()
    role = signal.get("role")
    shown = sum(1 for item in items if isinstance(item["name"], str) and item["name"].strip() == wanted
                and (role is None or item["role"] == role))
    if shown:
        return None
    return PROBLEM_TITLE if (title or "").strip() == wanted else PROBLEM_ABSENT


def _fitting(entry, items):
    return sum(1 for item in items if identity.fits(item["role"], item["id"], item["in"], entry))


def _walk(tree):
    """Every node in tree order, the root included, with the area it sits in (agent.identity's rule)."""
    if not isinstance(tree, dict):
        return
    pending = [(tree, None, tree.get("role") == "window")]
    while pending:
        node, container, is_root = pending.pop()
        if not isinstance(node, dict):
            continue
        yield node, container
        inner = identity.child_container(node, container, is_root)
        pending.extend((child, inner, False) for child in reversed(node.get("children") or []))


def _same(node, element):
    if (node.get("ref_id") or None) != (element.get("ref") or None):
        return False
    native = element.get("native_id") or {}
    return identity.native_value(node) == native.get("value") and all(
        (node.get(key) or None) == (element.get(key) or None) for key in MATCH_KEYS)


def _item(number, kind, node, native, container, frame, alias):
    bounds = node.get("bounds")
    rect = to_image_rect(bounds, *frame) if frame is not None and bounds else None
    return {"n": number, "kind": kind, "role": node.get("role"), "name": node.get("name"),
            "description": node.get("description"), "id": native,
            "in": dict(container) if native is not None and container is not None else None,
            "leaf": identity.is_leaf(node), "rect": rect, "alias": alias}


def _rows(desktop):
    envelope = desktop.run("list-windows")
    if not envelope.get("ok"):
        return []
    return [row for row in envelope.get("data") or [] if isinstance(row, dict)]


def _key(row):
    return row.get("id"), row.get("pid"), row.get("process_instance")
