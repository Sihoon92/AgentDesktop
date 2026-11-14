# 단계로 나눠 실행하기 구현 계획

- 날짜: 2026-11-14
- 상태: 사용자 승인(2026-11-14). 작업 1~5가 끝났습니다(창 없는 테스트 1652개 통과, 대본 모델로 맞물림 확인). 작업 6 가운데 실제 창 테스트(대본 모델)는 돌렸습니다(2026-11-15, 71개 모두 통과. 테스트 4개를 고쳤고, 창이 열림·닫힘의 기대는 그림을 보지 않고 판정하도록 고쳤습니다). 실제 모델 실행은 사용자가 합니다. 결과와 점검표는 [`../checklists/2026-11-14-staged-run.md`](../checklists/2026-11-14-staged-run.md)입니다.
- 설계: [단계로 나눠 실행하기 설계](../specs/2026-11-14-staged-run-design.md) (사용자 승인 2026-11-14). 이 계획은 설계를 근거로 하므로 구현하는 사람은 둘 다 읽습니다.

**목표:** 모델이 지시를 단계로 나누고, 단계마다 호출 수를 제한하며, 한도에서 사람에게 묻고, 끝난 단계의 기록을 줄이고, 헛걸음이 있었던 단계를 지식 후보로 올리고, 뷰어가 단계의 진행을 보여 주게 합니다.

**구조:** 단계의 상태(`agent/stages.py`)와 모델에게 보낼 기록(`agent/history.py`)을 입출력 없는 모듈 둘로 새로 만들고, 루프(`agent/loop.py`)가 둘을 씁니다. 등록, 기록, 요약, 뷰어는 루프가 내는 이벤트와 단계 기록을 받아 씁니다. 화면을 다루는 도구(`agent/tools.py` 등)의 동작은 바꾸지 않습니다.

**쓰는 것:** Python 표준 라이브러리, 지금의 의존성(PyYAML, Pillow, openai). 뷰어는 지금처럼 순수 HTML, CSS, JS입니다. 더하는 의존성은 없습니다.

## 전체에 걸친 조건

- `tests\test_application_agnostic.py` 통과. `agent/`와 `viewer/`의 코드에 앱 이름, 창 제목, 화면의 글을 넣지 않습니다.
- 단계의 글(`goal`, `done_when`, `note`), 한도 질문의 글, 지식 후보는 `events.jsonl`에만 둡니다. `run.json`과 실행 요약에는 수만 넣습니다.
- 수치(설계 그대로): 단계 한도 8, 단계 수 1~12, 계획 고치기 3번, 지시 실행의 전체 호출 60, 토큰 상한 600,000, 계획 전 추론 강도 `medium`.
- 한도를 넘으면 사람의 답 없이 진행하지 않습니다. 답이 없으면 실행이 멈춥니다.
- `finish`는 한도와 상한에 닿아도 받습니다.
- 승인하지 않은 지식은 저장하지 않습니다. 지식에 좌표를 넣지 않습니다.
- 계획이 없는 예전 기록이 뷰어와 `view.py --summary`에서 지금처럼 보여야 합니다. 기록 형식 번호(`RUN_FORMAT`)는 그대로입니다.
- 작업자는 커밋하지 않고, 실제 창을 띄우는 실행과 실제 모델 호출을 하지 않습니다. 둘은 사용자 확인 뒤 따로 합니다.
- 테스트는 `examples/llm-agent`에서 conda 환경 `AgentDesktop`의 파이썬으로 돌립니다. 제출 전 `python -m pytest -q` 전체, 실패 0.

## 검토에서 특히 볼 것

설계가 뜻하지만 한 작업의 테스트만으로는 드러나지 않기 쉬운 경우입니다. 각 줄의 테스트는 표시한 작업에 넣습니다.

1. **모델이 계획을 세우지 않거나 단계 하나로 뭉뚱그릴 때.** 계획 없이 앱을 바꾸는 호출은 거절되고 전체 호출 수에 잡혀 끝이 있어야 합니다. 단계 하나짜리 계획은 한도 질문으로 이어져야 합니다. (작업 2)
2. **한도 질문에 답이 없을 때.** 뷰어가 닫혔거나 터미널이 아니면 실행이 `stopped`로 끝나고 지금 단계가 `stopped`로 기록돼야 합니다. 조용히 계속되면 안 됩니다. (작업 2)
3. **줄인 기록의 짝.** 어떤 순서로 줄여도 도구 호출과 그 결과가 둘 다 있거나 둘 다 없어야 합니다. 한쪽만 남으면 API가 거절합니다. (작업 1)
4. **끝난 단계를 바꾸는 계획 고치기.** 이미 완료한 단계를 지우거나 고치는 `plan`은 거절돼야 합니다. (작업 1)
5. **질문이 없었고 사용자가 후보를 모두 거절한 실행.** 아무것도 저장되지 않아야 하고, 단계의 글이 `run.json`과 요약에 한 글자도 없어야 합니다. (작업 3)

