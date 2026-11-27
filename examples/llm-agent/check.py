"""Checks the binary, the settings, the LLM and the target app before a run.

Every line holds counts, codes and verdicts only, so it can be read aloud from a PC whose
screens and data must stay there. The exit code is 1 when any check failed.
"""

import argparse
import base64
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from agent import checks
from agent.capture import Capture
from agent.checks import failed, passed, skipped
from agent.desktop import DesktopClient, start_session
from agent.envfiles import ProfileError, load_env
from agent.handles import process_window_handles
from agent.images import image_items
from agent.knowledge import KnowledgeError, system_name
from agent.llm import (TEXT_URL_ENV, VISION_KEY_ENV, VISION_MODEL_ENV, VISION_URL_ENV, ModelSetupError,
                       text_responder, vision_setup)
from agent.profile import ProfileError as SystemProfileError
from agent.profile import load_profile, profile_root
from agent.ready import measure, ready_message, signals, wait_until_ready
from agent.settings import VISION_POINT_MODES, SettingsError, load_settings, settings_path
from agent.shortcut import guarded, is_shortcut, resolve_shortcut
from agent.survey import id_keys, kinds
from agent.systems import apply_ready, default_choice, system_exe
from agent.task import Task, model_choice
from agent.vision import VISION_INSTRUCTIONS
from run import BINARIES, ENV_FILES

HERE = Path(__file__).resolve().parent
CHECK_DIR = HERE / ".check"
SKIPPABLE = ("llm", "app")
OBSERVE_TIMEOUT_MS = "8000"
WINDOW_WAIT_S = 15
WINDOW_POLL_S = 0.5
CLI_TIMEOUT_S = 30
LLM_TIMEOUT_S = 90

BINARY = "바이너리"
SETTINGS = "설정"
LLM_CALL = "LLM 호출"
LLM_TOOL = "LLM 도구 호출"
LLM_IMAGE = "LLM 그림 입력"
LLM_NAMES = (LLM_CALL, LLM_TOOL, LLM_IMAGE)
VISION_CALL = "비전 호출"
VISION_POINTS = "비전 좌표"
VISION_NAMES = (VISION_CALL, VISION_POINTS)
SPLIT_MODE = "나눈 방식"
IMAGE_MODE_IGNORED = "설정의 image_mode 무시"
DIGITS_QUESTION = "Reply with only the digits the image shows, nothing else."
POINT_QUESTION = ('Where is the red circle? It has no numbered box. Put its center in "points" '
                  'with the label "red circle".')
READY = "준비 신호"
APP = "앱 관찰"
IDS = "id 유지"
PROFILE_FAILED = "시스템 프로파일 실패"

