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
| `--expose <영역>=<방식>[,...]` | 프로파일 위에 영역별로 덮어씀. 영역은 `menu`, `filters`, `grid`, `icon`, `radial`, `arrow`, 방식은 `ref`, `text`, `bare`, `idonly`, `noaction`, `rows`, `pixel`. `icon`은 `text`를 받지 않음. `arrow`는 `ref`, `bare`, `pixel`만 받음. `bare`는 `menu`와 `arrow`만, `idonly`와 `noaction`은 `menu`만, `rows`는 `grid`만 받음 |
| `--menu-container-id on\|off` | 메뉴 항목을 담은 영역(메뉴 줄, 메뉴 목록, 아래 `other`의 띠)에 `AutomationId`를 둠. 기본 `on`. `off`면 그 영역에 ID가 없음. 아래 "메뉴의 ID" |
| `--menu-dup-id none\|same\|other` | 첫째 메뉴 항목과 같은 `AutomationId`를 쓰는 요소를 하나 더 둠. 기본 `none`. `same`은 메뉴 줄의 둘째 항목, `other`는 메뉴 줄 아래 띠의 요소 하나. 아래 "메뉴의 ID" |
| `--delay-ms <N>` | 조회 대기 시간을 N밀리초로 고정. 없으면 조회마다 1000~4000 |
| `--seed <N>` | 대기 시간 순서의 시드. 기본 `20261112`. 같은 시드면 같은 순서 |
| `--combo-open arrow\|anywhere` | 필터 칸이 목록을 여는 방식. 기본 `arrow`(칸 오른쪽 끝의 화살표 버튼으로만 열림, 아래 "선택 칸 여는 방식"). `anywhere`는 칸 어디를 눌러도 열리는 옛 동작으로, 비교할 때만 씀 |
| `--log <경로>` | 이벤트 기록을 이 파일에 덧붙임 |
| `--splash-ms <N>` | 작은 팝업 창(제목 `VirtualMes`, 글 `불러오는 중`)을 N밀리초 띄운 뒤 닫고 메인 화면을 띄움. 아래 "시작 과정" |
| `--launcher` | `--splash-ms`와 함께. 팝업을 띄운 프로세스가 메인 화면을 다른 프로세스로 띄우고 끝남 |
| `--single-instance` | `--launcher`와 함께. 같은 exe의 메인 화면이 이미 떠 있으면 실행기가 팝업도 새 창도 없이 끝남 |
| `--menu-delay-ms <N>` | 메인 화면이 뜬 뒤 N밀리초 지나서 메뉴를 채움. 그동안 메뉴 줄은 비어 있음 |
| `--startup-notices <N>` | 메인 화면이 보이고 메뉴가 채워진 뒤, 메인 화면이 소유한 모달 공지 창 N개(0~5)를 하나씩 띄움. 기본 `0`. 아래 "회사 화면의 모양" |
| `--dialogs messagebox\|form` | 알림 창을 Win32 메시지 상자(`messagebox`, 기본)로 띄울지, 앱이 만든 소유 모달 폼(`form`)으로 띄울지. 아래 "회사 화면의 모양" |
| `--nest <N>` | 화면 패널을 패널 N겹(0~12)으로 감쌈. 기본 `0`. 아래 "회사 화면의 모양" |
| `--export-headless <화면> <필터 JSON> <출력 .xlsx>` | 창 없이 그 화면의 조회 결과를 저장하고 끝남 |
| `--parse-only` | 테스트용. 옵션을 해석한 결과를 JSON 한 줄로 출력하고 끝남(`menu_container_id`, `menu_dup_id`, `startup_notices`, `dialogs`, `nest` 포함). `--launcher`면 메인 화면을 띄울 인자(`main_command_line`)도 나옴 |

종료 코드는 `0` 성공, `1` 파일을 쓰지 못함, `2` 옵션이나 필터가 틀림입니다. 틀린 이유는 표준 오류에 나옵니다.

