"""Every code, value and meaning of the company capture session, in one place.

The capture session, the hypotheses, the labels check, the transmission block and the home
reader all take their codes from here, so a code means the same thing on both PCs. Meanings
are one Korean line each and carry no screen text.
"""

SURVEY_VERSION = 3
READABLE_VERSIONS = (2, 3)
"""Block versions --read-block reads without a warning: v3 only added the cut count and S to H11."""
SURVEY_POLL_S = 0.5
SURVEY_MAX_WINDOWS = 8
SURVEY_SNAPSHOT_MS = 5000
UIA_MAX_NODES = 3000
UIA_TIMEOUT_S = 30
SURVEY_QUEUE_MAX = 5
SURVEY_STARTUP_S = 60
SURVEY_FOREGROUND_S = 2
SURVEY_END_WAIT_S = 60
DEFAULT_CAPTURE_HOTKEY = "ctrl+alt+f9"
DEFAULT_END_HOTKEY = "ctrl+alt+f10"
BLOCK_MAX_LINES = 15
HYPOTHESES_PER_LINE = 4
SAME_SCREEN_SHARE = 0.2
CHECK_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

SCENES = ("STARTUP_POPUP", "LOGIN", "MAIN", "GRID", "FORM", "ERROR_DIALOG", "WARNING_DIALOG",
          "CONFIRM_DIALOG", "MESSAGE", "LOADING", "OTHER", "?")
KINDS = ("MAIN", "DIALOG", "OWNED", "TOP")
RELS = ("APP", "CHILD_OTHER", "SYSTEM", "OTHER", "UNKNOWN")
STATES = ("OK", "HUNG", "TIMEOUT", "GONE", "BLANK")
READS = ("OK", "TRUNCATED", "TIMEOUT", "UNRESPONSIVE", "GONE", "ERROR")
SIZES = ("S", "M", "L")
SHOTS = ("OK", "SHOT_BLANK", "SHOT_FAILED")
SLOWS = ("DONE", "QUEUE_FULL", "QUEUE_ABANDONED")
SHALLOW = ("deep_read_too_large", "deep_read_timeout")
"""The fallback codes of agent.deep_read a capture keeps as WindowFact.shallow."""
APP_SEEN = ("ALREADY_RUNNING", "STARTED", "PICKED_BY_CAPTURE", "NOT_SEEN")
EVENTS = ("window_shown", "window_hidden", "process_started", "process_exited", "hung_start",
          "hung_end", "foreground")
FAILURES = ("HOTKEY_TAKEN", "UIA_SPAWN", "UIA_TIMEOUT", "UIA_EXIT", "UIA_OUTPUT", "LABEL_INVALID",
            "NO_APP", "NO_READ", "MODULES_UNREAD")

