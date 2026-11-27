# 화면 구조 1·2단계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 깊이로 잘리던 표의 칸까지 읽고, 모델이 영역·줄·표의 요약을 보고 `open_area`, `read_table`, `find_rows`로 스스로 찾아 누르게 한다. 3단계(화면 지도)는 회사 확인 뒤 따로 계획한다.

**Architecture:** 트리 읽기는 새 모듈 `agent/deep_read.py` 하나로 모아 하니스·등록 화면·`check.py`·캡처 세션이 같은 깊이와 대체 규칙을 쓴다. 구조 계산은 새 순수 모듈 `agent/structure.py`가 맡고, `observe.py`의 번호 매기기를 그대로 받아 번호를 바꾸지 않는다. 도구는 그 구조를 읽기만 하고, workflow는 칸을 `find_rows`의 조건으로 적는다.

**Tech Stack:** Python 3(pytest, 캡처한 CLI 응답 재생 `tests/fake_cli.py`), C# 5 WinForms(가상 MES)

**Spec:** `examples/llm-agent/docs/specs/2026-11-27-screen-structure-design.md`

## Global Constraints

- 모든 경로는 `examples/llm-agent/` 기준. 시험은 거기서 `/c/Users/sihoo/anaconda3/envs/AgentDesktop/python.exe -X utf8 -m pytest -q -p no:cacheprovider`.
- `agent/`와 `viewer/`에 앱 이름이나 화면 글을 넣지 않는다(`tests/test_application_agnostic.py`). 구조는 역할, 박스, 이름의 모양, 값으로만 판단한다.
- 화면 글, 이름, id, 값은 `events.jsonl`과 관찰 글에만. `run.json`, 요약, `check.py` 출력, 전달 블록에는 수와 코드만.
- 번호(`[n]`)는 바뀌지 않는다: `render_marked`, `visible_elements`, `RunRecorder`가 같은 번호를 낸다.
- 깊이 상수 `DEEP_DEPTH = 30`. 깊게 읽기 실패 시 기본 깊이로 한 번 다시 읽고 코드 `deep_read_too_large`(CLI `INTERNAL`이면서 메시지에 `RefMap exceeds`) 또는 `deep_read_timeout`(CLI `TIMEOUT`)을 남긴다.
- 준비 확인(`agent/ready.py`)의 읽기는 바꾸지 않는다.
- 숫자 열 판단은 `agent/table.py`의 `NUMERIC`을 쓴다.
- 새 도구는 레벨 0, `READ_ONLY_TOOLS`. `expand`라는 이름은 이미 있으므로 `open_area`.
- 가상 MES는 C# 5(문자열 보간, `nameof`, `?.` 금지). 새 옵션은 기본 꺼짐.
- 작업자는 커밋하지 않는다. 커밋과 push는 사용자가 요청할 때 오케스트레이터가 한다(Conventional Commits). 각 작업의 "Commit" 단계는 "상태 파일에 끝낸 작업을 적기"로 읽는다.
- 실제 창 시험(`VIRTUAL_MES_LIVE=1`)은 작업자가 돌리지 않는다. 마지막 작업에서 오케스트레이터가 사용자 확인 뒤 돌린다.

## Review Focus

- 표가 없는 화면(메뉴, 대화상자)에서 구조 요약이 빈 줄이나 쓸모없는 이정표로 관찰 글을 늘리지 않는다 → Task 5 `test_a_screen_without_tables_gets_no_table_lines`, Task 6 `test_observation_text_of_a_tableless_window_is_unchanged_but_for_areas`.
- 같은 조건에 맞는 행이 둘 이상이면 어느 칸도 묻지 않고 눌리지 않는다(화면 밖 후보 포함) → Task 7 `test_find_rows_with_two_matches_blocks_both_cells_until_asked`, `test_an_offscreen_candidate_blocks_the_whole_table`.
- 칸 값이 비어 있는 행(병합된 칸, 소계 행)이 있어도 `find_rows`가 빈 값을 다른 값으로 채우지 않는다 → Task 5 `test_blank_cells_stay_blank`, Task 7 `test_find_rows_does_not_fill_blank_labels_down`.
- 깊게 읽기가 실패해도 관찰은 이어지고 그 사실이 기록된다 → Task 2 `test_a_refmap_too_large_falls_back_to_the_default_depth`, Task 3 `test_a_fallback_is_written_as_a_deep_read_event_and_counted`.
- 숫자 열은 행 열쇠 후보가 되지 않는다(매일 바뀌는 값) → Task 5 `test_numeric_columns_are_never_key_candidates`.