## 작업과 순서

| 작업 | 누가 | 무엇 | 먼저 끝나야 하는 것 |
| --- | --- | --- | --- |
| 0 | w1 | 그림만 바뀐 변화의 판정(끝남, 별도 배정) | — |
| 1 | w3 | `agent/stages.py`, `agent/history.py` | — |
| 2 | w1 | 루프, 도구 정의, 설정, 근거 확인 | 0, 1 |
| 3 | w3 | 등록, 터미널 질문, 기록의 수, 요약 | 1 (2와 나란히, 맞물림 확인은 2 뒤) |
| 4 | w2 | 뷰어와 실행 시작 설정 | 0의 결과 키 이름 (2와 나란히) |
| 5 | 오케스트레이터 | 검토, 맞물림 확인, README, `CLAUDE.md`, 점검표 | 2, 3, 4 |
| 6 | 사용자 확인 뒤 | 실제 창과 실제 모델로 확인 | 5 |

같은 파일을 두 작업이 고치지 않게 나눴습니다. 겹치는 곳은 작업마다 적었습니다.

---

### 작업 1: 단계의 상태와 줄인 기록 (w3)

**파일:**
- 새로: `agent/stages.py`, `agent/history.py`, `tests/test_stages.py`, `tests/test_history.py`
- 고치지 않음: 그 밖의 모든 파일

**내놓는 것 (작업 2, 3이 이 이름과 모양을 씁니다):**

```python
# agent/stages.py
MIN_STAGES, MAX_STAGES, MAX_REVISIONS = 1, 12, 3
SIGNALS = ("mismatch", "abandoned_window", "looked_twice", "limit_question")

class StageError(ValueError):
    code: str   # "bad_plan", "too_many_stages", "changes_done_stage", "too_many_revisions", "no_plan", "nothing_to_finish"

class StagePlan:
    def __init__(self, call_limit: int): ...
    exists: bool                      # property
    def set(self, stages: list[dict]) -> dict: ...
        # 첫 계획이거나 고치기. stages는 [{"goal": str, "done_when": str}, ...] 전체 목록.
        # 고칠 때 앞쪽의 완료한 단계들은 글이 그대로여야 한다. 아니면 StageError.
        # 돌려주는 것: plan 이벤트 {"type": "plan", "revision": int, "stages": [{"n", "goal", "done_when", "status"}]}
    def current(self) -> dict | None: ...        # {"n", "goal", "done_when", "calls", "limit"}
    def count_call(self) -> None: ...            # 지금 단계의 호출 +1. 계획이 없거나 다 끝났으면 아무것도 안 함
    def note_action(self, tool: str, arguments: dict, result: dict) -> None: ...
    def at_limit(self) -> bool: ...              # 지금 단계의 calls >= limit
    def extend(self) -> dict: ...                # limit += call_limit, 한도 질문 +1. stage 이벤트(status "limit")를 돌려줌
    def allow_revisions(self) -> None: ...       # 사람이 계속을 고른 뒤: 고칠 수 있는 횟수를 MAX_REVISIONS만큼 더 줌
    def complete(self, kind: str, note: str) -> list[dict]: ...
        # kind는 "text" 또는 "picture". 지금 단계를 done으로, 다음 단계가 있으면 started로.
        # 돌려주는 것: stage 이벤트들(done, 그리고 started)
    def stop(self) -> dict | None: ...           # 지금 단계를 stopped로. stage 이벤트
    def all_done(self) -> bool: ...
    def unfinished(self) -> list[int]: ...       # 끝나지 않은 단계 번호
    def records(self) -> list[dict]: ...         # 등록에 쓰는 단계 기록(아래)
    def stats(self) -> dict: ...                 # 수만: stage_count, stages_done, stage_calls(list), limit_questions, plan_revisions
```