_SCENE = {
    "STARTUP_POPUP": "켤 때 뜨는 안내·공지·업데이트 창",
    "LOGIN": "로그인 화면",
    "MAIN": "메인 화면(메뉴와 작업 영역)",
    "GRID": "표가 중심인 조회 화면",
    "FORM": "입력 칸이 중심인 등록 화면",
    "ERROR_DIALOG": "오류를 알리는 창",
    "WARNING_DIALOG": "경고 창",
    "CONFIRM_DIALOG": "예/아니오를 묻는 창",
    "MESSAGE": "단순 알림 창",
    "LOADING": "기다리는 중 표시",
    "OTHER": "위에 없음",
    "?": "판단하지 않음(기본)",
}
_KIND = {
    "MAIN": "앱의 가장 큰 소유 없는 최상위 창",
    "DIALOG": "Win32 대화상자(#32770) 창",
    "OWNED": "소유 창이 있는 다른 최상위 창",
    "TOP": "그 밖의 최상위 창",
}
_REL = {
    "APP": "앱 프로세스(대상 exe의 폴더나 추가 exe의 폴더에 있는 실행 파일)",
    "CHILD_OTHER": "앱이 띄운, 앱 폴더 밖의 다른 프로그램",
    "SYSTEM": "실행 파일이 Windows 폴더 아래인 프로세스",
    "OTHER": "앱과 관계없는 다른 프로세스",
    "UNKNOWN": "실행 파일 경로를 읽지 못한 프로세스",
}
_STATE = {
    "OK": "창을 읽음",
    "HUNG": "앱이 응답 없음(IsHungAppWindow 또는 UNRESPONSIVE)",
    "TIMEOUT": "창 읽기가 시간 안에 끝나지 않음",
    "GONE": "읽을 때 창이 이미 닫혔거나 목록에 없음",
    "BLANK": "화면 그림이 거의 한 색(캡처가 막힘)",
}
_READ = {
    "OK": "트리를 끝까지 읽음",
    "TRUNCATED": "읽기 예산이 다 돼 트리 일부만 읽음",
    "TIMEOUT": "읽기가 시간 안에 끝나지 않음",
    "UNRESPONSIVE": "앱이 응답하지 않아 읽지 못함",
    "GONE": "창이 닫혔거나 목록에 없음",
    "ERROR": "그 밖의 읽기 오류",
}
_SIZE = {
    "S": "작은 창(화면의 10% 미만)",
    "M": "중간 창",
    "L": "화면 대부분을 덮는 창(60% 이상)",
}
_SHOT = {
    "OK": "화면 그림을 저장함",
    "SHOT_BLANK": "화면 그림이 거의 한 색(보안 프로그램이 캡처를 막는 경우)",
    "SHOT_FAILED": "화면 그림을 찍지 못함",
}
_SLOW = {
    "DONE": "느린 단계(창 읽기)를 마침",
    "QUEUE_FULL": "대기열이 가득 차 느린 단계를 건너뜀",
    "QUEUE_ABANDONED": "세션이 끝날 때까지 느린 단계를 마치지 못함",
}
_SHALLOW = {
    "deep_read_too_large": "깊게 읽은 트리가 CLI 크기 한도를 넘어 기본 깊이로 다시 읽음(H11의 S)",
    "deep_read_timeout": "깊게 읽기가 시간 안에 끝나지 않아 기본 깊이로 다시 읽음(H11의 S)",
}
_APP_SEEN = {
    "ALREADY_RUNNING": "세션 시작 때 앱이 이미 떠 있었음",
    "STARTED": "세션 중 앱이 켜지는 것을 봄",
    "PICKED_BY_CAPTURE": "앱 프로세스를 못 봐서 첫 캡처의 전경 창 프로세스를 앱으로 삼음",
    "NOT_SEEN": "세션 내내 앱 프로세스를 보지 못함",
}
_FAILURE = {
    "HOTKEY_TAKEN": "다른 프로그램이 단축키를 이미 써서 등록하지 못함",
    "UIA_SPAWN": "UIA 개수를 세는 PowerShell을 시작하지 못함",
    "UIA_TIMEOUT": "UIA 개수 세기가 시간 안에 끝나지 않음",
    "UIA_EXIT": "UIA 개수 세기가 오류로 끝남",
    "UIA_OUTPUT": "UIA 개수 세기의 출력을 읽지 못함",
    "LABEL_INVALID": "labels.yaml에 코드표 밖의 값이나 틀린 형식이 있음",
    "NO_APP": "앱 프로세스를 보지 못함",
    "NO_READ": "앱 창이 보였지만 하나도 읽지 못함",
    "MODULES_UNREAD": "앱 프로세스의 모듈 목록을 읽지 못함",
}

MEANINGS: dict[str, str] = {
    f"{group}:{code}": text
    for group, table in (("scene", _SCENE), ("kind", _KIND), ("rel", _REL), ("state", _STATE),
                         ("read", _READ), ("size", _SIZE), ("shot", _SHOT), ("slow", _SLOW),
                         ("shallow", _SHALLOW), ("app_seen", _APP_SEEN), ("failure", _FAILURE))
    for code, text in table.items()
}
