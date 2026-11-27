"""The transmission block: the few lines a person copies by hand from the company PC to home.

Only this module writes it, from codes, counts, the system name and the commit, so no screen
text has a place to go. Every line ends with ` ·` and two check characters (a CRC32 of the
line with its spacing folded) so home can find a hand-copying slip line by line. The same
session, labels and code state always give the same block.
"""

import re
import zlib
from typing import NamedTuple

from agent.hypotheses import HYPOTHESES
from agent.survey_codes import BLOCK_MAX_LINES, CHECK_ALPHABET, HYPOTHESES_PER_LINE, MEANINGS, SURVEY_VERSION
from agent.survey_facts import capture_codes

HEADER = "[전달]"
UNKNOWN_HEAD = "커밋?"
NO_CHANGE = "변경없음"
CHANGED = "변경있음"
DROPPED = "… 외"
SEPARATOR = " · "
HYPOTHESIS_GAP = "   "
CHECK_MARK = " ·"
DROPPABLE_SCENES = ("MAIN", "?")
MONITOR_CODE = re.compile(r"^\d+x\d+@[\d.]+$")
ITEM = re.compile(r"(H\d{2}) ([^\s(]+)(?:\(([^)]*)\))?")
CAPTURE_LINE = re.compile(r"^(C\d{2}(?:~C\d{2})?) (\S+) ([A-Z_]+)/([A-Z_]+)/([A-Z_]+)$")
VERSION = re.compile(r"^\[전달\] v(\d+)")
UNKNOWN = "모르는 코드"


class BlockLine(NamedTuple):
    text: str
    ok: bool