- `stage` 이벤트: `{"type": "stage", "n": int, "status": "started"|"done"|"limit"|"stopped", "calls": int, "limit": int}`. `done`에는 `"kind"`와 `"note"`가 더 있습니다. 이벤트의 `step`은 루프가 붙입니다.
- 어느 단계가 진행 중인지: `plan` 이벤트의 `stages[].status`(`done`, `active`, `pending`)가 그 시점의 기준입니다. 첫 계획과 고친 계획 뒤에는 `stage started`가 따로 나오지 않습니다. `started`는 앞 단계를 `complete`했을 때만 나옵니다.
- 단계 기록(`records()`의 한 항목): `{"n", "goal", "done_when", "status", "calls", "kind", "note", "limit_questions", "actions": [{"tool", "intent", "expected_met", "refused_code", "opened": int, "closed": int}], "signals": [...]}`. `actions`에는 화면의 글을 넣지 않고 수만 넣습니다(창을 연 수, 닫은 수).
- 신호의 뜻(`note_action`이 본 결과로 셉니다):
  - `mismatch`: 그 단계에 `expected_met`가 `False`인 동작이 있었다.
  - `abandoned_window`: 창을 연 동작 바로 다음의 앱 변경 동작이 그 창을 닫거나 다른 창으로 바꿨다(`effects.windows_opened`와 `windows_closed`의 창 id로 판단. 연 창 안에서 한 동작이 그 사이에 없어야 한다).
  - `looked_twice`: 읽기만 하는 호출(`observe`, `look`, `find_cells`, `wait_for_text`)이 그 단계에 두 번 이상.
  - `limit_question`: 한도 질문이 있었다.

```python
# agent/history.py
class Transcript:
    def __init__(self, first_message: dict, image_mode: str): ...
    def add_response(self, output: list) -> None: ...          # 모델 응답의 output 항목들(한 턴의 시작)
    def add_result(self, call_id: str, name: str, output: dict, captures: list) -> None: ...
    def add_note(self, text: str) -> None: ...                 # 사용자 역할의 글 한 줄(한도 질문의 답 등)
    def close_stage(self, summary: str) -> None: ...           # 지난 close 뒤의 턴들을 summary 한 줄로 바꿈
    def view(self) -> list: ...                                # 지금 모델에게 보낼 input 항목들
```

`view()`의 규칙:
- 결과 가운데 `"observation"` 키를 가진 **가장 최근** 것만 그 글을 온전히 둡니다. 그 전 것들은 `observation`을 `"(earlier observation omitted: N lines)"`로 바꿉니다. 결과의 나머지 키(`effects`, `expected_met`, `refused` 등)는 둡니다.
- 그림은 가장 최근의 관찰 결과와 그 뒤의 결과에 붙은 것만 보냅니다. `look`의 그림은 그 뒤에 "거절되지 않은 앱 변경 도구의 결과"가 나오기 전까지 보냅니다. 앱 변경 도구는 `agent.tool_schemas.READ_ONLY_TOOLS`에 없는 이름입니다.
- `ask`의 호출과 결과는 줄이지 않고, `close_stage`로도 없어지지 않습니다. `plan`의 호출과 결과도 없어지지 않습니다.
- `close_stage`는 턴 단위로 통째로 뺍니다(응답의 항목 전부와 그 결과 전부). 한 턴에 `ask`나 `plan`이 섞여 있으면 그 턴은 남깁니다.
- 가장 최근 관찰을 가진 턴은 닫힌 단계의 것이어도 더 새로운 관찰이 나올 때까지 통째로 남습니다(그 관찰의 글과 그림도 온전히). `stage_done`은 화면을 바꾸지 않으므로 그 관찰은 다음 단계에서도 지금의 화면이고 번호와 ref가 유효합니다. 빼면 단계마다 관찰 호출이 하나씩 더 듭니다. 새 관찰이 나오면 그 턴은 빠집니다.
- 결과 항목은 `agent.images.image_items(call_id, output, captures, mode)`로 만듭니다(지금 루프가 쓰는 함수).

