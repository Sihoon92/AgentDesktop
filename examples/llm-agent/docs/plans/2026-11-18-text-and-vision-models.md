# 텍스트 모델과 비전 모델 나눠 쓰기 구현 계획

> **에이전트 작업자에게:** 작업마다 superpowers:test-driven-development를 따릅니다. 단계는 체크박스(`- [ ]`)로 표시합니다.

- 날짜: 2026-11-18
- 상태: 사용자 승인(2026-11-18), 작업 1~9 수락, 통합 확인 끝(커밋 전, 실제 모델 확인 전)
- 설계: [텍스트 모델과 비전 모델 나눠 쓰기 설계](../specs/2026-11-16-text-and-vision-models-design.md) (사용자 승인 2026-11-18). 이 계획은 설계를 근거로 하므로 구현하는 사람은 둘 다 읽습니다. 값과 규칙은 설계가 기준이고, 이 계획은 파일, 이름, 시그니처, 테스트, 순서를 정합니다.

**목표:** env에 비전 모델 이름을 넣으면 텍스트 모델에게는 그림을 보내지 않고, 그림이 필요한 곳에서 하니스가 비전 모델에게 질문 하나와 그림 한 장을 보내 그 답을 도구 결과에 넣습니다.

**구조:** 비전 호출과 답 해석은 입출력이 적은 새 모듈 `agent/vision.py`에 둡니다. 연결 정보와 모델 객체 만들기는 `agent/llm.py`로 모읍니다. `Toolbox`는 `vision`이 있으면 `look`과 동작 뒤 자동 캡처에서 그것을 부르고, 루프는 텍스트 모델에게 그림을 보내지 않으며 비전 토큰을 예산에 더합니다. 뷰어와 점검은 새 `vision` 이벤트와 같은 모듈을 씁니다.

**쓰는 것:** Python 표준 라이브러리, 지금의 의존성(PyYAML, Pillow, openai, python-dotenv). 더하는 의존성은 없습니다.

## 전체에 걸친 조건

- 비전 모델을 지정하지 않은 실행(한 모델 방식)의 동작, 도구 스키마, 기록 형식은 바뀌지 않습니다. 지금의 테스트는 고치지 않고 통과해야 합니다. 고쳐야 하면 오케스트레이터에게 먼저 묻습니다.
- env 이름: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `VISION_MODEL`, `VISION_API_KEY`, `VISION_BASE_URL`. 빈 글(공백만 포함)은 없는 것으로 봅니다.
- 텍스트 모델 이름의 우선순위: 작업 파일 > 설정 파일 > `OPENAI_MODEL` > 코드 기본값. 비전 모델 이름: 설정 파일 `vision_model` > `VISION_MODEL`.
- 새 설정 키: `vision_model`(글), `vision_reasoning_effort`(글), `vision_max_side`(1 이상 정수, 기본 1280), `vision_points`(`pixel` 기본, `normalized_1000`).
- `reasoning_effort`나 `plan_reasoning_effort`가 `omit`이면 `reasoning` 인자를 보내지 않습니다. 비전 호출은 `vision_reasoning_effort`가 있을 때만 `reasoning`을 보냅니다.
- 비전 호출은 Responses API, 입력은 사용자 메시지 하나(`input_text` + `input_image`, `detail: "high"`), 도구 없음.
- 실행 시작 줄, `check.py` 출력, `run.json`, 요약에는 모델 이름, 주소, 키를 넣지 않습니다. 출처(`작업 파일`, `설정 파일`, `env`, `기본값`)와 수만 넣습니다. 질문과 답의 글은 `events.jsonl`에만 둡니다.
- `tests\test_application_agnostic.py` 통과. 비전 질문의 틀을 포함해 `agent/`와 `viewer/`에 앱 이름이나 화면의 글을 넣지 않습니다.
- `points`로 고른 클릭은 지금처럼 사람의 확인을 받습니다(`agent/policy.py`는 고치지 않음).
- 작업자는 커밋하지 않고, 실제 창을 띄우는 실행과 실제 모델 호출을 하지 않습니다. 테스트는 가짜 client와 가짜 데스크톱으로 합니다.
- 테스트는 `examples/llm-agent`에서 `/c/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -m pytest -q`로 돌립니다(아래 `pytest`는 이 명령의 줄임). 제출 전 전체 실패 0.

## 검토에서 특히 볼 것

설계가 뜻하지만 한 작업의 테스트만으로는 드러나지 않기 쉬운 경우입니다. 각 줄의 테스트는 표시한 작업에 넣습니다.

