# 뷰어에서 실행 멈추기와 안전하게 종료하기 구현 계획

> **에이전트 작업자에게:** 작업마다 superpowers:test-driven-development를 따릅니다. 단계는 체크박스(`- [ ]`)로 표시합니다.

- 날짜: 2026-11-18
- 상태: 사용자 승인(2026-11-18, "그렇게 진행해"), 작업 1~5 수락, 통합 확인 끝(커밋 전)
- 설계: [뷰어에서 실행 멈추기와 안전하게 종료하기 설계](../specs/2026-11-18-viewer-stop-and-shutdown-design.md) (사용자 승인 2026-11-18). 값과 규칙은 설계가 기준이고, 이 계획은 파일, 이름, 시그니처, 테스트, 순서를 정합니다.

**목표:** 뷰어에서 실행을 안전한 자리에서 멈추고, 뷰어를 버튼이나 Ctrl+C로 정리하며 끄고, 다시 열 때 옛 뷰어와 포트가 겹치지 않게 합니다.

**구조:** 실행마다 멈춤 신호(`threading.Event`)를 두고 루프, 준비 기다리기, 질문·승인 대기가 그것을 봅니다(`RunManager.stop`). 서버는 멈춤·종료 경로와 포트 독점을 갖고, 새 `viewer/instance.py`가 기록 폴더의 `.viewer.json`으로 옛 뷰어를 알아보고 끕니다. 화면은 두 버튼과 화면 안 확인 띠를 더합니다.

**쓰는 것:** Python 표준 라이브러리(`threading`, `socket`, `signal`, `urllib.request`)와 지금의 의존성. 더하는 의존성은 없습니다.

## 전체에 걸친 조건

- 멈춤을 요청하지 않은 실행의 동작과 기록은 바뀌지 않습니다. 지금의 테스트는 고치지 않고 통과해야 합니다. 고쳐야 하면 오케스트레이터에게 먼저 묻습니다.
- 멈춤 이유 글은 `USER_STOP_REASON = "사용자가 실행을 멈췄습니다"`(`agent/loop.py`) 하나입니다.
- 대상 앱은 멈추거나 종료해도 닫지 않습니다(`close_process`를 부르지 않음).
- 진행 중인 모델 호출과 CLI 명령은 끊지 않습니다. 멈춤은 모델 호출 전, 도구 실행 전, 대기 중에만 효력이 납니다.
- 시간 값: 종료 때 실행 마무리 대기 `SHUTDOWN_WAIT_S = 120`, 옛 뷰어 종료 대기 `TAKEOVER_WAIT_S = 10`, 빈 포트 찾기 `PORT_TRIES = 20`.
- 브라우저의 `alert`/`confirm`/`prompt`를 쓰지 않습니다. 화면 글은 `textContent`로만 넣습니다.
- `tests\test_application_agnostic.py` 통과.
- 작업자는 커밋하지 않고, 실제 창을 띄우는 실행과 실제 모델 호출을 하지 않습니다. 실제 서버 소켓은 `127.0.0.1`의 테스트용 포트로만 엽니다.
- 테스트는 `examples/llm-agent`에서 `/c/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -m pytest -q`(아래 `pytest`)로 돌립니다. 제출 전 전체 실패 0.

## 검토에서 특히 볼 것

1. **사람의 답을 기다리는 질문 중에 멈춤을 누를 때.** 30분 기다리지 않고 바로 끝나야 하고, 이유가 "답이 없어 멈춤"이 아니라 `USER_STOP_REASON`이어야 합니다. (작업 1)
2. **멈춤 요청과 동시에 모델 호출이 진행 중일 때.** 그 호출의 결과로 도구를 실행하지 않고 멈춰야 합니다(응답을 받은 뒤 도구 실행 전 확인). (작업 1)
3. **Windows에서 같은 포트에 두 번째 뷰어를 열 때.** 겹쳐 열리지 않고 실패해야 하며, 그 실패를 받아 옛 뷰어를 끄거나 다음 포트로 갑니다. (작업 3)
4. **옛 뷰어가 실행 중일 때 새 뷰어를 열 때.** 옛 뷰어의 실행을 끊지 않고 다음 포트로 열어야 합니다. (작업 4)
5. **죽은 프로세스의 `.viewer.json`이 남아 있고 그 pid를 다른 프로그램이 쓰고 있을 때.** 시작 시각이 다르므로 옛 뷰어로 보지 않고, 그 프로그램에 종료 요청을 보내지 않아야 합니다. (작업 4)

