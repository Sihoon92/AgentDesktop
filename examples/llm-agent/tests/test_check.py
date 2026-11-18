"""check.py: seven lines of counts and codes, exit code 1 on any failure, and no screen text.

The CLI is tests/fake_cli.py and the model is a fake client, so no window opens and no
model is called.
"""

import base64
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fake_cli import FakeCli, scene_window  # noqa: E402

import check  # noqa: E402
import run  # noqa: E402
from agent import checks, settings as settings_module  # noqa: E402
from agent.survey import id_keys, kinds  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
MARK = "MARKER-화면글"
NAMES = ["바이너리", "설정", "LLM 호출", "LLM 도구 호출", "LLM 그림 입력", "앱 관찰", "id 유지"]
ALLOWED_COMMANDS = {"version", "session", "launch", "list-windows", "snapshot", "screenshot"}
MAIN = scene_window("w-1", f"{MARK} 창 제목", {"x": 0, "y": 0, "width": 816, "height": 608})


def box(x, y, width=40, height=20):
    return {"x": x, "y": y, "width": width, "height": height}


def marked_tree(extra_id=None):
    children = [
        {"role": "button", "name": f"{MARK} 버튼", "ref_id": "@e1", "bounds": box(10, 10)},
        {"role": "button", "ref_id": "@e2", "bounds": box(60, 10),
         "native_id": {"kind": "automation_id", "value": f"{MARK}-id"}},
        {"role": "button", "ref_id": "@e3", "bounds": box(110, 10)},
        {"role": "text", "name": f"{MARK} 글자", "bounds": box(10, 40)},
        {"role": "text", "name": f"{MARK} 글자 2", "bounds": box(10, 70)},
        {"role": "pane", "bounds": box(10, 100, 200, 100)},
    ]
    if extra_id:
        children.append({"role": "edit", "ref_id": "@e9", "bounds": box(10, 210),
                         "native_id": {"kind": "automation_id", "value": extra_id}})
    return {"role": "window", "name": f"{MARK} 창", "bounds": box(0, 0, 800, 600), "children": children}


class CheckCli(FakeCli):
    """FakeCli plus the two commands the check adds: version and session start."""

    def __init__(self, tree=None, session_ok=True, launch_window=True):
        super().__init__(scene={"tree": tree or marked_tree(), "windows": [dict(MAIN)]})
        self.session_ok = session_ok
        self.launch_window = launch_window
        self.launch_process = None
        self.snapshot_result = None

    def _respond(self, words):
        if words[0] == "version":
            return json.loads((FIXTURES / "version.json").read_text(encoding="utf-8"))
        if words[0] == "session":
            if not self.session_ok:
                return {"version": "2.6", "ok": False, "command": "session",
                        "error": {"code": "INTERNAL", "message": f"{MARK} C:\\secret\\path"}}
            return {"version": "2.6", "ok": True, "command": "session", "data": {"session_id": "check-1"}}
        if words[0] == "snapshot" and self.snapshot_result is not None:
            return self.snapshot_result
        envelope = super()._respond(words)
        if words[0] == "launch" and not self.launch_window:
            envelope["data"].pop("window", None)
        if words[0] == "launch" and self.launch_process is not None:
            pid, instance = self.launch_process
            envelope["data"]["pid"] = pid
            envelope["data"].pop("process_instance", None)
            if instance is not None:
                envelope["data"]["process_instance"] = instance
        return envelope


class SequenceRng:
    def __init__(self, values):
        self.values = list(values)

    def randint(self, _low, _high):
        return self.values.pop(0)