1. **비전 모델이 JSON을 코드 블록(```` ```json ... ``` ````)에 싸서 답할 때.** 많은 모델이 그렇게 답합니다. 감싼 JSON도 읽혀야 하고, 그래도 안 읽히면 글 전체가 `answer`가 됩니다. (작업 1)
2. **회사 설정에 `image_mode: off`가 남아 있는데 `VISION_MODEL`을 넣었을 때.** `look`이 거절되지 않고 비전 모델을 불러야 합니다. 실행 시작 줄에 `image_mode`를 무시했다고 한 줄 나와야 합니다. (작업 3, 작업 5)
3. **`VISION_BASE_URL`만 넣고 `VISION_MODEL`은 빈 값일 때.** 조용히 한 모델 방식으로 돌면 안 되고, 무엇이 빠졌는지 알리며 실행이 거절돼야 합니다. (작업 3)
4. **동작 뒤 자동 캡처에서 비전 호출이 시간 초과로 실패할 때.** 동작 결과는 그대로 돌아오고 `vision.error`만 붙어야 합니다. 실행이 멈추면 안 됩니다. (작업 6)
5. **캡처 파일을 읽을 수 없거나 줄이는 데 실패할 때.** 비전 호출 없이 `vision.error`가 `IMAGE_UNREADABLE`이 되고, 예외가 루프까지 올라가지 않아야 합니다. (작업 1)

## 배정과 순서

편집 경로가 겹치지 않게 셋으로 나눕니다. README와 `CLAUDE.md`는 오케스트레이터가 마지막에 고칩니다.

| 배정 | 작업자 | 작업 | 편집 경로 |
| --- | --- | --- | --- |
| `tvm-w1-connection` | w1 | 2, 3, 4 | `agent/llm.py`, `agent/settings.py`, `agent/task.py`, `run.py`, `view.py`, `viewer/run_manager.py`, `settings.example.yaml`, `.env.example`(새), `tasks/footprint_open_settings.yaml`, 각 테스트 |
| `tvm-w2-vision` | w2 | 1, 7, 8 | `agent/vision.py`(새), `check.py`, `agent/checks.py`, `viewer/static/*`, `viewer/export.py`, 각 테스트 |
| `tvm-w3-harness` | w3 | 5, 6, 9 | `agent/tool_schemas.py`, `agent/screen_tools.py`, `agent/tools.py`, `agent/loop.py`, `agent/recorder.py`, `agent/summary.py`, 각 테스트 |

의존:
- 작업 1(`agent/vision.py`)이 가장 먼저입니다. 작업 1 수락 전에 할 수 있는 것: 작업 2, 작업 5, 작업 7(가짜 `Vision`으로).
- 작업 3은 작업 1 수락 뒤(`Vision`을 import). 작업 6은 작업 1 수락 뒤(`auto_question`을 import).
- 작업 8은 작업 3 수락 뒤(`vision_setup`, `model_choice`를 씀).
- 작업 4는 작업 3과 작업 5 수락 뒤(`Toolbox(vision=...)`).
- 작업 9는 작업 6 수락 뒤.

## 계약

배정 사이에서 쓰는 이름입니다. 바꾸려면 오케스트레이터를 거칩니다.

**C1. `agent/vision.py` (작업 1)**

```python
VISION_INSTRUCTIONS: str          # 비전 모델에게 주는 지시(답 JSON 형식, 번호를 픽셀보다 먼저)
POINT_MODES = ("pixel", "normalized_1000")

@dataclass(frozen=True)
class VisionAnswer:
    answer: str
    marks: list            # 그 캡처에 그려진 번호만
    points: list           # [{"label": str, "x": int, "y": int}], 원본 캡처 픽셀
    unsure: bool
    error: str | None = None
    dropped: int = 0
    tokens: int = 0
    seconds: float = 0.0
    def as_result(self) -> dict    # 도구 결과의 "vision" 값. error·dropped는 있을 때만
    def as_event(self) -> dict     # vision 이벤트의 답 부분(answer, marks, points, unsure, error, dropped, tokens, seconds)

class Vision:
    def __init__(self, reader, max_side=1280, points="pixel", clock=time.monotonic): ...
    def read(self, capture, question: str, marks) -> VisionAnswer   # 예외를 던지지 않음

def auto_question(reason: str, expect: str | None, element: dict | None) -> str
```

