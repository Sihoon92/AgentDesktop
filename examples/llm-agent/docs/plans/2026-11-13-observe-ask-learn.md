# 관찰 넓히기, 사람에게 묻기, 답에서 배우기 Implementation Plan

> **For agentic workers:** 이 계획은 `ORCHESTRATION.md`에 따라 작업자 세 명(w1, w2, w3)이 나눠 구현합니다. 자기 배정 문서(`.coordination/assignments/<id>.md`)가 가리키는 작업만 순서대로 하고, 단계의 체크박스(`- [ ]`)로 진척을 적습니다. 구현은 테스트를 먼저 씁니다(superpowers:test-driven-development).

**Goal:** `examples/llm-agent` 에이전트가 화면의 모든 요소를 번호로 가리키고, 캡처를 보고, 동작의 결과를 기대와 비교하고, 확실하지 않으면 사람에게 묻고, 성공한 실행의 질문과 답을 시스템별 지식으로 등록해 다음 실행에서 참고하게 만든다.

**Architecture:** 세 층으로 나눈다. 좌표 계산, 표의 교차 지점, 변화 비교, 질문 강제 규칙은 앱을 모르는 순수 모듈(`agent/geometry.py`, `table.py`, `effects.py`, `policy.py`)이고, 의미 판단은 모델이 한다. 시스템 고유 지식은 YAML 파일(`agent/knowledge.py`)이며 코드가 재생하지 않고 모델에게 글로 준다. `Toolbox`가 이 모듈들을 묶고, 질문은 승인 대기와 같은 방식의 `AskGate`로 뷰어 서버와 주고받는다.

**Tech Stack:** Python 3(conda 환경 `AgentDesktop`), OpenAI Responses API, `pyyaml`, `pytest`, 새 의존성 `pillow`(캡처 자르기와 번호 박스 그리기). 뷰어는 표준 라이브러리 서버와 프레임워크 없는 JS. 가상 앱은 C# 5 WinForms.

**Spec:** [`../specs/2026-11-13-observe-ask-learn-design.md`](../specs/2026-11-13-observe-ask-learn-design.md). 배경은 [`../cua-background.md`](../cua-background.md). 작업자는 둘 다 읽는다.

## Global Constraints

- 에이전트 실행 코드(`agent/`, `viewer/`, `run.py`, `view.py`)에 앱 이름, 창 제목, 화면 배치, 특정 버튼 이름을 가정한 분기를 넣지 않는다. 그런 값은 테스트와 가상 앱에만 있다.
- `agent-desktop` CLI(Rust)는 고치지 않는다. CLI에 없는 기능이 필요하면 `blocked`로 보고한다.
- 지식 항목에는 화면 좌표를 넣지 않는다. 항목의 키는 이 문서 "계약 4"에 적힌 것뿐이다.
- 답이 없으면 진행하지 않는다. 질문 대기 시간(기본 1800초)이 지나면 실행은 `stopped`로 끝난다.
- 지식은 사용자가 승인한 항목만 저장한다. 실패하거나 멈춘 실행, `claimed` 실행에서는 저장하지 않는다.
- `run.json`에는 질문 글, 답, 지식 내용, 화면 글자를 넣지 않는다. 이 내용은 `events.jsonl`에만 남는다.
- 뷰어 서버는 표준 라이브러리만 쓰고 127.0.0.1과 토큰 검사를 유지한다.
- 새 의존성은 `pillow>=11` 하나다. 없어도 실행은 되어야 한다(캡처를 자르거나 그리지 않고 그대로 준다).
- 실제 창을 띄우거나 커서를 움직이는 테스트는 `VIRTUAL_MES_LIVE=1`일 때만 돌고, 데스크톱 잠금을 받은 작업자만 돌린다. 잠금은 오케스트레이터가 사용자에게 확인한 뒤 넘긴다.
- 작업자는 커밋하지 않는다. 자기 편집 경로 밖의 파일을 고치지 않는다.
- 사용자에게 보이는 글(뷰어, 터미널)은 한국어, 모델에게 가는 글(도구 설명, 거절 사유)은 영어다. 지금 코드의 관례와 같다. 완료 확인의 `detail`(`agent/completion.py`)은 뷰어에서 사용자가 읽는 글이므로 한국어이고, 모델에게도 그대로 간다.
- 기준: 시작할 때 `python -m pytest -q`는 `340 passed, 43 skipped`다. 기존 테스트는 통과하거나, 고친 이유를 보고서에 적는다.
- 코드가 설계 문서나 이 계획과 다르게 나오면 그 사실을 보고하고, 오케스트레이터가 문서를 고친다.

## Review Focus

설계 문서가 말하지 않지만 쓰는 사람이 부딪힐 입력입니다. 각 줄의 테스트는 표시한 작업에 들어 있습니다.

1. **같은 질문에 답을 두 번 보내거나, 이미 끝난 질문·없는 질문에 답한다.** 두 번째 답은 409로 거절되고 실행에 영향이 없다. (W2-2)
2. **캡처를 본 뒤 창이 움직였거나, 찍은 위치가 캡처 밖이다.** 좌표를 변환하지 않고 거절하며, 다시 보라고 알린다. (W1-5, W1-7, W2-2)
3. **지식 파일을 손으로 고치다 깨뜨렸다(YAML 오류, 필수 키 없음, 모르는 키).** 그 항목만 건너뛰고 경고를 남기며 실행은 계속된다. (W3-3)
4. **번호가 가리키는 요소가 화면 밖(`offscreen`)이거나 크기가 0이다.** 누르지 않고 거절한다. (W1-7)
5. **표의 위 헤더와 아래 헤더에 같은 이름이 있다("계획" 아래의 "계획").** 열 후보를 둘로 세지 않고 가장 좁은 것 하나로 잡는다. (W1-3)

---

## 계약

세 작업자가 서로의 코드를 보지 않고도 맞물리게 하는 약속입니다. 여기 적힌 이름과 모양은 바꾸지 않습니다. 바꿔야 하면 `blocked`로 보고합니다.

### 계약 1. 모델이 부르는 도구

| 도구 | 단계 | 읽기 전용 | 인자 |
| --- | --- | --- | --- |
| `look` | 0 | 예 | `mark: int \| null`(null이면 창 전체) |
| `find_cells` | 0 | 예 | `rows: [str]`, `columns: [str]` |
| `ask` | 0 | 예 | `kind: "confirm" \| "stuck"`, `text: str`, `candidates: [{label: str, mark: int \| null, capture: str \| null, x: int \| null, y: int \| null}]`, `proposed: int \| null`(1부터), `basis: [int]`(표시 번호) |
| `click_part` | 2 | 아니오 | `mark: int`, `part: "center" \| "left" \| "right" \| "top" \| "bottom"`, `action: "click" \| "double_click" \| "right_click"` |
| `click_point` | 2 | 아니오 | `capture: str`, `x: int`, `y: int`(캡처의 이미지 픽셀), `action`(위와 같음) |
| `finish` | 0 | 예 | 지금의 `success`, `evidence`에 `saved_path: str \| null`, `completion_id: str \| null` 추가 |

- 다시 관찰하는 도구(`REOBSERVING_TOOLS`와 `click_part`, `click_point`)는 모두 `expect`와 `knowledge_id: str | null`을 받는다. `expect`는 `"window_opened" | "window_closed" | "content_changed" | "value_changed" | "any"` 중 하나다.
- 하니스는 `expect`가 없는 호출도 받는다(baseline과 기존 테스트). 이때는 기대 비교와 횟수 세기를 하지 않는다.
- `look`, `find_cells`, `ask`는 `READ_ONLY_TOOLS`에 넣는다. 동작 전 승인(`--approve`)을 거치지 않는다.

### 계약 2. 동작 도구의 결과와 거절

동작 도구의 결과에 다음이 붙는다.

```json
{"expect": "window_opened", "expected_met": false, "capture": "c3",
 "effects": {"windows_opened": [{"id": "w-2", "title": "목록"}], "windows_closed": [],
             "added": {"count": 3, "names": ["WA0 #울산 믹싱"]}, "removed": {"count": 0, "names": []},
             "changed": [{"mark": 12, "name": "기간", "field": "value", "before": "전일", "after": "전주"}],
             "focus": {"mark": 4, "name": "라인"}, "none": false}}
```

`names`와 `changed`는 8개까지다. `capture`는 기대와 다르고 이미지 입력이 켜져 있을 때만 있다.

정책이 거절하면 기존 `_refused` 모양에 `refused_code`를 더한다.

| `refused_code` | 뜻 | 풀리는 조건 |
| --- | --- | --- |
| `needs_confirmation` | 누르기 전 확인이 필요한 대상이다 | 그 대상이 후보인 질문에 답을 받았거나, 유효한 `knowledge_id`를 밝힘 |
| `blocked_until_question` | 기대와 다른 결과가 연속 `mismatch_limit`번이거나, 지식을 참고한 동작이 기대와 달랐다 | 종류와 상관없이 질문 하나에 답을 받음 |
| `unknown_knowledge` | `knowledge_id`가 이 시스템의 사용 중인 항목이 아니다 | 올바른 id나 null |
| `stale_capture` | 캡처를 찍은 뒤 창의 위치나 크기가 바뀌었다 | `look`을 다시 부름 |
| `no_one_to_ask` | 답할 사람이 없는 실행이다(`ask` 도구) | 없음. 모델은 읽기만 하거나 `finish(success=false)` |

확인이 필요한 대상은 두 가지다. 모델이 고른 좌표를 쓰는 도구(`mouse_click`, `click_point`, `drag`)의 모든 호출, 그리고 가장 최근 `find_cells`가 후보를 둘 이상 돌려줬을 때 그 후보 영역 안을 누르는 모든 호출(ref 도구 포함).

### 계약 3. 묻는 쪽의 인터페이스 (`agent/ask.py`, w2가 만듦)

```python
@dataclass(frozen=True)
class Answer:
    text: str = ""
    pin: tuple | None = None        # (x, y) 화면 픽셀
    accepted: bool | None = None    # kind가 "completion"일 때만
    items: tuple = ()               # kind가 "registration"일 때만
    timed_out: bool = False

NO_ANSWER = Answer(timed_out=True)

class AskGate:                      # 뷰어용. interactive = True
    def __init__(self, timeout_s=1800, clock=time.monotonic): ...
    def ask(self, question: dict) -> Answer: ...            # 답이나 시간 초과까지 기다림
    def answer(self, question_id: str, answer: Answer) -> bool: ...   # 모르는 id, 이미 답한 id는 False
    def waiting(self, question_id: str) -> bool: ...        # 질문이 떠 있고 아직 답이 없으면 True

class TerminalAsker:                # run.py용. interactive = True
    def __init__(self, ask=input, show=print): ...
    def ask(self, question: dict) -> Answer: ...

class NoAsker:                      # interactive = False
    def ask(self, question: dict) -> Answer: ...            # 항상 NO_ANSWER
```

`Toolbox`가 넘기는 `question`은 화면 좌표 기준이다.

```python
{"type": "question", "id": "q1", "step": 3,
 "kind": "confirm" | "stuck" | "completion" | "registration",
 "text": "...",
 "candidates": [{"n": 1, "label": "...", "mark": 57, "bounds": {...} | None, "point": [x, y] | None}],
 "proposed": 1 | None,
 "basis": [{"mark": 12, "bounds": {...}}],
 "items": [...],                    # registration: 계약 5의 초안 항목
 "window_id": "w-1" | None}
```

### 계약 4. 지식 창고 (`agent/knowledge.py`, w3가 만듦)

파일은 `<knowledge_dir>/<system>/<id>.yaml`이고 항목 하나가 파일 하나다. 기본 `knowledge_dir`은 `examples/llm-agent/knowledge/`(git 제외)이고 환경 변수 `AGENT_KNOWLEDGE_DIR`, 작업 파일의 `knowledge_dir`, `--knowledge-dir` 순으로 덮어쓴다.

```yaml
id: r-20261113-001           # 해결 기록은 r-, 완료 조건은 c-
type: resolution
status: active               # active | disabled
situation: 선택 칸의 가운데를 눌러도 목록이 열리지 않는다
resolution: 선택 칸은 오른쪽 끝의 화살표를 눌러 연다
source: {run_id: 20261113-101500, question: "...", answer: "...", date: 2026-11-13}
uses: 0
failures: 0
```

```yaml
id: c-20261113-001
type: completion
status: active
task: 조회 결과를 엑셀로 저장하는 작업
checks:
  - {kind: file, pattern: "*.xlsx"}            # path가 없으면 finish의 saved_path를 확인
  - {kind: element, require: {role: statictext, name_contains: 저장 완료}}
  - {kind: human}
source: {run_id: ..., date: ...}
uses: 0
failures: 0
```

