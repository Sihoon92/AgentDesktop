# 가상 MES 앱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회사 MES의 화면과 조작 흐름을 재현하고, 컨트롤이 접근성 트리에 보이는 방식을 실행 옵션으로 바꿀 수 있는 WinForms 앱 `VirtualMes.exe`를 만든다.

**Architecture:** 화면의 모든 영역을 하나의 직접 그리는 컨트롤(`Surface`)로 그리고, 접근성 트리에 내보내는 모습만 노출 방식(`ref`/`text`/`pixel`)에 따라 바꾼다. 그래서 세 방식의 화면 모양은 완전히 같다. 화면 상태와 이벤트 기록은 UI와 분리된 `Session`이 맡고, 데이터·필터·엑셀 쓰기는 창 없이 도는 `--export-headless`로 시험한다.

**Tech Stack:** C# 5, .NET Framework 4.x WinForms, Windows에 들어 있는 `csc.exe`, `UIAutomationProvider.dll`(WM_GETOBJECT 제공자), `System.IO.Compression`, `System.Web.Extensions`(JSON). 테스트는 Python `pytest`와 저장소의 `agent-desktop.exe`.

**Spec:** [`../specs/2026-11-12-virtual-mes-app-design.md`](../specs/2026-11-12-virtual-mes-app-design.md). 배경은 [`../cua-background.md`](../cua-background.md).

## Global Constraints

- 컴파일러는 `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`를 절대 경로로 쓰고 `/langversion:5`로 고정한다. 문자열 보간, `nameof`, `?.`, 식 본문 멤버는 쓰지 않는다. NuGet 패키지와 SDK 설치는 없다.
- 소스 위치는 `examples/llm-agent/virtual-mes/`, 빌드 출력은 `examples/llm-agent/virtual-mes/build/VirtualMes.exe`, 창 제목은 `MES (가상)`, 네임스페이스는 `VirtualMesApp`이다.
- 앱의 "오늘"은 상수 `2026-11-12`다. 전일은 `2026-11-11`, 전전일은 `2026-11-10`, 전주는 `2026-11-02`~`2026-11-08`이다.
- 앱은 다른 프로그램을 실행하지 않는다.
- 에이전트 코드(`examples/llm-agent/agent/`, `run.py`, `view.py`, `viewer/`)와 `.github/workflows/ci.yml`은 고치지 않는다.
- 창을 띄우는 테스트는 `VIRTUAL_MES_LIVE=1`일 때만 돈다. 실행 전에 사용자에게 PC를 쓰지 않는지 확인받고, `AGENT_DESKTOP_HOME`을 임시 폴더로 지정한다.
- 커밋 메시지는 Conventional Commits이고 `Co-Authored-By` 같은 귀속 줄을 넣지 않는다(`AGENTS.md`). push는 하지 않는다.
- 코드가 설계 문서와 다르게 나오면 같은 커밋에서 설계 문서를 고친다.

## Review Focus

설계 문서가 말하지 않지만 쓰는 사람이 부딪힐 입력입니다. 각 줄의 테스트는 표시한 작업에 들어 있습니다.

1. **"기간"을 고르고 날짜 칸이 비었거나 형식이 틀리거나 시작일이 종료일보다 늦다.** 조회하지 않고 알림 창으로 이유를 알린다. 창 없는 실행은 종료 코드 2. (Task 2, Task 6)
2. **다중 선택에서 아무 행도 체크하지 않고 적용한다.** 칸에 "선택 없음"이 보이고 조회 결과는 0건이며, 0건도 열 이름만 있는 올바른 `.xlsx`로 저장된다. (Task 2, Task 5)
3. **저장 경로가 쓸 수 없는 곳이다(없는 폴더, 다른 프로그램이 연 파일).** 앱이 죽지 않고 알림 창으로 알리며 `export_saved`를 남기지 않는다. 창 없는 실행은 종료 코드 1. (Task 2, Task 7)
4. **로딩 팝업이 떠 있는 동안 F5를 다시 누르거나 창을 닫으려 한다.** 조회가 두 번 돌지 않고, 닫기는 로딩이 끝난 뒤에만 된다. (Task 6)
5. **목록이나 원형 메뉴가 열린 채 다른 앱으로 전환한다.** 열린 것이 닫히고(목록은 체크 상태 적용) 떠다니는 창이 남지 않는다. (Task 4)

---

## File Structure

```text
examples/llm-agent/virtual-mes/
  build.ps1            빌드. 기존 픽스처의 build.ps1과 같은 구조
  README.md            빌드, 옵션, 프로파일, 이벤트 기록 형식
  Program.cs           Main: DPI 선언, 옵션 해석, 창 없는 내보내기 분기, 폼 실행
  Options.cs           명령줄과 VirtualMes.args 해석, 프로파일 표, 검증
  Exposure.cs          Region, Exposure 열거형과 영역별 허용 방식
  FilterValues.cs      필터 값, 기간 해석, 필터 JSON 해석
  MesData.cs           고정 데이터와 화면별 조회
  XlsxWriter.cs        .xlsx 쓰기
  EventLog.cs          JSONL 이벤트 기록
  Session.cs           현재 화면·필터·조회 결과 상태, 이벤트 기록 호출
  Ui.cs                DPI 배율, 색, 글꼴 상수
  Surface.cs           항목을 직접 그리고 클릭을 판정하는 컨트롤
  SurfaceItem.cs       항목 하나(위치, 글자, 종류, 상태, 동작)
  SurfaceUia.cs        노출 방식별 UIA 제공자
  PopupHost.cs         활성화되지 않는 팝업 창과 바깥 클릭 처리
  MainForm.cs          틀: 메뉴바, 화면 탭, 본문, 상태 줄, 메뉴 목록
  ScreenSpec.cs        화면 6개의 정의(필터 구성, 아이콘 유무, 종류)
  ScreenPanel.cs       화면 하나: 필터 열, 조회 버튼, 데이터 패널
  FilterColumn.cs      필터 열 그리기, 드롭다운·다중 선택 목록, 날짜 칸
  GridSurface.cs       조회 결과를 Surface 항목으로 배치
  QueryRunner.cs       로딩 팝업과 대기 시간
  ExportFlow.cs        저장|열기|취소 팝업, 저장 대화상자, 파일 쓰기
  RadialMenu.cs        원형 메뉴 1·2단계
  CellPopup.cs         불량 상세 팝업
examples/llm-agent/tests/
  virtual_mes_support.py     exe 경로, 실행, .xlsx 읽기, 이벤트 기록 읽기
  test_virtual_mes.py        창 없는 테스트
  test_virtual_mes_live.py   창을 띄우는 테스트(VIRTUAL_MES_LIVE=1)
```

`.gitignore`에 `/examples/llm-agent/virtual-mes/build/`를 더한다. `VirtualMes.args`는 exe 옆(빌드 폴더)에 생기므로 함께 제외된다.

## 고정 데이터 (모든 작업이 따르는 값)

