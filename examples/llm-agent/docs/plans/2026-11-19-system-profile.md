# 시스템 프로파일 구현 계획

> **에이전트 작업자에게:** 작업마다 superpowers:test-driven-development를 따릅니다. 단계는 체크박스(`- [ ]`)로 표시합니다.

- 날짜: 2026-11-19
- 상태: 사용자 승인(2026-11-19), 작업 1~9 수락, 통합 확인 끝, 가상 MES의 실제 창 확인 끝(2026-11-19)
- 설계: [시스템 프로파일 설계](../specs/2026-11-19-system-profile-design.md) (사용자 승인 2026-11-19). 값과 규칙은 설계가 기준이고, 이 계획은 파일, 이름, 시그니처, 테스트, 순서를 정합니다. 구현하는 사람은 둘 다 읽습니다.

**목표:** 시스템마다 준비 신호와 요소 ID의 등록 이름을 파일로 두고, 준비 확인을 재서 빠른 읽는 방식을 고르며, 모델과 workflow가 ID를 추가 정보로 쓰고, 관리자가 뷰어의 등록 화면에서 이것을 등록합니다.

**구조:** `agent/profile.py`가 프로파일 파일을 읽고 쓰고, `agent/identity.py`가 "역할, ID, 담긴 영역"으로 노드를 맞추는 규칙을 한 곳에 둡니다. 준비 확인(`agent/ready.py`), 관찰(`agent/observe.py`), 대상 적기(`agent/locator.py`), 등록 화면(`viewer/profile_editor.py`)은 모두 이 규칙을 씁니다. 프로파일은 실행을 시작할 때 한 번 읽어 `apply_ready`, `Toolbox`, `RunRecorder`, workflow 만들기에 넘깁니다.

**쓰는 것:** Python 표준 라이브러리와 지금의 의존성(PyYAML, Pillow), 가상 MES는 지금의 C#(.NET Framework). 더하는 의존성은 없습니다. `agent-desktop` CLI는 고치지 않습니다.

## 전체에 걸친 조건

- **ID는 홀로 열쇠가 아닙니다.** 요소를 ID로 맞추는 모든 곳은 `identity.fits`를 거쳐 역할, ID, (적혀 있으면) 담긴 영역을 함께 봅니다. 이름이 있는 요소는 이름도 맞아야 합니다.
- 프로파일의 글(준비 신호, 등록 이름)과 ID는 `run.json`, 요약, `check.py` 출력, 오류 글에 나오지 않습니다. 오류는 "몇 번째 항목의 어느 키"까지만 말합니다. `events.jsonl`과 프로파일 파일에만 둡니다.
- `agent/`와 `viewer/` 코드에 앱 이름이나 화면의 글을 넣지 않습니다(`tests\test_application_agnostic.py`). 테스트와 문서의 예시에는 넣어도 됩니다.
- 프로파일이 없는 시스템의 동작은 아래 다섯 말고는 바뀌지 않습니다: 준비 확인의 쉬는 시간 0.3초, 준비 확인의 수 세기, ID만 있는 잎 노드가 요소가 됨, 이름 없는 요소의 줄에 `id="…"`가 붙음, `effects`의 요소 열쇠에 ID가 들어감. 지금의 테스트는 이 다섯 때문에 고치는 것 말고는 그대로 통과해야 합니다. 다른 이유로 고쳐야 하면 오케스트레이터에게 먼저 묻습니다.
- 등록 화면이 부르는 CLI 명령은 `launch`, `list-windows`, `snapshot`, `find`, `screenshot`뿐이고, 모델을 부르지 않습니다. `viewer/profile_editor.py`는 `agent.llm`을 import하지 않습니다.
- 값: 쉬는 시간 `READY_POLL_S = 0.3`, `snapshot` 읽기 제한 8000ms(지금 값), `find` 읽기 제한 `FIND_TIMEOUT_MS = "3000"`, 등록 이름은 한 줄에 80자까지.
- 작업자는 커밋하지 않고, 실제 창을 띄우는 실행과 실제 모델 호출을 하지 않습니다(`VIRTUAL_MES_LIVE=1` 테스트, `check.py`의 app 점검 포함). 가상 MES의 빌드(`virtual-mes\build.ps1`)와 `--parse-only` 실행은 창을 띄우지 않으므로 해도 됩니다.
- 테스트는 `examples/llm-agent`에서 `/c/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -X utf8 -m pytest -q -p no:cacheprovider`(아래 `pytest`)로 돌립니다. 제출 전 전체 실패 0. 테스트마다 구현을 되돌려 실패하는지 확인하고 보고서에 적습니다.

## 계획에서 정한 것 (설계가 맡겼거나 말하지 않은 것)

1. **담긴 영역은 ID가 있는 요소에만 싣습니다.** 쓰이는 곳(등록 이름 맞추기, workflow의 `in`, ID 준비 신호)이 모두 ID와 함께이고, 모든 요소에 실으면 기록이 커지고 지금 테스트가 대부분 바뀝니다.
2. **재실행도 다른 실행처럼 프로파일을 읽어 준비 신호와 요소의 범위를 정합니다.** 설계의 "재실행은 프로파일을 보지 않습니다"는 대상을 찾을 때 `stable_ids`를 따지지 않는다는 뜻으로 구현합니다.
3. **프로파일의 `system` 값이 파일 이름과 다르면 그 프로파일을 쓰지 않습니다.** 다른 시스템의 파일을 복사해 이름만 바꾸면 준비 신호가 영영 보이지 않기 때문입니다.
4. **손으로 적은 `id: 56`(따옴표 없는 숫자)은 받아서 글 `"56"`으로 봅니다.**
5. **등록 화면의 "3초 뒤 읽기"는 서버가 기다립니다.** 브라우저의 숨은 탭은 타이머가 늦어집니다. 화면의 남은 초 표시는 브라우저가 따로 셉니다.
6. **등록 화면에서 고를 수 있는 시스템은 설정의 `systems`에 적힌 것뿐입니다.** 없으면 "설정 파일의 systems에 시스템을 적어 주세요"를 보여 줍니다.
7. **준비 신호 목록이 저장된 것과 같으면 저장할 때 준비 신호를 다시 검사하지 않습니다.** 팝업 창을 읽어 등록 이름만 더하는 저장이 막히지 않게 합니다.
8. **`check.py`의 바퀴별 출력은 20바퀴를 넘으면 처음 10바퀴와 마지막 10바퀴만 내고 사이는 `… N바퀴 생략` 한 줄로 줄입니다.**
9. **`find` 방식에서 준비된 창을 기록하려고 읽은 트리에 `in`이 적힌 ID 신호의 요소가 없으면(깊이 10 아래) 준비가 아닌 것으로 칩니다.** 그 요소는 모델에게도 보이지 않는 깊이입니다.