```python
class KnowledgeError(ValueError): ...

class KnowledgeStore:
    def __init__(self, root, system): ...
    system: str
    warnings: list[str]                                   # 건너뛴 파일과 이유
    def entries(self, include_disabled=False) -> list[dict]: ...
    def get(self, entry_id) -> dict | None: ...
    def add(self, entry: dict, today=None) -> dict: ...   # id를 매기고 검증한 뒤 저장
    def update(self, entry_id, changes: dict) -> dict: ...
    def delete(self, entry_id) -> bool: ...
    def count_use(self, entry_id) -> None: ...
    def count_failure(self, entry_id) -> None: ...

def system_name(explicit, exe) -> str: ...                # explicit이 없으면 exe 파일 이름(확장자 제외, 소문자)
def list_systems(root) -> list[str]: ...
def relevant(store, goal, situation=None) -> list[dict]: ...        # 지금은 사용 중인 항목 전부. 항목이 늘면 여기만 검색으로 바꿈
def briefing(entries, limit_chars=6000) -> tuple[str, bool]: ...   # 모델에게 줄 글, 잘렸는지
def check_entry(entry) -> dict: ...                       # 저장하지 않고 검증만. 기본값을 채운 항목을 돌려줌
def knowledge_root(explicit=None, task_dir=None, environ=None) -> Path: ...   # 명령줄, 작업 파일, AGENT_KNOWLEDGE_DIR, 기본 폴더 순
```

### 계약 5. 실행 기록의 새 이벤트 (`events.jsonl`)

`recorder`는 화면 좌표의 `bounds`와 `point`를 지금의 `_image_target`과 같은 방식으로 이미지 좌표 `rect`, `image_point`로 바꿔 함께 적고, 그 질문이 가리키는 관찰의 id를 `observation`에 적는다.

| `type` | 필드 |
| --- | --- |
| `question` | 계약 3의 질문 + `observation`, 후보와 근거마다 `rect`, `image_point` |
| `answer` | `id`, `text`, `pin: {"point": [x, y], "image_point": [x, y] \| null, "mark": n \| null} \| null`, `accepted`, `items`, `timed_out` |
| `capture` | `id`("c1"…), `step`, `observation`, `mark`, `reason`("look" \| "mismatch" \| "question"), `file`("captures/c1.png") |
| `knowledge_loaded` | `system`, `ids`, `truncated` |
| `knowledge_used` | `step`, `id`, `text`(그 항목의 `resolution`) |
| `knowledge_failed` | `step`, `id` |
| `completion` | `by`("task" \| "knowledge" \| "human"), `id`(완료 조건 id 또는 null), `met`, `detail` |
| `registration` | `saved: [{"id", "type", "action": "add" \| "update"}]`, `rejected: int` |
| `warning` | `message`(사용자가 읽는 한국어 글. 예: 등록 중 오류) |
| `action`(기존) | `expect`, `effects`, `expected_met`, `knowledge_id`, `refused_code`, `capture`(그 동작에 붙은 캡처 id) 추가. `effects`는 `expect`가 없어도 붙고, `expect`와 `expected_met`은 `expect`를 받았을 때만 붙는다 |

등록 초안 항목과 그 답의 모양은 다음과 같다.

```python
# question["items"]
{"n": 1, "action": "add" | "update", "id": "r-..." | None,
 "entry": {"type": "resolution", "situation": "...", "resolution": "..."}}     # 또는 completion의 task, checks
# answer.items
{"n": 1, "decision": "approve" | "edit" | "reject", "entry": {...}}            # edit일 때만 entry가 고친 내용
```

실행 상태에 `confirmed`(사람이 결과를 확인함)가 새로 생긴다. 종료 코드는 0이다. `claimed`는 답할 사람이 없는 실행에만 남는다.

### 계약 6. `Toolbox`, `run_agent`, `Task`

```python
Toolbox(desktop, task, confirm, sleep=time.sleep, clock=time.monotonic, observer=None,
        asker=None, knowledge=None, captures_dir=None, wall=time.time)
toolbox.can_ask -> bool                                   # asker.interactive
toolbox.ask(question: dict) -> Answer                     # id와 step을 채우고, 기록하고, 시간 초과면 AgentStopped
toolbox.briefing() -> str                                 # 지식이 없으면 ""
toolbox.exchanges() -> list[dict]                         # 계약 7
toolbox.check_completion(completion_id, saved_path) -> dict   # {"met": bool, "by": "harness" | "human", "detail": str}
toolbox.take_attachments() -> list                        # 마지막 호출이 만든 Capture들. 읽으면 비워짐
toolbox.precheck(name, arguments) -> dict | None          # 정책이 거절할 호출이면 그 거절(계약 2), 아니면 None. 아무것도 실행하지 않음
toolbox.finish_note: dict | None                          # 루프가 끝낼 때 채움: {"goal", "evidence", "saved_path", "by": "harness" | "human"}

run_agent(model, toolbox, task, log=lambda _event: None, gate=None, register=None) -> RunResult
# register(model, toolbox, log) -> int(쓴 토큰). 최종 상태가 verified나 confirmed일 때만 불림

Task 새 필드: system: str | None = None, knowledge_dir: str | None = None,
              image_mode: str = "user_message",          # "off" | "user_message" | "tool_output"
              ask_timeout_s: int = 1800, mismatch_limit: int = 2
```

`observer`는 지금의 `on_observation`, `on_action`에 더해 `on_event(event)`를 받는다. `Toolbox`는 계약 5의 `question`, `answer`, `capture`, `knowledge_*`, `completion` 이벤트를 `on_event`로 보낸다.

### 계약 7. 등록의 재료 (`toolbox.exchanges()`)

```python
{"question": {"kind": "stuck", "text": "...", "candidates": [{"n": 1, "label": "..."}]},
 "answer": {"text": "4번", "pin_on": {"role": "statictext", "name": "라인"} | None},
 "actions": [{"tool": "click_part", "part": "right", "target": {"role": "statictext", "name": "라인"},
              "expect": "window_opened", "expected_met": True, "knowledge_id": None}],
 "failed_knowledge_id": "r-20261113-001" | None}
```

좌표는 넣지 않는다. `actions`는 그 답 뒤부터 다음 질문 전까지의 동작이다.

---

## File Structure

```text
examples/llm-agent/
  agent/
    observe.py         [w1] render_marked 추가
    geometry.py        [w1] 새 파일. 요소의 부분 → 화면 좌표
    table.py           [w1] 새 파일. 행 이름 × 열 이름 → 교차 지점 후보
    effects.py         [w1] 새 파일. 동작 전후 비교, 기대 판정
    capture.py         [w1] 새 파일. 모델용 캡처(자르기, 번호 박스), 이미지 → 화면 좌표
    images.py          [w1] 새 파일. 캡처를 Responses API 항목으로
    policy.py          [w1] 새 파일. 언제 질문을 강제하는가
    tools.py           [w1] 새 도구, 기대와 변화, 정책, 지식 참조
    tool_schemas.py    [w1] 계약 1
    loop.py            [w1] 지침, 이미지 전달, 완료 순서, 등록 호출
    task.py            [w1] 새 필드, success 선택 사항
    recorder.py        [w1] 계약 5의 이벤트
    ask.py             [w2] 새 파일. 계약 3
    knowledge.py       [w3] 새 파일. 계약 4
    completion.py      [w3] 새 파일. 확인 방법 실행
    registration.py    [w3] 새 파일. 초안, 승인, 저장
  run.py               [w1] 묻는 쪽, 지식, 등록 연결
  requirements.txt     [w1] pillow
  view.py              [w2]
  viewer/              [w2] 서버 경로, RunManager, 채팅, 질문 박스, 지식 목록, 내보내기
  virtual-mes/         [w3] 선택 칸 여는 방식, 묶음 헤더 화면
  knowledge.example/   [w3] 예시 항목 두 개
  .gitignore           [w3] knowledge/
  tests/               작업자마다 자기 모듈의 테스트 파일. fake_cli.py는 [w1]
  README.md            [오케스트레이터] 통합 때
```

## 순서와 의존

| 단계 | w1 (관찰·도구·루프) | w2 (묻기·뷰어) | w3 (가상 앱·지식·완료) |
| --- | --- | --- | --- |
| A (동시) | W1-1 ~ W1-7 | W2-1 ~ W2-4 | W3-1 ~ W3-4 |
| B | W1-8 ~ W1-10 (W2-1, W3-3, W3-4 수락 뒤) | W2-5 (W3-3 수락 뒤), W2-6 (W1-9, W3-5 수락 뒤) | W3-5 (W1-8 수락 뒤) |
| C | | | W3-6 (전부 수락 뒤, 데스크톱 잠금) |
| 통합 | 오케스트레이터: O-1 ~ O-3 | | |

A 단계에서는 다른 작업자가 기다리는 것을 먼저 하고 따로 제출한다. w2는 W2-1을, w3는 W3-3과 W3-4를 먼저 한다.

모든 명령은 `examples/llm-agent`에서 conda 환경 `AgentDesktop`으로 돌린다.

---

## w1: 관찰, 도구, 루프

### Task W1-1: 표시 번호

**Files:** Modify `agent/observe.py` · Test `tests/test_observe.py`

**Interfaces:**
- Produces: `render_marked(tree, include_bounds=False) -> tuple[str, dict, dict]`. 글, ref 지도(지금과 같음), 번호 지도 `{n: element}`. `element`는 `visible_elements()`의 항목에 `"mark": n`을 더한 것(`bounds` 포함). 번호는 트리 순서로 1부터.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_marks_number_controls_and_text_in_tree_order():
    tree = {"role": "window", "name": "W", "children": [
        {"role": "statictext", "name": "라인", "bounds": B},
        {"role": "button", "name": "조회", "ref_id": "@s1:e1", "bounds": B},
        {"role": "group", "children": [{"role": "statictext", "name": "소계", "bounds": B}]}]}
    text, refs, marks = render_marked(tree)
    assert [marks[n]["name"] for n in sorted(marks)] == ["라인", "조회", "소계"]
    assert marks[1]["kind"] == "text" and marks[2]["ref"] == "@s1:e1"
    assert text.splitlines()[0] == 'window "W"'              # 창에는 번호가 없다
    assert '[2] button "조회" @s1:e1' in text
    assert "@s1:e1" in refs

def test_render_tree_output_is_unchanged():                  # 기존 함수는 번호를 붙이지 않는다
    assert "[1]" not in render_tree(TREE)[0]
```

- [ ] **Step 2:** `python -m pytest tests/test_observe.py -q` → 새 테스트가 `ImportError`로 실패
- [ ] **Step 3:** `render_marked`를 구현한다. 번호가 붙는 줄은 `visible_elements`가 고르는 요소와 같은 집합이고, 줄 앞(들여쓰기 뒤)에 `[n] `을 붙인다.
- [ ] **Step 4:** 같은 명령 → 전부 통과

### Task W1-2: 요소의 부분 좌표

**Files:** Create `agent/geometry.py` · Test `tests/test_geometry.py`

**Interfaces:**
- Produces: `PARTS = ("center", "left", "right", "top", "bottom")`, `part_point(bounds, part) -> tuple[int, int]`, `contains(bounds, x, y) -> bool`, `smallest_containing(elements, x, y) -> dict | None`(위치를 포함하는 요소 가운데 넓이가 가장 작은 것).

안쪽 거리는 `inset_x = min(short / 2, width / 4)`, `inset_y = min(short / 2, height / 4)`이고 `short = min(width, height)`다. 가로로 긴 칸의 오른쪽 끝은 높이만 한 정사각형의 가운데가 되고, 이것이 드롭다운 화살표가 흔히 놓이는 자리다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
BOX = {"x": 100, "y": 50, "width": 104, "height": 24}

@pytest.mark.parametrize("part,point", [
    ("center", (152, 62)), ("left", (112, 62)), ("right", (192, 62)),
    ("top", (152, 56)), ("bottom", (152, 68))])
def test_part_points_of_a_wide_box(part, point):
    assert part_point(BOX, part) == point

def test_square_box_uses_a_quarter_inset():
    assert part_point({"x": 0, "y": 0, "width": 20, "height": 20}, "right") == (15, 10)

@pytest.mark.parametrize("bounds", [None, {"x": 0, "y": 0, "width": 0, "height": 10}])
def test_no_area_raises(bounds):
    with pytest.raises(ValueError):
        part_point(bounds, "center")

def test_smallest_containing_prefers_the_inner_element():
    outer = {"mark": 1, "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}}
    inner = {"mark": 2, "bounds": {"x": 10, "y": 10, "width": 20, "height": 20}}
    assert smallest_containing([outer, inner], 15, 15)["mark"] == 2
    assert smallest_containing([outer, inner], 500, 500) is None
```

- [ ] **Step 2:** `python -m pytest tests/test_geometry.py -q` → 실패
- [ ] **Step 3:** 구현한다. 모르는 `part`도 `ValueError`.
- [ ] **Step 4:** 통과

### Task W1-3: 표의 셀 찾기

**Files:** Create `agent/table.py` · Test `tests/test_table.py`

**Interfaces:**
- Consumes: `render_marked`의 번호 지도 값 목록(`mark`, `name`, `value`, `bounds`).
- Produces: `find_cells(elements, rows, columns) -> dict`

```python
{"candidates": [{"point": [x, y], "mark": 57 | None, "value": "12,480" | None, "bounds": {...} | None,
                 "row_context": ["극판", "소계"], "row_marks": [31, 32], "column_marks": [4, 12]}],
 "reason": None | "no_text_elements" | "row_label_not_found: <이름>" | "column_label_not_found: <이름>"
           | "labels_not_aligned"}
```

규칙(글자 일치와 기하 계산만 한다):