## 배정과 순서

| 배정 | 작업자 | 작업 | 편집 경로 |
| --- | --- | --- | --- |
| `vss-w1-stop` | w1 | 1, 2 | `agent/loop.py`, `agent/ask.py`, `agent/approval.py`, `agent/ready.py`, `agent/tools.py`, `agent/recorder.py`, `viewer/run_manager.py`, 각 테스트 |
| `vss-w2-server` | w2 | 3, 4 | `viewer/server.py`, `viewer/instance.py`(새), `view.py`, 각 테스트 |
| `vss-w3-ui` | w3 | 5 | `viewer/static/*`, `tests/test_server.py`(정적 파일 검사만) |

README와 `CLAUDE.md`는 오케스트레이터가 마지막에 고칩니다. 작업 3·4·5는 계약 C3·C4만 보고 가짜 manager로 바로 시작합니다. 작업 2는 작업 1 수락 뒤입니다.

## 계약

**C1. 멈춤 신호 (작업 1)**
- `run_agent(model, toolbox, task, log=..., gate=None, register=None, stop=None)`: `stop`은 `is_set()`이 있는 객체(`threading.Event`). `None`이면 지금 그대로.
- 루프는 모델 호출 전, 응답을 받은 뒤 각 도구 실행 전, 준비 기다리기에서 `stop.is_set()`을 봅니다. 참이면 `AgentStopped(USER_STOP_REASON)`. `AgentStopped`를 잡는 곳에서 `stop`이 켜져 있으면 이유를 `USER_STOP_REASON`으로 바꿉니다(질문·승인이 "답 없음"으로 풀린 경우).
- `AskGate.close()`, `ApprovalGate.close()`: 기다리던 것을 모두 `NO_ANSWER`로 풀고, 이후의 `ask`/`review`도 바로 `NO_ANSWER`를 돌려줍니다.
- `Toolbox.launch_ready(cancelled=None)`와 `wait_until_ready(..., cancelled=None)`: `cancelled()`가 참이면 다음 회차 전에 `ReadyOutcome(status="cancelled")`. `READY_STATUSES`에 `"cancelled"`, `READY_MESSAGES["cancelled"] = USER_STOP_REASON`과 같은 글.

**C2. `RunManager` (작업 2)**
- `stop(run_id) -> bool`: 진행 중인 그 실행이면 멈춤 신호를 켜고, 두 대기를 `close()`하고, 녹음기에 `{"type": "stop_requested"}`를 남기고 True. 아니면 False.
- `stop_and_wait(timeout_s) -> bool`: 진행 중인 실행이 있으면 `stop`하고 실행 스레드를 `timeout_s`까지 기다림. 끝에 진행 중인 실행이 없으면 True.
- `stopping() -> bool`: 멈춤을 요청했고 아직 끝나지 않았으면 True.

**C3. 서버 API (작업 3)**
- `POST /api/runs/<id>/stop`: 200 `{"stopping": true}`, 진행 중이 아니면 409 `{"error"}`, manager가 없으면 503.
- `POST /api/shutdown` 본문 `{"stop_run": bool}`:
  - 종료를 허용하지 않는 서버(`allow_shutdown=False`)면 404.
  - 실행 중인데 `stop_run`이 거짓이면 409 `{"error", "active": <run_id>}`.
  - 아니면 202 `{"shutting_down": true, "run_stopping": bool}`를 보내고, 별도 스레드에서 `stop_and_wait(SHUTDOWN_WAIT_S)` 뒤 `server.shutdown()`.