class FakeModel:
    """Answers like a model that reads images in the forms listed in `reads`."""

    def __init__(self, reads=("user_message", "tool_output"), calls_tool=True, error=None):
        self.reads = set(reads)
        self.calls_tool = calls_tool
        self.error = error
        self.requests = []
        self.clients = []
        self.vision_reads = True
        self.vision_points = "pixel"
        self.vision_error = None

    def client(self, api_key=None, base_url=None):
        self.clients.append((api_key, base_url))
        return SimpleNamespace(responses=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        if "tools" not in request:
            return self.see(request)
        if self.error is not None:
            raise self.error
        items = request["input"]
        mode, image = image_in(items)
        if mode is not None:
            digits = image_digits[image] if mode in self.reads else "0000"
            return reply(call(check.REPORT_TOOL, {"digits": digits}))
        text = items[0]["content"]
        if "with digits" in text and self.calls_tool:
            return reply(call(check.REPORT_TOOL, {"digits": text.split('"')[1]}))
        return reply(SimpleNamespace(type="message"), text="ready")

    def see(self, request):
        """A vision call: one user message with a question and an image, no tools."""
        if self.vision_error is not None:
            raise self.vision_error
        _, png = image_in(request["input"])
        if png in image_digits:
            return reply(text=image_digits[png] if self.vision_reads else "I cannot read it")
        x, y, width, height = red_center(png)
        if self.vision_points == "normalized_1000":
            x, y = x * 1000 / width, y * 1000 / height
        elif self.vision_points == "wrong":
            x, y = 3, 3
        elif self.vision_points == "none":
            return reply(text="no idea")
        answer = {"answer": "here", "marks": [], "points": [{"label": "red circle", "x": round(x), "y": round(y)}],
                  "unsure": False}
        return reply(text="```json\n" + json.dumps(answer) + "\n```")


def red_center(png):
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    red = [(x, y) for y in range(0, height, 2) for x in range(0, width, 2)
           if image.getpixel((x, y)) == (255, 0, 0)]
    xs, ys = [x for x, _ in red], [y for _, y in red]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, width, height


image_digits = {}


def image_in(items):
    """(mode, png bytes) of the image the request carries, or (None, None)."""
    for item in items:
        output = item.get("output") if isinstance(item, dict) else None
        if isinstance(output, list):
            return "tool_output", _png(output)
        if isinstance(item, dict) and item.get("role") == "user" and isinstance(item.get("content"), list):
            return "user_message", _png(item["content"])
    return None, None


def _png(parts):
    url = next(part["image_url"] for part in parts if part.get("type") == "input_image")
    return base64.b64decode(url.split(",", 1)[1])


def call(name, arguments):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id="c1")


def reply(*output, text=""):
    return SimpleNamespace(output=list(output), output_text=text, usage=SimpleNamespace(total_tokens=10))


@pytest.fixture
def digit_images(monkeypatch):
    """Remembers which digits each drawn image holds, so the fake model can 'read' it."""
    original = checks.digit_image

    def drawing(digits, path):
        size = original(digits, path)
        image_digits[Path(path).read_bytes()] = digits
        return size

    monkeypatch.setattr(checks, "digit_image", drawing)
    yield
    image_digits.clear()


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.exe = tmp_path / f"{MARK}폴더" / "target.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"")
        self.debug = tmp_path / "debug" / "agent-desktop.exe"
        self.debug.parent.mkdir()
        self.debug.write_bytes(b"")
        self.release = tmp_path / "release" / "agent-desktop.exe"
        self.cli = CheckCli()
        self.model = FakeModel()
        self.environ = {"OPENAI_API_KEY": f"{MARK}-key"}
        self.check_dir = tmp_path / ".check"
        self.now = 0.0
        self.rng = [4821, 5932, 7043] * 3
        monkeypatch.setattr(settings_module, "DEFAULT_PATH", tmp_path / "settings.local.yaml")

    def clock(self):
        self.now += 0.25
        return self.now

    def deps(self):
        return check.Deps(runner=self.cli, make_client=self.model.client, environ=self.environ, clock=self.clock,
                          sleep=lambda _s: None, rng=SequenceRng(self.rng),
                          check_dir=self.check_dir, binaries=(self.release, self.debug), env_files=())

    def settings(self, text):
        path = self.tmp / "settings.local.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def run(self, capsys, *argv):
        code = check.main(list(argv), self.deps())
        out = capsys.readouterr()
        return code, out.out + out.err


@pytest.fixture
def env(tmp_path, monkeypatch, digit_images):
    return Env(tmp_path, monkeypatch)


def lines_of(out):
    return {line.strip().split(":")[0]: line.strip() for line in out.splitlines() if line.startswith("  ")}


def test_all_seven_checks_print_one_line_each_and_pass(env, capsys):
    code, out = env.run(capsys, "--exe", str(env.exe))
    item_lines = [line.strip() for line in out.splitlines() if line.startswith("  ")]
    assert [line.split(":")[0] for line in item_lines] == NAMES
    assert code == 0, out
    assert all("실패" not in line for line in item_lines), out
    assert lines_of(out)["id 유지"].startswith("id 유지: 건너뜀")
    assert out.splitlines()[-1] == "결과: 실패 0 · 건너뜀 1 · 통과 6"


def test_output_holds_no_screen_text_path_setting_value_or_key(env, capsys):
    env.settings(f"model: {MARK}-model\nexe: {env.exe}\nsystem: marker-system\n")
    env.environ["OPENAI_BASE_URL"] = f"https://{MARK}.internal/v1"
    code, out = env.run(capsys)
    assert code == 0, out
    for secret in (MARK, "MARKER", str(env.tmp), "marker-system", "internal", "check-1"):
        assert secret not in out


