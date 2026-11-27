# 대상 창을 맨 앞으로 올리기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 다른 프로세스가 전경에 있어도 CLI가 대상 창을 올리고, 하니스는 준비 직후 창을 올리며, 모델이 `focus_window`로 직접 올리고, 실패하면 CLI의 이유가 모델과 기록에 남게 한다.

**Architecture:** CLI는 `raise_with_budget`의 두 번째 시도부터 현재 전경 창의 입력 스레드에 붙어(`AttachThreadInput`) 창을 올린다. 붙는 조건 판단은 순수 함수로, 붙고 떼기는 Drop으로 떼는 구조체로 새 파일에 둔다. 하니스는 `focus-window` 호출을 `Toolbox._focus` 하나로 모으고, `_physical`, 새 도구, 준비 직후 올리기가 모두 그것을 쓴다.

**Tech Stack:** Rust(`windows-sys`), Python 3(pytest), 캡처한 CLI 응답을 재생하는 테스트(`tests/fake_cli.py`, `tests/ready_support.py`)

**Spec:** `examples/llm-agent/docs/specs/2026-11-27-window-focus-design.md`

## Global Constraints

- Rust: 손으로 쓴 `.rs` 파일은 400줄 이하, 인라인 주석 금지(`///`, `//!`만), 테스트 밖에서 `unwrap`/`expect`/`panic!` 금지, 테스트는 `foo_tests.rs`로 옆에 두고 `#[cfg(test)] #[path = "foo_tests.rs"] mod tests;`로 선언, 함수 인자 5개 이하, 구조체 하나에 파일 하나.
- Rust 명령은 저장소 루트에서 실행한다. 툴체인은 `1.89.0-x86_64-pc-windows-gnu`이다.
- **사용자가 PC를 쓰는 동안 `agent-desktop-windows --lib` 전체를 돌리지 않는다.** 이름으로 거른 테스트만 돌린다. 실제 창을 쓰는 테스트는 사용자 확인 뒤에만 돌린다.
- 하니스 코드(`agent/`, `viewer/`)에는 앱 이름이나 화면 글을 넣지 않는다(`tests/test_application_agnostic.py`).
- 요약과 `run.json`에는 코드와 수만 넣는다. 창 제목과 CLI 메시지 글은 넣지 않는다.
- 커밋은 Conventional Commits, 소문자 명령형 요약. `Co-Authored-By`나 AI 표기를 넣지 않는다(AGENTS.md).
- Python 테스트는 `examples/llm-agent`에서 `conda activate AgentDesktop` 뒤 `python -m pytest -q`로 돌린다.

## Review Focus

- 전경 창이 응답 없는 앱일 때 `AttachThreadInput`으로 붙어 CLI가 함께 멈추면 안 된다. 붙지 않고 기존처럼 실패해야 한다(Task 1 `should_attach` 테스트).
- 붙은 뒤 `SetForegroundWindow`가 실패하거나 소유자 검사로 일찍 나가도 반드시 뗀다(Task 1, Drop으로 보장, 테스트는 구조로 확인).
- 대상이 더 높은 권한일 때는 지금처럼 `PERM_DENIED`이고, 하니스 거절 글에 관리자 권한 안내가 들어간다(Task 2 `test_physical_focus_perm_denied_names_rights`).
- 준비 직후 올리기가 실패해도 실행이 멈추지 않는다(Task 3 `test_ready_focus_failure_does_not_stop_the_run`).
- `focus_window`에 대상 프로세스가 아닌 창 id를 주면 CLI를 부르지 않고 거절한다(Task 2 `test_focus_window_refuses_a_foreign_window`).

---

### Task 1: CLI의 창 올리기에 입력 스레드 붙이기

**Files:**
- Create: `crates/windows/src/system/window/window_input_attach.rs`
- Create: `crates/windows/src/system/window/window_input_attach_tests.rs`
- Modify: `crates/windows/src/system/window/mod.rs` (모듈 등록)
- Modify: `crates/windows/src/system/window/window_activate.rs:123-154`(`raise_with_budget`), `:247-273`(`bring_to_foreground`)
- Modify (필요할 때만): `skills/agent-desktop-windows/SKILL.md`에서 포커스 실패를 설명하는 곳

