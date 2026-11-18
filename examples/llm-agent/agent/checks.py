"""The pure parts of the environment check: results, lines, and the numbers they carry.

A check line names what passed or failed with counts and codes only. It never carries a
screen text, an element name, a file path, a setting's value, an endpoint, or a response
body, because the person reads these lines aloud from a PC whose data cannot leave it.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent.images import IMAGE_MODES

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None

PASSED = "통과"
FAILED = "실패"
SKIPPED = "건너뜀"
BINARY_KINDS = ("환경변수", "release", "debug")
IMAGE_PREFERENCE = ("user_message", "tool_output")
IDS_FORMAT = 2
PROCESS_MARK_LENGTH = 16
DIGITS = re.compile(r"\d+")
DIGIT_CANVAS = (480, 200)
DIGIT_FONT_PX = 120
POINT_CANVAS = (1000, 600)
POINT_RADIUS_PX = 24
POINT_TOLERANCE_PX = 40
POINT_X_RANGE = (100, 900)
POINT_Y_RANGE = (200, 500)
POINT_COLOR = (255, 0, 0)
NORMALIZED_RANGE = 1000
POINT_X = re.compile(r'"x"\s*:\s*(-?\d+(?:\.\d+)?)')
POINT_Y = re.compile(r'"y"\s*:\s*(-?\d+(?:\.\d+)?)')


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    detail: str = ""
    code: str = ""


def passed(name, detail=""):
    return Result(name, PASSED, detail)


def failed(name, code, detail=""):
    return Result(name, FAILED, detail, code)


def skipped(name, detail=""):
    return Result(name, SKIPPED, detail)


def line(result):
    """`이름: 통과 (상세)`, `이름: 실패(코드) (상세)` or `이름: 건너뜀 (상세)`."""
    status = f"{FAILED}({result.code})" if result.status == FAILED else result.status
    tail = f" ({result.detail})" if result.detail else ""
    return f"{result.name}: {status}{tail}"


def exit_code(results):
    return 1 if any(result.status == FAILED for result in results) else 0


def footer(results):
    failures = sum(result.status == FAILED for result in results)
    skips = sum(result.status == SKIPPED for result in results)
    return f"결과: 실패 {failures} · 건너뜀 {skips} · 통과 {len(results) - failures - skips}"


def binary_location(environ, candidates):
    """Where run.py would find the binary, as (path, kind): the variable, release, then debug.
    `candidates` is (release, debug); with neither present the debug path is returned."""
    configured = environ.get("AGENT_DESKTOP_BIN")
    if configured:
        return Path(configured), BINARY_KINDS[0]
    for path, kind in zip(candidates, BINARY_KINDS[1:]):
        if Path(path).exists():
            return Path(path), kind
    return Path(candidates[-1]), BINARY_KINDS[-1]


def envelope_code(envelope, fallback="UNKNOWN"):
    return str((envelope.get("error") or {}).get("code") or fallback)


def llm_error_code(error):
    """The exception's class name and, when it has one, the HTTP status code. Never the body."""
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    name = type(error).__name__
    return f"{name} {status}" if isinstance(status, int) else name


def seconds(value):
    return f"{value:.1f}초"


def function_calls(response):
    return [item for item in getattr(response, "output", None) or [] if getattr(item, "type", None) == "function_call"]


def called_with(response, tool):
    """The decoded arguments of the first call to `tool`, or None when the model did not call it."""
    for call in function_calls(response):
        if call.name != tool:
            continue
        try:
            arguments = json.loads(call.arguments)
        except (TypeError, ValueError):
            return None
        return arguments if isinstance(arguments, dict) else None
    return None


def digits_read(response, tool):
    """The digits the model reported through `tool`, else the digits of its reply text."""
    arguments = called_with(response, tool)
    if arguments is not None:
        return "".join(DIGITS.findall(str(arguments.get("digits", ""))))
    return "".join(DIGITS.findall(getattr(response, "output_text", None) or ""))


def recommended_image_mode(read):
    """The image_mode to set from {mode: read correctly}: the first that worked, else "off"."""
    return next((mode for mode in IMAGE_PREFERENCE if read.get(mode)), IMAGE_MODES[0])


def image_detail(read, codes):
    parts = []
    for mode in IMAGE_PREFERENCE:
        if mode in codes:
            parts.append(f"{mode} 오류({codes[mode]})")
        else:
            parts.append(f"{mode} {'읽음' if read.get(mode) else '못 읽음'}")
    return " · ".join(parts + [f"권장 image_mode: {recommended_image_mode(read)}"])


