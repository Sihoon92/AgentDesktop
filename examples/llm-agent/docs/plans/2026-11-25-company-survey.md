# 새 시스템 조사(`survey.py`)와 회사 GLM 실행 전용 장치 구현 계획

> **에이전트 작업자에게:** 필수 하위 스킬: superpowers:subagent-driven-development(추천) 또는 superpowers:executing-plans로 작업 단위로 구현합니다. 작업마다 superpowers:test-driven-development를 따릅니다. 단계는 체크박스(`- [ ]`)로 표시합니다.

- 날짜: 2026-11-25
- 상태: 대체됨(2026-11-27). [캡처 세션 계획](2026-11-27-company-capture-session.md)이 이 계획을 대체하며, 그 계획이 가리키는 작업(1·2·3·6·8·10)의 시험과 규칙만 여기서 다시 씁니다.
- 설계: [새 시스템 조사와 회사 GLM 실행 전용 장치](../specs/2026-11-25-company-survey-design.md) (사용자 승인 2026-11-25). 값과 규칙은 설계가 기준이고, 이 계획은 파일, 이름, 시그니처, 테스트, 순서를 정합니다. 구현하는 사람은 둘 다 읽습니다.

**목표:** 회사에서 `python survey.py --system <이름>` 하나로 점검, 툴킷·창·UIA 조사, 가설 판정, 결과 md·오버레이·전달 블록을 만들고, 회사 Claude Code가 `company/`에서는 코드를 고치지 못하게 합니다.

**구조:** 사실을 모으는 모듈(`win32_facts`, `uia_facts`, `git_state`, 대상 고르기 `survey_target`)이 `SurveyFacts` 하나를 채우고, `hypotheses`가 그것만 보고 판정하며, `survey_report`가 같은 가설 정의로 결과 md, 가설 문서, 전달 블록을 만듭니다. `survey.py`는 이것들을 잇고, 바이너리·설정·LLM 점검은 `check.run_checks(..., skip=["app"])`를 그대로 부릅니다. `company/`는 독립된 규칙 파일, 훅 설정, 훅 스크립트입니다.

**쓰는 것:** Python 표준 라이브러리(ctypes, subprocess), 지금의 의존성(PyYAML, Pillow), Windows PowerShell 5.1의 `UIAutomationClient`. 의존성을 더하지 않습니다. `agent-desktop` CLI는 고치지 않습니다.

## 전체에 걸친 조건

- **공개 범위(설계 "사용자가 정한 것" A):** 콘솔, 전달 블록, 기본 `survey.md`, `facts.json`에는 화면 글자, 요소 이름, 경로, 원래의 창 클래스 이름, DLL 이름, 설정 값이 없습니다. 계열 이름(`agent/toolkits.py`의 이름)과 수, 판정 코드만. `--show-names`일 때만 `survey.md`의 마지막 절 `## 원래 이름 (옮겨 적기 전에 확인)`에 모르는 창 클래스와 사내 DLL의 파일 이름(경로 없이)을 적습니다. Windows 폴더 아래 모듈은 그 절에도 넣지 않습니다.
- **앱에 쓰지 않음:** 조사가 CLI에 보내는 명령은 `session`, `launch`, `list-windows`, `snapshot`, `find`, `screenshot`, `version`뿐입니다. 앱을 닫지 않습니다.
- `agent/` 코드와 `survey.py`에 앱 이름이나 화면 글을 넣지 않습니다. 특히 판정의 "집에서 할 일" 글에 `MES`라는 낱말을 쓰지 않고 "연습 앱"이라고 씁니다(`tests\test_application_agnostic.py`의 `\bMES\b`). 툴킷과 부품 이름(DevExpress, Nexacro 등)은 써도 됩니다.
- 값: `SURVEY_VERSION = 1`, 결과 폴더 `survey-results/<YYYYMMDD-HHMMSS>-<시스템>/`, UIA 노드 상한 `UIA_MAX_NODES = 5000`, UIA 시간 상한 `UIA_TIMEOUT_S = 60`, 전달 블록 최대 10줄·한 줄에 가설 4개, 스냅샷 읽기 제한 8000ms(점검과 같음).
- 판정 코드 집합은 설계 2장의 표와 3장의 H06 집합을 그대로 씁니다. 앱을 읽지 못한 경우 앱에 기대는 가설의 코드는 `SKIPPED`, 사람이 답하는 가설의 기본 코드는 `?`.
- 출력은 `check.py`처럼 `sys.stdout.reconfigure(errors="replace")`로 cp949 콘솔에서도 죽지 않습니다. 파일은 UTF-8.
- 작업자는 커밋하지 않습니다(커밋은 사용자가 요청할 때만). 실제 창을 띄우는 실행과 실제 모델 호출을 하지 않습니다. 자기 Python 프로세스나 새로 띄운 콘솔 없는 하위 프로세스(`python -c "import time; time.sleep(5)"`)를 Win32로 읽는 테스트는 해도 됩니다.
- 테스트는 `examples/llm-agent`에서 `/c/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -X utf8 -m pytest -q -p no:cacheprovider`(아래 `pytest`)로 돌립니다. 제출 전 전체 실패 0. 테스트마다 구현을 되돌려 실패하는지 확인하고 보고서에 적습니다.

## 확인할 곳 (작업의 테스트가 직접 다루지 않는 것)