`reader`는 C2의 `VisionReader`처럼 `ask(instructions, question, image_url) -> (text, tokens)`를 가진 객체입니다. 실패하면 예외를 던지고, `Vision.read`가 받아 `error`에 `agent.checks.llm_error_code(error)`를 넣습니다.

**C2. `agent/llm.py` (작업 3)**

```python
class ModelSetupError(Exception): ...          # 한국어 메시지, 경로·값·키 없음

class OpenAIResponder:                          # 지금 그대로 + omit, 키와 주소를 받음
    def __init__(self, model, reasoning_effort, client=None): ...
class VisionReader:
    def __init__(self, model, client, reasoning_effort=None): ...
    def ask(self, instructions, question, image_url) -> tuple[str, int]

def make_client(api_key, base_url):             # OpenAI(api_key=..., base_url=...)
def vision_setup(settings, environ, make_client=make_client) -> tuple[Vision | None, str | None]
def text_responder(task, environ, make_client=make_client) -> OpenAIResponder

@dataclass(frozen=True)
class Models:
    text: OpenAIResponder
    vision: Vision | None
    text_source: str
    vision_source: str | None
    image_mode_ignored: bool
def build_models(task, settings, environ, make_client=make_client) -> Models
def start_line(models) -> str
```

**C3. `agent/task.py` (작업 2)**

```python
MODEL_ENV = "OPENAI_MODEL"
def model_choice(file_value, settings, environ) -> tuple[str, str]   # (이름, 출처)
# Task에 model_source: str = "기본값" 필드를 더함. load_task와 task_from_args는 environ=None 인자를 더 받음
```

**C4. `Toolbox` (작업 5)**

```python
Toolbox(..., vision=None)        # 키워드 인자. 나머지 인자는 그대로
toolbox.vision_enabled -> bool   # 프로퍼티
toolbox.take_vision_usage() -> tuple[int, int]   # 마지막 호출 뒤의 (비전 호출 수, 비전 토큰), 읽으면 0으로
```

**C5. `vision` 이벤트 (작업 5·6이 내고, 작업 9가 기록하고, 작업 7이 보여 줌)**

```json
{"type": "vision", "step": 7, "capture": "c3", "asked_by": "look",
 "question": "...", "answer": "...", "marks": [3], "points": [{"label": "...", "x": 412, "y": 88}],
 "unsure": false, "error": null, "dropped": 0, "tokens": 812, "seconds": 2.4}
```

`asked_by`는 `look`, `mismatch`, `unconfirmed` 중 하나입니다. `points`는 원본 캡처 픽셀입니다. `step`은 비워 보내면 기록기가 채웁니다.

**C6. `run.json`과 요약 (작업 9)**: 나눈 방식일 때만 `run.json`에 `vision_calls`, `vision_tokens`(정수)를 더하고, 요약에 `비전 호출 N · 비전 토큰 M` 한 줄을 더합니다. `RunResult.stats`에 같은 두 키가 들어옵니다.

---

### 작업 1: 비전 모듈 (w2)

**파일:**
- 새로 만듦: `agent/vision.py`
- 테스트: `tests/test_vision.py`(새)

**인터페이스:** C1을 만듭니다. `agent.capture.Capture`(`path`, `width`, `height`)와 `agent.checks.llm_error_code`를 씁니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.** 가짜 reader(`ask`가 정해진 글과 토큰을 돌려주거나 예외를 던짐)와 Pillow로 만든 PNG 캡처를 씁니다.

```python
def test_reads_marks_and_points_from_json(tmp_path): ...
    # 답 {"answer":"조회","marks":[3,99],"points":[{"label":"a","x":10,"y":20},{"label":"b","x":5000,"y":1}],"unsure":false}
    # marks=[3, 5] 허용 → answer.marks == [3], answer.points == [{"label":"a","x":10,"y":20}], answer.dropped == 2
def test_reads_json_inside_a_code_fence(tmp_path): ...       # "```json\n{...}\n```" → marks가 읽힘
def test_unparsable_text_becomes_the_answer(tmp_path): ...   # "모르겠습니다" → answer == "모르겠습니다", marks == [], points == []
def test_shrinks_a_large_capture_and_maps_points_back(tmp_path): ...
    # 2560x1440 캡처, max_side=1280 → reader가 받은 그림의 긴 변 1280, 답 (640, 360) → points (1280, 720)
def test_small_capture_is_sent_as_is(tmp_path): ...         # 800x600 → 받은 그림 800x600, 좌표 그대로
def test_normalized_points_become_capture_pixels(tmp_path): ...
    # points="normalized_1000", 2000x1000 캡처, 답 (500, 500) → points (1000, 500)
def test_reader_failure_is_an_error_not_an_exception(tmp_path): ...
    # reader가 TimeoutError → error == "TimeoutError", answer == "", 예외 없음
def test_unreadable_capture_is_an_error(tmp_path): ...      # 없는 파일 → error == "IMAGE_UNREADABLE", reader 호출 0번
def test_as_result_leaves_out_empty_error_and_dropped(): ...
def test_auto_question_names_the_expectation_and_the_target(): ...
    # auto_question("mismatch", "value_changed", {"role":"checkbox","name":"x"})에 "value_changed", "checkbox", "x"가 들어감
    # element None이어도 글을 돌려줌
def test_instructions_ask_for_marks_before_pixels(): ...    # VISION_INSTRUCTIONS에 "marks", "points", "unsure"
```