- **Shop 3번 칸 행:** `WA0 | #울산 믹싱`, `WA1 | #울산 극판`, `WA2 | #울산 조립`, `WA3 | #울산 화성`. 1번 칸은 `BA0 | 전지` 한 행, 2번 칸은 `UA0 | #울산` 한 행이며 데이터를 거르지 않는다. 모든 칸의 처음 상태는 전부 체크다.
- **칸에 보이는 글자:** 전부 체크면 `전체`, 하나면 그 이름, 둘 이상이면 `N개`, 없으면 `선택 없음`.
- **공정:** Shop별 2개. 믹싱은 `계량`, `혼합`. 극판은 `코팅`, `압연`. 조립은 `권취`, `용접`. 화성은 `충전`, `선별`. 필터 항목은 `전체`와 이 8개.
- **제품:** `전체`, `모델 A`, `모델 B`. **설비:** `전체`, `권취 1호`, `권취 2호`, `권취 3호`.
- **날짜 범위:** `2026-11-01`~`2026-11-11`.
- **화면 A 행(`front-process`):** 날짜 × Shop × 그 Shop의 공정 × 제품. 열은 `일자, Shop, 공정, 제품, 생산수, 불량수, 불량률`. `d`=일(1~11), `s`=Shop 번호(0~3), `p`=공정 번호(0~7), `m`=제품 번호(0~1)일 때 생산수 = `10000 + 137*d + 911*s + 53*p - 2040*m`, 불량수 = `20 + (7*d + 13*p + 5*m + 3*s) % 40`, 불량률 = 불량수 / 생산수 × 100. Shop 열에는 이름에서 `#울산 `을 뺀 글자(`조립`)가 나온다.
- **화면 B 행(`roller-check`):** 날짜 × Shop × 설비. 열은 `점검일시, Shop, 설비, Roller, 점검자, 결과`. `e`=설비 번호(0~2)일 때 점검일시 = `<날짜> 08:<10 + 5*e>`, Shop = 코드(`WA0`), Roller = `R-0<e+1>`, 점검자 = `점검자 <1 + (d + s) % 3>`, 결과 = `(d + s + e) % 7 == 0`이면 `교체` 아니면 `정상`.
- **화면 C 표(`defect-by-process`):** 행은 `믹싱, 극판, 조립, 화성`, 열은 `공정, 생산수, 불량수, 불량률`. 각 행은 화면 A 데이터를 그 Shop, 고른 제품, 고른 기간으로 합한 값이다.
- **불량 상세(`defect-detail`):** 열은 `불량 유형, 수량, 비율`, 행은 `유형 1`~`유형 4`. 그 행의 불량수 합 `T`를 43%, 30%, 17%, 10%로 내림 분배하고 남는 수는 `유형 1`에 더한다. 비율은 수량 / `T` × 100, 소수 1자리.
- **표기:** 정수는 천 단위 쉼표(`12,480`), 불량률은 소수 2자리와 `%`(`0.30%`), 문화권은 고정(Invariant). 정렬은 날짜, Shop, 공정(또는 설비), 제품 순.
- **화면 id와 이름:** `front-process` 전공정 생산현황, `roller-check` 권취 Roller 점검일시, `defect-by-process` 공정별 불량현황, `rear-process` 후공정 생산현황(빈 화면), `equipment-status` 설비 가동현황(빈 화면).

---

### Task 1: 빌드, 옵션, 종료 코드

**Files:**
- Create: `examples/llm-agent/virtual-mes/build.ps1`, `Program.cs`, `Options.cs`, `Exposure.cs`
- Create: `examples/llm-agent/tests/virtual_mes_support.py`, `examples/llm-agent/tests/test_virtual_mes.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces (C#):
  - `enum Region { Menu, Filters, Grid, Icon, Radial }`, `enum Exposure { Ref, Text, Pixel }`
  - `static bool Exposures.IsAllowed(Region region, Exposure exposure)`: `Icon`은 `Ref`와 `Pixel`만.
  - `sealed class Options { Dictionary<Region, Exposure> Expose; string Profile; int? DelayMs; int Seed; string LogPath; string[] Headless; bool ParseOnly; }`
  - `static Options Options.Parse(string[] args, string exeDirectory)`: 잘못된 입력에 `OptionsException(string message)`. `args`가 비었고 `exeDirectory`에 `VirtualMes.args`가 있으면 그 파일의 첫 줄을 큰따옴표를 존중해 나눠 인자로 쓴다.
  - 기본 시드는 `20261112`.
  - `--parse-only`: 옵션을 해석해 한 줄 JSON(`profile`, `expose`의 영역별 방식, `seed`, `delay_ms`, `log`)을 표준 출력에 쓰고 창 없이 종료 코드 0으로 끝난다. 테스트 전용이며 README의 옵션 표에 "테스트용"으로 적는다.
- Produces (Python, `virtual_mes_support.py`):
  - `EXE: Path`(빌드 출력 경로), `requires_exe`(없으면 건너뛰는 pytest 표시)
  - `run_exe(*args, exe=EXE) -> subprocess.CompletedProcess`(`timeout=60`, `encoding="utf-8"`)

- [ ] **Step 1: 실패하는 테스트 작성** (`test_virtual_mes.py`)

```python
import json
import pytest
from virtual_mes_support import EXE, requires_exe, run_exe

pytestmark = requires_exe

def parsed(*args):
    done = run_exe(*args, "--parse-only")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)

def test_default_profile_is_mes_like():
    assert parsed()["expose"] == {
        "menu": "ref", "filters": "text", "grid": "text", "icon": "pixel", "radial": "pixel"}

def test_easy_and_hard_profiles():
    assert set(parsed("--profile", "easy")["expose"].values()) == {"ref"}
    assert parsed("--profile", "hard")["expose"] == {
        "menu": "text", "filters": "pixel", "grid": "pixel", "icon": "pixel", "radial": "pixel"}

def test_expose_overrides_profile():
    out = parsed("--profile", "easy", "--expose", "grid=pixel,radial=text")
    assert out["expose"]["grid"] == "pixel" and out["expose"]["radial"] == "text"
    assert out["expose"]["menu"] == "ref"

@pytest.mark.parametrize("args", [
    ["--expose", "icon=text"], ["--expose", "grid=shiny"], ["--expose", "nowhere=ref"],
    ["--profile", "medium"], ["--delay-ms", "soon"], ["--delay-ms", "-1"], ["--unknown"],
    ["--delay-ms"]])
def test_bad_options_exit_2_with_a_reason(args):
    done = run_exe(*args, "--parse-only")
    assert done.returncode == 2
    assert done.stderr.strip()

def test_args_file_is_used_only_without_arguments(tmp_path):
    copy = tmp_path / EXE.name
    copy.write_bytes(EXE.read_bytes())
    (tmp_path / "VirtualMes.args").write_text('--profile hard --parse-only --log "C:\\a b\\x.jsonl"', encoding="utf-8")
    from_file = json.loads(run_exe(exe=copy).stdout)
    assert from_file["profile"] == "hard" and from_file["log"] == "C:\\a b\\x.jsonl"
    assert json.loads(run_exe("--parse-only", exe=copy).stdout)["profile"] == "mes-like"
```

`run_exe`는 `exe=` 키워드로 다른 실행 파일을 받는다(기본값 `EXE`).

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_virtual_mes.py -q` (`examples/llm-agent`에서)
Expected: exe가 없어 전부 skip. `build.ps1`이 생기기 전이므로 정상이다.

- [ ] **Step 3: `build.ps1` 작성**

