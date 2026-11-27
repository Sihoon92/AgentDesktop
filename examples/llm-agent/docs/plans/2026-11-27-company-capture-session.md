# 회사 시스템 캡처 세션과 회사 GLM 정리 절차 구현 계획

> **에이전트 작업자에게:** 필수 하위 스킬: superpowers:subagent-driven-development(추천) 또는 superpowers:executing-plans로 작업 단위로 구현합니다. 작업마다 superpowers:test-driven-development를 따릅니다. 단계는 체크박스(`- [ ]`)로 표시합니다.

- 날짜: 2026-11-27
- 상태: 작업 0–16과 실제 창 확인 끝(2026-11-27, 가상 연습 앱 사본). 남은 것: 커밋(사용자 요청 때), 회사 첫 세션
- 설계: [회사 시스템 캡처 세션과 회사 GLM 정리 절차](../specs/2026-11-27-company-capture-session-design.md) (사용자 승인 2026-11-27). 값과 규칙은 설계가 기준이고, 이 계획은 파일, 이름, 시그니처, 시험, 순서, 담당을 정합니다. 구현하는 사람은 둘 다 읽습니다.
- 대체: [2026-11-25 계획](2026-11-25-company-survey.md). 그 계획의 작업 1·2·3·6·10의 시험과 규칙은 이 계획이 가리키는 곳에서 그대로 다시 씁니다(값이 다른 곳은 이 계획이 우선).

**Goal:** 회사에서 `python survey.py --system <이름>`으로 캡처 세션을 열어 앱을 쓰는 동안 순간들을 모으고, 세션 판정과 전달 블록을 스크립트가 만들며, 회사 GLM은 `company/`에서 장면 코드만 골라 넣게 합니다.

**Architecture:** 공통 계약(코드표와 사실 자료형, 작업 0)을 먼저 고정합니다. W1은 사실을 **모으는** 쪽(Win32·UIA 읽기, 앱 프로세스 추적, 감시, 캡처, 단축키·버튼, 세션 실행)을 맡아 `captures/<세션>/`에 계약 형식의 파일을 씁니다. W2는 그 파일을 **읽는** 쪽(가설 판정, `labels.yaml` 검사, 전달 블록, 결과 문서, 진입점, 집 쪽 해석, `company/` 장치)을 맡습니다. 두 쪽은 계약 파일 형식으로만 만납니다.

**Tech Stack:** Python 표준 라이브러리(ctypes, tkinter, threading, subprocess), 지금의 의존성(PyYAML, Pillow `ImageGrab`), Windows PowerShell 5.1 `UIAutomationClient`. 의존성을 더하지 않습니다. `agent-desktop` CLI는 고치지 않습니다.

**Spec:** `examples/llm-agent/docs/specs/2026-11-27-company-capture-session-design.md`

## Global Constraints

- **공개 범위:** 콘솔, 전달 블록, `session.json`, `events.jsonl`, `capture-NN/facts.json`에는 화면 글자, 창 제목, 요소 이름, 경로, 원래의 창 클래스 이름, DLL 이름, 설정 값이 없습니다. 계열 이름, 수, 코드, ID 해시(`agent.survey.id_keys`의 16자)만. 원래 이름은 `--show-names`일 때만 `names.json`(회사 PC 전용)에 쓰고 `summary.md`의 `## 원래 이름 (옮겨 적기 전에 확인)` 절에만 나옵니다. Windows 폴더 아래 모듈은 어디에도 이름으로 적지 않습니다.
- **창 제목은 읽지 않습니다.** `GetWindowText`를 부르지 않고, `list-windows` 행의 `title`과 `app_name`은 어떤 파일에도 옮기지 않습니다.
- **앱에 쓰지 않음:** 세션이 CLI에 보내는 명령은 `session`, `list-windows`, `snapshot`, `version`뿐입니다(`check.run_checks`의 바이너리 점검 포함). 앱을 띄우거나 닫거나 포커스를 옮기지 않습니다. Win32는 읽기와 `RegisterHotKey`, `MessageBeep`, 세션 자신의 창 스타일 변경만.
- **키 입력·마우스 기록 없음.** 저수준 훅(`SetWindowsHookEx`)을 쓰지 않습니다.
- `agent/` 코드와 `survey.py`에 앱 이름이나 화면 글을 넣지 않습니다. 가설·코드의 글에 `MES` 낱말을 쓰지 않고 "연습 앱"이라고 씁니다(`tests\test_application_agnostic.py`의 `\bMES\b`). 툴킷·부품 이름(DevExpress, Nexacro 등)과 Windows 프로세스 계열은 써도 됩니다.
- **값(모두 `agent/survey_codes.py` 한 곳):** `SURVEY_VERSION = 2`, `SURVEY_POLL_S = 0.5`, `SURVEY_MAX_WINDOWS = 8`, `SURVEY_SNAPSHOT_MS = 5000`, `UIA_MAX_NODES = 3000`, `UIA_TIMEOUT_S = 30`, `SURVEY_QUEUE_MAX = 5`, `SURVEY_STARTUP_S = 60`, `SURVEY_FOREGROUND_S = 2`, `SURVEY_END_WAIT_S = 60`, `DEFAULT_CAPTURE_HOTKEY = "ctrl+alt+f9"`, `DEFAULT_END_HOTKEY = "ctrl+alt+f10"`, `BLOCK_MAX_LINES = 15`, `HYPOTHESES_PER_LINE = 4`, `SAME_SCREEN_SHARE = 0.2`, `CHECK_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"`.
- **판정 문턱값(2026-11-25 계획에서 이어받음):** H01 70%와 `Win32`는 다른 계열이 없을 때만 셈, H03 `reach/total` 0.6/0.2, H04 패턴 묶음(Invoke와 Value+Toggle+ExpandCollapse+Selection).
- 출력은 `sys.stdout.reconfigure(errors="replace")`로 cp949 콘솔에서도 죽지 않습니다. 파일은 UTF-8(BOM 없음).
- 400줄을 넘는 Python 파일을 만들지 않습니다(저장소 규칙의 정신; 넘으면 책임별로 나눔).
- **작업자는 커밋하지 않습니다**(사용자가 요청할 때만, 오케스트레이터가). 실제 단축키 등록, 실제 버튼 표시, 실제 화면 캡처, 실제 앱 창, 실제 모델 호출을 하지 않습니다. 자기 Python 프로세스나 콘솔 없이 새로 띄운 하위 프로세스(`python -c "import time; time.sleep(5)"`)를 Win32로 읽는 시험은 해도 됩니다.
- 시험은 `examples/llm-agent`에서 `/c/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -X utf8 -m pytest -q -p no:cacheprovider`(아래 `pytest`)로 돌립니다. 제출 전 전체 실패 0. 시험마다 구현을 되돌려 실패하는지 확인하고 보고서에 적습니다.

## Review Focus

1. **세션 중 앱이 응답 없음:** 느린 단계의 스냅샷이 창마다 5초씩 막혀도 단축키와 버튼은 계속 받고, 빠른 단계의 `screen.png`는 매번 저장되며, 밀린 캡처는 `QUEUE_FULL`로 남습니다. → 작업 9의 `test_hung_app_keeps_fast_captures_coming`.
2. **세션 중 앱을 껐다 켬:** 새 pid는 새 `P` 번호이고 `APP`으로 다시 잡히며, H05가 두 프로세스의 같은 화면을 비교합니다. → 작업 4의 `test_restarted_app_is_a_new_app_process`, 작업 11의 `test_h05_compares_two_processes_of_one_session`.
3. **단축키를 다른 프로그램이 이미 씀 / tkinter가 없음:** 세션은 멈추지 않고 콘솔에 `HOTKEY_TAKEN`을 찍고 버튼으로 계속합니다. 버튼을 만들 수 없으면 단축키만으로 계속합니다. 둘 다 안 되면 Ctrl+C로만 끝낼 수 있다고 알립니다. → 작업 9의 `test_hotkey_taken_continues_with_button`, `test_no_button_continues_with_hotkey`.
4. **GLM이 `labels.yaml`을 한글 문장, BOM, CRLF로 씀:** BOM과 CRLF는 받아들이고, 한글 문장 값은 `LABEL_INVALID <줄>행 SCENE_CODE`로 거부하며 그 문장을 찍지 않습니다. → 작업 12의 `test_bom_and_crlf_are_accepted`, `test_sentence_value_is_rejected_without_echo`.
5. **캡처 없이 Ctrl+C로 끝냄:** `session.json`, `summary.md`, `캡처0`인 블록이 그래도 나옵니다. → 작업 9의 `test_interrupt_before_any_capture_still_finishes`.

