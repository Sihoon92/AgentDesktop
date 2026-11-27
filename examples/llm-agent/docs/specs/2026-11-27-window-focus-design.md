# 대상 창을 맨 앞으로 올리기 설계

- 날짜: 2026-11-27
- 상태: 구현됨

## 왜

회사 MES 실행에서 `click_part`가 `Window w-… could not take focus, so the point may hit another app`으로 거절되어 작업이 멈췄다. 그때 MES 본 화면 위에 팝업은 없었다(사용자 확인). 뷰어(브라우저)가 전경에 있으면 Windows는 뒤의 프로세스가 부르는 `SetForegroundWindow`를 거절한다. CLI는 이 요청을 여러 번 시도한 뒤 이유를 담은 오류를 돌려주지만, 하니스는 `focus-window`의 결과를 버리고 창 목록의 `is_focused`만 다시 본다(`agent/tools.py` `_physical`). 그래서 회사에서 왜 실패했는지 알 수 없었다.

## 목표

- CLI의 창 올리기가 다른 프로세스가 전경에 있을 때도 성공한다. 특정 앱에 맞춘 분기 없이 모든 앱에 같이 적용한다.
- 하니스가 앱이 준비되면 바로 대상 창을 맨 앞으로 올린다.
- 모델이 `focus_window` 도구로 직접 창을 올릴 수 있다.
- 실패하면 CLI가 준 코드와 이유가 모델, `events.jsonl`, 요약(코드와 수만)에 남는다. 권한 차이 때문이면 그렇다고 말한다.

## 하지 않는 것

- 권한 차이(대상이 더 높은 권한) 우회. Windows가 막으므로 방법이 없다. "에이전트를 관리자 권한으로 띄우라"는 안내만 한다.
- 제목 표시줄 클릭으로 활성화하기. 덮인 창을 누를 위험이 있다.
- 늘 맨 위에 두기(`HWND_TOPMOST`). 사용자의 다른 창을 계속 가리고, 앱 동작도 바꾼다.

## 접근 방법

- A. 하니스만 고친다: 준비 직후 올리기, `focus_window` 도구, 오류 전달. Windows가 거절하는 경우는 그대로 실패한다.
- **B. A에 더해 CLI의 `bring_to_foreground`를 보강한다. (사용자 선택)** 거절 원인 자체를 줄인다.
- C. 제목 표시줄 클릭. 다른 창을 누를 수 있어 뺐다.

## 설계

### CLI (`crates/windows/src/system/window/window_activate.rs`)

- `raise_with_budget`의 첫 시도는 지금처럼 `SetForegroundWindow`만 부른다.
- 첫 시도가 실패하면 두 번째 시도부터 입력 스레드를 붙인다. 현재 전경 창의 스레드에 `AttachThreadInput`으로 붙고, `BringWindowToTop`과 `SetForegroundWindow`를 부른 뒤 반드시 뗀다. 중간에 실패해도 떼는 것은 빠지지 않는다.
- 전경 창이 메시지를 처리하지 않으면 붙이지 않는다(`window_is_responsive`). 응답 없는 스레드에 붙으면 함께 멈출 수 있기 때문이다(`menu_state_tests.rs`의 경고). 전경 창이 없거나 이미 같은 스레드여도 붙이지 않는다.
- 성공 판단은 지금처럼 `is_owned_foreground`(핸들과 프로세스 인스턴스가 모두 맞아야 함)로 한다. 권한 차이 오류(`activation_elevation_denied`)와 시도 횟수, 마감은 그대로 둔다.
- 테스트: 붙이기 조건(응답 없음, 같은 스레드, 전경 없음)을 단위 테스트로 확인한다. 실제로 떠 있는 창을 쓰는 테스트(다른 창을 전경에 둔 채 픽스처 창을 올림)는 데스크톱을 건드리므로 사용자 확인을 받고 돌리거나 CI에 맡긴다.
- `skills/agent-desktop-windows/`에 포커스 동작이 적혀 있으면 함께 고친다.

### 하니스 (`examples/llm-agent/agent/`)

- 준비 직후 올리기: 준비 확인(`apply_ready`)이 `ready`로 끝났거나 `launch`가 성공하면 현재 창에 `focus-window`를 한 번 부르고 `focus` 이벤트(`ok`, `code`)를 남긴다. 실패해도 실행은 계속한다. 관찰은 포커스 없이도 된다.
- `focus_window(window_id)` 도구: 레벨 0(`restore_window`와 같음). 대상 프로세스의 창만 받는다. 결과는 `ok`, 실패하면 `code`, `message`, `suggestion`이다. 앱을 바꾸지 않으므로 다시 관찰하지 않고, 반복 동작 가드도 걸지 않는다.
- `_physical`: `focus-window`의 응답을 본다. `ok: false`이면 CLI의 `code`, `message`, `suggestion`을 거절 결과에 담는다. 응답은 `ok`인데 창 목록이 포커스 없음이면 지금 메시지를 그대로 쓴다. 두 경우 모두 "focus_window로 다시 올린 뒤 시도하라"는 안내를 붙인다.
- 시스템 프롬프트(`loop.py`)에 한 줄 더한다: 포커스 때문에 거절되면 `focus_window`를 부르고, 권한 오류면 사용자에게 묻는다.
- 요약(`summary.py`): 포커스 실패 수와 코드만 넣는다. 창 제목이나 메시지 글은 넣지 않는다.
- 앱 이름과 화면 글은 넣지 않는다(`tests/test_application_agnostic.py`).
- 테스트: 캡처한 CLI 응답을 재생하는 기존 방식으로 준비 직후 올리기, 도구, `_physical`의 오류 전달, 요약을 확인한다.

## 구현자에게 맡기는 것

- `AttachThreadInput`을 두 번째 시도부터 쓸지 모든 시도에 쓸지: 위는 내 판단이다. 첫 시도가 실패할 때만 쓰면 평소의 동작이 그대로 남는다.
- 가짜 ALT 키 입력 같은 다른 우회책은 넣지 않는다(내 판단). 전경 앱의 메뉴가 열리는 부작용이 있다. 붙이기로도 실패하는 사례가 확인되면 그때 다시 본다.
- 픽스처로 하는 실제 확인 절차의 세부.