---

### Task 1: `mouse_click`을 모델 도구에서 빼기

**Files:**
- Modify: `agent/tool_schemas.py` (`HIDDEN_BY_CLICK_PART`)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: `definitions_for(level, vision)`는 `level >= CLICK_PART_LEVEL`에서 `click`과 `mouse_click`을 내지 않는다. workflow 재실행(`Toolbox`의 `_tool_mouse_click`, `RAW_POINT_TOOLS`)은 그대로.

- [ ] **Step 1: 실패하는 시험**

```python
def test_mouse_click_is_hidden_where_click_part_is_offered():
    names = [d["name"] for d in definitions_for(CLICK_PART_LEVEL, vision=False)]
    assert "click_part" in names and "mouse_click" not in names and "click" not in names

def test_a_saved_mouse_click_step_still_replays(tmp_path):
    # ungated Toolbox(level 2): call("mouse_click", {...}) sends mouse-click to the CLI
```

- [ ] **Step 2:** 돌려서 첫 시험이 실패하는지 본다.
- [ ] **Step 3:** `HIDDEN_BY_CLICK_PART = frozenset({"click", "mouse_click"})`, 독스트링에 이유 한 줄(모델이 지어낸 화면 좌표; `click_part`와 `click_point`가 대신함).
- [ ] **Step 4:** 전체 pytest 통과.
- [ ] **Step 5:** 커밋 `feat: hide mouse_click from the model where click_part is offered`.

---

### Task 2: `agent/deep_read.py` — 깊게 읽기와 대체

**Files:**
- Create: `agent/deep_read.py`
- Test: `tests/test_deep_read.py`

**Interfaces:**
- Produces:
  - `DEEP_DEPTH = 30`, `TOO_LARGE = "deep_read_too_large"`, `TIMED_OUT = "deep_read_timeout"`
  - `@dataclass(frozen=True) class WindowRead: envelope: dict; depth: int | None; fallback: str | None; cut: int` — `depth`는 쓴 `--max-depth`(대체 뒤에는 `None` = CLI 기본), `cut`은 트리에서 `children_count`가 있는 노드 수(실패한 읽기는 0)
  - `read_window(desktop, window_id: str, timeout_ms: str, extra: tuple = (), include_bounds: bool = True, depth: int = DEEP_DEPTH) -> WindowRead` — `snapshot --window-id <id> --compact --timeout-ms <t> --max-depth <depth> [*extra] [--include-bounds]`를 보내고, 실패가 위 두 경우면 `--max-depth` 없이 한 번 더 보낸다. 다른 실패는 대체하지 않고 그대로 돌려준다.
  - `count_cut(tree: dict) -> int`

- [ ] **Step 1: 실패하는 시험**

```python
def test_reads_at_depth_30_first(): ...               # fake desktop records args; "--max-depth","30" present; depth == 30
def test_a_refmap_too_large_falls_back_to_the_default_depth(): ...
    # first reply INTERNAL "RefMap exceeds 1MB size limit on write" → second call has no --max-depth
    # read.fallback == "deep_read_too_large", read.depth is None, read.envelope is the second reply
def test_a_timeout_falls_back(): ...                  # TIMEOUT → fallback "deep_read_timeout"
def test_other_failures_are_returned_without_a_second_read(): ...   # WINDOW_NOT_FOUND → one call
def test_count_cut_counts_nodes_with_children_count(): ...
def test_extra_args_reach_the_cli(): ...              # extra=("--root","@s:e3") kept in both calls
```