1. **관리자 권한으로 도는 대상 앱:** `OpenProcess`가 거부되어 모듈과 실행 파일 경로를 읽지 못해도 조사는 끝까지 가고, 모듈 계열은 `읽지 못함`, H01은 창 클래스만으로 판정합니다. → 작업 2에 테스트(가짜 거부).
2. **32비트 대상 앱(64비트 Python):** 모듈 목록은 `EnumProcessModulesEx(..., LIST_MODULES_ALL)`로 읽어 32비트 모듈도 나옵니다. → 작업 2의 지시와 확인 메모(실제 32비트 대상은 실제 창 확인 때).
3. **PowerShell이 막히거나 느림:** 실행 정책, 시간 초과, 깨진 출력이면 UIA 사실은 `None`이고 H04는 `SKIPPED(UIA_*)`, 나머지는 그대로 판정. → 작업 3과 작업 9에 테스트.
4. **한글이나 비ASCII 이름:** `--show-names`의 이름과 시스템 이름에 한글이 있어도 파일과 콘솔이 깨지지 않음. → 작업 5에 테스트.
5. **같은 초에 두 번 실행:** 결과 폴더가 겹치면 뒤에 `-2`, `-3`을 붙여 덮어쓰지 않음. → 작업 9에 테스트.

---

## 파일 구조

| 파일 | 하는 일 |
| --- | --- |
| `agent/toolkits.py` (새) | 창 클래스와 모듈 이름 → 계열 이름 판별 표와 셈 |
| `agent/win32_facts.py` (새) | ctypes로 프로세스 트리, 실행 파일 경로, 창 클래스, 모듈, 모니터 읽기 |
| `agent/uia_count.ps1` (새), `agent/uia_facts.py` (새) | PowerShell UIA로 FrameworkId·패턴 개수 세기와 그 결과 읽기 |
| `agent/survey_facts.py` (새) | `SurveyFacts` 자료형 |
| `agent/hypotheses.py` (새) | 가설 정의와 판정 |
| `agent/survey_report.py` (새) | 결과 md, 가설 문서, 전달 블록 |
| `agent/git_state.py` (새) | 커밋 해시와 코드 변경 파일 |
| `agent/survey_target.py` (새), `agent/ready.py` (고침) | 떠 있는 창에 붙거나 띄워서 대상 창 고르기 |
| `agent/overlay.py` (새) | 오버레이 PNG |
| `survey.py` (새) | 진입점 |
| `docs/company-survey/hypotheses.md` (생성, 커밋) | 가설 문서 |
| `company/CLAUDE.md`, `company/.claude/settings.json`, `company/hooks/guard.py` (새) | 회사 Claude Code 실행 전용 |
| `.gitignore`(이 폴더), 저장소 루트 `.gitignore` (고침) | `survey-results/` 제외, `company/.claude/` 예외 |
| `tests/test_toolkits.py`, `test_win32_facts.py`, `test_uia_facts.py`, `test_hypotheses.py`, `test_survey_report.py`, `test_git_state.py`, `test_survey_target.py`, `test_overlay.py`, `test_survey_cli.py`, `test_company_guard.py` (새; 기존 `test_survey.py`는 `agent/survey.py`의 시험이라 그대로 둠), `tests/test_application_agnostic.py` (고침) | 시험 |

---

### 작업 1: 계열 판별 표 (`agent/toolkits.py`)

**Files:** Create `agent/toolkits.py`, `tests/test_toolkits.py`

**Interfaces:**
- Produces:
  - `UI_FAMILIES = ("WinForms", "WPF", "Win32", "MFC", "Delphi", "Java", "Qt", "Nexacro", "WebView2", "CEF", "IE")`
  - `GRID_FAMILIES = ("DevExpress", "Infragistics", "FarPoint", "ComponentOne", "Syncfusion", "Telerik", "ActiveX")`
  - `class_family(name: str) -> str | None`: 창 클래스 하나의 계열. 모르면 None.
  - `module_family(file_name: str) -> str | None`: 모듈 파일 이름(경로 없이) 하나의 계열(UI 또는 그리드). 모르면 None.
  - `is_system_module(path: str, windows_dir: str) -> bool`: `windows_dir` 아래면 True(대소문자 무시).
  - `count_families(names: Iterable[str], classify) -> FamilyCount`, `FamilyCount = NamedTuple(families: dict[str,int], unknown: int, unknown_names: tuple[str,...])`. `unknown_names`는 정렬·중복 제거.

- [ ] **Step 1: 실패하는 시험 작성**

```python
@pytest.mark.parametrize("name, family", [
    ("WindowsForms10.Window.8.app.0.141b42a_r6_ad1", "WinForms"),
    ("HwndWrapper[App;;1f2e]", "WPF"),
    ("TfrmMain", "Delphi"), ("TApplication", "Delphi"),
    ("SunAwtFrame", "Java"), ("Qt5QWindowIcon", "Qt"), ("Afx:00400000:8", "MFC"),
    ("Chrome_WidgetWin_1", "WebView2"), ("Internet Explorer_Server", "IE"),
    ("#32770", "Win32"), ("Button", "Win32"), ("SysListView32", "Win32"),
    ("CompanyGridWnd", None),
])
def test_class_family(name, family): assert class_family(name) == family

@pytest.mark.parametrize("name, family", [
    ("DevExpress.XtraGrid.v21.2.dll", "DevExpress"), ("Infragistics4.Win.UltraWinGrid.v19.1.dll", "Infragistics"),
    ("FPSpreadADO.ocx", "FarPoint"), ("FarPoint.Win.Spread.dll", "FarPoint"),
    ("C1.Win.C1FlexGrid.4.dll", "ComponentOne"), ("Syncfusion.Grid.Windows.dll", "Syncfusion"),
    ("Telerik.WinControls.GridView.dll", "Telerik"), ("AxInterop.SomeCtl.dll", "ActiveX"),
    ("nexacro.exe", "Nexacro"), ("XPlatform.dll", "Nexacro"), ("MiPlatform.dll", "Nexacro"),
    ("WebView2Loader.dll", "WebView2"), ("libcef.dll", "CEF"), ("vcl270.bpl", "Delphi"),
    ("System.Windows.Forms.dll", "WinForms"), ("PresentationFramework.dll", "WPF"), ("jvm.dll", "Java"),
    ("Qt5Core.dll", "Qt"), ("mfc140u.dll", "MFC"), ("CompanyMes.Core.dll", None),
])
def test_module_family(name, family): assert module_family(name) == family

def test_system_module_is_under_the_windows_folder():
    assert is_system_module(r"C:\WINDOWS\System32\user32.dll", r"C:\Windows")
    assert not is_system_module(r"C:\Program Files\App\App.dll", r"C:\Windows")

def test_count_keeps_unknown_names_sorted_and_unique():
    counted = count_families(["TfrmA", "TfrmB", "X", "X", "Y"], class_family)
    assert counted.families == {"Delphi": 2} and counted.unknown == 3 and counted.unknown_names == ("X", "Y")
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_toolkits.py` → ImportError
- [ ] **Step 3: 구현.** 표는 `(정규식, 계열)` 튜플 목록, 대소문자 무시, 처음 맞는 것. Delphi 클래스는 `^T[A-Z][A-Za-z0-9]+$`이고 이 규칙은 Win32 일반 컨트롤 표 다음에 둡니다. Win32 일반 컨트롤은 `#32770`, `Button`, `Edit`, `Static`, `ComboBox`, `ListBox`, `ScrollBar`, `SysListView32`, `SysTreeView32`, `SysTabControl32`, `msctls_*`, `ToolbarWindow32`, `ReBarWindow32`, `MDIClient`.
- [ ] **Step 4: 통과 확인** — `pytest tests/test_toolkits.py`