`tests/fixture-app-windows/build.ps1`의 구조(절대 경로 컴파일러, GAC 조회 함수, 임시 파일에 컴파일한 뒤 이동)를 그대로 따른다. 출력은 `build\VirtualMes.exe`, `/target:winexe`, 참조는 `System.dll`, `System.Core.dll`, `System.Drawing.dll`, `System.Windows.Forms.dll`, `System.IO.Compression.dll`, `System.Web.Extensions.dll`과 GAC의 `UIAutomationProvider`, `UIAutomationTypes`, `WindowsBase`.

- [ ] **Step 4: `Exposure.cs`, `Options.cs`, `Program.cs` 구현**

`Program.Main(string[] args)`는 `int`를 돌려준다. `OptionsException`이면 메시지를 표준 오류에 쓰고 2를 돌려준다. 이 작업에서는 `--parse-only`가 아니면 아무 창도 띄우지 않고 0을 돌려준다.

- [ ] **Step 5: 빌드와 통과 확인**

Run: `powershell -NoProfile -File examples\llm-agent\virtual-mes\build.ps1` 다음 `python -m pytest tests/test_virtual_mes.py -q`
Expected: `Built: ...VirtualMes.exe`, 테스트 전부 PASS.

- [ ] **Step 6: 커밋**

```bash
git add .gitignore examples/llm-agent/virtual-mes examples/llm-agent/tests/virtual_mes_support.py examples/llm-agent/tests/test_virtual_mes.py
git commit -m "feat: add the virtual mes build and option parsing"
```

### Task 2: 데이터, 필터, 엑셀 쓰기, 창 없는 내보내기

**Files:**
- Create: `virtual-mes/FilterValues.cs`, `MesData.cs`, `XlsxWriter.cs`
- Modify: `virtual-mes/Program.cs`, `tests/virtual_mes_support.py`, `tests/test_virtual_mes.py`