**옵션 파일.** 인자 없이 실행하면 실행 파일 옆의 `VirtualMes.args` 첫 줄을 인자로 씁니다. `agent-desktop launch`와 에이전트는 인자 없이 앱을 띄우므로, 프로파일이나 이벤트 기록을 지정하려면 이 파일을 먼저 써 둡니다.

```powershell
Set-Content build\VirtualMes.args '--profile hard --log "C:\temp\events.jsonl"' -Encoding utf8
```

### 시작 과정

회사 MES는 실행하면 작은 팝업 창이 먼저 뜨고, 그다음 메인 화면이 뜨며, 메뉴가 보여야 다 열린 것입니다. 하니스의 준비 기다리기(설계 [`../docs/specs/2026-11-18-system-launch-ready-design.md`](../docs/specs/2026-11-18-system-launch-ready-design.md))를 집에서 시험하려고 이 과정을 옵션으로 흉내 냅니다. 시작 옵션이 하나도 없으면 지금처럼 메인 화면만 곧바로 뜹니다.

```powershell
Set-Content build\VirtualMes.args '--splash-ms 2000 --launcher --menu-delay-ms 2000' -Encoding utf8   # 다른 프로세스
Set-Content build\VirtualMes.args '--splash-ms 2000 --menu-delay-ms 2000' -Encoding utf8              # 같은 프로세스
Set-Content build\VirtualMes.args '--splash-ms 2000 --launcher --single-instance' -Encoding utf8      # 다시 눌러도 새 창 없음
```

- **같은 프로세스(`--splash-ms`만):** 팝업이 닫히면 같은 프로세스가 메인 화면을 띄웁니다.
- **다른 프로세스(`--launcher`):** 팝업이 닫히면 같은 exe를 숨은 옵션 `--main`과 나머지 인자로 띄우고 실행기는 끝납니다. 나머지 인자는 받은 인자(옵션 파일에서 읽었으면 그 줄)에서 `--splash-ms`, `--launcher`, `--single-instance`를 뺀 것이라 `--profile`, `--log`, `--menu-delay-ms`, `--startup-notices`, `--dialogs`, `--nest`는 메인 화면에도 그대로 적용됩니다. 공지 창은 메인 화면 프로세스에서 뜹니다. `--main`을 받은 프로세스는 옵션 파일을 읽지 않고 그 세 옵션을 무시하므로 실행기가 다시 뜨지 않습니다. `agent-desktop launch`가 돌려주는 pid는 실행기(팝업)의 것이고, 메인 화면은 다른 pid입니다.
- **이미 떠 있을 때(`--single-instance`):** 메인 화면 프로세스는 언제나 exe 전체 경로의 해시를 이름에 넣은 Mutex를 잡습니다. `--single-instance` 실행기는 그 Mutex가 있으면 팝업 없이 곧바로 끝납니다(`launcher_skipped`). 다른 폴더에 복사한 exe는 경로가 달라 막지 않습니다.
- **메뉴 늦게 채우기(`--menu-delay-ms`):** 메인 화면은 메뉴 줄이 빈 채로 뜨고, 창이 보인 뒤 N밀리초가 지나면 메뉴가 채워집니다. 창이 떴다고 준비된 것이 아님을 시험합니다.
- 팝업의 제목(`VirtualMes`)과 글(`불러오는 중`)은 메뉴 이름과 겹치지 않습니다.

## 노출 방식

화면 모양은 모든 방식에서 완전히 같습니다. 접근성 트리에 내보내는 것만 다릅니다.

