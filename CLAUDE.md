# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

위 `AGENTS.md`가 명령어, 아키텍처, 코딩 규칙, 출력 계약의 기준입니다. 아래는 이 저장소와 이 PC에서 달라지는 내용만 적습니다. 충돌하면 아래가 우선합니다.

## 여러 에이전트 협업

여러 터미널의 에이전트가 함께 일할 때는 `ORCHESTRATION.md`를 따릅니다. 오케스트레이터는 `.coordination/coordinator.json`에 적힌 한 명뿐이고, 사용자나 기존 오케스트레이터가 지정하지 않았다면 작업자로 동작합니다.

## 이 포크의 상태

`AGENTS.md`는 원본 `vercel-labs/agent-desktop` 기준입니다. 이 저장소는 그 코드에서 새 히스토리로 시작한 **비공개** 개인 포크(`Sihoon92/AgentDesktop`)이며 upstream 원격이 없습니다. 원본의 변경은 패치로 직접 옮겨야 합니다.

- **비공개 유지:** `LICENSE`를 삭제한 상태입니다. 원본 코드는 여전히 Apache-2.0이므로 공개하거나 배포하려면 복원이 먼저입니다. `Cargo.toml`과 `npm/package.json`의 `Apache-2.0` 표기는 사실이므로 지우지 않습니다.
- **CI:** `ci.yml`의 `test-windows` 잡 하나만 돕니다(MSVC). 다른 워크플로는 파일만 남기고 GitHub에서 비활성화했고 Dependabot 설정은 삭제했습니다. `AGENTS.md`의 CI, 릴리스, macOS·Linux lane 설명은 여기에 적용되지 않습니다.
- **`ci.yml` 수정 주의:** `src/cli/contract_tests.rs`, `capture_envelope_parity_tests.rs`, `envelope_live_tests.rs`가 이 파일의 문자열을 직접 검사합니다. 단계 이름이나 명령을 바꾸면 이 테스트가 실패할 수 있습니다.
- **검사가 자동으로 돌지 않음:** pre-commit 훅이 꺼져 있습니다(`core.hooksPath` 미설정). `scripts/check-architecture.sh`는 CI에 없고, 이 PC에서는 수정 전 코드에서도 G11 자체 테스트에서 실패합니다.
- **`.claude`:** 저장소의 심볼릭 링크가 Windows에서 텍스트 파일로 풀려 있습니다. 프로젝트 스킬은 `.agents/skills/`에서 직접 읽습니다.

## Windows 개발

툴체인은 `1.89.0-x86_64-pc-windows-gnu`입니다. MSVC 빌드 도구가 이 PC에 없어 로컬은 GNU로, 공식 대상인 MSVC는 CI로 확인합니다. `~/.cargo/config.toml`이 `dlltool`을 conda 환경 `AgentDesktop`에서 가져오므로 **이 환경을 지우면 빌드가 실패합니다.** cargo 빌드에 환경 활성화는 필요 없고, Python 스크립트를 돌릴 때만 활성화합니다.

Git Bash 기준입니다.

```bash
HOST_PKGS=(-p agent-desktop-core -p agent-desktop-windows -p agent-desktop -p agent-desktop-ffi)

cargo build --locked -p agent-desktop                          # target/debug/agent-desktop.exe
cargo fmt --all -- --check
cargo clippy --locked "${HOST_PKGS[@]}" --all-targets -- -D warnings

# 데스크톱을 건드리지 않음
bash scripts/cargo-test-isolated-home.sh test --locked -p agent-desktop-core --lib
bash scripts/cargo-test-isolated-home.sh test --locked -p agent-desktop-windows --lib tree::live_read   # 모듈/테스트 이름으로 필터
bash scripts/cargo-test-isolated-home.sh test --locked -p agent-desktop --bin agent-desktop ci_
powershell -NoProfile -File tests/e2e-windows/selftest/Invoke-U7SelfTests.ps1                           # 스텁 기반
```

**사용자가 PC를 쓰는 동안 돌리지 말 것:** `agent-desktop-windows --lib` 전체와 `cargo test -p agent-desktop` 전체. 실제 클립보드, 키 입력, 시작 메뉴를 건드리며, 일부 헬퍼는 클립보드의 텍스트만 복원합니다. 평소에는 CI에 맡기고, 로컬에서 돌리려면 먼저 확인받습니다. 라이브 E2E(`tests/e2e-windows/Run-E2E.ps1`)도 같습니다.

전역 `agent-desktop` 명령은 npm의 공식 1.2.0이라 이 저장소의 수정이 반영되지 않습니다. 수정본을 확인할 때는 빌드한 exe를 직접 실행합니다.

## 실제 동작 확인

대상은 사용자 앱이 아니라 저장소의 WinForms 픽스처입니다. `AGENT_DESKTOP_HOME`을 임시 폴더로 지정해 `~/.agent-desktop`을 건드리지 않습니다. `launch`와 액션은 창을 띄우고 포커스를 옮길 수 있습니다.

```powershell
powershell -NoProfile -File tests\fixture-app-windows\build.ps1 -OutputDir <임시폴더>
$env:AGENT_DESKTOP_HOME = '<임시폴더>\state'
.\target\debug\agent-desktop.exe launch <임시폴더>\AgentDeskFixture.exe
.\target\debug\agent-desktop.exe snapshot --app AgentDeskFixture -i --compact
.\target\debug\agent-desktop.exe close-app AgentDeskFixture
```