**Interfaces:**
- Consumes: `Options.Headless`(`[화면 id, 필터 JSON, 출력 경로]`)
- Produces (C#):
  - `sealed class FilterValues { List<string> Shop; string Process; string Product; string Equipment; string Period; string From; string To; string Row; }`, 기본값은 Shop 4개 전부, 나머지 `전체`, `Period`는 `전일`.
  - `static FilterValues FilterValues.FromJson(string json)`: 모르는 키, 모르는 값, 틀린 날짜에 `OptionsException`.
  - `bool FilterValues.TryResolvePeriod(out DateTime from, out DateTime to, out string error)`
  - `sealed class QueryResult { string[] Header; List<string[]> Rows; }`
  - `static QueryResult MesData.Query(string screenId, FilterValues filters)`: `front-process`, `roller-check`, `defect-by-process`, `defect-detail`(`Row`가 공정 이름이며 필수). 모르는 화면 id와 `Row` 누락에 `OptionsException`.
  - `static void XlsxWriter.Write(string path, QueryResult result)`: 모든 셀을 inline string으로 쓴다. 쓰기 실패는 `IOException` 또는 `UnauthorizedAccessException` 그대로.
  - `--export-headless`의 종료 코드: 성공 0, 입력 오류 2, 파일 쓰기 실패 1.
- Produces (Python): `read_xlsx(path) -> list[list[str]]`(표준 라이브러리 `zipfile`과 `xml.etree`로 첫 시트를 읽음), `export(screen, filters: dict, out: Path) -> subprocess.CompletedProcess`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from virtual_mes_support import export, read_xlsx

def rows(tmp_path, screen, filters):
    out = tmp_path / "out.xlsx"
    done = export(screen, filters, out)
    assert done.returncode == 0, done.stderr
    return read_xlsx(out)

def test_front_process_default_is_yesterday_all_shops(tmp_path):
    table = rows(tmp_path, "front-process", {})
    assert table[0] == ["일자", "Shop", "공정", "제품", "생산수", "불량수", "불량률"]
    assert len(table) == 1 + 16
    assert {row[0] for row in table[1:]} == {"2026-11-11"}

def test_shop_filter_keeps_only_that_shop(tmp_path):
    table = rows(tmp_path, "front-process", {"shop": ["WA2"]})
    assert [row[1:4] for row in table[1:]] == [
        ["조립", "권취", "모델 A"], ["조립", "권취", "모델 B"],
        ["조립", "용접", "모델 A"], ["조립", "용접", "모델 B"]]
    assert table[1][4:] == ["13,541", "35", "0.26%"]

def test_same_request_gives_same_cells(tmp_path):
    first = rows(tmp_path, "roller-check", {"shop": ["WA0", "WA2"], "period": "전주"})
    assert first == rows(tmp_path, "roller-check", {"shop": ["WA0", "WA2"], "period": "전주"})
    assert len(first) == 1 + 7 * 2 * 3

def test_custom_period_is_inclusive(tmp_path):
    table = rows(tmp_path, "front-process", {"shop": ["WA0"], "process": "계량", "product": "모델 A",
                                             "period": "기간", "from": "2026-11-03", "to": "2026-11-05"})
    assert [row[0] for row in table[1:]] == ["2026-11-03", "2026-11-04", "2026-11-05"]

def test_defect_table_sums_front_process(tmp_path):
    summary = rows(tmp_path, "defect-by-process", {})
    assert [row[0] for row in summary[1:]] == ["믹싱", "극판", "조립", "화성"]
    detail = rows(tmp_path, "defect-detail", {"row": "조립"})
    assert detail[0] == ["불량 유형", "수량", "비율"]
    total = sum(int(row[1].replace(",", "")) for row in detail[1:])
    assert f"{total:,}" == summary[3][2]

def test_no_shop_selected_saves_header_only(tmp_path):
    assert rows(tmp_path, "front-process", {"shop": []}) == [
        ["일자", "Shop", "공정", "제품", "생산수", "불량수", "불량률"]]

@pytest.mark.parametrize("screen,filters", [
    ("front-process", {"period": "기간"}),
    ("front-process", {"period": "기간", "from": "2026-11-05", "to": "2026-11-03"}),
    ("front-process", {"period": "기간", "from": "11/05/2026", "to": "2026-11-06"}),
    ("front-process", {"shop": ["ZZ9"]}), ("front-process", {"colour": "red"}),
    ("no-such-screen", {}), ("defect-detail", {})])
def test_bad_requests_exit_2(tmp_path, screen, filters):
    done = export(screen, filters, tmp_path / "out.xlsx")
    assert done.returncode == 2 and done.stderr.strip()
    assert not (tmp_path / "out.xlsx").exists()

def test_unwritable_path_exits_1(tmp_path):
    done = export("front-process", {}, tmp_path / "missing-folder" / "out.xlsx")
    assert done.returncode == 1 and done.stderr.strip()
```

`13,541`, `35`, `0.26%`는 `d=11, s=2, p=4, m=0`을 식에 넣은 값이다(`10000 + 1507 + 1822 + 212 = 13541`, `20 + (77 + 52 + 0 + 6) % 40 = 35`, `35 / 13541 = 0.258%`).

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_virtual_mes.py -q`
Expected: 새 테스트가 종료 코드 불일치로 FAIL.

- [ ] **Step 3: `FilterValues.cs`, `MesData.cs`, `XlsxWriter.cs` 구현, `Program.cs`에 창 없는 분기 추가**

JSON은 `System.Web.Script.Serialization.JavaScriptSerializer`로 읽는다. `.xlsx`는 `ZipArchive`에 `[Content_Types].xml`, `_rels/.rels`, `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`, `xl/worksheets/sheet1.xml` 다섯 항목을 쓴다. 출력은 임시 파일에 쓴 뒤 옮겨, 실패하면 대상 경로에 아무것도 남지 않게 한다.

- [ ] **Step 4: 빌드와 통과 확인**

Run: `build.ps1` 다음 `python -m pytest tests/test_virtual_mes.py -q`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
git add examples/llm-agent/virtual-mes examples/llm-agent/tests
git commit -m "feat: add virtual mes data, filters and headless excel export"
```

### Task 3: Surface와 노출 방식, 틀 창

이 작업이 계획에서 가장 불확실합니다. `agent-desktop`이 세 방식을 의도대로 읽는지를 여기서 확인하고, 다르게 읽히면 이 작업 안에서 제공자를 고칩니다.

**Files:**
- Create: `virtual-mes/Ui.cs`, `SurfaceItem.cs`, `Surface.cs`, `SurfaceUia.cs`, `MainForm.cs`
- Create: `tests/test_virtual_mes_live.py`
- Modify: `virtual-mes/Program.cs`, `tests/virtual_mes_support.py`

**Interfaces:**
- Produces (C#):
  - `static class Ui { static float Scale; static int Px(int logical); static Font Font; }`. `Program.Main`이 창을 만들기 전에 `SetProcessDPIAware()`를 호출한다.
  - `enum ItemKind { Label, Button, Check, Cell, Graphic }`
  - `sealed class SurfaceItem { string Id; Rectangle Bounds; string Text; ItemKind Kind; bool Enabled; bool Checked; Action Click; Action<Point> RightClick; Action<Graphics, Rectangle> Paint; }`. `Bounds`는 Surface 안의 픽셀 좌표, `Paint`가 없으면 종류별 기본 그림.
  - `sealed class Surface : Control { Surface(Region region, Exposure exposure); void SetItems(IList<SurfaceItem> items); Action<Point> BackgroundRightClick; }`. 클릭 판정은 Surface가 하고, `Enabled`가 거짓인 항목은 반응하지 않는다.
  - 노출 규칙(`SurfaceUia.cs`, `UiaProviderHost` 방식의 WM_GETOBJECT 조각 제공자):

    | 방식 | Surface | 항목 |
    | --- | --- | --- |
    | `Pixel` | 제공자를 달지 않음. 이름 없는 pane 하나 | 없음 |
    | `Text` | 이름 없는 pane | `Graphic`을 뺀 항목마다 `ControlType.Text`, 이름 = `Text`, 화면 좌표 영역. 패턴 없음. `Check`의 체크 상태는 내보내지 않음 |
    | `Ref` | 이름 없는 pane | `Label` → Text. `Button` → Button + Invoke. `Check` → CheckBox + Toggle. `Cell` → DataItem + GridItem + Invoke. `Graphic` → `Text`가 있으면 Button + Invoke. 모두 `AutomationId` = `Id` |

  - Invoke와 Toggle은 UI 스레드로 `BeginInvoke`해서 `Click`을 부르고 바로 돌아온다. 항목 목록은 바뀔 때마다 통째로 교체하는 불변 배열로 읽는다.
  - `sealed class MainForm : Form { MainForm(Options options); }`. 이 작업에서는 제목 줄, 메뉴바(`Region.Menu`의 Surface, 카테고리 5개 `기준정보, 생산운영, 품질관리, 설비관리, 자재관리`, id는 `menu-0`~`menu-4`), 빈 본문, 상태 줄만 있다. 크기는 `Ui.Px(1100)` × `Ui.Px(680)`.
- Produces (Python):
  - `live`(환경 변수 `VIRTUAL_MES_LIVE=1`이 아니면 건너뛰는 표시)
  - `class LiveApp`: `with LiveApp(tmp_path, "--profile", "easy") as app:`로 쓴다. exe를 `tmp_path`에 복사하고 `VirtualMes.args`에 인자와 `--log <tmp>/events.jsonl`을 쓴 뒤 `agent-desktop launch`로 띄우고, 나갈 때 `close-app`으로 닫는다. `AGENT_DESKTOP_HOME`은 `tmp_path / "state"`. `agent-desktop`은 `AGENT_DESKTOP_BIN`, `target/release`, `target/debug` 순으로 찾는다(`run.py`의 `find_binary`와 같은 순서).
  - `app.windows() -> list[dict]`(키 `id`, `title`), `app.snapshot(window_id) -> dict`(`--compact --include-bounds`), `app.nodes(window_id) -> list[dict]`(트리를 펼친 목록. CLI의 실제 키 이름이 무엇이든 `role`, `name`, `ref`, `bounds`, `states`, `id`로 맞춰 돌려주고 값이 없는 키는 넣지 않는다. `id`는 `AutomationId`다), `app.find(name, role=None) -> dict`(모든 창에서 이름으로 찾음, 없으면 `AssertionError`), `app.click(ref)`, `app.events() -> list[dict]`.

- [ ] **Step 1: 실패하는 테스트 작성** (`test_virtual_mes_live.py`)

```python
from virtual_mes_support import LiveApp, live, requires_exe

pytestmark = [requires_exe, live]

def names(app, window, role=None):
    return [n.get("name") for n in app.nodes(window) if n.get("name") and (role is None or n.get("role") == role)]

def test_menu_as_ref_has_named_buttons_with_refs(tmp_path):
    with LiveApp(tmp_path, "--expose", "menu=ref") as app:
        main = app.windows()[0]["id"]
        buttons = [n for n in app.nodes(main) if n.get("role") == "button" and n.get("ref")]
        assert [n["name"] for n in buttons][:5] == ["기준정보", "생산운영", "품질관리", "설비관리", "자재관리"]

def test_menu_as_text_has_text_without_refs(tmp_path):
    with LiveApp(tmp_path, "--expose", "menu=text") as app:
        main = app.windows()[0]["id"]
        node = next(n for n in app.nodes(main) if n.get("name") == "생산운영")
        assert node["role"] == "statictext" and "ref" not in node and node.get("bounds")

def test_menu_as_pixel_shows_no_menu_text(tmp_path):
    with LiveApp(tmp_path, "--expose", "menu=pixel") as app:
        main = app.windows()[0]["id"]
        assert "생산운영" not in names(app, main)
```

- [ ] **Step 2: 구현** (`Ui.cs`, `SurfaceItem.cs`, `Surface.cs`, `SurfaceUia.cs`, `MainForm.cs`, `Program.cs`가 폼을 실행)

조각 제공자는 `IRawElementProviderFragmentRoot`(Surface)와 `IRawElementProviderFragment`(항목)다. Surface 쪽은 `HostRawElementProvider`로 창 제공자에 붙고, 항목은 `BoundingRectangle`을 화면 좌표로 돌려주며 런타임 id는 `{ AutomationInteropProvider.AppendRuntimeId, 항목 순번 }`이다.

- [ ] **Step 3: 빌드하고, 사용자 확인 뒤 창을 띄우는 테스트 실행**

Run: `build.ps1` 다음 `$env:VIRTUAL_MES_LIVE='1'; python -m pytest tests/test_virtual_mes_live.py -q`
Expected: 3개 PASS. 역할 이름이 기대와 다르면(예: `statictext`가 아닌 다른 토큰) 스냅샷 JSON을 보고 제공자의 `ControlType`과 패턴을 고친다. 테스트의 기대 역할을 바꾸는 것은, 그 역할이 설계 문서의 뜻(ref가 붙는다 / 글자와 위치만 있다 / 안 보인다)을 그대로 지킬 때만 한다.

- [ ] **Step 4: 창 없는 테스트가 그대로 통과하는지 확인**

Run: `python -m pytest -q`
Expected: 기존 테스트 포함 전부 PASS, 창을 띄우는 테스트는 skip.

- [ ] **Step 5: 커밋**

```bash
git add examples/llm-agent/virtual-mes examples/llm-agent/tests
git commit -m "feat: draw virtual mes surfaces with switchable accessibility exposure"
```

### Task 4: 이벤트 기록, 팝업 창, 메뉴 목록, 화면 전환

**Files:**
- Create: `virtual-mes/EventLog.cs`, `Session.cs`, `PopupHost.cs`, `ScreenSpec.cs`, `ScreenPanel.cs`
- Modify: `virtual-mes/MainForm.cs`, `tests/test_virtual_mes_live.py`

**Interfaces:**
- Produces (C#):
  - `sealed class EventLog { EventLog(string pathOrNull); void Write(string eventName, IDictionary<string, object> fields); }`. 한 줄에 JSON 하나, 모든 줄에 `seq`(1부터), `time`(ISO 8601), `event`. UTF-8(BOM 없음)로 덧붙이고 줄마다 디스크로 내보내며, 다른 프로세스가 읽을 수 있게 연다. 경로가 없으면 아무것도 하지 않는다.
  - `sealed class ScreenSpec { string Id; string Title; string Kind; bool HasIcon; string[] Filters; bool[] ShopBoxEnabled; }`, `static ScreenSpec[] ScreenSpecs.All`(5개), `Kind`는 `list`, `matrix`, `empty`.
  - `sealed class Session { Session(Options options, EventLog log); ScreenSpec Screen; FilterValues Filters; QueryResult Result; }`. 상태를 바꾸는 메서드가 이벤트 기록을 쓰고, UI는 `Session`을 통해서만 상태를 바꾼다. 메서드는 처음 쓰는 작업에서 더한다: `OpenMenu(string category)`, `OpenScreen(string screenId)`(Task 4), `ChangeFilter(string filter, object value, string closedBy)`(Task 5), `StartQuery(string startedBy, int delayMs)`, `FinishQuery(QueryResult result)`, `RejectQuery(string reason)`(Task 6), `ExportChoice(string route, string choice)`, `ExportSaved(string path, string source, int rows)`, `ExportFailed(string path, string reason)`, `ExportRejected(string reason)`(Task 7), `RadialOpened(int stage, string source)`, `RadialClosed(int stage, string reason)`(Task 8), `CellPopupOpened(string row, string column)`(Task 9).
  - `sealed class PopupHost { static PopupHost Show(Form owner, Point screenLocation, Size size, Region region, Exposure exposure, string title, Action<string> onClosed); Surface Surface; void Close(string reason); static void CloseAll(string reason); }`. 팝업은 소유자가 있는 테두리 없는 최상위 창이고 활성화를 가져가지 않는다(`ShowWithoutActivation`, `WS_EX_NOACTIVATE`). 열려 있는 동안 `IMessageFilter`가 팝업 밖의 마우스 버튼 누름을 가로채 팝업을 `outside_click`으로 닫고 그 누름과 짝이 되는 뗌을 삼킨다. Esc는 `escape`로 닫는다. 본 창이 비활성화되면 `deactivated`로 닫는다.
  - 메뉴 목록 창: 클릭한 카테고리 바로 아래. 맨 위에 카테고리 이름, 그 아래 항목. "생산운영"은 `전공정 생산현황, 후공정 생산현황, 공정별 불량현황, 권취 Roller 점검일시, 설비 가동현황` 순서. 다른 카테고리는 항목이 없다. 항목 id는 `screen-<화면 id>`.
  - `sealed class ScreenPanel : Panel { ScreenPanel(Session session, ScreenSpec spec); }`. 이 작업에서는 화면 이름 탭, 빈 필터 열, 빈 데이터 패널(왼쪽 위에 화면 이름), 상태 줄 글자 `준비`만 있다. 화면 이름과 상태 줄은 `Region.Grid`의 Surface다.
- 이벤트: `app_started {profile, expose, seed}`, `menu_opened {category}`, `screen_opened {screen, title}`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_menu_opens_a_list_window_and_picks_a_screen(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        before = len(app.windows())
        app.click(app.find("생산운영", role="button")["ref"])
        assert len(app.windows()) == before + 1
        app.click(app.find("전공정 생산현황")["ref"])
        assert len(app.windows()) == before
        events = app.events()
        assert events[0]["event"] == "app_started" and events[0]["profile"] == "easy"
        assert [e["event"] for e in events[1:]] == ["menu_opened", "screen_opened"]
        assert events[2]["screen"] == "front-process"
        assert [e["seq"] for e in events] == [1, 2, 3]

def test_similar_screen_name_opens_the_empty_screen(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("후공정 생산현황")["ref"])
        assert app.events()[-1]["screen"] == "rear-process"

def test_list_window_closes_when_the_app_loses_focus(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        before = len(app.windows())
        app.click(app.find("생산운영", role="button")["ref"])
        app.focus_elsewhere()
        assert len(app.windows()) == before
```

`app.focus_elsewhere()`를 `LiveApp`에 더한다. 테스트가 만든 작은 Tk 창을 잠깐 띄워 전경으로 올렸다가 닫는다.

- [ ] **Step 2: 구현**
- [ ] **Step 3: 빌드하고, 사용자 확인 뒤 실행**

Run: `$env:VIRTUAL_MES_LIVE='1'; python -m pytest tests/test_virtual_mes_live.py -q`
Expected: 전부 PASS. 목록 창이 `list-windows`에 나오지 않아 첫 테스트가 실패하면, 팝업을 활성화되는 소유 창으로 바꿔 다시 확인한다. 어느 쪽으로도 창 목록에 나오지 않으면 설계 문서의 "뜨는 것들의 창 구조"를 사실대로 고치고 테스트를 그 사실에 맞춘다.

- [ ] **Step 4: 커밋**

```bash
git add examples/llm-agent/virtual-mes examples/llm-agent/tests examples/llm-agent/docs
git commit -m "feat: add the virtual mes menu, screens and event log"
```

### Task 5: 필터 열

**Files:**
- Create: `virtual-mes/FilterColumn.cs`
- Modify: `virtual-mes/ScreenPanel.cs`, `Session.cs`, `tests/test_virtual_mes_live.py`

**Interfaces:**
- Consumes: `Session.ChangeFilter`, `PopupHost`, `ScreenSpec.Filters`, `ScreenSpec.ShopBoxEnabled`
- Produces:
  - 필터 열은 `Region.Filters`의 Surface 하나와 표준 컨트롤 3개(날짜 `TextBox` 2개, `조회(F5)` `Button`)다. 필터 이름은 `Label`, 박스는 `Button`, 박스 id는 `filter-<이름>`(`filter-shop-1`~`filter-shop-3`, `filter-process`, `filter-product`, `filter-equipment`, `filter-period`).
  - 날짜 칸의 `AccessibleName`은 `시작일`, `종료일`, 처음 값은 `0000-00-00`, "기간"이 `기간`일 때만 활성. 조회 버튼의 `Name`은 `query-button`.
  - 단일 선택 목록: 박스 아래에 박스와 같은 폭으로 뜬다. 항목 id는 `option-<순번>`. 항목 클릭은 `item_click`으로 닫는다. 바깥 클릭은 값을 바꾸지 않고 닫는다.
  - 다중 선택 목록: 맨 위 `All`(id `multi-all`), `Select`(id `multi-select`), 그 아래 행마다 체크(`ItemKind.Check`, id `row-<코드>`, `Text`는 코드), 코드와 이름 `Label`. `All`은 전부 체크. `Select`는 `select_button`으로, 바깥 클릭은 `outside_click`으로 적용하며 닫는다. Esc는 적용하지 않고 닫는다.
  - 이벤트: `filter_changed {screen, filter, value, closed_by}`. `filter`는 `shop-1`, `shop-2`, `shop-3`, `process`, `product`, `equipment`, `period`, `from`, `to`. 값이 그대로면 쓰지 않는다. 다중 선택의 `value`는 코드 배열. 날짜 칸은 포커스를 잃을 때 `closed_by: "typed"`로 쓴다.
  - 본 창의 최소 크기를 정해, 창을 줄여도 필터 열과 `조회(F5)` 버튼이 가려지지 않게 한다. 배치는 설계 문서의 "배치" 절을 따른다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def open_front(app):
    app.click(app.find("생산운영", role="button")["ref"])
    app.click(app.find("전공정 생산현황")["ref"])

def last(app, event):
    return [e for e in app.events() if e["event"] == event][-1]

def test_single_select_changes_on_item_click(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.click(app.by_id("filter-process")["ref"])
        app.click(app.find("권취")["ref"])
        changed = last(app, "filter_changed")
        assert (changed["filter"], changed["value"], changed["closed_by"]) == ("process", "권취", "item_click")

def test_period_enables_the_date_inputs(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        assert "disabled" in app.find("시작일").get("states", [])
        app.click(app.by_id("filter-period")["ref"])
        app.click(app.by_id("option-3")["ref"])
        assert "disabled" not in app.find("시작일").get("states", [])
        assert last(app, "filter_changed")["value"] == "기간"

def test_multi_select_applies_with_select_and_with_an_outside_click(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.click(app.by_id("filter-shop-3")["ref"])
        for code in ("WA0", "WA1", "WA3"):
            app.click(app.by_id(f"row-{code}")["ref"])
        app.click(app.by_id("multi-select")["ref"])
        assert last(app, "filter_changed")["value"] == ["WA2"]
        assert last(app, "filter_changed")["closed_by"] == "select_button"
        assert app.by_id("filter-shop-3")["name"] == "#울산 조립"
        app.click(app.by_id("filter-shop-3")["ref"])
        app.click(app.by_id("multi-all")["ref"])
        app.click_outside()
        assert last(app, "filter_changed")["value"] == ["WA0", "WA1", "WA2", "WA3"]
        assert last(app, "filter_changed")["closed_by"] == "outside_click"
        app.click(app.by_id("filter-shop-3")["ref"])
        for code in ("WA0", "WA1", "WA2", "WA3"):
            app.click(app.by_id(f"row-{code}")["ref"])
        app.click(app.by_id("multi-select")["ref"])
        assert app.by_id("filter-shop-3")["name"] == "선택 없음"

def test_disabled_shop_boxes_do_not_open(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("권취 Roller 점검일시")["ref"])
        assert "disabled" in app.by_id("filter-shop-1").get("states", [])
        assert "disabled" not in app.by_id("filter-shop-3").get("states", [])
```

`LiveApp`에 `by_id(automation_id)`(모든 창에서 `id`로 찾음)와 `click_outside()`(본 창 데이터 패널의 빈 곳을 `agent-desktop mouse-click`으로 한 번 누름. 실제 커서를 쓰는 유일한 헬퍼다)를 더한다.

- [ ] **Step 2: 구현**
- [ ] **Step 3: 빌드하고, 사용자 확인 뒤 실행** — Expected: 전부 PASS.
- [ ] **Step 4: 커밋** — `git commit -m "feat: add virtual mes filters with single and multi select lists"`

### Task 6: 조회, 로딩 팝업, 표

**Files:**
- Create: `virtual-mes/GridSurface.cs`, `QueryRunner.cs`
- Modify: `virtual-mes/ScreenPanel.cs`, `Session.cs`, `MainForm.cs`, `tests/test_virtual_mes_live.py`

**Interfaces:**
- Produces:
  - `static void GridSurface.Fill(Surface surface, QueryResult result, Action<int, int> cellClick)`: 첫 행(열 이름)은 `Label`, 나머지는 `Cell`(id `cell-<행>-<열>`). 표가 패널보다 길면 Surface를 담은 패널이 세로로 스크롤한다.
  - `sealed class QueryRunner { QueryRunner(Options options); int NextDelayMs(); }`: `--delay-ms`가 있으면 그 값, 없으면 `new Random(Seed)`에서 1000~4000.
  - 로딩 팝업: 제목 `조회 중`의 모달 창, 회전하는 그림(이름 없는 `PictureBox`류)과 `Label` `데이터를 조회하고 있습니다`. 사용자가 닫을 수 없다.
  - 조회 시작은 버튼 클릭과 본 창의 F5. 로딩 중의 F5와 본 창 닫기는 무시한다. "기간"의 날짜가 틀리면 조회하지 않고 `MessageBox`로 `TryResolvePeriod`의 오류를 보여 준다. 빈 화면(`Kind == "empty"`)에는 조회 버튼이 없다.
  - 조회가 끝나면 상태 줄이 `조회 완료 · N건`.
  - 이벤트: `query_started {screen, filters, started_by: "button"|"f5", delay_ms}`, `query_finished {screen, rows}`, `query_rejected {screen, reason}`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_query_fills_the_grid_and_logs_the_filters(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "300") as app:
        open_front(app)
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        started, finished = last(app, "query_started"), last(app, "query_finished")
        assert started["started_by"] == "button" and started["delay_ms"] == 300
        assert started["filters"]["period"] == "전일" and finished["rows"] == 16
        assert app.find("조회 완료 · 16건")
        assert app.by_id("cell-1-0")["name"] == "2026-11-11"

def test_loading_window_is_visible_while_waiting(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "4000") as app:
        open_front(app)
        before = len(app.windows())
        app.click(app.by_id("query-button")["ref"])
        assert len(app.windows()) == before + 1
        assert app.find("데이터를 조회하고 있습니다")
        app.press("f5")
        app.wait_for_event("query_finished")
        assert len([e for e in app.events() if e["event"] == "query_started"]) == 1

def test_default_delays_repeat_with_the_same_seed(tmp_path):
    delays = []
    for run in ("a", "b"):
        with LiveApp(tmp_path / run, "--profile", "easy") as app:
            open_front(app)
            for nth in (1, 2):
                app.click(app.by_id("query-button")["ref"])
                app.wait_for_event("query_finished", count=nth)
            delays.append([e["delay_ms"] for e in app.events() if e["event"] == "query_started"])
    assert delays[0] == delays[1] and all(1000 <= d <= 4000 for d in delays[0])

def test_custom_period_without_dates_is_rejected(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("filter-period")["ref"])
        app.click(app.by_id("option-3")["ref"])
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_rejected")
        assert not [e for e in app.events() if e["event"] == "query_started"]
```

`LiveApp`에 `wait_for_event(name, count=1, timeout_s=15)`(이벤트 기록 파일을 0.1초 간격으로 다시 읽음)와 `press(key)`를 더한다.

- [ ] **Step 2: 구현**
- [ ] **Step 3: 빌드하고, 사용자 확인 뒤 실행** — Expected: 전부 PASS.
- [ ] **Step 4: 커밋** — `git commit -m "feat: add virtual mes query with a loading window and result grid"`

### Task 7: 아이콘 저장

**Files:**
- Create: `virtual-mes/ExportFlow.cs`
- Modify: `virtual-mes/ScreenPanel.cs`, `Session.cs`, `tests/test_virtual_mes_live.py`

**Interfaces:**
- Produces:
  - 엑셀 아이콘: 화면 A의 데이터 패널 오른쪽 위, `Region.Icon`의 Surface, 항목 하나(`ItemKind.Graphic`, id `excel-icon`, `Text`는 `Ref`일 때만 `엑셀 저장`).
  - `static void ExportFlow.FromIcon(Form owner, Session session)`: 결과가 없으면 `MessageBox` `조회된 데이터가 없습니다`. 있으면 제목 `파일 다운로드`의 모달 창에 표준 `Button` `저장`, `열기`, `취소`.
  - `static void ExportFlow.SaveAs(Form owner, Session session, QueryResult result, string source, string route)`: `SaveFileDialog`(필터 `Excel (*.xlsx)|*.xlsx`, 기본 파일 이름 `<화면 이름>.xlsx`, `OverwritePrompt = true`). 쓰기에 실패하면 `MessageBox`로 알리고 `export_failed`를 남긴다.
  - "열기": `%TEMP%\VirtualMes\<화면 id>-<seq>.xlsx`에 쓴다.
  - 이벤트: `export_choice {route: "icon", choice: "save"|"open"|"cancel"}`, `export_saved {path, source: "panel"|"cell_popup", rows, filters}`, `export_failed {path, reason}`, `export_rejected {reason: "no_data"}`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_icon_save_writes_the_same_cells_as_the_headless_export(tmp_path):
    target = tmp_path / "saved.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        app.click(app.by_id("excel-icon")["ref"])
        app.click(app.find("저장", role="button")["ref"])
        app.save_dialog(target)
        app.wait_for_event("export_saved")
        saved = last(app, "export_saved")
        assert saved["path"] == str(target) and saved["source"] == "panel" and saved["rows"] == 16
    expected = tmp_path / "expected.xlsx"
    assert export("front-process", {}, expected).returncode == 0
    assert read_xlsx(target) == read_xlsx(expected)

def test_icon_before_a_query_reports_no_data(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy") as app:
        open_front(app)
        app.click(app.by_id("excel-icon")["ref"])
        assert app.find("조회된 데이터가 없습니다")
        assert last(app, "export_rejected")["reason"] == "no_data"

def test_unwritable_target_is_reported_and_not_logged_as_saved(tmp_path):
    target = tmp_path / "locked.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app, open(target, "wb"):
        open_front(app)
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        app.click(app.by_id("excel-icon")["ref"])
        app.click(app.find("저장", role="button")["ref"])
        app.save_dialog(target, confirm_overwrite=True)
        app.wait_for_event("export_failed")
        assert not [e for e in app.events() if e["event"] == "export_saved"]
```

`LiveApp.save_dialog(path, confirm_overwrite=False)`: 표준 저장 대화상자의 파일 이름 칸에 `set-value`로 전체 경로를 넣고 `저장` 버튼을 ref로 누른다. 덮어쓰기 확인이 뜨면 `예`를 누른다.

- [ ] **Step 2: 구현**
- [ ] **Step 3: 빌드하고, 사용자 확인 뒤 실행** — Expected: 전부 PASS.
- [ ] **Step 4: 커밋** — `git commit -m "feat: add virtual mes excel export through the icon"`

### Task 8: 우클릭 원형 메뉴

**Files:**
- Create: `virtual-mes/RadialMenu.cs`
- Modify: `virtual-mes/ScreenPanel.cs`, `Session.cs`, `tests/test_virtual_mes_live.py`

**Interfaces:**
- Consumes: `PopupHost`, `ExportFlow.SaveAs`, `Surface.BackgroundRightClick`, `SurfaceItem.RightClick`
- Produces:
  - `static void RadialMenu.Show(Form owner, Point screenCenter, Session session, Func<QueryResult> source, string sourceName)`: 지름 `Ui.Px(170)`의 원형 팝업, 중심이 우클릭 지점. `Region.Radial`의 Surface 하나에 칸마다 `Graphic` 항목(id `radial-<글자>`, `Text`는 칸의 글자, `Paint`가 부채꼴과 글자를 그림). 클릭 판정은 각도로 한다.
  - 1단계: 위 `export`, 오른쪽 `copy`, 아래 `print`, 왼쪽 `close`. `export` 외에는 메뉴만 닫는다.
  - 2단계(같은 자리): 오른쪽 위 `excel`, 아래 `html`, 왼쪽 위 `pdf`. `excel`은 `ExportFlow.SaveAs(..., route "radial")`, `pdf`와 `html`은 닫기만.
  - 데이터 패널의 표 어디서든(빈 곳 포함) 우클릭하면 뜬다. 결과가 없어도 뜨며, 그때 `excel`은 `조회된 데이터가 없습니다`.
  - 이벤트: `radial_opened {stage: 1|2, source}`, `radial_closed {stage, reason: "choice"|"outside_click"|"escape"|"deactivated"}`, `export_choice {route: "radial", choice: "excel"|"pdf"|"html"|"copy"|"print"|"close"}`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_radial_export_saves_through_two_stages(tmp_path):
    target = tmp_path / "radial.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("권취 Roller 점검일시")["ref"])
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        app.right_click(app.by_id("cell-1-0")["ref"])
        app.click(app.by_id("radial-export")["ref"])
        app.click(app.by_id("radial-excel")["ref"])
        app.save_dialog(target)
        app.wait_for_event("export_saved")
        assert [e["stage"] for e in app.events() if e["event"] == "radial_opened"] == [1, 2]
        choice = last(app, "export_choice")
        assert (choice["route"], choice["choice"]) == ("radial", "excel")
    expected = tmp_path / "expected.xlsx"
    assert export("roller-check", {}, expected).returncode == 0
    assert read_xlsx(target) == read_xlsx(expected)

def test_radial_in_pixel_mode_exposes_a_window_but_no_labels(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--expose", "radial=pixel", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        before = {w["id"] for w in app.windows()}
        app.right_click(app.by_id("cell-1-0")["ref"])
        new = [w for w in app.windows() if w["id"] not in before]
        assert len(new) == 1
        assert not [n for n in app.nodes(new[0]["id"]) if n.get("name") in ("export", "excel")]

def test_radial_closes_on_escape(tmp_path):
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        open_front(app)
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        app.right_click(app.by_id("cell-1-0")["ref"])
        app.press("escape")
        assert last(app, "radial_closed")["reason"] == "escape"
```

`LiveApp.right_click(ref)`는 `agent-desktop right-click`을 쓴다. 이 명령이 실제 커서를 옮길 수 있으므로 이 작업의 테스트는 사용자가 자리를 비웠을 때 돌린다.

- [ ] **Step 2: 구현**
- [ ] **Step 3: 빌드하고, 사용자 확인 뒤 실행** — Expected: 전부 PASS.
- [ ] **Step 4: 커밋** — `git commit -m "feat: add the virtual mes radial export menu"`

### Task 9: 표 화면과 셀 팝업

**Files:**
- Create: `virtual-mes/CellPopup.cs`
- Modify: `virtual-mes/ScreenPanel.cs`, `Session.cs`, `tests/test_virtual_mes_live.py`

**Interfaces:**
- Consumes: `GridSurface.Fill`의 `cellClick`, `RadialMenu.Show`, `MesData.Query("defect-detail", ...)`
- Produces:
  - 화면 C(`Kind == "matrix"`)에서 숫자 셀(1~3열)을 클릭하면 `CellPopup`이 뜬다. 0열(공정 이름)과 화면 A·B의 셀은 클릭해도 아무 일이 없다.
  - `sealed class CellPopup : Form { CellPopup(Session session, string row, string column); }`: 본 창이 소유하는 모달 아닌 창, 제목 `불량 상세 · <행> · <열>`, 안에 `Region.Grid` Surface로 그린 불량 상세 표. 표를 우클릭하면 `RadialMenu.Show(..., sourceName "cell_popup")`. 한 번에 하나만 열리고, 다른 셀을 누르면 내용이 바뀐다. 화면을 바꾸면 닫힌다.
  - 이벤트: `cell_popup_opened {screen, row, column}`. 팝업에서 저장하면 `export_saved.source`는 `cell_popup`, 기본 파일 이름은 `불량 상세 <행>.xlsx`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_cell_popup_exports_its_own_table(tmp_path):
    target = tmp_path / "detail.xlsx"
    with LiveApp(tmp_path, "--profile", "easy", "--delay-ms", "200") as app:
        app.click(app.find("생산운영", role="button")["ref"])
        app.click(app.find("공정별 불량현황")["ref"])
        app.click(app.by_id("query-button")["ref"])
        app.wait_for_event("query_finished")
        app.click(app.by_id("cell-3-2")["ref"])
        opened = last(app, "cell_popup_opened")
        assert opened["row"] == "조립" and opened["column"] == "불량수"
        popup = next(w for w in app.windows() if w["title"].startswith("불량 상세"))
        detail_cell = next(n for n in app.nodes(popup["id"]) if n.get("id") == "cell-1-0")
        app.right_click(detail_cell["ref"])
        app.click(app.by_id("radial-export")["ref"])
        app.click(app.by_id("radial-excel")["ref"])
        app.save_dialog(target)
        app.wait_for_event("export_saved")
        assert last(app, "export_saved")["source"] == "cell_popup"
    expected = tmp_path / "expected.xlsx"
    assert export("defect-detail", {"row": "조립"}, expected).returncode == 0
    assert read_xlsx(target) == read_xlsx(expected)
```

- [ ] **Step 2: 구현**
- [ ] **Step 3: 빌드하고, 사용자 확인 뒤 실행** — Expected: 전부 PASS.
- [ ] **Step 4: 커밋** — `git commit -m "feat: add the virtual mes matrix screen and cell popup"`

### Task 10: 프로파일 확인, README, 문서 정리

**Files:**
- Create: `virtual-mes/README.md`
- Modify: `tests/test_virtual_mes_live.py`, `examples/llm-agent/README.md`, `CLAUDE.md`, 필요하면 설계 문서

- [ ] **Step 1: 프로파일 테스트 작성** (설계 문서의 완료 기준 3, 4)

```python
def test_mes_like_profile_matches_what_the_user_saw(tmp_path):
    with LiveApp(tmp_path, "--delay-ms", "200") as app:
        main = app.windows()[0]["id"]
        menu = next(n for n in app.nodes(main) if n.get("name") == "생산운영")
        assert menu.get("ref")
        app.click(menu["ref"])
        app.click(app.find("전공정 생산현황")["ref"])
        nodes = app.nodes(main)
        for text in ("Shop", "공정", "제품", "기간", "전일"):
            node = next(n for n in nodes if n.get("name") == text)
            assert "ref" not in node and node.get("bounds")
        app.click_at_center(next(n for n in nodes if n.get("name") == "전일"))
        assert len([n for w in app.windows() for n in app.nodes(w["id"]) if n.get("name") == "기간"]) == 2
        assert not [n for n in nodes if n.get("name") == "엑셀 저장"]
        assert app.find("조회(F5)", role="button").get("ref")

def test_hard_profile_hides_filter_and_grid_text(tmp_path):
    with LiveApp(tmp_path, "--profile", "hard") as app:
        main = app.windows()[0]["id"]
        menu = next(n for n in app.nodes(main) if n.get("name") == "생산운영")
        assert "ref" not in menu
        app.click_at_center(menu)
        app.click_at_center(app.find("전공정 생산현황"))
        names = {n.get("name") for n in app.nodes(main)}
        assert not names & {"Shop", "공정", "제품", "전일", "전공정 생산현황"}
        assert "조회(F5)" in names
```

`기간`이 2개인 것은 필터 이름과 열린 목록의 항목이다. `LiveApp.click_at_center(node)`는 노드의 `bounds` 중심을 `agent-desktop mouse-click`으로 누른다(실제 커서 사용).

- [ ] **Step 2: 사용자 확인 뒤 창을 띄우는 테스트 전체 실행**

Run: `$env:VIRTUAL_MES_LIVE='1'; python -m pytest tests/test_virtual_mes_live.py -q`
Expected: 전부 PASS.

- [ ] **Step 3: 사용자와 함께 손으로 확인** (완료 기준 2, 5)

`mes-like`로 띄우고 사용자가 직접 한다: 메뉴 → 전공정 생산현황 → Shop을 조립만 → 기간 전일 → 조회 → 아이콘 저장. 이어서 권취 Roller 점검일시 → 우클릭 저장, 공정별 불량현황 → 셀 클릭 → 팝업에서 우클릭 저장. 끝난 뒤 이벤트 기록에서 `filter_changed`, `query_started`, `export_saved` 세 종류가 고른 값대로 남았는지 함께 본다. 사용자가 기억과 다르다고 한 곳은 고치고 배경 문서의 "MES 화면"에 반영한다.

- [ ] **Step 4: `virtual-mes/README.md` 작성**

빌드, 실행 옵션 표(`--parse-only`는 테스트용), 프로파일 표, `VirtualMes.args`, 이벤트 표(이 계획에서 더한 `query_rejected`, `export_failed`, `export_rejected` 포함), 고정 데이터 요약, 테스트 실행 방법(`VIRTUAL_MES_LIVE=1`은 실제 창을 띄우고 일부는 커서를 옮긴다는 경고 포함)을 적는다. `.agents/skills/technical-writer/SKILL.md`를 따른다.

- [ ] **Step 5: 가리키는 줄 추가**

`examples/llm-agent/README.md`에 가상 MES 절(두세 줄과 링크), `CLAUDE.md`의 "examples/llm-agent" 절에 빌드 명령과 창을 띄우는 테스트의 주의 한 줄을 더한다. 설계 문서의 상태를 `구현됨`으로 바꾸고, 구현하면서 달라진 점이 남아 있으면 고친다.

- [ ] **Step 6: 전체 테스트와 커밋**

Run: `python -m pytest -q`
Expected: 전부 PASS(창을 띄우는 테스트는 skip).

```bash
git add examples/llm-agent CLAUDE.md
git commit -m "docs: document the virtual mes app and its profiles"
```