| 방식 | `agent-desktop snapshot`에 보이는 것 |
| --- | --- |
| `ref` | 이름, ref, `AutomationId`가 있는 `button`, `checkbox`, `cell`. 조작 대상이 아닌 글자는 `statictext` |
| `text` | 이름과 위치가 있는 `statictext`. ref 없음. 체크박스의 체크 표시와 상태는 나오지 않음 |
| `bare` | 이름 없는 `button`. ref와 숫자 `AutomationId`가 있음. `menu`와 `arrow` 영역만 받음 |
| `idonly` | 이름 없는 `image`. 숫자 `AutomationId`만 있고 누르는 패턴(Invoke)이 없어 ref도 없음. 실제 클릭(좌표)에는 다른 방식과 똑같이 반응함. `menu` 영역만 받음 |
| `noaction` | 이름과 `ref`의 `AutomationId`가 있는 `button`. Invoke 패턴을 알리지 않음(`IsInvokePatternAvailable` 거짓). UIA가 Legacy 기본 동작을 스스로 붙여 CLI는 `Click`을 보이고, `click @ref`는 성공(`delivered_unverified`)으로 답하지만 앱은 아무것도 하지 않음. 실제 클릭(좌표)에만 반응함. `menu` 영역만 받음 |
| `rows` | 회사 표의 모양. 표(`table`) 아래 `Data Panel` > `Row <n>` > 칸. 칸은 이름이 `<열 머리글> row<k>`이고 값(`value`)이 화면 글자인 `DataItem`. GridItem·TableItem 패턴이 없어 CLI는 칸을 `row` 역할로 줄 수 있음. 머리글 칸은 나오지 않음. `grid` 영역만 받음 |
| `pixel` | 이름 없는 `group` 하나. 안의 글자는 나오지 않음 |

| 프로파일 | menu | filters | grid | icon | radial | arrow |
| --- | --- | --- | --- | --- | --- | --- |
| `mes-like` | ref | text | text | pixel | pixel | bare |
| `easy` | ref | ref | ref | ref | ref | ref |
| `hard` | text | pixel | pixel | pixel | pixel | pixel |

`mes-like`의 `arrow`가 `bare`인 것은 가정입니다. 실제 MES에서 화살표가 어떻게 보이는지 확인되면 맞춥니다.

`grid`에는 표, 화면 이름 탭, 데이터 패널의 화면 이름, 상태 줄, 불량 상세 팝업의 표가 들어갑니다. "조회(F5)" 버튼, 날짜 입력 칸, "저장 | 열기 | 취소" 버튼, 로딩 팝업의 안내 글, 알림 창, 저장 대화상자는 표준 컨트롤이라 어느 프로파일에서도 보입니다.

### 메뉴의 ID

회사 GSCM처럼 글자가 그림으로 그려지고 ID만 있는 메뉴를 흉내 냅니다. `menu=bare`는 이름 없는 버튼(ref 있음), `menu=idonly`는 이름도 ref도 없는 그림입니다. `idonly`의 `image`를 고른 이유: CLI는 역할이 `INTERACTIVE_ROLES`(`crates/core/src/tree/roles.rs`)에 있거나 주된 동작을 알리는 요소에만 ref를 주는데, `image`는 그 목록에 없는 읽기 전용 역할이고 이 요소는 어떤 패턴도 알리지 않습니다. 회사 GSCM의 메뉴도 Inspect에서 `IsInvokePatternAvailable`이 거짓입니다.

`bare`와 `idonly`의 메뉴 항목 `AutomationId`는 자리로 정해지는 고정된 숫자입니다. 화면이 달라도, 앱을 다시 켜도 같은 항목은 같은 숫자입니다(`MenuIds.cs`). `ref`의 ID는 지금처럼 `menu-<자리>`, `screen-<화면 id>`입니다.

| 메뉴 줄 | 기준정보 | 생산운영 | 품질관리 | 설비관리 | 자재관리 |
| --- | --- | --- | --- | --- | --- |
| `AutomationId` | 8001 | 8002 | 8003 | 8004 | 8005 |

| 메뉴 목록(생산운영) | 전공정 생산현황 | 후공정 생산현황 | 공정별 불량현황 | 권취 Roller 점검일시 | 설비 가동현황 | 공정별 생산집계 |
| --- | --- | --- | --- | --- | --- | --- |
| `AutomationId` | 8101 | 8102 | 8103 | 8104 | 8105 | 8106 |