- [ ] **Step 2:** 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** 통과.
- [ ] **Step 5:** 커밋 `feat: read windows deep and fall back when the cli refuses the size`.

---

### Task 3: 하니스·등록 화면·`check.py`가 깊게 읽기

**Files:**
- Modify: `agent/tools.py` (`_snapshot`, `observed_trees`, 새 `_deep_read_event`), `agent/recorder.py` (`KEPT_EVENTS`에 `"deep_read"`), `agent/summary.py`, `agent/loop.py`(시스템 안내 한 줄), `agent/settings.py` (`KEYS`, `NUMBER_KEYS`에 `observe_deep_depth`, 10~50만 받음), `agent/task.py` (`observe_deep_depth: int = 30`, `DEFAULT_KEYS`에 추가 — `image_mode`와 같은 길), `viewer/profile_editor.py:114`, `check.py:559`
- Test: `tests/test_tools.py`, `tests/test_summary.py`, `tests/test_profile_editor.py`(또는 기존 등록 화면 시험 파일), `tests/test_check.py`

**Interfaces:**
- Consumes: Task 2 `read_window`, `WindowRead`
- Produces:
  - `Toolbox._snapshot(window_id, root_ref=None, surface=None) -> dict`는 `read_window(..., extra=root/surface 인자, depth=task.observe_deep_depth)`를 쓰고 envelope를 돌려준다. 등록 화면, `check.py`, 캡처 세션은 `DEEP_DEPTH`를 쓴다. `fallback`이 있거나 `cut > 0`이면 `_emit({"type": "deep_read", "window_id", "depth", "fallback", "cut"})`.
  - 요약 줄 `  깊이: 얕게 <n>회 · 잘림 <m>` — `deep_read` 이벤트가 하나라도 있을 때만, 수만.
  - 시스템 안내(`loop.py` instructions)에 한 줄: `children=N on a line means N children were not read.`

- [ ] **Step 1: 실패하는 시험**

```python
def test_every_observation_asks_the_cli_for_depth_30(tmp_path): ...       # fake_cli: snapshot args contain --max-depth 30
def test_a_fallback_is_written_as_a_deep_read_event_and_counted(tmp_path): ...
def test_a_deep_read_with_nothing_cut_writes_no_event(tmp_path): ...
def test_summary_shows_shallow_reads_and_cuts_as_counts_only(): ...      # "  깊이: 얕게 1회 · 잘림 3", no names
def test_profile_editor_reads_deep(): ...
def test_check_app_observation_reads_deep(): ...
def test_ready_wait_snapshot_keeps_the_default_depth(): ...              # agent/ready.py args unchanged
def test_observe_deep_depth_setting_reaches_the_snapshot(tmp_path): ...  # settings 20 → --max-depth 20
def test_observe_deep_depth_outside_10_to_50_is_refused(): ...
```

- [ ] **Step 2:** 실패 확인.
- [ ] **Step 3:** 구현. `fake_cli.py`가 `--max-depth`를 받아들이는지 확인하고 필요하면 무시하도록 고친다.
- [ ] **Step 4:** 전체 pytest 통과(기존 시험의 기대 인자에 `--max-depth 30`이 늘어나는 것은 고친다).
- [ ] **Step 5:** 커밋 `feat: observe every window at depth 30 and record when it could not`.

---

### Task 4: 캡처 세션의 깊게 읽기와 블록 v3

**Files:**
- Modify: `agent/survey_capture.py:220` (`_read_window`), `agent/survey_facts.py` (`WindowFact`에 `depth: int | None = None`, `cut: int | None = None`, `shallow: str | None = None`), `agent/hypothesis_rules.py` (`judge_h11`), `agent/survey_codes.py` (`SURVEY_VERSION = 3`, `MEANINGS`에 대체 코드 두 개), `agent/survey_block.py` (`_explain_body` 버전 판단)
- Test: `tests/test_survey_capture.py`, `tests/test_hypothesis_rules.py`, `tests/test_survey_block.py`, `tests/test_survey_end_to_end.py`