## 검토에서 특히 볼 것

설계가 말하지 않았지만 쓰는 사람이 곧 만날 입력입니다. 각 항목의 테스트는 괄호 안의 작업에 있습니다.

1. **손으로 고친 프로파일: 따옴표 없는 숫자 ID, 다른 시스템의 파일을 복사한 것.** 숫자는 글로 받고, `system`이 파일 이름과 다르면 값 없는 이유와 함께 거절합니다. (작업 1)
2. **등록 이름에 줄바꿈, 80자를 넘는 글, 따옴표.** 줄바꿈과 80자 초과는 프로파일 검사가 거절합니다. 따옴표는 받고, 관찰 글에서 JSON 따옴표로 감쌉니다. 등록 이름은 모델의 입력에 그대로 들어가기 때문입니다. (작업 1, 작업 3)
3. **`find`가 신호를 모두 찾았는데 뒤이은 전체 읽기가 실패하거나 담긴 영역이 어긋남.** 그 바퀴는 준비가 아니고 다음 바퀴에 다시 봅니다. 예외를 내지 않고, 끝내 안 되면 `timeout`입니다. (작업 2)
4. **최소화됐거나 가려져 스크린샷을 못 찍는 창을 등록 화면에서 읽음.** 트리만으로 표를 보여 주고, 그림이 없다는 것과 창을 띄운 뒤 다시 읽으라는 것을 알립니다. 읽기 전체를 실패로 돌리지 않습니다. (작업 9)
5. **읽은 뒤 화면이 바뀌었는데 옛 읽기로 저장.** 저장은 가장 최근 읽기의 `read_id`로만 받고, 옛 것이면 409와 "다시 읽어 주세요"입니다. (작업 9)

## 배정과 순서

| 배정 | 작업자 | 작업 | 편집 경로 |
| --- | --- | --- | --- |
| `sp-w1-profile` | w1 | 1, 5, 6 | `agent/profile.py`(새), `agent/identity.py`(새), `agent/settings.py`, `agent/locator.py`, `agent/workflow_format.py`, `agent/workflow_author.py`, `agent/summary.py`, `run.py`, `settings.example.yaml`, `.gitignore`, `tests/workflow_support.py`, `tests/replay_support.py`, 각 테스트 |
| `sp-w2-observe` | w2 | 7, 3, 8 | `virtual-mes/*`, `agent/observe.py`, `agent/recorder.py`, `agent/effects.py`, `agent/survey.py`, `agent/checks.py`, `check.py`, `tests/fake_cli.py`, `tests/virtual_mes_support.py`, 각 테스트 |
| `sp-w3-ready` | w3 | 2, 4, 9 | `agent/ready.py`, `agent/task.py`, `agent/systems.py`, `agent/tools.py`, `agent/screen_tools.py`, `agent/loop.py`, `agent/baseline.py`, `agent/workflow_run.py`, `viewer/profile_editor.py`(새), `viewer/run_manager.py`, `viewer/server.py`, `viewer/static/*`, `view.py`, `tests/ready_support.py`, `tests/profile_support.py`(새), 각 테스트 |

README, `CLAUDE.md`, 설계 문서의 상태 줄은 오케스트레이터가 마지막에 고칩니다. 테스트 파일도 위 표의 주인만 고칩니다. 다른 작업자의 파일(예: `tests/fake_cli.py`)에 더할 것이 있으면 직접 고치지 말고 오케스트레이터에게 알립니다.

의존:
- 바로 시작: 작업 1(w1), 작업 7(w2), 작업 2(w3). 작업 2는 K1을 쓰는 부분(ID 신호)을 작업 1 수락 뒤에 잇습니다.
- 작업 5는 작업 1 수락 뒤. 작업 3은 작업 1 수락 뒤.
- 작업 4는 작업 2, 3 수락 뒤. 작업 8은 작업 1, 2, 3 수락 뒤.
- 작업 6은 작업 2, 3, 4, 5 수락 뒤. 작업 9는 작업 1, 2, 3, 4 수락 뒤.
- 기다리는 동안 작업자는 다음 작업의 테스트를 계약(K1~K11)만 보고 먼저 써 둘 수 있습니다.

## 계약

**K1. `agent/identity.py` (작업 1):** 스냅샷 노드만 받는 순수 함수입니다.

```python
def native_value(node) -> str | None            # node["native_id"]의 값을 글로. {"kind","value"}와 글 둘 다 받음. 없거나 비면 None
def is_leaf(node) -> bool                       # children도 children_count도 없음
def as_container(node) -> dict | None           # ID가 있으면 {"role", "id"}, 아니고 이름이 있으면 {"role", "name"}(앞뒤 공백 뺌), 아니면 None
def child_container(node, inherited, is_root) -> dict | None   # 이 노드의 자식이 받는 담긴 영역. is_root면 None, 아니면 as_container(node) or inherited
def container_key(container) -> tuple | None    # 담긴 영역을 해시되는 (role, "id"|"name", 글)로. {role, id}와 {role, name}은 다른 열쇠
def walk(tree) -> Iterator[tuple[dict, dict | None]]            # 뿌리 창 아래의 모든 노드와 그 담긴 영역, 트리 순서
def fits(role, native, container, entry) -> bool               # entry의 role·id와 같고, entry에 "in"이 있으면 container와 같음
def count(tree, signal) -> int                  # 뿌리 창 아래에서 신호에 맞는 노드 수
```

뿌리는 `tree`의 역할이 `window`일 때만 뺍니다(지금 `ready.has_named_element`의 규칙). 이름 신호(`{"name", "role"?}`)는 앞뒤 공백만 무시하고 이름이 똑같아야 하고, `role`이 있으면 역할도 같아야 합니다. ID 신호(`{"id", "role", "in"?}`)는 `fits`로 봅니다. ID는 글자 그대로 견줍니다.

**K2. `agent/profile.py` (작업 1)**

```python
PROFILE_ENV = "AGENT_PROFILE_DIR"
FORMAT = 1
READ_MODES = ("snapshot", "find")
ALIAS_MAX = 80
class ProfileError(Exception):                  # .reason: 값이 없는 짧은 이유(예: "elements 2번째 항목: 모르는 키")
@dataclass(frozen=True)
class Profile:
    system: str | None = None
    ready: tuple = ()                           # 신호 dict들
    elements: tuple = ()                        # {"id", "role", "name", "in"?}
    stable_ids: bool = False
    ready_read: str = "snapshot"
def profile_root(explicit=None, settings_dir=None, environ=None) -> Path   # 기본 examples/llm-agent/profiles
def load_profile(root, system) -> Profile       # 파일이 없으면 Profile(system=system). 깨졌으면 ProfileError
def check_profile(raw, system=None) -> Profile  # 형식 검사. system을 주면 파일의 system과 같아야 함
def save_profile(root, profile) -> Path         # 검사한 뒤 임시 파일에 쓰고 바꿔치기(workflow_store와 같은 방식)
def as_dict(profile) -> dict                    # 파일에 쓰는 꼴(설계 1절의 키 순서)
def entry_of(profile, role, native, container) -> dict | None  # fits에 맞는 elements 항목. in을 적은 항목이 앞섬
def alias_of(profile, role, native, container) -> str | None   # entry_of가 준 항목의 name
```