1. 이름 일치는 요소의 `name`(없으면 `value`)에서 앞뒤 공백을 떼고 연속 공백을 하나로 줄인 뒤의 완전 일치다.
2. 열: 열 이름마다 요소 하나씩 골라 가로 범위가 서로 겹쳐야 한다(겹침이 좁은 쪽 너비의 절반 이상). 열 띠는 고른 요소 가운데 가장 좁은 것의 가로 범위다. 한 이름에 일치한 요소들이 가로로 겹치면 가장 좁은 것만 남긴다(Review Focus 5).
3. 행: 행 이름마다 서로 다른 요소 하나씩 골라 세로 범위가 서로 겹쳐야 한다(겹침이 낮은 쪽 높이의 절반 이상). 행 띠는 고른 요소 가운데 가장 낮은 것의 세로 범위다. 병합되어 여러 행에 걸친 요소는 그 행들 모두와 겹친다.
4. 열 이름 요소는 행 띠보다 위에 있어야 한다(아래 끝 ≤ 행 띠의 위 끝).
5. 후보의 `point`는 열 띠의 가로 가운데와 행 띠의 세로 가운데다. 그 지점을 포함하는 요소 가운데 이름 요소가 아닌 가장 작은 것이 `mark`, `value`, `bounds`다.
6. `row_context`는 그 행 띠에 걸친 글자 요소의 이름을 왼쪽부터, `point`의 왼쪽에 있는 것만, 6개까지다.
7. 후보는 위에서 아래, 왼쪽에서 오른쪽 순으로 20개까지다.

- [ ] **Step 1: 테스트 표를 만든다.** 사용자가 준 화면 예시를 쓴다. 칸은 80×24, 헤더 두 줄.
  - 1행 헤더: `SHOP`(1칸, 두 줄 높이) · `공정`(2칸) · `계획`(3칸) · `생산`(4칸) · `불량`(6칸)
  - 2행 헤더: `그룹공정` `공정` · `계획` `실적` `달성률` · `작업수` `양품수` `양품율` `수율(Loss제외)` · `불량수` `불량율(PPM)` `LOSS수` `LOSS율(PPM)` `이탈수` `이탈율(PPM)`
  - 행: `믹싱/바인더/소계`, `믹싱/슬러리 믹싱/소계`, `극판/소계/(빈 칸)`, `극판/코팅/소계`, `극판/압연/소계`. 값 칸은 `"0"`이고 극판·소계의 양품수만 `"12,480"`.

- [ ] **Step 2: 실패하는 테스트를 쓴다**

```python
def test_unique_row_and_grouped_column_give_one_cell():
    found = find_cells(TABLE, ["믹싱", "바인더"], ["생산", "양품수"])
    assert found["reason"] is None and len(found["candidates"]) == 1
    cell = found["candidates"][0]
    assert cell["value"] == "0" and cell["mark"] is not None
    assert contains(cell["bounds"], *cell["point"])

def test_shop_and_subtotal_match_every_row_that_has_both():
    found = find_cells(TABLE, ["극판", "소계"], ["양품수"])
    assert [c["row_context"][:3] for c in found["candidates"]] == [
        ["극판", "소계"], ["극판", "코팅", "소계"], ["극판", "압연", "소계"]]
    assert found["candidates"][0]["value"] == "12,480"

def test_label_on_both_header_levels_is_one_column():          # Review Focus 5
    found = find_cells(TABLE, ["믹싱", "바인더"], ["계획"])
    assert len(found["candidates"]) == 1
    assert found["candidates"][0]["point"][0] == center_x_of_lower("계획")

def test_merged_shop_cell_spanning_rows_matches_each_row():
    assert len(find_cells(MERGED_TABLE, ["극판"], ["양품수"])["candidates"]) == 3

@pytest.mark.parametrize("rows,columns,reason", [
    (["없는행"], ["양품수"], "row_label_not_found: 없는행"),
    (["극판"], ["없는열"], "column_label_not_found: 없는열"),
    (["믹싱", "코팅"], ["양품수"], "labels_not_aligned")])
def test_no_candidate_says_why(rows, columns, reason):
    assert find_cells(TABLE, rows, columns) == {"candidates": [], "reason": reason}

def test_table_without_text_elements():
    assert find_cells([], ["극판"], ["양품수"])["reason"] == "no_text_elements"
```

- [ ] **Step 3:** `python -m pytest tests/test_table.py -q` → 실패
- [ ] **Step 4:** `find_cells`를 구현한다.
- [ ] **Step 5:** 통과

### Task W1-4: 변화 비교와 기대 판정

**Files:** Create `agent/effects.py` · Test `tests/test_effects.py`

**Interfaces:**
- Produces: `EXPECTATIONS = ("window_opened", "window_closed", "content_changed", "value_changed", "any")`, `state(windows, marks) -> dict`, `diff(before, after) -> dict`(계약 2의 `effects`), `met(expect, effects) -> bool`.

요소의 동일성은 `(kind, role, name, description)`과 같은 키 안에서의 순번이다. 같은 요소의 `value`나 `states`가 다르면 `changed`다. `states`의 `focused`는 비교에서 빼고 `focus`로만 보고한다. 판정은 다음과 같다.

| `expect` | 만족 조건 |
| --- | --- |
| `window_opened` | `windows_opened`가 비어 있지 않음 |
| `window_closed` | `windows_closed`가 비어 있지 않음 |
| `content_changed` | `added.count > 0` 또는 `removed.count > 0` |
| `value_changed` | `changed`가 비어 있지 않음, 또는 `content_changed` 조건(글자로만 보이는 표는 값이 이름이므로) |
| `any` | `none`이 false |

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_new_window_is_reported_and_meets_window_opened():
    effects = diff(state([W1], MARKS), state([W1, W2], MARKS))
    assert effects["windows_opened"] == [{"id": "w-2", "title": "목록"}]
    assert met("window_opened", effects) and not met("window_closed", effects)

def test_caret_only_click_changes_nothing_but_focus():
    after = with_state(MARKS, mark=3, add="focused")
    effects = diff(state([W1], MARKS), state([W1], after))
    assert effects["focus"] == {"mark": 3, "name": "라인"}
    assert effects["added"]["count"] == 0 and effects["changed"] == []
    assert not met("window_opened", effects) and not met("content_changed", effects)
    assert met("any", effects)

def test_value_change_names_before_and_after():
    effects = diff(state([W1], MARKS), state([W1], with_value(MARKS, mark=5, value="전주")))
    assert effects["changed"] == [
        {"mark": 5, "name": "기간", "field": "value", "before": "전일", "after": "전주"}]

def test_text_cells_that_change_meet_value_changed():
    effects = diff(state([W1], cells("0", "0")), state([W1], cells("0", "12,480")))
    assert effects["added"]["count"] == 1 and effects["removed"]["count"] == 1
    assert met("value_changed", effects)

def test_identical_states_are_none():
    effects = diff(state([W1], MARKS), state([W1], MARKS))
    assert effects["none"] is True and not met("any", effects)

def test_names_are_capped_at_eight():
    assert len(diff(state([W1], {}), state([W1], many(30)))["added"]["names"]) == 8
```

- [ ] **Step 2:** `python -m pytest tests/test_effects.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과

### Task W1-5: 모델용 캡처와 이미지 전달

**Files:** Create `agent/capture.py`, `agent/images.py` · Modify `requirements.txt`(`pillow>=11` 추가) · Test `tests/test_capture.py`, `tests/test_images.py`

**Interfaces:**
- Consumes: `agent.frames.frame_origin`, `DesktopClient.run("screenshot", "--window-id", id, path)`.
- Produces:

```python
@dataclass(frozen=True)
class Capture:
    id: str; path: Path; width: int; height: int
    origin: tuple            # 이미지 (0, 0) 픽셀의 화면 좌표
    window_id: str; window_bounds: dict; drawn: bool; cropped: bool

def take(desktop, window, marks, out_dir, capture_id, around=None) -> Capture | None   # 스크린샷 실패면 None
def to_screen(capture, x, y) -> tuple[int, int] | None                                 # 이미지 밖이면 None
def image_items(call_id, output: dict, captures: list, mode: str) -> list[dict]
```

- `around`가 요소의 `bounds`면 그 둘레 120픽셀까지 자른다. 번호 박스는 이미지 안의 요소에 그리되 150개를 넘으면 그리지 않고 `drawn=False`다.
- Pillow를 불러오지 못하면 자르지도 그리지도 않고 창 전체를 그대로 준다(`drawn=False`, `cropped=False`).
- `image_items`: `"off"`면 이미지를 버리고 결과에 `"image": "unavailable"`을 넣는다. `"user_message"`면 `function_call_output` 뒤에 `{"role": "user", "content": [input_text, input_image]}`를 붙인다. `"tool_output"`이면 `function_call_output`의 `output`을 `[input_text(JSON 글), input_image]` 배열로 만든다. 이미지는 `data:image/png;base64,` URL이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다.** 가짜 desktop은 Pillow로 400×300 단색 PNG를 쓰고 그 크기를 돌려준다. 창 `bounds`는 `{"x": 1000, "y": 500, "width": 416, "height": 308}`(테두리 8)이다.

```python
def test_whole_window_capture_maps_image_to_screen():
    shot = take(desktop, WINDOW, MARKS, tmp_path, "c1")
    assert (shot.width, shot.height) == (400, 300) and shot.origin == (1008, 500)
    assert to_screen(shot, 10, 20) == (1018, 520)
    assert to_screen(shot, 400, 0) is None                      # Review Focus 2: 캡처 밖

def test_crop_around_a_mark_keeps_the_mapping():
    target = {"x": 1208, "y": 650, "width": 100, "height": 24}
    shot = take(desktop, WINDOW, MARKS, tmp_path, "c2", around=target)
    assert shot.cropped and shot.origin == (1088, 530)
    assert to_screen(shot, 120, 120) == (1208, 650)

def test_mark_boxes_are_drawn():
    shot = take(desktop, WINDOW, {1: MARK_AT_1108_600}, tmp_path, "c3")
    assert shot.drawn and pixel(shot.path, 100, 100) != BACKGROUND

def test_too_many_marks_are_not_drawn():
    assert take(desktop, WINDOW, many_marks(151), tmp_path, "c4").drawn is False

def test_without_pillow_the_raw_window_is_returned(monkeypatch):
    monkeypatch.setattr(capture, "Image", None)
    shot = take(desktop, WINDOW, MARKS, tmp_path, "c5", around=TARGET)
    assert shot is not None and not shot.drawn and not shot.cropped

def test_failed_screenshot_gives_none():
    assert take(failing_desktop, WINDOW, MARKS, tmp_path, "c6") is None
```

```python
def test_user_message_mode_appends_an_image_message():
    items = image_items("call_1", {"ok": True}, [SHOT], "user_message")
    assert items[0]["type"] == "function_call_output" and json.loads(items[0]["output"]) == {"ok": True}
    assert items[1]["role"] == "user"
    assert items[1]["content"][1]["image_url"].startswith("data:image/png;base64,")

def test_tool_output_mode_puts_the_image_in_the_output():
    items = image_items("call_1", {"ok": True}, [SHOT], "tool_output")
    assert len(items) == 1 and [part["type"] for part in items[0]["output"]] == ["input_text", "input_image"]

def test_off_mode_sends_no_image():
    items = image_items("call_1", {"ok": True}, [SHOT], "off")
    assert len(items) == 1 and json.loads(items[0]["output"])["image"] == "unavailable"
```

- [ ] **Step 2:** `pip install "pillow>=11"` 뒤 `python -m pytest tests/test_capture.py tests/test_images.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과

### Task W1-6: 질문을 강제하는 정책

**Files:** Create `agent/policy.py` · Test `tests/test_policy.py`

**Interfaces:**
- Produces:

```python
TOLERANCE_PX = 12

class AskPolicy:
    def __init__(self, mismatch_limit=2): ...
    def refusal(self, raw_point: bool, point: tuple | None, cites_knowledge: bool) -> str | None   # refused_code
    def note_ambiguous(self, rects: list[dict]) -> None        # 가장 최근 find_cells의 후보 영역. 2개 미만이면 비움
    def note_answer(self, rects: list[dict], points: list[tuple]) -> None
    def note_result(self, expected_met: bool, cited_knowledge: bool) -> None
    blocked: bool
```

`refusal`의 순서: ① `blocked`면 `"blocked_until_question"`(지식을 밝혀도). ② `raw_point`도 아니고 `point`가 모호한 영역 안도 아니면 통과. ③ `cites_knowledge`면 통과. ④ `point`가 답으로 풀린 영역 안이거나 풀린 지점에서 `TOLERANCE_PX` 안이면 통과하고 그 허가를 지운다. ⑤ `"needs_confirmation"`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_element_action_passes_without_a_question():
    assert AskPolicy().refusal(False, (10, 10), False) is None

def test_raw_point_needs_confirmation_until_answered():
    policy = AskPolicy()
    assert policy.refusal(True, (500, 300), False) == "needs_confirmation"
    policy.note_answer([], [(505, 302)])
    assert policy.refusal(True, (500, 300), False) is None
    assert policy.refusal(True, (500, 300), False) == "needs_confirmation"     # 허가는 한 번

def test_answer_for_another_place_does_not_clear():
    policy = AskPolicy()
    policy.note_answer([], [(900, 900)])
    assert policy.refusal(True, (500, 300), False) == "needs_confirmation"

def test_ambiguous_cells_need_confirmation_even_by_ref():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A, CELL_B])
    assert policy.refusal(False, center(CELL_A), False) == "needs_confirmation"
    assert policy.refusal(False, (5, 5), False) is None
    policy.note_answer([CELL_A, CELL_B], [])
    assert policy.refusal(False, center(CELL_B), False) is None

def test_single_candidate_is_not_ambiguous():
    policy = AskPolicy()
    policy.note_ambiguous([CELL_A])
    assert policy.refusal(False, center(CELL_A), False) is None

def test_cited_knowledge_passes_confirmation():
    assert AskPolicy().refusal(True, (500, 300), True) is None

def test_two_mismatches_in_a_row_block_everything_until_a_question():
    policy = AskPolicy()
    policy.note_result(False, False)
    assert not policy.blocked
    policy.note_result(False, False)
    assert policy.refusal(False, (10, 10), True) == "blocked_until_question"
    policy.note_answer([], [])
    assert policy.refusal(False, (10, 10), False) is None

def test_a_met_expectation_resets_the_streak():
    policy = AskPolicy()
    policy.note_result(False, False); policy.note_result(True, False); policy.note_result(False, False)
    assert not policy.blocked

def test_mismatch_after_cited_knowledge_blocks_at_once():
    policy = AskPolicy()
    policy.note_result(False, True)
    assert policy.blocked
```

