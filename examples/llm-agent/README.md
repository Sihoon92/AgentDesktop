# LLM 에이전트 예제

OpenAI Responses API와 `agent-desktop` CLI로 관찰-동작 루프를 돌립니다. 작업 파일에 적은 목표를 LLM이 수행하고, 하니스가 성공 조건을 직접 읽어 판정합니다.

`agent-desktop`은 LLM을 내장하지 않으므로 이 루프는 호출하는 쪽인 Python에 있습니다.

## 준비

Windows PowerShell 기준입니다. 저장소 루트에서 CLI를 빌드합니다.

```powershell
cargo build -p agent-desktop
Set-Location examples\llm-agent
conda activate AgentDesktop
pip install -r requirements.txt
```

API 키는 환경변수나 `examples\llm-agent\.env`에 넣습니다. `.env`는 Git에서 제외되어 있습니다.

```powershell
$env:OPENAI_API_KEY = '<키>'
```

## 실행

```powershell
python run.py tasks\footprint_open_settings.yaml --baseline
python run.py tasks\footprint_open_settings.yaml
```

`--baseline`은 LLM 없이 고정 순서로 실행하므로 키가 필요 없습니다. 두 번째 명령은 LLM을 호출합니다.

작업 파일 없이 지시를 바로 줄 수도 있습니다.

```powershell
python run.py --exe "C:\절대\경로\App.exe" --goal "5분봉으로 바꿔줘" [--level 0-3]
```

`--level`의 기본값은 `3`입니다. 이 방식에는 성공 조건이 없어 하니스가 결과를 확인하지 못하므로, 모델이 성공을 주장하면 `claimed`로 끝납니다. 결과는 출력된 마지막 관찰이나 앱 화면에서 직접 확인합니다. 작업 파일과 `--goal`을 함께 주면 작업 파일의 `goal`만 바뀌고 성공 조건은 그대로 씁니다. 대상 앱이 이미 떠 있으면 같은 경로의 프로세스에 붙습니다.

| 종료 코드 | 뜻 |
| --- | --- |
| `0` | 하니스가 성공 조건을 확인함 |
| `1` | 실행했지만 성공 조건을 확인하지 못함 |
| `2` | 앱을 건드리기 전에 설정 오류로 중단함 |
| `3` | 성공 조건 없이 모델이 성공을 주장함(`claimed`, 미검증) |

`--approve`를 붙이면 관찰이 아닌 동작마다 터미널에서 승인을 묻습니다. `y`면 실행하고, 그 밖의 답은 거부입니다. 거부한 동작은 앱에 보내지 않고 이유를 모델에 돌려줍니다.

```powershell
python run.py --exe "C:\절대\경로\App.exe" --goal "새 탭을 열어줘" --level 1 --approve
```

실행이 끝나면 기록 폴더, 그 실행을 나중에 다시 여는 명령(`나중에 보기: python view.py --open <run_id>`), `trace export` 명령을 출력합니다. `trace export`로 CLI trace를 HTML로 내보낼 수 있습니다.

## 실행 기록