**Interfaces:**
- Produces: `pub(crate) fn should_attach(current_tid: u32, foreground_tid: u32, foreground_responsive: bool) -> bool`
- Produces: `pub(crate) struct InputAttachment { current_tid: u32, foreground_tid: u32 }` (`Drop`에서 `AttachThreadInput(current, foreground, FALSE)`)
- Produces: `pub(crate) fn attach_to_foreground_input() -> Option<InputAttachment>` (Windows가 아니면 항상 `None`)
- 바뀌는 것: `bring_to_foreground(handle, evidence, attach: bool)`. `raise_with_budget`은 `attach = attempt > 0`을 넘긴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`window_input_attach_tests.rs`)

```rust
use super::should_attach;

#[test]
fn no_foreground_window_is_never_attached() {
    assert!(!should_attach(7, 0, true));
}

#[test]
fn the_callers_own_thread_is_never_attached() {
    assert!(!should_attach(7, 7, true));
}

#[test]
fn a_hung_foreground_thread_is_never_attached() {
    assert!(!should_attach(7, 9, false));
}

#[test]
fn a_responsive_foreign_foreground_thread_is_attached() {
    assert!(should_attach(7, 9, true));
}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `bash scripts/cargo-test-isolated-home.sh test --locked -p agent-desktop-windows --lib window_input_attach`
Expected: 컴파일 실패(`should_attach` 없음)

- [ ] **Step 3: `window_input_attach.rs`를 구현하고 `mod.rs`에 `pub(crate) mod window_input_attach;`를 더한다**

`attach_to_foreground_input`은 `GetForegroundWindow`, `GetWindowThreadProcessId`, `GetCurrentThreadId`, `window_enum::window_is_responsive`로 `should_attach`의 세 인자를 구한다. 참이면 `AttachThreadInput(current, foreground, TRUE)`를 부르고, 성공했을 때만 `Some(InputAttachment)`를 돌려준다. 참고할 코드는 `crates/windows/src/system/fixture/test_support.rs:113-139`의 `stage_foreground`이다. `//!` 모듈 문서에 붙는 이유(Windows는 전경이나 마지막 입력을 가진 스레드의 요청만 받아 준다)와 응답 없는 스레드에 붙지 않는 이유를 적는다.

- [ ] **Step 4: `window_activate.rs`에 연결한다**

`bring_to_foreground`에 `attach: bool`을 더한다. 참이면 두 번째 `owned_for_write` 검사가 통과한 뒤 `let _attachment = attach_to_foreground_input();`로 붙고, 붙었을 때 `BringWindowToTop(handle)`을 부른 다음 `SetForegroundWindow(handle)`를 부른다. 함수가 끝나면 `_attachment`의 Drop이 뗀다. Windows가 아닌 쪽의 `bring_to_foreground`도 인자를 맞춘다. `raise_with_budget`은 `bring_to_foreground(handle, evidence, attempt > 0)`를 부른다. 이미 `FOCUS_STEAL_BUDGET`의 문서는 "attach-and-set-foreground attempt"라고 적고 있으므로 코드가 그 말과 맞게 된다. 파일이 400줄을 넘지 않는지 확인한다.

- [ ] **Step 5: 거른 테스트와 검사를 돌린다**

Run: `bash scripts/cargo-test-isolated-home.sh test --locked -p agent-desktop-windows --lib window_input_attach`
Expected: 4 passed

Run: `cargo fmt --all -- --check && cargo clippy --locked -p agent-desktop-core -p agent-desktop-windows -p agent-desktop -p agent-desktop-ffi --all-targets -- -D warnings && bash scripts/check-rust-file-size.sh`
Expected: 모두 통과

- [ ] **Step 6: 실제 창 테스트는 사용자에게 묻는다**

`window_activate` 테스트(`visible_background_window_is_raised_without_restore` 등)는 실제 창을 띄우고 포커스를 옮긴다. 사용자가 허락하면 `bash scripts/cargo-test-isolated-home.sh test --locked -p agent-desktop-windows --lib window_activate`를 돌리고, 허락하지 않으면 CI(`test-windows`)에 맡긴다. 어느 쪽이었는지 보고에 적는다.

- [ ] **Step 7: 스킬 문서를 확인한다**

