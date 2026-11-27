# 실시간 진행 표시 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모델 호출, 도구 호출, CLI 명령의 시작과 끝을 `runs/<id>/progress.jsonl`에 적고, 그것을 터미널(`run.py`, `view.py`)과 웹 뷰어의 "지금" 패널, 스텝 상세에 실시간으로 보여 준다.

**Architecture:** `agent/progress.py`의 `ProgressLog`가 열린 항목 스택과 sink 목록을 들고 기록을 만든다. `RunRecorder`가 실행마다 하나 만들어 파일 sink를 붙이고 `DesktopClient`에 연결한다. `Toolbox`는 관찰자(기록기)에서 `progress`를 얻고, 루프는 `toolbox.progress`를 쓴다. 터미널은 `agent/progress_printer.py`의 sink, 웹은 새 API와 순수 함수 파일 `viewer/static/progress.js`가 맡는다.

**Tech Stack:** Python 3(pytest), 표준 라이브러리 HTTP 서버, 바닐라 JS(Node 22로 순수 함수 테스트)

**Spec:** `examples/llm-agent/docs/specs/2026-11-27-live-progress-design.md`

## Global Constraints

- `events.jsonl`과 그것을 읽는 코드(요약, 뷰어 타임라인 항목, workflow 만들기)는 바꾸지 않는다. 요약과 `run.json`에는 아무것도 더하지 않는다.
- CLI 기록에는 명령 이름(`args[0]`)만 적는다. 인자는 적지 않는다.
- 진행 기록이나 터미널 출력이 실패해도 실행은 계속된다. sink 예외는 삼키고 그 sink만 뗀다. 파일 쓰기가 실패하면 그 뒤로는 파일만 쓰지 않는다.
- `agent/`와 `viewer/`에는 앱 이름이나 화면 글을 넣지 않는다(`tests/test_application_agnostic.py`).
- 터미널은 cp949 콘솔에서 깨지거나 멈추지 않는다. `view.py`도 `run.py`처럼 `sys.stdout.reconfigure(errors="replace")`를 한다. 기호는 `▶ ○ △ ×`이고 모두 cp949에 있다.
- 웹에 넣는 글은 모두 `textContent`로 넣는다.
- 테스트는 `examples/llm-agent`에서 `C:/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -m pytest -q -p no:cacheprovider`로 돌린다. 실제 앱을 띄우는 테스트는 돌리지 않는다.
- 커밋은 사용자가 요청할 때만 한다. 규칙은 Conventional Commits이고 AI 표기는 넣지 않는다.

## Review Focus

- 실행이 `AgentStopped`나 예외로 끝날 때 열린 항목이 `end` 없이 남아, 웹 패널이 영원히 "진행 중"을 보이는 경우. 기대: 모든 열린 항목이 `ok: false`로 닫힌다(Task 2 `test_finish_closes_open_items`, Task 1 `test_span_closes_on_exception`).
- 0.5초 반복 읽기(준비 대기, `wait_for_text`, workflow 재실행의 `list_windows`/`observe`)가 터미널을 수백 줄로 덮는 경우. 기대: 한 줄로 묶인다(Task 4 `test_repeated_reads_print_one_line`, `test_read_only_tools_group_and_hide_children`).
- `type` 도구의 글이나 경로가 CLI 기록에 새는 경우. 기대: `name`만 있다(Task 2 `test_cli_records_carry_only_the_command_name`).
- 진행 기록이 없는 옛 실행 폴더를 뷰어나 내보내기가 열 때 깨지는 경우. 기대: 빈 목록으로 지금처럼 열린다(Task 5 `test_progress_of_a_run_without_the_file_is_empty`, `test_export_without_progress_has_an_empty_list`).
- 같은 응답에서 같은 도구가 두 번 불릴 때 스텝 상세가 엉뚱한 도구의 시간을 보이는 경우. 기대: 줄 번호로 맞는 짝을 고른다(Task 6 `toolForEventLine picks the innermost tool around the line`).

---

### Task 1: 진행 기록 모델 `agent/progress.py`

**Files:**
- Create: `examples/llm-agent/agent/progress.py`
- Test: `examples/llm-agent/tests/test_progress.py`

