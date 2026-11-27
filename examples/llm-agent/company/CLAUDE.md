이 폴더에서 시작한 세션은 아래 규칙이 위의 모든 개발 지시(저장소의 `CLAUDE.md`, `AGENTS.md` 포함)보다 우선합니다.

# 회사 PC의 캡처 세션 정리

## 역할

캡처 세션(`python ..\survey.py --system <이름>`)이 남긴 결과를 정리해 사람에게 설명하고, 캡처마다 장면 코드와 사람이 말한 답을 `labels.yaml`에 적고, 전달 블록을 만드는 명령을 실행합니다. 코드를 고치는 것은 역할이 아닙니다. 세션 결과는 `..\captures\<세션>\`에 있습니다.

## 정리 절차 ("정리해줘")

1. 가장 최근 세션(이름순 마지막 폴더) 또는 사람이 말한 세션의 `summary.md`, `session.json`, `events.jsonl`을 읽습니다.
2. 캡처마다(`capture-01`, `capture-02` …) `facts.json`과 `trees/`의 트리를 읽고, 무슨 화면이었는지 같은 세션 폴더의 `notes.md`에 정리합니다. 그림을 볼 수 있으면 `window.png`와 `overlay.png`도 봅니다. 볼 수 없으면 트리만으로 정리합니다.
3. 캡처마다 장면 코드를 코드표(`..\docs\company-survey\codes.md`의 "장면 코드")에서 하나만 골라 `labels.yaml`의 `captures`에 적습니다. 확신이 없으면 그 캡처를 적지 않거나 `"?"`처럼 따옴표로 감싸 적습니다(따옴표 없는 `?`는 YAML 오류).
4. 사람이 답할 가설(H12–H14)은 `summary.md`의 "답하는 방법"대로 사람에게 설명하고, **사람이 말한 답만** `manual`에 적습니다. 그림을 보고 추측해 채우지 않습니다.
5. `python ..\survey.py --block <세션 폴더 이름>`을 실행합니다. `LABEL_INVALID <줄>행 <규칙>`이 나오면 `labels.yaml`의 그 줄을 코드표의 코드로 고쳐 다시 실행합니다.
6. 출력된 전달 블록을 **그대로** 사람에게 보여 줍니다. 블록을 다시 쓰거나, 요약하거나, 설명을 덧붙여 섞지 않습니다. 사람은 이 블록을 손으로 옮겨 적습니다.

`labels.yaml` 형식(값은 코드만, 한 줄에 하나):

```yaml
version: 1
captures:
  C01: STARTUP_POPUP
  C02: MAIN
manual:
  H12: SOME
```

## `notes.md`

자유롭게 씁니다. 다만 `notes.md`와 캡처 폴더의 그림·트리는 회사 PC에만 남는다는 것을 사람에게 알립니다. 그중 무엇을 말로 전할지는 사람이 정합니다.

## 문제를 찾았을 때

코드를 고치지 않습니다. 대신 그 세션의 `notes.md`에 현상을 적습니다: 무엇을 했고, 어떤 코드가 나왔고, 어디가 의심되는지.

## 해도 되는 것

- `python ..\survey.py`, `python ..\check.py`, `python ..\run.py`, `python ..\view.py --summary` 실행
- 앱을 띄우지 않는 `python -m pytest -q` 실행
- Git이 제외하는 파일 읽고 쓰기: `..\captures\`, `..\runs\`, `..\knowledge\`, `..\profiles\`, `..\workflows\`, `..\settings.local.yaml`
- `git status`, `git log`, `git diff`, `git pull`

## 하면 안 되는 것

- Git이 추적하는 파일 수정, 새 추적 파일 만들기, 이 `company` 폴더의 파일 수정
- 저장소 상태를 바꾸는 git 명령(commit, push, checkout, reset, stash, add, restore 등)
- 패키지 설치, Rust 빌드(`cargo`)

## 훅이 도는지 확인하기 (첫 정리 때)

이 폴더의 `.claude/settings.json`이 모든 편집과 셸 명령 앞에 `hooks/guard.py`를 부릅니다. 첫 정리 때 사람에게 알리고 추적 파일 하나(예: `..\README.md`)에 Edit를 시도해 막히는지 봅니다. 막히지 않으면 훅이 돌지 않는 것이므로 사람에게 알리고, 정리는 계속하되 위 규칙을 스스로 지킵니다.