- [ ] **Step 2:** `python -m pytest tests/test_policy.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과

### Task W1-7: 도구 묶기 1 — 번호, 부분 클릭, 셀 찾기, 캡처, 기대와 변화

**Files:** Modify `agent/tools.py`, `agent/tool_schemas.py`, `agent/recorder.py`, `tests/fake_cli.py` · Test `tests/test_tools.py`, `tests/test_recorder.py`

**Interfaces:**
- Consumes: W1-1 ~ W1-5.
- Produces: 계약 1의 `look`, `find_cells`, `click_part`, `click_point` 스키마와 동작, 계약 2의 `expect`/`effects`/`expected_met`, `toolbox.take_attachments()`, `observer.on_event`로 가는 `capture` 이벤트, `action` 이벤트의 새 필드.

정해 둔 것:

- 관찰 글은 `render_marked`로 만든다. 번호 지도는 모델이 마지막으로 받은 관찰의 것이다(`root_ref`로 일부만 읽으면 그 부분의 번호로 바뀐다).
- `click_part`는 `part_point`로 좌표를 구해 `mouse-click --xy=<x>,<y> --button=<left|right> --count=<1|2>`를 `--headed`로 보낸다. `_physical`과 같은 창 소유·포커스 확인을 거친다. 요소의 `states`에 `offscreen`이 있거나 `bounds`의 넓이가 0이면 누르지 않고 거절한다(Review Focus 4).
- `click_point`는 `to_screen`으로 바꾼다. 캡처 id를 모르거나, 그 창의 지금 `bounds`가 캡처 때와 다르면 `refused_code: "stale_capture"`로 거절한다(Review Focus 2).
- 동작 도구는 동작 전에 창 목록을 읽어 두고, 다시 관찰한 뒤 `effects.diff`로 비교한다. `expect`가 있으면 `expected_met`을 넣고, 다르면 이미지 입력이 켜져 있을 때 창 전체를 캡처해 붙인다.
- `look`은 캡처 id, 크기, `drawn`, `cropped`를 돌려주고 이미지를 붙인다. `image_mode`가 `"off"`면 `{"ok": False, "refused": ...}`로 번호와 `find_cells`를 쓰라고 알린다.
- `NON_IDENTITY_ARGUMENTS`에 `expect`, `knowledge_id`를 더한다.
- `preview`는 `click_part`에 요소의 역할·이름·`bounds`와 누를 지점(`points`)을, `click_point`에 창과 화면 지점을 돌려준다. 동작 전 승인 화면이 대상을 그릴 수 있어야 한다.
- `recorder.on_event`는 `capture` 이벤트를 그대로 적고, `on_action`은 `RESULT_FIELDS`에 `expect`, `effects`, `expected_met`, `knowledge_id`를 더한다.

- [ ] **Step 1: `tests/fake_cli.py`에 장면을 더한다.** `FakeCli(scene=...)`가 스냅샷 트리와 창 목록을 직접 받게 하고(창 `bounds`는 W1-5의 테스트와 같은 값, 번호 3의 요소는 W1-2의 `BOX`), `mouse-click`의 좌표에 따라 다음 스냅샷을 바꾸는 `on_click(x, y)` 훅을 둔다. 스크린샷은 Pillow로 실제 크기의 PNG를 쓴다. 기존 생성자 인자와 동작은 그대로 둔다.

- [ ] **Step 2: 실패하는 테스트를 쓴다** (`tests/test_tools.py`)

```python
def test_observation_lines_carry_marks():
    assert re.search(r"\[\d+\] statictext \"라인\"", observe(toolbox)["observation"])

def test_click_part_right_end_clicks_inside_the_box_and_reports_the_new_window():
    cli.on_click = lambda x, y: cli.open_window(LIST_WINDOW) if x > 180 else cli.focus(mark=3)
    result = toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened"))
    assert "--xy=192,62" in cli.full_call("mouse-click")
    assert result["expected_met"] is True and result["effects"]["windows_opened"][0]["title"] == "목록"

def test_center_click_that_only_moves_focus_is_a_mismatch_with_a_capture():
    result = toolbox.call("click_part", part_call(mark=3, part="center", expect="window_opened"))
    assert result["expected_met"] is False and result["effects"]["focus"]["mark"] == 3
    assert [shot.id for shot in toolbox.take_attachments()] == [result["capture"]]

def test_double_click_and_right_click_use_count_and_button():
    toolbox.call("click_part", part_call(mark=3, part="center", action="double_click"))
    assert "--count=2" in cli.full_call("mouse-click")

@pytest.mark.parametrize("scene", [OFFSCREEN_MARK, ZERO_SIZE_MARK])      # Review Focus 4
def test_click_part_refuses_what_cannot_be_clicked(scene):
    assert "refused" in toolbox_for(scene).call("click_part", part_call(mark=1, part="center"))
    assert "mouse-click" not in cli.command_names()

def test_find_cells_reads_the_latest_observation():
    found = toolbox.call("find_cells", {"rows": ["믹싱", "바인더"], "columns": ["양품수"]})
    assert found["ok"] and len(found["candidates"]) == 1

def test_look_returns_a_capture_id_and_attaches_the_image():
    result = toolbox.call("look", {"mark": None})
    assert result["capture"] == "c1" and len(toolbox.take_attachments()) == 1
    assert toolbox.take_attachments() == []

def test_click_point_maps_image_pixels_to_the_screen():
    toolbox.call("look", {"mark": None})
    toolbox.call("click_point", point_call(capture="c1", x=10, y=20))
    assert "--xy=1018,520" in cli.full_call("mouse-click")

def test_click_point_after_the_window_moved_is_stale():                   # Review Focus 2
    toolbox.call("look", {"mark": None})
    cli.window_shift = 100
    assert toolbox.call("click_point", point_call(capture="c1", x=10, y=20))["refused_code"] == "stale_capture"

def test_look_with_images_off_is_refused_with_an_alternative():
    assert "find_cells" in toolbox_for(SCENE, image_mode="off").call("look", {"mark": None})["refused"]

def test_legacy_call_without_expect_has_no_expectation_fields():
    assert "expected_met" not in toolbox.call("check", {"ref": REF})

def test_preview_of_click_part_gives_the_element_and_the_point():
    target = toolbox.preview("click_part", part_call(mark=3, part="right"))
    assert target["name"] == "라인" and target["points"] == [[192, 62]]
```

`tests/test_recorder.py`에는 `capture` 이벤트가 그대로 적히는지, `action` 이벤트에 `expect`와 `expected_met`이 들어가는지를 더한다.

- [ ] **Step 3:** `python -m pytest tests/test_tools.py tests/test_recorder.py -q` → 새 테스트 실패
- [ ] **Step 4:** 구현한다.
- [ ] **Step 5:** `python -m pytest -q` → 기존 테스트 포함 전부 통과

### Task W1-8: 도구 묶기 2 — 묻기, 정책, 지식 참조, 완료 확인

**Files:** Modify `agent/tools.py`, `agent/tool_schemas.py`, `agent/recorder.py` · Test `tests/test_tools_ask.py`, `tests/test_recorder.py`

**의존:** W2-1(`agent.ask`), W3-3(`agent.knowledge`), W3-4(`agent.completion`) 수락 뒤 시작. 수락 전에는 테스트용 가짜(`ask(question)`을 가진 객체, `KnowledgeStore`와 같은 메서드를 가진 객체)로 Step 1~2를 할 수 있다.

**Interfaces:**
- Consumes: 계약 3의 `Answer`, `NoAsker`; 계약 4의 `KnowledgeStore`, `briefing`; `agent.completion.run_checks`(W3-4); `AskPolicy`.
- Produces: 계약 1의 `ask` 도구, 계약 2의 거절, 계약 6의 `can_ask`, `ask`, `briefing`, `exchanges`, `check_completion`, 계약 5의 `question`/`answer`/`knowledge_*`/`completion` 이벤트(recorder가 이미지 좌표를 더함).

정해 둔 것:

- `ask` 도구는 후보의 `mark`를 번호 지도에서 찾아 `bounds`를 채우고, `capture`+`x`+`y`는 `to_screen`으로 `point`를 채운다. 모르는 번호나 캡처는 거절한다. 질문 직전에 현재 창을 다시 관찰해(`source="question"`) 뷰어가 그 화면 위에 박스를 그리게 한다.
- 답을 받으면 `policy.note_answer(후보의 bounds들, 후보의 point들 + 찍은 위치)`를 부른다. 찍은 위치가 있으면 `smallest_containing`으로 요소를 찾아 결과에 `"pin": {"x", "y", "mark"}`를 넣는다.
- 답할 사람이 없으면(`can_ask`가 false) `refused_code: "no_one_to_ask"`. 시간 초과면 `AgentStopped`.
- 동작 도구는 실행 전에 `policy.refusal`을 묻는다. `point`는 좌표 도구면 그 좌표, ref 도구와 `click_part`면 누를 지점(요소 `bounds`의 가운데 또는 부분 좌표), `bounds`를 모르면 None이다. `raw_point`는 `mouse_click`, `click_point`, `drag`일 때 true다.
- `knowledge_id`가 null이 아니면 `knowledge.get`으로 확인한다. 없거나 `disabled`면 `unknown_knowledge`로 거절한다. 통과하면 `count_use`를 부르고 `knowledge_used` 이벤트를 보낸다. 그 동작의 `expected_met`이 false면 `count_failure`, `knowledge_failed` 이벤트, 그리고 진행 중인 exchange의 `failed_knowledge_id`를 적는다.
- `find_cells`가 후보를 돌려줄 때마다 `policy.note_ambiguous(후보의 bounds들)`를 부른다. `bounds`가 없는 후보는 `point` 둘레 `TOLERANCE_PX`의 정사각형으로 친다.
- 정책이 동작을 거절할 때(`needs_confirmation`, `blocked_until_question`) 이미지 입력이 켜져 있으면 창 전체 캡처를 붙인다(`reason: "question"`). 모델이 그림을 보고 질문을 만들게 하려는 것이다.
- `precheck(name, arguments)`는 같은 판정을 실행 없이 돌려준다. 허가를 쓰지 않고 횟수도 세지 않는다.
- `briefing()`은 `knowledge.briefing(knowledge.relevant(store, task.goal))`의 글이다.
- `check_completion(completion_id, saved_path)`는 그 id의 사용 중인 완료 조건을 `run_checks`로 실행하고 `completion` 이벤트를 보낸다. 모르는 id면 `{"met": False, "detail": "unknown completion id"}`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_ask_passes_candidates_with_bounds_and_returns_the_answer():
    asker = ScriptedAsker([Answer(text="2번")])
    result = toolbox_with(asker).call("ask", stuck_question(marks=[3, 4]))
    assert result == {"ok": True, "answer": "2번", "pin": None}
    assert [c["n"] for c in asker.questions[0]["candidates"]] == [1, 2]
    assert asker.questions[0]["candidates"][0]["bounds"] == BOX

def test_pin_inside_an_element_names_its_mark():
    asker = ScriptedAsker([Answer(text="여기", pin=(192, 62))])
    assert toolbox_with(asker).call("ask", stuck_question(marks=[3]))["pin"] == {"x": 192, "y": 62, "mark": 3}

def test_unanswered_question_stops_the_run():
    with pytest.raises(AgentStopped):
        toolbox_with(ScriptedAsker([NO_ANSWER])).call("ask", stuck_question(marks=[3]))

def test_no_one_to_ask_is_refused():
    assert toolbox_with(NoAsker()).call("ask", stuck_question(marks=[3]))["refused_code"] == "no_one_to_ask"

def test_point_click_is_refused_until_confirmed():
    toolbox = toolbox_with(ScriptedAsker([Answer(text="맞아")]))
    toolbox.call("look", {"mark": None})
    click = point_call(capture="c1", x=10, y=20)
    assert toolbox.call("click_point", click)["refused_code"] == "needs_confirmation"
    assert "mouse-click" not in cli.command_names()
    toolbox.call("ask", confirm_question(capture="c1", x=10, y=20))
    assert toolbox.call("click_point", click)["ok"]

def test_two_mismatches_block_until_a_question_is_answered():
    toolbox = toolbox_with(ScriptedAsker([Answer(text="4번")]))
    for _ in range(2):
        toolbox.call("click_part", part_call(mark=3, part="center", expect="window_opened"))
    assert toolbox.call("click_part", part_call(mark=3, part="right"))["refused_code"] == "blocked_until_question"
    assert toolbox.call("observe", OBSERVE)["ok"]                          # 읽기는 된다
    toolbox.call("ask", stuck_question(marks=[3, 4]))
    assert toolbox.call("click_part", part_call(mark=3, part="right", expect="window_opened"))["ok"]

def test_ambiguous_cell_needs_a_confirmation_or_a_knowledge_id():
    toolbox = toolbox_with(asker, knowledge=store_with(RESOLUTION))
    found = toolbox.call("find_cells", {"rows": ["극판", "소계"], "columns": ["양품수"]})
    cell = part_call(mark=found["candidates"][0]["mark"], part="center")
    assert toolbox.call("click_part", cell)["refused_code"] == "needs_confirmation"
    assert toolbox.call("click_part", {**cell, "knowledge_id": RESOLUTION["id"]})["ok"]
    assert store.get(RESOLUTION["id"])["uses"] == 1

@pytest.mark.parametrize("entry_id", ["r-none", DISABLED["id"]])
def test_unknown_or_disabled_knowledge_is_refused(entry_id):
    assert toolbox.call("click_part", {**CELL, "knowledge_id": entry_id})["refused_code"] == "unknown_knowledge"

def test_mismatch_after_knowledge_counts_a_failure_and_blocks():
    toolbox.call("click_part", {**part_call(mark=3, part="center", expect="window_opened"),
                                "knowledge_id": RESOLUTION["id"]})
    assert store.get(RESOLUTION["id"])["failures"] == 1
    assert toolbox.call("click_part", part_call(mark=3, part="right"))["refused_code"] == "blocked_until_question"

def test_exchanges_hold_the_answer_and_the_actions_after_it_without_coordinates():
    exchange = run_stuck_scene(toolbox).exchanges()[0]
    assert exchange["answer"]["text"] == "4번"
    assert exchange["actions"][0]["part"] == "right" and exchange["actions"][0]["expected_met"] is True
    assert "x" not in json.dumps(exchange) and "bounds" not in json.dumps(exchange)

def test_briefing_lists_active_entries_and_logs_their_ids():
    assert RESOLUTION["id"] in toolbox.briefing() and DISABLED["id"] not in toolbox.briefing()
    assert observer.events[-1]["type"] == "knowledge_loaded"

def test_check_completion_runs_the_entry_checks(tmp_path):
    saved = tmp_path / "out.xlsx"; saved.write_bytes(b"x")
    assert toolbox.check_completion(FILE_COMPLETION["id"], str(saved))["met"] is True
    assert toolbox.check_completion("c-none", str(saved))["met"] is False

def test_refusal_by_the_policy_attaches_a_capture_for_the_question():
    toolbox.call("look", {"mark": None}); toolbox.take_attachments()
    toolbox.call("click_point", point_call(capture="c1", x=10, y=20))
    assert len(toolbox.take_attachments()) == 1

def test_precheck_reports_the_refusal_without_running_or_consuming_anything():
    assert toolbox.precheck("click_point", point_call(capture="c1", x=10, y=20))["refused_code"] == "needs_confirmation"
    assert toolbox.precheck("click_part", part_call(mark=3, part="center")) is None
    assert "mouse-click" not in cli.command_names()
```