`alias`의 "한 항목에 맞는 요소가 하나뿐"은 이름이 아니라 `entry_of`가 준 항목으로 셉니다(같은 등록 이름을 가진 두 항목이 섞이지 않게). `format`과 `system`은 꼭 있어야 하고, 나머지 키는 빠지면 기본값입니다. 이름(이름 신호, 등록 이름, `in`의 `name`)은 앞뒤 공백을 빼고 저장하며 `id`와 `role`은 글자 그대로 둡니다.

검사: 맨 위 키는 `format`, `system`, `stable_ids`, `ready_read`, `ready`, `elements`뿐. `ready` 항목은 `{name, role?}`이거나 `{id, role, in?}`(둘을 섞지 않음). `elements` 항목은 `{id, role, name, in?}`. `in`은 `{role, id}`이거나 `{role, name}`. 글은 비어 있지 않아야 하고, `id`는 정수도 받아 글로 바꿉니다. `name`(등록 이름)은 한 줄이고 `ALIAS_MAX`자까지. 같은 신호나 같은 `(role, id, in)`이 두 번 있으면 거절. 시스템 이름의 규칙은 `knowledge.system_name`과 같습니다. 설정 키 `profile_dir`는 `agent/settings.py`의 `KEYS`, `PATH_KEYS`, `FOLDER_KEYS`에 더합니다.

**K3. 요소와 관찰 글 (작업 3)**

```python
def render_tree(tree, include_bounds=False, profile=None)
def render_marked(tree, include_bounds=False, profile=None)
def visible_elements(tree, profile=None)
```

- 요소의 범위: 지금 규칙에 더해, ref·이름·설명·값이 없고 ID가 있는 노드는 역할이 `window`가 아니고 `is_leaf`이거나 `profile.elements`의 항목과 `fits`일 때 요소입니다. 종류는 `"id"`.
- 요소 dict에 더하는 키(값이 있을 때만): `native_id`(`{"kind", "value"}`, 지금 표시 번호 맵의 꼴), `in`(ID가 있는 요소의 담긴 영역), `alias`. 표시 번호 맵과 `visible_elements`가 같은 키를 냅니다.
- `alias`는 `alias_of`가 이름을 주고, 그 항목에 맞는 요소가 이 관찰에 하나뿐일 때만 붙습니다.
- 줄: 요소인 노드는 모두 줄이 있습니다. 이름이 없고 ID가 있으면 `id="…"`, `alias`가 있으면 `alias="…"`를 역할(과 ref) 뒤에 붙입니다. 둘 다 지금의 `_quoted`(JSON 따옴표)를 씁니다. 예: `[13] image id="60" alias="Execution Plan"`, `[9] button @s1:e4 id="7001" actions=Click`.
- `--root`로 읽은 부분 트리에서는 담긴 영역을 그 부분 안에서만 구합니다.

**K4. 준비 확인 (작업 2)**

```python
READY_POLL_S = 0.3
FIND_TIMEOUT_MS = "3000"
def signals(task) -> tuple                      # task.ready, 없으면 ready_text 하나짜리 이름 신호, 그것도 없으면 ()
def wait_until_ready(desktop, exe, wanted, timeout_s, clock, sleep, no_window_s=NO_WINDOW_S, cancelled=None,
                     read="snapshot", on_round=None) -> ReadyOutcome
def candidates(rows, launched, before_keys) -> list[dict]      # 지금의 후보 창 규칙을 꺼낸 것
def measure(desktop, row, wanted, clock) -> dict
def recommended_read(measured) -> str | None
```

- `wanted`는 신호 dict의 목록이고, 글 하나도 받습니다(이름 신호 하나, 지금 호출과 테스트를 위해).
- `ReadyOutcome`에 더하는 필드: `reads`, `failed_reads`, `timeouts`, `max_read_ms`, `candidates`(마지막 바퀴의 후보 창 수), `read`. `reads`는 `snapshot`과 `find` 호출을 모두 셉니다(창 목록 읽기는 빼고). `timeouts`는 `find`가 `TIMEOUT`으로 답한 수이고, 그 밖의 실패한 읽기가 `failed_reads`입니다.
- `find` 방식: 이름 신호는 `find --window-id W --name N --exact --first --timeout-ms 3000`(`role`이 있으면 `--role R`), ID 신호는 `find --window-id W --native-id ID --role R --first --timeout-ms 3000`. 답은 `data.match`(없으면 null)이고, 돌려준 요소를 한 번 더 봅니다: 이름 신호는 이름(앞뒤 공백만 무시)과 역할, ID 신호는 역할만입니다. CLI의 답에는 `native_id`가 없어 ID를 다시 볼 수 없고, ID는 CLI가 글자 그대로 견줍니다. 돌려준 요소가 뿌리 창이면(역할 `window`, `path` 빔) 신호가 아닙니다. 창의 제목이 이름 신호와 `--exact`만큼 느슨하게(대소문자 무시, 안쪽 공백 줄임) 같으면 그 창은 그 바퀴에 `snapshot`으로 판정합니다. 신호가 모두 찾아지면 전체를 한 번 읽어(`snapshot`) `in`이 적힌 ID 신호를 `identity.count`로 다시 보고, 그 읽기를 `tree_envelope`로 돌려줍니다.
- `on_round(info)`: 바퀴마다 `{"round": int, "list_ms": int, "candidates": int, "reads": [{"window": int, "signal": int|None, "found": bool, "ms": int, "code": str|None}]}`. `window`와 `signal`은 그 바퀴 안의 순서(1부터)이고 글이 아닙니다.
- `measure`는 `{"snapshot": {"ms", "ok", "found": [bool…]}, "find": {"ms", "ok", "timeouts", "found": [bool…]}}`. `recommended_read`는 두 방식이 모두 `ok`이고 신호를 다 찾았을 때 `ms`가 작은 쪽(같으면 `snapshot`), 아니면 `None`. `find`의 `ms`는 신호마다의 `find` 시간의 합이고, 준비된 뒤 한 번 하는 전체 읽기는 넣지 않습니다. 준비 실패 뒤 힌트를 보려고 읽는 것은 `reads`에 넣지 않습니다.
- `ready` 끝 이벤트에 `reads`, `failed_reads`, `timeouts`, `max_read_ms`, `candidates`, `read`를 더합니다.

