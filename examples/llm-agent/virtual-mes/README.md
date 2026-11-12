# 가상 MES 앱

회사 MES의 화면과 조작 흐름을 흉내 낸 WinForms 앱입니다. 실제 MES는 집 PC에서 실행할 수 없으므로, CUA의 공통 기능을 이 앱으로 개발하고 시험합니다. 컨트롤이 접근성 트리에 보이는 방식을 실행 옵션으로 바꿀 수 있습니다.

설계는 [`../docs/specs/2026-11-12-virtual-mes-app-design.md`](../docs/specs/2026-11-12-virtual-mes-app-design.md), 배경은 [`../docs/cua-background.md`](../docs/cua-background.md)에 있습니다.

## 빌드

```powershell
powershell -NoProfile -File examples\llm-agent\virtual-mes\build.ps1
```

`build\VirtualMes.exe`가 생깁니다. Windows에 들어 있는 .NET Framework 컴파일러(`%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`)만 쓰므로 따로 설치할 것이 없습니다. 이 컴파일러는 C# 5까지만 받습니다. 문자열 보간, `nameof`, `?.`는 쓸 수 없습니다.

## 실행

```powershell
.\build\VirtualMes.exe                                   # 기본 프로파일 mes-like
.\build\VirtualMes.exe --profile easy --delay-ms 300
.\build\VirtualMes.exe --expose radial=text --log C:\temp\events.jsonl
```

| 옵션 | 뜻 |
| --- | --- |
| `--profile mes-like\|easy\|hard` | 영역별 노출 방식 묶음. 기본 `mes-like` |
| `--expose <영역>=<방식>[,...]` | 프로파일 위에 영역별로 덮어씀. 영역은 `menu`, `filters`, `grid`, `icon`, `radial`, 방식은 `ref`, `text`, `pixel`. `icon`은 `text`를 받지 않음 |
| `--delay-ms <N>` | 조회 대기 시간을 N밀리초로 고정. 없으면 조회마다 1000~4000 |
| `--seed <N>` | 대기 시간 순서의 시드. 기본 `20261112`. 같은 시드면 같은 순서 |
| `--log <경로>` | 이벤트 기록을 이 파일에 덧붙임 |
| `--export-headless <화면> <필터 JSON> <출력 .xlsx>` | 창 없이 그 화면의 조회 결과를 저장하고 끝남 |
| `--parse-only` | 테스트용. 옵션을 해석한 결과를 JSON 한 줄로 출력하고 끝남 |

종료 코드는 `0` 성공, `1` 파일을 쓰지 못함, `2` 옵션이나 필터가 틀림입니다. 틀린 이유는 표준 오류에 나옵니다.

**옵션 파일.** 인자 없이 실행하면 실행 파일 옆의 `VirtualMes.args` 첫 줄을 인자로 씁니다. `agent-desktop launch`와 에이전트는 인자 없이 앱을 띄우므로, 프로파일이나 이벤트 기록을 지정하려면 이 파일을 먼저 써 둡니다.

```powershell
Set-Content build\VirtualMes.args '--profile hard --log "C:\temp\events.jsonl"' -Encoding utf8
```

## 노출 방식

화면 모양은 세 방식에서 완전히 같습니다. 접근성 트리에 내보내는 것만 다릅니다.

| 방식 | `agent-desktop snapshot`에 보이는 것 |
| --- | --- |
| `ref` | 이름, ref, `AutomationId`가 있는 `button`, `checkbox`, `cell`. 조작 대상이 아닌 글자는 `statictext` |
| `text` | 이름과 위치가 있는 `statictext`. ref 없음. 체크박스의 체크 표시와 상태는 나오지 않음 |
| `pixel` | 이름 없는 `group` 하나. 안의 글자는 나오지 않음 |

| 프로파일 | menu | filters | grid | icon | radial |
| --- | --- | --- | --- | --- | --- |
| `mes-like` | ref | text | text | pixel | pixel |
| `easy` | ref | ref | ref | ref | ref |
| `hard` | text | pixel | pixel | pixel | pixel |