### 작업 2: Win32 사실 (`agent/win32_facts.py`)

**Files:** Create `agent/win32_facts.py`, `tests/test_win32_facts.py`

**Interfaces:**
- Produces (모두 Windows 밖에서는 빈 값이나 None):
  - `process_tree(pid: int) -> tuple[int, ...]`: `pid`와 그 자손(Toolhelp 스냅샷의 부모 관계), `pid`가 처음.
  - `process_image(pid: int) -> str | None`: `QueryFullProcessImageNameW`, 거부되면 None.
  - `process_bits(pid: int) -> int | None`: 32 또는 64(`IsWow64Process`), 모르면 None.
  - `windows_of(pids: Iterable[int]) -> tuple[WindowInfo, ...]`, `WindowInfo = NamedTuple(hwnd:int, class_name:str, top_level:bool, visible:bool)`: 최상위와 그 자식 창 전부.
  - `modules_of(pid: int) -> tuple[str, ...] | None`: 모듈 전체 경로, `LIST_MODULES_ALL`. 열지 못하면 None.
  - `monitor_of(hwnd: int) -> MonitorInfo | None`, `MonitorInfo = NamedTuple(width:int, height:int, scale:float, count:int)`: 창이 있는 모니터의 해상도와 배율(`GetDpiForMonitor`/96), 모니터 수.
  - `windows_dir() -> str`

- [ ] **Step 1: 실패하는 시험 작성** (Windows에서만, 나머지는 `pytest.mark.skipif`)

```python
def test_process_tree_finds_a_child(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], creationflags=subprocess.CREATE_NO_WINDOW)
    try: assert process_tree(os.getpid())[0] == os.getpid() and child.pid in process_tree(os.getpid())
    finally: child.kill()

def test_own_image_and_modules():
    assert Path(process_image(os.getpid())).name.lower().startswith("python")
    assert any(Path(m).name.lower() == "kernel32.dll" for m in modules_of(os.getpid()))
    assert process_bits(os.getpid()) in (32, 64)

def test_denied_process_reads_as_none(monkeypatch):
    monkeypatch.setattr(win32_facts, "_open_process", lambda pid: None)
    assert modules_of(os.getpid()) is None and process_image(os.getpid()) is None

def test_no_window_and_no_monitor_for_a_dead_handle():
    assert windows_of([999999]) == () and monitor_of(0) is None
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현.** `ctypes.WinDLL(..., use_last_error=True)`와 argtypes를 `agent/handles.py`처럼 명시. 프로세스 열기는 `_open_process(pid) -> handle | None` 하나로 모아(`PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ`) 시험이 바꿀 수 있게 하고, 핸들은 항상 닫습니다. `GetDpiForMonitor`(shcore)가 없으면 scale 1.0.
- [ ] **Step 4: 통과 확인**

### 작업 3: UIA 개수 (`agent/uia_count.ps1`, `agent/uia_facts.py`)

**Files:** Create `agent/uia_count.ps1`, `agent/uia_facts.py`, `tests/test_uia_facts.py`

**Interfaces:**
- Produces:
  - `UIA_MAX_NODES = 5000`, `UIA_TIMEOUT_S = 60`, `PATTERNS = ("Invoke", "Value", "Toggle", "ExpandCollapse", "Selection", "Grid", "Table", "LegacyIAccessible")`
  - `UiaCounts = NamedTuple(nodes:int, truncated:bool, frameworks:dict[str,int], patterns:dict[str,int])`
  - `count_uia(hwnd: int, runner=subprocess.run) -> tuple[UiaCounts | None, str | None]`: (개수, 실패 코드). 코드는 `UIA_SPAWN`, `UIA_TIMEOUT`, `UIA_EXIT`, `UIA_OUTPUT`.
  - `parse_counts(text: str) -> UiaCounts | None`

PowerShell 명령: `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <agent/uia_count.ps1> -Hwnd <hwnd> -MaxNodes 5000`. 스크립트는 `AutomationElement.FromHandle`과 `TreeWalker.RawViewWalker`로 너비 우선 순회하며 `Current.FrameworkId`(빈 값은 `"(none)"`)와 `GetSupportedPatterns()`의 패턴 이름(`ProgrammaticName`에서 `Pattern`과 `Identifiers` 앞까지)을 세고, `{"nodes":…, "truncated":…, "frameworks":{…}, "patterns":{…}}` 한 줄 JSON만 출력합니다. FrameworkId 값은 툴킷 이름(`WinForm`, `Win32`, `WPF`, `DirectUI` 등)이고 화면 글이 아닙니다.

- [ ] **Step 1: 실패하는 시험 작성**

```python
def test_parse_keeps_only_listed_patterns():
    counts = parse_counts('{"nodes":12,"truncated":false,"frameworks":{"WinForm":11,"(none)":1},"patterns":{"Invoke":3,"Text":2}}')
    assert counts == UiaCounts(12, False, {"WinForm": 11, "(none)": 1}, {"Invoke": 3})