실행마다 `runs\<YYYYMMDD-HHMMSS>\`에 기록이 남습니다.

| 파일 | 내용 |
| --- | --- |
| `run.json` | 지시, 실행 파일, 모델, 권한 단계, 시작 방법, 상태(`running`에서 최종 상태로), 시작·끝 시각, 기록하는 프로세스와 10초마다 갱신하는 살아 있음 표시 |
| `events.jsonl` | 관찰, 동작, 모델 호출, 승인 제안·결정, 종료를 한 줄에 하나씩 |
| `frames\NNNN.png` | 관찰마다 찍은 대상 창 스크린샷 |

관찰마다 창 스크린샷을 한 장 찍으므로 스텝마다 약 1초가 더 걸립니다. 기록에는 화면 내용과 UI 텍스트가 그대로 들어가므로 민감 정보로 다루세요. `runs\`는 Git에서 제외되어 있습니다. 관찰한 창 제목과 UI 텍스트는 모델 API로도 전송됩니다.

## 단계 뷰어

`run.py`에 `--view`를 붙이면 실행할 때마다 뷰어가 브라우저로 열리고 그 실행을 실시간으로 따라갑니다. 실행이 끝나도 Enter를 누를 때까지 뷰어가 남아 있어 단계를 돌려 볼 수 있습니다.

```powershell
python run.py --exe "C:\절대\경로\App.exe" --goal "5분봉으로 바꿔줘" --view
```

기록을 보거나 브라우저에서 새 실행을 시작하려면 뷰어만 띄웁니다.

```powershell
python view.py --exe "C:\절대\경로\App.exe"
```

브라우저에 뷰어가 열립니다. 주소에 실행할 때마다 새로 만든 토큰이 붙고, 서버는 `127.0.0.1`에서만 받습니다. 주소를 다른 사람과 공유하지 마세요.

- **재생:** 왼쪽에서 실행을 고르면 단계마다 스크린샷 위에 박스를 그립니다. 파란 테두리는 에이전트가 조작할 수 있다고 인식한 요소, 회색 점선은 모델이 읽은 텍스트(ref 없음, 관찰 하나에 400개까지), 빨간 테두리는 조작한 대상입니다. 박스에 마우스를 올리면 역할, 이름, 설명이 나옵니다. 오른쪽에 모델이 적은 동작 이유(`intent`), 도구와 인자, 전달 결과, 모델이 받은 관찰 텍스트가 나옵니다.
- **키:** `←` `→`로 단계를 옮기고, `B`로 동작 전·후 화면을, `A`로 전체 요소·대상만을, `T`로 텍스트 박스 표시를 바꿉니다.
- **새 실행:** 위쪽에 지시, 실행 파일, 권한 단계를 넣고 실행하면 진행 중인 단계가 1초마다 붙습니다. 한 번에 하나만 실행할 수 있습니다. 뷰어에서 시작한 실행은 터미널 확인을 받을 수 없으므로 `restart_target`이 항상 거부됩니다.
- **승인:** 승인 모드로 시작하면 동작마다 대상을 노란 박스로 보여 주고 승인이나 거부를 기다립니다. 600초 동안 답이 없으면 실행을 멈춥니다.
- **경고 띠:** 스크린샷을 찍지 못했거나 찍는 사이 창이 움직였으면 박스가 어긋날 수 있다고 표시합니다.

`--runs`로 다른 기록 폴더를 볼 수 있고, `--no-browser`는 주소만 출력합니다. `agent-desktop` 바이너리가 없으면 기록 보기만 합니다.

### 지난 실행 보기

뷰어는 `run.py`가 돌고 있지 않아도 `runs\`의 기록을 모두 보여 줍니다. 실행 하나를 바로 열려면 `--open`에 실행 ID나 `latest`를 줍니다.

```powershell
python view.py --open latest
python view.py --open 20261107-132854
```

- **찾기:** 목록은 날짜별로 묶이고, 위쪽에서 지시나 실행 파일 이름으로 검색하거나 상태와 날짜로 거를 수 있습니다. 항목마다 판정, 스텝, 토큰, 걸린 시간, 시작 방법이 나옵니다.
- **중단됨:** `running`으로 남은 기록 중 기록하던 프로세스가 없거나, 같은 PID가 다른 프로세스에 쓰였거나, 살아 있음 표시가 60초 넘게 멈춘 것은 "중단됨"으로 보입니다. 강제 종료, Ctrl+C, PC 재시작으로 끊긴 실행입니다. 뷰어는 기록 파일을 고치지 않습니다.
- **바뀐 점:** 동작 단계에 동작 전후 화면에서 생긴 요소, 사라진 요소, 상태(`checked` 등)나 값이 바뀐 요소가 나옵니다. 동작 후 화면에서는 바뀐 요소를 초록 박스로, 동작 전 화면에서는 사라진 요소를 빨간 점선으로 표시합니다. 요소 상태가 기록되기 전의 기록은 생김과 사라짐만 보입니다.
- **실행 요약:** 타임라인 위에 지시, 판정과 판정 방식(하니스가 확인, 모델 주장만 등), 걸린 시간, 스텝, 토큰, 승인·거부 수가 나옵니다.
- **옛 로그:** 기록 형식이 생기기 전의 `runs\<id>.jsonl`은 "옛 로그"로 보이고, 스크린샷 없이 단계별 도구, 인자, 결과만 나옵니다.

### 기록 정리

오래된 기록은 자동으로 지우지 않습니다. `--prune`은 지울 기록과 용량을 출력만 하고, `--yes`를 붙여야 지웁니다. 지운 기록은 휴지통을 거치지 않으므로 되돌릴 수 없습니다.

```powershell
python view.py --prune --older-than 30d     # 30일보다 오래된 기록을 보여 주기만 함
python view.py --prune --keep 100 --yes     # 최신 100개만 남기고 영구 삭제
```

`--older-than`과 `--keep` 중 하나만 줍니다. 뷰어 목록이나 실행 요약에서 ☆를 눌러 보관한 기록(기록 폴더에 `keep` 파일이 생김), 실행 중인 기록, 읽을 수 없는 기록, 링크나 정션은 지우지 않고 이유와 함께 건너뜁니다. 상태 필터의 "보관"으로 보관한 기록만 볼 수 있습니다.

### 한 파일로 내보내기

```powershell
python view.py --export latest C:\공유\run.html
```

실행 하나를 스크린샷까지 HTML 파일 하나로 묶습니다. 서버 없이 브라우저에서 열리고 그 실행만 재생합니다. 토큰과 서버 주소는 들어가지 않지만 앱 화면, UI 텍스트, 실행 파일 경로는 그대로 들어가므로 보내기 전에 확인하세요. 이미 있는 파일은 덮어쓰지 않습니다.

### 박스 좌표

UIA 요소 영역과 `list-windows` 창 영역은 물리 화면 픽셀이고, 창 영역에는 보이지 않는 테두리가 포함됩니다. 창 스크린샷은 보이는 부분만 찍으므로, 스크린샷 왼쪽 위의 화면 좌표를 다음처럼 구합니다.

```text
border = (창 너비 - 이미지 너비) / 2
원점   = (창 x + border, 창 y + (창 높이 - 이미지 높이) - border)
```

요소 박스는 화면 좌표에서 원점을 빼서 이미지 좌표로 바꾸고, 이미지 밖은 잘라냅니다. 175% 주 모니터의 일반 창(테두리 10px)과 최대화 창(테두리 12px, 원점 0,0), 125% 보조 모니터의 창(테두리 8px)에서 FootprintChart와 메모장으로 확인했습니다.

## 작업 파일

```yaml
goal: 에이전트에게 줄 목표
exe: C:\절대\경로\App.exe
permission_level: 3
success:
  require:
    - {role: switch, description_contains: 설정 패널, states: [checked]}
    - {role: statictext, name: 지표}
