# 시스템 골라 띄우고 준비될 때까지 기다리기 구현 계획

> **에이전트 작업자에게:** 작업마다 superpowers:test-driven-development를 따릅니다. 단계는 체크박스(`- [ ]`)로 표시합니다.

- 날짜: 2026-11-18
- 상태: 사용자 승인(2026-11-18), 작업 1~8 수락, 통합 확인 끝(커밋 전, 실제 창 확인 전)
- 설계: [시스템 골라 띄우고 준비될 때까지 기다리기 설계](../specs/2026-11-18-system-launch-ready-design.md) (사용자 승인 2026-11-18). 값과 규칙은 설계가 기준이고, 이 계획은 파일, 이름, 시그니처, 테스트, 순서를 정합니다. 구현하는 사람은 둘 다 읽습니다.

**목표:** 설정 파일에 적은 시스템을 뷰어에서 골라 실행하면, 하니스가 그 exe를 띄우고 준비 신호(이름이 똑같은 요소)가 보이는 창을 찾을 때까지 기다린 뒤 모델의 첫 호출로 넘어갑니다.

**구조:** 설정에서 대상을 고르는 규칙은 새 모듈 `agent/systems.py`에, 띄우고 기다리는 일은 데스크톱 클라이언트만 받는 새 모듈 `agent/ready.py`에 둡니다. `Toolbox.launch_ready()`가 그 결과로 대상 프로세스를 옮기고 이벤트를 남기며, 루프는 준비 신호가 있는 작업에서 첫 턴 전에 그것을 부릅니다. 뷰어, `check.py`, 가상 MES는 같은 두 모듈을 씁니다.