메뉴 목록 창의 제목 줄(분류 이름)은 `bare`와 `idonly`에서 트리에 나오지 않습니다.

메뉴 항목을 담은 영역(`group`)에도 고정된 `AutomationId`가 있습니다: 메뉴 줄 `8000`, 메뉴 목록 `8100`, `--menu-dup-id other`의 띠 `8200`. `menu`가 `ref`, `text`, `bare`, `idonly`일 때 붙고(`pixel`이면 영역 자체가 이름 없는 `group` 하나뿐입니다), `--menu-container-id off`면 셋 다 없습니다. 기본 프로파일에서 바뀌는 것은 이 영역 ID뿐입니다. 그때 메뉴 항목에는 ID나 이름이 있는 조상이 창 말고 없습니다.

`--menu-dup-id`는 ID가 겹치는 경우를 만듭니다.

- `same`: 메뉴 줄의 둘째 항목(생산운영)이 첫째(기준정보)와 같은 `AutomationId`를 씁니다(`bare`와 `idonly`에서는 둘 다 `8001`, `ref`에서는 `menu-0`). 같은 영역 안에서 역할과 ID가 같은 두 요소이므로 담긴 영역으로도 갈리지 않습니다. 누르면 지금처럼 생산운영이 열립니다.
- `other`: 메뉴 줄 아래에 띠를 하나 더 두고, 그 안에 첫째 메뉴 항목과 모양, 역할, `AutomationId`가 같은 요소를 하나 둡니다. 담긴 영역이 메뉴 줄(`8000`)과 띠(`8200`)로 다르므로 담긴 영역으로 갈립니다. 누르면 기준정보의 목록이 열립니다. 띠 때문에 화면 본문이 띠 높이만큼 내려갑니다.

### 선택 칸 여는 방식

기본(`--combo-open arrow`)에서는 모든 필터 칸(Shop 세 칸, 공정, 제품, 설비, 기간)이 오른쪽 끝의 화살표 버튼으로만 열립니다. 실제 MES가 그렇습니다. 화살표 버튼은 칸 오른쪽 끝에 있는, 칸 높이만 한 정사각형이고, 테두리와 버튼 바탕색으로 칸과 구분되며 누르는 동안 눌림 표시가 됩니다. 칸의 나머지(몸통)는 흰 입력 칸처럼 그려집니다. 몸통을 누르면 글자 뒤에 깜빡이는 입력 커서만 그려지고 목록은 열리지 않습니다. 커서는 그림으로만 그려져 접근성 트리에 나오지 않습니다.

화살표가 접근성 트리에 보이는 방식은 `arrow` 영역이 정하며 `filters`와 따로 고릅니다. 화살표는 칸의 자식이 아니라 필터 열(칸들이 있는 `group`)의 자식이고, 자기 칸 바로 다음에 옵니다. 그래서 `filters`가 `text`나 `pixel`이라 칸이 글자이거나 아예 보이지 않아도 화살표는 버튼으로 나올 수 있습니다.

| `arrow` | 화살표 |
| --- | --- |
| `ref` | 이름 `열기`인 `button`. `AutomationId` `<칸 id>-arrow`(예: `filter-shop-3-arrow`) |
| `bare` | 이름 없는 `button`. `AutomationId`는 칸마다 고정된 숫자(아래 표). 캡처의 번호나 id로만 구분됨 |
| `pixel` | 없음. 칸의 오른쪽 끝 위치를 눌러야 함 |

`bare`의 숫자는 `7001`에 아래 순서의 자리(0부터)를 더한 값입니다. 화면이 달라도, 앱을 다시 켜도 같은 칸은 같은 숫자입니다(`ComboArrow.cs`의 `Keys`).

| 칸 | `shop-1` | `shop-2` | `shop-3` | `process` | `product` | `equipment` | `period` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `AutomationId` | 7001 | 7002 | 7003 | 7004 | 7005 | 7006 | 7007 |