baseline_target: {role: switch, description_contains: 설정 패널}
```

대상 프로세스의 창 하나에 `require`의 요소가 모두 있어야 성공입니다. 요소 조건은 `role`, `name`, `name_contains`, `description_contains`, `states`를 쓸 수 있습니다. 선택 항목의 기본값은 `max_steps: 12`, `token_budget: 200000`, `model: gpt-6.1-terra`, `reasoning_effort: low`입니다.

## 권한 단계

| 단계 | 추가되는 도구 | 앱에 미치는 영향 |
| --- | --- | --- |
| 0 | `launch_target`, `list_windows`, `observe`, `wait_for_text`, `restore_window`, `click`, `toggle`, `expand`, `collapse`, `finish` | 커서와 포커스를 가져가지 않음 |
| 1 | `set_value`, `select`, `check`, `uncheck`, `scroll`, `press` | `press`를 뺀 도구는 같음 |
| 2 | `type`, `double_click`, `right_click`, `mouse_click`, `mouse_wheel`, `drag` | 실제 커서와 포커스를 사용하며, 2단계부터 `press`도 포커스를 가져감 |
| 3 | `restart_target` | 실행 전 터미널에서 확인을 받고, 강제 종료는 하지 않음 |

## 하니스가 막는 동작

- `exe`와 경로가 같은 프로세스의 창만 다룹니다. 같은 이름의 다른 버전은 대상이 아닙니다.
- 다른 프로세스를 띄우고 끝나는 실행기는 대상으로 쓸 수 없습니다. 예를 들어 Windows 11의 `C:\Windows\System32\notepad.exe`는 WindowsApps의 `Notepad.exe`를 띄우므로, 에이전트가 창을 찾지 못하고 포기합니다. 실제 앱의 경로(`Get-Process Notepad | Select-Object Path`)를 지정하세요.
- 앱이 이전 세션을 복원하면(메모장의 지난 탭 등) 그 내용도 관찰에 포함되어 모델 API로 전송되고 기록에 남습니다.
- 최신 관찰에 없는 ref는 앱에 보내지 않습니다.
- 전달된 `toggle`은 같은 컨트롤에 다시 보내지 않습니다. `disposition.retry`가 `safe`가 아닌 실패도 반복하지 않습니다.
- 같은 동작이 3번 실행된 뒤 다시 요청되면 실행을 중단합니다.
- 좌표 입력은 점이 대상 창 안에 있고 그 창이 포커스를 얻었을 때만 보냅니다.
- `press`에 `--force`를 붙이지 않으므로 CLI가 막는 단축키는 계속 막힙니다.

## 테스트

```powershell
python -m pytest -q
```

테스트는 `tests\fixtures`에 캡처한 CLI 응답을 재생하므로 앱을 조작하지 않습니다.

## OpenAI 호환 API

`OPENAI_BASE_URL`을 설정하면 다른 엔드포인트로 호출합니다. 도구 호출에 Responses API를 쓰므로, 해당 엔드포인트가 이 API를 지원해야 합니다.