- `/api/status`에 `"can_shutdown": bool`, `"stopping": bool`.
- `make_server(..., allow_shutdown=False)`; `view.py`만 True.
- `ViewerServer`는 Windows에서 `allow_reuse_address = False`, `server_bind`에서 `SO_EXCLUSIVEADDRUSE`를 켭니다.

**C4. `viewer/instance.py` (작업 4)**

```python
INSTANCE_FILE = ".viewer.json"
def write_instance(runs_dir, port, token, pid=None, started_at=None) -> Path
def read_instance(runs_dir) -> dict | None          # 읽을 수 없거나 모양이 틀리면 None
def clear_instance(runs_dir, token) -> None         # 파일의 token이 같을 때만 지움
def instance_alive(info, started_at_of=process_started_at) -> bool
def request_shutdown(info, stop_run, timeout_s=5, opener=urllib.request.urlopen) -> tuple[int | None, dict]
def open_viewer(runs_dir, port, make, *, read=read_instance, alive=instance_alive,
                shutdown=request_shutdown, wait=TAKEOVER_WAIT_S, sleep=time.sleep, clock=time.monotonic)
    -> tuple[ViewerServer, str | None]               # (서버, 사람에게 알릴 글 또는 None). make(port)가 서버를 만듦
```

---

### 작업 1: 멈춤 신호를 하니스에 잇기 (w1)

**파일:** `agent/loop.py`, `agent/ask.py`, `agent/approval.py`, `agent/ready.py`, `agent/tools.py`. 테스트: `tests/test_loop_stop.py`(새), `tests/test_ask.py`, `tests/test_approval.py`, `tests/test_ready.py`.

**인터페이스:** C1을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_stop_before_a_model_call_ends_without_calling_it(): ...   # stop 켜진 채 시작 → 모델 호출 0, stopped, USER_STOP_REASON
def test_stop_set_during_a_model_call_runs_no_tool_from_its_answer(): ...
    # 대본 모델이 응답하면서 stop을 켬, 응답의 도구는 click → click 명령 0, stopped
def test_stop_while_a_question_waits_ends_at_once_with_the_user_reason(): ...
    # 실제 AskGate(timeout 1800)와 다른 스레드의 stop+close → 몇 초 안에 끝, reason USER_STOP_REASON
def test_stop_while_an_approval_waits_ends_with_the_user_reason(): ...
def test_stop_during_the_ready_wait_is_cancelled(): ...            # TimelineDesktop, 2회차 뒤 cancelled → status cancelled, 모델 호출 0
def test_closed_ask_gate_answers_no_answer_now_and_later(): ...
def test_closed_approval_gate_answers_no_answer_now_and_later(): ...
def test_without_stop_the_run_is_unchanged(): ...
def test_stopping_never_closes_the_target(): ...                   # close_process 호출 0
```

- [ ] **2단계:** `pytest tests/test_loop_stop.py tests/test_ask.py tests/test_approval.py tests/test_ready.py`. 기대: 새 테스트 실패.
- [ ] **3단계:** C1대로 구현합니다. `_Run`이 `stop`을 받아 `_turns`의 맨 앞과 각 도구 실행 앞에서 확인하고, `_start`의 `except AgentStopped`에서 이유를 바꿉니다. 준비 기다리기는 `launch_ready(cancelled=stop.is_set)`.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 2: `RunManager`의 멈춤과 기록 (w1, 작업 1 수락 뒤)

**파일:** `viewer/run_manager.py`, `agent/recorder.py`. 테스트: `tests/test_run_manager.py`, `tests/test_recorder.py`.

**인터페이스:** C1을 쓰고 C2를 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_stop_ends_the_active_run_as_stopped_with_a_summary(tmp_path): ...
    # GatedModel이 기다리는 동안 stop → run.json status stopped, reason USER_STOP_REASON, summary.txt 있음, 자리 반납
def test_stop_of_another_or_no_run_is_false(tmp_path): ...
def test_stop_answers_a_waiting_question_at_once(tmp_path): ...    # 질문 대기 중 stop → 1초 안에 끝
def test_stop_and_wait_returns_true_once_the_run_ended(tmp_path): ...
def test_stop_and_wait_gives_up_after_the_timeout(tmp_path): ...  # 멈추지 않는 모델 대역, timeout 0.2 → False
def test_stopping_is_true_until_the_run_ends(tmp_path): ...
def test_stop_requested_is_recorded(tmp_path): ...                 # events.jsonl에 stop_requested
```