화살표 버튼의 Invoke는 화살표 클릭과 같습니다(`combo_arrow_clicked` 뒤 목록이 열림). `filters`가 `ref`일 때 칸 자체의 Invoke는 몸통 클릭과 같아서 목록을 열지 않습니다. 비활성 칸은 몸통도 화살표도 반응하지 않고, 화살표 버튼은 `disabled`입니다.

`--combo-open anywhere`에서는 칸 어디를 눌러도 목록이 열리고, 화면 모양과 접근성 트리가 화살표가 기본이 되기 전과 같습니다. `arrow` 영역의 설정과 무관하게 화살표 요소가 없습니다.

### 회사 화면의 모양

회사의 첫 캡처 세션(2026-11-27)과 그 뒤의 확인에서 본 구조를 흉내 내는 옵션들입니다. 모두 기본은 꺼져 있어, 주지 않으면 화면, 트리, 이벤트 기록이 예전과 같습니다.

```powershell
Set-Content build\VirtualMes.args '--startup-notices 5 --dialogs form --expose menu=noaction' -Encoding utf8
Set-Content build\VirtualMes.args '--expose grid=rows --nest 12' -Encoding utf8   # 깊이 잘리는 회사 표
```

- **시작 공지(`--startup-notices <N>`):** 회사 MES는 켤 때 자기 프로세스의 소유·모달 창 다섯 개를 띄웁니다(H15). 메인 화면이 보이고 메뉴가 채워진 뒤(`--menu-delay-ms`가 있으면 그 뒤) 메인 화면이 소유한 모달 창을 하나씩 띄웁니다. 창은 WinForms `Form`이고 제목은 `공지 1`, `공지 2`처럼 번호, 본문은 한 줄(`시스템 안내 사항 1/5`), 버튼은 `확인` 하나입니다. 하나를 닫으면 다음 창이 뜨고, 모두 닫혀야 메인 화면을 쓸 수 있습니다(그동안 메인 화면의 요소는 `disabled`). 열림과 닫힘은 `notice_shown`, `notice_closed`로 기록됩니다. 시작 옵션의 `splash_*`, `main_started`, `menu_ready`와 달리 이 두 이벤트는 공지가 있으면 언제나 기록됩니다.
- **소유 폼 대화상자(`--dialogs form`):** 회사 MES의 대화상자는 Win32 `#32770`이 아니라 앱이 만든 소유 폼입니다(H18). `form`이면 `MessageBox.Show`로 띄우던 알림 창(조회의 기간 경고, 엑셀 저장의 데이터 없음과 저장 실패)을 같은 제목, 글, `확인` 버튼의 소유 모달 `Form`으로 띄웁니다. 창 클래스는 `#32770`이 아닙니다. 저장 대화상자(`SaveFileDialog`)는 그대로 표준 대화상자입니다.
- **누르기 기능 없는 버튼(`--expose menu=noaction`):** 회사 MES에는 버튼 역할이라 ref는 있지만 Invoke를 알리지 않는 요소가 있습니다(H04 `NONE(I0/V0/T0)`). `noaction`의 메뉴 항목은 `ref`처럼 이름과 `menu-<자리>`, `screen-<화면 id>` ID가 있는 `button`이지만, 사용자 정의 UIA 제공자가 Invoke 패턴을 주지 않고 `IsInvokePatternAvailable`을 거짓으로 답합니다. 실제 창에서 재 보니(2026-11-27) UIA가 이런 Button에 LegacyIAccessible 기본 동작을 스스로 붙입니다. 그래서 CLI는 동작 목록에 `Click`을 넣고, `click @ref`는 Invoke를 건너뛰고 `LegacyIAccessible.DoDefaultAction`으로 성공(`delivered_unverified`)을 답하지만 앱에는 아무 일도 일어나지 않습니다. 관리형 UIA 제공자는 Legacy 속성을 답할 수 없어(모르는 속성 번호를 걸러 냄) 이 기본 동작을 지울 수 없습니다. 실제 마우스 클릭은 다른 방식과 똑같이 메뉴를 엽니다. "누르기가 성공으로 보이는데 화면이 그대로"인 경우를 하니스가 어떻게 다루는지 보는 데 씁니다. 동작 목록에 `Click`이 아예 없는 ref의 실제 클릭 대체는 이 모드가 아니라 날짜 칸(`textfield`)으로 확인합니다(`tests/test_scenes_live.py`).
- **회사 모양 표(`--expose grid=rows`):** 회사 표의 칸은 `창 > … > 표 > Data Panel > Row 1 > "양품수 row0"` 경로에 있고, 칸의 이름은 `<열 이름> row<번호>`, 값(Value)은 화면 글자입니다. `rows`의 표는 `table` 노드 아래에 `Data Panel`(Pane) 하나, 그 아래 데이터 행마다 `Row <n>`(Custom, n은 1부터), 그 아래 칸을 둡니다. 칸은 이름이 `<열 머리글> row<k>`(k는 0부터)이고 Value 패턴의 값이 화면 글자인 `DataItem`이며, `ref` 칸처럼 Invoke로도 실제 클릭으로도 눌리고 같은 `cell_clicked`(또는 `cell_popup_opened`) 이벤트를 남깁니다. 회사 경로에 머리글이 없으므로 머리글 칸은 내지 않습니다. 공정별 생산집계처럼 위아래로 병합된 칸은 걸친 첫 행에만 값이 있고, 나머지 행에는 같은 열의 빈 값 칸이 있습니다. 칸은 **GridItem·TableItem 패턴을 내지 않습니다.** 회사 칸에 그 패턴이 있는지 모르므로 더 어려운 쪽을 흉내 낸 것이고, 그래서 CLI는 이 칸을 `cell`이 아니라 `row` 역할로 줄 수 있습니다(`crates/windows/src/tree/roles.rs`의 `data_item_role`). 패턴이 있는 칸은 지금의 `grid=ref`가 맡습니다. 상태 줄과 제목처럼 칸이 아닌 글자는 `text`처럼 나옵니다.
- **깊은 감싸개(`--nest <N>`):** 회사 표의 칸은 창부터 12번째 단계라 CLI 기본 깊이(10)에서 잘립니다. `--nest N`은 화면 패널을 패널 N겹(0~12)으로 감쌉니다. 바깥부터 세어 홀수 겹은 이름도 id도 없고, 짝수 겹은 `nest-<i>`라는 id만 있는 영역입니다(WinForms `Name`). `--expose grid=rows --nest 12`면 기본 깊이의 `snapshot`에서는 표 쪽 노드에 `children_count`만 남고 칸이 보이지 않으며, `--max-depth 30`이면 칸까지 읽힙니다.