**K5. `Task`와 `apply_ready` (작업 2):** `Task.ready: tuple = ()`, `Task.ready_read: str = "snapshot"`. `apply_ready(task, settings, profile=None)`의 순서: 작업 파일의 `ready_text`가 있으면 그대로 > exe가 설정에 적힌 앱이 아니면 그대로 > `profile.ready`가 있으면 `ready`와 `ready_read`를 채움 > 설정의 `ready_text`(지금 동작). `ready_timeout_s`는 지금처럼 설정에서 옵니다. `exe`, `ready_text`, `ready_timeout_s`, `ready`, `ready_read` 필드가 있는 dataclass면 무엇이든 받습니다(`check.py`의 `Target`). 준비 기다리기가 있는지는 어디서나 `ready.signals(task)`로 봅니다(`loop.py`, `baseline.py`, `workflow_run.py`).

**K6. `Toolbox`와 `RunRecorder` (작업 4, 작업 3):** 둘 다 생성자에 `profile=None`. `Toolbox`는 `render_marked`를 부르는 모든 곳에, `RunRecorder`는 `visible_elements`에 넘깁니다. 같은 프로파일을 받은 둘의 표시 번호는 같습니다. 기록은 종류가 `id`인 요소를 `control`처럼 자르지 않습니다.

**K7. `run.json`의 `ready`와 요약 (작업 3, 작업 6):** 끝 이벤트의 새 값이 모두 제 꼴이면(0 이상의 정수, `read`는 `READ_MODES`) `run.json`의 `ready`는 `{"status", "waited_s", "switched", "read", "polls", "reads", "failed_reads", "timeouts", "max_read_ms"}`이고, 아니면 지금의 세 키입니다. 요약은 새 키가 있으면 `  준비: ready · 95초 · snapshot · 확인 12바퀴 · 읽기 31회(실패 4, 시간 초과 0, 최대 7.9초) · 대상 바뀜`, 없으면 지금 줄 그대로입니다.

**K8. 대상 적기의 ID (작업 5)**

```python
def describe(scoped, mark, avoid=(), use_ids=False) -> dict
def build(record, draft, use_ids=False) -> dict
def make_workflow(model, run_dir, store, log, today=None, status=None, use_ids=False)
def after_run(model, result, run_dir, store, log, use_ids=False)
```

- `describe`: `use_ids`이고 대상에 `native_id`가 있으면 바탕(역할, 이름, 설명)에 `id`(값의 글)와, 요소에 `in`이 있으면 `in`을 더합니다. 그것으로 하나가 안 되면 지금처럼 `near`.
- `find`: 대상 적기에 `id`가 있으면 요소의 ID 값이, `in`이 있으면 요소의 `in`이 같아야 합니다. 없는 키는 지금처럼 보지 않습니다.
- `workflow_author`: 이름이 파라미터인 대상(`name_param`)과 `select_only`의 항목은 `use_ids=False`로 적습니다. `select_only`의 `all`은 `use_ids`를 따릅니다.
- `workflow_format`: 대상 적기의 키에 `id`(비어 있지 않은 글), `in`(`{role, id}`나 `{role, name}`). `in`은 `id`가 있을 때만. `name`이 `$이름`인 대상 적기에 `id`가 있으면 `WORKFLOW_INVALID`.
- 재실행(`workflow_run.py`)은 `locator.find`를 그대로 쓰므로 고치지 않습니다.

**K9. 입구 (작업 6, 작업 9):** `run.py`와 `view.py`에 `--profile-dir <폴더>`. 폴더는 `profile_root(인자, 설정의 profile_dir, 환경변수)`. 시스템 이름은 지식 폴더에 쓰는 그 이름입니다. 실행(모델 실행, 재실행, 기준 실행)을 시작할 때 `load_profile`을 한 번 부르고 `apply_ready`, `RunRecorder`, `Toolbox`에 넘기며, 만들기에는 `use_ids=profile.stable_ids`를 넘깁니다. `--make-workflow`는 그 기록의 시스템 프로파일을 읽습니다. 프로파일이 깨졌으면 시작하지 않습니다: `run.py`는 `시스템 프로파일을 읽지 못했습니다: <reason>`을 출력하고 2로 끝나고, `RunManager`는 `InvalidRun`입니다. `RunManager`는 생성자의 `profile_dir=None`으로 폴더를 받습니다(`workflow_dir`과 같은 방식).

**K10. 점검 (작업 8):** 설정 항목에 `시스템 프로파일: 준비 신호 2 · 등록 이름 14 · ID 고정 예 · 읽는 방식 snapshot`(없으면 `시스템 프로파일: 없음`, 깨졌으면 실패 코드 `PROFILE_INVALID`와 `reason`). 준비 항목은 `on_round`로 바퀴마다 `  1바퀴: 창 목록 120ms · 후보 2 · 창1 신호1 찾음 310ms · 창1 신호2 없음 95ms`처럼 내고(실패는 `실패 <코드>`, `find`의 시간 초과는 `시간 초과`), 준비된 뒤 `읽는 방식: snapshot 4.2초 · find 0.3초 · 권장 find`를 냅니다. `survey.kinds`에 `id_only`(ref도 이름도 없고 ID와 자리가 있는 노드)를 더해 앱 관찰 줄에 넣습니다. ID 유지 항목이 같다고 나오면 `등록 화면에서 'ID는 다시 켜도 같음'을 켜도 됩니다`를 붙입니다.

**K11. 등록 화면 (작업 9)**

```python
class ProfileEditor:                            # viewer/profile_editor.py
    def __init__(self, desktop_factory, systems, root, sleep=time.sleep, clock=time.monotonic)
    def get(self, system) -> dict               # {"system", "exists", "profile": as_dict}
    def launch(self, system) -> dict            # {"windows": [{"id", "title"}]}
    def windows(self, system) -> dict
    def read(self, system, window_id, delay_s=0) -> dict
    def check(self, system, window_id, ready) -> dict          # 신호마다 found, ready.measure의 두 방식 ms, 권하는 방식
    def save(self, system, read_id, profile) -> dict           # 저장했으면 {"saved": True}, 아니면 EditorError
    def close(self)                                             # 임시 폴더를 지움
def problems(profile, saved, items, title) -> list[dict]       # 순수 함수: [{"where": "ready"|"elements", "index": int, "problem": str}]
class EditorError(Exception):                   # .status(400|409), .payload
```