`tests/test_recorder.py`: `question` 이벤트의 후보에 `rect`가 생기고 `observation`이 직전 관찰 id인지, `answer`의 `pin`에 `image_point`가 생기는지, 그리고 질문·답·`knowledge_used`를 기록한 뒤의 `run.json`에 질문 글, 답 글, 지식 글이 없는지를 더한다.

- [ ] **Step 2:** `python -m pytest tests/test_tools_ask.py tests/test_recorder.py -q` → 실패
- [ ] **Step 3:** 구현한다. `tools.py`가 400줄을 크게 넘으면 묻기와 지식 참조를 `agent/asking.py`로 나눈다.
- [ ] **Step 4:** `python -m pytest -q` → 전부 통과

### Task W1-9: 루프 — 지침, 이미지, 완료 순서, 등록 호출

**Files:** Modify `agent/loop.py`, `agent/task.py`, `agent/recorder.py` · Test `tests/test_loop.py`, `tests/test_task.py`

**Interfaces:**
- Consumes: W1-8, `image_items`.
- Produces: 계약 6의 `run_agent`와 `Task`, 상태 `confirmed`, `recorder`의 `registration` 이벤트 기록과 `run.json`의 `system`.

정해 둔 것:

- 첫 사용자 메시지는 `task.goal` 뒤에 `toolbox.briefing()`을 붙인 것이다.
- 도구 결과는 `image_items(call.call_id, output, toolbox.take_attachments(), task.image_mode)`로 넣는다.
- 동작 전 승인(`gate`)보다 정책을 먼저 묻는다. `toolbox.precheck`가 거절을 돌려주면 승인을 묻지 않고 `toolbox.call`로 넘겨 그 거절을 모델에게 준다. 정책을 통과한 동작은 지금처럼 승인을 거친다.
- 끝낼 때 `toolbox.finish_note`를 채운다(`by`는 사람이 확인했으면 `"human"`, 그 밖에는 `"harness"`).
- 한 번의 모델 응답이 부른 도구가 `ask`뿐이면 그 응답은 `max_steps`에 세지 않는다. 토큰 한도는 지금처럼 센다.
- `task_from_args`의 `max_steps` 기본값을 40으로 올린다(지시만으로 시작한 실행은 관찰, 캡처 보기, 질문이 더해져 12단계로는 모자란다). 작업 파일의 기본값 12는 그대로다.
- `load_task`는 `success`가 없어도 받는다(`require`는 빈 목록). 새 선택 키 `system`, `knowledge_dir`, `image_mode`, `ask_timeout_s`, `mismatch_limit`을 읽고, `image_mode`가 세 값이 아니면 `TaskError`.
- `finish(success=true)`의 순서:
  1. `task.require`가 있으면 지금처럼 `toolbox.verify()` → `verified`.
  2. `completion_id`가 있으면 `toolbox.check_completion` → 만족하면 `by`가 `"harness"`일 때 `verified`, `"human"`일 때 `confirmed`. 만족하지 않으면 모델에게 `{"verified": False, "unmet": detail}`을 돌려주고 계속한다.
  3. `toolbox.can_ask`면 `kind: "completion"` 질문을 한다(글은 "결과가 맞나요?"와 모델의 `evidence`, `saved_path`). `accepted`가 true면 `confirmed`, 아니면 `{"confirmed": False, "comment": 답의 글}`을 돌려주고 계속한다.
  4. 그 밖에는 `claimed`.
- 최종 상태가 `verified`나 `confirmed`이고 `register`가 있으면 `tokens += register(model, toolbox, log)`를 부른 뒤 결과를 돌려준다. `register`가 예외를 내도 실행 결과는 바뀌지 않고 `log`에 경고 이벤트만 남긴다.
- `SYSTEM_PROMPT`에 다음 규칙을 더한다(문구는 작업자가 쓴다): ① 줄 앞의 `[n]`은 그 관찰에서만 유효한 번호다. ② ref가 없는 요소나 요소의 한쪽 끝은 `click_part`로 누른다. ③ 동작마다 `expect`를 정직하게 고르고, 결과의 `effects`와 `expected_met`을 읽는다. ④ 기대와 다르면 같은 동작을 되풀이하지 말고 캡처를 보고 다른 부분(예: 칸의 끝에 있는 화살표)을 시도한다. ⑤ 표의 셀은 `find_cells`로 찾고, 행 이름의 일반적인 해석(예: 상위 항목만 말하면 소계 행)을 스스로 정해 `proposed`로 내놓되, 후보가 둘 이상이면 누르기 전에 `ask(kind="confirm")`로 묻는다. 같은 이름의 대상이 여럿이고 지시가 하나를 가리키지 않을 때도 같다. ⑥ 캡처에서 고른 지점은 `click_point`로 누르며 먼저 확인받는다. ⑦ "지난 실행에서 배운 것"은 사용자의 지난 답이다. 따를 때는 그 id를 `knowledge_id`로 밝히고 묻지 않는다. ⑧ 파일을 저장했으면 `finish`의 `saved_path`에 경로를 적고, 맞는 완료 조건이 있으면 `completion_id`를 적는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_loop.py`)

```python
def test_first_message_carries_the_goal_and_the_briefing():
    run_agent(model, toolbox_with(knowledge=store_with(RESOLUTION)), task)
    assert task.goal in first_user_text(model) and RESOLUTION["resolution"] in first_user_text(model)

def test_capture_reaches_the_model_as_an_image_message():
    run_agent(ScriptedModel([[call("launch_target", {})], [call("look", {"mark": None})], give_up]), toolbox, task)
    assert any(part.get("type") == "input_image" for part in user_parts(model.inputs[-1]))

def test_ask_only_turns_do_not_use_steps():
    turns = [[call("launch_target", {})]] + [[call("ask", STUCK)]] * 5 + [give_up]
    result = run_agent(ScriptedModel(turns), toolbox_with(ScriptedAsker([Answer(text="1번")] * 5)), task_with(max_steps=2))
    assert result.status == "gave_up"

def test_finish_with_a_completion_entry_is_verified_by_the_file(tmp_path):
    result = run_agent(finishing(saved_path=str(SAVED), completion_id=FILE_COMPLETION["id"]), toolbox, free_task)
    assert result.status == "verified"

def test_unmet_completion_goes_back_to_the_model():
    model = finishing_then_giving_up(saved_path=str(tmp_path / "missing.xlsx"), completion_id=FILE_COMPLETION["id"])
    assert run_agent(model, toolbox, free_task).status == "gave_up"
    assert last_output(model.inputs[-1])["verified"] is False

def test_human_confirmation_gives_confirmed():
    toolbox = toolbox_with(ScriptedAsker([Answer(text="", accepted=True)]))
    assert run_agent(finishing(), toolbox, free_task).status == "confirmed"
    assert toolbox.asker.questions[0]["kind"] == "completion"

def test_human_rejection_returns_the_comment_to_the_model():
    toolbox = toolbox_with(ScriptedAsker([Answer(text="파일이 비었어", accepted=False)]))
    run_agent(finishing_then_giving_up(), toolbox, free_task)
    assert last_output(model.inputs[-1]) == {"confirmed": False, "comment": "파일이 비었어"}

def test_no_one_to_ask_stays_claimed():
    assert run_agent(finishing(), toolbox_with(NoAsker()), free_task).status == "claimed"

@pytest.mark.parametrize("ending,called", [("verified", 1), ("confirmed", 1), ("claimed", 0), ("gave_up", 0), ("stopped", 0)])
def test_register_runs_only_after_a_checked_success(ending, called):
    calls = []
    run_to(ending, register=lambda model, toolbox, log: calls.append(1) or 50)
    assert len(calls) == called

def test_register_tokens_are_added_and_its_failure_does_not_change_the_result():
    assert run_to("verified", register=lambda *_: 50).tokens == BASE_TOKENS + 50
    assert run_to("verified", register=raising).status == "verified"

def test_timed_out_completion_question_stops_the_run():
    assert run_agent(finishing(), toolbox_with(ScriptedAsker([NO_ANSWER])), free_task).status == "stopped"

def test_policy_refusal_comes_before_the_approval_prompt():
    gate = RecordingGate(approve=True)
    run_agent(point_click_then_give_up, toolbox, task, gate=gate)
    assert gate.proposals == []                                  # 거절될 동작은 승인을 묻지 않는다

def test_confirmed_action_still_goes_through_approval():
    gate = RecordingGate(approve=True)
    run_agent(confirm_then_point_click, toolbox_with(ScriptedAsker([Answer(text="맞아")])), task, gate=gate)
    assert [p["tool"] for p in gate.proposals] == ["click_point"]

def test_human_confirmation_leaves_a_finish_note(tmp_path):
    toolbox = toolbox_with(ScriptedAsker([Answer(accepted=True)]))
    run_agent(finishing(saved_path=str(tmp_path / "a.xlsx")), toolbox, free_task)
    assert toolbox.finish_note["by"] == "human" and toolbox.finish_note["saved_path"].endswith("a.xlsx")
```

`tests/test_task.py`: `success` 없는 작업 파일이 읽히는지, 새 키가 읽히는지, 틀린 `image_mode`가 `TaskError`인지, `task_from_args(...).max_steps == 40`인지.

- [ ] **Step 2:** `python -m pytest tests/test_loop.py tests/test_task.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** `python -m pytest -q` → 전부 통과

### Task W1-10: `run.py` 연결

**Files:** Modify `run.py` · Test `tests/test_run.py`

**의존:** W3-5(`agent.registration`) 수락 뒤.

**Interfaces:**
- Consumes: `TerminalAsker`, `NoAsker`, `KnowledgeStore`, `system_name`, `agent.registration.register_after_run`(W3-5).
- Produces: `--system <이름>`, `--knowledge-dir <경로>`, `EXIT_CODES`에 `"confirmed": 0`.

`is_interactive()`면 `TerminalAsker`, 아니면 `NoAsker`다. `--baseline`에서는 묻기, 지식, 등록을 연결하지 않는다. 캡처 폴더는 `recorder.path / "captures"`다. `meta`에 `system`을 넣는다. 지식 폴더는 `--knowledge-dir`, 작업 파일의 `knowledge_dir`, `AGENT_KNOWLEDGE_DIR`, `HERE / "knowledge"` 순이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_confirmed_exits_zero(run_with):
    assert run_with(status="confirmed") == 0