`grid`에는 표, 화면 이름 탭, 데이터 패널의 화면 이름, 상태 줄, 불량 상세 팝업의 표가 들어갑니다. "조회(F5)" 버튼, 날짜 입력 칸, "저장 | 열기 | 취소" 버튼, 로딩 팝업의 안내 글, 알림 창, 저장 대화상자는 표준 컨트롤이라 어느 프로파일에서도 보입니다.

메뉴 목록, 드롭다운 목록, 원형 메뉴는 본 창과 별개의 창이며 `list-windows`에 나옵니다. 창 제목은 내용을 드러내지 않습니다(`메뉴 목록`, `목록`, `메뉴`).

## 화면

"생산운영"을 누르면 목록 창에 다섯 화면이 나옵니다.

| 목록 항목 | 화면 id | 필터 | 저장 방법 |
| --- | --- | --- | --- |
| 전공정 생산현황 | `front-process` | Shop(3칸 모두 활성), 공정, 제품, 기간 | 엑셀 아이콘, 우클릭 원형 메뉴 |
| 후공정 생산현황 | `rear-process` | 없음(빈 화면) | |
| 공정별 불량현황 | `defect-by-process` | 제품, 기간 | 우클릭 원형 메뉴. 숫자 셀을 누르면 불량 상세 팝업 |
| 권취 Roller 점검일시 | `roller-check` | Shop(3번 칸만 활성), 설비, 기간 | 우클릭 원형 메뉴 |
| 설비 가동현황 | `equipment-status` | 없음(빈 화면) | |

- **Shop 칸:** 누르면 체크박스 목록이 뜹니다. "All"은 전부 체크, "Select"는 적용하고 닫기입니다. 목록 밖을 눌러도 체크한 대로 적용되며, 그 클릭은 아래 컨트롤에 전달되지 않습니다. Esc는 적용하지 않고 닫습니다. 3번 칸의 행은 `WA0 #울산 믹싱`, `WA1 #울산 극판`, `WA2 #울산 조립`, `WA3 #울산 화성`이고 처음에는 전부 체크되어 있습니다.
- **기간:** "전일", "전전일", "전주", "기간". "기간"을 고르면 날짜 칸 2개가 켜집니다. 날짜가 비었거나 형식이 틀리면 조회하지 않고 알림 창을 띄웁니다.
- **조회:** "조회(F5)" 버튼이나 F5 키. 로딩 팝업이 대기 시간 동안 뜹니다.
- **원형 메뉴:** 표를 우클릭하면 그 자리에 뜹니다. 1단계는 위부터 시계 방향으로 export, copy, print, close이고, export를 누르면 excel(오른쪽 위), html(아래), pdf(왼쪽 위)로 바뀝니다. excel만 저장 대화상자를 엽니다.

앱의 "오늘"은 2026-11-12로 고정입니다. "전일"은 언제 실행해도 2026-11-11이고, 데이터는 2026-11-01부터 2026-11-11까지 있습니다. 같은 필터면 항상 같은 행이 나옵니다. 값을 만드는 식은 `MesData.cs`에 있습니다.

## 이벤트 기록

`--log`를 주면 한 줄에 JSON 하나씩 덧붙입니다. 모든 줄에 `seq`(1부터), `time`, `event`가 있습니다. 앱의 어느 창에도 이 기록의 내용이나 경로는 나오지 않습니다. 테스트 하니스만 읽습니다.

| `event` | 그 밖의 필드 |
| --- | --- |
| `app_started` | `profile`, `expose`, `seed` |
| `menu_opened` | `category` |
| `screen_opened` | `screen`, `title` |
| `filter_changed` | `screen`, `filter`, `value`, `closed_by`(`item_click`, `select_button`, `outside_click`, `deactivated`, `typed`) |
| `query_started` | `screen`, `filters`, `started_by`(`button`, `f5`), `delay_ms` |
| `query_finished` | `screen`, `rows` |
| `query_rejected` | `screen`, `reason`(`invalid_period`) |
| `cell_popup_opened` | `screen`, `row`, `column` |
| `radial_opened` | `stage`(1, 2), `source`(`panel`, `cell_popup`) |
| `radial_closed` | `stage`, `reason`(`choice`, `outside_click`, `escape`, `deactivated`) |
| `export_choice` | `route`(`icon`, `radial`), `choice`(`save`, `open`, `cancel`, `excel`, `pdf`, `html`, `copy`, `print`, `close`) |
| `export_saved` | `path`, `source`, `rows`, `screen`, `filters`(저장한 표를 조회했을 때의 필터. 조회 뒤에 바꾼 필터는 들어가지 않음) |
| `export_failed` | `path`, `reason` |
| `export_rejected` | `reason`(`no_data`) |