**테스트 (이름과 확인하는 것):**
- `test_stages.py`
  - `test_a_first_plan_starts_stage_one`: `set` 뒤 `current()["n"] == 1`, 이벤트의 `revision == 0`, 상태가 `active, pending, pending`.
  - `test_a_plan_outside_one_to_twelve_stages_is_refused`: 0개와 13개에서 `StageError`, `code`가 `bad_plan`, `too_many_stages`.
  - `test_a_stage_needs_both_texts`: `goal`이나 `done_when`이 비면 `bad_plan`.
  - `test_a_revision_keeps_done_stages_and_replaces_the_rest`: 단계 1 완료 뒤 3개를 4개로 고치면 단계 1은 그대로 done, `revision == 1`.
  - `test_a_revision_that_rewrites_or_drops_a_done_stage_is_refused`: `changes_done_stage`. (검토 4)
  - `test_a_fourth_revision_is_refused`: `too_many_revisions`.
  - `test_calls_count_on_the_current_stage_only`, `test_the_limit_is_reached_at_the_call_limit`(8에서 `at_limit()` 참, 7에서 거짓), `test_extend_adds_one_more_allowance_and_counts_a_limit_question`.
  - `test_completing_the_last_stage_leaves_nothing_current`, `test_complete_without_a_plan_is_refused`(`no_plan`), `test_complete_after_all_done_is_refused`(`nothing_to_finish`).
  - `test_stats_hold_numbers_only`: `stats()`의 값이 정수와 정수 목록뿐이고 단계의 글이 없음.
  - 신호 넷 각각: 생기는 경우와 생기지 않는 경우 하나씩(예: 창을 열고 그 안에서 동작한 뒤 닫으면 `abandoned_window`가 아님).
  - `test_records_carry_no_screen_text`: `actions`의 값에 `effects`의 이름들이 들어가지 않음.
- `test_history.py`
  - `test_only_the_latest_observation_is_sent_in_full`, `test_an_omitted_observation_keeps_its_effects`.
  - `test_images_of_older_observations_are_not_sent`, `test_a_look_image_stays_until_the_app_changes`, `test_a_refused_action_does_not_drop_the_look_image`.
  - `test_questions_and_answers_are_never_trimmed`, `test_the_plan_turn_survives_closing_a_stage`.
  - `test_closing_a_stage_replaces_its_turns_with_one_note`.
  - `test_the_latest_observation_survives_closing_its_stage`, `test_a_newer_observation_drops_the_closed_turn_that_was_kept`.
  - `test_every_call_keeps_its_output_and_every_output_its_call`: 무작위로 만든 200개의 턴 순서(관찰, 동작, 거절, `look`, `ask`, 단계 닫기)에서 `view()`의 `function_call`과 `function_call_output`의 `call_id` 집합이 같음. (검토 3)
  - `test_reasoning_items_leave_with_their_turn`: 응답에 `reasoning` 항목이 있을 때 턴이 빠지면 함께 빠지고, 남으면 함께 남음.
  - `test_image_mode_off_sends_no_image`.

**하지 않는 것:** 루프와 연결하지 않습니다. 실제 API 형식 확인은 작업 6입니다.

---

### 작업 2: 루프에 단계를 넣기 (w1)

**파일:**
- 고침: `agent/loop.py`, `agent/tool_schemas.py`, `agent/task.py`, `agent/settings.py`, `agent/llm.py`, `agent/asking.py`(근거 확인과 한도 질문의 기록), `settings.example.yaml`, `run.py`(설정 키 전달만)
- 테스트: `tests/test_loop.py`와 관련 테스트, 새 `tests/test_loop_stages.py`
- 고치지 않음: `agent/registration.py`, `agent/recorder.py`, `agent/summary.py`, `agent/ask.py`(작업 3), `viewer/`(작업 4)

**쓰는 것:** 작업 1의 `StagePlan`, `Transcript`.

**내놓는 것:**
- 도구 둘(권한 단계 0, `READ_ONLY_TOOLS`에 넣음. 앱을 바꾸지 않으므로 동작 전 승인도 거치지 않음):
  - `plan(stages: [{goal, done_when}])`
  - `stage_done(marks: [int] | null, capture: str | null, note: str)`