@pytest.mark.parametrize("text", ["", "not json", '{"nodes":"x"}', "[]"])
def test_broken_output_is_none(text): assert parse_counts(text) is None

def test_runner_failures_become_codes():
    def timeout(*a, **k): raise subprocess.TimeoutExpired("powershell", 60)
    assert count_uia(1, timeout) == (None, "UIA_TIMEOUT")
    def exits(*a, **k): return subprocess.CompletedProcess(a, 1, "", "denied")
    assert count_uia(1, exits) == (None, "UIA_EXIT")

def test_command_uses_bypass_and_the_repo_script():
    seen = {}
    def run(argv, **k): seen["argv"] = argv; return subprocess.CompletedProcess(argv, 0, '{"nodes":1,"truncated":false,"frameworks":{},"patterns":{}}', "")
    count_uia(42, run)
    assert "-ExecutionPolicy" in seen["argv"] and "Bypass" in seen["argv"] and seen["argv"][seen["argv"].index("-Hwnd") + 1] == "42"

def test_script_never_reads_names_or_values():
    text = (AGENT / "uia_count.ps1").read_text(encoding="utf-8")
    for forbidden in ("Current.Name", "AutomationId", "ValuePattern", "HelpText", "ClassName"):
        assert forbidden not in text
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** (`timeout=UIA_TIMEOUT_S`, `encoding="utf-8", errors="replace"`, `stdin=DEVNULL`, `creationflags=CREATE_NO_WINDOW`(Windows에서만))
- [ ] **Step 4: 통과 확인**

### 작업 4: 사실 자료형과 가설 판정 (`agent/survey_facts.py`, `agent/hypotheses.py`)

**Files:** Create `agent/survey_facts.py`, `agent/hypotheses.py`, `tests/test_hypotheses.py`

**Interfaces:**
- Consumes: `FamilyCount`(작업 1), `MonitorInfo`(작업 2), `UiaCounts`(작업 3), `checks.Result`
- Produces:
  - `SurveyFacts` (frozen dataclass, 기본값 있음): `app_code: str | None`(앱을 읽지 못한 이유 코드, 읽었으면 None), `launch: str`(H06 코드), `kinds: dict`(`agent.survey.kinds` 결과), `complete: bool`, `read_s: float`, `ids: str`(`STABLE`/`CHANGES`/`NO_IDS`/`PENDING`), `classes: FamilyCount | None`, `modules: FamilyCount | None`(None이면 읽지 못함), `mdi: bool`, `visible_top: int`, `uia: UiaCounts | None`, `uia_code: str | None`, `monitor: MonitorInfo | None`, `capture: tuple[int,int] | None`, `checks: dict[str, checks.Result]`(점검 이름 → 결과)
  - `Hypothesis` (frozen dataclass): `id, title, why, kind("auto"|"manual"), codes: tuple[str,...], next: dict[str,str], judge: Callable[[SurveyFacts], Verdict] | None, how_to_answer: str | None, status: str = "open", closed_note: str | None = None`
  - `Verdict = NamedTuple(code: str, short: str, evidence: str)`: `short`는 전달 블록 괄호 안(없으면 ""), `evidence`는 결과 md의 근거 한 줄. 모두 수·계열 이름·코드만.
  - `HYPOTHESES: tuple[Hypothesis, ...]` (H01–H14, 설계 2장의 순서·질문·코드·다음 할 일)
  - `judge_all(facts: SurveyFacts) -> list[tuple[Hypothesis, Verdict]]`: manual은 `Verdict("?", "", "")`. 판정 중 예외는 `Verdict("ERROR", type(e).__name__, "")`.

판정 규칙(설계가 구현자에게 맡긴 문턱값을 여기서 정함):
- 앱 가설(H01–H05, H07–H09, H11, H12–H14 제외 manual)은 `facts.app_code`가 있으면 `Verdict("SKIPPED", app_code, "")`. H06과 H10은 항상 판정.
- **H01:** 창 클래스 계열과 모듈 UI 계열을 더한 수에서 `Win32`는 다른 계열이 하나도 없을 때만 셈. 가장 많은 계열이 70% 이상이면 그 계열(`WebView2`·`CEF`·`IE`는 `WEB`, 나머지는 대문자: `WINFORMS`, `WPF`, `WIN32`, `MFC`, `DELPHI`, `JAVA`, `QT`, `NEXACRO`), 계열이 둘 이상이고 그런 계열이 없으면 `MIXED`, 하나도 없으면 `UNKNOWN`. UIA FrameworkId `WinForm`/`WPF`/`Win32`는 같은 계열에 1씩 더함. short: `"<1위 수>/<전체 수>"`.
- **H02:** 모듈의 그리드 계열 중 가장 많은 것(대문자, `ActiveX`는 `ACTIVEX_OTHER`), 없고 UIA `Grid` 패턴 > 0이면 `NATIVE`, 아니면 `NONE_SEEN`. 모듈을 못 읽고 UIA도 없으면 `SKIPPED`(`MODULES_UNREAD`).
- **H03:** `reach = named_ref + text`, `total = named_ref + unnamed_ref + text + blank`. total 0이면 `LOW`. `reach/total ≥ 0.6` `RICH`, `≥ 0.2` `PARTIAL`, 아니면 `LOW`. short `"<named_ref>/<text>/<blank>"`.
- **H04:** uia None이면 `SKIPPED`(uia_code). Invoke > 0이고 Value+Toggle+ExpandCollapse+Selection > 0이면 `PATTERNS_OK`, Invoke만 > 0이면 `INVOKE_ONLY`, 아니면 `NONE`. short `"I<Invoke>·V<Value>·T<Toggle>"`.
- **H05:** `facts.ids` 그대로.
- **H06:** `facts.launch` 그대로.
- **H07:** mdi면 `MDI`, visible_top > 1이면 `MULTI_TOP`, 아니면 `SINGLE`.
- **H08:** 계열에 WebView2 → `WEBVIEW2`, CEF → `CEF`, IE → `IE`(그 순서), 없으면 `NONE`.
- **H09:** monitor None이면 `SKIPPED`. 코드 `f"{w}x{h}@{scale:g}"`, short `f"모니터{count}"`.
- **H10:** 점검 결과 `LLM 도구 호출`이 통과가 아니면 `FAIL`(short=그 code나 `SKIPPED`), `LLM 그림 입력` 통과 또는 `비전 호출` 통과면 `TOOLS_IMAGE`, 아니면 `TOOLS_ONLY`. 점검을 건너뛰었으면 `SKIPPED`. short에 비전 모델이 있으면 `비전`.
- **H11:** 코드 `COMPLETE`/`TRUNCATED`, short `f"{read_s:.1f}s"`.