**Interfaces:**
- Consumes: Task 2 `read_window`
- Produces: H11 값의 괄호 안 끝에 `c<잘림 수>`, 얕게 돌아갔으면 `S`를 붙인다. 예 `COMPLETE(2.5s/c0)`, `COMPLETE(3.1s/c2/S)`. `--read-block`은 v2와 v3을 모두 오타 없이 읽고, v2에는 버전 경고를 내지 않는다(이 변경은 뜻을 바꾸지 않고 덧붙이기만 한다).

- [ ] **Step 1: 실패하는 시험**

```python
def test_capture_reads_the_window_at_depth_30_and_keeps_the_cut_count(): ...
def test_h11_detail_carries_the_cut_count(): ...        # read OK, read_s 2.5, cut 0 → detail "2.5s/c0"
def test_h11_detail_marks_a_shallow_fallback(): ...     # shallow "deep_read_too_large" → ends with "/S"
def test_read_block_accepts_a_v2_block_without_a_version_warning(): ...
def test_old_facts_without_depth_fields_still_load(): ...   # facts.json from v2 sessions
```

- [ ] **Step 2:** 실패 확인. **Step 3:** 구현. **Step 4:** 전체 pytest 통과(끝에서 끝 시험의 블록 기대값 갱신).
- [ ] **Step 5:** 커밋 `feat: capture sessions read deep and count what the depth still cut`.

---

### Task 5: `agent/structure.py` — 영역, 줄, 표

**Files:**
- Create: `agent/structure.py`
- Modify: `agent/observe.py` (공개 함수 `numbered_nodes` 추가, 기존 `_items` 재사용)
- Test: `tests/test_structure.py`

**Interfaces:**
- Consumes: `observe._items`, `identity.as_container`, `locator.overlaps`/`locator.box`, `table.NUMERIC`
- Produces (모두 frozen dataclass):
  - `observe.numbered_nodes(tree, profile=None) -> list[tuple[dict, int | None, dict | None]]` — (노드, 번호, 담긴 영역) 트리 순서. `render_marked`와 같은 번호.
  - `Cell(mark: int, value: str | None, offscreen: bool)` — 값은 `value`, 없으면 이름. 박스가 없거나 상태에 `offscreen`이면 `offscreen=True`.
  - `Column(label: str, rule: str)` — `rule`은 `"header"`, `"pattern"`, `"index"`.
  - `Row(index: int, cells: tuple[Cell | None, ...])` — `cells[i]`는 `columns[i]`의 칸. 0부터 센다.
  - `Table(handle: str, mark: int | None, role: str, native_id: str | None, area: dict | None, columns: tuple[Column, ...], rows: tuple[Row, ...], key_candidates: tuple[str, ...])`
  - `Area(handle: str, container: dict, lines: tuple[tuple[int, ...], ...], tables: tuple[str, ...])` — 줄마다 번호를 왼→오.
  - `Structure(areas: tuple[Area, ...], tables: tuple[Table, ...], cell_marks: frozenset[int])` with `table(handle) -> Table | None`, `area(handle) -> Area | None`
  - `analyze(tree: dict, profile=None) -> Structure`
  - `table_identity(table: Table, use_ids: bool) -> dict` — `use_ids`이고 `native_id`가 있으면 `{"role", "id", "in"?}`, 아니면 `{"role", "in"?, "columns": [labels]}`
  - `record(structure: Structure) -> dict` — 이벤트용: 영역 핸들과 영역, 표마다 핸들·신원(`use_ids=False` 모양)·열 이름·행 수·열쇠 후보
