import re

import pytest

from agent import survey_codes
from agent.git_state import CodeState
from agent.hypotheses import HYPOTHESES, judge_all
from agent.survey_block import check_chars, explain, parse_block, render_block
from agent.survey_facts import Labels
from survey_support import capture, make_session, window

NO_LABELS = Labels({}, {})


def block(loaded=None, labels=NO_LABELS, now=None):
    loaded = loaded or make_session()
    return render_block(loaded, judge_all(loaded, labels), labels, now)


def body(line):
    return line.rpartition(" ·")[0]


def test_header_and_four_hypotheses_per_line():
    lines = block()
    assert body(lines[0]) == "[전달] v2 · abc1234 · 변경없음 · SYS · 캡처2 · 32분"
    items = body(lines[1]).split("   ")
    assert [item.split(" ")[0] for item in items] == ["H01", "H02", "H03", "H04"]
    assert items[0] == "H01 WINFORMS(16/16)"
    assert body(lines[5]) .startswith("H17 ") and len(body(lines[5]).split("   ")) == 2
    assert body(lines[6]) == "C01 ? MAIN/APP/OK" and body(lines[7]) == "C02 ? DIALOG/SYSTEM/OK"
    assert all(re.fullmatch(r".+ ·[0-9A-Z]{2}", line) for line in lines)


def test_closed_hypotheses_leave_the_block(monkeypatch):
    from agent import hypotheses
    closed = tuple(h if h.id != "H02" else hypotheses.replace(h, status="closed") for h in HYPOTHESES)
    monkeypatch.setattr(hypotheses, "HYPOTHESES", closed)
    assert "H02 " not in " ".join(block()) and "H01 " in body(block()[1])


def test_block_never_exceeds_fifteen_lines():
    loaded = make_session([capture(n, window(kind="MAIN" if n % 2 else "DIALOG")) for n in range(1, 41)])
    lines = block(loaded)
    assert len(lines) == survey_codes.BLOCK_MAX_LINES
    assert body(lines[-1]).startswith("… 외 ")


def test_grouping_then_dropping_order():
    grouped = make_session([capture(1, window(kind="DIALOG", rel="OTHER")),
                            *[capture(n) for n in range(2, 7)],
                            *[capture(n, window(f"W{n}", kind="OWNED" if n % 2 else "DIALOG")) for n in range(7, 13)]])
    lines = [body(line) for line in block(grouped)]
    assert "C02~C06 ? MAIN/APP/OK" in lines and "C07 ? OWNED/APP/OK" in lines and not any("외" in l for l in lines)
    labels = Labels({f"C{n:02d}": "ERROR_DIALOG" for n in range(1, 15, 2)}, {})
    alternating = make_session([capture(n, window(kind="DIALOG", rel="SYSTEM") if n % 2 else window())
                                for n in range(1, 15)])
    lines = [body(line) for line in block(alternating, labels)]
    captures = [l for l in lines if l.startswith("C")]
    assert [l.split(" ")[0] for l in captures] == ["C01", "C02", "C03", "C05", "C07", "C09", "C11", "C13"]
    assert lines[-1] == "… 외 6" and len(lines) <= 15


def test_labelled_captures_are_trimmed_from_the_end_after_the_droppable_ones():
    loaded = make_session([capture(n, window(kind="MAIN" if n % 2 else "DIALOG")) for n in range(1, 41)])
    labels = Labels({f"C{n:02d}": "ERROR_DIALOG" for n in range(1, 41)}, {})
    lines = [body(line) for line in block(loaded, labels)]
    assert len(lines) == 15 and lines[-1] == "… 외 32"
    assert [l.split(" ")[0] for l in lines[6:-1]] == [f"C{n:02d}" for n in range(1, 9)]


def test_a_hung_main_capture_is_not_dropped_first():
    caps = [capture(n, window(kind="DIALOG", rel="SYSTEM") if n % 2 else window()) for n in range(1, 14)]
    caps.append(capture(14, window(read="UNRESPONSIVE")))
    labels = Labels({f"C{n:02d}": "ERROR_DIALOG" for n in range(1, 15, 2)}, {})
    lines = [body(line) for line in block(make_session(caps), labels)]
    assert "C14 ? MAIN/APP/HUNG" in lines and lines[-1] == "… 외 6"


def test_captures_are_grouped_only_when_lines_run_short():
    lines = [body(line) for line in block(make_session([capture(n) for n in range(1, 4)]))]
    assert lines[-3:] == ["C01 ? MAIN/APP/OK", "C02 ? MAIN/APP/OK", "C03 ? MAIN/APP/OK"]