- `read`의 답: `{"read_id", "image": 파일 이름|None, "size": [w, h]|None, "title", "items": [...]}`. `items`는 그 창에서 하니스가 보는 요소(저장된 프로파일로 `visible_elements`)에 자식이 있는 ID만 있는 노드(`window` 빼고)를 더한 것이고, 항목은 `{"n", "kind", "role", "name", "description", "id", "in", "leaf", "rect", "alias"}`입니다.
- `problems`: 설계 5절의 "검사" 네 가지와 "계획에서 정한 것" 7. 문제의 글에 값은 없습니다.
- `RunManager.exclusive()`는 컨텍스트 관리자입니다. 실행이 있으면 `RunBusy`를 내고, 잡고 있는 동안 `start`와 `start_workflow`가 `RunBusy`를 냅니다. `/api/status`의 `active`에는 지금처럼 실행 ID만 나갑니다. `launch`, `windows`, `read`, `check`는 이것을 잡고 돕니다.
- 주소: `GET /api/profile?system=`, `POST /api/profile/launch|windows|read|check|save`, `GET /api/profile/shots/<이름>`. 실행을 관리하지 않는 뷰어는 503. 답에 exe 경로를 넣지 않습니다. `/api/status`에 `can_register: bool`.
- 화면: `viewer/static/profiles.js`, 보기 전환의 "등록" 단추. 글은 `textContent`로만 넣습니다. 실행 화면의 박스 툴팁에 `id`와 `alias`를 더합니다(`app.js`).

---

### 작업 1: 프로파일 파일과 맞추는 규칙 (w1)

**파일:** `agent/identity.py`(새), `agent/profile.py`(새), `agent/settings.py`, `settings.example.yaml`, `.gitignore`(`/profiles/`). 테스트: `tests/test_identity.py`(새), `tests/test_profile.py`(새), `tests/test_settings.py`.

**인터페이스:** K1, K2를 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_identity.py
def test_the_container_is_the_nearest_ancestor_with_an_id_or_a_name(): ...
    # window > toolbar(id 12) > group(이름·ID 없음) > image(id 56) → {"role": "toolbar", "id": "12"}
def test_an_ancestor_with_an_id_and_a_name_is_named_by_its_id(): ...
def test_the_root_window_is_never_a_container(): ...          # 뿌리에 ID가 있어도 그 바로 아래는 None
def test_fits_needs_role_id_and_the_written_container(): ...  # 역할만 다름, in만 다름 → False. entry에 in이 없으면 container를 안 봄
def test_count_of_a_name_signal_ignores_only_outer_spaces(): ...   # " 조회 " 맞음, "조회 결과"와 대소문자만 다른 것은 아님
def test_count_of_a_name_signal_skips_the_window_title(): ...
def test_count_of_an_id_signal_tells_two_containers_apart(): ...   # 두 toolbar에 같은 (image, 56) → in을 적으면 1, 안 적으면 2
def test_native_value_reads_a_dict_a_string_and_nothing(): ...
# test_profile.py
def test_a_missing_file_is_an_empty_profile(tmp_path): ...
def test_save_then_load_gives_the_same_profile(tmp_path): ...       # 설계 1절의 예시 그대로
def test_an_unquoted_number_id_is_read_as_text(tmp_path): ...       # id: 56 → "56"
def test_a_file_copied_from_another_system_is_refused(tmp_path): ...  # system: GSCM을 MES.yaml로 → ProfileError
@pytest.mark.parametrize("broken", [...])                           # 모르는 키, 빈 글, 이름과 id를 섞은 신호, role 없는 ID 신호,
def test_a_broken_profile_is_refused_without_its_values(tmp_path, broken): ...   # in에 role만, 겹친 항목, 모르는 ready_read,
    # 줄바꿈이 든 등록 이름, 81자 등록 이름, format 2 → reason에 그 파일의 어떤 값도 없음
def test_an_alias_with_quotes_is_kept(tmp_path): ...
def test_alias_of_needs_the_container_when_the_entry_writes_one(): ...
def test_profile_root_order(tmp_path): ...                          # 인자 > 설정 > AGENT_PROFILE_DIR > 기본
# test_settings.py
def test_profile_dir_is_a_folder_key_taken_from_the_settings_folder(tmp_path): ...
```

- [ ] **2단계:** `pytest tests/test_identity.py tests/test_profile.py tests/test_settings.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** 두 모듈은 서로와 표준 라이브러리, PyYAML, `agent.knowledge.system_name`만 씁니다. 쓰기는 `workflow_store.py`의 임시 파일 바꿔치기를 따릅니다. `settings.example.yaml`에 `profile_dir` 설명을 더합니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 2: 준비 확인 (w3)

**파일:** `agent/ready.py`, `agent/task.py`, `agent/systems.py`, `agent/tools.py`(`launch_ready`만), `agent/loop.py`, `agent/baseline.py`, `agent/workflow_run.py`, `tests/ready_support.py`. 테스트: `tests/test_ready.py`, `tests/test_systems.py`, `tests/test_task.py`, `tests/test_loop_ready.py`, `tests/test_tools.py`.

**인터페이스:** K1을 쓰고, K4, K5를 만듭니다.

- [ ] **1단계: `tests/ready_support.py`의 `TimelineDesktop`이 `find`에 답하게 합니다.** `find_s`(한 번에 걸리는 가짜 시간), `find_timeouts`(시간 초과로 답할 창), 그리고 그 시각의 트리에서 `--name`/`--native-id`/`--role`로 찾은 첫 노드를 `data.match`로 줍니다. `--exact`는 CLI처럼 대소문자를 무시하고 안쪽 공백을 줄여 맞춥니다(하니스가 다시 거르는지 시험하려는 것).
- [ ] **2단계: 실패하는 테스트를 씁니다.**

```python
# test_ready.py
def test_the_pause_between_rounds_is_three_tenths_of_a_second(): ...
def test_every_signal_must_show_in_the_same_window(): ...        # 신호 둘 중 하나만 보이는 창 → 아직 아님
def test_an_id_signal_needs_its_role_and_container(): ...
def test_a_text_is_still_one_name_signal(): ...                   # 지금 호출 그대로
def test_the_outcome_counts_reads_failures_and_the_longest_read(): ...
    # snapshot_s=2, 한 창은 실패 → reads, failed_reads, max_read_ms == 2000, candidates
def test_find_mode_asks_one_find_per_signal_and_reads_the_tree_once_at_the_end(): ...
    # 명령 순서: find, find, snapshot. tree_envelope는 그 snapshot
def test_find_mode_stops_at_the_first_missing_signal_of_a_window(): ...
def test_a_find_timeout_is_not_yet_and_is_counted_apart(): ...    # timeouts 1, failed_reads 0
def test_a_looser_find_match_is_not_the_signal(): ...             # 대소문자만 다른 이름을 find가 돌려줌 → 찾지 못한 것
def test_a_window_titled_like_a_name_signal_is_judged_by_snapshot(): ...
def test_find_mode_checks_the_container_on_the_final_tree(): ...  # find는 찾았으나 in이 다름 → 준비 아님, 끝은 timeout
def test_a_failed_final_read_leaves_the_round_unready(): ...      # 다음 바퀴에 성공하면 ready, 예외 없음
def test_on_round_gets_numbers_and_no_text(): ...                 # info의 모든 값이 수, bool, 코드. 신호의 글과 ID가 없음
def test_measure_times_both_ways_on_one_window(): ...
def test_recommended_read_needs_both_ways_to_find_every_signal(): ...
def test_the_running_hint_reads_every_signal(): ...
# test_systems.py
def test_a_profile_ready_beats_the_settings_ready_text(): ...     # ready, ready_read가 채워지고 ready_text는 None
def test_a_task_file_ready_text_beats_the_profile(): ...
def test_a_profile_reaches_only_an_exe_the_settings_name(): ...
def test_an_empty_profile_leaves_the_settings_ready_text(): ...
# test_loop_ready.py / test_tools.py
def test_a_task_with_only_id_signals_waits_before_the_first_call(): ...   # ready_text 없이 ready만
def test_the_ready_end_event_carries_the_new_counts(): ...
```