- [ ] **2단계:** `pytest tests/test_vision.py`. 기대: 모듈이 없어 실패.
- [ ] **3단계: `agent/vision.py`를 구현합니다.**
  - 줄이기: 긴 변이 `max_side`보다 크면 비율을 지켜 줄인 PNG를 메모리에서 만들고 `data:image/png;base64,` URL로 넘깁니다. 원본 파일은 건드리지 않습니다. 배율 `scale = 원본 긴 변 / 보낸 긴 변`.
  - 좌표 되돌리기: `normalized_1000`이면 `x * 보낸 폭 / 1000`으로 보낸 그림의 픽셀로 바꾼 뒤, `round(x * scale)`로 원본 픽셀로 바꿉니다. 원본 크기 밖은 버리고 `dropped`에 셉니다.
  - JSON 읽기: 글 전체, 그다음 첫 번째 코드 블록 안, 그다음 첫 `{`부터 마지막 `}`까지를 차례로 시도합니다.
  - `marks`는 정수이면서 `marks` 인자에 든 것만 남깁니다. 나머지는 `dropped`에 셉니다.
  - `seconds`는 `clock`으로 잽니다.
  - `auto_question`의 틀은 영어 한 문장으로, 기대의 종류, 대상의 역할과 이름(있을 때), "번호보다 화면 상태를 답하라"를 담습니다. 앱 이름이나 화면의 글을 코드에 넣지 않습니다.
- [ ] **4단계:** `pytest tests/test_vision.py tests/test_application_agnostic.py`. 기대: 통과.

### 작업 2: 모델 이름의 출처와 `omit` (w1)

**파일:**
- 고침: `agent/task.py`, `agent/settings.py`, `agent/llm.py`(`OpenAIResponder`만), `settings.example.yaml`, `tasks/footprint_open_settings.yaml`(`model` 줄 삭제)
- 테스트: `tests/test_task.py`, `tests/test_settings.py`, `tests/test_llm.py`(새)

**인터페이스:** C3을 만들고 `OpenAIResponder`에 `omit`을 더합니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_task.py
def test_model_from_task_file_beats_settings_and_env(): ...       # model_choice("a", {"model":"b"}, {"OPENAI_MODEL":"c"}) == ("a", "작업 파일")
def test_model_from_settings_beats_env(): ...                     # == ("b", "설정 파일")
def test_model_from_env_when_file_and_settings_lack_it(): ...     # == ("c", "env")
def test_blank_env_model_falls_to_the_default(): ...             # {"OPENAI_MODEL": "  "} → ("gpt-6.1-terra", "기본값")
def test_load_task_records_the_model_source(tmp_path): ...        # 작업 파일에 model 없음, environ OPENAI_MODEL → task.model_source == "env"
def test_task_from_args_takes_env_model(): ...
def test_omit_is_a_valid_effort(): ...                            # reasoning_effort: omit이 TaskError 없음
# test_settings.py
def test_vision_keys_are_read(tmp_path): ...                      # vision_model, vision_reasoning_effort, vision_max_side: 1024, vision_points: normalized_1000
def test_vision_points_must_be_a_known_mode(tmp_path): ...        # vision_points: percent → SettingsError
def test_vision_max_side_must_be_a_positive_integer(tmp_path): ...
# test_llm.py
def test_omit_sends_no_reasoning(): ...       # 가짜 client의 responses.create 인자에 "reasoning" 키가 없음
def test_effort_is_sent_as_before(): ...      # reasoning == {"effort": "low"}
def test_omit_for_one_call(): ...             # respond(..., effort="omit") → reasoning 없음
```

- [ ] **2단계:** `pytest tests/test_task.py tests/test_settings.py tests/test_llm.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.**
  - `agent/task.py`: `model_choice(file_value, settings, environ)`. `load_task(path, defaults=None, environ=None)`, `task_from_args(goal, exe, permission_level=3, defaults=None, environ=None)`. `environ`이 None이면 env를 보지 않습니다(지금의 호출과 테스트가 그대로 돌게). `Task.model_source` 필드를 끝에 더합니다.
  - `agent/settings.py`: `KEYS`에 네 키를 더하고 `NUMBER_KEYS`에 `vision_max_side`를 더합니다. `vision_points`는 `agent.vision.POINT_MODES`를 import하지 않고 같은 값을 `settings.py`에 둡니다(작업 1과 순서를 묶지 않으려고). 작업 3에서 두 상수가 같은지 테스트합니다.
  - `agent/llm.py`: `respond`에서 실제로 쓸 effort가 `"omit"`이면 `reasoning` 키를 빼고 부릅니다.
  - `settings.example.yaml`: 네 키에 설명 주석을 더하고, `reasoning_effort` 설명에 `omit`을 적습니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 3: 연결 정보와 모델 객체 (w1, 작업 1 수락 뒤)