- `Task`의 새 값: `stage_call_limit: int = 8`, `plan_reasoning_effort: str = "medium"`. 기본값 변경: `token_budget` 600,000, `ARGS_MAX_STEPS` 60. 넷 다 작업 파일의 키로 받고, `stage_call_limit`과 `plan_reasoning_effort`는 설정 파일(`agent/settings.py`의 `KEYS`)에서도 받습니다.
- `OpenAIResponder.respond(instructions, items, tools, effort=None)`: `effort`가 있으면 그 호출만 그 강도로 부릅니다.
- `Toolbox.evidence(marks, capture) -> dict`: `{"ok": True, "kind": "text"|"picture"}` 또는 거절(`{"ok": False, "refused": ..., "refused_code": ...}`). 확인하는 것: 둘 중 정확히 하나가 주어짐, 번호가 모두 지금의 표시 번호에 있음, 캡처가 신선함(지금의 `_fresh_capture`), 정책이 막힌 상태가 아님.
- `Toolbox.stage_records`: 실행이 끝날 때 루프가 `plan.records()`를 넣습니다(`finish_note`처럼). 작업 3의 등록이 읽습니다.
- `Toolbox.note_exchange(question: dict, answer)`: 하니스가 만든 질문(한도 질문)과 답을 지금의 `exchanges()`에 넣습니다.
- `RunResult.stats: dict | None`: `plan.stats()`에 `max_call_tokens`를 더한 것. 작업 3의 기록이 읽습니다.
- `model` 이벤트에 `call_tokens`(그 호출의 토큰)를 더하고, 계획이 있으면 `stage`(지금 단계 번호), `stage_calls`(이 호출까지 센 그 단계의 호출 수), `stage_limit`(그 단계의 지금 한도)을 더합니다. `ask`만 있는 턴에는 `stage_calls`가 늘지 않은 값으로 들어갑니다. 뷰어가 진행 중인 단계의 `3/8`을 이 값으로 그립니다.
- 이벤트: `plan`, `stage`(작업 1의 모양에 `step`을 붙임), 한도 질문(`question`, `kind: "stage_limit"`, `stage`, `calls`, `intents`), 계획 고치기 초과 질문(`question`, `kind: "plan_limit"`, `stage`, `revisions`).

**루프의 순서 (한 턴):**
1. 상한 검사: 센 호출 수가 `max_steps` 이상이거나 누적 토큰이 `token_budget` 이상이면, `finish`만 있는 도구 목록과 "상한에 닿았으니 지금 판정을 내라"는 한 줄을 붙여 한 번 더 부르고, 그 결과로 끝냅니다. `finish`가 아니면 `stopped`.
2. 한도 검사: `plan.at_limit()`이면 한도 질문을 합니다(아래).
3. 모델 호출: `model.respond(SYSTEM_PROMPT, transcript.view(), tools, effort=...)`. 계획이 없을 때만 `effort=task.plan_reasoning_effort`.
4. `ask`만 있는 턴이 아니고 계획이 있으면 `plan.count_call()`.
5. 호출 실행:
   - `plan`: `StagePlan.set`. `StageError`는 거절로 돌려주고, `too_many_revisions`면 `kind: "plan_limit"` 질문을 합니다. 답의 해석은 한도 질문과 같습니다(중단이면 `stopped`, 그 밖은 `plan.allow_revisions()` 뒤 그 `plan` 호출은 거절로 돌려주어 모델이 다시 부르게 하고, 글이 있으면 전합니다). 성공하면 이벤트를 기록합니다.
   - `stage_done`: `toolbox.evidence` → `plan.complete` → 이벤트 기록 → `transcript.close_stage(...)`. 요약 한 줄은 단계 번호, `goal`, `note`, 그 단계에서 전달된 동작들의 `intent`입니다.
   - `finish`: `success`가 참인데 `plan.unfinished()`가 있으면 거절합니다. 계획이 아예 없으면 지금처럼 판정합니다(계획 없이 끝낼 수 있는 실행을 막지 않음). 그 밖은 지금과 같습니다.
   - 그 밖의 도구: 계획이 없고 이름이 `READ_ONLY_TOOLS`에 없으며 `launch_target`도 아니면 `refused_code: "needs_plan"`으로 거절합니다. 아니면 지금처럼 실행하고 `plan.note_action`에 결과를 넘깁니다.
6. 끝날 때: 멈춘 실행이면 `plan.stop()`의 이벤트를 기록하고, `toolbox.stage_records`와 `RunResult.stats`를 채웁니다.

**한도 질문:** `toolbox.ask({"kind": "stage_limit", "text": <고정 문장>, "stage": n, "calls": calls, "intents": [...]})`. 화면은 지금의 질문처럼 캡처를 붙입니다(`_show_question_screen`). 답의 해석: `accepted is False`면 중단(`stopped`, 이유는 단계 한도), 그 밖은 `plan.extend()` 뒤 계속. 답의 글이 있으면 `transcript.add_note`로 모델에게 전하고 `toolbox.note_exchange`로 남깁니다. 답이 없으면(`AgentStopped`) 지금처럼 멈춥니다.

**`SYSTEM_PROMPT`에 더할 것:** 첫 관찰 뒤 `plan`을 부를 것, 단계는 화면에서 확인되는 결과로 적을 것, 단계가 끝나면 근거와 함께 `stage_done`을 부를 것, 계획이 틀렸으면 `plan`을 다시 부를 것(끝난 단계는 그대로), 이전 관찰은 생략되니 필요한 것은 다시 관찰할 것.

