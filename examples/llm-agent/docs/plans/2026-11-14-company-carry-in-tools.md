# 회사 반입 도구 구현 계획

- 날짜: 2026-11-14
- 상태: 구현했습니다(2026-11-14). 창 없는 테스트가 통과하고, 가상 MES 앱과 실제 모델로 `check.py`를 돌려 확인했습니다(아래 "실제 확인").
- 배경: [`../cua-background.md`](../cua-background.md)의 "집–회사 제약"

## 왜 만드는가

회사에서 나온 실행 결과는 집으로 가져올 수 없고, 사용자가 말로 전하는 것이 유일한 경로입니다. 지금은 전할 것이 `결과: gave_up (스텝 14, 토큰 31200)` 한 줄뿐이라, 무엇이 어긋났는지 알려면 사용자가 기록을 열어 화면 내용을 읽어야 합니다. 또 `--goal`과 뷰어의 새 실행은 모델 이름이 코드의 기본값(`agent/task.py`의 `Task.model`)으로 고정되어, 내부 LLM의 모델 이름을 쓰려면 코드를 고쳐야 합니다.

이 계획은 세 가지를 만듭니다.

1. **설정 파일:** 회사마다 다른 값을 커밋하지 않는 파일 하나로 받습니다.
2. **점검 명령:** 실행 전에 바이너리, LLM, 대상 앱이 쓸 수 있는 상태인지 한 번에 확인합니다.
3. **실행 요약:** 실행이 끝나면 화면 내용 없이 수와 코드만으로 된 요약을 냅니다.

## 전역 제약

- 요약과 점검 출력에는 화면의 글, 요소 이름, 지시 문장, 질문, 답, 지식 내용, 파일 경로를 넣지 않습니다. 수, 코드, 도구 이름, 판정만 넣습니다. 이를 어기면 실패하는 테스트를 둡니다.
- `agent/`와 `viewer/`의 코드에 앱 이름이나 화면의 글을 넣지 않습니다(`tests\test_application_agnostic.py`).
- 사내 값은 커밋하지 않습니다. 저장소에는 예시 파일만 둡니다.
- 테스트는 창을 띄우지 않고 모델을 부르지 않습니다. 실제 창과 실제 모델로 하는 확인은 마지막에 사용자 확인을 받고 한 번 합니다.
- `agent-desktop` CLI(Rust)는 바꾸지 않습니다.
- 사용자에게 보이는 글은 한국어입니다.

## 계약

### 설정 (`agent/settings.py`)

```python
SETTINGS_ENV = "AGENT_SETTINGS"
DEFAULT_PATH = HERE / "settings.local.yaml"      # .gitignore에 추가
KEYS = ("model", "reasoning_effort", "image_mode", "knowledge_dir", "exe", "system")

class SettingsError(Exception): ...
def load_settings(path=None, environ=None) -> dict   # 기본 파일이 없으면 {}. 모르는 키와 틀린 값은 SettingsError
def settings_path(path=None, environ=None) -> Path   # 읽을 파일의 경로. 있든 없든
```

- 인자나 `AGENT_SETTINGS`로 이름을 준 파일이 없으면 `{}`가 아니라 `SettingsError`입니다. 오타로 모든 값이 조용히 빠지는 것을 막습니다.
- 상대 경로 `knowledge_dir`는 설정 파일이 있는 폴더 기준입니다.

- 우선순위: 명령줄 인자 > 작업 파일의 키 > 설정 파일 > 코드의 기본값.
- `task_from_args(goal, exe, permission_level=3, defaults=None)`, `load_task(path, defaults=None)`가 `defaults`의 `model`, `reasoning_effort`, `image_mode`, `knowledge_dir`, `system`을 받습니다.
- `exe`는 `run.py --goal`에서 `--exe`가 없을 때와 `view.py`에서 `--exe`가 없을 때의 기본값입니다.
- `RunManager(..., task_defaults=None)`가 뷰어의 새 실행에 같은 값을 씁니다.
- 예시는 `settings.example.yaml`. API 키와 `OPENAI_BASE_URL`은 지금처럼 `.env`에 둡니다.

### 관찰 분류 (`agent/survey.py`)

```python
def kinds(tree) -> dict
# {"named_ref": n, "unnamed_ref": n, "unnamed_ref_with_id": n, "text": n, "blank": n, "nodes": n, "depth": n}

def id_keys(tree) -> set[str]
# native id가 있는 요소마다 sha256(role + "\n" + native id 종류 + "\n" + 값)의 앞 16자
```

