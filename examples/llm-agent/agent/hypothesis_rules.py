"""How each automatic hypothesis is judged from one capture session's facts.

Each judge takes a `LoadedSession` and returns a `Verdict` whose code, short and evidence hold
counts, family names and codes only. `agent.hypotheses` pairs these with the questions and the
home actions; this module holds only the rules and thresholds.

Rules that the design and the plan leave to the implementer are written here: H02 also counts
grid families found among window classes, H09 and H11 skip with NO_READ when no app main window
was read, the H05 same-screen share is measured against the larger of the two element counts,
and a session-to-check comparison needs `trees/map.json` to tell the process apart (without it
the comparison stays PENDING rather than risk comparing a process with itself).
"""

import json
from pathlib import Path
from typing import NamedTuple

from agent.checks import FAILED, PASSED, SKIPPED, process_mark
from agent.survey_codes import SAME_SCREEN_SHARE, SURVEY_FOREGROUND_S, SURVEY_STARTUP_S
from agent.survey_facts import capture_dir

UI_FAMILIES = ("WinForms", "WPF", "Win32", "MFC", "Delphi", "Java", "Qt", "Nexacro", "WebView2", "CEF", "IE")
GRID_FAMILIES = ("DevExpress", "Infragistics", "FarPoint", "ComponentOne", "Syncfusion", "Telerik", "ActiveX")
WEB_FAMILIES = ("WebView2", "CEF", "IE")
UIA_FRAMEWORKS = {"WinForm": "WinForms", "WPF": "WPF", "Win32": "Win32"}
H01_SHARE = 0.7
H03_RICH = 0.6
H03_PARTIAL = 0.2
STATE_PATTERNS = ("Value", "Toggle", "ExpandCollapse", "Selection")
READ_OK = ("OK", "TRUNCATED")
SAME_SCREEN_KINDS = ("named_ref", "unnamed_ref", "text", "blank")
LLM_TOOL = "LLM 도구 호출"
LLM_IMAGE = "LLM 그림 입력"
VISION_CALL = "비전 호출"
TREE_MAP = Path("trees") / "map.json"


class Verdict(NamedTuple):
    code: str
    short: str
    evidence: str


def skipped(reason, evidence=""):
    return Verdict("SKIPPED", reason, evidence)


def app_processes(loaded):
    return [p for p in loaded.session.processes if p.rel == "APP"]


def app_windows(loaded):
    """(capture, window) for every app window of every capture, in capture order."""
    return [(c, w) for c in loaded.captures for w in c.windows if w.rel == "APP"]


def main_windows(loaded):
    return [(c, w) for c, w in app_windows(loaded) if w.kind == "MAIN" and w.read in READ_OK]


def app_skip(loaded):
    """The reason an app hypothesis cannot be judged, or None."""
    if not app_processes(loaded):
        return "NO_APP"
    if not any(w.read in READ_OK for _, w in app_windows(loaded)):
        return "NO_READ"
    return None


def _families(loaded, source):
    total = {}
    for p in app_processes(loaded):
        count = getattr(p, source)
        if count is None:
            continue
        for name, n in count.families.items():
            total[name] = total.get(name, 0) + n
    return total