`skills/agent-desktop-windows/SKILL.md`에서 `focus-window`와 foreground 거절을 설명하는 줄을 읽는다. 두 번째 시도에서 입력을 붙인다는 사실과 어긋나는 문장이 있으면 고친다. 없으면 고치지 않는다.

- [ ] **Step 8: 커밋한다**

```bash
git add crates/windows/src/system/window/ skills/agent-desktop-windows/
git commit -m "fix: attach to the foreground input thread when a raise is refused"
```

---

### Task 2: 하니스의 포커스 호출을 모으고 `focus_window` 도구를 더하기

**Files:**
- Modify: `examples/llm-agent/agent/tools.py` (`_physical` 438-459, 새 `_focus`, 새 `_tool_focus_window`)
- Modify: `examples/llm-agent/agent/tool_schemas.py` (`restore_window` 옆에 스키마)
- Modify: `examples/llm-agent/agent/loop.py` (시스템 프롬프트 한 줄)
- Test: `examples/llm-agent/tests/test_tools.py`

**Interfaces:**
- Produces: `Toolbox._focus(self, window_id: str) -> dict`. `focus-window --window-id <id>`를 `headed=True`로 부르고 `_summary(envelope)`를 돌려준다(`{"ok": True, ...}` 또는 `{"ok": False, "code", "message", "suggestion", ...}`).
- Produces: 도구 `focus_window(window_id, intent)`, 레벨 0. `READ_ONLY_TOOLS`, `EXPECTING_TOOLS`, `REOBSERVING_TOOLS`에 넣지 않는다. 따라서 intent를 받고, 다시 관찰하지 않고, 가드를 거치지 않는다.
- Produces: 거절 코드 `focus_failed`(CLI가 실패를 돌려줌, 거절에 `"focus_error": {"code", "message", "suggestion"}`를 담음)와 `focus_not_confirmed`(CLI는 성공했지만 창 목록이 포커스 없음).

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_tools.py`, 기존 `launched()`, `allow()`, `WINDOW_A`, `FakeCli.action_results` 사용)

```python
FOCUS_DENIED = {"ok": False, "command": "focus-window", "error": {
    "code": "ACTION_FAILED", "message": "Target window did not become foreground before headed delivery",
    "suggestion": "Retry after ensuring the target window can accept focus"}}
FOCUS_PERM = {"ok": False, "command": "focus-window", "error": {
    "code": "PERM_DENIED", "message": "activation denied"}}


def test_physical_focus_failure_carries_the_cli_reason():
    cli = FakeCli()
    cli.action_results["focus-window"] = FOCUS_DENIED
    toolbox, _ = launched(cli=cli)
    allow(toolbox, (2000, 100))

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert result["refused_code"] == "focus_failed"
    assert result["focus_error"]["code"] == "ACTION_FAILED"
    assert "focus_window" in result["refused"]
    assert "mouse-click" not in cli.command_names()


def test_physical_focus_perm_denied_names_rights():
    cli = FakeCli()
    cli.action_results["focus-window"] = FOCUS_PERM
    toolbox, _ = launched(cli=cli)
    allow(toolbox, (2000, 100))

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert result["focus_error"]["code"] == "PERM_DENIED"
    assert "higher rights" in result["refused"]


def test_physical_unconfirmed_focus_has_its_own_code():
    cli = FakeCli()
    cli.focused = False
    toolbox, _ = launched(cli=cli)
    allow(toolbox, (2000, 100))

    result = toolbox.call("mouse_click", {"window_id": WINDOW_A, "x": 2000, "y": 100, "button": "left"})

    assert result["refused_code"] == "focus_not_confirmed"


def test_focus_window_raises_our_window():
    toolbox, cli = launched()

    result = toolbox.call("focus_window", {"window_id": WINDOW_A, "intent": "앞으로"})

    assert result["ok"] is True
    assert ["focus-window", "--window-id", WINDOW_A] in cli.commands()
    assert "--headed" in cli.full_call("focus-window")
    assert toolbox.current_window == WINDOW_A


def test_focus_window_refuses_a_foreign_window():
    toolbox, cli = launched()

    result = toolbox.call("focus_window", {"window_id": "w-999", "intent": "앞으로"})

    assert result["ok"] is False and "refused" in result
    assert "focus-window" not in cli.command_names()