---

## 담당과 순서

| 작업 | 담당 | 먼저 끝나야 하는 것 |
| --- | --- | --- |
| 0 계약 | 오케스트레이터 | 없음 |
| 1 계열 판별 · 2 Win32 · 3 UIA · 4 앱 프로세스 · 5 감시 · 6 오버레이 · 7 캡처 · 8 단축키·버튼 · 9 세션 | W1 | 0. W1 안에서는 번호 순 |
| 10 git 상태 · 11 가설 · 12 labels · 13 전달 블록 · 14 결과 문서·진입점 · 15 `company/` | W2 | 0. W2 안에서는 번호 순 |
| 16 통합 | 오케스트레이터 | 1–15 전부 |

W1과 W2는 서로의 파일을 고치지 않습니다. W2는 판정과 블록을 계약 형식의 가짜 세션 폴더로 시험하고, W1은 세션 끝의 `finish`를 가짜로 바꿔 시험합니다.

## 파일 구조 (`examples/llm-agent/` 기준)

| 파일 | 작업 | 하는 일 |
| --- | --- | --- |
| `agent/survey_codes.py` | 0 | 코드표, 코드의 뜻, 값 |
| `agent/survey_facts.py` | 0 | 사실 자료형, 세션 폴더 형식, 읽고 쓰기, 캡처 줄의 구조 코드 |
| `.gitignore` | 0 | `/captures/` |
| `agent/toolkits.py` | 1 | 창 클래스·모듈 이름 → 계열 |
| `agent/win32_facts.py` | 2 | ctypes 읽기: 프로세스, 최상위 창, 자식 클래스, 모듈, 응답 없음, 전경, 모니터, DPI |
| `agent/uia_count.ps1`, `agent/uia_facts.py` | 3 | UIA FrameworkId·패턴 개수 |
| `agent/app_processes.py` | 4 | 앱 프로세스 알아보기와 `rel` |
| `agent/watch.py` | 5 | 감시 한 걸음(순수)과 감시 기록 |
| `agent/overlay.py`, `agent/survey.py`(고침) | 6 | 오버레이 PNG, 종류 판별 공개 |
| `agent/survey_capture.py` | 7 | 대상 창 고르기, 빠른·느린 단계 |
| `agent/hotkey.py`, `agent/float_button.py` | 8 | 전역 단축키, 포커스를 가져가지 않는 버튼 |
| `agent/survey_session.py`, `agent/settings.py`(고침), `check.py`(고침) | 9 | 세션 실행, 설정 키, `with_shortcut` 공개 |
| `agent/git_state.py` | 10 | 커밋과 코드 변경 |
| `agent/hypotheses.py` | 11 | 가설 H01–H18과 판정 |
| `agent/labels.py` | 12 | `labels.yaml` 검사 |
| `agent/survey_block.py` | 13 | 전달 블록, 확인 문자, 블록 읽기와 풀이 |
| `agent/survey_report.py`, `survey.py`, `docs/company-survey/{hypotheses,codes}.md`, `docs/company-survey/feedback/README.md` | 14 | `summary.md`, 세션 마무리, 생성 문서, 진입점 |
| `company/CLAUDE.md`, `company/.claude/settings.json`, `company/hooks/guard.py`, 저장소 루트 `.gitignore` | 15 | 회사 Claude Code 실행·정리 전용 |
| `README.md`, 저장소 `CLAUDE.md`, `docs/cua-background.md`, `tests/test_application_agnostic.py` | 16 | 안내와 통합 |

시험 파일은 `tests/test_<모듈>.py`(예: `tests/test_watch.py`). 기존 `tests/test_survey.py`는 `agent/survey.py`의 시험이라 이름을 바꾸지 않습니다. 진입점 시험은 `tests/test_survey_cli.py`, 세션 시험은 `tests/test_survey_session.py`.

---

### 작업 0: 계약 (오케스트레이터)

**Files:** Create `agent/survey_codes.py`, `agent/survey_facts.py`, `tests/test_survey_codes.py`, `tests/test_survey_facts.py`; Modify `.gitignore`(`/captures/` 한 줄)

**Interfaces — Produces:**

`agent/survey_codes.py`:
- Global Constraints의 값 전부.
- `SCENES = ("STARTUP_POPUP", "LOGIN", "MAIN", "GRID", "FORM", "ERROR_DIALOG", "WARNING_DIALOG", "CONFIRM_DIALOG", "MESSAGE", "LOADING", "OTHER", "?")`
- `KINDS = ("MAIN", "DIALOG", "OWNED", "TOP")`, `RELS = ("APP", "CHILD_OTHER", "SYSTEM", "OTHER", "UNKNOWN")`, `STATES = ("OK", "HUNG", "TIMEOUT", "GONE", "BLANK")`, `READS = ("OK", "TRUNCATED", "TIMEOUT", "UNRESPONSIVE", "GONE", "ERROR")`, `SIZES = ("S", "M", "L")`, `SHOTS = ("OK", "SHOT_BLANK", "SHOT_FAILED")`, `SLOWS = ("DONE", "QUEUE_FULL", "QUEUE_ABANDONED")`, `APP_SEEN = ("ALREADY_RUNNING", "STARTED", "PICKED_BY_CAPTURE", "NOT_SEEN")`, `EVENTS = ("window_shown", "window_hidden", "process_started", "process_exited", "hung_start", "hung_end", "foreground")`, `FAILURES = ("HOTKEY_TAKEN", "UIA_SPAWN", "UIA_TIMEOUT", "UIA_EXIT", "UIA_OUTPUT", "LABEL_INVALID", "NO_APP", "NO_READ", "MODULES_UNREAD")`
- `MEANINGS: dict[str, str]`: 위 모든 튜플(`EVENTS` 제외)의 코드마다 한국어 한 줄 뜻(장면은 설계 5장의 표, 나머지는 설계 2·3장). 같은 코드가 둘 이상의 튜플에 있으면(예: `MAIN`, `OK`, `GONE`) 뜻은 `"<튜플 이름>:<코드>"` 키로 나눔: `MEANINGS["scene:MAIN"]`, `MEANINGS["kind:MAIN"]`. 키 형식은 `f"{group}:{code}"`, group은 `scene`, `kind`, `rel`, `state`, `read`, `size`, `shot`, `slow`, `app_seen`, `failure`.