**파일:**
- 고침: `agent/llm.py`
- 테스트: `tests/test_llm.py`

**인터페이스:** C2를 만듭니다. C1의 `Vision`, C3의 `Task.model_source`를 씁니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.** `make_client`는 받은 `(api_key, base_url)`를 기록하는 가짜입니다.

```python
def test_no_vision_model_means_one_model_mode(): ...              # vision is None, vision_source is None
def test_vision_model_from_env_uses_text_key_and_url(): ...
    # environ {OPENAI_API_KEY:k, OPENAI_BASE_URL:u, VISION_MODEL:v} → 비전 client가 (k, u)로 만들어짐, vision_source == "env"
def test_vision_key_and_url_override_the_text_ones(): ...        # VISION_API_KEY, VISION_BASE_URL가 쓰임
def test_settings_vision_model_beats_env(): ...                  # vision_source == "설정 파일"
def test_vision_url_without_vision_model_is_refused(): ...       # VISION_BASE_URL만 → ModelSetupError, 메시지에 "VISION_MODEL"
def test_blank_vision_model_with_vision_key_is_refused(): ...    # VISION_MODEL "  " + VISION_API_KEY → ModelSetupError
def test_vision_settings_reach_the_vision_object(): ...          # vision_max_side 1024, vision_points normalized_1000이 Vision에 전달
def test_vision_reasoning_only_when_set(): ...                   # VisionReader가 보낸 인자에 reasoning 없음 / 설정하면 있음
def test_vision_reader_sends_one_user_message_with_a_high_detail_image(): ...
    # input == [{"role":"user","content":[{"type":"input_text",...},{"type":"input_image","image_url":..., "detail":"high"}]}], tools 인자 없음
def test_start_line_names_sources_not_models(): ...
    # "텍스트 모델: env · 비전 모델: env". 모델 이름, 주소, 키가 들어가지 않음
def test_start_line_notes_ignored_image_mode(): ...              # task.image_mode "off" + 비전 → image_mode_ignored, 줄에 "image_mode"
def test_point_modes_match_settings(): ...                       # agent.vision.POINT_MODES == agent.settings의 값
```

- [ ] **2단계:** `pytest tests/test_llm.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** `OpenAIResponder`를 만드는 곳은 `text_responder` 하나로 모읍니다. `make_client(api_key, base_url)`는 `base_url`이 None이면 넘기지 않습니다. `image_mode_ignored`는 비전이 있고 `task.image_mode != "user_message"`일 때 참입니다. `start_line`의 꼴은 `텍스트 모델: <출처> · 비전 모델: <출처 또는 "없음">`이고, 무시했으면 ` · image_mode 무시(나눈 방식)`을 붙입니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 4: 실행 시작 경로 연결 (w1, 작업 3과 작업 5 수락 뒤)

**파일:**
- 고침: `run.py`, `view.py`, `viewer/run_manager.py`, `check.py`는 고치지 않음(작업 8)
- 새로 만듦: `.env.example`(env 이름 여섯 개와 한 줄 설명, 값은 비움)
- 테스트: `tests/test_run.py`, `tests/test_view.py`, `tests/test_run_manager.py`

**인터페이스:** C2의 `build_models`, `start_line`, `ModelSetupError`와 C4의 `Toolbox(vision=...)`를 씁니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_run.py
def test_run_passes_env_to_task_loading(): ...      # OPENAI_MODEL이 있으면 task.model_source == "env"
def test_run_prints_the_start_line(): ...           # 출력에 "텍스트 모델: " 줄, 모델 이름 없음
def test_run_refuses_a_broken_vision_setup(): ...   # VISION_BASE_URL만 → 종료 코드 2(다른 설정 오류와 같은 `_fail`), 메시지에 "VISION_MODEL", 실행 기록 없음
def test_run_gives_the_toolbox_the_vision(): ...    # 나눈 방식이면 Toolbox가 vision=을 받음
# test_run_manager.py
def test_model_factory_returns_models_and_toolbox_gets_vision(): ...
def test_broken_vision_setup_fails_the_start(): ... # RunStartFailed, 메시지에 "VISION_MODEL"
```