def test_focus_window_reports_the_cli_error():
    cli = FakeCli()
    cli.action_results["focus-window"] = FOCUS_DENIED
    toolbox, _ = launched(cli=cli)

    result = toolbox.call("focus_window", {"window_id": WINDOW_A, "intent": "앞으로"})

    assert result == {**result, "ok": False, "code": "ACTION_FAILED"}


def test_focus_window_is_offered_at_level_zero():
    names = [d["function"]["name"] if "function" in d else d["name"] for d in definitions_for(0, False)]
    assert "focus_window" in names
```

`FakeCli._respond`는 `action_results`를 마지막에 보므로 `focus-window`도 그 사전으로 바꿀 수 있다. 정의의 이름 자리는 `tool_schemas._tool`이 만드는 모양에 맞춰 고친다.

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest -q tests/test_tools.py -k "focus"`
Expected: 새 테스트 7개 FAIL

- [ ] **Step 3: `tools.py`를 구현한다**

`_focus(window_id)`를 더한다. `_physical`에서 `self._desktop.run("focus-window", ...)` 줄을 `focused = self._focus(window_id)`로 바꾸고, `focused["ok"]`가 거짓이면 `{**_refused(<글>, "focus_failed"), "focus_error": {k: focused[k] for k in ("code", "message", "suggestion") if k in focused}}`를 돌려준다. 거절 글(영어, 모델용)은 다음과 같다.

`Window {id} could not take focus ({code}: {message}), so the point may hit another app. Call focus_window on it and try again.`

`code`가 `PERM_DENIED`이면 뒤에 `The target runs with higher rights than this agent; ask the user to start the agent as administrator.`를 붙인다. 창 목록이 포커스 없음이면 지금 글에 같은 `Call focus_window…` 문장을 붙여 `_refused(..., "focus_not_confirmed")`로 돌려준다. `_tool_focus_window(arguments)`는 `_owned_window`가 None이면 `_refused(f"{window_id} is not a window of the target process")`, 아니면 `self._current_window = window_id` 뒤 `self._focus(window_id)`를 돌려준다.

- [ ] **Step 4: 스키마와 프롬프트를 더한다**

`tool_schemas.py`의 `restore_window` 다음에 `_tool("focus_window", "Bring a window of the target to the front so physical clicks and keys reach it. Use it when an action was refused because the window could not take focus.", 0, window_id=WINDOW)`를 더한다. `loop.py` 시스템 프롬프트의 물리 도구 줄 근처에 한 줄을 더한다: `- When an action is refused with focus_failed or focus_not_confirmed, call focus_window on that window and retry once; if focus_error.code is PERM_DENIED, ask the user.`

- [ ] **Step 5: 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 전부 통과. 시스템 프롬프트 글이나 도구 목록을 그대로 비교하는 테스트가 깨지면 새 도구와 새 줄을 반영해 고친다.

- [ ] **Step 6: 커밋한다**

```bash
git add examples/llm-agent/agent/tools.py examples/llm-agent/agent/tool_schemas.py examples/llm-agent/agent/loop.py examples/llm-agent/tests/
git commit -m "feat: pass the cli focus error on and let the model raise a window"
```

---

### Task 3: 준비 직후 창을 올리고 요약에 남기기

**Files:**
- Modify: `examples/llm-agent/agent/tools.py` (`launch_ready` 성공 분기, `_launch` 성공 분기, 새 `_focus_after_start`)
- Modify: `examples/llm-agent/agent/summary.py` (`포커스:` 줄)
- Modify: `examples/llm-agent/tests/ready_support.py` (`TimelineDesktop.run`이 `focus-window`에 응답)
- Test: `examples/llm-agent/tests/test_loop_ready.py`, `examples/llm-agent/tests/test_tools.py`, `examples/llm-agent/tests/test_summary.py`