- `named_ref`: ref와 이름이 있는 요소. 이름으로 찾아 누를 수 있습니다.
- `unnamed_ref`: ref는 있고 이름이 없는 요소. 캡처의 번호로만 구분됩니다.
- `unnamed_ref_with_id`: `unnamed_ref` 가운데 스냅샷의 `native_id`(Windows에서는 `AutomationId`)가 있는 요소. 이 값이 앱을 다시 켜도 같으면 이름 없는 요소를 모델 없이 다시 찾는 열쇠가 됩니다.
- `id_keys`는 값을 그대로 두지 않고 해시로 바꿉니다. 점검 명령이 앱을 다시 켠 뒤에도 같은 id가 나오는지 비교하는 데 씁니다.
- `text`: ref 없이 이름과 위치만 있는 요소.
- `blank`: 이름도 ref도 자식도 없고 위치만 있는 요소. 그림으로만 그려진 영역의 후보입니다.
- 가상 앱의 세 프로파일(`easy`, `mes-like`, `hard`)에서 캡처해 둔 응답으로 수가 서로 다르게 나오는지 테스트합니다.

### 점검 명령 (`check.py`)

```
python check.py [--exe <절대경로>] [--skip llm|app] [--settings <파일>]
```

항목마다 `통과`, `실패(코드)`, `건너뜀` 한 줄을 내고, 실패가 있으면 종료 코드 1입니다.

| 항목 | 확인하는 것 |
| --- | --- |
| 바이너리 | 찾은 위치의 종류(환경변수, release, debug), `version` 응답 |
| 설정 | 설정 파일을 읽었는지, 어느 키가 설정 파일에서 왔는지(값은 내지 않음) |
| LLM 호출 | Responses API 한 번. 실패하면 오류 종류와 HTTP 상태 코드 |
| LLM 도구 호출 | strict 도구 하나를 주고 모델이 그 도구를 부르는지 |
| LLM 그림 입력 | Pillow로 만든 숫자 그림을 `user_message`와 `tool_output` 두 형태로 보내 숫자를 읽는지. 결과로 권하는 `image_mode` |
| 앱 관찰 | 대상 앱의 주 창 스냅샷 한 번: `kinds`의 수, `complete` 여부, 걸린 시간, 화면 캡처의 크기, DPI 배율 |
| id 유지 | 지난 점검의 `id_keys`와 이번 것을 비교해 `같은 id 37/40`처럼 냅니다. 지난 점검이 없으면 `건너뜀`이고 이번 것을 저장합니다. 저장 위치는 `.check\<시스템 이름>.json`(Git 제외). 앱을 닫았다 켠 뒤 다시 점검하면 id가 유지되는지 알 수 있습니다 |

- LLM 항목은 화면 내용을 보내지 않습니다. 보내는 그림은 점검 명령이 만든 것입니다.
- 앱 관찰은 앱을 띄우거나 이미 떠 있는 창을 읽기만 하고, 누르거나 입력하지 않습니다. 창이 뜨므로 사용자가 직접 실행합니다.
- 앱 관찰의 결과는 그 시스템에 무엇이 필요한지를 가릅니다. `text`와 `blank`가 많으면 그림 입력과 사람의 확인이 많이 필요한 시스템입니다.

### 실행 요약 (`agent/summary.py`)

```python
def summarize(run, events, code=None) -> list[str]   # 순수 함수. run.json과 events.jsonl의 내용만 씀
def code_version(repo) -> str                        # "284f163" 또는 "284f163+수정", git이 없으면 "알 수 없음"
```

출력 예:

```
요약 (화면 내용 없음)
  코드: 284f163
  판정: gave_up · 스텝 14/40 · 토큰 31200
  마지막 동작: [14] click_point → needs_confirmation
  동작: 12회 · 기대 지정 9 · 일치 6 · 불일치 3
  거절: needs_confirmation 2 · blocked_until_question 1
  오류 코드: STALE_REF 1 · TIMEOUT 2
  질문: 3 · 답 2 · 무응답 1
  지식: 불러옴 4 · 참고 2 · 참고 뒤 실패 1
  마지막 관찰: 이름 있는 ref 12 · 이름 없는 ref 3 · 글자 40 · 빈 영역 2 · 잘림 없음
```