- 규칙(사양 그대로, 구현자가 고를 수 없는 것만):
  - 표: 역할 `table`이나 `grid`인 노드, 또는 역할 `cell`인 요소를 두 행 이상 담은 노드(가장 안쪽). 행: 표 아래에서 칸을 직접 자식으로 둔 노드, 없으면 칸 박스의 세로 겹침(`overlaps(..., "y")`)으로 묶음.
  - 열 배정: 가장 칸이 많은 행의 순서가 기준. 칸이 적은 행은 기준 칸과 가로로 겹치는 열에 넣는다.
  - 열 이름(열마다): 머리글 요소(표 안, 칸이 아닌 요소, 기준 칸과 가로 겹침, 칸들보다 위) → 칸 이름의 숫자 무리를 `#`로 바꾼 모양이 그 열 모든 칸에서 같으면 그것 → `열<i+1>`.
  - 열쇠 후보: 모든 값이 `NUMERIC`이거나 빈 열은 뺀다. 남은 열 가운데 빈칸이 없고 값이 겹치지 않는 열들, 없으면 그런 두 열의 조합 하나(`"공정+구분"` 모양), 없으면 빈 튜플.
  - 영역: `as_container`가 있는 노드 가운데 요소를 직접 담거나 영역을 둘 이상 담은 것, 단 표 안의 노드는 제외. 핸들은 트리 순서로 `A1`부터, 표는 `T1`부터.
  - 줄: 영역이 직접 담은 요소(칸과 표 안 요소 제외)를 세로 겹침으로 묶어 위→아래, 각 줄은 왼→오.

- [ ] **Step 1: 실패하는 시험** (트리는 손으로 만든 dict, `tests/fixtures/`에 회사 모양 트리 하나 추가: 창 > 감싸개 7겹 > 표 > `Data Panel` > `Row 1..3` > `양품수 row0` 등 값 있는 칸)

```python
def test_numbering_is_the_same_as_render_marked(): ...
def test_company_shaped_table_has_rows_columns_and_values(): ...
    # columns labels ["공정 row#", "구분 row#", "양품수 row#"], rule "pattern"; rows[1].cells[2].value == "980"
def test_header_labels_win_per_column(): ...      # header for 2 of 3 columns → third uses pattern
def test_blank_cells_stay_blank(): ...
def test_numeric_columns_are_never_key_candidates(): ...
def test_key_candidates_fall_back_to_a_pair(): ...   # 공정 repeats, 구분 repeats, pair unique → ("공정 row#+구분 row#",)
def test_offscreen_cells_count_as_rows(): ...
def test_rows_inside_a_table_are_not_areas(): ...
def test_wrapper_chain_collapses_to_one_area(): ...
def test_lines_are_left_to_right(): ...            # three boxes under a label given in reverse tree order
def test_a_screen_without_tables_gets_no_table_lines(): ...   # menu tree → tables == ()
def test_table_identity_without_an_id_uses_area_role_and_columns(): ...
```

- [ ] **Step 2:** 실패 확인. **Step 3:** 구현. **Step 4:** 통과 + 전체 pytest.
- [ ] **Step 5:** 커밋 `feat: work out areas, lines and tables from a snapshot tree`.

---

### Task 6: 관찰 글에 구조 요약, 표 칸 줄 숨기기

**Files:**
- Modify: `agent/observe.py` (`render_marked(tree, include_bounds=False, profile=None, structure=None)`), `agent/structure.py` (`render_summary(structure) -> str`), `agent/tools.py` (`_record_read`, `observed_trees`가 `analyze` 후 넘기고 `self._structure` 보관; `_tool_wait_for_text`), `agent/recorder.py` (`on_observation`이 `event["structure"] = record(analyze(tree, profile))`)
- Test: `tests/test_observe.py`, `tests/test_structure.py`, `tests/test_tools.py`, `tests/test_recorder.py`

**Interfaces:**
- Consumes: Task 5 `analyze`, `record`, `Structure.cell_marks`
- Produces:
  - `render_marked(..., structure)`: `structure`가 있으면 글 맨 앞에 `render_summary`를 두고, `structure.cell_marks`에 든 번호의 줄은 쓰지 않는다. 번호와 돌려주는 `marks`는 그대로(숨긴 칸도 `marks`에 있다).
  - `render_summary` 모양(사양 예시 그대로):