- [ ] **3단계:** `pytest tests/test_ready.py tests/test_systems.py tests/test_task.py tests/test_loop_ready.py tests/test_tools.py`. 기대: 새 테스트 실패.
- [ ] **4단계: 구현합니다.** 트리에서 신호를 보는 일은 `identity.count`에 맡기고 `has_named_element`는 그것을 부르게 남깁니다(지금 쓰는 곳과 테스트를 위해). 읽기 하나는 한 곳에서 시간을 재고 수를 셉니다. `launch_ready`는 `signals(task)`와 `task.ready_read`를 넘기고 끝 이벤트에 새 값을 싣습니다. `loop.py`, `baseline.py`, `workflow_run.py`의 `task.ready_text` 검사를 `signals(task)`로 바꿉니다.
- [ ] **5단계:** `pytest`. 기대: 전체 통과.

### 작업 3: 관찰과 기록 (w2, 작업 1 수락 뒤)

**파일:** `agent/observe.py`, `agent/recorder.py`, `agent/effects.py`, `tests/fake_cli.py`. 테스트: `tests/test_observe.py`, `tests/test_recorder.py`, `tests/test_effects.py`.

**인터페이스:** K1, K2를 쓰고, K3과 K6의 `RunRecorder`, K7의 `run.json`을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_observe.py
def test_an_id_only_leaf_is_an_element_with_a_number_and_a_line(): ...     # [n] group id="71", kind "id"
def test_an_id_only_node_with_children_is_no_element_unless_registered(): ...
def test_a_registered_node_with_children_is_an_element_with_its_alias(): ...
def test_a_window_role_node_with_only_an_id_opens_no_scope(): ...          # 그 아래 요소의 scope가 그대로
def test_a_named_element_shows_no_id(): ...
def test_an_unnamed_ref_shows_its_id_after_the_ref(): ...
def test_alias_needs_the_written_container(): ...                           # 다른 toolbar의 같은 (image, 56)에는 안 붙음
def test_two_elements_fitting_one_entry_get_no_alias(): ...
def test_an_alias_with_quotes_is_json_quoted_in_the_line(): ...
def test_elements_with_an_id_carry_native_id_and_in(): ...                  # ID 없는 요소에는 두 키가 없음
def test_marks_and_visible_elements_agree_with_a_profile(): ...
def test_without_a_profile_only_the_leaf_rule_and_the_id_text_differ(): ... # ID 없는 트리의 글과 번호는 지금과 같음
# test_recorder.py
def test_observation_elements_keep_native_id_in_and_alias(tmp_path): ...
def test_id_elements_are_not_cut_with_the_text(tmp_path): ...               # 글자 요소 401개 뒤의 id 요소가 남음
def test_run_json_ready_takes_the_new_counts(tmp_path): ...
def test_a_ready_end_with_a_malformed_count_keeps_the_three_keys(tmp_path): ...
# test_effects.py
def test_unnamed_elements_told_apart_only_by_id_are_compared_by_id(): ...   # 가운데 하나가 사라짐 → removed가 그 ID의 요소
```

- [ ] **2단계:** `pytest tests/test_observe.py tests/test_recorder.py tests/test_effects.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** `_walk`가 `identity.child_container`로 담긴 영역을 내려 주고, 요소 판정과 줄 그리기가 한 번호 매기기를 씁니다. `alias`의 "하나뿐" 판정은 걷기가 끝난 뒤에 합니다. `tests/fake_cli.py`의 `_is_element`를 같은 규칙으로 맞춥니다. 요소 dict가 통째로 같은지 보던 지금 테스트는 새 키 때문에 달라지는 것만 고칩니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 4: Toolbox와 모델의 지시문 (w3, 작업 2·3 수락 뒤)

**파일:** `agent/tools.py`, `agent/screen_tools.py`, `agent/loop.py`. 테스트: `tests/test_tools.py`, `tests/test_loop.py`.

**인터페이스:** K3을 쓰고, K6의 `Toolbox`를 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_tools.py
def test_the_observation_text_shows_id_and_alias_from_the_profile(): ...
def test_click_part_presses_an_id_only_element_by_its_mark(): ...          # 실제 클릭 명령이 그 요소의 가운데로
def test_click_part_on_an_id_element_needs_permission_level_two(): ...
def test_the_guard_tells_two_unnamed_id_elements_apart(): ...
def test_marked_elements_carry_native_id_in_and_alias(): ...
def test_every_read_uses_the_profile(): ...     # observe, 동작 뒤 다시 읽기, wait_for_text, ready의 관찰에서 번호가 같음
# test_loop.py
def test_the_system_prompt_explains_id_and_alias(): ...   # "id=", "alias=", 어긋나 보이면 look이나 ask
```

- [ ] **2단계:** `pytest tests/test_tools.py tests/test_loop.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** `SYSTEM_PROMPT`에 더하는 글(영어, 지금 문체): `id="…"`는 앱이 준 값으로 이름 없는 요소를 구별하는 데 쓰고, `alias="…"`는 글자가 그림으로 그려진 요소에 관리자가 붙인 이름이니 그 요소의 이름으로 읽되, 화면에서 본 것과 어긋나 보이면 믿지 말고 `look`으로 확인하거나 `ask`로 물으며, ref가 없는 요소는 `click_part`로 누른다는 것.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 5: workflow의 대상 적기에 ID (w1, 작업 1 수락 뒤)

**파일:** `agent/locator.py`, `agent/workflow_format.py`, `agent/workflow_author.py`. 테스트: `tests/test_locator.py`, `tests/test_workflow_format.py`, `tests/test_workflow_author.py`, `tests/test_workflow_ids.py`(새. 재실행이 `id`로 찾는 것, `workflow_run.py`는 고치지 않음).