**쓰는 것:** Python 표준 라이브러리와 지금의 의존성. 가상 MES는 지금처럼 .NET Framework `csc.exe`(C# 5). 더하는 의존성은 없습니다.

## 전체에 걸친 조건

- 준비 신호(`ready_text`)가 없는 실행의 동작, 도구 스키마, 기록 형식은 바뀌지 않습니다. 지금의 테스트는 고치지 않고 통과해야 합니다. 고쳐야 하면 오케스트레이터에게 먼저 묻습니다.
- 새 설정 키: `systems`(이름 → exe 절대 경로 묶음), `ready_text`(비어 있지 않은 글), `ready_timeout_s`(1 이상 정수, 기본 180).
- `ready_text`는 실행의 exe가 `systems`의 한 경로이거나 설정의 `exe`와 같을 때만 적용합니다(대소문자와 구분자를 맞춰 비교). 작업 파일은 `ready_text`, `ready_timeout_s`를 직접 적을 수 있고, 그 값이 설정보다 앞섭니다.
- exe를 고르는 순서: `--exe` > `--system`이 가리키는 `systems` 항목 > 작업 파일의 exe > 설정의 `system`이 가리키는 `systems` 항목 > 설정의 `exe`.
- 준비 판정: 후보 창의 트리에 `name`이 `ready_text`와 똑같은 요소가 있음(앞뒤 공백만 무시). 부분 문자열 아님.
- 실행 뒤 30초 동안 새 창이 하나도 없으면 멈춤. 다시 읽는 간격은 읽기가 끝난 뒤 1초.
- 고른 시스템이 이미 떠 있으면(`launch`가 돌려준 프로세스가 실행 전 창 목록에 있음) 건드리지 않고 멈춤. `launch --no-attach`는 쓰지 않음.
- `ready_text`와 시스템 경로는 코드에 넣지 않습니다(`tests\test_application_agnostic.py`). `ready_text`는 `run.json`, 요약, `check.py` 출력에 넣지 않고, 시스템 경로는 요약과 `check.py` 출력에 넣지 않습니다. `run.json`의 `exe`는 지금처럼 모든 실행에 남습니다(로컬 기록, 뷰어가 씀). 시스템 이름도 지금의 `system`처럼 남습니다. 이벤트의 글은 `events.jsonl`에만 둡니다.
- 작업자는 커밋하지 않고, 실제 창을 띄우는 실행과 실제 모델 호출을 하지 않습니다(`VIRTUAL_MES_LIVE=1` 테스트 포함). 데스크톱은 가짜로 시험합니다.
- 테스트는 `examples/llm-agent`에서 `/c/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -m pytest -q`(아래 `pytest`)로 돌립니다. 제출 전 전체 실패 0.

## 검토에서 특히 볼 것

1. **팝업 창의 제목이나 안내 글에 준비 신호 글자가 섞여 있을 때.** 팝업을 준비된 창으로 잡으면 곧 사라질 프로세스가 대상이 됩니다. 이름이 똑같은 요소만 인정해야 합니다. (작업 2)
2. **팝업 프로세스가 메인 화면을 띄우고 끝날 때.** `launch`가 돌려준 pid는 사라지고 메인 화면은 다른 pid입니다. 새 창으로 찾아 대상을 옮기고 `switched`가 참이어야 합니다. (작업 2, 작업 3)
3. **울산을 켜 둔 채 천안을 실행할 때.** 경로가 다르므로 막지 않아야 하고, 이미 떠 있는 울산 창이 후보가 되면 안 됩니다(실행 전 창은 `launch`가 그 프로세스를 돌려줄 때만 후보). (작업 2)
4. **준비 뒤 모델이 그래도 `launch_target`을 부를 때.** 실행기를 다시 띄우지 않고 지금 대상을 돌려줘야 합니다. 다시 띄우면 회사 MES는 새 창 없이 끝나지만 다른 앱은 하나 더 뜹니다. (작업 3)
5. **설정 파일의 `systems`에 경로를 큰따옴표로 감싸 역슬래시가 깨졌을 때.** 지금의 `exe`처럼 제어 문자를 잡아 몇 번째 항목인지와 따옴표 안내를 줘야 하고, 이름이나 경로는 오류에 나오면 안 됩니다. (작업 1)

## 배정과 순서

| 배정 | 작업자 | 작업 | 편집 경로 |
| --- | --- | --- | --- |
| `slr-w1-target` | w1 | 1, 4 | `agent/settings.py`, `agent/systems.py`(새), `agent/task.py`, `run.py`, `view.py`, `viewer/run_manager.py`, `settings.example.yaml`, 각 테스트 |
| `slr-w2-ready` | w2 | 2, 3, 5 | `agent/ready.py`(새), `agent/tools.py`, `agent/loop.py`, `agent/baseline.py`, `agent/recorder.py`, `agent/summary.py`, 각 테스트 |
| `slr-w3-surface` | w3 | 6, 7, 8 | `virtual-mes/*`(C#, `build.ps1`, `README.md`), `viewer/server.py`, `viewer/static/*`, `check.py`, `agent/checks.py`, 각 테스트 |

README의 llm-agent 본문과 `CLAUDE.md`는 오케스트레이터가 마지막에 고칩니다. 가상 MES의 `README.md`는 w3가 고칩니다.

의존:
- 바로 시작: 작업 1(w1), 작업 2(w2), 작업 6(w3).
- 작업 3은 작업 1(Task 필드)과 작업 2 수락 뒤. 작업 5는 작업 3 수락 뒤.
- 작업 4는 작업 1 수락 뒤.
- 작업 7은 작업 4 수락 뒤(`RunManager.systems`). 작업 8은 작업 1과 작업 2 수락 뒤.

## 계약

**C1. 설정 (작업 1):** `load_settings()`가 돌려주는 dict에 키가 있을 때만 `"systems": dict[str, str]`(파일의 순서 유지, 경로는 앞뒤 공백 제거), `"ready_text": str`, `"ready_timeout_s": int`가 들어갑니다.

**C2. `agent/systems.py` (작업 1)**

```python
def system_exe(settings: dict, name: str | None) -> str | None
    # settings["systems"][name], 이름이 없거나 None이면 None
def default_choice(settings: dict) -> str | None
    # 설정의 system이 systems에 있으면 그것, 아니면 systems의 첫 이름, systems가 없으면 None
def system_names(settings: dict) -> list[str]
def apply_ready(task: Task, settings: dict) -> Task
    # task.ready_text가 이미 있으면 그대로(작업 파일이 적음).
    # 아니면 settings에 ready_text가 있고 task.exe가 systems의 경로나 설정의 exe와 같을 때
    # replace(task, ready_text=..., ready_timeout_s=settings.get("ready_timeout_s", task.ready_timeout_s))
```

**C3. `Task` (작업 1):** `ready_text: str | None = None`, `ready_timeout_s: int = 180`. 둘 다 `OPTIONAL_KEYS`에 들어가고 `DEFAULT_KEYS`에는 넣지 않습니다(설정 값은 `apply_ready`로만 들어감).

**C4. `agent/ready.py` (작업 2)**

```python
NO_WINDOW_S = 30
READY_POLL_S = 1.0
RUNNING_HINT_MAX_WINDOWS = 30
READY_STATUSES = ("ready", "already_running", "no_new_window", "timeout", "launch_failed")

@dataclass(frozen=True)
class ReadyOutcome:
    status: str                 # READY_STATUSES 중 하나
    pid: int | None = None      # 준비된 창의 프로세스 (ready일 때)
    instance: str | None = None
    window: dict | None = None  # 준비된 창의 list-windows 행
    tree_envelope: dict | None = None  # 준비를 판정한 snapshot 봉투 (관찰 기록용)
    waited_ms: int = 0
    polls: int = 0
    switched: bool = False      # 준비된 창의 프로세스가 launch가 돌려준 프로세스와 다름
    running_hint: bool = False  # 실패할 때 실행 전 창 가운데 준비 신호가 있는 창이 있었음
    launch_error: dict | None = None  # launch 실패 봉투의 error

def has_named_element(tree: dict, text: str) -> bool
def wait_until_ready(desktop, exe: str, ready_text: str, timeout_s: int, clock, sleep,
                     no_window_s: int = NO_WINDOW_S) -> ReadyOutcome
```

**C5. `Toolbox.launch_ready()` (작업 3):** `dict`를 돌려줍니다. 성공은 `{"ok": True, "pid", "windows", "ready": {"waited_ms", "switched"}}`, 실패는 `{"ok": False, "code": "READY_" + status.upper(), "message": <한국어 이유>}`. `ready` 이벤트 두 개를 보냅니다: `{"type": "ready", "phase": "start"}`, `{"type": "ready", "phase": "end", "status", "waited_ms", "polls", "switched", "running_hint"}`. 준비된 창은 `source="ready"`인 관찰 하나로 기록합니다.

**C6. `run.json`과 요약 (작업 5):** `ready` 끝 이벤트가 있으면 `run.json`에 `"ready": {"status": <READY_STATUSES>, "waited_s": int, "switched": bool}`. 요약에 `  준비: <status> · <n>초 · 대상 바뀜|대상 그대로` 한 줄.

**C7. `RunManager` (작업 4):** 속성 `systems -> list[str]`, `default_choice -> str | None`. `start(..., system=이름)`에서 `exe`가 없고 이름이 `systems`에 있으면 그 exe를 씁니다.

**C8. 실패 이유 (작업 3, 루프와 `check.py`가 같은 글을 씀):** `agent/ready.py`의 `READY_MESSAGES: dict[str, str]`

| status | 글 |
| --- | --- |
| `already_running` | 같은 시스템이 이미 떠 있습니다. 닫고 다시 실행해 주세요 |
| `no_new_window` (힌트 있음) | 같은 시스템이 이미 떠 있는 것 같습니다. 닫고 다시 실행해 주세요 |
| `no_new_window` (힌트 없음) | 실행했지만 창이 뜨지 않았습니다 |
| `timeout` | 준비 신호가 정한 시간 안에 보이지 않았습니다 |
| `launch_failed` | 대상 앱을 띄우지 못했습니다 |

키는 `already_running`, `no_new_window_hint`, `no_new_window`, `timeout`, `launch_failed`. `ready_message(outcome) -> str`가 고릅니다. `timeout`에 힌트가 있으면 `no_new_window_hint`의 글을 뒤에 붙입니다.

---

### 작업 1: 설정, 대상 고르기, Task 필드 (w1)

**파일:** `agent/settings.py`, `agent/systems.py`(새), `agent/task.py`, `settings.example.yaml`. 테스트: `tests/test_settings.py`, `tests/test_systems.py`(새), `tests/test_task.py`.

**인터페이스:** C1, C2, C3을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_settings.py
def test_systems_keep_their_order_and_paths(tmp_path): ...
    # systems: {울산: C:\A\MES.exe, 천안소형: C:\B\MES.exe} → list(s["systems"]) == ["울산", "천안소형"]
def test_ready_keys_are_read(tmp_path): ...            # ready_text: 생산운영, ready_timeout_s: 90
@pytest.mark.parametrize("body", ["systems: C:\\A.exe", "systems: {}", "systems: {울산: D\\rel.exe}",
                                  "systems: {'a b': C:\\A.exe}", "systems: {울산: 3}", 'systems: {울산: "C:\\new\\MES.exe"}'])
def test_bad_systems_are_refused_without_names_or_paths(tmp_path, body): ...
    # SettingsError, reason에 "울산", "MES.exe", "C:\\"가 없음, 두 번째 이후 항목이면 몇 번째인지 나옴
def test_ready_timeout_must_be_a_positive_whole_number(tmp_path): ...  # 0, "90" → SettingsError
# test_systems.py
def test_system_exe_and_names(): ...
def test_default_choice_prefers_the_settings_system_then_the_first(): ...
def test_apply_ready_only_for_a_configured_exe(): ...
    # exe가 systems 경로와 대소문자·구분자만 다름 → 적용; 다른 exe → ready_text None; 설정 exe와 같음 → 적용
def test_apply_ready_keeps_the_task_files_own_value(): ...
def test_apply_ready_without_ready_text_changes_nothing(): ...
# test_task.py
def test_task_file_may_set_ready_text_and_timeout(tmp_path): ...
def test_bad_ready_values_in_a_task_file_are_refused(tmp_path): ...  # ready_text: "", ready_timeout_s: 0
```

- [ ] **2단계:** `pytest tests/test_settings.py tests/test_systems.py tests/test_task.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.**
  - `settings.py`: `KEYS`에 세 키, `NUMBER_KEYS`에 `ready_timeout_s`. `systems`는 `_parse`에서 따로 검사합니다: 값이 비어 있지 않은 dict, 이름은 지금의 `system` 규칙(`system_name`), 경로는 지금의 `exe` 규칙(`_checked("exe", ...)`의 제어 문자·절대 경로 검사). 오류 이유는 "systems N번째 항목: …" 꼴로 이름과 경로를 넣지 않습니다.
  - `systems.py`: C2. 경로 비교는 `os.path.normcase(os.path.normpath(...))`.
  - `task.py`: C3. `_check_optional`에서 `ready_text`는 비어 있지 않은 글, `ready_timeout_s`는 1 이상 정수.
  - `settings.example.yaml`: 세 키를 지금 형식(주석 처리, 한국어 설명, 따옴표 안내)으로 더합니다. 예시 이름과 경로는 `예) 공장A: C:\Example\MES\MES.exe`처럼 실제 시스템을 드러내지 않습니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 2: 띄우고 준비 기다리기 (w2)

**파일:** `agent/ready.py`(새). 테스트: `tests/test_ready.py`(새).

**인터페이스:** C4, C8을 만듭니다. `desktop.run(*args)`만 씁니다(`launch <exe>`, `list-windows`, `snapshot --window-id <id> --compact --timeout-ms 8000`).

- [ ] **1단계: 실패하는 테스트를 씁니다.** 테스트 안에 `TimelineDesktop`을 둡니다: 가짜 시계의 시각에 따라 `list-windows` 행과 창별 snapshot 트리를 돌려주고, 받은 명령을 기록합니다. `sleep`은 시계를 앞으로 돌립니다.

```python
def test_has_named_element_needs_the_whole_name(): ...
    # 이름 "생산운영" 노드 → True, "생산운영시스템 시작 중" → False, " 생산운영 " → True, 자식 깊숙이 있어도 True
def test_ready_in_the_launched_process(): ...       # 같은 pid 창에 3초 뒤 신호 → status "ready", switched False
def test_ready_in_a_new_process_after_the_splash(): ...
    # launch pid 100의 팝업 창(신호 글자가 제목에 섞임) → 2초 뒤 사라짐 → pid 200 새 창에 신호
    # → status "ready", pid 200, switched True, 팝업은 준비로 잡히지 않음
def test_already_running_stops_without_waiting(): ...
    # launch가 돌려준 pid·instance가 실행 전 창 목록에 있음 → "already_running", polls 0, snapshot 호출 0
def test_another_system_left_open_is_not_a_candidate(): ...
    # 실행 전 창(pid 50)에 신호 있음, launch는 새 pid 100, 100의 창에 5초 뒤 신호 → pid 100으로 ready
def test_no_new_window_for_30_seconds(): ...
    # 실행 전 창(pid 50)에 신호 있음, 새 창 없음 → 30초쯤 "no_new_window", running_hint True, 180초 기다리지 않음
def test_no_new_window_without_hint(): ...           # 실행 전 창에 신호 없음 → running_hint False
def test_timeout_when_the_signal_never_appears(): ... # 새 창은 있으나 신호 없음, timeout_s=20 → "timeout"
def test_launch_failure_is_an_outcome(): ...         # launch ok False → "launch_failed", launch_error 그대로
def test_polls_wait_one_second_after_each_read(): ...
def test_ready_message_texts(): ...                  # C8 표의 글, timeout+힌트는 두 글을 이음
```

- [ ] **2단계:** `pytest tests/test_ready.py`. 기대: 모듈이 없어 실패.
- [ ] **3단계: `agent/ready.py`를 구현합니다.** 순서는 설계 3절입니다.
  - 창의 열쇠는 `(id, pid, process_instance)`입니다. 실행 전 열쇠 모음과 실행 전 `(pid, process_instance)` 모음을 둘 다 기억합니다.
  - 후보: launch가 돌려준 `(pid, process_instance)`의 창 + 실행 전 열쇠 모음에 없는 창. 최소화된 창도 넣습니다.
  - "새 창을 본 적 있음"은 실행 전 열쇠에 없는 창을 한 번이라도 봤는지입니다. 30초가 지나도록 없으면 `no_new_window`.
  - 힌트는 실패할 때만 실행 전 창을 목록 순서로 최대 `RUNNING_HINT_MAX_WINDOWS`개 읽어 처음 찾는 데서 멈춥니다.
  - snapshot이 실패한 창은 이번 회차에서 건너뜁니다.
- [ ] **4단계:** `pytest tests/test_ready.py tests/test_application_agnostic.py`. 기대: 통과.

### 작업 3: Toolbox와 루프에 잇기 (w2, 작업 1과 작업 2 수락 뒤)

**파일:** `agent/tools.py`, `agent/loop.py`, `agent/baseline.py`. 테스트: `tests/test_tools.py`, `tests/test_loop.py`(또는 새 `tests/test_loop_ready.py`).

**인터페이스:** C3, C4, C8을 쓰고 C5를 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.** 작업 2의 `TimelineDesktop`을 `tests/ready_support.py`로 옮겨 함께 씁니다.

```python
def test_launch_ready_moves_the_target_to_the_ready_window(): ...
    # 팝업 pid 100 → 메인 pid 200: 이후 _rows()는 pid 200의 창, 결과 ready.switched True
def test_launch_ready_records_events_and_one_ready_observation(): ...
    # observer.on_event가 ready start, ready end를 받고, on_observation이 source="ready"로 한 번
def test_launch_target_after_ready_does_not_launch_again(): ...  # launch 명령 1번뿐, 결과 ok와 같은 pid
def test_restart_target_waits_for_ready_again(): ...
def test_run_waits_before_the_first_model_call(): ...
    # 대본 모델: 첫 입력에 READY_BRIEFING이 들어 있고, 모델 호출 전에 launch가 이미 끝남
def test_run_stops_without_calling_the_model_when_not_ready(): ...
    # already_running → RunResult status "stopped", reason이 C8 글, 모델 호출 0
def test_run_without_ready_text_is_unchanged(): ...  # 지금처럼 모델이 launch_target을 부름
def test_baseline_waits_for_ready_when_the_task_has_a_signal(): ...
```

- [ ] **2단계:** 해당 테스트를 돌립니다. 기대: 실패.
- [ ] **3단계: 구현합니다.**
  - `tools.py`: `launch_ready()`(C5). 준비되면 `_pid`, `_instance`, `_current_window`를 옮기고, 판정에 쓴 `tree_envelope`로 `_observe`와 같은 기록을 `source="ready"`로 남깁니다(트리를 다시 읽지 않아도 됨). 이벤트는 `_emit`으로 보냅니다. `_launch()`는 준비가 끝난 뒤에는 다시 띄우지 않고 지금 대상의 `{"ok": True, "pid", "windows"}`를 돌려줍니다. `_tool_restart_target`은 닫은 뒤 `ready_text`가 있으면 `launch_ready()`를 부릅니다.
  - `loop.py`: `_Run.run`에서 `Transcript`를 만들기 전에, `task.ready_text`가 있으면 `launch_ready()`를 부릅니다. 실패하면 턴 없이 `self._result("stopped", message)`로 끝냅니다(이후 정리 경로는 지금 그대로). 성공하면 목표 글 뒤에 영어 `READY_BRIEFING`(대상 앱이 이미 떠서 준비됨, `launch_target`을 부르지 말 것, 창 목록 JSON)을 붙입니다. 모델 호출 수와 토큰은 늘지 않습니다. `launch_ready()`는 `_Approval`을 거치지 않으므로 승인 모드에서도 묻지 않습니다(테스트: 승인 게이트가 있는 실행에서 게이트의 `review`가 준비 기다리기 동안 불리지 않음).
  - `baseline.py`: `task.ready_text`가 있으면 `launch_target` 대신 `launch_ready()`를 씁니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 4: 실행 시작 경로에서 시스템 고르기 (w1, 작업 1 수락 뒤)

**파일:** `run.py`, `view.py`, `viewer/run_manager.py`. 테스트: `tests/test_run.py`, `tests/test_view.py`, `tests/test_run_manager.py`.

**인터페이스:** C2, C3을 쓰고 C7을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_run.py
def test_system_option_picks_its_exe(): ...                  # --system 울산 --goal ... → task.exe == 울산 경로, system "울산"
def test_exe_option_beats_the_system_entry(): ...
def test_system_option_beats_the_task_files_exe(tmp_path): ...
def test_settings_system_picks_its_entry_before_the_settings_exe(): ...
def test_unknown_system_name_without_exe_is_refused(): ...   # systems에 없는 --system, exe도 없음 → 실패, 이름 규칙 오류와 구분
def test_ready_text_reaches_the_task_for_a_configured_exe(): ...
def test_ready_text_does_not_reach_another_exe(): ...
# test_run_manager.py
def test_start_with_a_system_name_uses_its_exe(): ...
def test_systems_and_default_choice_come_from_the_settings(): ...
def test_viewer_run_gets_the_ready_signal_only_for_a_configured_exe(): ...
```

- [ ] **2단계:** 해당 테스트. 기대: 실패.
- [ ] **3단계: 구현합니다.**
  - `run.py` `_resolve_task`: 전체 조건의 순서. `--system`이 `systems`에 없는 이름이면 지금처럼 지식 이름으로만 쓰고 exe를 바꾸지 않습니다. 작업이 정해지면 `apply_ready(task, settings)`.
  - `viewer/run_manager.py`: `task_defaults`(설정)에서 C7. `start`가 `build_task` 전에 exe를 정하고 `build_task` 뒤에 `apply_ready`를 부릅니다.
  - `view.py`: 지금처럼 설정을 `task_defaults`로 넘기면 됩니다. 기본 exe가 없어도 `systems`가 있으면 실행할 수 있어야 합니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 5: 기록과 요약 (w2, 작업 3 수락 뒤)

**파일:** `agent/recorder.py`, `agent/summary.py`. 테스트: `tests/test_recorder.py`, `tests/test_summary.py`.

**인터페이스:** C5의 이벤트를 받아 C6을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_ready_events_are_kept_and_the_end_goes_to_run_json(tmp_path): ...
    # ready end(status "ready", waited_ms 12400, switched True) → run.json["ready"] == {"status": "ready", "waited_s": 12, "switched": True}
def test_unknown_ready_status_is_not_written(tmp_path): ...   # status "x" → run.json에 ready 없음
def test_summary_has_a_ready_line(): ...                      # "  준비: ready · 12초 · 대상 바뀜"
def test_summary_without_ready_has_no_line(): ...
def test_summary_never_shows_the_ready_text(): ...            # 이벤트에 신호 글자가 섞여 있어도 요약에 없음
```

- [ ] **2단계:** 해당 테스트. 기대: 실패.
- [ ] **3단계:** `recorder.py`의 `on_event`가 `ready`를 기록하고, 끝 이벤트의 `status`가 `READY_STATUSES`에 들 때 `run.json`의 `ready`를 씁니다. 실행 도중에도 써서 뷰어 목록이 볼 수 있게 합니다. `summary.py`는 `run.json`의 `ready`로 한 줄을 만듭니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 6: 가상 MES의 시작 과정 (w3)

**파일:** `virtual-mes/Options.cs`, `virtual-mes/Program.cs`, `virtual-mes/MainForm.cs`, 새 `virtual-mes/SplashForm.cs`, `virtual-mes/EventLog.cs`(이벤트 이름만), `virtual-mes/build.ps1`(새 파일을 컴파일 목록에 넣을 때), `virtual-mes/README.md`. 테스트: `tests/test_virtual_mes.py`(창 없이 `--parse-only`), `tests/test_virtual_mes_live.py`(실제 창, 돌리지는 않음).

**인터페이스:** 설계 6절의 옵션 `--splash-ms <N>`, `--launcher`, `--menu-delay-ms <N>`, `--single-instance`와, 실행기가 메인 화면을 띄울 때만 쓰는 숨은 옵션 `--main`.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_startup_options_parse(): ...
    # --splash-ms 1500 --launcher --single-instance --menu-delay-ms 2000 → parse-only JSON의
    # splash_ms 1500, launcher true, single_instance true, menu_delay_ms 2000; 없으면 null/false
@pytest.mark.parametrize("args", [["--launcher"], ["--single-instance"], ["--splash-ms", "-1"],
                                  ["--menu-delay-ms", "x"], ["--launcher", "--single-instance"]])
def test_bad_startup_options_exit_2(args): ...   # --launcher와 --single-instance는 --splash-ms가 있어야 함
def test_main_flag_ignores_the_args_file(tmp_path): ...
    # 옵션 파일에 --splash-ms 1000 --launcher, 인자 --main → splash_ms null, launcher false
# test_virtual_mes_live.py (VIRTUAL_MES_LIVE=1일 때만)
def test_launcher_hands_the_main_window_to_another_process(): ...
def test_single_instance_launcher_shows_no_window_when_main_is_open(): ...
```

- [ ] **2단계:** `pytest tests/test_virtual_mes.py`. 기대: 새 테스트 실패(빌드는 `virtual_mes_support.py`가 하는 방식 그대로).
- [ ] **3단계: 구현합니다(C# 5).**
  - 팝업(`SplashForm`): 작은 창, 제목과 글은 메뉴 이름과 겹치지 않게(예: 제목 "VirtualMes", 글 "불러오는 중"). `--splash-ms`가 지나면 닫힙니다.
  - `--launcher`: 팝업이 닫히면 같은 exe를 `--main`과 남은 인자(시작 옵션 넷을 뺀 것)로 띄우고 끝납니다. `--main`은 옵션 파일을 읽지 않고 시작 옵션을 무시합니다.
  - `--single-instance`: 메인 화면 프로세스가 이름 있는 Mutex를 잡고, 실행기는 그 Mutex가 있으면 팝업 없이 곧바로 끝납니다. Mutex 이름에 exe 경로의 해시를 넣어 다른 폴더의 복사본은 막지 않습니다.
  - `--launcher` 없이 `--splash-ms`만 있으면 같은 프로세스에서 팝업 뒤 메인 화면을 띄웁니다.
  - `--menu-delay-ms`: 메인 화면을 메뉴 없이 띄우고 그 시간이 지나 메뉴를 채웁니다.
  - 이벤트 기록: `splash_shown`, `splash_closed`, `main_started`(`by`: `launcher`|`direct`), `menu_ready`.
  - README의 옵션 표와 "실행" 절을 갱신합니다.
- [ ] **4단계:** `powershell -NoProfile -File virtual-mes\build.ps1` 성공, `pytest tests/test_virtual_mes.py`. 기대: 통과. 라이브 테스트는 돌리지 않고 보고에 "미실행"으로 적습니다.

### 작업 7: 뷰어의 시스템 목록과 준비 중 표시 (w3, 작업 4 수락 뒤)

**파일:** `viewer/server.py`, `viewer/static/index.html`, `viewer/static/app.js`, `viewer/static/style.css`. 테스트: `tests/test_server.py`.

**인터페이스:** C7을 쓰고, `ready` 이벤트(C5)를 읽습니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_status_lists_system_names_without_paths(): ...
    # /api/status에 "systems": ["울산", "천안소형"], "default_choice": "울산", 응답 어디에도 경로 없음
def test_status_without_a_run_manager_has_no_systems(): ...   # "systems": [], "default_choice": None
def test_start_with_only_a_system_name(): ...                 # {"goal", "system": "울산"} → manager.start(exe=None, system="울산")
```

- [ ] **2단계:** `pytest tests/test_server.py`. 기대: 실패.
- [ ] **3단계: 구현합니다.**
  - `server.py`: `_status`에 C7의 두 값. `getattr`로 읽어 옛 manager 대역도 동작하게 합니다.
  - `index.html`/`app.js`: `systems`가 있으면 설정 칸 첫 자리에 `<select id="system-choice">`(이름들 + "직접 입력")를 보여 주고 `default_choice`를 고릅니다. 이름을 고르면 실행 파일 칸과 시스템 칸을 숨기고 요청에 `system`만 보냅니다. "직접 입력"이면 지금 칸 그대로입니다. 설정 요약에는 고른 시스템 이름을 보여 줍니다.
  - 준비 중 표시: 진행 중인 실행의 이벤트에 `ready` 시작이 있고 끝이 없으면 캡처 자리 위에 "대상 앱 준비 중 · N초"를 1초마다 갱신합니다. 끝 이벤트가 실패면 그 상태 코드를 보여 줍니다. 글은 `textContent`로만 넣습니다.
- [ ] **4단계:** `pytest`. 오케스트레이터가 헤드리스 Chrome으로 1600px와 390px 폭에서 목록과 준비 중 표시를 확인합니다.

### 작업 8: `check.py`의 시스템 고르기와 준비 신호 점검 (w3, 작업 1과 작업 2 수락 뒤)

**파일:** `check.py`, `agent/checks.py`. 테스트: `tests/test_check.py`.

**인터페이스:** C2, C4, C8을 씁니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_settings_line_counts_systems_and_says_whether_a_signal_is_set(): ...
    # "시스템 3개 · 준비 신호 있음", 이름·경로·신호 글자는 출력에 없음
def test_system_option_picks_the_exe_to_check(): ...
def test_without_system_option_the_default_choice_is_checked(): ...  # 한 시스템만 띄움(launch 1번)
def test_ready_check_reports_seconds_and_switch(): ...    # 새 줄 "준비 신호": 통과 "찾음 · 12초 · 대상 바뀜"
@pytest.mark.parametrize("status", ["already_running", "no_new_window", "timeout"])
def test_ready_check_failure_codes(status): ...           # 실패 코드 READY_ALREADY_RUNNING 등
def test_no_ready_line_without_a_signal(): ...            # 지금의 앱 관찰 그대로
```

- [ ] **2단계:** `pytest tests/test_check.py`. 기대: 실패.
- [ ] **3단계:** `--system <이름>`을 더합니다. exe는 `--exe` > `--system` 항목 > `default_choice` 항목 > 설정 `exe`. 준비 신호가 적용되면 앱 관찰 전에 `wait_until_ready`로 띄우고 기다린 뒤, 준비된 창을 관찰합니다. 새 결과 이름 `READY = "준비 신호"`는 `APP` 앞에 둡니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 오케스트레이터: 통합과 문서

- [ ] 모든 배정이 수락되면 전체 `pytest`. 대본 모델과 `TimelineDesktop`으로 "팝업 → 다른 프로세스의 메인 화면 → 첫 단계"가 이어지는 실행 하나를 녹음기, 요약, 내보내기까지 맞물려 확인합니다.
- [ ] `README.md`에 세 설정 키, 시스템 고르기, 준비 기다리기, `check.py --system`, 가상 MES 시작 옵션을 적고, `CLAUDE.md`의 llm-agent 절에 한 줄을 더합니다. 설계와 이 계획의 상태를 갱신합니다.
- [ ] 사용자와 할 일(확인받고): 가상 MES `--splash-ms 2000 --launcher --menu-delay-ms 2000`으로 뷰어 실행 한 번, 이미 떠 있는 경우 한 번. 회사에서는 `check.py --system <이름>`으로 준비 신호 줄을 먼저 확인합니다.