CHECK_INSTRUCTIONS = "This is a connectivity check of the API. Do exactly what the user asks, nothing more."
REPORT_TOOL = "report_digits"
SHOW_TOOL = "show_image"
TOOLS = [
    {
        "type": "function",
        "name": REPORT_TOOL,
        "description": "Report the digits you were asked for.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"digits": {"type": "string", "description": "Only the digits, nothing else"}},
            "required": ["digits"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": SHOW_TOOL,
        "description": "Returns an image for the check.",
        "strict": True,
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
]
IMAGE_CALL_ID = "call_check_image"


def _openai_client(api_key, base_url):
    from openai import OpenAI

    arguments = {"api_key": api_key, "timeout": LLM_TIMEOUT_S, "max_retries": 1}
    if base_url is not None:
        arguments["base_url"] = base_url
    return OpenAI(**arguments)


@dataclass(frozen=True)
class Target:
    """The app the check reads: its exe, the system it was picked as, its system profile and the
    ready signals agent.systems.apply_ready gives it. `unknown` is a --system name the settings
    do not have. `profile` is None when the system has no profile file (or no name), and
    `profile_error` the reason when its file cannot be used. When `exe` is a shortcut, `shortcut`
    is what it names (agent.shortcut.Shortcut) or `shortcut_error` the code it cannot be launched
    with; both stay None for an exe."""

    exe: str | None
    system: str | None = None
    ready_text: str | None = None
    ready_timeout_s: int = next(item.default for item in fields(Task) if item.name == "ready_timeout_s")
    unknown: bool = False
    ready: tuple = ()
    ready_read: str = "snapshot"
    profile: object = None
    profile_error: str | None = None
    shortcut: object = None
    shortcut_error: str | None = None


def pick_target(exe, system, settings, profile_dir=None, environ=None):
    """The exe in the same order as a run: --exe, then the --system entry, then the default
    choice's entry, then the settings exe. Only one system is ever picked. Its profile is read
    from profile_root(--profile-dir, the settings' profile_dir, AGENT_PROFILE_DIR) under the
    system's knowledge name and given to apply_ready; a profile that cannot be used is kept
    out and its reason noted."""
    if exe:
        target = Target(exe)
    elif system is not None:
        found = system_exe(settings, system)
        if found is None:
            return Target(None, unknown=True)
        target = Target(found, system=system)
    elif default_choice(settings) is not None:
        chosen = default_choice(settings)
        target = Target(system_exe(settings, chosen), system=chosen)
    else:
        target = Target(settings.get("exe"))
    if not target.exe:
        return target
    root = profile_root(profile_dir, settings.get("profile_dir"), environ)
    try:
        name = system_name(target.system or settings.get("system"), target.exe)
    except KnowledgeError:
        return apply_ready(target, settings)
    try:
        profile = load_profile(root, name)
    except SystemProfileError as error:
        return replace(apply_ready(target, settings), profile_error=error.reason)
    if not (Path(root) / f"{name}.yaml").is_file():
        profile = None
    return replace(apply_ready(target, settings, profile), profile=profile)


@dataclass
class Deps:
    """What the check reaches outside itself; tests replace each part."""

    runner: object = subprocess.run
    make_client: object = _openai_client
    environ: object = field(default_factory=lambda: os.environ)
    clock: object = time.monotonic
    sleep: object = time.sleep
    rng: object = field(default_factory=random.SystemRandom)
    check_dir: Path = CHECK_DIR
    binaries: tuple = tuple(BINARIES)
    env_files: tuple = tuple(ENV_FILES)
    read_shortcut: object = resolve_shortcut


def main(argv=None, deps=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", help="Absolute path of the target executable; defaults to the chosen system's exe")
    parser.add_argument("--system", help="A name from the settings' systems to check; defaults to the viewer's choice")
    parser.add_argument("--skip", action="append", choices=SKIPPABLE, default=[], help="Skip the llm or app checks")
    parser.add_argument("--settings", help="Settings file; defaults to AGENT_SETTINGS, then settings.local.yaml")
    parser.add_argument("--profile", help="Load .env.<profile> before .env (default: AGENT_PROFILE)")
    parser.add_argument("--profile-dir", help="System profile folder; defaults to the settings' profile_dir, "
                                              "then AGENT_PROFILE_DIR, then profiles/")
    args = parser.parse_args(argv)
    deps = deps or Deps()
    try:
        profile = load_env(args.profile, deps.env_files, deps.environ, loader=load_dotenv)
    except ProfileError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2
    results = run_checks(args, deps)
    print("점검 (화면 내용 없음)")
    if profile:
        print(f"  프로필: {profile}")
    for result in results:
        print(f"  {checks.line(result)}")
        for note in result.notes:
            print(f"    {note}")
    print(checks.footer(results))
    return checks.exit_code(results)


def run_checks(args, deps):
    binary, kind = checks.binary_location(deps.environ, deps.binaries)
    results = [_guarded([BINARY], lambda: [check_binary(binary, kind, deps.runner)])[0]]
    settings_result, settings = check_settings(args.settings, deps.environ)
    target = with_shortcut(pick_target(args.exe, args.system, settings, args.profile_dir, deps.environ),
                            deps.read_shortcut)
    results.append(_shortcut_noted(_with_profile(settings_result, target), target))
    split = _vision_named(settings, deps.environ)
    vision_items = split or _vision_connection_without_model(deps.environ)
    if "llm" in args.skip:
        results += [skipped(name) for name in LLM_NAMES]
        results += [skipped(name) for name in VISION_NAMES] if vision_items else []
    else:
        results += _guarded(LLM_NAMES, lambda: check_llm(deps, settings, split))
        if vision_items:
            results += _guarded(VISION_NAMES, lambda: check_vision(deps, settings, split))
    names = ([READY] if signals(target) else []) + [APP, IDS]
    if "app" in args.skip:
        results += [skipped(name) for name in names]
    elif target.profile_error is not None:
        results += [skipped(name, PROFILE_FAILED) for name in names]
    else:
        results += _guarded(names, lambda: check_app(deps, binary, target, settings))
    return results


def _with_profile(result, target):
    """The settings result with the system profile: a line of counts under it, or PROFILE_INVALID
    with the profile's reason (which names a place, never a value) when its file cannot be used."""
    if target.profile_error is not None:
        if result.status == checks.FAILED:
            return result
        return failed(SETTINGS, "PROFILE_INVALID", target.profile_error)
    return replace(result, notes=(checks.profile_detail(target.profile),))


def with_shortcut(target, read):
    """`target` with what its shortcut names, or the code it cannot be launched with; as it was
    when its exe is not a shortcut. `read` is resolve_shortcut or a stand-in for it, and it is the
    one the check's client launches with (agent.shortcut.guarded)."""
    if not target.exe or not is_shortcut(target.exe):
        return target
    found, code = guarded(target.exe, read)
    return replace(target, shortcut=found, shortcut_error=code)


def _shortcut_noted(result, target):
    """The settings result with the `바로가기:` line under it when the target is a shortcut."""
    if target.shortcut is None and target.shortcut_error is None:
        return result
    return replace(result, notes=result.notes + (checks.shortcut_detail(target.shortcut, target.shortcut_error),))


def _guarded(names, run):
    """Runs one group of checks; an unexpected error fails each with its class name only."""
    try:
        return run()
    except Exception as error:
        return [failed(name, f"INTERNAL {type(error).__name__}") for name in names]


def check_binary(binary, kind, runner):
    if not binary.exists():
        return failed(BINARY, "BINARY_MISSING", kind)
    envelope = _cli(runner, [str(binary), "version"])
    if not envelope.get("ok"):
        return failed(BINARY, f"version {checks.envelope_code(envelope)}", kind)
    version = (envelope.get("data") or {}).get("version")
    return passed(BINARY, f"{kind} · 버전 {version}" if version else kind)


def _cli(runner, argv):
    try:
        completed = runner(argv, capture_output=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=CLI_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "HARNESS_TIMEOUT"}}
    except OSError:
        return {"ok": False, "error": {"code": "HARNESS_SPAWN"}}
    try:
        envelope = json.loads(completed.stdout)
    except (TypeError, ValueError):
        envelope = None
    return envelope if isinstance(envelope, dict) and "ok" in envelope else {"ok": False, "error": {"code": "HARNESS_OUTPUT"}}


def check_settings(explicit, environ):
    """(result, settings). The line names which keys came from the file, never their values.
    A file that cannot be used fails with the error's reason, which holds no path, value or typed key."""
    path = settings_path(explicit, environ)
    try:
        settings = load_settings(explicit, environ)
    except SettingsError as error:
        if not path.is_file():
            return failed(SETTINGS, "SETTINGS_MISSING"), {}
        return failed(SETTINGS, "SETTINGS_INVALID", error.reason), {}
    if not path.is_file():
        return passed(SETTINGS, "설정 파일 없음 · 코드 기본값 사용"), settings
    keys = ", ".join(settings) if settings else "없음"
    detail = f"설정 파일 읽음 · 설정 파일에서 온 키: {keys}"
    if "systems" in settings or "ready_text" in settings:
        detail += f" · {checks.systems_detail(settings)}"
    return passed(SETTINGS, detail), settings


def _env_set(environ, name):
    value = environ.get(name)
    return isinstance(value, str) and bool(value.strip())


def _vision_named(settings, environ):
    named = settings.get("vision_model")
    return (isinstance(named, str) and bool(named.strip())) or _env_set(environ, VISION_MODEL_ENV)


def _vision_connection_without_model(environ):
    return any(_env_set(environ, name) for name in (VISION_KEY_ENV, VISION_URL_ENV))


def _image_mode_ignored(settings):
    defaults = {item.name: item.default for item in fields(Task)}
    return (settings.get("image_mode") or defaults["image_mode"]) != defaults["image_mode"]


def check_llm(deps, settings, split=False):
    """The text model's call, tool call and, in one-model mode, image reading. In split mode the
    text model gets no picture, so the image item is skipped and the vision items check pictures."""
    environ = deps.environ
    if not _env_set(environ, "OPENAI_API_KEY"):
        return [failed(name, "NO_API_KEY") for name in LLM_NAMES]
    defaults = {item.name: item.default for item in fields(Task)}
    model, source = model_choice(None, settings, environ)
    effort = settings.get("reasoning_effort") or defaults["reasoning_effort"]
    try:
        responder = text_responder(SimpleNamespace(model=model, reasoning_effort=effort), environ, deps.make_client)
    except ModelSetupError:
        return [failed(name, "NO_API_KEY") for name in LLM_NAMES]
    except Exception as error:
        return [failed(name, checks.llm_error_code(error)) for name in LLM_NAMES]
    endpoint = f"{TEXT_URL_ENV} 사용" if _env_set(environ, TEXT_URL_ENV) else "기본 주소"
    started = deps.clock()
    try:
        responder.respond(CHECK_INSTRUCTIONS, [_user("Reply with the single word: ready. Do not call any tool.")], TOOLS)
    except Exception as error:
        reason = f"{LLM_CALL} 실패"
        return [failed(LLM_CALL, checks.llm_error_code(error), endpoint), skipped(LLM_TOOL, reason),
                skipped(LLM_IMAGE, reason)]
    first = passed(LLM_CALL, f"{endpoint} · 텍스트 모델: {source} · {checks.seconds(deps.clock() - started)}")
    if split:
        detail = SPLIT_MODE + (f" · {IMAGE_MODE_IGNORED}" if _image_mode_ignored(settings) else "")
        return [first, _check_tool(responder, deps), skipped(LLM_IMAGE, detail)]
    return [first, _check_tool(responder, deps), _check_image(responder, deps)]


def check_vision(deps, settings, split):
    """The vision model's call (it reads drawn digits) and its point rule (pixel or 0-1000). The
    reader is called directly, without shrinking or mapping, so the raw coordinates show the rule."""
    environ = deps.environ
    if not split:
        return [failed(VISION_CALL, "NO_VISION_MODEL", f"{VISION_MODEL_ENV} 없음"),
                skipped(VISION_POINTS, "비전 설정 실패")]
    try:
        vision, source = vision_setup(settings, environ, deps.make_client)
    except ModelSetupError:
        return [failed(VISION_CALL, "NO_API_KEY"), skipped(VISION_POINTS, "비전 설정 실패")]
    except Exception as error:
        return [failed(VISION_CALL, checks.llm_error_code(error)), skipped(VISION_POINTS, "비전 설정 실패")]
    if _env_set(environ, VISION_URL_ENV):
        endpoint = f"{VISION_URL_ENV} 사용"
    elif _env_set(environ, TEXT_URL_ENV):
        endpoint = f"{TEXT_URL_ENV} 사용"
    else:
        endpoint = "기본 주소"
    reader = vision.reader
    call = _check_vision_digits(reader, deps, f"{endpoint} · 비전 모델: {source}")
    if call.status == checks.FAILED and call.code != "DIGITS_NOT_READ":
        return [call, skipped(VISION_POINTS, f"{VISION_CALL} 실패")]
    return [call, _check_vision_points(reader, deps, settings)]


def _image_url(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _check_vision_digits(reader, deps, prefix):
    expected = _digits(deps.rng)
    with tempfile.TemporaryDirectory(prefix="agent-check-") as folder:
        path = Path(folder) / "digits.png"
        try:
            checks.digit_image(expected, path)
        except RuntimeError:
            return failed(VISION_CALL, "NO_PILLOW", prefix)
        started = deps.clock()
        try:
            text, _ = reader.ask(CHECK_INSTRUCTIONS, DIGITS_QUESTION, _image_url(path))
        except Exception as error:
            return failed(VISION_CALL, checks.llm_error_code(error), prefix)
    detail = f"{prefix} · {checks.seconds(deps.clock() - started)}"
    if "".join(checks.DIGITS.findall(text or "")) != expected:
        return failed(VISION_CALL, "DIGITS_NOT_READ", detail)
    return passed(VISION_CALL, detail)


def _check_vision_points(reader, deps, settings):
    with tempfile.TemporaryDirectory(prefix="agent-check-") as folder:
        path = Path(folder) / "point.png"
        try:
            center, size = checks.point_image(path, deps.rng)
        except RuntimeError:
            return failed(VISION_POINTS, "NO_PILLOW")
        started = deps.clock()
        try:
            text, _ = reader.ask(VISION_INSTRUCTIONS, POINT_QUESTION, _image_url(path))
        except Exception as error:
            return failed(VISION_POINTS, checks.llm_error_code(error))
    elapsed = checks.seconds(deps.clock() - started)
    point = checks.first_point(text)
    if point is None:
        return failed(VISION_POINTS, "NO_POINT", elapsed)
    mode = checks.point_mode(point, center, size)
    if mode is None:
        return failed(VISION_POINTS, "POINT_WRONG", f"{' · '.join(f'{name} 아님' for name in VISION_POINT_MODES)}")
    detail = f"권장 vision_points: {mode}"
    if settings.get("vision_points", VISION_POINT_MODES[0]) != mode:
        detail += " · 지금 설정과 다름"
    return passed(VISION_POINTS, detail)


def _user(text):
    return {"role": "user", "content": text}


def _digits(rng):
    return str(rng.randint(1000, 9999))


def _check_tool(responder, deps):
    expected = _digits(deps.rng)
    started = deps.clock()
    try:
        response = responder.respond(
            CHECK_INSTRUCTIONS, [_user(f"Call {REPORT_TOOL} with digits \"{expected}\".")], TOOLS)
    except Exception as error:
        return failed(LLM_TOOL, checks.llm_error_code(error))
    elapsed = checks.seconds(deps.clock() - started)
    arguments = checks.called_with(response, REPORT_TOOL)
    if arguments is None:
        return failed(LLM_TOOL, "NO_TOOL_CALL", elapsed)
    if arguments.get("digits") != expected:
        return failed(LLM_TOOL, "WRONG_ARGUMENTS", elapsed)
    return passed(LLM_TOOL, elapsed)


def _check_image(responder, deps):
    """Sends a drawn number in each image form the agent can use and asks the model to read it."""
    read, codes = {}, {}
    with tempfile.TemporaryDirectory(prefix="agent-check-") as folder:
        for mode in checks.IMAGE_PREFERENCE:
            expected = _digits(deps.rng)
            path = Path(folder) / f"{mode}.png"
            try:
                width, height = checks.digit_image(expected, path)
            except RuntimeError:
                return failed(LLM_IMAGE, "NO_PILLOW", "권장 image_mode: off")
            shot = Capture(id="check", path=path, width=width, height=height, origin=(0, 0), window_id="check",
                           window_bounds={}, drawn=False, cropped=False)
            items = [
                _user(f"Call {SHOW_TOOL}, then call {REPORT_TOOL} with the digits the image shows."),
                {"type": "function_call", "call_id": IMAGE_CALL_ID, "name": SHOW_TOOL, "arguments": "{}"},
                *image_items(IMAGE_CALL_ID, {"ok": True, "image": "attached"}, [shot], mode),
            ]
            try:
                response = responder.respond(CHECK_INSTRUCTIONS, items, TOOLS)
            except Exception as error:
                codes[mode] = checks.llm_error_code(error)
                continue
            read[mode] = checks.digits_read(response, REPORT_TOOL) == expected
    detail = checks.image_detail(read, codes)
    if any(read.values()):
        return passed(LLM_IMAGE, detail)
    return failed(LLM_IMAGE, "IMAGE_NOT_READ", detail)


def check_app(deps, binary, target, settings):
    """Reads the target's main window once, launching the app only when it is not running.
    Nothing is clicked or typed, and an app the check launched stays open.

    With a ready signal the app is launched and waited for the way a run does
    (agent.ready.wait_until_ready), and the ready window is the one read; a system that is
    already running fails the wait instead of being read."""
    no_ids = skipped(IDS, "관찰 없음")
    wanted = signals(target)
    signal = bool(wanted)
    not_waited = [skipped(READY, "띄우지 않음")] if signal else []
    if target.unknown:
        return [failed(APP, "SYSTEM_UNKNOWN"), no_ids]
    exe = target.exe
    if not exe:
        return [failed(APP, "NO_EXE"), no_ids]
    if target.shortcut_error is not None:
        return not_waited + [failed(APP, target.shortcut_error), no_ids]
    if not Path(exe).exists():
        return not_waited + [failed(APP, "EXE_MISSING"), no_ids]
    if not binary.exists():
        return not_waited + [failed(APP, "BINARY_MISSING"), no_ids]
    session = start_session(str(binary), f"check {datetime.now():%Y%m%d-%H%M%S}", deps.runner)
    if session is None:
        return not_waited + [failed(APP, "SESSION_FAILED"), no_ids]
    desktop = DesktopClient(str(binary), session, deps.runner,
                            window_handles=process_window_handles if deps.runner is subprocess.run else None,
                            read_shortcut=deps.read_shortcut)
    if signal:
        rounds = []
        outcome = wait_until_ready(desktop, str(exe), wanted, target.ready_timeout_s, deps.clock, deps.sleep,
                                   read=target.ready_read, on_round=rounds.append)
        notes = tuple(checks.round_lines(rounds))
        if outcome.status != "ready":
            return [replace(_ready_failure(outcome), notes=notes), skipped(APP, "준비 신호 실패"), no_ids]
        ways = checks.read_ways_line(measure(desktop, outcome.window, wanted, deps.clock))
        waited = [replace(passed(READY, checks.ready_detail(outcome.waited_ms, outcome.switched)),
                          notes=notes + (ways,))]
        window = (outcome.window or {}).get("id")
        process = checks.process_mark(outcome.pid, outcome.instance)
    else:
        launched = desktop.run("launch", str(exe))
        if not launched.get("ok"):
            return [failed(APP, f"launch {checks.envelope_code(launched)}"), no_ids]
        launched_data = launched.get("data") or {}
        window = _main_window(desktop, launched_data, deps)
        if window is None:
            return [failed(APP, "NO_WINDOW"), no_ids]
        waited = []
        process = checks.process_mark(launched_data.get("pid"), launched_data.get("process_instance"))
    app, keys = _observe(deps, desktop, window)
    if keys is None:
        return waited + [app, no_ids]
    return waited + [app, _check_ids(deps, exe, target.system or settings.get("system"), keys, process)]


def _ready_failure(outcome):
    """READY_<STATUS> with the run's own reason (agent.ready.READY_MESSAGES) and the seconds waited.
    The signal itself is never in the reason."""
    detail = f"{ready_message(outcome)} · {checks.whole_seconds(outcome.waited_ms)}"
    if outcome.status == "launch_failed":
        detail += f" · launch {checks.envelope_code({'error': outcome.launch_error})}"
    return failed(READY, f"READY_{outcome.status.upper()}", detail)


def _observe(deps, desktop, window):
    """(app result, element id keys) of one read of `window`; keys is None when the read failed."""
    started = deps.clock()
    snapshot = desktop.run("snapshot", "--window-id", window, "--compact", "--include-bounds",
                           "--timeout-ms", OBSERVE_TIMEOUT_MS)
    elapsed = deps.clock() - started
    if not snapshot.get("ok"):
        return failed(APP, f"snapshot {checks.envelope_code(snapshot)}", checks.seconds(elapsed)), None
    data = snapshot.get("data") or {}
    tree = data.get("tree") or {}
    counted, keys = kinds(tree), id_keys(tree)
    shot = _screenshot(desktop, window)
    shot_data = shot.get("data") or {} if shot.get("ok") else {}
    detail = checks.observation_detail(counted, data.get("complete"), elapsed, shot_data)
    app = (passed(APP, detail) if shot.get("ok")
           else failed(APP, f"screenshot {checks.envelope_code(shot)}", detail))
    return app, keys


def _main_window(desktop, launched, deps):
    """The window launch reported, else the first visible window of that process to appear."""
    window = launched.get("window") or {}
    if window.get("id"):
        return window["id"]
    deadline = deps.clock() + WINDOW_WAIT_S
    while True:
        listed = desktop.run("list-windows")
        rows = [row for row in listed.get("data") or [] if listed.get("ok")
                and row.get("pid") == launched.get("pid")
                and row.get("process_instance") == launched.get("process_instance")
                and row.get("visible", True)]
        rows.sort(key=lambda row: bool(row.get("minimized")))
        if rows:
            return rows[0]["id"]
        if deps.clock() >= deadline:
            return None
        deps.sleep(WINDOW_POLL_S)


def _screenshot(desktop, window):
    with tempfile.TemporaryDirectory(prefix="agent-check-") as folder:
        return desktop.run("screenshot", "--window-id", window, str(Path(folder) / "window.png"))


def _check_ids(deps, exe, system, keys, process):
    """Compares the element ids with the last check of this system when the app was started
    again since then. A check of the same process proves nothing and keeps the saved ids.
    `system` is the chosen system's name, else the settings' system (else the exe's name)."""
    try:
        system = system_name(system, exe)
    except KnowledgeError:
        return failed(IDS, "SYSTEM_NAME_INVALID")
    path = checks.ids_path(deps.check_dir, system)
    previous = checks.load_ids(path)
    if previous is not None and previous.process == process:
        return skipped(IDS, "앱을 다시 켜지 않음 · 껐다 켠 뒤 다시 점검")
    try:
        checks.save_ids(path, keys, process)
    except OSError:
        return failed(IDS, "SAVE_FAILED")
    if previous is None:
        return skipped(IDS, f"지난 점검 없음 · 이번 id {len(keys)} 저장")
    detail = checks.ids_detail(previous.keys, keys)
    if keys and previous.keys == keys:
        detail += f" · {checks.STABLE_IDS_HINT}"
    return passed(IDS, detail)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    sys.exit(main())