**인터페이스:** K3의 요소 키를 쓰고, K8을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_locator.py
def test_describe_adds_id_and_in_on_top_of_the_name(): ...        # use_ids → {"role", "name", "id", "in"}
def test_describe_without_use_ids_is_what_it_was(): ...
def test_describe_tells_picture_only_items_apart_by_id(): ...     # 이름도 가까운 글자도 없는 두 image → use_ids면 됨, 아니면 LocatorError
def test_find_needs_the_id_and_the_container_a_locator_writes(): ...   # 다른 영역의 같은 ID는 아님
def test_find_with_an_id_still_needs_the_name(): ...               # ID는 같고 이름이 다름 → 없음
def test_find_after_describe_gives_only_the_target_with_ids(): ...
# test_workflow_format.py
def test_a_locator_takes_id_and_in(): ...
@pytest.mark.parametrize("locator", [...])     # id 없는 in, 빈 id, in에 모르는 키, name이 $이름인데 id가 있음
def test_bad_id_locators_are_refused(locator): ...
# test_workflow_author.py
def test_stable_ids_writes_the_id_into_the_target(): ...
def test_a_target_named_by_a_parameter_gets_no_id(): ...
def test_select_only_items_are_found_by_name_and_all_may_carry_an_id(): ...
def test_without_stable_ids_the_workflow_is_what_it_was(): ...
def test_picture_only_menu_items_make_a_workflow_only_with_stable_ids(): ...   # 없으면 LOCATOR_NOT_UNIQUE
# test_workflow_ids.py
def test_replay_finds_a_target_by_role_id_and_container(): ...
def test_replay_stops_when_the_id_changed(): ...                   # TARGET_NOT_FOUND
```

- [ ] **2단계:** `pytest tests/test_locator.py tests/test_workflow_format.py tests/test_workflow_author.py tests/test_workflow_ids.py`. 기대: 새 테스트 실패(재실행 테스트는 `locator.find`를 고치기 전이라 실패).
- [ ] **3단계: 구현합니다.** `locator`는 요소의 `native_id`와 `in`을 K3의 꼴로 읽습니다. 저장 전에 그 관찰에 다시 대 보는 지금의 확인은 `id`가 든 대상 적기에도 그대로 돕니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 6: `run.py` 입구와 요약 (w1, 작업 2·3·4·5 수락 뒤)

**파일:** `run.py`, `agent/summary.py`. 테스트: `tests/test_run.py`, `tests/test_summary.py`, `tests/test_workflow_end_to_end.py`.

**인터페이스:** K2, K5, K6, K8을 쓰고, K9의 `run.py`와 K7의 요약을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_run.py
def test_profile_dir_argument_beats_the_settings_and_the_env(tmp_path): ...
def test_a_run_gives_one_profile_to_the_ready_wait_the_toolbox_and_the_recorder(tmp_path): ...
def test_recorded_marks_match_the_toolbox_marks_with_a_registered_parent(tmp_path): ...
    # 자식 있는 등록 요소가 있는 화면: events.jsonl의 요소 번호 == toolbox.marked_elements()의 번호
def test_a_broken_profile_stops_before_anything_is_launched(tmp_path, capsys): ...   # 종료 2, 출력에 파일의 값 없음
def test_a_replay_reads_the_profile_too(tmp_path): ...
def test_make_workflow_uses_the_record_systems_stable_ids(tmp_path): ...
def test_no_profile_text_reaches_run_json_or_the_summary(tmp_path): ...   # 신호의 글, 등록 이름, ID를 두 파일에서 찾아 없음
# test_summary.py
def test_ready_line_with_the_new_counts(): ...
def test_ready_line_of_an_older_run_is_unchanged(): ...
# test_workflow_end_to_end.py
def test_a_model_run_on_picture_only_menus_makes_a_workflow_that_replays(tmp_path): ...
    # menu=idonly 꼴의 장면, 프로파일에 stable_ids와 등록 이름 → 만들어지고, 재실행이 통과
```

- [ ] **2단계:** `pytest tests/test_run.py tests/test_summary.py tests/test_workflow_end_to_end.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** 프로파일을 읽는 곳은 `run.py`에 하나만 두고 모델 실행, 재실행, 기준 실행, `--make-workflow`가 그것을 씁니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 7: 가상 MES (w2)

**파일:** `virtual-mes/Exposure.cs`, `Options.cs`, `SurfaceUia.cs`, `Surface.cs`, `MainForm.cs`, 필요하면 새 `MenuIds.cs`, `README.md`. 테스트: `tests/test_virtual_mes.py`, `tests/test_virtual_mes_live.py`(쓰기만 하고 돌리지 않음), `tests/virtual_mes_support.py`.

**인터페이스:** 설계 7절. 다른 작업과 기대는 것이 없습니다.

- `--expose menu=bare`: 메뉴 항목이 이름 없는 `button`(ref와 고정된 숫자 `AutomationId`).
- `--expose menu=idonly`: 이름도 누르는 패턴도 없고 `AutomationId`만 있는 요소(Invoke 없음, 실제 클릭에는 지금처럼 반응). `idonly`는 `menu`만 받습니다.
- 메뉴 항목의 `AutomationId`는 화면이 달라도, 앱을 다시 켜도 같은 고정된 숫자입니다(위쪽 메뉴 줄과 메뉴 목록 모두). README에 표로 적습니다.
- 메뉴 줄과 메뉴 목록을 담은 영역에 `AutomationId`가 있습니다. `--menu-container-id off`면 없앱니다(기본 `on`).
- `--menu-dup-id same`: 둘째 메뉴 항목이 첫째와 같은 `AutomationId`를 씁니다. `--menu-dup-id other`: 메뉴 줄 밖의 다른 영역에 첫째 메뉴 항목과 역할과 `AutomationId`가 같은 요소를 하나 둡니다. 기본은 없음.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_virtual_mes.py (--parse-only, 창을 띄우지 않음)
@pytest.mark.parametrize("mode", ["bare", "idonly"])
def test_menu_takes_bare_and_idonly(mode): ...
@pytest.mark.parametrize("region", ["filters", "grid", "icon", "radial", "arrow"])
def test_idonly_is_for_the_menu_only(region): ...                  # 종료 2
def test_bare_is_still_refused_outside_menu_and_arrow(): ...
def test_menu_container_id_and_dup_id_parse(): ...                 # 기본 on / none, off, same, other
def test_bad_menu_options_exit_2(): ...
# test_virtual_mes_live.py (VIRTUAL_MES_LIVE=1일 때만)
def test_idonly_menu_items_have_a_fixed_id_no_name_and_no_ref(): ...
def test_menu_ids_survive_a_restart(): ...
def test_a_real_click_on_an_idonly_menu_item_opens_its_list(): ...
def test_dup_id_other_puts_the_same_role_and_id_in_another_container(): ...
```