def test_failures_print_codes_only_and_exit_one(env, capsys):
    env.cli.session_ok = False
    error = openai.AuthenticationError(
        f"{MARK} body", response=httpx.Response(401, request=httpx.Request("POST", f"https://{MARK}.x/v1")),
        body={"error": {"message": f"{MARK} detail"}})
    env.model.error = error
    code, out = env.run(capsys, "--exe", str(env.exe))
    rows = lines_of(out)
    assert code == 1
    assert rows["LLM 호출"].startswith("LLM 호출: 실패(AuthenticationError 401)")
    assert rows["LLM 도구 호출"].startswith("LLM 도구 호출: 건너뜀")
    assert rows["LLM 그림 입력"].startswith("LLM 그림 입력: 건너뜀")
    assert rows["앱 관찰"] == "앱 관찰: 실패(SESSION_FAILED)"
    assert MARK not in out and "MARKER" not in out and "secret" not in out


def test_connection_error_without_status_reports_the_class_name(env, capsys):
    env.model.error = openai.APIConnectionError(request=httpx.Request("POST", f"https://{MARK}.x/v1"))
    _, out = env.run(capsys, "--skip", "app")
    assert lines_of(out)["LLM 호출"] == "LLM 호출: 실패(APIConnectionError) (기본 주소)"


def test_skip_llm_and_app_print_skipped_and_touch_neither(env, capsys):
    made = []
    env.model.client = lambda *args: made.append(1)
    code, out = env.run(capsys, "--skip", "llm", "--skip", "app", "--exe", str(env.exe))
    rows = lines_of(out)
    for name in NAMES[2:]:
        assert rows[name] == f"{name}: 건너뜀"
    assert code == 0
    assert made == []
    assert env.cli.command_names() == ["version"]


def test_missing_api_key_fails_every_llm_check(env, capsys):
    del env.environ["OPENAI_API_KEY"]
    code, out = env.run(capsys, "--skip", "app")
    assert code == 1
    for name in ("LLM 호출", "LLM 도구 호출", "LLM 그림 입력"):
        assert lines_of(out)[name] == f"{name}: 실패(NO_API_KEY)"
    assert env.model.requests == []


def test_llm_requests_carry_only_check_text_and_the_drawn_digits(env, capsys):
    env.run(capsys, "--skip", "app")
    assert len(env.model.requests) == 4
    sent = json.dumps([r["input"] for r in env.model.requests], default=str)
    assert MARK not in sent
    images = [image_in(r["input"]) for r in env.model.requests]
    assert [mode for mode, _ in images] == [None, None, "user_message", "tool_output"]
    for _, png in images[2:]:
        assert Image.open(io.BytesIO(png)).size == checks.DIGIT_CANVAS
        assert image_digits[png].isdigit()


def test_requests_use_the_settings_model_and_the_agent_call_shape(env, capsys):
    path = env.settings("model: company-model\nreasoning_effort: medium\n")
    env.run(capsys, "--skip", "app", "--settings", str(path))
    for request in env.model.requests:
        assert request["model"] == "company-model"
        assert request["reasoning"] == {"effort": "medium"}
        assert request["parallel_tool_calls"] is False
        assert all(tool["strict"] for tool in request["tools"])


def test_code_default_model_is_used_without_settings(env, capsys):
    env.run(capsys, "--skip", "app")
    assert {r["model"] for r in env.model.requests} == {check.Task.__dataclass_fields__["model"].default}


def test_tool_check_fails_when_the_model_does_not_call_the_tool(env, capsys):
    env.model.calls_tool = False
    code, out = env.run(capsys, "--skip", "app")
    assert code == 1
    assert lines_of(out)["LLM 도구 호출"].startswith("LLM 도구 호출: 실패(NO_TOOL_CALL)")


@pytest.mark.parametrize(
    ("reads", "status", "recommended"),
    [
        (("user_message", "tool_output"), "통과", "user_message"),
        (("tool_output",), "통과", "tool_output"),
        (("user_message",), "통과", "user_message"),
        ((), "실패(IMAGE_NOT_READ)", "off"),
    ],
)
def test_image_check_measures_both_forms_and_recommends_one(env, capsys, reads, status, recommended):
    env.model.reads = set(reads)
    code, out = env.run(capsys, "--skip", "app")
    row = lines_of(out)["LLM 그림 입력"]
    assert row.startswith(f"LLM 그림 입력: {status}")
    assert row.endswith(f"권장 image_mode: {recommended})")
    for mode in ("user_message", "tool_output"):
        assert f"{mode} {'읽음' if mode in reads else '못 읽음'}" in row
    assert code == (0 if reads else 1)