효과는 `ok: true`가 아니라 픽스처의 상태 표시(`*-status`, 예: `checked#1`)로 확인합니다. 시행착오로 확인한 함정은 다음과 같습니다.

- **독립 검증:** .NET 관리형 UIA는 이 픽스처의 `AutomationId`를 숫자로 돌려줘 찾을 수 없고, `BM_GETCHECK`는 체크박스에서 항상 0입니다. 상태 표시를 `WM_GETTEXT`로 읽습니다.
- **PowerShell 5.1:** ref는 따옴표로 감싸고(`'@s8f3k2p9:e8'`), `batch` JSON은 큰따옴표를 `\"`로 이스케이프합니다. 파이프 입력에는 줄바꿈이 붙습니다.
- **MCP 테스트:** .NET Framework의 `Process.StandardInput`으로 붙이면 첫 줄이 파싱 오류가 납니다(BOM으로 추정). Node 클라이언트는 정상입니다.
- **DPI 175%:** 폼이 창보다 커져 아래쪽 컨트롤이 `offscreen`입니다. 먼저 `scroll-to`를 합니다.
- **콤보박스:** `select`가 아니라 `set-value`를 씁니다. 값은 바뀌지만 앱의 변경 이벤트는 발생하지 않습니다(`application_commit: not_verified`).

미해결: WinForms 슬라이더의 `set-value`는 값이 바뀌지 않아 `ACTION_FAILED`가 됩니다. 실행 직후 첫 액션 도중 전경 창이 대상 앱으로 넘어간 적이 있으나 원인은 모릅니다. 둘 다 근본 원인은 확인되지 않았습니다.

## examples/llm-agent

`agent-desktop` CLI 위에 관찰-동작 루프를 올린 Python 에이전트입니다(README 참고). 저장소의 Rust 규칙은 적용되지 않습니다.

**CUA 배경:** 이 예제는 사내 MES용 computer-use agent로 키우는 중입니다. 설계, 기능 추가, workflow 생성, 다른 시스템으로의 일반화, 회사에서 전해 온 현상 해석 전에 `examples/llm-agent/docs/cua-background.md`를 읽습니다. 회사 실행 결과는 집으로 가져올 수 없고 사용자가 말로 전합니다.

```powershell
Set-Location examples\llm-agent
conda activate AgentDesktop
python -m pytest -q                                         # 캡처한 CLI 응답 재생, 앱 조작 없음
python run.py tasks\footprint_open_settings.yaml --baseline  # LLM 없이 고정 순서 실행
python run.py tasks\footprint_open_settings.yaml             # LLM 호출
python run.py --exe "<절대경로>" --goal "<지시>" [--approve]   # 작업 파일 없이 지시(결과는 claimed, 종료 코드 3)
python view.py --exe "<절대경로>"                              # 단계 뷰어: 기록 재생, 새 실행, 동작 전 승인
python view.py --open latest                                # 지난 실행 바로 열기(run.py가 돌지 않아도 됨)
python view.py --prune --older-than 30d [--yes]              # 오래된 기록 정리, --yes 없으면 출력만
python view.py --export <run_id|latest> <out.html>           # 실행 하나를 서버 없이 열리는 HTML로
```

**가상 MES 앱:** `examples/llm-agent/virtual-mes/`는 CUA 개발용 연습 앱입니다(`virtual-mes/README.md`). `powershell -NoProfile -File examples\llm-agent\virtual-mes\build.ps1`로 빌드합니다. `tests\test_virtual_mes.py`는 창을 띄우지 않습니다. `tests\test_virtual_mes_live.py`는 `VIRTUAL_MES_LIVE=1`일 때만 돌고 실제 창을 띄우며 커서를 움직이므로, 돌리기 전에 사용자에게 확인받습니다.

실행마다 `runs\<id>\`에 스크린샷과 요소 박스가 기록되고(`agent/recorder.py`), 뷰어(`viewer/`)가 표준 라이브러리 서버로 127.0.0.1에서 토큰을 받아 보여 줍니다.

`run.py`가 작업 파일과 CLI 바이너리를 찾아 `agent/loop.py`의 루프를 돌립니다. 모델은 `agent/tools.py`의 도구로만 앱을 조작하고, 쓸 수 있는 도구는 작업 파일의 `permission_level`로 제한되며, `agent/guard.py`가 반복 동작을 막습니다. 성공 여부는 모델이 아니라 하니스(`agent/verify.py`)가 작업 파일의 `success` 조건으로 판단합니다. 바이너리는 `AGENT_DESKTOP_BIN`, `target/release`, `target/debug` 순으로 찾습니다.

**사내 배포:** 내부 OpenAI 호환 LLM을 `OPENAI_BASE_URL`로 쓸 계획입니다. 이 예제는 Chat Completions가 아니라 **Responses API**를 호출하며(`agent/llm.py`), 내부 API도 이를 지원한다고 사용자가 확인했습니다. `scripts/jev/`는 외부 결정 API로 화면 내용을 보내고 OpenAI 형식도 아니므로 사내에서 쓰지 않습니다.