**Interfaces:**
- Produces: `class ProgressLog(path=None, clock=time.monotonic, events_seen=None)`
  - `add_sink(sink)`: `sink.on_record(record)`가 있으면 기록마다, `sink.on_event(event)`가 있으면 `note_event`마다 부른다.
  - `start(kind: str, **fields) -> int`: `id`, `phase="start"`, `kind`, `t_ms`(생성 시각부터 흐른 정수 ms), `parent`(열린 스택의 맨 위, 없으면 키 생략), 값이 None이 아닌 `fields`로 레코드를 만든다. `kind == "tool"`이고 `events_seen`이 있으면 `events_seen=events_seen()`도 넣는다.
  - `end(item_id: int, ok: bool, **fields)`: `phase="end"`, `id`, `kind`(시작과 같음), `t_ms`, `ok`, None이 아닌 `fields`를 넣는다. tool이면 `events_seen`도 넣는다. 스택에서 `item_id`와 그 위를 모두 뗀다. 위에 있던 항목은 먼저 `ok=False, code="UNCLOSED"`로 닫는다. 모르는 id는 무시한다.
  - `span(kind, **fields)`: 컨텍스트 매니저. `Span`을 돌려준다. `Span.finish(ok=True, **fields)`는 끝 필드를 정하고, 블록이 끝날 때 한 번만 `end`한다. 예외가 나면 `ok=False, code=<예외 클래스 이름>`으로 닫고 예외를 다시 던진다. `finish` 없이 정상으로 끝나면 `ok=True`다.
  - `innermost() -> dict | None`: 맨 위 열린 항목의 시작 레코드 사본.
  - `close_open(code: str)`: 열린 항목을 안쪽부터 모두 `ok=False, code=code`로 닫는다.
  - `note_event(event)`: `on_event`가 있는 sink에 넘긴다.
  - `timed_model(model, name) -> object`: `respond(*args, **kwargs)`를 `span("model", name=name)`으로 감싸 부르고, 응답의 `usage.total_tokens`가 있으면 끝에 `tokens`를 넣는다. 그 밖의 속성은 원래 모델로 넘긴다.
- Produces: `NULL_PROGRESS`. 파일과 sink가 없는 `ProgressLog()`이고 아무 데도 쓰지 않는다.
- Produces: `result_fields(result: dict) -> dict`. 도구 결과를 끝 필드로 바꾼다. `"refused" in result`이거나 `result.get("ok") is False`이면 `ok=False`, `code`는 `refused_code` 또는 `code`(있을 때)이다. 아니면 `ok=True`이고, `expected_met is False`면 `mismatch=True`를 넣는다.
- Produces: `envelope_fields(envelope: dict) -> dict`. CLI 응답을 끝 필드로 바꾼다. `ok=bool(envelope.get("ok"))`이고, 실패면 `code=error.code`(있을 때)이다.
- 파일 sink: `path`가 있으면 기록마다 `json.dumps(record, ensure_ascii=False)` 한 줄을 덧붙인다. `threading.Lock`으로 감싼다. `OSError`가 나면 그 뒤로 파일을 쓰지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_progress.py`, 시계는 `iter`로 만든 가짜)

```python
def clock_from(*seconds):
    values = iter(seconds)
    return lambda: next(values)

def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