- [ ] **2단계:** `pytest tests/test_run.py tests/test_view.py tests/test_run_manager.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.**
  - `run.py`: `_resolve_task`가 `environ=os.environ`을 넘깁니다. 모델 쪽 실행이면 기록을 만들기 전에 `build_models`를 부르고, `ModelSetupError`면 `_fail`로 끝냅니다. 실행 시작 때 `start_line`을 출력합니다. `meta`에 `vision`(참/거짓)을 더합니다. `run.json`에 비전 모델 이름은 넣지 않습니다.
  - `view.py`의 `openai_model(task)`가 `Models`를 돌려줍니다. `RunManager`는 `model_factory(task)`의 결과에서 `.text`를 루프에 넘기고, `toolbox_factory`에 `vision`을 키워드 인자로 넘깁니다(`default_toolbox(..., vision=None)`). `ModelSetupError`는 `RunStartFailed`로 바꿉니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 5: `look`이 비전 모델에게 묻기 (w3)

**파일:**
- 고침: `agent/tool_schemas.py`, `agent/tools.py`, `agent/screen_tools.py`
- 테스트: `tests/test_vision_tools.py`(새)

**인터페이스:** C4와 C5(`asked_by: "look"`)를 만듭니다. C1의 `Vision.read`, `VisionAnswer`를 씁니다. 작업 1 수락 전에는 같은 모양의 가짜(`read`가 정해진 `VisionAnswer`를 돌려줌)로 테스트합니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.** 지금의 `tests/test_tools.py`가 쓰는 가짜 데스크톱(`tests/fake_cli.py`)을 그대로 씁니다.

```python
def test_without_vision_look_schema_is_unchanged(): ...       # look의 properties == {"mark"}
def test_with_vision_look_requires_a_question(): ...          # properties에 "question", required에 "question"
def test_look_with_vision_returns_the_answer(): ...
    # 결과 == 지금의 look 결과 + "vision": answer.as_result(); Vision.read가 (그 캡처, question, 그려진 번호들)로 불림
def test_look_with_vision_works_when_image_mode_is_off(): ... # task.image_mode "off"여도 거절되지 않음
def test_look_emits_a_vision_event(): ...                     # C5 꼴, asked_by "look", capture가 결과의 capture와 같음
def test_vision_error_still_returns_the_capture(): ...        # answer.error "TimeoutError" → ok True, capture 있음, vision.error
def test_captures_are_not_attached_for_the_text_model(): ...  # 나눈 방식에서 take_attachments() == []
def test_vision_usage_is_counted_and_reset(): ...             # look 두 번(토큰 100, 50) → take_vision_usage() == (2, 150), 다시 → (0, 0)
```

- [ ] **2단계:** `pytest tests/test_vision_tools.py`. 기대: 실패.
- [ ] **3단계: 구현합니다.**
  - `tool_schemas.py`: `LOOK_QUESTION` 설명과 함께 나눈 방식의 `look` 정의를 만들고, `definitions_for(level, vision)`를 둡니다. `Toolbox.definitions()`가 이것을 씁니다. 한 모델 방식의 정의는 글자 하나 바꾸지 않습니다.
  - `screen_tools.py`: 나눈 방식에서는 `image_mode`를 보지 않습니다(`look`의 거절, `_capture_current`의 `off` 검사). 캡처는 기록(`capture` 이벤트)에 남기되 `_attachments`에는 넣지 않습니다. `look`은 캡처 뒤 `self._vision.read(shot, arguments["question"], 그려진 번호)`를 부르고 `vision` 이벤트를 냅니다. 그려진 번호는 그 캡처를 그릴 때 쓴 `marks`의 키입니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과(한 모델 방식 테스트 포함).

### 작업 6: 동작 뒤 자동 캡처와 질문 거절의 캡처 (w3, 작업 1 수락 뒤)

**파일:**
- 고침: `agent/tools.py`, `agent/screen_tools.py`
- 테스트: `tests/test_vision_tools.py`

**인터페이스:** C5(`asked_by: "mismatch" | "unconfirmed"`)를 냅니다. C1의 `auto_question`을 씁니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_mismatch_asks_vision_about_the_target(): ...
    # expected_met False → 결과에 "vision", Vision.read의 question == auto_question("mismatch", expect, 동작 전 대상)
def test_auto_capture_is_cropped_around_the_target(): ...   # 동작 전 대상 bounds가 있으면 캡처가 cropped
def test_auto_capture_without_a_target_uses_the_whole_window(): ...
def test_unconfirmed_asks_vision_too(): ...                 # asked_by "unconfirmed"
def test_vision_timeout_after_an_action_keeps_the_result(): ...
    # Vision.read가 error "TimeoutError" → 동작 결과(ok, effects, expected_met) 그대로, vision.error만 더함
def test_question_refusal_capture_does_not_call_vision(): ...  # 정책 거절: capture는 있음, read 0번
def test_one_model_mode_auto_capture_is_unchanged(): ...     # vision 없음 → 지금처럼 첨부되고 "vision" 키 없음
```