- [ ] **Step 1: 실패하는 시험 작성** — 가설마다 사실 → 판정 표 시험. 최소한 아래와 규칙의 경계(0.6, 0.2, 70%)마다 하나씩.

```python
def facts(**changes): return replace(SurveyFacts(), **changes)

def test_registry_rules():
    ids = [h.id for h in HYPOTHESES]
    assert ids == [f"H{n:02d}" for n in range(1, 15)]
    for h in HYPOTHESES:
        assert (h.judge is not None) == (h.kind == "auto") and (h.how_to_answer is not None) == (h.kind == "manual")
        assert set(h.next) == set(h.codes) - {"SKIPPED", "?"} or h.id in ("H09", "H11")

def test_h01_delphi_wins_at_seventy_percent():
    f = facts(classes=FamilyCount({"Delphi": 7, "WinForms": 3}, 0, ()), modules=FamilyCount({}, 0, ()))
    assert verdict(f, "H01").code == "DELPHI"

def test_h01_win32_only_counts_alone():
    f = facts(classes=FamilyCount({"Win32": 40, "WinForms": 5}, 0, ()), modules=FamilyCount({}, 0, ()))
    assert verdict(f, "H01").code == "WINFORMS"

def test_h03_boundaries():
    assert verdict(facts(kinds=kinds_of(named_ref=6, text=0, blank=4)), "H03").code == "RICH"
    assert verdict(facts(kinds=kinds_of(named_ref=2, blank=8)), "H03").code == "PARTIAL"
    assert verdict(facts(kinds=kinds_of(named_ref=1, blank=9)), "H03").code == "LOW"

def test_app_failure_skips_app_hypotheses_but_not_launch_or_llm():
    out = dict((h.id, v) for h, v in judge_all(facts(app_code="ATTACH_AMBIGUOUS", launch="ATTACHED")))
    assert out["H01"] == Verdict("SKIPPED", "ATTACH_AMBIGUOUS", "") and out["H06"].code == "ATTACHED" and out["H12"].code == "?"

def test_a_judge_that_raises_becomes_error(monkeypatch): ...

def test_texts_hold_no_app_words():
    for h in HYPOTHESES:
        for text in (h.title, h.why, h.how_to_answer or "", *h.next.values()):
            assert not re.search(r"\bMES\b", text)
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현.** 가설 글(질문, 왜, 다음 할 일, 답하는 방법)은 설계 2장의 표를 문장으로 풀어 씁니다. H12–H14의 답하는 방법은 오버레이 색 이름(작업 8의 범례)을 가리킵니다.
- [ ] **Step 4: 통과 확인**

### 작업 5: 결과 md, 가설 문서, 전달 블록 (`agent/survey_report.py`)

**Files:** Create `agent/survey_report.py`, `tests/test_survey_report.py`, `docs/company-survey/hypotheses.md`(생성)

**Interfaces:**
- Consumes: `HYPOTHESES`, `judge_all`, `Verdict`, `SurveyFacts`
- Produces:
  - `RunHeader = NamedTuple(when: str, system: str, head: str | None, changed: tuple[str, ...], version: int)`
  - `transmission_block(header: RunHeader, judged) -> list[str]`: 첫 줄 `[전달] survey v1 · <head 또는 "커밋?"> · <"변경없음" 또는 "변경있음 N"> · <system>`, 이어서 `open` 가설만 `"<ID> <code>"` + (`short`가 있으면 `"(<short>)"`), 한 줄에 4개를 공백 3칸으로. 최대 10줄(넘으면 마지막 줄을 `… 외 N개`로).
  - `result_markdown(header, judged, show_names: NamesForReport | None) -> str`; `NamesForReport = NamedTuple(classes: tuple[str,...], modules: tuple[str,...])`
  - `hypotheses_markdown() -> str`
  - `HYPOTHESES_DOC = Path("docs/company-survey/hypotheses.md")`(이 폴더 기준)

결과 md 모양은 설계 4장. 변경 파일 이름은 결과 md의 머리에만(`- 바뀐 추적 파일: a.py, b.py`). `show_names`가 있으면 마지막 절 `## 원래 이름 (옮겨 적기 전에 확인)`에 `### 창 클래스`, `### 모듈`로 나열.

- [ ] **Step 1: 실패하는 시험 작성**