`agent/survey_facts.py` (모두 frozen dataclass, `to_dict(self) -> dict`와 `from_dict(cls, d) -> Self`; `from_dict`는 모르는 키를 무시하고 빠진 키는 기본값):
- `FamilyCount(families: dict[str, int], unknown: int)`
- `UiaCounts(nodes: int, truncated: bool, frameworks: dict[str, int], patterns: dict[str, int])`
- `WindowFact(win: str, proc: str | None, instance: str | None, rel: str, family: str, kind: str, owned: str | None, modal: bool, hung: bool, size: str, read: str, read_s: float | None, kinds: dict[str, int] | None, id_keys: tuple[str, ...])` — `family`는 계열 이름 또는 `"?"`, `instance`는 CLI의 `process_instance`.
- `CaptureFacts(id: str, t: float, shot: str, main: str | None, windows: tuple[WindowFact, ...], new_since_last: int, uia: UiaCounts | None, uia_code: str | None, monitor: str | None, monitor_count: int | None, slow: str)` — `monitor`는 `f"{w}x{h}@{scale:g}"`.
- `ProcessFact(proc: str, rel: str, target_exe: bool, started_in_session: bool, bits: int | None, modules: FamilyCount | None, classes: FamilyCount, mdi: bool, max_visible_top: int, first_window_t: float | None)`
- `CheckFact(name: str, status: str, code: str)` — `checks.Result`의 이름·상태·코드.
- `SessionFacts(version: int, system: str, started: str, duration_s: float, head: str | None, changed: int, hotkey: str, app_seen: str, first_app_window_t: float | None, unknown_paths: int, checks: tuple[CheckFact, ...], processes: tuple[ProcessFact, ...], captures: tuple[str, ...], saved_ids: tuple[str, ...] | None, saved_ids_process: str | None)` — `hotkey`는 `"OK"` 또는 `"HOTKEY_TAKEN"`, `saved_ids`는 시작 때 읽은 `.check/<시스템>.json`의 키, `saved_ids_process`는 그 기록의 `process` 표시.
- `Event(t: float, event: str, win: str | None = None, proc: str | None = None, rel: str | None = None, family: str | None = None, kind: str | None = None, owned: str | None = None, modal: bool | None = None, size: str | None = None, fg_within: str | None = None)` — `to_dict`는 None 칸을 뺌.
- `Labels(captures: dict[str, str], manual: dict[str, str])`
- `LoadedSession(folder: Path, session: SessionFacts, captures: tuple[CaptureFacts, ...], events: tuple[Event, ...])`
- 파일 이름: `SESSION_JSON = "session.json"`, `EVENTS_JSONL = "events.jsonl"`, `LABELS_YAML = "labels.yaml"`, `NAMES_JSON = "names.json"`, `SUMMARY_MD = "summary.md"`, `NOTES_MD = "notes.md"`, `BLOCK_TXT = "block.txt"`, `FACTS_JSON = "facts.json"`.
- `capture_id(n: int) -> str` (`"C01"`), `capture_dir(folder: Path, cid: str) -> Path` (`folder / "capture-01"`).
- `write_json(path: Path, value: dict) -> None` (임시 파일에 쓰고 `os.replace`), `append_event(path: Path, event: Event) -> None`(한 줄 JSON).
- `load_session(folder: Path) -> LoadedSession` — `session.json`이 없으면 `FileNotFoundError`. 캡처는 `session.captures` 순서, 그 폴더에 `facts.json`이 없으면 빠른 단계만 있는 캡처로 `CaptureFacts(id, t=0.0, shot="SHOT_FAILED", main=None, windows=(), new_since_last=0, uia=None, uia_code=None, monitor=None, monitor_count=None, slow="QUEUE_ABANDONED")`.
- `capture_codes(capture: CaptureFacts) -> tuple[str, str, str]`: 주 창의 `(kind, rel, state)`. 주 창이 없으면 `("TOP", "UNKNOWN", "GONE")`. state: shot이 `SHOT_BLANK`면 `BLANK`, read가 `GONE`이면 `GONE`, `hung`이거나 read가 `UNRESPONSIVE`면 `HUNG`, read가 `TIMEOUT`이면 `TIMEOUT`, 아니면 `OK`(이 순서).

- [ ] **Step 1: 실패하는 시험 작성**

```python
def test_every_code_has_a_meaning():
    groups = {"scene": SCENES, "kind": KINDS, "rel": RELS, "state": STATES, "read": READS, "size": SIZES,
              "shot": SHOTS, "slow": SLOWS, "app_seen": APP_SEEN, "failure": FAILURES}
    for group, codes in groups.items():
        for code in codes: assert MEANINGS[f"{group}:{code}"]

def test_meanings_hold_no_app_word():
    assert not any(re.search(r"\bMES\b", text) for text in MEANINGS.values())

def test_session_round_trip(tmp_path):  # SessionFacts·CaptureFacts·Event를 써서 load_session으로 다시 읽으면 같음
    ...
def test_missing_capture_facts_reads_as_abandoned(tmp_path): ...
def test_event_dict_drops_none(): assert Event(1.0, "foreground", win="W1").to_dict() == {"t": 1.0, "event": "foreground", "win": "W1"}

@pytest.mark.parametrize("shot, read, hung, state", [
    ("SHOT_BLANK", "OK", False, "BLANK"), ("OK", "GONE", True, "GONE"), ("OK", "OK", True, "HUNG"),
    ("OK", "UNRESPONSIVE", False, "HUNG"), ("OK", "TIMEOUT", False, "TIMEOUT"), ("OK", "TRUNCATED", False, "OK")])
def test_capture_codes_state_order(shot, read, hung, state): ...
def test_capture_without_main_window(): assert capture_codes(empty_capture) == ("TOP", "UNKNOWN", "GONE")
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_survey_codes.py tests/test_survey_facts.py` → ImportError
- [ ] **Step 3: 구현.** 자료형의 튜플 칸은 JSON 목록으로 쓰고 읽을 때 튜플로. 중첩 자료형(`FamilyCount`, `UiaCounts`, `WindowFact`…)은 각자의 `from_dict`로.
- [ ] **Step 4: 통과 확인**, `.gitignore`에 `/captures/` 추가 후 `git check-ignore -q captures/x`가 0.

---

## W1

### 작업 1: 계열 판별 (`agent/toolkits.py`)

2026-11-25 계획 작업 1을 그대로 하고(시그니처, 표, 시험 전부), 하나를 더합니다.

**Interfaces — Produces:** 2026-11-25 계획 작업 1의 `UI_FAMILIES`, `GRID_FAMILIES`, `class_family`, `module_family`, `is_system_module`, `count_families`(반환 `FamilyCount`는 그 계획의 NamedTuple 대신 **작업 0의 `survey_facts.FamilyCount`**와, 이름 목록 `unknown_names: tuple[str, ...]`를 따로 돌려주는 `count_families(names, classify) -> tuple[FamilyCount, tuple[str, ...]]`), 그리고 `is_dialog_class(name: str) -> bool`(`#32770`일 때만 참).

- [ ] **Step 1:** 2026-11-25 계획 작업 1의 시험을 반환 형태에 맞춰 옮기고 `test_dialog_class_is_only_32770`을 더함(`#32770` 참, `TfrmDialog`·`Button` 거짓).
- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 2: Win32 읽기 (`agent/win32_facts.py`)

**Interfaces — Produces** (Windows 밖에서는 빈 값이나 None; 모든 함수는 예외를 내지 않음):
- `ProcessRow = NamedTuple(pid: int, parent: int, image: str | None, started: str | None)` — `started`는 `process_identity.process_started_at`.
- `WindowRow = NamedTuple(hwnd: int, pid: int, class_name: str, visible: bool, minimized: bool, owner: int, owner_enabled: bool, rect: tuple[int, int, int, int])` — rect는 (left, top, right, bottom) 물리 픽셀. `owner`는 `GetWindow(GW_OWNER)`(없으면 0), `owner_enabled`는 소유 창의 `IsWindowEnabled`(소유 창이 없으면 참).
- `MonitorInfo = NamedTuple(width: int, height: int, scale: float, count: int)`
- `process_rows() -> tuple[ProcessRow, ...]` (Toolhelp 스냅샷; image·started는 열 수 있는 것만)
- `process_image(pid) -> str | None`, `process_bits(pid) -> int | None`, `modules_of(pid) -> tuple[str, ...] | None`(`EnumProcessModulesEx(..., LIST_MODULES_ALL)`, 전체 경로)
- `top_windows() -> tuple[WindowRow, ...]` (`EnumWindows` 순서 = 앞에서 뒤)
- `child_classes(hwnd) -> tuple[str, ...]` (`EnumChildWindows`)
- `is_hung(hwnd) -> bool`, `foreground() -> int | None`, `monitor_of(hwnd) -> MonitorInfo | None`, `windows_dir() -> str`
- `set_dpi_awareness() -> str`: `"PER_MONITOR_V2"`, `"PER_MONITOR"`, `"SYSTEM"`, `"NONE"` 중 성공한 것(`SetProcessDpiAwarenessContext` → `SetProcessDpiAwareness` → `SetProcessDPIAware`)
- `beep() -> None` (`MessageBeep(MB_OK)`)

`GetWindowText`·`GetWindowTextLength`는 이 모듈에 없어야 합니다. 프로세스 열기는 `_open_process(pid) -> handle | None` 하나로 모으고 핸들은 항상 닫습니다. argtypes·restype은 `agent/handles.py`처럼 명시.

- [ ] **Step 1: 실패하는 시험 작성** (Windows 아니면 skip)