- [ ] **2단계:** `pytest tests/test_vision_tools.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** 나눈 방식이면 `_before_state`에서 `_picture_target`으로 대상의 `(창, bounds)`를 함께 기억합니다(기대가 창 기대여도). 자동 캡처는 그 bounds를 `around`로 줘서 찍고, `auto_question(reason, expect, 동작 전 대상 요소)`로 묻습니다. 동작 전 대상 요소는 `preview`가 돌려준 대상(역할, 이름)입니다. 결과에 `vision`을 더하고 이벤트를 냅니다. `_refuse_for_question`의 캡처는 지금처럼 찍되 비전을 부르지 않습니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 7: 뷰어에 비전의 질문과 답 보이기 (w2)

**파일:**
- 고침: `viewer/static/app.js`, `viewer/static/style.css`, 필요하면 `viewer/export.py`
- 테스트: `tests/test_server.py`, `tests/test_export.py`

**인터페이스:** C5의 `vision` 이벤트를 읽습니다. 작업 9 전에는 손으로 만든 `events.jsonl`로 확인합니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_vision_event_is_served_with_the_run(tmp_path): ...    # /api/runs/<id>/events에 vision 이벤트가 그대로
def test_export_keeps_vision_events(tmp_path): ...             # 내보낸 HTML 안의 이벤트에 vision이 있음
```

- [ ] **2단계:** `pytest tests/test_server.py tests/test_export.py`. 기대: 새 테스트 실패(이미 통과하면 그 사실을 보고하고 3단계로).
- [ ] **3단계: 구현합니다.** 캡처를 보여 주는 칸에서 같은 `capture` id의 `vision` 이벤트를 찾아 `asked_by`, 질문, 답, `unsure`, `error`를 캡처 옆에 보여 줍니다. `points`는 캡처 위에 점과 `label`로 그립니다. 좌표는 캡처 이미지 픽셀이므로 지금의 박스 그리기와 같은 배율 계산을 씁니다. 글은 `textContent`로 넣습니다(앱의 글이 HTML로 해석되지 않게).
- [ ] **4단계:** `pytest`. 기대: 전체 통과. 화면은 오케스트레이터가 헤드리스 Chrome으로 확인합니다(1600px, 390px, 콘솔 오류 0).

### 작업 8: `check.py`의 비전 점검 (w2, 작업 3 수락 뒤)

**파일:**
- 고침: `check.py`, `agent/checks.py`
- 테스트: `tests/test_check.py`