def test_interactive_runs_ask_on_the_terminal_and_others_do_not(patched):
    assert isinstance(asker_of(main_with(interactive=True)), TerminalAsker)
    assert isinstance(asker_of(main_with(interactive=False)), NoAsker)

def test_system_defaults_to_the_exe_name_and_can_be_overridden(tmp_path):
    assert store_of(main_with("--exe", r"C:\apps\VirtualMes.exe", "--goal", "x")).system == "virtualmes"
    assert store_of(main_with("--exe", r"C:\apps\VirtualMes.exe", "--goal", "x", "--system", "mes")).system == "mes"

def test_knowledge_dir_argument_wins(tmp_path):
    assert store_root(main_with("--knowledge-dir", str(tmp_path))) == tmp_path

def test_baseline_wires_no_asker_or_register(patched):
    assert register_of(main_with("--baseline")) is None
```

- [ ] **Step 2:** `python -m pytest tests/test_run.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** `python -m pytest -q` → 전부 통과

---

## w2: 묻기, 뷰어

### Task W2-1: 묻는 쪽 (`agent/ask.py`)

**Files:** Create `agent/ask.py` · Test `tests/test_ask.py`

**Interfaces:**
- Produces: 계약 3 전부.

`TerminalAsker`의 동작: 질문 글과 후보를 `n. label`로 보여 준다. `confirm`·`stuck`은 한 줄을 받아 `Answer(text=...)`. `completion`은 `[y/N, 뒤에 의견]`을 받아 `accepted`와 `text`를 채운다. `registration`은 항목마다 내용을 보여 주고 `y`는 승인, `n`이나 빈 줄은 거절, 그 밖의 글은 그 글로 `resolution`(완료 조건이면 `task`)을 고쳐 승인한다. 입력이 끊기면(`EOFError`) `NO_ANSWER`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_answer_from_another_thread_releases_the_question():
    gate = AskGate()
    threading.Timer(0.05, gate.answer, ("q1", Answer(text="2번"))).start()
    assert gate.ask({"id": "q1", "kind": "stuck", "text": "?"}).text == "2번"

def test_unanswered_question_times_out_as_no_answer():
    clock = FakeClock()
    assert AskGate(timeout_s=30, clock=clock.advancing(31)).ask({"id": "q1", "kind": "stuck", "text": "?"}) is NO_ANSWER

def test_second_answer_and_unknown_id_are_rejected():                       # Review Focus 1
    gate = answered_gate("q1")
    assert gate.answer("q1", Answer(text="또")) is False
    assert gate.answer("q9", Answer(text="?")) is False

def test_terminal_lists_candidates_and_returns_the_typed_line():
    shown = []
    asker = TerminalAsker(ask=lambda _prompt: "2번", show=shown.append)
    assert asker.ask(STUCK_WITH_TWO).text == "2번" and "2. 화살표" in "\n".join(shown)

@pytest.mark.parametrize("typed,accepted,text", [("y", True, ""), ("n 파일이 비었어", False, "파일이 비었어"), ("", False, "")])
def test_terminal_completion_answer(typed, accepted, text): ...

def test_terminal_registration_approves_edits_and_rejects():
    asker = TerminalAsker(ask=scripted("y", "오른쪽 끝의 화살표로 연다", "n"), show=lambda _line: None)
    assert [item["decision"] for item in asker.ask(REGISTRATION_OF_THREE).items] == ["approve", "edit", "reject"]

def test_terminal_eof_is_no_answer(): ...
def test_no_asker_never_answers():
    assert NoAsker().ask({"id": "q1"}) is NO_ANSWER and NoAsker.interactive is False
```

- [ ] **Step 2:** `python -m pytest tests/test_ask.py -q` → 실패
- [ ] **Step 3:** 구현한다. `agent/approval.py`는 고치지 않는다.
- [ ] **Step 4:** 통과

### Task W2-2: 답을 받는 서버 경로

**Files:** Modify `viewer/server.py`, `viewer/run_manager.py` · Test `tests/test_server.py`, `tests/test_run_manager.py`

**Interfaces:**
- Consumes: `AskGate`, `Answer`, `agent.frames.frame_origin`.
- Produces:
  - `POST /api/runs/<run_id>/answer`, 본문 `{"question_id": str, "text": str, "accepted": bool | null, "pin": {"observation": int, "x": int, "y": int} | null, "items": [...]}`
  - `RunManager.answer(run_id, question_id, text, accepted=None, pin=None, items=()) -> bool`
  - `RunManager(..., ask_gate_factory=AskGate)`. 실행마다 `ask_gate_factory(timeout_s=getattr(task, "ask_timeout_s", 1800))`로 만든다. 승인(`approve`)과 상관없이 항상 만든다.
  - `GET /api/status`에 `"can_answer": bool`(이 서버가 그 실행의 질문에 답할 수 있는지).

`pin`은 그 실행 `events.jsonl`의 해당 `observation` 이벤트에서 `window.bounds`와 `image`를 읽어 `frame_origin`으로 화면 좌표로 바꾼다. 응답 코드: 본문 검증 실패 400, `pin`의 관찰이 없거나 이미지가 없거나 좌표가 이미지 밖이면 400(Review Focus 2), 답할 수 없는 질문이면 409(Review Focus 1), 관리자가 없으면 503. `text`는 `MAX_COMMENT_CHARS` 이하다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_answer_reaches_the_waiting_question(server_with_run):
    status, body = post(f"/api/runs/{RUN}/answer", {"question_id": "q1", "text": "2번"})
    assert (status, body) == (200, {"accepted": True}) and waiting_answer().text == "2번"

def test_pin_is_converted_from_image_to_screen_pixels(server_with_run):
    post(f"/api/runs/{RUN}/answer", {"question_id": "q1", "text": "여기", "pin": {"observation": 3, "x": 10, "y": 20}})
    assert waiting_answer().pin == (1018, 520)

@pytest.mark.parametrize("pin", [{"observation": 99, "x": 1, "y": 1}, {"observation": 3, "x": 5000, "y": 1}])
def test_pin_that_cannot_be_converted_is_400(pin): ...                      # Review Focus 2

def test_second_answer_is_409(server_with_run):                             # Review Focus 1
    post(f"/api/runs/{RUN}/answer", {"question_id": "q1", "text": "a"})
    assert post(f"/api/runs/{RUN}/answer", {"question_id": "q1", "text": "b"})[0] == 409

@pytest.mark.parametrize("body", [{}, {"question_id": "q1"}, {"question_id": "q1", "text": 3},
                                  {"question_id": "q1", "text": "x" * 2001}, {"question_id": "q1", "text": "", "accepted": "yes"}])
def test_bad_answer_bodies_are_400(body): ...

def test_answer_without_a_manager_is_503(): ...
def test_answer_needs_the_token(): ...
def test_status_reports_can_answer(): ...
```

- [ ] **Step 2:** `python -m pytest tests/test_server.py tests/test_run_manager.py -q` → 새 테스트 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과

### Task W2-3: 뷰어 화면 — 채팅, 질문 박스, 위치 찍기, 새 단계 정보

**Files:** Modify `viewer/static/index.html`, `viewer/static/app.js`, `viewer/static/style.css`, `viewer/server.py`(정적 파일 이름을 더할 때), `viewer/history.py`(상태 `confirmed`) · Create `tests/fixtures/run_questions/`(`run.json`, `events.jsonl`, `frames/`) · Test `tests/test_server.py`, `tests/test_view.py`

**Interfaces:**
- Consumes: 계약 5의 이벤트, W2-2의 경로.
- Produces: 사용자가 보는 화면. 기준은 설계 문서 "9. 뷰어"와 사용자가 승인한 목업이다.

화면이 해야 하는 것:

1. 오른쪽 단계 정보 옆(또는 아래)에 채팅 영역. `question`, `answer`, `registration`, `completion` 이벤트가 시간순으로 쌓인다. 질문은 노란 왼쪽 띠, 내 답은 오른쪽 정렬.
2. 답하지 않은 질문이 있고 `can_answer`면 입력 칸과 "보내기"가 켜진다. `completion`은 "맞음"·"아님" 버튼과 의견 칸, `registration`은 항목마다 "승인"·"고쳐서 승인"(글 칸)·"거절"이다. 답할 수 없는 뷰어(기록 보기, `run.py --view`)는 "이 실행은 터미널에서 답합니다"를 보이고 입력을 끈다.
3. 질문이 가리키는 관찰의 프레임 위에 후보 박스를 그린다. `proposed`는 빨간 실선(`--target`), 나머지 후보는 점선(`--proposal`), `basis`는 노란 반투명 채움. 박스 왼쪽 위에 질문 안 번호(①②③) 배지. 후보의 `rect`가 없고 `image_point`만 있으면 십자 표시.
4. 답하지 않은 질문이 떠 있는 동안 프레임을 클릭하면 위치 표시가 생기고 답에 "📍 찍은 위치" 칩이 붙는다. 다시 클릭하면 옮겨지고 칩의 ×로 지운다. 프레임이 화면에 줄어든 비율로 보여도 보내는 좌표는 원본 이미지 픽셀이다.
5. 동작 단계의 정보에 "기대한 변화", "일어난 변화"(요약 줄), "기대와 다름" 배지, "지난 답을 참고함"과 그 항목의 글(`knowledge_used`)을 보인다.
6. 상태 `confirmed`의 이름은 "사람이 확인함"이고 목록 필터에 들어간다.
7. 답하지 않은 질문이 생기면 타임라인이 그 질문으로 이동하고 문서 제목 앞에 "(질문)"을 붙인다.

- [ ] **Step 1: 고정 기록을 만든다.** `tests/fixtures/run_questions/`에 관찰 2개, `confirm` 질문(후보 3개, `proposed` 1, `basis` 2개)과 답, `stuck` 질문(후보 2개)과 `pin`이 있는 답, 기대와 다른 `action`, `knowledge_used`, `completion`(human), `registration`, `finish`(`confirmed`)를 계약 5의 모양으로 적는다. 프레임은 작은 PNG면 된다.
- [ ] **Step 2: 실패하는 테스트를 쓴다.** `run_questions`의 이벤트가 `/api/runs/<id>/events`로 그대로 나오는지, 상태 필터 `confirmed`가 받아들여지는지, 새 정적 파일을 더했다면 `is_static_name`이 그것만 더 허용하는지.
- [ ] **Step 3:** `python -m pytest tests/test_server.py tests/test_view.py -q` → 실패
- [ ] **Step 4:** 화면을 구현한다. `app.js`가 이미 1480줄이므로 채팅과 질문 박스는 새 파일(`chat.js`)에 둔다.
- [ ] **Step 5:** 통과. 그다음 `python view.py --open <고정 기록을 복사한 run id>`로 열어 위 1~7을 눈으로 확인하고, 확인한 것과 못 한 것을 보고서에 적는다(4의 좌표는 브라우저 콘솔에서 보내는 본문을 확인).

### Task W2-4: 내보내기 HTML에 질문과 답

**Files:** Modify `viewer/export.py`, `viewer/static/*`(내보내기 분기) · Test `tests/test_export.py`

**Interfaces:**
- Consumes: W2-3.
- Produces: `view.py --export`로 만든 HTML에 질문, 후보 박스, 답, 등록 결과가 읽기 전용으로 보인다. 입력 칸과 보내기 버튼은 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_export_carries_questions_and_answers(tmp_path):
    html = export_run(FIXTURES / "run_questions", tmp_path / "out.html").read_text(encoding="utf-8")
    assert '"type": "question"' in html or '"type":"question"' in html
    assert "2번" in html

def test_export_has_no_answer_form(tmp_path): ...
```

- [ ] **Step 2:** `python -m pytest tests/test_export.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과. 내보낸 파일을 브라우저로 열어 질문 박스가 보이는지 확인한다.

### Task W2-5: 지식 목록 화면

**의존:** W3-3 수락 뒤.

**Files:** Modify `viewer/server.py`, `viewer/static/*`, `view.py` · Test `tests/test_server.py`

**Interfaces:**
- Consumes: `KnowledgeStore`, `list_systems`, `KnowledgeError`.
- Produces:
  - `make_server(..., knowledge_dir=None)`. 없으면 지식 경로는 모두 503.
  - `GET /api/knowledge` → `{"systems": [...]}`
  - `GET /api/knowledge/<system>` → `{"entries": [...], "warnings": [...]}`(비활성 포함)
  - `POST /api/knowledge/<system>/<id>` 본문 `{"changes": {...}}` → 고친 항목. 검증 실패는 400.
  - `POST /api/knowledge/<system>/<id>/delete` → `{"deleted": true}`
  - 화면: 위쪽에 "실행 | 지식" 전환. 시스템을 고르면 항목 목록(종류, 상황 또는 작업, 해결, 참고 횟수, 실패 횟수, 상태). 항목마다 글 고치기, 사용 멈추기·다시 쓰기, 지우기(한 번 더 묻는다). 건너뛴 파일의 경고를 목록 위에 보인다.

`<system>`과 `<id>`는 `[A-Za-z0-9가-힣._-]{1,64}`만 받고 그 밖은 404다(경로 탈출 방지).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_lists_systems_and_entries(server_with_knowledge): ...
def test_update_changes_the_file_and_returns_the_entry(server_with_knowledge):
    status, body = post("/api/knowledge/mes/r-20261113-001", {"changes": {"status": "disabled"}})
    assert status == 200 and store.get("r-20261113-001")["status"] == "disabled"