def test_records_carry_parent_and_elapsed_ms(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl", clock=clock_from(10.0, 10.5, 11.0, 11.25, 12.0))
    outer = log.start("tool", name="click_part", step=3)
    inner = log.start("cli", name="mouse-click")
    log.end(inner, ok=True)
    log.end(outer, ok=True)
    assert read(tmp_path / "p.jsonl") == [
        {"id": 1, "phase": "start", "kind": "tool", "t_ms": 500, "name": "click_part", "step": 3},
        {"id": 2, "phase": "start", "kind": "cli", "t_ms": 1000, "parent": 1, "name": "mouse-click"},
        {"id": 2, "phase": "end", "kind": "cli", "t_ms": 1250, "ok": True},
        {"id": 1, "phase": "end", "kind": "tool", "t_ms": 2000, "ok": True}]

def test_tool_records_carry_events_seen(tmp_path):
    seen = iter([4, 6])
    log = ProgressLog(tmp_path / "p.jsonl", events_seen=lambda: next(seen))
    log.end(log.start("tool", name="observe"), ok=True)
    assert [r["events_seen"] for r in read(tmp_path / "p.jsonl")] == [4, 6]

def test_span_closes_on_exception(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    with pytest.raises(KeyError):
        with log.span("model", step=1):
            raise KeyError("x")
    assert read(tmp_path / "p.jsonl")[-1] == {**read(tmp_path / "p.jsonl")[-1], "ok": False, "code": "KeyError"}
    assert log.innermost() is None

def test_span_finish_sets_end_fields(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    with log.span("model", step=1) as span:
        span.finish(ok=True, tokens=120)
    assert read(tmp_path / "p.jsonl")[-1]["tokens"] == 120

def test_ending_an_outer_item_closes_what_was_left_open(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    outer = log.start("tool", name="click")
    log.start("cli", name="click")
    log.end(outer, ok=True)
    ends = [r for r in read(tmp_path / "p.jsonl") if r["phase"] == "end"]
    assert [(r["id"], r["ok"], r.get("code")) for r in ends] == [(2, False, "UNCLOSED"), (1, True, None)]

def test_close_open_closes_everything_inside_out(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    log.start("tool", name="click"); log.start("cli", name="click")
    log.close_open("STOPPED")
    assert [(r["id"], r["code"]) for r in read(tmp_path / "p.jsonl") if r["phase"] == "end"] == [(2, "STOPPED"), (1, "STOPPED")]

def test_a_failing_sink_is_dropped_and_the_run_goes_on(tmp_path):
    class Broken:
        calls = 0
        def on_record(self, record):
            Broken.calls += 1
            raise RuntimeError("console gone")
    log = ProgressLog(tmp_path / "p.jsonl")
    log.add_sink(Broken())
    log.end(log.start("cli", name="snapshot"), ok=True)
    assert Broken.calls == 1 and len(read(tmp_path / "p.jsonl")) == 2

def test_an_unwritable_file_does_not_raise(tmp_path):
    log = ProgressLog(tmp_path / "missing" / "p.jsonl")
    log.end(log.start("cli", name="snapshot"), ok=True)

def test_null_progress_writes_nothing():
    NULL_PROGRESS.end(NULL_PROGRESS.start("cli", name="snapshot"), ok=True)
    assert NULL_PROGRESS.innermost() is None

def test_result_fields_tell_refused_failed_mismatch_and_ok():
    assert result_fields({"ok": False, "refused": "x", "refused_code": "focus_failed"}) == {"ok": False, "code": "focus_failed"}
    assert result_fields({"ok": False, "code": "STALE_REF"}) == {"ok": False, "code": "STALE_REF"}
    assert result_fields({"ok": True, "expected_met": False}) == {"ok": True, "mismatch": True}
    assert result_fields({"ok": True, "verdict": "unconfirmed", "expected_met": None}) == {"ok": True}

def test_envelope_fields_take_the_error_code():
    assert envelope_fields({"ok": False, "error": {"code": "ACTION_FAILED"}}) == {"ok": False, "code": "ACTION_FAILED"}
    assert envelope_fields({"ok": True, "data": {}}) == {"ok": True}

def test_timed_model_wraps_respond_with_tokens(tmp_path):
    class Usage: total_tokens = 42
    class Reply: usage = Usage()
    class Model:
        name = "m"
        def respond(self, *a, **k): return Reply()
    log = ProgressLog(tmp_path / "p.jsonl")
    timed = log.timed_model(Model(), "workflow")
    assert timed.respond("i", [], []).usage.total_tokens == 42 and timed.name == "m"
    first, last = read(tmp_path / "p.jsonl")
    assert first["name"] == "workflow" and last["tokens"] == 42
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest -q -p no:cacheprovider tests/test_progress.py`
Expected: `ModuleNotFoundError: agent.progress`

- [ ] **Step 3: `agent/progress.py`를 구현한다** (위 Interfaces 그대로. 모듈 문서에 레코드 필드와 "명령 이름만 적는다"를 적는다.)

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest -q -p no:cacheprovider tests/test_progress.py`
Expected: 13 passed

---

### Task 2: 기록기와 `DesktopClient`에 연결

**Files:**
- Modify: `examples/llm-agent/agent/recorder.py` (`__init__`, `_write_line`, `on_event`, `finish`/`close`)
- Modify: `examples/llm-agent/agent/desktop.py` (`DesktopClient.__init__`, `run`, 새 `set_progress`)
- Test: `examples/llm-agent/tests/test_recorder.py`, `examples/llm-agent/tests/test_desktop.py`

**Interfaces:**
- Consumes: Task 1의 `ProgressLog`, `NULL_PROGRESS`, `envelope_fields`
- Produces: `RunRecorder.progress: ProgressLog`(`self.path / "progress.jsonl"`, `events_seen=lambda: self.events_written`), `RunRecorder.events_written: int`(`events.jsonl`에 쓴 줄 수)
- Produces: `DesktopClient.set_progress(progress)`. 기본값은 `NULL_PROGRESS`이다. `run`은 셸 바로가기를 바꾼 뒤의 `args[0]`로 `cli` 항목을 열고(`headed`는 참일 때만), `try/finally`로 반드시 닫는다. 정상 응답은 `envelope_fields`로, 하니스 실패(`HARNESS_TIMEOUT`, `HARNESS_SPAWN`, `SHORTCUT_*`)도 그 응답의 `error.code`로 닫는다.
- 기록기 생성자는 `desktop`에 `set_progress`가 있으면 `desktop.set_progress(self.progress)`를 부른다. `on_event`는 맨 앞에서 `self.progress.note_event(event)`를 부른다. `finish`(와 `close`)는 `self.progress.close_open("STOPPED")`를 부른다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_recorder.py` (기존 `recorded`, `events` 도우미):

```python
def progress_records(recorder):
    path = recorder.path / "progress.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

def test_cli_commands_are_recorded_in_progress(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    names = [r["name"] for r in progress_records(recorder) if r["kind"] == "cli" and r["phase"] == "start"]
    assert names[0] == "launch"

def test_events_written_counts_event_lines(tmp_path):
    recorder, toolbox, _ = recorded(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을 연다"})
    toolbox.call("observe", OBSERVE)
    assert recorder.events_written == len(events(recorder, focus=True))

def test_finish_closes_open_items(tmp_path):
    recorder, _, _ = recorded(tmp_path)
    recorder.progress.start("tool", name="click")
    recorder.finish(RunResult("stopped", 0, "x"))
    assert progress_records(recorder)[-1] == {**progress_records(recorder)[-1], "phase": "end", "ok": False, "code": "STOPPED"}
```

`tests/test_desktop.py` (기존 가짜 runner 방식을 따른다):

```python
def test_cli_records_carry_only_the_command_name(tmp_path):
    log = ProgressLog(tmp_path / "p.jsonl")
    client = DesktopClient("ad.exe", "s", runner=<성공 응답 runner>)
    client.set_progress(log)
    client.run("type", "@s1:e2", "--", "비밀 글", headed=True)
    start = json.loads((tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert start == {"id": 1, "phase": "start", "kind": "cli", "t_ms": start["t_ms"], "name": "type", "headed": True}

def test_a_failed_command_ends_with_its_code(tmp_path):
    # runner가 {"ok": false, "error": {"code": "STALE_REF"}}를 돌려줄 때 끝 레코드가 ok False, code STALE_REF

def test_a_timed_out_command_ends_with_the_harness_code(tmp_path):
    # runner가 subprocess.TimeoutExpired를 던질 때 끝 레코드 code가 HARNESS_TIMEOUT
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest -q -p no:cacheprovider tests/test_recorder.py tests/test_desktop.py -k "progress or events_written or finish_closes or cli_records or failed_command_ends or timed_out_command"`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현한다** (위 Interfaces 그대로. `events_written`은 `_write_line`이 쓴 뒤 1 늘린다.)

- [ ] **Step 4: 전체 테스트를 돌린다**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: 전부 통과

---

### Task 3: 도구, 모델, 준비 대기, 비전에 항목 달기

**Files:**
- Modify: `examples/llm-agent/agent/tools.py` (`progress` 속성, `call`, `launch_ready`)
- Modify: `examples/llm-agent/agent/screen_tools.py` (비전 읽기를 부르는 곳)
- Modify: `examples/llm-agent/agent/loop.py` (`_respond`, 도구 실행 두 곳, `run_agent`의 `register`)
- Modify: `examples/llm-agent/viewer/run_manager.py:459`, `examples/llm-agent/run.py:329` (`after_run`에 `timed_model`)
- Test: `examples/llm-agent/tests/test_tools.py`, `examples/llm-agent/tests/test_loop.py`, `examples/llm-agent/tests/test_loop_ready.py`

**Interfaces:**
- Consumes: Task 1의 `span`, `result_fields`, `innermost`, `timed_model`, `NULL_PROGRESS`, Task 2의 `RunRecorder.progress`
- Produces: `Toolbox.progress`(property). `getattr(self._observer, "progress", None) or NULL_PROGRESS`.
- 도구 항목의 시작 필드: `step`(`self.step`, 없으면 생략), `name`, `intent`(문자열이면 줄바꿈을 공백으로 바꾸고 그대로), `read_only`(`name in READ_ONLY_TOOLS`일 때만 `True`). 끝 필드는 `result_fields(result)`이다.
- `Toolbox.call`: `innermost()`가 `kind == "tool"`이고 같은 `name`이면 새로 열지 않는다. 아니면 위 필드로 `span("tool", ...)`을 열고 결과로 `finish`한다.
- `loop._Run`: 도구 호출마다(`run`의 반복과 `_last_turn`) `span("tool", step=self._turn, name=call.name, intent=<arguments JSON의 intent, 파싱 실패면 None>, read_only=...)`으로 `_execute`를 감싸고 `result_fields(output)`로 끝낸다. `_respond`는 `span("model", step=self._turn)`으로 모델 호출을 감싸고 `tokens=self._call_tokens`로 끝낸다. 진행 기록은 `getattr(self._toolbox, "progress", NULL_PROGRESS)`로 얻는다(테스트의 가짜 toolbox에는 없을 수 있음).
- `run_agent`: `register(progress.timed_model(model, "registration"), toolbox, log)`.
- `launch_ready`: `span("ready")`로 대기 전체(시작 직후 올리기 포함)를 감싸고 `ok=outcome.status == "ready"`, 실패면 `code=f"READY_{status.upper()}"`로 끝낸다.
- 비전: `screen_tools`에서 비전 리더를 부르는 곳을 `span("model", name="vision")`으로 감싸고, 결과의 토큰이 있으면 `tokens`로 끝낸다.
- `after_run` 호출: `run.py`와 `run_manager.py`가 넘기는 모델을 `recorder.progress.timed_model(model, "workflow")`로 바꾼다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_tools.py`:

```python
class ProgressObserver:
    def __init__(self, path):
        self.progress = ProgressLog(path)
    def on_observation(self, *a, **k): pass
    def on_action(self, *a, **k): pass

def progress_toolbox(tmp_path):
    cli = FakeCli()
    observer = ProgressObserver(tmp_path / "p.jsonl")
    clock = iter(range(0, 10_000))
    toolbox = Toolbox(DesktopClient("ad.exe", "run1", runner=cli), Task("goal", EXE, 3, REQUIRE),
                      confirm=lambda _p: True, sleep=lambda _s: None, clock=lambda: next(clock), observer=observer)
    toolbox._desktop.set_progress(observer.progress)
    return toolbox, tmp_path / "p.jsonl"

def test_a_tool_call_is_one_item_with_its_cli_commands_inside(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    toolbox.call("launch_target", {"intent": "앱을\n연다"})
    records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    tool = records[0]
    assert tool["kind"] == "tool" and tool["name"] == "launch_target" and tool["intent"] == "앱을 연다"
    assert all(r.get("parent") == tool["id"] for r in records if r["kind"] == "cli" and r["phase"] == "start")
    assert records[-1] == {**records[-1], "id": tool["id"], "phase": "end", "ok": True}

def test_a_read_only_tool_is_marked(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    toolbox.call("launch_target", {"intent": "x"})
    toolbox.call("list_windows", {})
    starts = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert [r.get("read_only") for r in starts if r["phase"] == "start" and r["kind"] == "tool"] == [None, True]

def test_a_tool_already_open_with_the_same_name_is_not_opened_twice(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    with toolbox.progress.span("tool", name="launch_target"):
        toolbox.call("launch_target", {"intent": "x"})
    tools = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len([r for r in tools if r["kind"] == "tool" and r["phase"] == "start"]) == 1

def test_a_refused_tool_ends_with_its_refused_code(tmp_path):
    toolbox, path = progress_toolbox(tmp_path)
    toolbox.call("launch_target", {"intent": "x"})
    toolbox.call("focus_window", {"window_id": "w-999", "intent": "x"})
    last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "end" and last["ok"] is False
```

`tests/test_loop.py` (기존 `ScriptedModel`, `call`, 툴박스 도우미를 따른다. 관찰자에 `ProgressObserver`를 준다):

```python
def test_model_calls_and_tool_calls_are_items_with_steps(tmp_path):
    # GIVE_UP으로 끝나는 짧은 실행: 첫 레코드는 kind model, step 1;
    # finish 호출이 kind tool, name finish, step 1 항목으로 감싸이고 그 끝이 기록된다
```

`tests/test_loop_ready.py`:

```python
def test_the_ready_wait_is_one_item_holding_its_reads(tmp_path):
    # toolbox_for의 관찰자에 progress를 주고 launch_ready: 첫 레코드 kind ready,
    # snapshot과 focus-window cli 항목의 parent가 그 id, 끝은 ok True
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest -q -p no:cacheprovider tests/test_tools.py tests/test_loop.py tests/test_loop_ready.py -k "item or read_only_tool_is_marked or opened_twice or refused_tool_ends"`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현한다** (위 Interfaces 그대로)

- [ ] **Step 4: 전체 테스트를 돌린다**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: 전부 통과

---

### Task 4: 터미널 출력

**Files:**
- Create: `examples/llm-agent/agent/progress_printer.py`
- Modify: `examples/llm-agent/run.py` (`make_logger`는 기록만 하고 출력은 프린터가 맡음, 모델 실행과 재실행 모두 `recorder.progress.add_sink(ProgressPrinter())`)
- Modify: `examples/llm-agent/viewer/run_manager.py` (`RunManager(..., print_progress=False)`, 기록기를 만든 두 곳에서 참이면 프린터를 붙임)
- Modify: `examples/llm-agent/view.py` (`print_progress=True`로 만들고, `main` 시작에 `sys.stdout.reconfigure(errors="replace")`와 `sys.stderr.reconfigure(errors="replace")`)
- Test: `examples/llm-agent/tests/test_progress_printer.py`, `examples/llm-agent/tests/test_run.py`(필요하면), `examples/llm-agent/tests/test_run_manager.py`

**Interfaces:**
- Produces: `class ProgressPrinter(write=print)`. `on_record(record)`, `on_event(event)`, `flush()`. `write`는 한 줄 문자열을 받는다(기본 `functools.partial(print, flush=True)`).
- 출력 규칙(문자열이 정확히 이렇게 나온다):
  - 모델 시작: `▶ 모델 생각 중 · 스텝 {step}`. `name`이 있으면 `▶ 모델({name}) 생각 중`.
  - 모델 끝: `○ 모델 응답 · {초}초 · 토큰 {tokens:,}`. 실패면 `× 모델 응답 · {초}초 · {code}`. 이름이 있으면 `모델({name}) 응답`.
  - 도구 시작(읽기 전용이 아닐 때만): `▶ {name}` + intent가 있으면 ` · {intent 60자, 넘으면 59자+…}`.
  - 도구 끝: `○ {name} · {초}초`. 기대와 다르면 `△ {name} · {초}초 · 기대와 다름`. 실패면 `× {name} · {초}초 · {code}`.
  - 준비: 시작 `▶ 대상 앱 준비 중`, 끝 `○ 대상 앱 준비 · {초}초` 또는 `× 대상 앱 준비 · {초}초 · {code}`.
  - CLI: 끝날 때만 `{들여쓰기}○ {name} · {초}초`, 실패면 `×`와 ` · {code}`. 들여쓰기는 출력된 조상 수 × 3칸이다.
  - 초는 `(end.t_ms - start.t_ms) / 1000`을 소수 첫째 자리까지(`f"{s:.1f}"`) 쓴다.
  - 묶기: 같은 `parent` 아래 연달아 끝난 같은 `(kind, name)`의 `cli`, 또는 `read_only` 도구는 모아 두었다가 다른 줄이 나가기 전(또는 `flush`)에 한 줄로 찍는다. 한 개면 위의 보통 줄이다. 둘 이상이면 `○ {name} ×{n} · {합계초}초`이고, 실패가 있으면 ` · 실패 {k}`를 붙인다. 모두 실패면 기호가 `×`이다. 읽기 전용 도구의 자식 CLI는 찍지 않는다.
  - 단계: `stage` 이벤트 중 `status == "started"`면 `[단계 {n}/{total}]`을 찍는다. `total`은 마지막 `plan` 이벤트의 `stages` 길이다. 모르면 `[단계 {n}]`이다.
  - 실행이 끝나면(`finish`) 프린터의 묶음을 비운다. `RunRecorder.finish`가 `close_open` 뒤 sink마다 `flush`를 부른다(sink에 있을 때). `ProgressLog.flush_sinks()`를 더해도 된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_progress_printer.py`, 레코드를 직접 넣는다)

```python
def printed(records, events=()):
    lines = []
    printer = ProgressPrinter(write=lines.append)
    for event in events:
        printer.on_event(event)
    for record in records:
        printer.on_record(record)
    printer.flush()
    return lines

def s(id, kind, t, **f): return {"id": id, "phase": "start", "kind": kind, "t_ms": t, **f}
def e(id, kind, t, ok=True, **f): return {"id": id, "phase": "end", "kind": kind, "t_ms": t, "ok": ok, **f}

def test_a_tool_with_its_commands():
    assert printed([s(1, "tool", 0, name="click_part", step=7, intent="메뉴를 연다"),
                    s(2, "cli", 10, parent=1, name="focus-window"), e(2, "cli", 310),
                    s(3, "cli", 320, parent=1, name="mouse-click"), e(3, "cli", 720),
                    e(1, "tool", 2800)]) == [
        "▶ click_part · 메뉴를 연다", "   ○ focus-window · 0.3초", "   ○ mouse-click · 0.4초", "○ click_part · 2.8초"]

def test_model_lines_carry_step_and_tokens():
    assert printed([s(1, "model", 0, step=7), e(1, "model", 14200, tokens=3120)]) == [
        "▶ 모델 생각 중 · 스텝 7", "○ 모델 응답 · 14.2초 · 토큰 3,120"]

def test_repeated_reads_print_one_line():
    records = [s(1, "ready", 0)]
    for i in range(24):
        records += [s(2 + i, "cli", i * 500, parent=1, name="snapshot"), e(2 + i, "cli", i * 500 + 500, ok=i % 12 != 0)]
    records.append(e(1, "ready", 12_500))
    assert printed(records) == ["▶ 대상 앱 준비 중", "   ○ snapshot ×24 · 12.0초 · 실패 2", "○ 대상 앱 준비 · 12.5초"]

def test_read_only_tools_group_and_hide_children():
    records = []
    for i in range(3):
        base = i * 10
        records += [s(base + 1, "tool", i * 1000, name="observe", read_only=True),
                    s(base + 2, "cli", i * 1000 + 1, parent=base + 1, name="snapshot"), e(base + 2, "cli", i * 1000 + 400),
                    e(base + 1, "tool", i * 1000 + 500)]
    assert printed(records) == ["○ observe ×3 · 1.5초"]

def test_failure_and_mismatch_marks():
    assert printed([s(1, "tool", 0, name="click_part"), e(1, "tool", 400, ok=False, code="focus_failed"),
                    s(2, "tool", 500, name="click"), e(2, "tool", 900, mismatch=True)]) == [
        "▶ click_part", "× click_part · 0.4초 · focus_failed", "▶ click", "△ click · 0.4초 · 기대와 다름"]

def test_long_intent_is_cut_and_newlines_become_spaces():
    line = printed([s(1, "tool", 0, name="click", intent="가" * 70)])[0]
    assert line == "▶ click · " + "가" * 59 + "…"

def test_stage_line_counts_from_the_plan():
    events = [{"type": "plan", "stages": [{}, {}, {}, {}]}, {"type": "stage", "n": 2, "status": "started"}]
    assert printed([], events) == ["[단계 2/4]"]

def test_every_mark_survives_cp949():
    for mark in "▶○△×…":
        mark.encode("cp949")
```

`tests/test_run_manager.py`: `print_progress=True`로 만든 매니저에서 실행 하나(기존 테스트의 가짜 모델과 desktop)를 돌리면 `capsys`의 출력에 `▶ 모델 생각 중`이 있다. `False`(기본)면 없다.

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest -q -p no:cacheprovider tests/test_progress_printer.py tests/test_run_manager.py -k "printer or print_progress or line or mark or tool or model or stage or read"`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현하고 연결한다** (위 Interfaces 그대로. `run.py`의 `make_logger`는 `recorder.on_event`만 남기고, 지금 도구마다 찍던 줄은 지운다.)

- [ ] **Step 4: 전체 테스트를 돌린다**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: 전부 통과. `run.py`의 도구 줄 출력을 확인하던 테스트가 있으면 새 프린터 줄을 기대하도록 고친다.

---

### Task 5: 서버 API와 내보내기

**Files:**
- Modify: `examples/llm-agent/viewer/server.py` (라우팅 `segments[3] == "progress"`, 새 `_send_progress`)
- Modify: `examples/llm-agent/viewer/export.py` (`_record`에 `"progress"`)
- Test: `examples/llm-agent/tests/test_server.py`, `examples/llm-agent/tests/test_export.py`

**Interfaces:**
- Produces: `GET /api/runs/<id>/progress?since=N` → `{"records": [...], "next": N}`. 인증, 실행 id 검사, `since` 검사, 반쯤 쓰인 마지막 줄 처리는 `_send_events`와 같고 `read_events(run_dir / "progress.jsonl", since)`를 그대로 쓴다. 파일이 없으면 `{"records": [], "next": 0}`이다.
- Produces: 내보내기 레코드의 `"progress"`. `_events(run_dir / "progress.jsonl")`로 읽고, 파일이 없으면 `[]`이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (기존 서버, 내보내기 테스트 도우미를 따른다)

```python
def test_progress_records_are_served_after_since(...):
    # progress.jsonl에 세 줄 → since=1이면 records 두 개, next 3
def test_progress_of_a_run_without_the_file_is_empty(...):
    # {"records": [], "next": 0}
def test_progress_needs_the_token(...):
    # 토큰 없이 부르면 events와 같은 거절 상태 코드
def test_export_carries_progress(tmp_path):
    # 실행 폴더에 progress.jsonl → payload["progress"]가 그 레코드들
def test_export_without_progress_has_an_empty_list(tmp_path):
    # payload["progress"] == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest -q -p no:cacheprovider tests/test_server.py tests/test_export.py -k progress`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현한다**

- [ ] **Step 4: 전체 테스트를 돌린다**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: 전부 통과

---

### Task 6: 웹 "지금" 패널과 스텝 상세

**Files:**
- Create: `examples/llm-agent/viewer/static/progress.js` (순수 함수, DOM 없음)
- Create: `examples/llm-agent/tests/js/progress.test.mjs` (Node 내장 `node:test`)
- Create: `examples/llm-agent/tests/test_progress_js.py` (`shutil.which("node")`이 없으면 skip, 있으면 `node --test tests/js/progress.test.mjs`가 0으로 끝나는지 봄)
- Modify: `examples/llm-agent/viewer/static/index.html` (`app.js` 앞에 `<script src="/static/progress.js" defer></script>`)
- Modify: `examples/llm-agent/viewer/static/app.js` (진행 기록 폴링, "지금" 패널, 스텝 상세, 내보낸 파일에서 `record.progress` 읽기)
- Modify: `examples/llm-agent/viewer/static/style.css`

**Interfaces:**
- Consumes: Task 5의 API와 내보내기 `progress`
- Produces (`progress.js`, 브라우저에서는 전역 `AgentProgress`, Node에서는 `module.exports`):
  - `buildProgress(records) -> {items: Map<id, Item>, roots: number[], lastMs: number}`. `Item`은 `{id, kind, name, step, intent, readOnly, parent, startMs, endMs (없으면 null), ok, code, mismatch, tokens, seenStart, seenEnd, children: number[]}`이다. 끝이 없는 항목은 `endMs: null`이다.
  - `groupSiblings(progress, ids) -> Group[]`. `Group`은 `{kind, name, count, ms, failures, items}`이다. 묶기 규칙은 Task 4와 같다(연달아 끝난 같은 `(kind, name)`의 `cli` 또는 `readOnly` 도구). 끝나지 않은 항목은 묶지 않는다.
  - `openChain(progress) -> Item[]`. 열린 항목을 바깥부터 안쪽 순서로 돌려준다. 맨 안쪽 열린 항목에서 `parent`를 따라 올라가 만든다.
  - `recentDone(progress, n) -> Group[]`. 끝난 항목 중 맨 안쪽 열린 항목의 형제를 끝난 순서로 묶어 마지막 `n`개를 준다. 열린 항목이 없으면 최상위 항목에서 고른다.
  - `toolForEventLine(progress, line) -> Item | null`. `seenStart < line <= seenEnd`인 `tool` 항목 중 가장 안쪽(조상이 가장 많은) 것이다.
  - `formatSeconds(ms) -> string`. 예: `"2.8초"`.
- `app.js`:
  - `state.progress`(레코드 배열)와 `state.progressNext`. `pollLive`가 이벤트를 받을 때 같은 주기로 `/progress?since=`를 받는다. 받은 시각을 `state.progressReceivedAt`(`Date.now()`)에 둔다.
  - "지금" 패널(`#now-panel`, 타임라인 위)은 실행 중일 때만 보인다.
    - 첫 줄은 `단계 n/m · 스텝 k`이다. 단계는 기존 단계 표시의 데이터를 쓰고, 스텝은 마지막 `model` 항목의 `step`이다.
    - 그 아래 `openChain`의 항목마다 한 줄씩, 안쪽일수록 들여 쓴다. 항목 글은 `모델 생각 중`, `{name} · {intent}`, `대상 앱 준비 중`, `{cli 이름}` 중 하나이고, 흐른 시간은 `lastMs + (Date.now() - progressReceivedAt) - startMs`로 계산한다.
    - 그 아래 `recentDone(…, 5)`를 `○/△/×` 기호와 함께 보여 준다.
    - 1초 타이머로 흐른 시간만 다시 그린다.
  - 스텝 상세: 고른 타임라인 항목을 만든 이벤트의 줄 번호(`state.events`에서의 위치 + 1)로 `toolForEventLine`을 부른다. 있으면 `걸린 시간 · 2.8초`와 `<details>`(기본 접힘) 안에 `groupSiblings(children)` 목록을 넣는다. 없으면 넣지 않는다.
  - 내보낸 파일에서는 `record.progress`로 `state.progress`를 채운다. 패널은 보이지 않고 스텝 상세만 보인다.

- [ ] **Step 1: 실패하는 JS 테스트를 쓴다** (`tests/js/progress.test.mjs`)

```js
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const P = createRequire(import.meta.url)("../../viewer/static/progress.js");

const s = (id, kind, t, f = {}) => ({ id, phase: "start", kind, t_ms: t, ...f });
const e = (id, kind, t, f = {}) => ({ id, phase: "end", kind, t_ms: t, ok: true, ...f });

test("openChain lists open items outermost first", () => {
  const p = P.buildProgress([s(1, "tool", 0, { name: "click_part" }), s(2, "cli", 5, { parent: 1, name: "mouse-click" })]);
  assert.deepEqual(P.openChain(p).map((i) => i.id), [1, 2]);
});

test("groupSiblings folds repeated reads and counts failures", () => {
  const records = [s(1, "ready", 0)];
  for (let i = 0; i < 4; i++) records.push(s(2 + i, "cli", i * 500, { parent: 1, name: "snapshot" }), e(2 + i, "cli", i * 500 + 500, { ok: i !== 0 }));
  const p = P.buildProgress(records);
  const groups = P.groupSiblings(p, p.items.get(1).children);
  assert.equal(groups.length, 1);
  assert.deepEqual([groups[0].name, groups[0].count, groups[0].ms, groups[0].failures], ["snapshot", 4, 2000, 1]);
});

test("toolForEventLine picks the innermost tool around the line", () => {
  const p = P.buildProgress([
    s(1, "tool", 0, { name: "click", events_seen: 3 }), e(1, "tool", 100, { events_seen: 5 }),
    s(2, "tool", 200, { name: "click", events_seen: 5 }), e(2, "tool", 300, { events_seen: 7 })]);
  assert.equal(P.toolForEventLine(p, 5).id, 1);
  assert.equal(P.toolForEventLine(p, 6).id, 2);
  assert.equal(P.toolForEventLine(p, 9), null);
});

test("recentDone keeps the last n finished siblings", () => {
  const records = [s(1, "tool", 0, { name: "click" })];
  for (let i = 0; i < 7; i++) records.push(s(10 + i, "cli", i * 10, { parent: 1, name: `c${i}` }), e(10 + i, "cli", i * 10 + 5));
  assert.deepEqual(P.recentDone(P.buildProgress(records), 5).map((g) => g.name), ["c2", "c3", "c4", "c5", "c6"]);
});

test("formatSeconds", () => assert.equal(P.formatSeconds(2800), "2.8초"));
```

- [ ] **Step 2: 실패를 확인한다**

Run: `node --test tests/js/progress.test.mjs`
Expected: `progress.js`가 없어 실패

- [ ] **Step 3: `progress.js`를 구현한다** (`(function (root) { ... if (typeof module !== "undefined") module.exports = api; else root.AgentProgress = api; })(this)`)

- [ ] **Step 4: JS 테스트와 Python 래퍼를 돌린다**

Run: `node --test tests/js/progress.test.mjs && python -m pytest -q -p no:cacheprovider tests/test_progress_js.py`
Expected: 5 pass, 1 passed

- [ ] **Step 5: `app.js`, `index.html`, `style.css`를 고친다** (위 Interfaces 그대로)

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: 전부 통과. 내보내기가 `index.html`의 스크립트 목록을 모두 담는지 확인하는 테스트가 `progress.js`도 담는지 본다.

- [ ] **Step 7: 눈으로 확인한다**

진행 기록이 있는 실행 폴더를 하나 만든다. 앱을 띄우지 않도록 `tests`의 재생 도구로 짧은 실행을 기록하거나, Task 1~3의 테스트가 쓴 형식으로 직접 만든다. `python view.py --open <run_id>`로 열어 스텝 상세의 시간과 접힌 CLI 목록을 확인하고, `--export`로 만든 HTML에서도 확인한다. 실행 중 "지금" 패널은 실제 실행이 필요하므로, 사용자에게 묻고 가상 MES로 확인하거나 회사 실행에 맡긴다.

---

## 마무리

- 설계 문서의 상태를 "구현됨"으로 바꾼다.
- `CLAUDE.md`의 `examples/llm-agent` 절에 한 줄을 더한다. 진행 기록(`progress.jsonl`, `agent/progress.py`)은 회사 PC 전용이고 명령 이름만 담으며, 요약에는 넣지 않는다는 내용이다.