**테스트 (`tests/test_loop_stages.py`, 대본대로 답하는 가짜 모델):**
- `test_an_app_changing_call_before_a_plan_is_refused`(`needs_plan`), `test_launch_observe_and_look_run_before_a_plan`.
- `test_a_run_without_any_plan_ends_at_the_call_ceiling`: 계획 없이 누르기만 반복하는 대본이 `max_steps`에서 끝남. (검토 1)
- `test_the_plan_call_uses_the_plan_effort_and_later_calls_do_not`.
- `test_stage_done_needs_evidence_in_the_latest_observation`: 없는 번호, 오래된 캡처, 둘 다 줌, 둘 다 안 줌에서 거절되고 단계가 그대로.
- `test_stage_done_is_refused_while_actions_are_blocked`.
- `test_calls_are_counted_per_stage_and_ask_turns_are_not`.
- `test_at_the_limit_the_person_is_asked_before_the_next_call`: 8번째 호출 뒤 모델을 부르기 전에 `stage_limit` 질문이 나옴. 단계 하나짜리 계획에서도 같음. (검토 1)
- `test_continue_gives_another_allowance`, `test_guidance_reaches_the_model_and_the_exchanges`, `test_stop_ends_the_run_and_marks_the_stage_stopped`.
- `test_an_unanswered_limit_question_stops_the_run`: 결과 `stopped`, `stage` 이벤트 `stopped`, 그 뒤 모델 호출 0번. (검토 2)
- `test_finish_with_unfinished_stages_is_refused_but_giving_up_is_not`.
- `test_finish_is_accepted_at_the_ceiling`: 상한에 닿은 뒤의 한 번에서 `finish`가 받아들여져 지금의 판정(작업 조건, 지식, 사람)을 탐.
- `test_the_ceiling_turn_offers_only_finish`.
- `test_a_closed_stage_is_one_line_in_the_next_request`: 다음 호출의 `items`에 그 단계의 관찰 글이 가장 최근 것 하나만 있고, 새 관찰 뒤의 호출에는 하나도 없음.
- `test_stats_and_stage_records_are_left_for_the_recorder_and_registration`.
- 지금의 `tests/test_loop.py`가 계획 없는 대본을 쓰는 곳은 대본에 `plan`을 넣어 고칩니다. 고친 테스트와 이유를 보고서에 적습니다.

---

### 작업 3: 등록, 터미널 질문, 기록의 수, 요약 (w3)

**파일:**
- 고침: `agent/registration.py`, `agent/ask.py`, `agent/recorder.py`, `agent/summary.py`
- 테스트: `tests/test_registration.py`, `tests/test_ask.py`, `tests/test_recorder.py`, `tests/test_summary.py`
- 겹침: `agent/summary.py`는 작업 0(w1)이 "확인 못 함"의 수를 더했습니다(수락됨). 그 위에 더합니다.

**쓰는 것:** `toolbox.stage_records`(작업 1의 단계 기록 목록), `RunResult.stats`, 이벤트 `plan`과 `stage`.

**할 것:**
- `register_after_run`: 초안을 만드는 조건을 "질문과 답이 있거나, 사람이 결과를 확인했거나, **신호가 있는 완료 단계가 있다**"로 바꿉니다. 초안 호출의 재료에 `"stages"`(신호가 있는 완료 단계의 기록)를 더합니다.
- `DRAFT_INSTRUCTIONS`: 단계 기록이 무엇인지, 헛걸음에서 다음 실행이 피할 것을 "상황 / 해결"로 적으라는 것, 질문에서 나온 것과 겹치면 하나만 적으라는 것을 더합니다. 좌표와 화면의 값에 대한 지금의 금지는 그대로입니다.
- 항목에 출처를 답니다: `ITEM_SCHEMA`에 `"stage"`(단계 번호 또는 null)를 더하고, 사람에게 보이는 항목(`_shown`)의 최상위에 `"origin": "question" | "discovered"`와 `"stage"`(번호 또는 null)를 넣습니다. 저장되는 지식의 `source`에도 `origin`이 남고, 스스로 알아낸 것이면 `stage`도 남습니다(`_source`).
- `TerminalAsker`: `stage_limit` 질문을 묻습니다. 단계 번호, 쓴 호출 수, 동작의 `intent`들을 보여 주고, 빈 줄은 계속, `q`는 중단, 그 밖의 글은 방법으로 받습니다.
- `RunRecorder`: `model` 이벤트를 기록할 때 `call_tokens`, `stage`, `stage_calls`, `stage_limit`을 남깁니다(지금은 `step`과 `tokens`만 남기고 버립니다). 동작 결과의 기록 항목(`RESULT_FIELDS`)에 `verdict`를 더합니다(작업 0이 만든 "확인 못 함" 판정의 이름. 지금은 기록에 남지 않습니다). `KEPT_EVENTS`에 `plan`과 `stage`를 더합니다. `finish(result)`가 `result.stats`를 `run.json`의 `stages`에 씁니다(수만).
- `summarize`: 줄을 더합니다. `단계: 4 · 완료 3 · 단계별 호출 3/7/1 · 한도 질문 1 · 계획 고침 0`, `호출당 토큰 최대: 9120`. `run.json`에 `stages`가 없는 예전 기록에서는 이 줄들이 나오지 않습니다. 계획 없이 끝난 새 실행은 `단계:` 줄 없이 `호출당 토큰 최대:` 줄만 나옵니다. 단계의 수는 실행이 끝날 때 `run.json`에 쓰이므로 끊긴 실행에는 두 줄 다 없습니다.