def test_update_with_a_coordinate_field_is_400(server_with_knowledge): ...
def test_delete_removes_the_file(server_with_knowledge): ...
@pytest.mark.parametrize("path", ["/api/knowledge/..", "/api/knowledge/mes/..%2Fx", "/api/knowledge/a b"])
def test_odd_names_are_404(path): ...
def test_knowledge_routes_without_a_dir_are_503(): ...
def test_knowledge_routes_need_the_token(): ...
```

- [ ] **Step 2:** `python -m pytest tests/test_server.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과. `knowledge.example/`을 임시 폴더에 복사해 화면에서 고치기, 멈추기, 지우기를 확인한다.

### Task W2-6: 뷰어에서 시작한 실행 연결

**의존:** W1-9, W3-5 수락 뒤.

**Files:** Modify `viewer/run_manager.py`, `viewer/server.py`, `viewer/static/*`, `view.py` · Test `tests/test_run_manager.py`, `tests/test_view.py`

**Interfaces:**
- Consumes: 계약 6의 `Toolbox`·`run_agent`, `KnowledgeStore`, `system_name`, `register_after_run`.
- Produces:
  - `RunManager(..., knowledge_dir=None)`, `RunManager.start(goal, exe=None, level=3, approve=False, system=None)`
  - `toolbox_factory(desktop, task, confirm, observer, asker, knowledge, captures_dir)`
  - 시작 폼에 "시스템"(선택) 칸. 비우면 실행 파일 이름.
  - `view.py --knowledge-dir <경로>`(기본은 `run.py`와 같은 순서).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_started_run_gets_the_ask_gate_the_store_and_the_register_hook(manager): ...
def test_question_from_the_run_is_answered_through_the_manager(manager):
    run_id = manager.start("목록을 열어줘")
    wait_for_event(run_id, "question")
    assert manager.answer(run_id, "q1", "1번") is True
    manager.join(5)
    assert last_status(run_id) != "stopped"
def test_unanswered_question_ends_the_run_as_stopped(manager_with_timeout_0): ...
def test_system_from_the_form_selects_the_knowledge_folder(manager): ...
def test_system_with_odd_characters_is_invalid(manager): ...
```

- [ ] **Step 2:** `python -m pytest tests/test_run_manager.py tests/test_view.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** `python -m pytest -q` → 전부 통과

---

## w3: 가상 앱, 지식, 완료

### Task W3-1: 가상 앱 — 화살표로만 열리는 선택 칸

**Files:** Modify `virtual-mes/Options.cs`, `FilterColumn.cs`, `Surface.cs`, `SurfaceUia.cs`, `SurfaceItem.cs`, `Session.cs`, `README.md` · Test `tests/test_virtual_mes.py`, `tests/test_virtual_mes_live.py`(쓰기만 하고 W3-6에서 돌림)

**Interfaces:**
- Produces:
  - 옵션 `--combo-open anywhere|arrow`(기본 `anywhere`). `--parse-only` 출력과 `app_started` 이벤트에 `combo_open`.
  - `arrow`일 때: 칸의 화살표 영역(오른쪽 끝, 칸 높이만 한 정사각형)을 눌러야 목록이 열린다. 몸통을 누르면 입력 커서만 그려지고(깜빡임) 목록은 열리지 않는다.
  - 화살표의 노출은 `filters` 영역의 방식을 따른다. `ref`: 칸의 자식으로 이름 `열기`, `AutomationId` `<칸 id>-arrow`인 `button`. 칸 자체의 Invoke는 몸통 클릭과 같다(목록을 열지 않음). `text`: 화살표 요소 없음. `pixel`: 아무것도 없음.
  - 이벤트 `combo_body_clicked {screen, filter}`, `combo_arrow_clicked {screen, filter}`. `anywhere`에서는 기록하지 않는다.
  - 비활성 칸은 두 방식 모두 아무 반응이 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_virtual_mes.py`)

```python
def test_combo_open_defaults_to_anywhere_and_accepts_arrow():
    assert parsed()["combo_open"] == "anywhere"
    assert parsed("--combo-open", "arrow")["combo_open"] == "arrow"

def test_bad_combo_open_exits_2():
    assert run_exe("--combo-open", "sideways", "--parse-only").returncode == 2
```

`tests/test_virtual_mes_live.py`에 다음을 쓴다(지금은 건너뜀).

```python
def test_arrow_mode_body_click_shows_no_list(tmp_path): ...      # combo_body_clicked만, 창 목록에 "목록" 없음
def test_arrow_mode_arrow_click_opens_the_list(tmp_path): ...    # combo_arrow_clicked, "목록" 창이 뜸
def test_arrow_is_a_child_button_only_in_ref_mode(tmp_path): ...
def test_anywhere_mode_still_opens_on_a_body_click(tmp_path): ...
```

- [ ] **Step 2:** 빌드하고 `python -m pytest tests/test_virtual_mes.py -q` → 새 테스트 실패
- [ ] **Step 3:** 구현하고 `powershell -NoProfile -File virtual-mes\build.ps1`로 빌드한다.
- [ ] **Step 4:** `python -m pytest tests/test_virtual_mes.py -q` → 통과
- [ ] **Step 5:** `virtual-mes/README.md`의 옵션 표, 노출 방식, 이벤트 표를 고친다.

### Task W3-2: 가상 앱 — 묶음 헤더와 소계 행이 있는 화면

**Files:** Modify `virtual-mes/ScreenSpec.cs`, `MesData.cs`, `GridSurface.cs`, `ScreenPanel.cs`, `CellPopup.cs`, `Session.cs`, `FilterValues.cs`(필요하면), `README.md` · Test `tests/test_virtual_mes.py`, `tests/test_virtual_mes_live.py`

**Interfaces:**
- Produces:
  - 여섯 번째 화면 `process-summary`, 목록 이름 "공정별 생산집계", 종류 `grouped`. 필터는 Shop(세 칸 모두 활성)과 기간. 저장은 엑셀 아이콘과 우클릭 원형 메뉴.
  - 헤더 두 줄. 1행: `SHOP`(두 줄 높이) · `공정`(2칸) · `계획`(3칸) · `생산`(4칸) · `불량`(6칸). 2행: `그룹공정` `공정` · `계획` `실적` `달성률` · `작업수` `양품수` `양품율` `수율(Loss제외)` · `불량수` `불량율(PPM)` `LOSS수` `LOSS율(PPM)` `이탈수` `이탈율(PPM)`.
  - 행: Shop마다 그룹공정이 `소계`이고 공정이 빈 칸인 행 하나가 먼저 오고, 그 아래 그룹공정마다 공정이 `소계`인 행이 온다. Shop과 그룹공정의 이름은 `MesData`의 기존 Shop과 공정에서 가져오고, 값은 `front-process` 데이터의 합으로 계산해 같은 필터면 항상 같다.
  - 숫자 셀을 누르면 이벤트 `cell_clicked {screen, row: [shop, 그룹공정, 공정], column: [위 헤더, 아래 헤더]}`를 남기고 상세 팝업을 띄운다(`cell_popup_opened`의 `row`는 세 이름을 ` · `로 이은 글, `column`은 아래 헤더 이름).
  - `--export-headless process-summary <필터> <출력>`: 첫 행은 아래 헤더 이름(첫 칸은 `SHOP`), 그 아래 행들.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_virtual_mes.py`)

```python
def test_process_summary_has_the_lower_header_and_subtotal_rows(tmp_path):
    table = rows(tmp_path, "process-summary", {})
    assert table[0] == ["SHOP", "그룹공정", "공정", "계획", "실적", "달성률", "작업수", "양품수", "양품율",
                        "수율(Loss제외)", "불량수", "불량율(PPM)", "LOSS수", "LOSS율(PPM)", "이탈수", "이탈율(PPM)"]
    shops = [row[0] for row in table[1:]]
    assert shops == sorted(shops, key=["믹싱", "극판", "조립", "화성"].index)
    first_of_shop = next(row for row in table[1:] if row[0] == "극판")
    assert first_of_shop[1:3] == ["소계", ""]
    assert all(row[2] == "소계" for row in table[1:] if row[1] != "소계")

def test_shop_subtotal_is_the_sum_of_its_group_rows(tmp_path):
    table = rows(tmp_path, "process-summary", {})
    quantity = lambda row: int(row[7].replace(",", ""))
    groups = [row for row in table[1:] if row[0] == "극판" and row[1] != "소계"]
    assert quantity(next(r for r in table[1:] if r[0] == "극판" and r[1] == "소계")) == sum(map(quantity, groups))

def test_process_summary_respects_the_shop_filter(tmp_path):
    assert {row[0] for row in rows(tmp_path, "process-summary", {"shop": ["WA1"]})[1:]} == {"극판"}
```

`tests/test_virtual_mes_live.py`에는 다음을 쓴다.

```python
def test_grouped_header_cells_span_their_columns(tmp_path): ...   # "생산"의 가로 범위가 작업수~수율을 덮음
def test_clicking_a_number_cell_logs_row_and_column_and_opens_the_popup(tmp_path): ...
def test_menu_lists_six_screens(tmp_path): ...
```

- [ ] **Step 2:** `python -m pytest tests/test_virtual_mes.py -q` → 실패
- [ ] **Step 3:** 구현하고 빌드한다. 화면이 여섯 개가 되어 깨지는 기존 테스트는 고치고 이유를 보고서에 적는다.
- [ ] **Step 4:** `python -m pytest tests/test_virtual_mes.py -q` → 통과
- [ ] **Step 5:** `virtual-mes/README.md`의 화면 표, 이벤트 표, 창 없는 내보내기 설명을 고친다.

### Task W3-3: 지식 창고

**Files:** Create `agent/knowledge.py`, `knowledge.example/virtualmes/r-example-001.yaml`, `knowledge.example/virtualmes/c-example-001.yaml` · Modify `.gitignore`(`knowledge/` 추가) · Test `tests/test_knowledge.py`

**Interfaces:**
- Produces: 계약 4 전부.

정해 둔 것:

- 항목의 키는 계약 4에 적힌 것뿐이다. 모르는 키가 있으면 `KnowledgeError`.
- `situation`, `resolution`, `task`에 좌표처럼 보이는 글(`(123, 456)` 꼴, `x=123`·`y: 45` 꼴)이 있으면 `KnowledgeError`.
- `checks`의 `kind`는 `file`, `element`, `human`뿐이다. `file`은 `path`나 `pattern` 가운데 하나 이상, `element`는 `require`가 있어야 한다.
- id는 `<r|c>-<YYYYMMDD>-<세 자리 순번>`이고 그날 그 시스템에서 비어 있는 가장 작은 순번이다.
- 파일은 임시 파일에 쓴 뒤 바꿔치기한다. YAML은 한글을 그대로 쓴다(`allow_unicode=True`).
- 읽을 때 깨진 파일(YAML 오류, 필수 키 없음, 모르는 키, 파일 이름과 id 불일치)은 건너뛰고 `warnings`에 파일 이름과 이유를 넣는다(Review Focus 3).
- 시스템 이름은 `[A-Za-z0-9가-힣._-]{1,64}`만 받고 `.`과 `..`은 받지 않는다. `system_name`은 exe 파일 이름에서 허용되지 않는 글자를 `_`로 바꾼다.
- `briefing`은 `resolution` 항목을 `- (id) 상황: … → 해결: …`로, `completion` 항목을 `- (id) 작업: … → 확인: file *.xlsx, human`처럼 적고, `limit_chars`를 넘으면 넘는 항목부터 빼고 `truncated`를 true로 돌려준다. 순서는 `uses`가 많은 것부터.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_add_assigns_an_id_and_writes_one_readable_file(tmp_path):
    store = KnowledgeStore(tmp_path, "mes")
    entry = store.add(RESOLUTION, today=date(2026, 11, 13))
    assert entry["id"] == "r-20261113-001" and entry["uses"] == 0 and entry["status"] == "active"
    text = (tmp_path / "mes" / "r-20261113-001.yaml").read_text(encoding="utf-8")
    assert "오른쪽 끝의 화살표" in text

def test_second_entry_of_the_day_gets_the_next_number(tmp_path): ...
def test_update_changes_only_the_given_keys_and_delete_removes_the_file(tmp_path): ...
def test_disabled_entries_are_hidden_unless_asked(tmp_path): ...
def test_counts_survive_a_reload(tmp_path): ...

@pytest.mark.parametrize("text", ["(412, 233)을 누른다", "x=412 지점을 누른다", "y: 233"])
def test_coordinates_in_the_text_are_rejected(tmp_path, text):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").add({**RESOLUTION, "resolution": text})

def test_unknown_keys_are_rejected(tmp_path):
    with pytest.raises(KnowledgeError):
        KnowledgeStore(tmp_path, "mes").add({**RESOLUTION, "bounds": {"x": 1}})

@pytest.mark.parametrize("content", ["id: [unclosed", "type: resolution\n", VALID_YAML + "extra: 1\n"])
def test_broken_files_are_skipped_with_a_warning(tmp_path, content):          # Review Focus 3
    store = store_with_files(tmp_path, good=VALID_YAML, bad=content)
    assert [e["id"] for e in store.entries()] == ["r-20261113-001"] and "bad.yaml" in store.warnings[0]

@pytest.mark.parametrize("name", ["..", "a/b", "a\\b", "", "x" * 65])
def test_odd_system_names_are_rejected(tmp_path, name): ...