```python
def test_process_rows_include_a_child_with_its_parent():  # CREATE_NO_WINDOW 하위 프로세스, parent == os.getpid()
def test_own_image_bits_and_modules(): ...                  # 2026-11-25 계획 작업 2와 같음
def test_denied_process_reads_as_none(monkeypatch): ...     # _open_process → None
def test_dead_handles_read_as_empty(): assert child_classes(0) == () and monitor_of(0) is None and is_hung(0) is False
def test_top_windows_rows_are_well_formed(): # 모든 행의 rect가 4개 정수, class_name은 str
def test_module_never_reads_window_text():
    assert "GetWindowText" not in (AGENT / "win32_facts.py").read_text(encoding="utf-8")
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 3: UIA 개수 (`agent/uia_count.ps1`, `agent/uia_facts.py`)

2026-11-25 계획 작업 3을 그대로 하되 값은 `survey_codes.UIA_MAX_NODES`(3000), `UIA_TIMEOUT_S`(30)를 쓰고, 반환은 작업 0의 `UiaCounts`입니다.

**Interfaces — Produces:** `PATTERNS`, `count_uia(hwnd: int, runner=subprocess.run) -> tuple[UiaCounts | None, str | None]`(코드 `UIA_SPAWN`·`UIA_TIMEOUT`·`UIA_EXIT`·`UIA_OUTPUT`), `parse_counts(text) -> UiaCounts | None`.

- [ ] **Step 1–4:** 그 계획의 시험(값만 바꿔서) → 실패 확인 → 구현 → 통과 확인

### 작업 4: 앱 프로세스 (`agent/app_processes.py`)

**Interfaces:**
- Consumes: `win32_facts.ProcessRow`, `survey_facts.Event`
- Produces:
  - `classify(image: str | None, parent_is_app: bool, app_dirs: tuple[str, ...], windows_dir: str) -> str`: 설계 2.2의 순서. image None → `UNKNOWN`; Windows 폴더 아래 → `SYSTEM`; `app_dirs` 중 하나의 아래 → `APP`; `parent_is_app` → `CHILD_OTHER`; 아니면 `OTHER`. 경로 비교는 `os.path.normcase(os.path.abspath(...))`와 경계(`dir + os.sep`).
  - `class AppTracker(target_exe: str, extra_exes: tuple[str, ...], windows_dir: str)`:
    - `update(rows: Iterable[ProcessRow], t: float) -> list[Event]`: (pid, started) 쌍으로 프로세스를 구별. `APP`이거나 `CHILD_OTHER`인 새 프로세스마다 번호(`P1`…)를 주고 `process_started`, 사라지면 `process_exited`(그 번호가 있는 것만). 첫 `update` 때 이미 있던 `APP`은 이벤트 없이 번호만 주고 `already_running`을 참으로.
    - `rel(pid: int) -> str` (모르는 pid는 `OTHER`), `proc(pid: int) -> str | None`, `is_app(pid: int) -> bool`, `is_target(pid: int) -> bool`(실행 파일이 `target_exe` 자신), `started_in_session(pid: int) -> bool`
    - `already_running: bool`, `unknown_paths: int`(지금까지 image를 못 읽은 서로 다른 프로세스 수)
    - `pick(pid: int) -> None`: 그 프로세스의 실행 파일 폴더를 `app_dirs`에 더함(설계 2.2의 4).
    - `app_dirs: tuple[str, ...]` (target_exe와 extra_exes의 폴더, `pick`으로 더해진 것)

- [ ] **Step 1: 실패하는 시험 작성** (가짜 `ProcessRow`만)

```python
@pytest.mark.parametrize("image, parent_app, rel", [
    (r"C:\Windows\System32\WerFault.exe", True, "SYSTEM"), (r"C:\Apps\Mes\Real.exe", False, "APP"),
    (r"C:\Apps\Mes\sub\Helper.exe", True, "APP"), (r"C:\Program Files\Office\EXCEL.EXE", True, "CHILD_OTHER"),
    (r"C:\Other\x.exe", False, "OTHER"), (None, True, "UNKNOWN"), (r"C:\Apps\Mesh\x.exe", False, "OTHER")])
def test_classify(image, parent_app, rel): assert classify(image, parent_app, (r"C:\Apps\Mes",), r"C:\Windows") == rel

def test_launcher_child_in_the_same_folder_is_app_after_the_launcher_exits(): ...
def test_child_of_app_outside_the_folder_is_child_other_and_not_app(): ...
def test_extra_exe_folder_counts_as_app(): ...
def test_running_at_first_update_is_already_running_without_event(): ...
def test_restarted_app_is_a_new_app_process():  # 같은 pid라도 started가 다르면 P2와 process_started
def test_pick_makes_that_folder_app(): ...
def test_unknown_paths_are_counted_once_per_process(): ...
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 5: 감시 (`agent/watch.py`)

**Interfaces:**
- Consumes: `WindowRow`, `AppTracker`, `toolkits.class_family`, `toolkits.is_dialog_class`, `survey_facts.Event`
- Produces:
  - `Observation = NamedTuple(t: float, windows: tuple[WindowRow, ...], foreground: int | None, hung: frozenset[int])`
  - `size_class(rect, screen: tuple[int, int]) -> str` (`S`/`M`/`L`; 경계는 구현자, 기본 제안: 면적이 화면의 10% 미만 S, 60% 이상 L)
  - `class Watcher(tracker: AppTracker, own_pids: frozenset[int], screen: tuple[int, int])`:
    - `step(obs: Observation) -> list[Event]`: 보이는(`visible and not minimized`) 최상위 창의 생김(`window_shown`: win, proc, rel, family, kind(`DIALOG`/`OWNED`/`TOP`), owned(소유 창의 win), modal(`not owner_enabled`), size)·사라짐(`window_hidden`: win), 앱 창(`tracker.is_app`)의 `hung`이 바뀔 때 `hung_start`/`hung_end`(win), 전경이 바뀔 때 `foreground`(win, rel, 그 창이 뜬 지 `SURVEY_FOREGROUND_S` 안이면 `fg_within` = 직전 전경 창의 rel). `own_pids`의 창은 어떤 이벤트도 만들지 않고 전경으로도 기억하지 않음.
    - `number(hwnd: int) -> str`: 창 번호(`W1`…), 처음 보면 새로 줌.
    - `hwnd_of(win: str) -> int | None`
    - `last_foreground: int | None` (세션 자신의 창이 아닌 마지막 전경)
    - `mark() -> int` / `shown_since(mark: int) -> list[int]`: 그 표시 이후 생겨 아직 보이는 창(hwnd), 생긴 순서.
    - `tracker: AppTracker`(받은 것), `first_app_window_t: float | None`, `first_window_t: dict[str, float]`(프로세스 번호 → 그 프로세스의 창이 처음 보인 시각), `max_visible_top: dict[str, int]`(앱 프로세스 번호 → 동시에 보인 소유 없는 최상위 창의 최대 수)
  - `class WatchThread(watcher, observe: Callable[[], Observation], on_events: Callable[[list[Event]], None], poll_s=SURVEY_POLL_S)`: `start()`, `stop()`; 관찰 중 예외는 삼키고 다음 주기에 다시.

- [ ] **Step 1: 실패하는 시험 작성** (가짜 `Observation` 연속)