**테스트:**
- `test_a_run_with_no_question_but_a_detour_is_drafted`, `test_a_run_with_no_question_and_no_detour_drafts_nothing`(모델 호출 0번).
- `test_only_done_stages_with_a_signal_are_given_to_the_draft`.
- `test_a_discovered_item_is_shown_with_its_origin`, `test_a_saved_discovered_entry_records_its_origin`.
- `test_rejecting_every_item_saves_nothing`. (검토 5)
- `test_stage_texts_never_reach_run_json_or_the_summary`: 눈에 띄는 문자열을 `goal`, `done_when`, `note`에 넣은 실행을 기록하고 `run.json`과 요약 글에 그 문자열이 없음을 확인. `events.jsonl`에는 있음. (검토 5)
- `test_the_terminal_asks_at_a_stage_limit`: 빈 줄, `q`, 글 세 경우의 `Answer`.
- `test_plan_and_stage_events_are_kept`, `test_run_json_holds_stage_numbers_only`, `test_an_unconfirmed_verdict_is_recorded`.
- `test_the_summary_shows_stage_counts`, `test_a_run_without_stages_has_no_stage_line`.

---

### 작업 4: 뷰어와 실행 시작 설정 (w2)

**파일:**
- 고침: `viewer/static/index.html`, `style.css`, `app.js`, `chat.js`, `viewer/export.py`(단계 줄이 내보낸 HTML에 나오게, 필요한 만큼), `viewer/run_manager.py`(`start`와 `build_task`에 `stage_call_limit`), `viewer/server.py`(실행 시작 요청에서 그 값을 받는 것)
- 테스트: `tests/test_export.py`, `tests/test_run_manager.py`, `tests/test_server.py`에서 해당하는 것, 헤드리스 Chrome 확인 스크립트
- 고치지 않음: `agent/`

**쓰는 것 (이벤트의 모양):**
- `plan`: `{"type": "plan", "step", "revision", "stages": [{"n", "goal", "done_when", "status"}]}`
- `stage`: `{"type": "stage", "step", "n", "status", "calls", "limit"}`, `done`이면 `"kind"`, `"note"`
- `question`의 `kind: "stage_limit"`: `stage`, `calls`, `intents`, 캡처. `kind: "plan_limit"`: `stage`, `revisions`. 둘 다 같은 답 양식(계속, 중단, 글)을 씁니다.
- `model`: `{"type": "model", "step", "tokens", "call_tokens", "stage", "stage_calls", "stage_limit"}`. 뒤의 넷은 없을 수 있습니다.
- 동작 이벤트(`action`)에서 `expect`, `expected_met`, `effects`, `refused_code`, `capture`, `verdict`는 `result` 안에 있습니다. 지금의 뷰어는 이벤트 최상위에서 읽어 실제 기록에서 기대 판정 줄과 불일치 표시가 나오지 않았습니다(w2가 찾음). `result`에서 읽게 고칩니다.
- 등록 질문의 항목: `origin`
- 동작 결과의 "확인 못 함": `expected_met`이 `null`이고 `effects.picture.changed`가 참입니다(작업 3 뒤에는 `verdict: "unconfirmed"`도 기록에 남습니다). 캡처 이벤트의 이유에 `unconfirmed`가 더해졌습니다.
- 관찰하는 창이 바뀐 동작: `effects.observed_window`(`before`, `after` 창 id).