def test_settings_line_names_the_keys_from_the_file_but_not_values(env, capsys):
    path = env.settings(f"model: {MARK}\nimage_mode: tool_output\n")
    _, out = env.run(capsys, "--skip", "llm", "--skip", "app", "--settings", str(path))
    assert lines_of(out)["설정"] == "설정: 통과 (설정 파일 읽음 · 설정 파일에서 온 키: model, image_mode)"


def test_settings_line_without_a_file(env, capsys):
    code, out = env.run(capsys, "--skip", "llm", "--skip", "app")
    assert lines_of(out)["설정"] == "설정: 통과 (설정 파일 없음 · 코드 기본값 사용)"
    assert code == 0


@pytest.mark.parametrize(("content", "expected"), [
    (None, "설정: 실패(SETTINGS_MISSING)"),
    (f"colour: red\n{MARK}-key: x\n", "설정: 실패(SETTINGS_INVALID) (모르는 키 2개)"),
    (f"image_mode: {MARK}\n", "설정: 실패(SETTINGS_INVALID) (image_mode 값이 off, user_message, tool_output 중 하나가 아님)"),
    (f'model: m1\nexe: "C:\\Users\\{MARK}\\VirtualMes.exe"\n',
     "설정: 실패(SETTINGS_INVALID) (YAML 형식 오류 2행 · 큰따옴표 안의 역슬래시 · 경로는 따옴표 없이 적거나 작은따옴표로 감싸세요)"),
    (f'exe: "C:\\tools\\new{MARK}.exe"\n',
     "설정: 실패(SETTINGS_INVALID) (exe 경로에 제어 문자 · 경로는 따옴표 없이 적거나 작은따옴표로 감싸세요)"),
])
def test_unusable_settings_fail_with_a_code_and_a_reason(env, capsys, content, expected):
    path = env.tmp / f"{MARK}.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    exit_code, out = env.run(capsys, "--skip", "llm", "--skip", "app", "--settings", str(path))
    assert exit_code == 1
    assert lines_of(out)["설정"] == expected
    assert MARK not in out and str(env.tmp) not in out


def test_settings_named_by_the_environment_variable(env, capsys):
    path = env.tmp / "named.yaml"
    path.write_text("system: abc\n", encoding="utf-8")
    env.environ[settings_module.SETTINGS_ENV] = str(path)
    _, out = env.run(capsys, "--skip", "llm", "--skip", "app")
    assert lines_of(out)["설정"].endswith("설정 파일에서 온 키: system)")


@pytest.mark.parametrize("where", ["env", "release", "debug"])
def test_binary_line_names_where_it_was_found_like_run_py(env, capsys, monkeypatch, where):
    if where == "env":
        env.environ["AGENT_DESKTOP_BIN"] = str(env.debug)
    if where == "release":
        env.release.parent.mkdir()
        env.release.write_bytes(b"")
    kind = {"env": "환경변수", "release": "release", "debug": "debug"}[where]
    _, out = env.run(capsys, "--skip", "llm", "--skip", "app")
    assert lines_of(out)["바이너리"] == f"바이너리: 통과 ({kind} · 버전 1.2.0)"
    monkeypatch.setattr(run, "BINARIES", [env.release, env.debug])
    monkeypatch.setattr(run.os, "environ", env.environ)
    assert checks.binary_location(env.environ, (env.release, env.debug))[0] == run.find_binary()


def test_missing_binary_fails_and_the_app_check_does_not_run(env, capsys):
    env.debug.unlink()
    code, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    rows = lines_of(out)
    assert code == 1
    assert rows["바이너리"] == "바이너리: 실패(BINARY_MISSING) (debug)"
    assert rows["앱 관찰"] == "앱 관찰: 실패(BINARY_MISSING)"
    assert env.cli.calls == []


def test_app_line_holds_the_survey_counts_time_capture_and_scale(env, capsys):
    env.cli.screenshot_result = {"ok": True, "data": {"path": "x", "width": 1896, "height": 2060,
                                                      "scale_factor": 1.75}}
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    counted = kinds(marked_tree())
    assert counted["named_ref"] == 1 and counted["unnamed_ref"] == 2 and counted["text"] == 2
    expected = checks.observation_detail(counted, True, 0.25, {"width": 1896, "height": 2060, "scale_factor": 1.75})
    assert lines_of(out)["앱 관찰"] == f"앱 관찰: 통과 ({expected})"
    assert "배율 1.75" in expected and "캡처 1896x2060" in expected and "잘림 없음" in expected