def digit_image(digits, path):
    """Draws `digits` large, black on white, into a PNG at `path` and returns (width, height).
    Raises RuntimeError without Pillow."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")
    try:
        font = ImageFont.load_default(size=DIGIT_FONT_PX)
    except (TypeError, OSError):
        font = None
    if font is not None:
        image = Image.new("RGB", DIGIT_CANVAS, "white")
        ImageDraw.Draw(image).text((DIGIT_CANVAS[0] // 2, DIGIT_CANVAS[1] // 2), digits, fill="black",
                                   font=font, anchor="mm")
    else:
        small = Image.new("RGB", (DIGIT_CANVAS[0] // 8, DIGIT_CANVAS[1] // 8), "white")
        ImageDraw.Draw(small).text((4, 4), digits, fill="black")
        image = small.resize(DIGIT_CANVAS, Image.Resampling.NEAREST)
    image.save(path)
    return image.size


def point_image(path, rng):
    """Draws one red circle on a white POINT_CANVAS PNG at `path`; returns (center, size).
    The center's y stays in POINT_Y_RANGE, where a 0-1000 answer and a pixel answer lie more
    than twice POINT_TOLERANCE_PX apart, so the two rules cannot be mistaken for each other.
    Raises RuntimeError without Pillow."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")
    center = (rng.randint(*POINT_X_RANGE), rng.randint(*POINT_Y_RANGE))
    image = Image.new("RGB", POINT_CANVAS, "white")
    x, y = center
    ImageDraw.Draw(image).ellipse(
        (x - POINT_RADIUS_PX, y - POINT_RADIUS_PX, x + POINT_RADIUS_PX, y + POINT_RADIUS_PX), fill=POINT_COLOR)
    image.save(path)
    return center, image.size


def first_point(text):
    """The first (x, y) a vision reply gives, or None when it gives no point."""
    text = text if isinstance(text, str) else ""
    x, y = POINT_X.search(text), POINT_Y.search(text)
    if x is None or y is None:
        return None
    return tuple(float(match.group(1)) if "." in match.group(1) else int(match.group(1)) for match in (x, y))


def point_mode(answer, center, size):
    """"pixel" when `answer` is the center in image pixels, "normalized_1000" when it is the
    center in thousandths of the image, else None. Both are judged in pixels within POINT_TOLERANCE_PX."""
    x, y = answer
    if _near((x, y), center):
        return "pixel"
    if _near((x * size[0] / NORMALIZED_RANGE, y * size[1] / NORMALIZED_RANGE), center):
        return "normalized_1000"
    return None


def _near(point, center):
    return abs(point[0] - center[0]) <= POINT_TOLERANCE_PX and abs(point[1] - center[1]) <= POINT_TOLERANCE_PX


def observation_detail(kinds, complete, elapsed, shot):
    """The app observation as counts: the survey kinds, truncation, time, capture size and scale."""
    parts = [
        f"이름 있는 ref {kinds.get('named_ref', 0)}",
        f"이름 없는 ref {kinds.get('unnamed_ref', 0)}(id 있음 {kinds.get('unnamed_ref_with_id', 0)})",
        f"글자 {kinds.get('text', 0)}",
        f"빈 영역(그림 후보) {kinds.get('blank', 0)}",
        f"노드 {kinds.get('nodes', 0)}",
        f"깊이 {kinds.get('depth', 0)}",
        "잘림 없음" if complete else "잘림 있음",
        seconds(elapsed),
    ]
    if shot.get("width") and shot.get("height"):
        parts.append(f"캡처 {int(shot['width'])}x{int(shot['height'])}")
    scale = shot.get("scale_factor")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        parts.append(f"배율 {scale:g}")
    return " · ".join(parts)


def ids_path(folder, system):
    return Path(folder) / f"{system}.json"


def process_mark(pid, instance):
    """A hash that names one running process: its pid and the CLI's process_instance, which
    tells a reused pid apart. Without process_instance only the pid is hashed."""
    text = f"{pid}\n{instance or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:PROCESS_MARK_LENGTH]


@dataclass(frozen=True)
class SavedIds:
    keys: frozenset
    process: str | None


def load_ids(path):
    """What the last check saved, or None when there is none or it cannot be read."""
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    keys = saved.get("id_keys") if isinstance(saved, dict) else None
    if not isinstance(keys, list):
        return None
    process = saved.get("process")
    return SavedIds(frozenset(key for key in keys if isinstance(key, str)),
                    process if isinstance(process, str) else None)


def save_ids(path, keys, process):
    """Writes the keys and the process mark atomically, and a `*` .gitignore beside them so
    the folder stays local."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    temporary = path.with_suffix(".json.tmp")
    saved = {"format": IDS_FORMAT, "process": process, "id_keys": sorted(keys)}
    temporary.write_text(json.dumps(saved), encoding="utf-8")
    os.replace(temporary, path)


def ids_detail(previous, current):
    """`같은 id 37/40 · 새 id 3`: how many of the last check's keys came back, and how many are new."""
    if not previous:
        return f"지난 id 없음 · 이번 id {len(current)}"
    same = len(previous & current)
    return f"같은 id {same}/{len(previous)} · 새 id {len(current - previous)}"