```python
def test_block_first_line_and_four_per_line(): ...  # 첫 줄 정확히, 둘째 줄에 가설 4개
def test_block_never_exceeds_ten_lines_with_many_hypotheses(monkeypatch): ...  # 가설 60개로 늘려도 10줄
def test_closed_hypotheses_leave_the_block_but_stay_in_the_markdown(monkeypatch): ...
def test_names_appear_only_in_their_section():
    md = result_markdown(header, judged, NamesForReport(("TfrmSecret",), ("사내모듈.dll",)))
    head, tail = md.split("## 원래 이름 (옮겨 적기 전에 확인)")
    assert "TfrmSecret" not in head and "TfrmSecret" in tail and "사내모듈.dll" in tail
def test_without_show_names_there_is_no_names_section(): ...
def test_changed_files_listed_in_markdown_only(): ...  # 블록에는 "변경있음 2"만
def test_committed_hypotheses_doc_is_current():
    assert (HERE / HYPOTHESES_DOC).read_text(encoding="utf-8") == hypotheses_markdown()
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**, 그리고 `docs/company-survey/hypotheses.md`를 `hypotheses_markdown()`으로 생성(작업 9의 `--write-hypotheses`가 생기기 전에는 한 줄 Python으로). 머리에 "이 파일은 `python survey.py --write-hypotheses`로 만듭니다. 손으로 고치지 마세요."
- [ ] **Step 4: 통과 확인**

### 작업 6: 커밋과 코드 변경 (`agent/git_state.py`)

**Files:** Create `agent/git_state.py`, `tests/test_git_state.py`

**Interfaces:**
- Produces: `code_state(folder: Path, runner=subprocess.run) -> CodeState`, `CodeState = NamedTuple(head: str | None, changed: tuple[str, ...])`. head는 `git rev-parse --short=7 HEAD`, changed는 `git -c core.fileMode=false diff --name-only HEAD -- .`(cwd=folder)의 줄, 폴더 기준 상대 경로, 정렬. git이 없거나 저장소가 아니면 `CodeState(None, ())`.

- [ ] **Step 1: 실패하는 시험 작성** (임시 폴더에 `git init`, 사용자 이름·메일은 `-c`로)

```python
def test_clean_repo(tmp_repo): assert code_state(tmp_repo.sub) == CodeState(tmp_repo.head7, ())
def test_edited_tracked_file_counts(tmp_repo): ...
def test_untracked_file_does_not_count(tmp_repo): ...
def test_change_outside_the_folder_does_not_count(tmp_repo): ...
def test_line_ending_only_change_does_not_count(tmp_repo):  # core.autocrlf=true, LF로 커밋 후 CRLF로 다시 씀
    ...
def test_no_git_gives_unknown(tmp_path): assert code_state(tmp_path, runner=raises_oserror) == CodeState(None, ())
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 7: 대상 창 고르기 (`agent/survey_target.py`, `agent/ready.py`)

**Files:** Create `agent/survey_target.py`, `tests/test_survey_target.py`; Modify `agent/ready.py`(공개 함수 하나 추가)

**Interfaces:**
- Consumes: `ready.wait_until_ready`, `ready.signals`, `check.Target`(exe, shortcut), `win32_facts.process_image`
- Produces:
  - `agent/ready.py`: `windows_showing(desktop, rows, wanted) -> list[dict]`: `rows` 중 신호가 모두 보이는 창(`_snapshot` + `_shows_all`과 같은 규칙, 수를 세지 않음, 최대 `RUNNING_HINT_MAX_WINDOWS`개 읽음). `_running_hint`는 이것을 쓰도록 바꿉니다.
  - `Chosen = NamedTuple(window: dict | None, launch: str, code: str | None, pid: int | None, instance: str | None, ready: ReadyOutcome | None)`. `window`가 None이면 `code`가 앱을 읽지 못한 이유(`ATTACH_AMBIGUOUS`, `LAUNCHER_UNRESOLVED`, `READY_<STATUS>`, `launch <CODE>`, `EXE_MISSING`, `SHORTCUT_*`).
  - `choose_window(desktop, target, wanted, image_of, timing) -> Chosen`; `timing = Timing(clock, sleep, timeout_s, window_wait_s=15, poll_s=0.5)`.

규칙은 설계 3장 "대상 창 고르기" 1–5. 신호 없는 붙기의 비교는 `os.path.normcase(os.path.abspath(...))`로 실행 파일 경로(바로가기면 `target.shortcut`의 대상)와 `image_of(row["pid"])`를 맞춤. 띄웠을 때 `launch` 코드: 신호가 있고 `outcome.switched`면 `LAUNCHER`, 아니면 바로가기가 아니면 `EXE`, 바로가기면 `LNK_EXE`. 바로가기 오류가 `SHORTCUT_NOT_EXE`·`SHORTCUT_NO_TARGET`이면 `launch="LNK_NOT_EXE"`이고 window None.

- [ ] **Step 1: 실패하는 시험 작성** (작은 가짜 desktop: `run(*argv) -> envelope`, 부른 명령을 기록)