def test_app_check_only_reads_and_leaves_the_app_open(env, capsys):
    env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert set(env.cli.command_names()) <= ALLOWED_COMMANDS
    assert not any(call[0] == "taskkill" for call in env.cli.calls)
    launch = env.cli.commands()[env.cli.command_names().index("launch")]
    assert launch == ["launch", str(env.exe)]
    snapshot = env.cli.commands()[env.cli.command_names().index("snapshot")]
    assert snapshot[:3] == ["snapshot", "--window-id", "w-1"]


def test_app_window_is_found_by_process_when_launch_reports_none(env, capsys):
    env.cli.launch_window = False
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["앱 관찰"].startswith("앱 관찰: 통과")
    assert "list-windows" in env.cli.command_names()


def test_app_without_a_window_fails_after_waiting(env, capsys):
    env.cli.launch_window = False
    env.cli.scene["windows"][0]["visible"] = False
    code, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert code == 1
    assert lines_of(out)["앱 관찰"] == "앱 관찰: 실패(NO_WINDOW)"
    assert "snapshot" not in env.cli.command_names()


def test_snapshot_and_screenshot_failures_give_their_cli_codes(env, capsys):
    env.cli.snapshot_result = {"ok": False, "error": {"code": "TIMEOUT", "message": f"{MARK}"}}
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["앱 관찰"].startswith("앱 관찰: 실패(snapshot TIMEOUT)")
    env.cli.snapshot_result = None
    env.cli.screenshot_result = {"ok": False, "error": {"code": "ACTION_FAILED", "message": f"{MARK}"}}
    code, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert code == 1
    assert lines_of(out)["앱 관찰"].startswith("앱 관찰: 실패(screenshot ACTION_FAILED) (이름 있는 ref 1")
    assert MARK not in out


def test_exe_comes_from_settings_and_is_required(env, capsys):
    code, out = env.run(capsys, "--skip", "llm")
    assert code == 1
    assert lines_of(out)["앱 관찰"] == "앱 관찰: 실패(NO_EXE)"
    assert lines_of(out)["id 유지"] == "id 유지: 건너뜀 (관찰 없음)"
    env.settings(f"exe: {env.exe}\n")
    code, out = env.run(capsys, "--skip", "llm")
    assert code == 0, out
    assert ["launch", str(env.exe)] in env.cli.commands()


def test_missing_exe_fails_without_launching(env, capsys):
    code, out = env.run(capsys, "--skip", "llm", "--exe", str(env.tmp / "none.exe"))
    assert lines_of(out)["앱 관찰"] == "앱 관찰: 실패(EXE_MISSING)"
    assert "launch" not in env.cli.command_names()


def restart(env, pid=101, instance="windows-proc-v1:restarted"):
    env.cli.launch_process = (pid, instance)