메뉴 목록, 드롭다운 목록, 원형 메뉴는 본 창과 별개의 창이며 `list-windows`에 나옵니다. 창 제목은 내용을 드러내지 않습니다(`메뉴 목록`, `목록`, `메뉴`).

## 화면

"생산운영"을 누르면 목록 창에 여섯 화면이 나옵니다.

| 목록 항목 | 화면 id | 필터 | 저장 방법 |
| --- | --- | --- | --- |
| 전공정 생산현황 | `front-process` | Shop(3칸 모두 활성), 공정, 제품, 기간 | 엑셀 아이콘, 우클릭 원형 메뉴 |
| 후공정 생산현황 | `rear-process` | 없음(빈 화면) | |
| 공정별 불량현황 | `defect-by-process` | 제품, 기간 | 우클릭 원형 메뉴. 숫자 셀을 누르면 불량 상세 팝업 |
| 권취 Roller 점검일시 | `roller-check` | Shop(3번 칸만 활성), 설비, 기간 | 우클릭 원형 메뉴 |
| 설비 가동현황 | `equipment-status` | 없음(빈 화면) | |
| 공정별 생산집계 | `process-summary` | Shop(3칸 모두 활성), 기간 | 엑셀 아이콘, 우클릭 원형 메뉴. 숫자 셀을 누르면 생산 상세 팝업 |