```
구조
  A1 영역 id="pn_Filter"
    줄1: [20] "Shop" → [21] · [22] · [23]
  A2 영역 id="pn_Main"
    T1 표 [40]  열 12개 · 행 40개 · 값 있음 · 행을 가르는 열: 공정 row#+구분 row#
      열: 공정 row# | 구분 row# | 양품수 row# | …
```

    줄의 요소는 이름이 있으면 `[n] "이름"`, 없으면 `[n]`. 영역은 `id="…"` 또는 `"이름"`. 표가 없는 영역에 줄이 하나도 없으면 영역 줄도 쓰지 않는다. 구조가 비면(영역·표 없음) 요약 전체를 쓰지 않는다.
  - `wait_for_text`: 관찰 글 또는 `self._structure`의 칸 값 가운데 하나라도 글자를 담으면 찾은 것.

- [ ] **Step 1: 실패하는 시험**

```python
def test_cells_of_a_table_have_no_lines_but_keep_their_marks(): ...
def test_summary_comes_first_and_matches_the_spec_shape(): ...
def test_observation_text_of_a_tableless_window_is_unchanged_but_for_areas(): ...
def test_wait_for_text_finds_a_value_only_a_hidden_cell_holds(tmp_path): ...
def test_recorder_observation_event_carries_the_structure_record(tmp_path): ...
def test_recorder_and_toolbox_marks_still_agree(tmp_path): ...   # existing invariant test extended to a table tree
```

- [ ] **Step 2–4:** 실패 확인, 구현, 전체 pytest(관찰 글 기대값이 바뀐 시험은 표 없는 화면이면 그대로여야 한다. 바뀌었다면 규칙 위반).
- [ ] **Step 5:** 커밋 `feat: show the screen structure first and leave table cells to the table tools`.

---

### Task 7: `open_area`, `read_table`, `find_rows`

**Files:**
- Modify: `agent/structure.py` (`render_area`, `render_table`, `find_rows`), `agent/screen_tools.py` (`_tool_open_area`, `_tool_read_table`, `_tool_find_rows`), `agent/asking.py` (`_note_cells`가 화면 밖 후보를 받으면 그 표 전체를 막음), `agent/policy.py` (막을 사각형 = 표 박스), `agent/tool_schemas.py` (세 정의, 레벨 0, `READ_ONLY_TOOLS`), `agent/recorder.py` (`KEPT_EVENTS`에 `"find_rows"`), `agent/summary.py` (`  표 찾기: <n>회 · 없음 <a> · 여럿 <b>` — `find_rows` 이벤트가 있을 때만, 수만), `agent/loop.py` (시스템 안내: 표가 `T`로 보이면 `find_rows`, 아니면 `find_cells`)
- Test: `tests/test_structure.py`, `tests/test_table_tools.py`(새), `tests/test_tools_ask.py`

**Interfaces:**
- Consumes: Task 5·6의 `Structure`, `Toolbox._structure`
- Produces:
  - `render_area(structure, handle) -> str | None`(없는 핸들은 None)
  - `READ_ROWS = 30`; `render_table(table, columns: list[str] | None, start: int = 0) -> dict` → `{"text", "start", "shown", "total", "next": int | None}`. 글은 행마다 `row<i>: 열=값[번호] …`.
  - `find_rows(table, where: dict[str, str], columns: list[str] | None) -> dict` → `{"rows": [{"index", "keys": {열: 값}, "cells": {열: {"mark", "value", "offscreen"}}}], "reason": None | "no_such_column" | "no_match"}`. 값 비교는 양쪽 `strip()` 뒤 같음. 빈칸은 빈 문자열로 비교(위 행 값으로 채우지 않음). 열 이름은 `Column.label`과 정확히 같아야 한다.
  - 도구 인자: `open_area(area: str)`, `read_table(table: str, columns: list[str] | null, start: int | null)`, `find_rows(table: str, where: object(문자열 값), columns: list[str] | null)`. 핸들은 최근 관찰의 것만 받는다(없으면 거절 "Observe again").
  - `find_rows`가 두 행 이상이면 돌려준 칸들을 `_note_cells`로 알린다. 후보 중 `offscreen`이 하나라도 있으면 그 표 박스 전체를 막는다. 결과는 `_emit({"type": "find_rows", "table", "where", "matched", "marks"})`.