def test_check_chars_formula():
    import zlib
    crc = zlib.crc32("H01 DELPHI(7/10)".encode("utf-8"))
    alphabet = survey_codes.CHECK_ALPHABET
    assert check_chars("H01 DELPHI(7/10)") == alphabet[crc % 32] + alphabet[(crc // 32) % 32]


def test_changed_after_session_shows_both_values():
    head = body(block(now=CodeState("abc1234", ("a.py", "b.py")))[0])
    assert "· 변경있음 0→2 ·" in head and "· abc1234 ·" in head
    moved = body(block(make_session(changed=1), now=CodeState("fff0000", ("a.py",)))[0])
    assert "abc1234→fff0000" in moved and "변경있음 1 ·" in moved


def test_same_state_gives_identical_block():
    assert block(now=CodeState("abc1234", ())) == block() == block()
    assert body(block(make_session(head=None))[0]).split(" · ")[1] == "커밋?"


def allowed_words(loaded):
    codes = {code for h in HYPOTHESES for code in h.codes} | {h.id for h in HYPOTHESES}
    for name in ("SCENES", "KINDS", "RELS", "STATES", "READS", "SHOTS", "SLOWS", "APP_SEEN", "FAILURES"):
        codes |= set(getattr(survey_codes, name))
    codes |= {"SKIPPED", "ERROR", "[전달]", "변경없음", "변경있음", "커밋?", "…", "외", "비전", loaded.session.system,
              loaded.session.head}
    return codes


NUMERIC = re.compile(r"\d+|v\d+|\d+분|캡처\d+|\d+(\.\d+)?s|pre\d+|[IVT]\d+|\d+x\d+@[\d.]+|C\d{2}|[0-9A-Z]{2}")


def test_every_word_is_allowed():
    loaded = make_session(system="가 나")
    labels = Labels({"C01": "LOGIN"}, {"H12": "SOME"})
    lines = render_block(loaded, judge_all(loaded, labels), labels, CodeState("fff0000", ("x",)))
    allowed = allowed_words(loaded) | {"fff0000", "가", "나"}
    for line in lines:
        for word in re.split(r"[\s·()/~→]+", line):
            assert not word or word in allowed or NUMERIC.fullmatch(word), (word, line)


def test_one_changed_character_is_caught_on_that_line_only():
    lines = block()
    copied = list(lines)
    copied[2] = copied[2].replace("H05", "H06", 1)
    parsed = parse_block("메모\n\n" + "\n".join(f"  {line}  " for line in copied) + "\n\n뒤 글")
    assert [line.ok for line in parsed] == [i != 2 for i in range(len(lines))]
    assert all(line.ok for line in parse_block("```\n" + "\n".join(lines) + "\n```\n"))
    assert len(parse_block("```\n" + "\n".join(lines) + "\n```\n")) == len(lines)


def test_explain_gives_meaning_and_next_and_marks_unknown():
    lines = block(labels=Labels({"C02": "ERROR_DIALOG"}, {}))
    text = "\n".join(lines + ["H99 FOO   H01 BANANA ·" + check_chars("H99 FOO   H01 BANANA"),
                              "C09 SPLASH TOP/APP/OK ·" + check_chars("C09 SPLASH TOP/APP/OK")])
    out = explain(parse_block(text))
    joined = "\n".join(out)
    h01 = next(h for h in HYPOTHESES if h.id == "H01")
    assert h01.next["WINFORMS"] in joined
    assert "오류를 알리는 창" in joined and "Win32 대화상자(#32770) 창" in joined
    assert "H99 FOO: 모르는 코드" in joined and "BANANA" in joined
    assert any("SPLASH 모르는 코드" in line for line in out)
    assert not any(line.startswith("오타?") for line in out)
    h09 = next(line for line in out if line.startswith("H09"))
    assert next(h for h in HYPOTHESES if h.id == "H09").next["WxH@S"] in h09


def test_explain_marks_typo_and_version():
    lines = block()
    lines[0] = lines[0].replace("v2", "v1")
    lines[3] = lines[3][:-1] + ("A" if lines[3][-1] != "A" else "B")
    out = explain(parse_block("\n".join(lines)))
    assert out[0].startswith("오타? 머리:")
    assert any("블록 버전 v1, 지금 코드 v2" in line for line in out)
    assert sum(line.startswith("오타?") for line in out) == 2


def test_explain_skipped_gives_reason_meaning():
    loaded = make_session(processes=(), app_seen="NOT_SEEN")
    out = explain(parse_block("\n".join(block(loaded))))
    assert any(line.startswith("H01 ") and "앱 프로세스를 보지 못함" in line for line in out)


def test_explain_skipped_without_reason_is_not_an_unknown_code():
    lines = explain(parse_block("[전달] v2 · x ·00\nH10 SKIPPED ·00"))
    h10 = next(line for line in lines if "H10" in line)
    assert "모르는 코드" not in h10 and "건너뜀" in h10


def test_check_chars_ignore_spacing():
    assert check_chars("H01  DELPHI") == check_chars("H01 DELPHI") == check_chars(" H01 DELPHI ")
    assert check_chars("H01 DELPHI") != check_chars("H01 DELPH1")
    assert all(ch in survey_codes.CHECK_ALPHABET for ch in check_chars("아무 글"))


@pytest.mark.parametrize("text", ["", "no block here", "[전달]"])
def test_parse_block_edges(text):
    parsed = parse_block(text)
    assert len(parsed) == (1 if text == "[전달]" else 0)
    assert all(not line.ok for line in parsed)