```python
def test_shown_and_hidden_events_carry_codes_not_names(): ...
def test_own_windows_never_make_events_or_foreground(): ...
def test_foreground_within_two_seconds_records_previous_rel():
    # t=10 앱 창 전경 → t=11 다른 프로세스 대화상자 생김 → t=12 그 창 전경 → fg_within == "APP"
def test_alt_tab_to_an_old_window_has_no_fg_within(): ...
def test_hung_start_and_end_only_for_app_windows(): ...
def test_shown_since_mark_lists_only_still_visible_new_windows(): ...
def test_modal_and_owned_dialog(): ...  # #32770, owner=앱 창, owner_enabled=False → kind DIALOG, modal True, owned "W1"
def test_max_visible_top_counts_unowned_app_windows(): ...
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 6: 오버레이 (`agent/overlay.py`, `agent/survey.py` 고침)

**Interfaces:**
- Produces:
  - `agent/survey.py`: `node_kind(node) -> str | None`와 `is_id_only(node) -> bool` 공개(지금의 `_kind`, `_id_only`를 이름만 바꿔 내보내고 `kinds()`가 그것을 씀).
  - `agent/overlay.py`: `LEGEND = (("named_ref", "파랑", (0, 120, 255)), ("unnamed_ref", "보라", (160, 0, 200)), ("text", "초록", (0, 170, 0)), ("blank", "빨강", (230, 0, 0)), ("id_only", "주황", (255, 140, 0)))`; `draw_overlay(image: PIL.Image.Image, origin: tuple[int, int], tree: dict, out: Path) -> int`(그린 박스 수). 노드 bounds(화면 좌표)에서 origin을 빼서 그림. 그림 밖 노드는 건너뜀. 박스에 글자 없음. 오른쪽 아래 범례(색 사각형과 색 이름·종류 설명).

- [ ] **Step 1:** 2026-11-25 계획 작업 8의 세 시험을 이 시그니처로(흰 이미지, origin (100, 50)). `tests/test_survey.py`는 그대로 통과해야 함.
- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 7: 캡처 (`agent/survey_capture.py`)

**Interfaces:**
- Consumes: 작업 0–6 전부, `agent.survey.kinds`/`id_keys`, `agent.handles.window_number`
- Produces:
  - `pick_targets(foreground: int | None, new_windows: list[int], app_windows: list[int], limit: int = SURVEY_MAX_WINDOWS) -> list[int]`: 순서대로 중복 없이.
  - `is_blank(image) -> bool` (기준은 구현자; 제안: RGB 각 채널의 최대−최소가 8 이하)
  - `read_code(envelope: dict) -> str`: ok이고 `data.complete`가 거짓이 아니면 `OK`, ok이고 거짓이면 `TRUNCATED`; 오류 코드 `TIMEOUT`·`HARNESS_TIMEOUT` → `TIMEOUT`, `APP_UNRESPONSIVE` → `UNRESPONSIVE`, `WINDOW_NOT_FOUND`·`STALE_REF`·`ELEMENT_NOT_FOUND` → `GONE`, 그 밖 → `ERROR`.
  - `window_kind(row: WindowRow, app_rows: list[WindowRow], is_app: bool) -> str`: 앱 창이고 소유 없고 보이는 앱 창 중 면적이 가장 크면 `MAIN`; `is_dialog_class` → `DIALOG`; owner가 있으면 `OWNED`; 아니면 `TOP`.
  - `FastCapture = NamedTuple(id: str, t: float, shot: str, foreground: int | None, targets: tuple[int, ...], new_since_last: int, hung: frozenset[int], rows: tuple[WindowRow, ...])`
  - `fast_capture(cid: str, t: float, folder: Path, grab: Callable, watcher: Watcher, observe: Callable[[], Observation], own_pids: frozenset[int]) -> FastCapture`: `grab(all_screens=True)`을 `capture-NN/screen.png`로(실패면 `SHOT_FAILED`), 관찰 한 번, 전경이 세션 창이면 `watcher.last_foreground`, `pick_targets`(새 창은 `watcher.shown_since(직전 표시)`, 그리고 새 표시), 앱 창은 `watcher.tracker.is_app`인 보이는 최상위 창.
  - `SlowContext`(dataclass): `desktop`, `win32`(작업 2 모듈 같은 객체), `tracker`, `watcher`, `grab`, `uia_runner`, `uia_done: set[str]`(UIA를 센 앱 프로세스 번호), `module_cache: dict[str, tuple[FamilyCount | None, int | None]]`, `class_tally: dict[str, list[str]]`(프로세스 번호 → 이 세션에 본 원래 클래스 이름, 회사 PC 전용 `names.json`과 `ProcessFact.classes`의 재료), `unknown_modules: set[str]`, `mdi: set[str]`(대상 창의 `child_classes`에 `MDIClient`가 있었던 앱 프로세스 번호), `windows_dir: str`.
  - `slow_capture(fast: FastCapture, folder: Path, ctx: SlowContext) -> CaptureFacts`: 설계 2.5 느린 단계 1–7. `list-windows` 한 번으로 hwnd(`window_number(row["id"])`) → 행(pid, process_instance). 스냅샷은 `desktop.run("snapshot", "--window-id", id, "--compact", "--include-bounds", "--timeout-ms", SURVEY_SNAPSHOT_MS)`. 트리는 `trees/W<k>.json`, `trees/map.json`(`{"W3": {"hwnd": …, "pid": …}}`). 주 창이 `MAIN`이고 그 프로세스가 `uia_done`에 없으면 `count_uia`. 주 창 트리를 읽은 직후 `grab(bbox=rect, all_screens=True)`로 `window.png`, `draw_overlay`로 `overlay.png`(주 창이 `GONE`이면 둘 다 없음). `facts.json`을 쓰고 돌려줌.
  - `class CaptureWorker(ctx, folder, on_done: Callable[[CaptureFacts], None], queue_max=SURVEY_QUEUE_MAX)`: `submit(fast) -> bool`(가득 차면 False이고 그 캡처의 `facts.json`을 `slow="QUEUE_FULL"`로 바로 씀), `start()`, `drain(timeout_s) -> list[str]`(남은 캡처를 `QUEUE_ABANDONED`로 쓰고 그 번호들을 돌려줌).

- [ ] **Step 1: 실패하는 시험 작성** (가짜 desktop: `run(*argv) -> envelope`, 부른 argv 기록; 가짜 win32; `grab`은 단색이나 무늬 PIL 이미지를 돌려줌; 가짜 제목·이름에 표지 `SECRET_TITLE`, `SECRET_NAME`, `TfrmSecretScreen`, `CompanySecret.dll`)

```python
def test_pick_targets_order_dedup_and_limit(): ...
@pytest.mark.parametrize("envelope, code", [...])  # read_code의 모든 갈래
def test_read_code(envelope, code): ...
def test_window_kind_main_is_the_largest_unowned_app_window(): ...
def test_button_press_uses_last_foreground_when_own_window_is_in_front(): ...
def test_blank_screen_is_shot_blank(tmp_path): ...
def test_slow_capture_writes_facts_trees_window_and_overlay(tmp_path): ...
def test_facts_hold_no_marker(tmp_path): ...  # facts.json 텍스트에 표지가 하나도 없음
def test_closed_window_is_gone_and_has_no_overlay(tmp_path): ...
def test_uia_runs_once_per_app_process(tmp_path): ...
def test_modules_read_once_per_process(tmp_path): ...
def test_only_read_commands_reach_the_cli(tmp_path): assert {argv[0] for argv in cli.calls} <= {"list-windows", "snapshot"}
def test_full_queue_writes_queue_full_facts_at_once(tmp_path): ...
def test_drain_marks_leftovers_abandoned(tmp_path): ...
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 8: 단축키와 버튼 (`agent/hotkey.py`, `agent/float_button.py`)

**Interfaces — Produces:**
- `agent/hotkey.py`:
  - `parse_hotkey(text: str) -> tuple[int, int]`: `"ctrl+alt+f9"` → (`MOD_CONTROL | MOD_ALT | MOD_NOREPEAT`, `VK_F9`). 수식키 `ctrl`, `alt`, `shift`, `win`(대소문자 무시), 키는 `f1`–`f24`, `a`–`z`, `0`–`9`. 수식키가 없거나 키가 없거나 모르면 `ValueError`.
  - `class HotkeyThread(bindings: dict[str, str], on_signal: Callable[[str], None], user32=None)`: `bindings`는 신호 이름(`"capture"`, `"end"`) → 단축키 글. `start() -> tuple[str, ...]`(등록에 실패한 신호 이름들; 스레드가 등록을 마칠 때까지 기다렸다 돌려줌), `stop()`(`PostThreadMessageW(WM_QUIT)` 후 `UnregisterHotKey`). `user32`를 넘기면 그것을 씀(시험용).