def test_ids_are_saved_then_compared_after_the_app_restarts(env, capsys):
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    first = id_keys(marked_tree())
    assert lines_of(out)["id 유지"] == f"id 유지: 건너뜀 (지난 점검 없음 · 이번 id {len(first)} 저장)"
    saved = env.check_dir / "target.json"
    stored = json.loads(saved.read_text(encoding="utf-8"))
    assert stored["id_keys"] == sorted(first)
    assert (env.check_dir / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert MARK not in saved.read_text(encoding="utf-8")

    restart(env, instance="windows-proc-v1:second")
    env.cli.scene["tree"] = marked_tree(extra_id="new-id")
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["id 유지"] == f"id 유지: 통과 (같은 id {len(first)}/{len(first)} · 새 id 1)"

    restart(env, instance="windows-proc-v1:third")
    env.cli.scene["tree"] = {"role": "window", "children": []}
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["id 유지"] == f"id 유지: 통과 (같은 id 0/{len(first) + 1} · 새 id 0)"


def test_same_process_is_skipped_and_keeps_the_saved_ids(env, capsys):
    env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    saved = env.check_dir / "target.json"
    before = saved.read_text(encoding="utf-8")
    env.cli.scene["tree"] = marked_tree(extra_id="new-id")
    code, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["id 유지"] == "id 유지: 건너뜀 (앱을 다시 켜지 않음 · 껐다 켠 뒤 다시 점검)"
    assert code == 0
    assert saved.read_text(encoding="utf-8") == before


def test_saved_process_mark_is_a_hash_of_pid_and_instance(env, capsys):
    restart(env, pid=4242, instance="windows-proc-v1:raw-instance")
    env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    text = (env.check_dir / "target.json").read_text(encoding="utf-8")
    assert json.loads(text)["process"] == checks.process_mark(4242, "windows-proc-v1:raw-instance")
    assert "raw-instance" not in text and "4242" not in text


def test_reused_pid_with_a_new_instance_counts_as_a_restart(env, capsys):
    restart(env, pid=7, instance="windows-proc-v1:a")
    env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    restart(env, pid=7, instance="windows-proc-v1:b")
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["id 유지"].startswith("id 유지: 통과 (같은 id")


@pytest.mark.parametrize(("second_pid", "expected"), [(7, "건너뜀 (앱을 다시 켜지 않음"), (8, "통과 (같은 id")])
def test_without_process_instance_the_pid_alone_decides(env, capsys, second_pid, expected):
    restart(env, pid=7, instance=None)
    env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    restart(env, pid=second_pid, instance=None)
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["id 유지"].startswith(f"id 유지: {expected}")


def test_a_saved_file_without_a_process_mark_is_compared(env, capsys):
    saved = env.check_dir / "target.json"
    saved.parent.mkdir()
    saved.write_text(json.dumps({"format": 1, "id_keys": sorted(id_keys(marked_tree()))}), encoding="utf-8")
    _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert lines_of(out)["id 유지"].startswith("id 유지: 통과 (같은 id 1/1")


def test_ids_file_is_named_by_the_settings_system(env, capsys):
    env.settings(f"exe: {env.exe}\nsystem: plant-a\n")
    env.run(capsys, "--skip", "llm")
    assert (env.check_dir / "plant-a.json").is_file()


def test_unexpected_error_fails_the_group_with_its_class_name(env, capsys, monkeypatch):
    def broken(_tree):
        raise ValueError(MARK)

    monkeypatch.setattr(check, "kinds", broken)
    code, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
    assert code == 1
    assert lines_of(out)["앱 관찰"] == "앱 관찰: 실패(INTERNAL ValueError)"
    assert MARK not in out


PROFILES = ["easy", "mes_like", "hard"]


def test_three_practice_profiles_give_different_app_lines(env, capsys):
    paths = [FIXTURES / f"snapshot_virtual_mes_{name}.json" for name in PROFILES]
    if not all(path.is_file() for path in paths):
        pytest.skip("profile snapshots are not captured")
    rows = []
    for path in paths:
        env.cli.snapshot_result = json.loads(path.read_text(encoding="utf-8"))
        _, out = env.run(capsys, "--skip", "llm", "--exe", str(env.exe))
        rows.append(lines_of(out)["앱 관찰"].split(" · 잘림")[0])
    assert len(set(rows)) == 3, rows


def test_digit_image_is_a_large_png(tmp_path):
    path = tmp_path / "d.png"
    assert checks.digit_image("4821", path) == checks.DIGIT_CANVAS
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.convert("L").getextrema()[0] < 50


def test_llm_error_code_takes_the_status_from_the_response():
    class Fails(Exception):
        response = SimpleNamespace(status_code=503)

    assert checks.llm_error_code(Fails("body")) == "Fails 503"
    assert checks.llm_error_code(ValueError("x")) == "ValueError"


def test_saved_ids_that_cannot_be_read_count_as_none(tmp_path):
    path = tmp_path / "s.json"
    assert checks.load_ids(path) is None
    path.write_text("{", encoding="utf-8")
    assert checks.load_ids(path) is None
    path.write_text('{"id_keys": "x"}', encoding="utf-8")
    assert checks.load_ids(path) is None


VISION_NAMES = ["비전 호출", "비전 좌표"]
SPLIT_NAMES = NAMES[:5] + VISION_NAMES + NAMES[5:]


def split(env, x=300, y=400):
    """Names a vision model; the rng then gives the tool digits, the vision digits and the circle."""
    env.environ["VISION_MODEL"] = f"{MARK}-vision"
    env.rng = [4821, 5932, x, y]


def text_requests(env):
    return [request for request in env.model.requests if "tools" in request]


def vision_requests(env):
    return [request for request in env.model.requests if "tools" not in request]


def test_text_model_check_uses_env_model(env, capsys):
    env.environ["OPENAI_MODEL"] = f"{MARK}-text"
    code, out = env.run(capsys, "--skip", "app")
    assert code == 0, out
    assert {request["model"] for request in env.model.requests} == {f"{MARK}-text"}
    assert "텍스트 모델: env" in lines_of(out)["LLM 호출"]
    assert MARK not in out


def test_text_model_source_follows_the_settings_file(env, capsys):
    env.environ["OPENAI_MODEL"] = f"{MARK}-text"
    path = env.settings("model: company-model\n")
    _, out = env.run(capsys, "--skip", "app", "--settings", str(path))
    assert {request["model"] for request in env.model.requests} == {"company-model"}
    assert "텍스트 모델: 설정 파일" in lines_of(out)["LLM 호출"]


def test_omit_effort_sends_no_reasoning_in_the_check(env, capsys):
    path = env.settings("reasoning_effort: omit\n")
    env.run(capsys, "--skip", "app", "--settings", str(path))
    assert env.model.requests and all("reasoning" not in request for request in env.model.requests)


def test_split_mode_skips_the_text_image_check(env, capsys):
    split(env)
    code, out = env.run(capsys, "--skip", "app")
    assert code == 0, out
    assert lines_of(out)["LLM 그림 입력"] == "LLM 그림 입력: 건너뜀 (나눈 방식)"
    assert all(image_in(request["input"]) == (None, None) for request in text_requests(env))
    assert len(text_requests(env)) == 2


def test_vision_call_reads_the_digits(env, capsys):
    split(env)
    code, out = env.run(capsys, "--skip", "app")
    item_lines = [line.strip() for line in out.splitlines() if line.startswith("  ")]
    assert [line.split(":")[0] for line in item_lines] == SPLIT_NAMES
    assert lines_of(out)["비전 호출"].startswith("비전 호출: 통과 (기본 주소 · 비전 모델: env · ")
    first = vision_requests(env)[0]
    assert first["model"] == f"{MARK}-vision"
    assert "reasoning" not in first
    content = first["input"][0]["content"]
    assert [part["type"] for part in content] == ["input_text", "input_image"]
    assert content[1]["detail"] == "high"
    assert image_digits[_png(content)] == "5932"
    assert code == 0


def test_vision_call_that_cannot_read_the_digits_fails(env, capsys):
    split(env)
    env.model.vision_reads = False
    code, out = env.run(capsys, "--skip", "app")
    assert lines_of(out)["비전 호출"].startswith("비전 호출: 실패(DIGITS_NOT_READ)")
    assert code == 1


def test_vision_call_error_skips_the_point_check(env, capsys):
    split(env)
    env.model.vision_error = TimeoutError()
    code, out = env.run(capsys, "--skip", "app")
    rows = lines_of(out)
    assert rows["비전 호출"].startswith("비전 호출: 실패(TimeoutError)")
    assert rows["비전 좌표"] == "비전 좌표: 건너뜀 (비전 호출 실패)"
    assert code == 1


def test_vision_points_pixel_is_recommended(env, capsys):
    split(env)
    code, out = env.run(capsys, "--skip", "app")
    assert lines_of(out)["비전 좌표"] == "비전 좌표: 통과 (권장 vision_points: pixel)"
    point_request = vision_requests(env)[1]
    with Image.open(io.BytesIO(_png(point_request["input"][0]["content"]))) as image:
        assert image.size == checks.POINT_CANVAS
        assert max(image.size) <= 1280
    assert point_request["instructions"] == check.VISION_INSTRUCTIONS
    assert code == 0


def test_vision_points_normalized_is_recommended(env, capsys):
    split(env)
    env.model.vision_points = "normalized_1000"
    _, out = env.run(capsys, "--skip", "app")
    row = lines_of(out)["비전 좌표"]
    assert row.startswith("비전 좌표: 통과 (권장 vision_points: normalized_1000")
    assert "지금 설정과 다름" in row


def test_vision_points_matching_the_settings_say_nothing_more(env, capsys):
    split(env)
    env.model.vision_points = "normalized_1000"
    path = env.settings("vision_points: normalized_1000\n")
    _, out = env.run(capsys, "--skip", "app", "--settings", str(path))
    assert lines_of(out)["비전 좌표"] == "비전 좌표: 통과 (권장 vision_points: normalized_1000)"


@pytest.mark.parametrize(("answer", "code"), [("wrong", "POINT_WRONG"), ("none", "NO_POINT")])
def test_vision_points_wrong_fails(env, capsys, answer, code):
    split(env)
    env.model.vision_points = answer
    exit_code, out = env.run(capsys, "--skip", "app")
    assert lines_of(out)["비전 좌표"].startswith(f"비전 좌표: 실패({code})")
    assert exit_code == 1


def test_one_model_mode_has_no_vision_items(env, capsys):
    _, out = env.run(capsys, "--exe", str(env.exe))
    item_lines = [line.strip() for line in out.splitlines() if line.startswith("  ")]
    assert [line.split(":")[0] for line in item_lines] == NAMES
    assert vision_requests(env) == []


def test_split_mode_lists_nine_items_in_order(env, capsys):
    split(env)
    code, out = env.run(capsys, "--exe", str(env.exe))
    item_lines = [line.strip() for line in out.splitlines() if line.startswith("  ")]
    assert [line.split(":")[0] for line in item_lines] == SPLIT_NAMES
    assert code == 0, out


def test_check_output_has_no_model_names_or_urls(env, capsys):
    split(env)
    env.environ.update({
        "OPENAI_MODEL": f"{MARK}-text", "OPENAI_BASE_URL": f"https://{MARK}.text/v1",
        "VISION_API_KEY": f"{MARK}-vision-key", "VISION_BASE_URL": f"https://{MARK}.vision/v1",
    })
    code, out = env.run(capsys, "--skip", "app")
    assert code == 0, out
    for secret in (MARK, "MARKER", "https", "/v1"):
        assert secret not in out
    assert lines_of(out)["비전 호출"].startswith("비전 호출: 통과 (VISION_BASE_URL 사용 · 비전 모델: env")
    assert (f"{MARK}-key", f"https://{MARK}.text/v1") in env.model.clients
    assert (f"{MARK}-vision-key", f"https://{MARK}.vision/v1") in env.model.clients


def test_vision_settings_model_is_named_as_the_settings_file(env, capsys):
    env.rng = [4821, 5932, 300, 400]
    path = env.settings("vision_model: company-vision\nvision_reasoning_effort: low\n")
    _, out = env.run(capsys, "--skip", "app", "--settings", str(path))
    assert "비전 모델: 설정 파일" in lines_of(out)["비전 호출"]
    assert {request["model"] for request in vision_requests(env)} == {"company-vision"}
    assert all(request["reasoning"] == {"effort": "low"} for request in vision_requests(env))


def test_ignored_image_mode_is_noted(env, capsys):
    split(env)
    path = env.settings("image_mode: off\n")
    _, out = env.run(capsys, "--skip", "app", "--settings", str(path))
    assert lines_of(out)["LLM 그림 입력"] == "LLM 그림 입력: 건너뜀 (나눈 방식 · 설정의 image_mode 무시)"


def test_image_mode_is_not_noted_in_one_model_mode(env, capsys):
    path = env.settings("image_mode: tool_output\n")
    _, out = env.run(capsys, "--skip", "app", "--settings", str(path))
    assert "무시" not in out


def test_vision_address_without_a_model_fails(env, capsys):
    env.environ["VISION_BASE_URL"] = f"https://{MARK}.vision/v1"
    code, out = env.run(capsys, "--skip", "app")
    rows = lines_of(out)
    assert rows["비전 호출"].startswith("비전 호출: 실패(NO_VISION_MODEL)")
    assert rows["비전 좌표"] == "비전 좌표: 건너뜀 (비전 설정 실패)"
    assert vision_requests(env) == []
    assert code == 1
    assert MARK not in out


def test_skip_llm_in_split_mode_skips_the_vision_items_without_a_client(env, capsys):
    split(env)
    code, out = env.run(capsys, "--skip", "llm", "--skip", "app")
    rows = lines_of(out)
    for name in VISION_NAMES:
        assert rows[name] == f"{name}: 건너뜀"
    assert env.model.clients == []
    assert code == 0


def test_point_image_draws_one_red_circle_where_the_rng_says(tmp_path):
    path = tmp_path / "point.png"
    center, size = checks.point_image(path, SequenceRng([300, 400]))
    assert center == (300, 400) and size == checks.POINT_CANVAS
    with Image.open(path) as image:
        assert image.size == (1000, 600)
        assert image.convert("RGB").getpixel((300, 400)) == (255, 0, 0)
        assert image.convert("RGB").getpixel((10, 10)) == (255, 255, 255)


@pytest.mark.parametrize(("answer", "mode"), [
    ((310, 380), "pixel"), ((300, 667), "normalized_1000"), ((300, 300), None), ((900, 400), None),
])
def test_point_mode_tells_pixels_from_thousandths(answer, mode):
    assert checks.point_mode(answer, (300, 400), (1000, 600)) == mode


def test_point_rng_range_keeps_the_two_rules_apart():
    low, high = checks.POINT_Y_RANGE
    for y in (low, high):
        assert y * 1000 / 600 - y > 2 * checks.POINT_TOLERANCE_PX
        assert y * 1000 / 600 + checks.POINT_TOLERANCE_PX < 1000


@pytest.mark.parametrize(("text", "point"), [
    ('{"points": [{"label": "a", "x": 12, "y": 34.5}]}', (12, 34.5)),
    ('```json\n{"answer": "", "points": [{"x": -3, "y": 4}]}\n```', (-3, 4)),
    ("no point here", None),
    ('{"points": []}', None),
])
def test_first_point_reads_the_reply(text, point):
    assert checks.first_point(text) == point