- **Shop 칸:** 누르면 체크박스 목록이 뜹니다. "All"은 전부 체크, "Select"는 적용하고 닫기입니다. 목록 밖을 눌러도 체크한 대로 적용되며, 그 클릭은 아래 컨트롤에 전달되지 않습니다. Esc는 적용하지 않고 닫습니다. 3번 칸의 행은 `WA0 #울산 믹싱`, `WA1 #울산 극판`, `WA2 #울산 조립`, `WA3 #울산 화성`이고 처음에는 전부 체크되어 있습니다.
- **기간:** "전일", "전전일", "전주", "기간". "기간"을 고르면 날짜 칸 2개가 켜집니다. 날짜가 비었거나 형식이 틀리면 조회하지 않고 알림 창을 띄웁니다.
- **조회:** "조회(F5)" 버튼이나 F5 키. 로딩 팝업이 대기 시간 동안 뜹니다.
- **공정별 생산집계:** 헤더가 두 줄인 표입니다. 위 줄은 `SHOP`(두 줄 높이), `공정`(그룹공정·공정), `계획`(계획·실적·달성률), `생산`(작업수·양품수·양품율·수율(Loss제외)), `불량`(불량수·불량율(PPM)·LOSS수·LOSS율(PPM)·이탈수·이탈율(PPM))이고, 위 칸은 자기가 묶는 아래 칸들의 가로 범위를 덮습니다. `계획`은 위와 아래에 같은 이름으로 한 번씩 나옵니다. 행은 Shop마다 그룹공정이 `소계`이고 공정이 빈 행이 먼저 오고, 그 아래에 그룹공정(전공정 생산현황의 공정)마다 공정이 `소계`인 행이 옵니다. SHOP 이름은 그 Shop의 행들을 세로로 덮는 한 칸입니다. 값은 전공정 생산현황 행(제품 전체)의 합이라서, Shop 소계는 그 아래 행들의 합과 같습니다. 숫자 칸만 누를 수 있고, 누르면 그 행의 전공정 생산현황 행을 보여 주는 "생산 상세" 팝업이 뜹니다. 팝업은 우클릭 원형 메뉴로 저장합니다.
- **원형 메뉴:** 표를 우클릭하면 그 자리에 뜹니다. 1단계는 위부터 시계 방향으로 export, copy, print, close이고, export를 누르면 excel(오른쪽 위), html(아래), pdf(왼쪽 위)로 바뀝니다. excel만 저장 대화상자를 엽니다.

앱의 "오늘"은 2026-11-12로 고정입니다. "전일"은 언제 실행해도 2026-11-11이고, 데이터는 2026-11-01부터 2026-11-11까지 있습니다. 같은 필터면 항상 같은 행이 나옵니다. 값을 만드는 식은 `MesData.cs`에 있습니다.

## 이벤트 기록

`--log`를 주면 한 줄에 JSON 하나씩 덧붙입니다. 모든 줄에 `seq`(1부터), `time`, `event`가 있습니다. 앱의 어느 창에도 이 기록의 내용이나 경로는 나오지 않습니다. 테스트 하니스만 읽습니다.