- [ ] **Step 1: 실패하는 시험**

```python
def test_find_rows_returns_the_single_matching_row_with_cell_marks(): ...
def test_find_rows_reports_no_such_column_and_no_match(): ...
def test_find_rows_does_not_fill_blank_labels_down(): ...
def test_find_rows_with_two_matches_blocks_both_cells_until_asked(tmp_path): ...   # click_part on either → needs_confirmation
def test_an_offscreen_candidate_blocks_the_whole_table(tmp_path): ...
def test_one_match_clicks_without_a_question(tmp_path): ...
def test_read_table_pages_by_30_rows(): ...
def test_open_area_lists_lines_left_to_right(): ...
def test_handles_from_an_older_observation_are_refused(tmp_path): ...
def test_table_tools_are_level_0_and_read_only(): ...
def test_find_rows_is_kept_in_events(tmp_path): ...
def test_summary_counts_find_rows_without_values(): ...
```

- [ ] **Step 2–4:** 실패 확인, 구현, 전체 pytest.
- [ ] **Step 5:** 커밋 `feat: let the model open areas, read tables and find rows by their values`.

---

### Task 8: workflow가 칸을 조건으로 적고 다시 찾기

**Files:**
- Modify: `agent/workflow_format.py` (`ACT_KEYS`에 `"cell"`, `CELL_KEYS = ("table", "column", "where")`, 검사), `agent/workflow_author.py` (칸 단계에 `cell` 붙이기), `agent/workflow_run.py` (`cell`이 있으면 그것으로 찾기)
- Test: `tests/test_workflow_format.py`, `tests/test_workflow_author.py`, `tests/test_workflow_run.py`

**Interfaces:**
- Consumes: Task 5 `analyze`, `table_identity`; Task 7 `find_rows`; 기록의 `observation.structure`와 `find_rows` 이벤트
- Produces:
  - 단계 모양: 기존 `target`은 그대로 두고, `click_part`가 누른 번호가 같은 관찰의 직전 `find_rows` 결과 칸이었다면 `cell: {"table": table_identity(..., use_ids), "column": <열 이름>, "where": <그 find_rows의 where>}`를 더한다. `where`의 값이 파라미터의 예시와 같으면 `"$<파라미터>"`로 바꾼다(기존 이름 파라미터와 같은 방식).
  - 재실행: `cell`이 있으면 새 관찰을 `analyze`해 신원이 맞는 표를 고르고(`use_ids`는 프로파일의 `stable_ids`), `find_rows(where)`의 한 행에서 `column` 칸을 누른다. 표 0개·여럿은 `CELL_TABLE_NOT_FOUND`·`CELL_TABLE_AMBIGUOUS`, 행 0개·여럿은 `CELL_NOT_FOUND`·`CELL_AMBIGUOUS`로 그 단계에서 멈춘다. 칸이 화면 밖이면 `CELL_OFFSCREEN`으로 멈춘다(스크롤은 하지 않음).

- [ ] **Step 1: 실패하는 시험**

```python
def test_a_cell_found_by_find_rows_is_written_with_its_condition(): ...
def test_a_where_value_equal_to_a_param_example_becomes_the_param(): ...
def test_replay_finds_the_same_row_at_another_index(): ...    # recorded row1, replay tree has it at row3 → clicks row3's mark
def test_replay_stops_with_cell_codes(): ...                   # each of the five codes
def test_a_cell_clicked_without_find_rows_is_written_as_before(): ...
def test_format_rejects_unknown_cell_keys(): ...
```