def test_system_name_falls_back_to_the_exe_stem():
    assert system_name(None, r"C:\apps\Virtual Mes.exe") == "virtual_mes"
    assert system_name("mes", r"C:\apps\VirtualMes.exe") == "mes"

def test_briefing_lists_entries_most_used_first_and_reports_truncation(): ...
def test_example_entries_load():
    assert len(KnowledgeStore(EXAMPLES, "virtualmes").entries()) == 2
```

- [ ] **Step 2:** `python -m pytest tests/test_knowledge.py -q` → 실패
- [ ] **Step 3:** 구현하고 예시 항목 두 개를 쓴다(가상 앱의 화살표 칸, 엑셀 저장 완료 조건).
- [ ] **Step 4:** 통과. `git status`로 `knowledge/`가 무시되는지 확인한다.

### Task W3-4: 완료 확인 방법

**Files:** Create `agent/completion.py` · Test `tests/test_completion.py`

**Interfaces:**
- Consumes: `agent.verify`의 요구 조건 모양.
- Produces:

```python
def check_file(path, started_at, pattern=None) -> dict                    # {"met": bool, "detail": str}
def run_checks(checks, *, verify_elements, saved_path, started_at, ask_human) -> dict
# verify_elements(require: list) -> {"met": bool, "unmet": ...}
# ask_human() -> bool
# 돌려주는 것: {"met": bool, "by": "harness" | "human", "detail": str}
```

- `check_file`: 경로가 있고, 폴더가 아닌 파일이고, 크기가 0보다 크고, 고친 시각이 `started_at - 2`초 이후여야 한다. `pattern`이 있으면 파일 이름이 맞아야 한다(`fnmatch`). `path`가 폴더이고 `pattern`이 있으면 그 폴더에서 맞는 가장 새 파일을 본다.
- `run_checks`: 목록 순서대로 실행하고 처음 만족하지 않은 것에서 멈춘다. `file` 확인의 경로는 항목의 `path`, 없으면 `saved_path`다. 둘 다 없으면 만족하지 않음이고 `detail`에 "saved_path가 필요함"을 적는다. `human` 확인이 하나라도 실행되어 전부 만족하면 `by`는 `"human"`이다. 빈 목록은 만족하지 않음이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_file_written_after_the_start_is_met(tmp_path):
    saved = tmp_path / "out.xlsx"; saved.write_bytes(b"data")
    assert check_file(str(saved), started_at=saved.stat().st_mtime - 10)["met"] is True

@pytest.mark.parametrize("make,why", [(missing, "없음"), (empty_file, "비어 있음"), (old_file, "실행 전"), (a_folder, "폴더")])
def test_file_check_fails_with_a_reason(tmp_path, make, why):
    result = check_file(make(tmp_path), started_at=NOW)
    assert result["met"] is False and why in result["detail"]

def test_pattern_must_match_the_name(tmp_path): ...
def test_folder_with_pattern_checks_the_newest_match(tmp_path): ...

def test_checks_run_in_order_and_stop_at_the_first_unmet(tmp_path):
    asked = []
    result = run_checks([{"kind": "file"}, {"kind": "human"}], verify_elements=None, saved_path=None,
                        started_at=NOW, ask_human=lambda: asked.append(1) or True)
    assert result["met"] is False and asked == []

def test_all_harness_checks_met_is_by_harness(tmp_path): ...
def test_a_human_check_makes_it_by_human(tmp_path): ...
def test_element_check_uses_the_given_verifier(): ...
def test_empty_checks_are_not_met():
    assert run_checks([], verify_elements=None, saved_path=None, started_at=NOW, ask_human=None)["met"] is False
```

- [ ] **Step 2:** `python -m pytest tests/test_completion.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과

### Task W3-5: 등록 — 초안, 승인, 저장

**의존:** W1-8 수락 뒤(`toolbox.exchanges()`, `toolbox.ask`). 수락 전에는 계약 7의 모양으로 만든 가짜로 Step 1~2를 할 수 있다.

**Files:** Create `agent/registration.py` · Test `tests/test_registration.py`

**Interfaces:**
- Consumes: 계약 3, 4, 5, 7.
- Produces:

```python
def draft(model, exchanges, finish_note, existing) -> tuple[list[dict], int]   # 초안 항목(계약 5), 쓴 토큰
def register_after_run(model, toolbox, log, *, store, run_id, today=None) -> int
# toolbox.exchanges()와 toolbox.finish_note(계약 6)를 읽는다
```

정해 둔 것:

- `exchanges`가 비어 있고 `toolbox.finish_note`가 없거나 그 `by`가 `"harness"`면 모델을 부르지 않고 0을 돌려준다.
- 초안은 모델 한 번의 호출로 받는다. 도구 `propose_entries(items=[{action, id, type, situation, resolution, task, checks}])` 하나만 주고 그 호출의 인자를 읽는다. 모델이 도구를 부르지 않으면 초안은 빈 목록이다.
- 초안 지침의 요점(문구는 작업자가 쓴다): 질문과 답과 그 뒤 통한 동작에서 다음 실행에 다시 묻지 않게 할 내용만 뽑는다. 좌표, 번호, ref, 화면의 값(수량, 날짜, 이름 같은 업무 데이터)은 쓰지 않는다. 요소의 역할, 이름, 부분, 상대 위치로 쓴다. 이미 있는 항목과 같으면 만들지 않는다. `failed_knowledge_id`가 있는 exchange는 그 id의 `update`로 낸다. `finish_note.by`가 `"human"`이고 `saved_path`가 있으면 그 확장자의 `file` 확인을 가진 완료 조건 하나를 낸다.
- 초안 항목은 `store`의 검증을 미리 거쳐, 통과하지 못한 것은 버리고 `log`에 경고를 남긴다.
- 항목이 하나라도 있으면 `toolbox.ask({"kind": "registration", "text": "다음 내용을 등록할까요?", "items": [...]})`로 묻는다. 시간 초과(`AgentStopped`)는 여기서 잡아 아무것도 저장하지 않는다.
- `approve`는 초안대로, `edit`는 답의 `entry`로 저장한다. `add`는 `store.add`, `update`는 `store.update`. `source`에는 `run_id`, 그 exchange의 질문 글과 답 글, 날짜를 넣는다.
- 끝에 `registration` 이벤트를 `log`로 보낸다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_nothing_to_learn_calls_no_model():
    assert register_after_run(model, toolbox_without_exchanges, log, store=store, run_id=RUN) == 0
    assert model.inputs == []

def test_approved_item_is_saved_with_its_source(tmp_path):
    toolbox = toolbox_with(exchanges=[STUCK_EXCHANGE], answers=[approve_all])
    tokens = register_after_run(drafting(RESOLUTION_ITEM), toolbox, log, store=store, run_id=RUN, today=DAY)
    saved = store.entries()[0]
    assert tokens == 100 and saved["resolution"] == RESOLUTION_ITEM["entry"]["resolution"]
    assert saved["source"]["run_id"] == RUN and saved["source"]["answer"] == "4번"

def test_edited_item_is_saved_as_edited(tmp_path): ...
def test_rejected_item_is_not_saved_and_is_counted(tmp_path):
    register_after_run(drafting(RESOLUTION_ITEM), toolbox_with(answers=[reject_all]), log, store=store, run_id=RUN)
    assert store.entries() == [] and events[-1] == {"type": "registration", "saved": [], "rejected": 1}

def test_failed_knowledge_becomes_an_update_of_that_entry(tmp_path): ...
def test_draft_with_coordinates_is_dropped_before_asking(tmp_path): ...
def test_human_confirmed_save_proposes_a_file_completion(tmp_path):
    note = {"goal": "엑셀로 저장해줘", "evidence": "저장함", "saved_path": r"C:\out\a.xlsx", "by": "human"}
    register_after_run(drafting(COMPLETION_ITEM), toolbox_with(answers=[approve_all], finish_note=note), log,
                       store=store, run_id=RUN)
    assert store.entries()[0]["checks"] == [{"kind": "file", "pattern": "*.xlsx"}]

def test_unanswered_registration_saves_nothing_and_does_not_raise(tmp_path): ...
def test_model_that_proposes_nothing_asks_nothing(tmp_path): ...
```

- [ ] **Step 2:** `python -m pytest tests/test_registration.py -q` → 실패
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 통과

### Task W3-6: 실제 창 테스트

**의존:** 모든 배정 수락 뒤. 데스크톱 잠금이 필요하다. 오케스트레이터가 사용자에게 확인한 뒤 잠금을 넘긴다.

**Files:** Create `tests/test_scenes_live.py` · Modify `tests/test_virtual_mes_live.py`, `tests/virtual_mes_support.py`

**Interfaces:**
- Consumes: 실제 `Toolbox`, `run_agent`, `KnowledgeStore`, `register_after_run`, 가상 앱의 이벤트 기록.
- Produces: `VIRTUAL_MES_LIVE=1`일 때만 도는, 정해진 순서로 움직이는 가짜 모델의 장면 테스트. 성공은 도구 결과가 아니라 앱의 이벤트 기록과 저장된 파일로 판정한다.

- [ ] **Step 1: 장면 테스트를 쓴다.** 모델은 `ScriptedModel`, 묻는 쪽은 `ScriptedAsker`다. 각 테스트는 임시 폴더의 지식 창고와 `AGENT_DESKTOP_HOME`을 쓴다.

```python
def test_scene_1_center_click_fails_then_the_arrow_opens_the_list(live):       # 목표 1. filters=ref
    # click_part(center, expect=window_opened) → expected_met False → click(화살표 ref) → 목록 창
    assert event_names(live) [-2:] == ["combo_body_clicked", "combo_arrow_clicked"]

def test_scene_2_stuck_question_then_the_right_end_opens_the_list(live):       # 목표 2. filters=text
    # center 두 번 → blocked_until_question → ask(stuck) 답 "오른쪽 끝" → click_part(right)
def test_scene_2_pin_answer_on_a_pixel_filter(live):                           # 목표 2. filters=pixel
    # look → click_point 거절(needs_confirmation) → ask 답에 pin → click_point(pin 지점) → 목록 창
def test_scene_3_confirm_among_three_boxes_then_open_the_chosen_one(live):     # 목표 3. 묻는 판단은 O-2에서 모델로 확인
    # ask(confirm, 후보 3칸) 답 "3번" → click_part(3번 칸) → filter_changed의 filter가 shop-3
def test_scene_4_cell_is_confirmed_then_clicked(live):                         # 목표 4. grid=text
    # find_cells(["극판","소계"],["양품수"]) 후보 3 → 클릭 거절 → ask(confirm) → click_part → cell_clicked
    assert last(live, "cell_clicked")["row"] == ["극판", "소계", ""]
    assert last(live, "cell_clicked")["column"] == ["생산", "양품수"]
def test_scene_5_saved_file_is_confirmed_and_registered(live):                 # 목표 5
def test_scene_6_second_run_cites_knowledge_and_asks_nothing(live):            # 목표 6
def test_scene_7_wrong_knowledge_is_counted_and_asked_again(live):             # 목표 7
def test_image_point_lands_on_the_cell_at_this_dpi(live):                      # 설계의 미확인: DPI 175%
```

- [ ] **Step 2:** 잠금을 받은 뒤 `$env:VIRTUAL_MES_LIVE='1'; python -m pytest tests/test_virtual_mes_live.py tests/test_scenes_live.py -q`
- [ ] **Step 3:** 실패한 테스트는 원인을 가려 보고한다. 자기 편집 경로의 문제면 고치고, 다른 작업자의 모듈 문제면 재현 방법과 함께 오케스트레이터에게 넘긴다.
- [ ] **Step 4:** 전부 통과한 출력을 보고서에 붙이고 잠금을 반납한다.

---

## 오케스트레이터: 통합

### Task O-1: 전체 검사와 문서

- [ ] `python -m pytest -q`(창 없는 테스트 전부)를 직접 돌린다.
- [ ] `tests/test_application_agnostic.py`를 쓴다. `agent/*.py`, `viewer/*.py`, `run.py`, `view.py`의 코드(주석과 문서 문자열, `SYSTEM_PROMPT`의 예시 문장 제외)에 `VirtualMes`, `MES`, `목록`, `Open`, `열기`, `소계`, `극판`이 나오면 실패한다.
- [ ] `run.json`에 질문 글과 지식 글이 없는지 W3-6의 기록으로 확인한다.
- [ ] `README.md`, 저장소 `CLAUDE.md`의 `examples/llm-agent` 절, 설계 문서의 상태 줄과 "미확인"을 고친다.

### Task O-2: 모델을 쓰는 확인 (목표 1~7)

사용자가 뷰어에서 직접 답한다. 자동화하지 않는다. `docs/checklists/2026-11-13-observe-ask-learn.md`에 장면마다 가상 앱 옵션, 지시 문장, 기대하는 일, 실제로 일어난 일을 적는다. 실제 모델을 부르고 창을 띄우므로 사용자와 함께 한다.

### Task O-3: 일반화 확인 (목표 8)

가상 앱이 아닌 앱 두 개 이상에서 앱별 코드 없이 목표 1(가운데 클릭이 통하지 않으면 다른 부분을 시도)과 변화 보고가 통하는지 본다. 제안하는 앱은 저장소의 WinForms 픽스처(`tests/fixture-app-windows`, 표준 콤보박스)와 Windows 메모장(WinUI)이다. 어느 앱으로 할지는 사용자가 정한다.