- `agent/float_button.py`:
  - `NO_ACTIVATE_STYLE = WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW`
  - `apply_no_activate(hwnd: int, user32) -> None`: 지금의 확장 스타일에 `NO_ACTIVATE_STYLE`을 OR해서 `SetWindowLongPtrW(GWL_EXSTYLE, …)`.
  - `class FloatButton(on_capture: Callable[[], None], on_end: Callable[[], None])`: tkinter 창(테두리 없음, 항상 위, 끌어서 옮김). `start()`는 창을 만들고 `apply_no_activate`; `set_count(n: int)`, `set_busy(busy: bool)`(스레드 안전하게 `after`로), `run()`(mainloop, 주 스레드), `close()`. 왼쪽 클릭 → `on_capture`, 오른쪽 클릭 → "종료" 메뉴 → `on_end`. tkinter를 불러오지 못하면 `FloatButton.available() -> bool`이 거짓.

- [ ] **Step 1: 실패하는 시험 작성** (실제 단축키·창 없음)

```python
@pytest.mark.parametrize("text", ["ctrl+alt+f9", "Ctrl+Shift+A", "win+alt+0"])
def test_parse_valid(text): ...
@pytest.mark.parametrize("text", ["f9", "ctrl+alt", "ctrl+alt+printscreen", ""])
def test_parse_invalid(text): ...
def test_taken_hotkey_is_reported(): # 가짜 user32.RegisterHotKey가 "end"에만 0 → start() == ("end",)
def test_no_activate_ors_the_style(): # 가짜 user32: Get=0x100 → Set에 0x100|NO_ACTIVATE_STYLE
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 9: 세션 실행 (`agent/survey_session.py`, `agent/settings.py`, `check.py`)

**Files:** Create `agent/survey_session.py`, `tests/test_survey_session.py`; Modify `agent/settings.py`(키 셋), `check.py`(`_with_shortcut` → 공개 `with_shortcut`, 안의 호출도), `tests/test_settings.py`(새 키 시험)

**Interfaces:**
- Consumes: 작업 0–8, `check.run_checks`, `check.check_settings`, `check.pick_target`, `check.with_shortcut`, `check.Deps`, `checks.load_ids`/`ids_path`, `agent.desktop.DesktopClient`/`start_session`, `agent.handles.process_window_handles`, `agent.knowledge.system_name`, `agent.envfiles.load_env`
- Produces:
  - 설정 키: `survey_capture_hotkey`(문자열, `parse_hotkey`가 받아야 함), `survey_end_hotkey`(같음), `survey_app_exes`(절대 경로 문자열 목록). 틀리면 `SettingsError`이고 reason에 값을 넣지 않음.
  - `SessionDeps`(dataclass): `check: check.Deps`, `win32`(기본 모듈 `agent.win32_facts`), `grab`(기본 `PIL.ImageGrab.grab`), `uia_runner=subprocess.run`, `code_state`(기본: 부를 때 `agent.git_state.code_state`를 import), `finish`(기본: 부를 때 `agent.survey_report.finish_session`을 import; 시그니처 `finish(folder: Path) -> list[str]`), `captures_dir: Path = HERE / "captures"`, `now=datetime.now`, `clock=time.monotonic`, `make_hotkeys`(기본 `HotkeyThread`), `make_button`(기본 `FloatButton`, 사용할 수 없으면 None), `make_desktop`(기본: `start_session` + `DesktopClient(binary, session, window_handles=process_window_handles, read_shortcut=check.Deps().read_shortcut)`).
  - `class Session(args, deps: SessionDeps)`: 시험이 스레드 없이 몰 수 있는 핵심.
    - `start() -> list[str]`: 설계 2.1의 1–5(점검, 코드 상태, DPI, desktop, 첫 프로세스 갱신, `.check` 읽기). 콘솔에 찍을 줄을 돌려줌.
    - `tick(obs: Observation | None = None) -> None`: 프로세스 갱신 + 감시 한 걸음 + 이벤트 기록(`events.jsonl`).
    - `capture() -> str`: `win32.beep()` 뒤 빠른 단계를 하고 느린 단계를 worker에 넘기고 캡처 번호를 돌려줌. 앱 프로세스가 하나도 없고 첫 캡처면 전경 창 프로세스(`SYSTEM` 아님)를 `tracker.pick`.
    - `end() -> list[str]`: 설계 2.6. `session.json`(`app_seen`: `already_running`이면 `ALREADY_RUNNING`, `pick`을 썼으면 `PICKED_BY_CAPTURE`, 앱 창을 봤으면 `STARTED`, 아니면 `NOT_SEEN`; `processes`는 tracker의 `APP`·`CHILD_OTHER` 프로세스마다 `ProcessFact`), `--show-names`면 `names.json`(`{"classes": [...], "modules": [...]}`, 모르는 것만, 정렬), 그다음 `deps.finish(folder)`의 줄을 붙여 돌려줌.
  - `run_session(args, deps: SessionDeps | None = None) -> int`: `Session`을 만들고 감시 스레드, 처리 스레드, 단축키 스레드, 버튼(주 스레드 mainloop; 없으면 주 스레드가 종료 신호를 기다림)을 묶음. Ctrl+C(`KeyboardInterrupt`)와 종료 신호는 같은 `end()`. 종료 코드: 끝까지 갔으면 0, 인자·env·설정 오류 2.
  - `args`의 칸: `exe`, `system`, `settings`, `profile`, `profile_dir`, `skip`(목록), `show_names`(bool).
  - 세션 폴더: `captures_dir / f"{now:%Y%m%d-%H%M%S}-{system}"`, 시스템 부분의 파일 이름에 못 쓰는 글자는 `_`, 이미 있으면 `-2`, `-3`.

- [ ] **Step 1: 실패하는 시험 작성** (`tests/test_check.py`의 `CheckCli`·`FakeModel`을 본뜬 가짜, 가짜 win32 객체의 관찰을 시험이 순서대로 바꿈, 가짜 `finish`는 `["[전달] fake"]`를 돌려주고 부른 폴더를 기록)

```python
def test_session_writes_events_captures_and_session_json(env): ...
def test_console_and_name_free_files_hold_no_marker(env, capsys): ...
def test_show_names_writes_names_json_only_then(env): ...
def test_hung_app_keeps_fast_captures_coming(env):
    # 가짜 snapshot이 매번 HARNESS_TIMEOUT, worker를 멈춘 채 capture() 7번 → screen.png 7개, 6·7번째는 QUEUE_FULL
def test_restart_mid_session_gives_two_app_processes(env): ...
def test_no_app_then_first_capture_picks_foreground(env): ...  # app_seen == "PICKED_BY_CAPTURE"
def test_hotkey_taken_continues_with_button(env, capsys): ...   # run_session, 가짜 make_hotkeys가 ("capture",) 실패 → 콘솔에 HOTKEY_TAKEN, session.json hotkey
def test_no_button_continues_with_hotkey(env): ...
def test_interrupt_before_any_capture_still_finishes(env): ...  # KeyboardInterrupt → session.json, finish 불림, 0
def test_session_folder_name_is_sanitized_and_unique(env): ...  # 시스템 "가 나/다" 두 번 → 두 폴더
def test_only_read_commands_reach_the_cli(env): assert set(cli.commands) <= {"session", "list-windows", "snapshot", "version"}
def test_settings_reads_survey_keys(tmp_path): ...  # tests/test_settings.py: 좋은 값 읽힘, 나쁜 단축키·상대 경로는 SettingsError, reason에 값 없음
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인. `tests/test_check.py`, `tests/test_settings.py`가 그대로 통과해야 함.

---

## W2

### 작업 10: 커밋과 코드 변경 (`agent/git_state.py`)

2026-11-25 계획 작업 6을 그대로 합니다(`code_state(folder: Path, runner=subprocess.run) -> CodeState`, `CodeState = NamedTuple(head: str | None, changed: tuple[str, ...])`, 시험 여섯 개).

- [ ] **Step 1–4:** 그 계획의 시험 → 실패 확인 → 구현 → 통과 확인

### 작업 11: 가설 (`agent/hypotheses.py`)