**Interfaces:**
- Consumes: Task 2의 `Toolbox._focus(window_id) -> dict`
- Produces: 이벤트 `{"type": "focus", "phase": "start", "ok": bool}`. 실패하면 `"code": <CLI 코드>`를 더한다. 글은 넣지 않는다.
- Produces: `TimelineDesktop.focus_result`(기본값 `{"ok": True, "command": "focus-window", "data": {}}`)
- Produces: 요약 줄 `  포커스: 시작 뒤 실패 · CODE`. 시작 뒤 올리기가 실패한 실행에만 나온다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_loop_ready.py` (기존 `splash_then_main`, `toolbox_for`, `FakeClock` 사용, 이벤트는 그 파일의 기존 테스트처럼 관찰자나 `_emit`을 받는 방식으로 모은다):

```python
def test_ready_raises_the_ready_window():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    toolbox = toolbox_for(desktop, clock)

    toolbox.launch_ready()

    focus_calls = desktop.commands("focus-window")
    assert focus_calls and toolbox.current_window in focus_calls[-1]


def test_ready_focus_failure_does_not_stop_the_run():
    clock = FakeClock()
    desktop = splash_then_main(clock)
    desktop.focus_result = {"ok": False, "command": "focus-window",
                            "error": {"code": "ACTION_FAILED", "message": "refused"}}
    toolbox = toolbox_for(desktop, clock)

    result = toolbox.launch_ready()

    assert result["ok"] is True
    # 이 실행의 이벤트 중 focus 이벤트가 {"type": "focus", "phase": "start", "ok": False, "code": "ACTION_FAILED"}
```

`test_tools.py`:

```python
def test_launch_target_raises_the_launched_window():
    toolbox, cli = launched()
    names = cli.command_names()
    assert names.index("launch") < names.index("focus-window")
```

`test_summary.py` (그 파일의 `summarize` 호출 모양을 따름):

```python
def test_summary_names_a_failed_start_focus_by_code_only():
    events = [{"type": "focus", "phase": "start", "ok": False, "code": "ACTION_FAILED"}]
    lines = summarize(<기존 테스트의 최소 run>, events)
    assert "  포커스: 시작 뒤 실패 · ACTION_FAILED" in lines


def test_summary_has_no_focus_line_when_start_focus_succeeded():
    events = [{"type": "focus", "phase": "start", "ok": True}]
    assert not any(line.startswith("  포커스") for line in summarize(<같은 run>, events))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest -q tests/test_loop_ready.py tests/test_tools.py tests/test_summary.py -k "focus"`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현한다**

`_focus_after_start(self)`: `self._current_window`가 None이면 아무것도 하지 않는다. 아니면 `self._focus(...)`를 부르고 위 모양의 `focus` 이벤트를 `self._emit`으로 남긴다. `launch_ready`의 `outcome.status == "ready"` 분기에서 `_record_read` 다음에, `_launch`에서 `self._current_window`를 정한 다음에 부른다. `TimelineDesktop.run`에 `if args[0] == "focus-window": return self.focus_result`를 더하고 `__init__`에 기본값을 둔다. `summary.py`에 `_focus(events)`를 더해, 마지막 `focus`/`start` 이벤트가 `ok: False`이면 `"시작 뒤 실패" + SEPARATOR + _code(code)`를 돌려주게 하고, `summarize`가 그 값이 있을 때 `"  포커스: " + 값`을 더하게 한다. 위치는 `준비:` 줄 다음이다. 뷰어의 `buildEntries`가 모르는 이벤트 종류를 건너뛰는지 `viewer/static/`에서 확인한다(`ready` 이벤트가 이미 같은 방식으로 들어간다). 건너뛰지 않으면 건너뛰게 고친다.

- [ ] **Step 4: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 전부 통과. 실행 직후 명령 순서나 호출 수를 그대로 비교하던 테스트가 `focus-window` 때문에 깨지면, 새 호출을 반영해 기대값을 고친다. 하니스 동작을 되돌리지 않는다.

- [ ] **Step 5: 커밋한다**

```bash
git add examples/llm-agent/agent/tools.py examples/llm-agent/agent/summary.py examples/llm-agent/tests/
git commit -m "feat: raise the target window once it is ready and note a failure in the summary"
```

---

## 마무리 확인

- `cargo build --locked -p agent-desktop`로 새 exe를 만든다. 회사에는 이 exe를 가져가야 하고, `AGENT_DESKTOP_BIN`이나 `target/debug`로 찾게 한다.
- 픽스처로 하는 실제 확인(브라우저를 앞에 둔 채 `focus-window`)은 데스크톱을 건드리므로 사용자에게 묻고 한다.
- 설계 문서의 상태를 "구현됨"으로 바꾼다.