def check_chars(text: str) -> str:
    crc = zlib.crc32(" ".join(text.split()).encode("utf-8"))
    return CHECK_ALPHABET[crc % 32] + CHECK_ALPHABET[(crc // 32) % 32]


def _signed(text):
    return f"{text}{CHECK_MARK}{check_chars(text)}"


def _head_field(then, now):
    then_text = then or UNKNOWN_HEAD
    if now is None or now.head == then:
        return then_text
    return f"{then_text}→{now.head or UNKNOWN_HEAD}"


def _change_field(then, now):
    if now is not None and len(now.changed) != then:
        return f"{CHANGED} {then}→{len(now.changed)}"
    return NO_CHANGE if then == 0 else f"{CHANGED} {then}"


def _header(loaded, now):
    s = loaded.session
    return SEPARATOR.join((f"{HEADER} v{SURVEY_VERSION}", _head_field(s.head, now), _change_field(s.changed, now),
                           s.system, f"캡처{len(loaded.captures)}", f"{round(s.duration_s / 60)}분"))


def _hypothesis_lines(judged):
    items = [f"{h.id} {v.code}" + (f"({v.short})" if v.short else "") for h, v in judged if h.status == "open"]
    return [HYPOTHESIS_GAP.join(items[i:i + HYPOTHESES_PER_LINE]) for i in range(0, len(items), HYPOTHESES_PER_LINE)]


class _Item(NamedTuple):
    first: str
    last: str
    scene: str
    codes: tuple
    size: int

    def text(self):
        ids = self.first if self.size == 1 else f"{self.first}~{self.last}"
        return f"{ids} {self.scene} {'/'.join(self.codes)}"

    def droppable(self):
        return self.scene in DROPPABLE_SCENES and self.codes[2] == "OK"


def _items(loaded, labels):
    return [_Item(c.id, c.id, labels.captures.get(c.id, "?"), capture_codes(c), 1) for c in loaded.captures]


def _grouped(items):
    groups = []
    for item in items:
        last = groups[-1] if groups else None
        if last is not None and (last.scene, last.codes) == (item.scene, item.codes):
            groups[-1] = last._replace(last=item.last, size=last.size + 1)
        else:
            groups.append(item)
    return groups


def _fit(items, room):
    """Capture lines in at most `room` lines: as they are, then grouped, then trimmed."""
    if len(items) <= room:
        return [item.text() for item in items]
    items = _grouped(items)
    if len(items) <= room:
        return [item.text() for item in items]
    kept, dropped = list(items), 0
    for wanted in (True, False):
        for index in range(len(kept) - 1, -1, -1):
            if len(kept) <= room - 1:
                break
            if wanted is False or kept[index].droppable():
                dropped += kept.pop(index).size
    return [item.text() for item in kept] + [f"{DROPPED} {dropped}"]


def render_block(loaded, judged, labels, now=None) -> list:
    """The block lines, at most BLOCK_MAX_LINES. `now` is the code state when the block is
    made again with `--block`; a commit or change count that differs shows both values."""
    lines = [_header(loaded, now), *_hypothesis_lines(judged)]
    lines += _fit(_items(loaded, labels), BLOCK_MAX_LINES - len(lines))
    return [_signed(line) for line in lines[:BLOCK_MAX_LINES]]


def _split(text):
    body, mark, chars = text.rpartition(CHECK_MARK)
    if not mark or len(chars.strip()) != 2:
        return text, False
    return body, check_chars(body) == chars.strip().upper()


def parse_block(text: str) -> list:
    """The lines from the first line starting with `[전달]` to a blank line, a code fence or the end."""
    found, started = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if not started:
            started = line.startswith(HEADER)
        elif not line or line.startswith("```"):
            break
        if started:
            found.append(BlockLine(line, _split(line)[1]))
    return found


def _hypothesis_text(hid, code, short):
    hypothesis = next((h for h in HYPOTHESES if h.id == hid), None)
    shown = code + (f"({short})" if short else "")
    if hypothesis is None:
        return f"{hid} {shown}: {UNKNOWN}"
    head = f"{hid} {hypothesis.title} → {shown}"
    if code == "SKIPPED" and not short:
        return f"{head}: 판정하지 않음(점검을 건너뜀)"
    if code == "SKIPPED":
        return f"{head}: 판정하지 못함 · {MEANINGS.get(f'failure:{short}', f'{short} {UNKNOWN}')}"
    if code == "ERROR":
        return f"{head}: 판정 중 오류({short})"
    if code == "?":
        return f"{head}: 답 없음"
    key = "WxH@S" if hypothesis.id == "H09" and MONITOR_CODE.match(code) else code
    return f"{head}: {hypothesis.next.get(key, UNKNOWN)}"


def _capture_text(match):
    ids, scene, kind, rel, state = match.groups()
    parts = [MEANINGS.get(f"scene:{scene}", f"{scene} {UNKNOWN}"), MEANINGS.get(f"kind:{kind}", f"{kind} {UNKNOWN}"),
             MEANINGS.get(f"rel:{rel}", f"{rel} {UNKNOWN}"), MEANINGS.get(f"state:{state}", f"{state} {UNKNOWN}")]
    return f"{ids} {scene} {kind}/{rel}/{state}: " + " · ".join(parts)


def _explain_body(body):
    if body.startswith(HEADER):
        notes = [f"머리: {body}"]
        version = VERSION.match(body)
        if version is None:
            notes.append(f"버전을 읽지 못함: {UNKNOWN}")
        elif int(version.group(1)) != SURVEY_VERSION:
            notes.append(f"블록 버전 v{version.group(1)}, 지금 코드 v{SURVEY_VERSION}: 코드의 뜻이 다를 수 있음")
        return notes
    if body.startswith(DROPPED):
        return [f"{body}: 줄 수 때문에 뺀 캡처 수"]
    capture = CAPTURE_LINE.match(body)
    if capture:
        return [_capture_text(capture)]
    items = ITEM.findall(body)
    if items:
        return [_hypothesis_text(hid, code, short) for hid, code, short in items]
    return [f"{body}: 모르는 줄"]


def explain(lines: list) -> list:
    """Home's reading of a copied block: each line's meaning, with `오타?` on a line whose
    check characters do not match."""
    out = []
    for line in lines:
        body = _split(line.text)[0]
        notes = _explain_body(body)
        if not line.ok:
            notes = [f"오타? {notes[0]}", *notes[1:]]
        out.extend(notes)
    return out