- [ ] **2단계:** 해당 테스트. 기대: 실패.
- [ ] **3단계:** C2대로 구현합니다. 실행마다 `threading.Event`를 만들어 `run_agent(..., stop=event)`에 넘깁니다. `recorder.py`의 `KEPT_EVENTS`에 `"stop_requested"`.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 3: 서버의 멈춤·종료 경로와 포트 독점 (w2)

**파일:** `viewer/server.py`. 테스트: `tests/test_server.py`.

**인터페이스:** C3을 만듭니다. manager는 `stop`, `stop_and_wait`, `stopping`, `active`를 가진 가짜로 시험합니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_stop_route_stops_the_active_run(): ...
def test_stop_route_refuses_a_run_that_is_not_active(): ...        # 409
def test_shutdown_is_not_offered_unless_allowed(): ...             # allow_shutdown False → 404, status can_shutdown False
def test_shutdown_with_an_active_run_needs_stop_run(): ...         # 409, active 담김, manager.stop 호출 0
def test_shutdown_stops_the_run_then_closes_the_server(): ...      # 202, stop_and_wait(120) 호출, serve_forever가 끝남
def test_shutdown_without_a_run_closes_at_once(): ...
def test_status_says_stopping_and_can_shutdown(): ...
@pytest.mark.skipif(os.name != "nt", reason="Windows socket option")
def test_a_second_server_cannot_share_the_port(): ...              # 같은 포트 두 번째 make_server → OSError
```

- [ ] **2단계:** `pytest tests/test_server.py`. 기대: 새 테스트 실패.
- [ ] **3단계:** C3대로 구현합니다. 종료 요청은 응답을 다 보낸 뒤 별도 스레드에서 처리하고, 그 스레드는 `server.shutdown()`만 부릅니다(`server_close`는 `view.py`가 함).
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 4: 다시 열기와 Ctrl+C (w2, 작업 3 수락 뒤)

**파일:** `viewer/instance.py`(새), `view.py`. 테스트: `tests/test_instance.py`(새), `tests/test_view.py`.

**인터페이스:** C3을 쓰고 C4를 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_instance.py
def test_write_read_and_clear_only_with_the_same_token(tmp_path): ...
def test_broken_instance_file_reads_as_none(tmp_path): ...
def test_alive_needs_the_same_start_time(): ...                    # 같은 pid, 다른 시작 시각 → False (검토 5번)
def test_free_port_opens_without_asking_anyone(tmp_path): ...
def test_idle_old_viewer_is_shut_down_and_its_port_reused(tmp_path): ...
    # make가 처음 OSError, 살아 있는 옛 뷰어, shutdown이 202 → 그 뒤 make 성공, 같은 포트, 알림 글
def test_old_viewer_with_a_run_is_left_alone(tmp_path): ...       # shutdown이 409 → 다음 포트, 글에 "실행 중" (검토 4번)
def test_a_port_held_by_something_else_moves_to_the_next(tmp_path): ...  # 파일 없음/죽은 프로세스 → 요청 0, 다음 포트
def test_all_ports_taken_raises(tmp_path): ...                     # 20개 모두 OSError → OSError
def test_request_shutdown_sends_the_token_and_stop_run(): ...      # opener 대역이 받은 URL, 헤더, 본문
# test_view.py
def test_viewer_writes_and_clears_the_instance_file(tmp_path, monkeypatch): ...
def test_ctrl_c_stops_the_run_before_closing(tmp_path, monkeypatch): ...  # serve_forever가 KeyboardInterrupt → stop_and_wait 호출
def test_second_ctrl_c_closes_without_waiting(tmp_path, monkeypatch): ...
def test_stop_option_asks_the_running_viewer_to_stop(tmp_path, monkeypatch, capsys): ...
def test_stop_option_without_a_viewer_says_so(tmp_path, monkeypatch, capsys): ...
```