`filter_changed`의 `filter`는 `shop-1`, `shop-2`, `shop-3`, `process`, `product`, `equipment`, `period`, `from`, `to`입니다. Shop 칸의 `value`는 코드 배열입니다. 값이 그대로면 기록하지 않습니다.

## 창 없는 내보내기

```powershell
.\build\VirtualMes.exe --export-headless front-process '{\"shop\":[\"WA2\"],\"period\":\"전주\"}' C:\temp\out.xlsx
```

화면 id는 `front-process`, `roller-check`, `defect-by-process`, `defect-detail`입니다. 필터 키는 `shop`(코드 배열), `process`, `product`, `equipment`, `period`, `from`, `to`, `row`이고, 빠진 키는 기본값(전부, `전체`, `전일`)입니다. `defect-detail`은 `row`(`믹싱`, `극판`, `조립`, `화성`)가 필요합니다. PowerShell 5.1에서는 JSON의 큰따옴표를 `\"`로 적습니다.

저장되는 파일은 시트 하나짜리 `.xlsx`이고, 모든 셀은 화면에 보이는 글자 그대로입니다.

## 테스트

```powershell
Set-Location examples\llm-agent
python -m pytest tests\test_virtual_mes.py -q              # 창을 띄우지 않음
$env:VIRTUAL_MES_LIVE = '1'
python -m pytest tests\test_virtual_mes_live.py -q         # 실제 창을 띄움
```

`VIRTUAL_MES_LIVE=1`인 테스트는 앱 창을 여러 번 띄우고 닫으며, 일부는 실제 마우스 커서를 움직이고 키를 입력합니다. PC를 쓰지 않을 때만 돌리세요. `agent-desktop.exe`는 `AGENT_DESKTOP_BIN`, `target\release`, `target\debug` 순으로 찾습니다. `VirtualMes.exe`가 빌드되어 있지 않으면 두 파일 모두 건너뜁니다.

## 이 앱으로 확인한 것

에이전트 쪽 설계에 필요한 사실입니다. 표준 저장 대화상자 항목은 조사로 확인했고 테스트는 그 동작을 피해 갑니다. 나머지는 테스트가 고정합니다.

- **표준 저장 대화상자는 `set-value`를 저장에 쓰지 않습니다.** 파일 이름 칸에 `set-value`로 넣은 경로는 화면과 트리에는 보이지만, 저장은 기본 파일 이름으로 됩니다. `--headed type`으로 실제 키를 입력해야 합니다(`tests\virtual_mes_support.py`의 `save_dialog`).
- **대화상자를 여는 클릭은 바로 돌아옵니다.** 앱은 모달 창을 여는 동작을 클릭 처리 뒤로 미루므로, `click`이 모달 창이 닫힐 때까지 막히지 않습니다.
- **좌표 클릭은 팝업을 활성 창으로 만듭니다.** `--headed mouse-click`으로 팝업 안을 누르면 팝업이 활성 창이 됩니다. 앱은 활성 창이 팝업이나 그 소유 창인 동안에는 팝업을 닫지 않습니다. 다른 앱으로 넘어가면 닫습니다.
- **로딩 팝업이 떠 있는 동안에는 ref로도 본 창을 조작할 수 없습니다.** 그동안 본 창의 요소는 `disabled`로 나오고 `click`은 실패합니다.
- **스크롤로 가려진 셀은 `offscreen`으로 나옵니다.** 위치는 그대로 보고되지만 창 밖을 가리킬 수 있으므로, `offscreen`인 요소의 좌표는 누르면 안 됩니다.
- **`ref`가 없는 요소는 좌표로만 누를 수 있습니다.** `text` 방식의 글자는 `bounds`의 가운데를 `--headed mouse-click`으로 누릅니다. 우클릭은 `--button right`입니다.