**인터페이스:** C2의 `vision_setup`, `text_responder`와 C3의 `model_choice`를 씁니다. `Deps.make_client`는 `(api_key, base_url)`를 받게 바꿉니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
def test_text_model_check_uses_env_model(): ...              # OPENAI_MODEL이 출처로 "env"
def test_split_mode_skips_the_text_image_check(): ...       # LLM 그림 입력: skipped, 이유 "나눈 방식"
def test_vision_call_reads_the_digits(): ...                # 비전 호출: passed
def test_vision_points_pixel_is_recommended(): ...          # 답이 표시 위치의 픽셀 → passed, detail "권장 vision_points: pixel"
def test_vision_points_normalized_is_recommended(): ...     # 답이 0~1000 → "권장 vision_points: normalized_1000"
def test_vision_points_wrong_fails(): ...                   # 둘 다 아님 → failed
def test_one_model_mode_has_no_vision_items(): ...          # 비전 항목 없음, 지금의 세 항목 그대로
def test_check_output_has_no_model_names_or_urls(): ...     # 출력에 모델 이름, 주소, 키가 없음
def test_ignored_image_mode_is_noted(): ...                 # 설정 image_mode off + 비전 → 한 줄 알림
```

- [ ] **2단계:** `pytest tests/test_check.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** 비전 점검은 `Vision`을 거치지 않고 `VisionReader.ask`를 직접 불러 줄이기와 좌표 변환 없이 보낸 그림 그대로의 좌표를 받습니다(그래야 두 좌표 규칙을 구별할 수 있음). 좌표 그림은 `agent/checks.py`에 함수로 둡니다: 1000x600 흰 그림의 정해진 위치(무작위)에 빨간 원 하나. 허용 오차는 픽셀로 40입니다. 점검 그림은 긴 변이 1280을 넘지 않게 만듭니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 9: 루프, 기록, 요약 (w3, 작업 6 수락 뒤)

**파일:**
- 고침: `agent/loop.py`, `agent/recorder.py`, `agent/summary.py`
- 테스트: `tests/test_loop_vision.py`(새), `tests/test_recorder.py`, `tests/test_summary.py`

**인터페이스:** C4의 `vision_enabled`, `take_vision_usage`를 쓰고 C6을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.** 대본 모델은 `tests/test_loop_stages.py`의 방식을 따릅니다.

```python
# test_loop_vision.py
def test_split_mode_sends_no_image_to_the_text_model(): ...   # 모든 respond 호출의 input에 input_image가 없음
def test_split_mode_adds_the_vision_paragraph(): ...          # instructions에 비전 문단, 한 모델 방식에는 없음
def test_vision_tokens_count_toward_the_budget(): ...         # token_budget 1000, 비전 900 + 모델 200 → 다음 호출 전에 상한
def test_vision_calls_do_not_count_toward_the_stage_limit(): ...
def test_stats_hold_vision_counts_only_in_split_mode(): ...   # stats["vision_calls"], ["vision_tokens"] / 한 모델 방식은 키 없음
# test_recorder.py
def test_vision_event_is_recorded_with_a_step(): ...          # step None → 현재 스텝
def test_run_json_gets_vision_counts(): ...                   # vision_calls, vision_tokens
# test_summary.py
def test_summary_has_a_vision_line_in_split_mode(): ...       # "비전 호출 2 · 비전 토큰 1500", 질문·답 글 없음
def test_summary_has_no_vision_line_without_vision(): ...
```

- [ ] **2단계:** `pytest tests/test_loop_vision.py tests/test_recorder.py tests/test_summary.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.**
  - `loop.py`: 나눈 방식이면 `Transcript`를 `"off"`로 만들고, `SYSTEM_PROMPT` 뒤에 `VISION_PROMPT`(설계 3절 "텍스트 모델에게 주는 안내"의 세 줄을 영어로)를 붙입니다. 도구를 하나 실행할 때마다 `take_vision_usage()`를 읽어 토큰을 `self._tokens`에 더하고 따로도 셉니다. 단계 호출 수는 건드리지 않습니다.
  - `recorder.py`: `vision` 이벤트를 기록하고 `step`이 없으면 채웁니다. `finish`가 `stats`의 두 키를 `run.json` 최상위에 씁니다.
  - `summary.py`: 두 키가 있을 때만 한 줄을 더합니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 오케스트레이터: 통합과 문서

- [ ] 모든 배정이 수락되면 전체 `pytest`를 다시 돌립니다. 대본 텍스트 모델과 가짜 비전으로 `look`과 불일치 캡처가 이어지는 실행 하나를 맞물려 확인합니다.
- [ ] `README.md`에 env 여섯 개, 나눈 방식, `check.py`의 비전 항목을 적고, `CLAUDE.md`의 llm-agent 절에 한 줄을 더합니다.
- [ ] 설계의 상태를 갱신합니다.
- [ ] 사용자와 할 일: 실제 모델로 `check.py`(비전 좌표 권장 값 확인), 가상 MES 실행 한 번. 사내 비전 모델이 `input_image`를 받는지는 회사에서 사용자가 확인합니다.