- [ ] **2단계:** 해당 테스트. 기대: 실패.
- [ ] **3단계: 구현합니다.**
  - `open_viewer`: 설계 4절의 순서. 옛 뷰어를 끈 뒤 `wait` 안에서 `make(port)`를 다시 시도합니다.
  - `view.py`: `make_server(..., allow_shutdown=True)`를 `open_viewer`로 열고, 알림 글을 출력하고, 표시 파일을 씁니다. `signal.SIGBREAK`(있을 때)를 Ctrl+C와 같게 둡니다. `serve_forever`가 `KeyboardInterrupt`로 끝나면 "실행을 멈추는 중입니다. 바로 끄려면 Ctrl+C를 한 번 더 누르세요"를 출력하고 `run_manager.stop_and_wait(SHUTDOWN_WAIT_S)`를 부르며, 그동안의 두 번째 `KeyboardInterrupt`는 기다림만 끝냅니다. 끝에 `server_close`와 `clear_instance`.
  - `--stop`: 표시 파일의 뷰어가 살아 있으면 `request_shutdown(stop_run=True)` 결과를 출력하고 0, 없으면 "떠 있는 뷰어가 없습니다"를 출력하고 파일을 지운 뒤 0.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 5: 화면의 두 버튼과 확인 띠 (w3)

**파일:** `viewer/static/index.html`, `viewer/static/app.js`, `viewer/static/style.css`. 테스트: `tests/test_server.py`(정적 파일 검사 하나).

**인터페이스:** C3을 씁니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.** `test_page_has_stop_and_shutdown_controls`: `index.html`에 `id="stop-run"`, `id="shutdown-viewer"`, `id="confirm-strip"`, `app.js`에 `/api/shutdown`, `"/stop"`, `stop_run`이 있고 `confirm(`/`alert(`가 없습니다.
- [ ] **2단계:** `pytest tests/test_server.py -k stop_and_shutdown`. 기대: 실패.
- [ ] **3단계: 구현합니다.**
  - 위쪽 줄 오른쪽에 "실행 멈춤"(진행 중인 실행이 있을 때만 켜짐)과 "뷰어 종료"(`can_shutdown`일 때만 보임).
  - 확인 띠 문구는 설계 5절 그대로. 실행 중 종료는 먼저 `stop_run: false`로 보내 409를 받으면 띠를 띄우고, "멈추고 종료"로 `stop_run: true`를 다시 보냅니다.
  - 멈춤 요청 뒤 `stopping`이 참인 동안 "멈추는 중…"을 보여 줍니다.
  - 종료 응답(202) 뒤 "종료하는 중…"을 띄우고 상태 요청이 실패할 때까지 1초마다 묻다가 "뷰어를 종료했습니다. 다시 열려면 터미널에서 python view.py"로 바꾸고 폴링을 모두 멈춥니다.
- [ ] **4단계:** `node --check viewer/static/app.js`, `pytest`. 오케스트레이터가 헤드리스 Chrome으로 1600px와 390px 폭에서 확인합니다.

### 오케스트레이터: 통합과 문서

- [ ] 전체 `pytest`. 실제 `view.py`를 테스트 포트로 띄워(대상 앱 없이) 두 번째 `view.py`가 옛 뷰어를 끄고 같은 포트로 여는지, `--stop`이 끄는지 확인합니다.
- [ ] 가짜 manager와 헤드리스 Chrome으로 실행 멈춤, 실행 중 종료 확인 띠, 종료 안내를 확인합니다.
- [ ] README의 뷰어 절과 `CLAUDE.md`를 갱신하고, 설계와 이 계획의 상태를 갱신합니다.
