import re

from agent.survey_codes import (APP_SEEN, CHECK_ALPHABET, FAILURES, KINDS, MEANINGS, READS, RELS, SCENES,
                                SHOTS, SIZES, SLOWS, STATES)

GROUPS = {"scene": SCENES, "kind": KINDS, "rel": RELS, "state": STATES, "read": READS, "size": SIZES,
          "shot": SHOTS, "slow": SLOWS, "app_seen": APP_SEEN, "failure": FAILURES}


def test_every_code_has_a_meaning():
    for group, codes in GROUPS.items():
        for code in codes:
            assert MEANINGS[f"{group}:{code}"]


def test_every_meaning_belongs_to_a_code():
    expected = {f"{group}:{code}" for group, codes in GROUPS.items() for code in codes}
    assert set(MEANINGS) == expected


def test_meanings_hold_no_app_word():
    assert not any(re.search(r"\bMES\b", text) for text in MEANINGS.values())


def test_check_alphabet_has_32_unambiguous_characters():
    assert len(CHECK_ALPHABET) == len(set(CHECK_ALPHABET)) == 32
    assert not set("ILOU") & set(CHECK_ALPHABET)