- [ ] **Step 2–4:** 실패 확인, 구현, 전체 pytest.
- [ ] **Step 5:** 커밋 `feat: remember table cells by the row values that found them`.

---

### Task 9: 가상 MES — 회사 모양 표(`rows`)와 `--nest`

**Files:**
- Modify: `virtual-mes/Exposure.cs` (`Exposure.Rows`, `grid`만 허용), `virtual-mes/SurfaceUia.cs` (행 그룹 제공자), `virtual-mes/Options.cs` (`--nest <0..12>`, `--parse-only` JSON에 `nest`), `virtual-mes/MainForm.cs` 또는 `ScreenPanel.cs` (감싸개 패널), `virtual-mes/README.md`
- Test: `tests/test_virtual_mes.py`(창 없음), `tests/test_virtual_mes_live.py`(새 실제 창 시험, 작업자는 돌리지 않음)

**Interfaces:**
- Produces:
  - `--expose grid=rows`: 표 노드 아래 `Data Panel`(ControlType Pane, 이름 `Data Panel`) > `Row <n>`(ControlType Custom, 이름 `Row <n>`, n은 1부터) > 칸(ControlType DataItem, 이름 `<열 머리글 글자> row<k>`, k는 0부터, Value 패턴 값 = 화면 글자, 지금 `ref` 칸과 같은 클릭 동작과 `cell_clicked` 이벤트). 머리글 줄은 칸으로 내지 않는다(회사 경로에 머리글이 없음). 병합된 칸은 그 칸이 걸친 첫 행에만 값, 나머지 행은 빈 값 칸.
  - `--nest N`: 화면 패널을 N겹의 패널로 감싼다. 홀수 겹은 이름·id 없음, 짝수 겹은 `Name`이 `nest-<i>`인 패널(id만 있는 영역 흉내). 기본 0이면 지금과 같다.
  - 해석 시험: 좋은 값, 나쁜 값(`--nest 13`, `menu=rows`) → 종료 코드 2. `build.ps1 -OutputDir <임시>` → `Built:`.
  - 실제 창 시험(이름만, 몸체는 기존 `LiveApp` 방식): `test_rows_grid_shows_rows_of_named_cells_with_values`, `test_nest_12_cuts_the_cells_at_the_default_depth_and_depth_30_reaches_them`.

- [ ] **Step 1–4:** 해석 시험 먼저, 구현, 빌드, 전체 pytest. 변이 확인은 인자를 직접 골라 창 없이(`--parse-only`).
- [ ] **Step 5:** 커밋 `feat: give the virtual mes a company-shaped grid and deep nesting`.

---

### Task 10: 실제 창 확인과 문서 (오케스트레이터)

**Files:**
- Modify: `tests/test_scenes_live.py` (장면 하나), 저장소 `CLAUDE.md`의 `examples/llm-agent` 부분, `README.md`, 사양 상태 줄

**Interfaces:**
- Consumes: Task 1–9 전부

- [ ] **Step 1:** 장면 시험 `test_scene_rows_find_rows_then_click_the_cell`: `--expose grid=rows --nest 12`로 띄우고, 공정별 생산집계를 조회한 뒤 `find_rows(T1, {공정: 극판, 구분: 소계})` → `click_part(칸, center)` → 앱의 `cell_clicked` 행이 `["극판", "소계", …]`이고 질문이 0개(한 행일 때) 또는 1개(여러 Shop일 때, 답 뒤 누름).
- [ ] **Step 2:** 사용자 확인 뒤 `VIRTUAL_MES_LIVE=1`로 Task 9의 두 시험과 이 장면, 그리고 기존 실제 창 시험 전체를 돌린다(짧은 `--basetemp`, ACL 끊은 폴더).
- [ ] **Step 3:** 문서: `CLAUDE.md`에 깊게 읽기·구조 요약·표 도구를 한 단락, README 도구 목록, 사양 상태 "1·2단계 구현됨".
- [ ] **Step 4:** 전체 pytest, 커밋 `docs: describe deep reads, the structure summary and the table tools`.