def _listing(counts):
    return ", ".join(f"{name} {n}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))) or "없음"


def _uia_framework_families(loaded):
    found = {}
    for c in loaded.captures:
        if c.uia is None:
            continue
        for framework, n in c.uia.frameworks.items():
            family = UIA_FRAMEWORKS.get(framework)
            if family and n > 0:
                found[family] = found.get(family, 0) + 1
    return found


def judge_h01(loaded):
    classes, modules, uia = _families(loaded, "classes"), _families(loaded, "modules"), _uia_framework_families(loaded)
    counts = {}
    for source in (classes, modules, uia):
        for name, n in source.items():
            if name in UI_FAMILIES:
                counts[name] = counts.get(name, 0) + n
    if any(n > 0 for name, n in counts.items() if name != "Win32"):
        counts.pop("Win32", None)
    counts = {name: n for name, n in counts.items() if n > 0}
    evidence = f"창 클래스 {_listing(classes)} · 모듈 {_listing(modules)} · UIA {_listing(uia)}"
    if not counts:
        return Verdict("UNKNOWN", "0/0", evidence)
    total = sum(counts.values())
    leader, top = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    short = f"{top}/{total}"
    if top / total >= H01_SHARE:
        return Verdict("WEB" if leader in WEB_FAMILIES else leader.upper(), short, evidence)
    return Verdict("MIXED" if len(counts) > 1 else "UNKNOWN", short, evidence)


def judge_h02(loaded):
    modules_read = any(p.modules is not None for p in app_processes(loaded))
    grids = {}
    for source in (_families(loaded, "modules"), _families(loaded, "classes")):
        for name, n in source.items():
            if name in GRID_FAMILIES and n > 0:
                grids[name] = grids.get(name, 0) + n
    uia = [c.uia for c in loaded.captures if c.uia is not None]
    grid_patterns = sum(u.patterns.get("Grid", 0) for u in uia)
    evidence = f"그리드 계열 {_listing(grids)} · UIA Grid 패턴 {grid_patterns}"
    if grids:
        leader = max(grids.items(), key=lambda kv: (kv[1], kv[0]))[0]
        return Verdict("ACTIVEX_OTHER" if leader == "ActiveX" else leader.upper(), "", evidence)
    if grid_patterns > 0:
        return Verdict("NATIVE", "", evidence)
    if not modules_read and not uia:
        return skipped("MODULES_UNREAD", evidence)
    return Verdict("NONE_SEEN", "", evidence)


def _first_main(loaded):
    found = main_windows(loaded)
    return found[0] if found else None


def judge_h03(loaded):
    first = _first_main(loaded)
    if first is None:
        return skipped("NO_READ")
    capture, window = first
    k = window.kinds or {}
    named, unnamed, text, blank = (k.get(key, 0) for key in SAME_SCREEN_KINDS)
    reach, total = named + text, named + unnamed + text + blank
    share = reach / total if total else 0.0
    code = "RICH" if total and share >= H03_RICH else "PARTIAL" if total and share >= H03_PARTIAL else "LOW"
    evidence = f"{capture.id} 이름 있는 ref {named}, 이름 없는 ref {unnamed}, 글자 {text}, 빈 요소 {blank}"
    return Verdict(code, f"{named}/{text}/{blank}", evidence)


def judge_h04(loaded):
    counted = next((c for c in loaded.captures if c.uia is not None), None)
    if counted is None:
        failed = next((c.uia_code for c in loaded.captures if c.uia_code), None)
        return skipped(failed or "NO_READ")
    p = counted.uia.patterns
    invoke, states = p.get("Invoke", 0), sum(p.get(name, 0) for name in STATE_PATTERNS)
    code = "PATTERNS_OK" if invoke > 0 and states > 0 else "INVOKE_ONLY" if invoke > 0 else "NONE"
    short = f"I{invoke}/V{p.get('Value', 0)}/T{p.get('Toggle', 0)}"
    evidence = f"{counted.id} UIA 노드 {counted.uia.nodes}, 패턴 {_listing(p)}"
    return Verdict(code, short, evidence)


def _total(window):
    k = window.kinds or {}
    return sum(k.get(key, 0) for key in SAME_SCREEN_KINDS)


def _same_screen(a, b):
    if a.family != b.family or a.kind != b.kind:
        return False
    x, y = _total(a), _total(b)
    return abs(x - y) <= SAME_SCREEN_SHARE * max(x, y)


def _process_key(window):
    return (window.proc, window.instance)


def _compare(keys_a, keys_b):
    if not keys_a and not keys_b:
        return "NO_IDS"
    return "STABLE" if set(keys_a) == set(keys_b) else "CHANGES"


def _session_pairs(loaded):
    read = [w for _, w in app_windows(loaded) if w.read in READ_OK]
    results = []
    for i, a in enumerate(read):
        for b in read[i + 1:]:
            if _process_key(a) != _process_key(b) and _same_screen(a, b):
                results.append(_compare(a.id_keys, b.id_keys))
    return results


def _window_pid(folder, capture, win):
    try:
        mapping = json.loads((capture_dir(folder, capture.id) / TREE_MAP).read_text(encoding="utf-8"))
        pid = mapping[win]["pid"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pid if isinstance(pid, int) else None


def _saved_comparison(loaded):
    saved = loaded.session.saved_ids
    first = _first_main(loaded)
    if saved is None or first is None:
        return None, ""
    capture, window = first
    pid = _window_pid(loaded.folder, capture, window.win)
    if pid is None:
        return None, "점검 기록 있음, 프로세스를 가리지 못해 비교 안 함"
    if loaded.session.saved_ids_process == process_mark(pid, window.instance):
        return None, "점검 기록이 같은 프로세스라 비교 안 함"
    return _compare(window.id_keys, saved), f"점검 기록과 {capture.id} 비교(같은 화면인지는 확인 못 함)"


def judge_h05(loaded):
    results = _session_pairs(loaded)
    saved, note = _saved_comparison(loaded)
    if saved is not None:
        results.append(saved)
    evidence = " · ".join(part for part in (f"세션 안 같은 화면 짝 {len(results) - (saved is not None)}", note) if part)
    if not results:
        return Verdict("PENDING", "0", evidence)
    code = "CHANGES" if "CHANGES" in results else "STABLE" if "STABLE" in results else "NO_IDS"
    return Verdict(code, str(len(results)), evidence)


def judge_h06(loaded):
    seen = loaded.session.app_seen
    if seen != "STARTED":
        return Verdict(seen, "", f"앱 프로세스 {len(app_processes(loaded))}")
    apps = app_processes(loaded)
    timed = sorted((p for p in apps if p.first_window_t is not None), key=lambda p: p.first_window_t)
    first = timed[0] if timed else next((p for p in apps if p.target_exe), None)
    code = "EXE" if first is not None and first.target_exe else "LAUNCHER"
    return Verdict(code, "", f"앱 프로세스 {len(apps)}, 첫 창의 프로세스 {first.proc if first else '?'}")


def judge_h07(loaded):
    apps = app_processes(loaded)
    most = max((p.max_visible_top for p in apps), default=0)
    evidence = f"MDI 프로세스 {sum(p.mdi for p in apps)}, 동시에 보인 최상위 창 최대 {most}"
    if any(p.mdi for p in apps):
        return Verdict("MDI", "", evidence)
    return Verdict("MULTI_TOP" if most > 1 else "SINGLE", "", evidence)


def judge_h08(loaded):
    found = {**_families(loaded, "classes")}
    for name, n in _families(loaded, "modules").items():
        found[name] = found.get(name, 0) + n
    present = [name for name in WEB_FAMILIES if found.get(name, 0) > 0]
    evidence = f"웹 계열 {_listing({name: found[name] for name in present})}"
    return Verdict(present[0].upper() if present else "NONE", "", evidence)


def judge_h09(loaded):
    first = _first_main(loaded)
    if first is None or first[0].monitor is None:
        return skipped("NO_READ")
    capture = first[0]
    count = "" if capture.monitor_count is None else str(capture.monitor_count)
    return Verdict(capture.monitor, count, f"{capture.id} 주 창의 모니터, 모니터 수 {count or '?'}")


def judge_h10(loaded):
    checks = {c.name: c for c in loaded.session.checks}
    vision = checks.get(VISION_CALL)
    vision_mark = "비전" if vision is not None and vision.status != SKIPPED else ""
    tool = checks.get(LLM_TOOL)
    if tool is None or tool.status == SKIPPED:
        return Verdict("SKIPPED", "", "LLM 점검 건너뜀")
    if tool.status == FAILED:
        return Verdict("FAIL", tool.code, "도구 호출 실패")
    image = checks.get(LLM_IMAGE)
    sees = (image is not None and image.status == PASSED) or (vision is not None and vision.status == PASSED)
    return Verdict("TOOLS_IMAGE" if sees else "TOOLS_ONLY", vision_mark,
                   f"그림 입력 {image.status if image else '없음'}, 비전 {vision.status if vision else '없음'}")


def judge_h11(loaded):
    first = _first_main(loaded)
    if first is None:
        return skipped("NO_READ")
    capture, window = first
    timeouts = sum(w.read == "TIMEOUT" for _, w in app_windows(loaded))
    parts = ([f"{window.read_s:.1f}s"] if window.read_s is not None else []) + ([f"T{timeouts}"] if timeouts else [])
    code = "COMPLETE" if window.read == "OK" else "TRUNCATED"
    return Verdict(code, "/".join(parts), f"{capture.id} 주 창 읽기, 앱 창 시간 초과 {timeouts}")


def _shown(loaded):
    return [e for e in loaded.events if e.event == "window_shown"]


def _app_wins(loaded):
    wins = {e.win for e in _shown(loaded) if e.rel == "APP"}
    return wins | {w.win for _, w in app_windows(loaded)}


def _took_foreground_from_app(loaded):
    return {e.win for e in loaded.events if e.event == "foreground" and e.fg_within == "APP"}


def _popups(loaded):
    """(same, other, unowned_app) window_shown events, each counting a window once."""
    app_wins, took = _app_wins(loaded), _took_foreground_from_app(loaded)
    same, other, pre = [], [], []
    for e in _shown(loaded):
        owned_by_app = e.owned is not None and e.owned in app_wins
        if e.rel == "APP":
            (same if owned_by_app or e.modal else pre if e.owned is None else []).append(e)
        elif owned_by_app or e.win in took:
            other.append(e)
    return same, other, pre


def _startup(loaded):
    """(start, end) of the startup window, or None when the session did not see the start."""
    t0 = loaded.session.first_app_window_t
    if loaded.session.app_seen != "STARTED" or t0 is None:
        return None
    return t0, t0 + SURVEY_STARTUP_S


def _within(event, span):
    return span is not None and span[0] <= event.t <= span[1]


def judge_h15(loaded):
    span = _startup(loaded)
    if span is None:
        return Verdict("NOT_SEEN", "", f"앱 시작을 보지 못함({loaded.session.app_seen})")
    same, other, pre = (sum(_within(e, span) for e in group) for group in _popups(loaded))
    code = "BOTH" if same and other else "SAME_PROC" if same else "OTHER_PROC" if other else "NONE"
    evidence = f"첫 앱 창 뒤 {SURVEY_STARTUP_S}초 안: 앱 팝업 {same}, 다른 프로세스 팝업 {other}, 소유 없는 앱 창 {pre}"
    return Verdict(code, f"{same}/{other}/pre{pre}", evidence)


def judge_h16(loaded):
    span = _startup(loaded)
    outside = [e for e in _popups(loaded)[1] if not _within(e, span)]
    system = sum(e.rel == "SYSTEM" for e in outside)
    rest = len(outside) - system
    code = "BOTH" if system and rest else "SYSTEM" if system else "OTHER" if rest else "NONE"
    evidence = f"시작 시간 밖 다른 프로세스 팝업: Windows 폴더 {system}, 그 밖 {rest} (전경 기준 {SURVEY_FOREGROUND_S}초)"
    return Verdict(code, str(len(outside)), evidence)


def judge_h17(loaded):
    starts, longest = 0, 0.0
    open_since = {}
    for e in loaded.events:
        if e.event == "hung_start":
            starts += 1
            open_since[e.win] = e.t
        elif e.event == "hung_end" and e.win in open_since:
            longest = max(longest, e.t - open_since.pop(e.win))
    for t in open_since.values():
        longest = max(longest, loaded.session.duration_s - t)
    stuck = sum(w.read in ("UNRESPONSIVE", "TIMEOUT") for _, w in app_windows(loaded))
    count = starts + stuck
    evidence = f"응답 없음 시작 {starts}, 읽기 응답 없음·시간 초과 {stuck}, 가장 긴 {longest:.0f}초"
    if not count:
        return Verdict("NONE", "", evidence)
    return Verdict("SEEN", f"{count}/{longest:.0f}s", evidence)


def judge_h18(loaded):
    kinds = {}
    for e in _shown(loaded):
        if e.rel == "APP" and e.kind in ("DIALOG", "OWNED"):
            kinds[e.win] = e.kind
    for _, w in app_windows(loaded):
        if w.kind in ("DIALOG", "OWNED"):
            kinds.setdefault(w.win, w.kind)
    dialogs = sum(kind == "DIALOG" for kind in kinds.values())
    owned = len(kinds) - dialogs
    code = "MIXED" if dialogs and owned else "WIN32_DIALOG" if dialogs else "OWNED_FORM" if owned else "NONE_SEEN"
    return Verdict(code, "", f"앱 대화상자 #32770 {dialogs}, 소유된 창 {owned}")