**할 것:**
- **단계 줄:** 캡처 바로 위. 단계마다 번호, `goal`, 상태(대기, 진행 중, 완료, 질문 중, 중단), 쓴 호출 수(`3/8`). 완료는 체크 표시. 진행 중인 단계가 한눈에 구분됩니다. 단계를 누르면 그 단계의 첫 스텝으로 타임라인이 옮겨지고, 완료한 단계는 근거의 종류와 `note`가 보입니다. 단계가 많아 한 줄에 안 들어가면 줄을 바꾸되, 1280×620에서 단계 여섯 개까지는 캡처를 가리지 않아야 합니다. 계획이 없는 기록에서는 단계 줄이 없습니다.
- **재생:** 타임라인에서 지난 스텝을 보면 단계 줄도 그 시점의 상태를 보여 줍니다.
- **타임라인:** 단계의 경계를 표시합니다.
- **계획을 고쳤을 때:** 단계 줄이 바뀌고 대화 칸에 "계획을 고침"이 남습니다.
- **한도 질문의 답 양식:** "계속", "중단" 버튼과 방법을 적는 글 칸. 글을 적고 보내면 방법으로 전달됩니다(`accepted: true`와 글). "중단"은 `accepted: false`. 지금의 답 양식처럼 잘리지 않아야 합니다.
- **등록 승인 양식:** 항목마다 출처(질문에서, 스스로 알아냄)를 표시합니다. 지식 보기에서도 `source.origin`이 있으면 보여 줍니다.
- **낱말:** 타임라인의 한 칸은 "스텝", 계획의 한 덩어리는 "단계"로 부릅니다. 지금 화면에서 타임라인의 칸을 "단계"라 부르는 글("단계 이동", "단계 정보", "단계 타임라인" 등)은 "스텝"으로 바꿉니다.
- **동작 결과:** "확인 못 함"을 일치, 불일치와 구분해 보여 주고, `observed_window`가 있으면 "관찰하는 창이 바뀜"을 보여 줍니다.
- **시작 양식의 "설정":** 단계별 호출 한도 칸(비우면 기본값). 요약 줄에도 나옵니다.

**검증:** 지금의 방식대로 헤드리스 Chrome(시스템 Chrome)과 꾸민 기록으로 확인합니다. 단계 셋의 실행이 진행되며 단계 줄이 바뀌는 것, 한도 질문에 세 가지로 답해 서버에 한 번씩 전달되는 것, 계획이 없는 예전 기록, 내보낸 HTML, 라이트와 다크, 1920×950부터 390px 폭까지 가로 스크롤 없음. 화면 그림을 남깁니다.

---

### 작업 5: 맞물림 확인과 문서 (오케스트레이터)

- 작업마다 코드와 보고서를 읽고 테스트를 다시 돌린 뒤 수락합니다.
- 맞물림: 대본 모델로 실행 하나를 끝까지 돌려(계획, 단계 완료, 한도 질문, 등록 승인) 기록을 뷰어로 열어 봅니다. 창은 가짜 CLI를 씁니다.
- `README.md`: 단계, 호출 한도, 한도 질문, 줄인 기록, 스스로 알아낸 것의 등록, 설정 키, 뷰어의 단계 줄.
- `CLAUDE.md`의 `examples/llm-agent` 절과 점검표.

### 작업 6: 실제 창과 실제 모델로 확인 (사용자 확인 뒤)

가상 MES에서 실제 모델을 부릅니다. 창을 띄우고 커서를 움직이며 토큰을 쓰므로 사용자 확인을 받고 합니다.

- 지난 실행과 같은 지시가 `confirmed`까지 가는지.
- 줄인 기록을 실제 API가 받는지, 호출당 토큰의 최댓값이 20,779의 절반 이하인지.
- 모델이 `plan`과 `stage_done`을 제대로 쓰는지, 단계 수와 크기가 납득되는지.
- 등록 양식에 "스스로 알아냄" 후보가 뜨는지, 승인한 뒤 다음 실행이 탐색 없이 가는지.
- 단계 한도 8이 정당하게 긴 단계에 걸리는지.

사내 API가 줄인 기록의 형식을 받는지는 회사에서 확인합니다.

## 끝났다고 보는 기준

- 설계의 목표 넷이 테스트로 확인됩니다(작업 1~4의 테스트).
- `python -m pytest -q` 전체 통과, `tests\test_application_agnostic.py` 통과.
- 계획이 없는 예전 기록이 뷰어와 요약에서 지금처럼 보입니다.
- 작업 6의 확인은 사용자 확인 뒤에 하고, 그 전까지는 "실제 모델로는 확인 전"이라고 적습니다.