| `event` | 그 밖의 필드 |
| --- | --- |
| `splash_shown` | `pid`, `splash_ms`. 팝업 창이 보였을 때(시작 옵션) |
| `splash_closed` | `pid`. 팝업 창이 닫혔을 때 |
| `launcher_skipped` | `pid`, `reason`(`main_running`). `--single-instance` 실행기가 메인 화면이 떠 있어 창 없이 끝났을 때 |
| `app_started` | `profile`, `expose`, `seed`, `combo_open` |
| `main_started` | `by`(`launcher`, `direct`), `pid`. 시작 옵션이 있을 때만. `launcher`는 실행기가 띄운 프로세스 |
| `menu_ready` | `delay_ms`. 시작 옵션이 있을 때만. 메인 화면이 보이고 메뉴가 채워졌을 때 |
| `notice_shown` | `index`(1부터), `count`. `--startup-notices`의 공지 창이 보였을 때 |
| `notice_closed` | `index`. 그 공지 창이 닫혔을 때 |
| `menu_opened` | `category` |
| `screen_opened` | `screen`, `title` |
| `filter_changed` | `screen`, `filter`, `value`, `closed_by`(`item_click`, `select_button`, `outside_click`, `deactivated`, `typed`) |
| `combo_body_clicked` | `screen`, `filter`. 기본(`--combo-open arrow`)에서 칸의 몸통을 눌렀을 때 |
| `combo_arrow_clicked` | `screen`, `filter`. 기본(`--combo-open arrow`)에서 화살표를 눌렀을 때. 이어서 목록이 열림 |
| `query_started` | `screen`, `filters`, `started_by`(`button`, `f5`), `delay_ms` |
| `query_finished` | `screen`, `rows` |
| `query_rejected` | `screen`, `reason`(`invalid_period`) |
| `cell_clicked` | `screen`, `row`(`[SHOP, 그룹공정, 공정]`, 빈 칸은 `""`), `column`(`[위 헤더, 아래 헤더]`). 공정별 생산집계의 숫자 칸을 눌렀을 때만 |
| `cell_popup_opened` | `screen`, `row`, `column`. 공정별 생산집계에서는 `row`가 비지 않은 행 이름을 ` · `로 이은 글(예: `극판 · 소계`), `column`이 아래 헤더 이름 |
| `radial_opened` | `stage`(1, 2), `source`(`panel`, `cell_popup`) |
| `radial_closed` | `stage`, `reason`(`choice`, `outside_click`, `escape`, `deactivated`) |
| `export_choice` | `route`(`icon`, `radial`), `choice`(`save`, `open`, `cancel`, `excel`, `pdf`, `html`, `copy`, `print`, `close`) |
| `export_saved` | `path`, `source`, `rows`, `screen`, `filters`(저장한 표를 조회했을 때의 필터. 조회 뒤에 바꾼 필터는 들어가지 않음) |
| `export_failed` | `path`, `reason` |
| `export_rejected` | `reason`(`no_data`) |

시작 옵션이 없으면 `splash_*`, `main_started`, `menu_ready`는 기록되지 않아 기록이 예전과 같습니다. `--launcher`로 띄운 메인 화면은 실행기가 쓴 줄 다음 번호부터 `seq`를 이어 씁니다(파일에 이미 있는 줄 수 다음).

`filter_changed`의 `filter`는 `shop-1`, `shop-2`, `shop-3`, `process`, `product`, `equipment`, `period`, `from`, `to`입니다. `combo_*_clicked`의 `filter`도 같은 이름을 씁니다(날짜 칸 제외). Shop 칸의 `value`는 코드 배열입니다. 값이 그대로면 기록하지 않습니다.

## 창 없는 내보내기

```powershell
.\build\VirtualMes.exe --export-headless front-process '{\"shop\":[\"WA2\"],\"period\":\"전주\"}' C:\temp\out.xlsx
```

화면 id는 `front-process`, `roller-check`, `defect-by-process`, `defect-detail`, `process-summary`입니다. 필터 키는 `shop`(코드 배열), `process`, `product`, `equipment`, `period`, `from`, `to`, `row`이고, 빠진 키는 기본값(전부, `전체`, `전일`)입니다. `defect-detail`은 `row`(`믹싱`, `극판`, `조립`, `화성`)가 필요합니다. `process-summary`는 `shop`과 기간만 쓰고, 첫 행에 아래 헤더 이름(첫 칸은 `SHOP`)을 적으며, 행마다 SHOP 이름을 반복해 적습니다. PowerShell 5.1에서는 JSON의 큰따옴표를 `\"`로 적습니다.

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