- "마지막 관찰" 줄을 위해 기록기(`agent/recorder.py`)가 observation 이벤트에 `kinds`(`agent.survey.kinds`의 결과, 잘리기 전의 전체 트리 기준)와 `complete`를 넣습니다. 둘 다 수와 참·거짓뿐입니다. 이 값이 없는 옛 기록에서는 그 줄을 뺍니다.
- "스텝 14/40"의 40은 `run.json`의 `max_steps`입니다. `run.py`와 뷰어가 실행을 시작할 때 넣습니다. 없는 기록에서는 `/40`을 뺍니다.
- `run.py`가 실행 끝에 출력하고 `runs\<id>\summary.txt`로도 남깁니다.
- `python view.py --summary <run_id|latest>`가 지난 실행의 요약을 다시 냅니다.
- 뷰어의 실행 화면에 같은 요약을 보여 주고 복사 버튼을 둡니다.

## 작업 나누기

| 작업자 | 맡는 것 | 파일 |
| --- | --- | --- |
| w1 | 관찰 분류, 실행 요약, 요약의 `run.py`·`view.py`·뷰어 연결 | `agent/survey.py`, `agent/summary.py`, `viewer/static/*`, 해당 테스트. `run.py`와 `view.py`는 w2가 설정 연결을 제출한 뒤에 고침 |
| w2 | 설정 파일과 `task.py`, `run.py`, `view.py`, `RunManager` 연결 | `agent/settings.py`, `agent/task.py`, `run.py`, `view.py`, `viewer/run_manager.py`, `settings.example.yaml`, `.gitignore`, 해당 테스트 |
| w3 | 점검 명령 | `check.py`, `tests/test_check.py`. `agent/survey.kinds`와 `agent/settings.load_settings`는 위 계약대로 있다고 보고 씀 |

오케스트레이터는 제출마다 코드를 읽고 테스트를 다시 돌려 수락하고, README의 "준비"와 "실행 기록", `CLAUDE.md`의 명령 목록, `cua-background.md`의 "나머지 도구는 후보" 문장을 고칩니다.

## 구현하면서 정한 것

- 요약의 코드 버전은 실행을 시작할 때 `run.json`의 `code`로 남깁니다. 지난 실행의 요약을 나중에 다시 만들어도 그때의 버전이 나옵니다.
- 기록기가 `run.json`을 교체하는 순간에는 Windows가 그 파일을 열어 주지 않습니다. 요약은 잠깐 기다렸다 다시 읽습니다.
- "id 유지"는 지난 점검과 프로세스가 같으면 건너뜁니다. 프로세스의 표식도 해시로만 저장합니다.
- 질문은 답을 받을 수 있게 된 뒤에 기록합니다(`AskGate.ask(question, on_waiting=None)`). 기록에 보이는 질문은 언제든 답할 수 있습니다.
- `run.json`의 `reason`에는 모델이 쓴 완료 문장이 들어가고 화면의 글이 섞일 수 있습니다. 밖으로 전하는 것은 요약뿐입니다.

## 실제 확인 (2026-11-14, 가상 MES 앱, 배율 125%)

- LLM 세 항목 통과. 그림은 `user_message`와 `tool_output` 두 방식 모두 읽었습니다.
- 같은 프로세스를 다시 점검하면 "id 유지"가 `건너뜀 (앱을 다시 켜지 않음)`으로 나옵니다.
- 같은 프로파일로 앱을 껐다 켜면 `같은 id 10/13 · 새 id 3`입니다. 이 앱의 id 13개 중 3개는 켤 때마다 바뀝니다.
- 켠 직후의 주 창에서 `easy`와 `mes-like`는 수가 같고(이름 있는 ref 9, 이름 없는 ref 1, 글자 2, 빈 영역 1), `hard`만 다릅니다(4, 1, 6, 2). 두 프로파일은 화면을 연 뒤에 나오는 필터와 표에서만 다르기 때문입니다. 점검은 누르지 않으므로 화면을 연 상태의 수는 재지 않았습니다. 세 프로파일이 모두 다르게 나온다는 기준은 이 범위에서만 확인했습니다.

## 끝났다고 보는 기준

- `python -m pytest -q`가 통과합니다.
- 요약과 점검 출력에 화면의 글이 없다는 테스트가, 기록에 넣어 둔 표식 글을 출력에서 찾지 못합니다.
- 설정 파일의 `model`만 바꿔 `--goal` 실행과 뷰어의 새 실행이 그 모델 이름으로 호출합니다(가짜 클라이언트로 확인).
- 사용자 확인을 받고 가상 앱에서 `check.py`를 한 번 실제로 돌려, 세 프로파일의 앱 관찰 수가 다르게 나오는 것을 봅니다.