**Interfaces:**
- Consumes: `LoadedSession`, `Labels`, `survey_codes`의 값과 `MEANINGS`
- Produces:
  - `Verdict = NamedTuple(code: str, short: str, evidence: str)` — 모두 수·계열 이름·코드만.
  - `Hypothesis`(frozen dataclass): `id, title, why, kind("auto"|"manual"), codes: tuple[str, ...], next: dict[str, str], judge: Callable[[LoadedSession], Verdict] | None, how_to_answer: str | None, status: str = "open", closed_note: str | None = None`. H09·H11처럼 코드가 값으로 만들어지는 가설은 `codes`에 그 형태를 설명하는 대표 코드(H09 `"WxH@S"`, H11 `"COMPLETE"`, `"TRUNCATED"`)를 두고 `next`는 그 대표 코드를 덮음.
  - `HYPOTHESES: tuple[Hypothesis, ...]` (H01–H18, 설계 4장의 순서·질문·코드)
  - `main_windows(loaded) -> list[tuple[CaptureFacts, WindowFact]]`: 앱 `MAIN` 창 중 read가 `OK`·`TRUNCATED`인 것, 캡처 순서.
  - `judge_all(loaded: LoadedSession, labels: Labels) -> list[tuple[Hypothesis, Verdict]]`: manual은 `labels.manual`의 답 또는 `"?"`. 판정 중 예외는 `Verdict("ERROR", type(e).__name__, "")`.
- 규칙: 설계 4장 표와 "H05의 비교", "팝업으로 세는 창", 그리고 Global Constraints의 문턱값. 앱 가설(H01–H05, H07–H09, H11, H15, H17, H18)은 앱 프로세스가 없으면 `SKIPPED(NO_APP)`, 앱 창을 하나도 읽지 못했으면 `SKIPPED(NO_READ)`(H15·H17·H18은 감시 기록만으로도 판정하므로 `NO_APP`일 때만). H01·H02·H08은 `APP` 프로세스의 `classes`·`modules` 합. H02에서 모듈을 하나도 읽지 못했고 UIA도 없으면 `SKIPPED(MODULES_UNREAD)`. H04는 UIA가 있는 첫 캡처. H10은 `CheckFact` 이름 `LLM 도구 호출`·`LLM 그림 입력`·`비전 호출`(2026-11-25 계획 작업 4의 H10 규칙).

- [ ] **Step 1: 실패하는 시험 작성** (작업 0의 자료형으로 `LoadedSession`을 직접 만드는 `session_of(...)` 도우미)

```python
def test_registry_rules():  # 번호 H01–H18 순서, auto↔judge, manual↔how_to_answer, next가 codes를 덮음, 글에 MES 없음
def test_h01_delphi_wins_at_seventy_percent(): ...
def test_h01_win32_only_counts_alone(): ...
def test_h03_boundaries(): ...  # 0.6, 0.2
def test_h05_compares_two_processes_of_one_session(): ...  # 같은 계열·종류·요소 수 20% 안 → 키 같으면 STABLE, 다르면 CHANGES
def test_h05_different_screens_stay_pending(): ...          # 요소 수가 30% 다름 → PENDING
def test_h05_against_saved_check_ids(): ...                 # 다른 process 표시 → 비교, 같은 표시 → PENDING
@pytest.mark.parametrize("app_seen, target, code", [("ALREADY_RUNNING", True, "ALREADY_RUNNING"), ("STARTED", True, "EXE"),
    ("STARTED", False, "LAUNCHER"), ("PICKED_BY_CAPTURE", True, "PICKED_BY_CAPTURE"), ("NOT_SEEN", False, "NOT_SEEN")])
def test_h06(app_seen, target, code): ...
def test_h15_counts_owned_or_modal_app_windows_and_foreground_takers(): ...  # 소유 없는 앱 창은 pre로, 토스트(전경 안 가져감)는 셈하지 않음
def test_h15_not_seen_when_already_running(): ...
def test_h16_outside_startup_system_vs_other(): ...
def test_h17_counts_hung_and_longest_seconds(): ...
def test_h18_dialog_kinds(): ...
def test_no_app_skips_app_hypotheses_but_not_h06_h10(): ...
def test_manual_answers_come_from_labels(): ...
def test_a_judge_that_raises_becomes_error(monkeypatch): ...
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 12: `labels.yaml` 검사 (`agent/labels.py`)

**Interfaces — Produces:**
- `LabelProblem = NamedTuple(line: int, rule: str)`; 규칙 이름은 `YAML`, `TOP_KEY`, `VERSION`, `CAPTURE_ID`, `SCENE_CODE`, `MANUAL_ID`, `MANUAL_CODE`, `NOT_TEXT`.
- `class LabelError(Exception)`: `problems: tuple[LabelProblem, ...]`. `str(e)`는 `"LABEL_INVALID 3행 SCENE_CODE; 7행 MANUAL_CODE"` 형태로 값 없이.
- `load_labels(path: Path, capture_ids: tuple[str, ...], manual_codes: dict[str, tuple[str, ...]]) -> Labels`: 파일이 없으면 `Labels({}, {})`. UTF-8(BOM 허용), CRLF 허용. 줄 번호는 `yaml.compose`의 노드 표시에서. 설계 5장 검사 규칙.

- [ ] **Step 1: 실패하는 시험 작성**

```python
def test_good_file(tmp_path): ...
def test_missing_file_is_empty(tmp_path): ...
def test_bom_and_crlf_are_accepted(tmp_path): ...
def test_sentence_value_is_rejected_without_echo(tmp_path):
    write(tmp_path, "version: 1\ncaptures:\n  C01: 출하지시 저장 실패\n")
    with pytest.raises(LabelError) as caught: load_labels(...)
    assert caught.value.problems == (LabelProblem(3, "SCENE_CODE"),) and "출하" not in str(caught.value)
@pytest.mark.parametrize("text, rule", [("extra: 1\n", "TOP_KEY"), ("version: 2\n", "VERSION"), ("captures:\n  C99: MAIN\n", "CAPTURE_ID"),
    ("manual:\n  H01: RICH\n", "MANUAL_ID"), ("manual:\n  H12: MAYBE\n", "MANUAL_CODE"), ("captures:\n  C01: [MAIN]\n", "NOT_TEXT"), ("captures: [\n", "YAML")])
def test_each_rule(tmp_path, text, rule): ...
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 13: 전달 블록 (`agent/survey_block.py`)

**Interfaces:**
- Consumes: `LoadedSession`, `Labels`, `Hypothesis`/`Verdict`, `capture_codes`, `CodeState`
- Produces:
  - `check_chars(text: str) -> str`: `" ".join(text.split())`의 UTF-8 CRC32(`zlib.crc32`)를 `CHECK_ALPHABET`으로 두 글자(`crc % 32`, `(crc // 32) % 32`).
  - `render_block(loaded, judged, labels, now: CodeState | None = None) -> list[str]`: 설계 6장. 머리 줄 `[전달] v<SURVEY_VERSION> · <head|커밋?> · <변경없음|변경있음 N> · <system> · 캡처<n> · <분>분`, `now`가 있고 다르면 `a→b`/`변경있음 0→2`. 가설 줄은 `open`만 4개씩, 칸 사이 공백 3칸, `ID 코드` + (`short`가 있으면 `(short)`). 캡처 줄 `C01 <장면> <kind>/<rel>/<state>`, 줄이 모자라면 묶기(`C03~C05`) 뒤 빼기(`MAIN`·`?`이면서 `OK`인 것부터, 마지막 줄 `… 외 N`). 줄마다 끝에 ` ·` + `check_chars(앞부분)`. 최대 `BLOCK_MAX_LINES`.
  - `BlockLine = NamedTuple(text: str, ok: bool)`, `parse_block(text: str) -> list[BlockLine]`: `[전달]`로 시작하는 줄부터 빈 줄이나 끝까지, 줄 앞뒤 공백 무시.
  - `explain(lines: list[BlockLine]) -> list[str]`: 줄마다 `오타?` 표시, 버전 차이 알림, 가설 판정마다 뜻과 `next`, 캡처 줄의 장면·구조 코드 뜻(`MEANINGS`), 모르는 코드는 `모르는 코드`.

- [ ] **Step 1: 실패하는 시험 작성**