```python
def test_one_running_window_is_attached_without_launch(): ...   # launch="ATTACHED", "launch" 명령 없음
def test_two_running_windows_stop_ambiguous(): ...              # code="ATTACH_AMBIGUOUS", launch 없음
def test_signals_pick_the_window_that_shows_them(): ...         # windows_showing 경유
def test_nothing_running_launches_and_waits_with_signals(): ... # wait_until_ready 경유, switched → LAUNCHER
def test_no_signal_launch_without_own_window_never_picks_another(): ...  # 다른 pid 창이 새로 떠도 LAUNCHER_UNRESOLVED
def test_shortcut_not_exe_is_reported_without_launch(): ...
def test_image_comparison_ignores_case_and_slashes(): ...
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인. `tests/test_ready.py` 전체가 그대로 통과해야 합니다.

### 작업 8: 오버레이 (`agent/overlay.py`)

**Files:** Create `agent/overlay.py`, `tests/test_overlay.py`

**Interfaces:**
- Produces: `LEGEND = (("named_ref", "파랑", (0,120,255)), ("unnamed_ref", "보라", (160,0,200)), ("text", "초록", (0,170,0)), ("blank", "빨강", (230,0,0)), ("id_only", "주황", (255,140,0)))`; `draw_overlay(screenshot: Path, window_bounds: dict, tree: dict, out: Path) -> int`(그린 박스 수). 종류는 `agent.survey`의 `_kind`/`_id_only`와 같은 규칙(그 둘을 공개 함수 `node_kind(node) -> str | None`, `is_id_only(node) -> bool`로 내어 씀). 좌표는 `agent.frames.frame_origin`과 `to_image_rect`. 오른쪽 아래에 범례(색 사각형과 한국어 색 이름 + 종류 설명). 박스에 글자는 쓰지 않습니다.

- [ ] **Step 1: 실패하는 시험 작성**

```python
def test_each_kind_is_drawn_in_its_color(tmp_path): ...   # 흰 PNG, 종류마다 노드 하나 → 각 박스 테두리 픽셀이 LEGEND 색, 반환 5
def test_node_outside_the_capture_is_skipped(tmp_path): ...
def test_survey_kinds_and_overlay_agree(): ...            # 같은 트리에서 kinds()의 수 == 종류별 그린 수
```

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인. `tests/test_survey.py`(기존, `agent/survey.py`)가 그대로 통과해야 합니다.

### 작업 9: 진입점 `survey.py`

**Files:** Create `survey.py`, `tests/test_survey_cli.py`; Modify `.gitignore`(이 폴더, `/survey-results/` 추가), `tests/test_application_agnostic.py`(`PYTHON_SOURCES`에 `survey.py`)

**Interfaces:**
- Consumes: 작업 1–8 전부, `check.run_checks`, `check.check_settings`, `check.pick_target`, `check._with_shortcut`(이름을 `with_shortcut`으로 공개하고 `check.py` 안의 호출도 바꿈), `check.Deps`, `checks.load_ids/save_ids/ids_path/process_mark`, `agent.survey.kinds/id_keys`, `agent.desktop.DesktopClient/start_session`, `agent.handles.process_window_handles`
- Produces: `main(argv=None, deps: SurveyDeps | None = None) -> int`; `SurveyDeps`(dataclass): `check: check.Deps`, `win32`(작업 2의 함수를 속성으로 가진 객체, 기본은 모듈 `agent.win32_facts`), `uia_runner=subprocess.run`, `git_runner=subprocess.run`, `results_dir: Path = HERE / "survey-results"`, `now: Callable[[], datetime] = datetime.now`.

인자: `--exe`, `--system`, `--settings`, `--profile`, `--profile-dir`(뜻은 `check.py`와 같음), `--skip llm`(LLM 점검만 건너뜀; 앱은 건너뛸 수 없음), `--show-names`, `--write-hypotheses`(가설 문서만 쓰고 0으로 끝남, 앱·LLM 없음).

흐름: env 읽기(`load_env`) → `check.run_checks(SimpleNamespace(..., skip=["app", *args.skip]), deps.check)` → 설정·대상(`pick_target`, `with_shortcut`) → 세션과 `DesktopClient`(실제 실행일 때만 `window_handles`, `read_shortcut`을 넘김) → `choose_window` → 창이 있으면: 스냅샷(8000ms), 스크린샷(결과 폴더의 `window.png`), `kinds`/`id_keys`, ID 기록(H05: `checks`의 저장소와 규칙; 이전 없음·같은 프로세스 → `PENDING`, 키 없음 → `NO_IDS`, 같음 → `STABLE`, 다름 → `CHANGES`; 같은 프로세스면 저장하지 않음), `process_tree(pid)`로 창 클래스·모듈(시스템 모듈은 계열 판별에만), `count_uia(hwnd)`, `monitor_of(hwnd)`, `draw_overlay` → `SurveyFacts` → `judge_all` → `code_state(HERE)` → 결과 폴더에 `survey.md`, `facts.json`(SurveyFacts를 JSON으로, `unknown_names` 제외), `overlay.png`, `window.png` → 콘솔에 점검 줄(`checks.line`)과 전달 블록, 결과 폴더 이름(시각-시스템, 절대 경로 아님). 종료 코드: 전달 블록을 찍었으면 0, 인자·env 오류 2.

시스템 이름: `--system`이 있으면 그것, 없으면 `knowledge.system_name(settings의 system, exe)`. 결과 폴더 이름의 시스템 부분은 파일 이름에 못 쓰는 글자를 `_`로.

- [ ] **Step 1: 실패하는 시험 작성** (`tests/test_check.py`의 `CheckCli`와 `FakeModel`을 본떠 가짜 CLI·모델, 가짜 win32 객체·UIA runner·git runner. 가짜 값 곳곳에 표지 `SECRET_NAME`, `TfrmSecretScreen`, `CompanySecret.dll`, `C:\\secret\\path`를 넣음)

```python
def test_one_run_writes_markdown_overlay_and_facts_and_prints_the_block(env, capsys): ...
def test_console_block_and_default_files_hold_no_marker(env, capsys):
    main([...], deps)
    for text in (capsys.readouterr().out, md_text(), facts_text()):
        for marker in MARKERS: assert marker not in text
def test_show_names_puts_names_only_in_the_markdown_section(env, capsys): ...
def test_only_read_commands_reach_the_cli(env): assert set(cli.commands) <= {"session", "launch", "list-windows", "snapshot", "find", "screenshot", "version"}
def test_uia_failure_skips_h04_only(env, capsys): ...         # 블록에 "H04 SKIPPED(UIA_TIMEOUT)", H01은 판정
def test_attach_ambiguous_still_prints_launch_and_llm(env, capsys): ...
def test_two_runs_in_the_same_second_use_two_folders(env): ...
def test_write_hypotheses_writes_the_doc_and_touches_nothing_else(tmp_path): ...
def test_ids_pending_then_stable_after_a_new_process(env): ...
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현.** 파일 끝은 `check.py`처럼 `sys.stdout.reconfigure(errors="replace")`.
- [ ] **Step 4: 통과 확인** — `pytest tests/test_survey_cli.py tests/test_check.py tests/test_application_agnostic.py`

### 작업 10: 회사 Claude Code 실행 전용 장치 (`company/`)