- [ ] **2단계:** 빌드한 뒤 `pytest tests/test_virtual_mes.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현하고 README의 옵션 표, 노출 방식 표, ID 표를 고칩니다.** 이 PC의 `build\VirtualMes.args`는 고치지 않습니다.
- [ ] **4단계:** `powershell -NoProfile -File virtual-mes\build.ps1` 뒤 `pytest`. 기대: 전체 통과(라이브 테스트는 건너뜀).

### 작업 8: 점검 (w2, 작업 1·2·3 수락 뒤)

**파일:** `check.py`, `agent/checks.py`, `agent/survey.py`. 테스트: `tests/test_check.py`, `tests/test_survey.py`.

**인터페이스:** K2, K4, K5를 쓰고, K10을 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_check.py
def test_settings_line_counts_the_profile_and_shows_none_of_it(): ...
def test_a_broken_profile_fails_the_settings_check_with_the_place_only(): ...
def test_the_ready_check_uses_the_profile_signals_and_read_mode(): ...
def test_each_round_is_one_line_of_numbers(): ...
def test_many_rounds_keep_the_first_and_last_ten(): ...           # 25바퀴 → 20줄과 "… 5바퀴 생략"
def test_both_ways_are_timed_after_ready_and_the_faster_is_recommended(): ...
def test_no_recommendation_when_a_way_misses_a_signal(): ...
def test_same_ids_after_a_restart_add_the_stable_ids_hint(): ...
def test_check_output_holds_no_signal_text_alias_or_id(): ...      # 전체 출력에서 프로파일의 값을 찾아 없음
# test_survey.py
def test_id_only_counts_nodes_with_an_id_and_a_place_but_no_ref_or_name(): ...
```

- [ ] **2단계:** `pytest tests/test_check.py tests/test_survey.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** `Target`에 `ready`, `ready_read`, `profile`을 두고 `pick_target`이 프로파일을 읽어 `apply_ready`에 넘깁니다. `check.py`에 `--profile-dir`.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

### 작업 9: 등록 화면 (w3, 작업 1·2·3·4 수락 뒤)

**파일:** `viewer/profile_editor.py`(새), `viewer/run_manager.py`, `viewer/server.py`, `viewer/static/profiles.js`(새), `viewer/static/index.html`, `viewer/static/style.css`, `viewer/static/app.js`, `view.py`. 테스트: `tests/test_profile_editor.py`(새), `tests/test_run_manager.py`, `tests/test_server.py`, `tests/test_view.py`.

**인터페이스:** K1~K6을 쓰고, K11과 K9의 `RunManager`·`view.py`를 만듭니다.

- [ ] **1단계: 실패하는 테스트를 씁니다.**

```python
# test_profile_editor.py
def test_launch_lists_the_windows_of_the_launched_process_and_new_ones_only(): ...   # 다른 앱의 창 제목이 없음
def test_a_failed_launch_says_to_close_and_start_again(): ...        # EditorError 409, 글에 경로 없음
def test_read_gives_boxes_and_the_id_nodes_with_children(): ...
def test_read_waits_the_delay_on_the_server(): ...                   # delay_s=3 → sleep(3) 뒤에 snapshot
def test_a_window_that_cannot_be_pictured_is_still_read(): ...       # screenshot 실패 → image None, items는 있음
def test_only_reading_commands_reach_the_desktop(): ...              # 쓴 명령 ⊆ launch, list-windows, snapshot, find, screenshot
def test_check_times_both_ways_and_says_which_signal_is_missing(): ...
def test_save_writes_the_profile_and_keeps_names_from_other_screens(): ...
def test_save_with_an_older_read_is_refused(): ...                   # 409
@pytest.mark.parametrize("case", [...])     # 한 창에서 둘과 맞는 등록 이름, 둘과 맞는 ID 신호, 읽은 창에 없는 이름 신호, 제목과만 같은 신호
def test_problems_name_the_row_and_no_value(case): ...
def test_two_containers_with_the_same_id_can_both_be_saved(): ...
def test_an_unchanged_ready_list_is_not_checked_again(): ...
def test_close_removes_the_pictures(): ...
# test_run_manager.py
def test_exclusive_refuses_while_a_run_is_active(): ...
def test_a_run_cannot_start_while_exclusive_is_held(): ...
def test_a_viewer_run_gets_the_profile_like_run_py(): ...            # ready, Toolbox, RunRecorder, use_ids
def test_a_broken_profile_is_an_invalid_run(): ...
# test_server.py
def test_profile_routes_need_the_token(): ...
def test_profile_routes_answer_503_without_a_run_manager(): ...
def test_reading_during_a_run_is_409(): ...
def test_the_profile_answers_hold_no_exe_path(): ...
def test_profiles_js_sets_text_with_textcontent_only(): ...          # 지금 workflows.js를 보는 방식
def test_the_run_view_box_tooltip_shows_id_and_alias(): ...          # app.js가 요소의 native_id와 alias를 읽음
def test_the_exported_html_has_no_register_view(): ...
# test_view.py
def test_view_takes_profile_dir(): ...
```

- [ ] **2단계:** `pytest tests/test_profile_editor.py tests/test_run_manager.py tests/test_server.py tests/test_view.py`. 기대: 새 테스트 실패.
- [ ] **3단계: 구현합니다.** 화면은 설계 5절의 그림과 흐름을 따릅니다: 시스템 고르기, 앱 띄우기, 창 고르기, 화면 읽기와 3초 뒤 읽기(남은 초 표시), 박스, 고른 요소와 "준비 신호로", 이름 없는 ID 요소의 표(잘라 낸 그림, 역할, ID, 담긴 영역, 이름 칸), 준비 신호 목록, "ID는 다시 켜도 같음" 체크 칸, 읽는 방식 고르기, "지금 확인", "저장". 준비 신호가 ID 신호 하나뿐이면 "이름 신호나 다른 ID 신호를 함께 두세요"를 보여 주되 저장은 됩니다. 문제는 그 줄에 보여 줍니다. 뷰어를 끌 때 `ProfileEditor.close()`를 부릅니다.
- [ ] **4단계:** `pytest`. 기대: 전체 통과.

---

## 통합과 실제 창 확인 (오케스트레이터)

1. 작업마다 보고서가 아니라 산출물로 검토합니다: 파일을 읽고, 핵심 테스트를 직접 다시 돌리고, 테스트 한두 개는 구현을 되돌려 실패하는지 봅니다.
2. 모두 수락한 뒤 전체 `pytest`, 그리고 "실패로 보는 것" 네 항목마다 그것을 막는 테스트가 있는지 대조합니다.
3. README, `CLAUDE.md`, 설계와 계획의 상태 줄을 고칩니다.
4. 설계 8절의 실제 창 확인 다섯 가지는 창을 띄우므로 사용자에게 확인받은 뒤에 합니다.