```python
def test_header_and_four_hypotheses_per_line(): ...
def test_block_never_exceeds_fifteen_lines(): ...  # 캡처 40개
def test_grouping_then_dropping_order(): ...
def test_changed_after_session_shows_both_values(): ...  # now.changed 2개, session.changed 0 → "변경있음 0→2"
def test_same_state_gives_identical_block(): ...
def test_every_word_is_allowed(): # 블록의 낱말이 코드·수·시스템 이름·커밋·고정 글자(전달, 변경없음 …) 집합에 속함
def test_one_changed_character_is_caught_on_that_line_only(): ...
def test_explain_gives_meaning_and_next_and_marks_unknown(): ...
def test_check_chars_ignore_spacing(): assert check_chars("H01  DELPHI") == check_chars("H01 DELPHI")
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 14: 결과 문서, 세션 마무리, 진입점 (`agent/survey_report.py`, `survey.py`)

**Files:** Create `agent/survey_report.py`, `survey.py`, `tests/test_survey_report.py`, `tests/test_survey_cli.py`, `docs/company-survey/hypotheses.md`(생성), `docs/company-survey/codes.md`(생성), `docs/company-survey/feedback/README.md`

**Interfaces:**
- Consumes: 작업 10–13, `load_session`, `survey_session.run_session`(부를 때 import)
- Produces:
  - `summary_markdown(loaded, judged, labels, names: dict | None, changed_files: tuple[str, ...]) -> str`: 머리(시각, 버전, 커밋, 바뀐 추적 파일 이름), 전달 블록, 가설마다 절(판정, 근거, 왜, 판정별 다음 할 일; manual은 답과 답하는 방법), 캡처 목록(번호, 시각, 구조 코드, 장면, 창 수, 폴더 이름), `names`가 있으면 마지막 절 `## 원래 이름 (옮겨 적기 전에 확인)`.
  - `finish_session(folder: Path, now: CodeState | None = None) -> list[str]`: `load_session` → `load_labels`(실패하면 `LabelError`를 그대로 올림) → `judge_all` → `render_block` → `summary.md`, `block.txt` 쓰기 → 블록 줄 돌려줌.
  - `hypotheses_markdown() -> str`, `codes_markdown() -> str`, `HYPOTHESES_DOC = Path("docs/company-survey/hypotheses.md")`, `CODES_DOC = Path("docs/company-survey/codes.md")`. 머리에 "이 파일은 `python survey.py --write-docs`로 만듭니다. 손으로 고치지 마세요."
  - `survey.py`: `main(argv=None) -> int`. 상호 배타 모드: (기본) 세션 → `run_session(args)`; `--block <세션|latest>` → `captures/`에서 그 폴더(`latest`는 이름순 마지막), `finish_session(folder, now=code_state(HERE))`, 블록 출력, 0 / `LABEL_INVALID …`는 1; `--read-block <파일>` → `parse_block` + `explain` 출력, 0(오타가 있어도 0, 파일 없음 2); `--write-docs` → 두 문서를 쓰고 0. 세션 인자: `--exe`, `--system`, `--settings`, `--profile`, `--profile-dir`, `--skip llm`, `--show-names`. 파일 끝에서 `sys.stdout.reconfigure(errors="replace")`.
  - `feedback/README.md`: 파일 이름(`<YYYY-MM-DD>-<시스템>.md`), 양식(날짜, 시스템, 블록을 코드 블록으로, "말로 덧붙인 것" 절), `--read-block` 쓰는 법.

- [ ] **Step 1: 실패하는 시험 작성** (작업 0 자료형으로 만든 가짜 세션 폴더, 표지 `TfrmSecretScreen`, `CompanySecret.dll`)

```python
def test_finish_writes_summary_and_block_and_returns_lines(tmp_path): ...
def test_names_appear_only_in_their_section(tmp_path): ...
def test_without_names_there_is_no_names_section(tmp_path): ...
def test_label_error_writes_no_block(tmp_path): ...
def test_committed_docs_are_current(): assert (HERE / HYPOTHESES_DOC).read_text(encoding="utf-8") == hypotheses_markdown() and ...
def test_cli_block_latest_prints_block_and_exit_zero(tmp_path, capsys, monkeypatch): ...
def test_cli_block_label_invalid_exit_one_without_value(tmp_path, capsys, monkeypatch): ...
def test_cli_read_block_marks_typo(tmp_path, capsys): ...
def test_cli_korean_system_name_does_not_crash_on_cp949(tmp_path, monkeypatch): ...  # stdout을 cp949 TextIOWrapper로
def test_cli_session_mode_calls_run_session_with_args(monkeypatch): ...
def test_write_docs_touches_only_the_two_docs(tmp_path, monkeypatch): ...
```

- [ ] **Step 2–4:** 실패 확인 → 구현(문서 두 개를 `--write-docs`로 생성) → 통과 확인

### 작업 15: 회사 Claude Code 실행·정리 전용 장치 (`company/`)

2026-11-25 계획 작업 10을 그대로 하되(`guard.py`의 `decide`·`main`, `settings.json`, 루트 `.gitignore` 예외, 시험 전부), `company/CLAUDE.md`의 내용은 **설계 7장**을 따릅니다: 첫 줄 우선 선언, 역할, 정리 절차 1–6(`summary.md`·`session.json`·`events.jsonl` 읽기 → 캡처별 `notes.md` 정리 → 장면 코드를 `labels.yaml`에 → 사람이 말한 답만 `manual`에 → `python ..\survey.py --block <세션>` → 블록을 그대로 보여 줌), `notes.md`는 회사 PC 전용임을 사람에게 알림, 해도 되는 것·하면 안 되는 것, 첫 정리 때 훅이 도는지 확인하는 법. 장면 코드표는 `docs/company-survey/codes.md`를 가리키고 CLAUDE.md에 복사하지 않습니다.

- [ ] **Step 1:** 그 계획 작업 10의 시험 + `test_captures_folder_is_writable`(`captures/x/labels.yaml` Edit 허용) + `test_claude_md_points_to_codes_doc_and_block_command`(본문에 `codes.md`와 `--block`이 있음).
- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

---

### 작업 16: 통합 (오케스트레이터)

**Files:** Modify `tests/test_application_agnostic.py`(`PYTHON_SOURCES`에 `survey.py`, 새 `agent/` 모듈은 이미 폴더로 들어가는지 확인), `README.md`(이 폴더, "회사 시스템 캡처 세션" 절), 저장소 루트 `CLAUDE.md`의 `examples/llm-agent` 명령 목록과 한 단락, `docs/cua-background.md`의 "집–회사 제약" 끝 한 문장, 설계 문서 상태 줄, 2026-11-25 계획 상태 줄("대체됨"); Create `tests/test_survey_end_to_end.py`

- [ ] **Step 1: 실패하는 시험 작성** — `test_session_to_block_to_read_block`: 가짜 win32·CLI·grab으로 `run_session`을 돌려(앱 시작, 팝업 하나, 캡처 둘, 종료) 진짜 `finish_session`이 블록을 찍고, `labels.yaml`을 쓴 뒤 `survey.py --block latest`가 장면 코드가 들어간 블록을 찍고, 그 블록을 파일에 넣어 `--read-block`이 오타 없이 풀어 내는지. 콘솔·이름 없는 파일 전부에 표지가 없는지.
- [ ] **Step 2: 실패 확인 → 맞물리지 않는 곳 고치기**(고칠 곳이 W1·W2의 파일이면 그 담당에게 되돌림)
- [ ] **Step 3:** 문서 고치기. README 절: 세션 명령, 단축키와 버튼, `captures/` 폴더의 파일과 무엇이 회사 PC 전용인지, 회사 Claude Code는 `company/`에서 시작해 "정리해줘", 블록 옮겨 적기, 집에서 `--read-block`.
- [ ] **Step 4:** 전체 `pytest` → 실패 0; `python survey.py --write-docs` 뒤 `git diff --stat docs/company-survey/`가 비어 있음.
- [ ] **Step 5:** 실제 창 확인은 사용자에게 확인받은 뒤에 합니다: 가상 MES 복사본으로 세션을 열고, 앱을 켜고, 팝업에서 단축키와 버튼으로 캡처해 버튼을 눌러도 포커스가 옮겨지지 않는지, H01 `WINFORMS`, H15, 캡처 줄이 맞는지, `overlay.png`가 트리와 맞는지 눈으로 봅니다.
- [ ] **Step 6:** 커밋은 사용자가 요청할 때.