**Files:** Create `company/CLAUDE.md`, `company/.claude/settings.json`, `company/hooks/guard.py`, `tests/test_company_guard.py`; Modify 저장소 루트 `.gitignore`

**Interfaces:**
- Produces: `guard.py`에 `decide(payload: dict, repo_root: Path, company_dir: Path, is_ignored: Callable[[Path], bool]) -> str | None`(막을 이유 또는 None)와 `main() -> int`(stdin JSON을 읽어 막으면 이유를 stderr에 쓰고 2, 아니면 0; 입력을 해석하지 못하거나 예상 밖 오류면 2). `repo_root`는 `git rev-parse --show-toplevel`(cwd=`company_dir`), `is_ignored`는 `git check-ignore -q <path>`의 종료 코드 0. 표준 라이브러리만.

규칙(설계 5장):
- `tool_name`이 `Edit`·`Write`·`NotebookEdit`·`MultiEdit`: `tool_input.file_path`(NotebookEdit는 `notebook_path`)를 절대 경로로. `company_dir` 아래면 막음. `repo_root` 아래이고 `is_ignored`가 아니면 막음. 그 밖은 허용.
- `Bash`: `tool_input.command`를 `;`, `&&`, `||`, `|`, 줄바꿈으로 나눈 조각마다 앞의 환경 대입을 빼고 검사. `git <commit|push|checkout|switch|reset|restore|stash|apply|am|cherry-pick|rebase|merge|clean|rm|mv|add|config|update-index|worktree>`(앞에 `-C <dir>`·`-c <k=v>`가 있어도), `pip install`, `python -m pip install`, `conda install`, `npm install`, `cargo` → 막음. `git pull`, `status`, `log`, `diff`, `show`, `fetch`는 허용.
- 다른 도구는 허용.

`settings.json`:
```json
{
  "hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
    "hooks": [{"type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/hooks/guard.py\""}]}]},
  "permissions": {"deny": ["Bash(git commit:*)", "Bash(git push:*)", "Bash(git checkout:*)", "Bash(git reset:*)",
    "Bash(git stash:*)", "Bash(git add:*)", "Bash(git restore:*)", "Bash(pip install:*)", "Bash(cargo:*)"]}
}
```

`CLAUDE.md`: 설계 5장의 항목 그대로(첫 줄 우선 선언, 역할, 해도 되는 것, 하면 안 되는 것, 문제를 찾았을 때 `survey-results/notes-<날짜>.md`, 결과 문서 도움, 전달 블록은 그대로). 회사에서 할 순서(`git pull` → `python ..\survey.py --system <이름>` → md 채우기 → 블록 옮겨 적기)와 이 폴더에서 `claude`를 시작하라는 안내, 첫 실행 때 훅이 도는지 확인하는 방법(추적 파일 하나에 Edit를 시켜 막히는지)을 넣습니다.

루트 `.gitignore`의 `.claude/` 줄 다음에:
```
!examples/llm-agent/company/.claude/
!examples/llm-agent/company/.claude/settings.json
```

- [ ] **Step 1: 실패하는 시험 작성** (`guard.py`를 `importlib.util.spec_from_file_location`으로 읽음)

```python
@pytest.mark.parametrize("path, allowed", [("agent/loop.py", False), ("runs/x/run.json", True), ("company/hooks/guard.py", False),
                                            ("company/CLAUDE.md", False), ("company/notes.md", False)])
def test_edit_rules(path, allowed, repo): ...
def test_outside_repo_is_allowed(repo, tmp_path): ...
@pytest.mark.parametrize("command", ["git commit -m x", "git -C .. push", "cd .. && git checkout main", "FOO=1 git reset --hard",
                                     "pip install x", "python -m pip install x", "cargo build", "git -c a=b add ."])
def test_blocked_commands(command): assert decide(bash(command), ...) is not None
@pytest.mark.parametrize("command", ["git pull", "git status", "git log -3", "python ..\\survey.py --system A", "git diff"])
def test_allowed_commands(command): assert decide(bash(command), ...) is None
def test_broken_input_blocks(monkeypatch, capsys): ...  # stdin "not json" → main() == 2
def test_settings_file_is_tracked_by_git():
    out = subprocess.run(["git", "ls-files", "--error-unmatch", "company/.claude/settings.json"], cwd=HERE, capture_output=True)
    assert out.returncode == 0
```

마지막 시험은 파일을 `git add` 하기 전에는 실패합니다. 작업자는 커밋하지 않으므로, 이 시험은 `git check-ignore`가 1(제외되지 않음)인지로 확인하고 이름을 `test_settings_file_is_not_ignored`로 합니다.

- [ ] **Step 2–4:** 실패 확인 → 구현 → 통과 확인

### 작업 11: 문서와 통합 확인

**Files:** Modify `README.md`(이 폴더, "새 시스템 조사" 절), 저장소 루트 `CLAUDE.md`의 `examples/llm-agent` 명령 목록(`python survey.py ...` 한 줄과 "회사 GLM은 `company/`에서" 한 줄), `docs/cua-background.md`의 "집–회사 제약" 끝(조사 도구가 있다는 한 문장), 설계 문서 상태 줄

- [ ] **Step 1:** README 절: 실행 명령, 결과 폴더의 네 파일, 전달 블록을 옮겨 적는 법, `--show-names`의 뜻(옮겨 적기 전에 확인), 회사 Claude Code는 `company/`에서 시작.
- [ ] **Step 2:** 전체 `pytest` → 실패 0
- [ ] **Step 3:** `python survey.py --write-hypotheses` 후 `git diff --stat docs/company-survey/hypotheses.md`가 비어 있음
- [ ] **Step 4:** 실제 창 확인은 사용자에게 확인받은 뒤(가상 MES 복사본으로 H01 `WINFORMS`, H07, H09가 맞는지, `overlay.png` 눈 확인). 작업자는 하지 않습니다.
