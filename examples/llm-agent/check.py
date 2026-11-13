"""Checks the binary, the settings, the LLM and the target app before a run.

Every line holds counts, codes and verdicts only, so it can be read aloud from a PC whose
screens and data must stay there. The exit code is 1 when any check failed.
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agent import checks
from agent.capture import Capture
from agent.checks import failed, passed, skipped
from agent.desktop import DesktopClient, start_session
from agent.images import image_items
from agent.knowledge import KnowledgeError, system_name
from agent.llm import OpenAIResponder
from agent.settings import SettingsError, load_settings, settings_path
from agent.survey import id_keys, kinds
from agent.task import Task
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
APP = "앱 관찰"
IDS = "id 유지"

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


def _openai_client():
    from openai import OpenAI

    return OpenAI(timeout=LLM_TIMEOUT_S, max_retries=1)


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


def main(argv=None, deps=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", help="Absolute path of the target executable; defaults to the settings' exe")
    parser.add_argument("--skip", action="append", choices=SKIPPABLE, default=[], help="Skip the llm or app checks")
    parser.add_argument("--settings", help="Settings file; defaults to AGENT_SETTINGS, then settings.local.yaml")
    args = parser.parse_args(argv)
    deps = deps or Deps()
    for path in deps.env_files:
        load_dotenv(path)
    results = run_checks(args, deps)
    print("점검 (화면 내용 없음)")
    for result in results:
        print(f"  {checks.line(result)}")
    print(checks.footer(results))
    return checks.exit_code(results)


def run_checks(args, deps):
    binary, kind = checks.binary_location(deps.environ, deps.binaries)
    results = [_guarded([BINARY], lambda: [check_binary(binary, kind, deps.runner)])[0]]
    settings_result, settings = check_settings(args.settings, deps.environ)
    results.append(settings_result)
    if "llm" in args.skip:
        results += [skipped(name) for name in LLM_NAMES]
    else:
        results += _guarded(LLM_NAMES, lambda: check_llm(deps, settings))
    if "app" in args.skip:
        results += [skipped(APP), skipped(IDS)]
    else:
        exe = args.exe or settings.get("exe")
        results += _guarded([APP, IDS], lambda: check_app(deps, binary, exe, settings))
    return results


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
    """(result, settings). The line names which keys came from the file, never their values."""
    path = settings_path(explicit, environ)
    try:
        settings = load_settings(explicit, environ)
    except SettingsError:
        code = "SETTINGS_INVALID" if path.is_file() else "SETTINGS_MISSING"
        return failed(SETTINGS, code), {}
    if not path.is_file():
        return passed(SETTINGS, "설정 파일 없음 · 코드 기본값 사용"), settings
    keys = ", ".join(settings) if settings else "없음"
    return passed(SETTINGS, f"설정 파일 읽음 · 설정 파일에서 온 키: {keys}"), settings


def check_llm(deps, settings):
    environ = deps.environ
    if not environ.get("OPENAI_API_KEY"):
        return [failed(name, "NO_API_KEY") for name in LLM_NAMES]
    defaults = {item.name: item.default for item in fields(Task)}
    model = settings.get("model") or defaults["model"]
    effort = settings.get("reasoning_effort") or defaults["reasoning_effort"]
    try:
        responder = OpenAIResponder(model, effort, deps.make_client())
    except Exception as error:
        return [failed(name, checks.llm_error_code(error)) for name in LLM_NAMES]
    endpoint = "OPENAI_BASE_URL 사용" if environ.get("OPENAI_BASE_URL") else "기본 주소"
    started = deps.clock()
    try:
        responder.respond(CHECK_INSTRUCTIONS, [_user("Reply with the single word: ready. Do not call any tool.")], TOOLS)
    except Exception as error:
        reason = f"{LLM_CALL} 실패"
        return [failed(LLM_CALL, checks.llm_error_code(error), endpoint), skipped(LLM_TOOL, reason),
                skipped(LLM_IMAGE, reason)]
    first = passed(LLM_CALL, f"{endpoint} · {checks.seconds(deps.clock() - started)}")
    return [first, _check_tool(responder, deps), _check_image(responder, deps)]


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


def check_app(deps, binary, exe, settings):
    """Reads the target's main window once, launching the app only when it is not running.
    Nothing is clicked or typed, and an app the check launched stays open."""
    no_ids = skipped(IDS, "관찰 없음")
    if not exe:
        return [failed(APP, "NO_EXE"), no_ids]
    if not Path(exe).exists():
        return [failed(APP, "EXE_MISSING"), no_ids]
    if not binary.exists():
        return [failed(APP, "BINARY_MISSING"), no_ids]
    session = start_session(str(binary), f"check {datetime.now():%Y%m%d-%H%M%S}", deps.runner)
    if session is None:
        return [failed(APP, "SESSION_FAILED"), no_ids]
    desktop = DesktopClient(str(binary), session, deps.runner)
    launched = desktop.run("launch", str(exe))
    if not launched.get("ok"):
        return [failed(APP, f"launch {checks.envelope_code(launched)}"), no_ids]
    window = _main_window(desktop, launched.get("data") or {}, deps)
    if window is None:
        return [failed(APP, "NO_WINDOW"), no_ids]
    started = deps.clock()
    snapshot = desktop.run("snapshot", "--window-id", window, "--compact", "--include-bounds",
                           "--timeout-ms", OBSERVE_TIMEOUT_MS)
    elapsed = deps.clock() - started
    if not snapshot.get("ok"):
        return [failed(APP, f"snapshot {checks.envelope_code(snapshot)}", checks.seconds(elapsed)), no_ids]
    data = snapshot.get("data") or {}
    tree = data.get("tree") or {}
    counted, keys = kinds(tree), id_keys(tree)
    shot = _screenshot(desktop, window)
    shot_data = shot.get("data") or {} if shot.get("ok") else {}
    detail = checks.observation_detail(counted, data.get("complete"), elapsed, shot_data)
    app = (passed(APP, detail) if shot.get("ok")
           else failed(APP, f"screenshot {checks.envelope_code(shot)}", detail))
    launched_data = launched.get("data") or {}
    process = checks.process_mark(launched_data.get("pid"), launched_data.get("process_instance"))
    return [app, _check_ids(deps, exe, settings, keys, process)]


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


def _check_ids(deps, exe, settings, keys, process):
    """Compares the element ids with the last check of this system when the app was started
    again since then. A check of the same process proves nothing and keeps the saved ids."""
    try:
        system = system_name(settings.get("system"), exe)
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
    return passed(IDS, checks.ids_detail(previous.keys, keys))


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    sys.exit(main())
