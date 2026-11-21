"""The naming rules, without an MDM"""

import pytest

from odmdm_connector import core
from odmdm_connector.mdm import Phone, State


def _phone(phone: str, callsign: str = "") -> Phone:
    return Phone(phone, callsign, "a phone", "2026-11-01T08:00:00Z", State.READY if callsign else State.NEEDS_NAME)


def test_ids_are_whatever_the_mdm_calls_its_phones() -> None:
    """Opaque strings, split at the first ="""
    assert core.pairs(["31=OTTER4", "a-b=OTTER5"]) == [("31", "OTTER4"), ("a-b", "OTTER5")]


@pytest.mark.parametrize("value", ["OTTER4", "=OTTER4", "31="])
def test_a_pair_without_both_halves_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="is not ID=CALLSIGN"):
        core.pairs([value])


def test_numbering_goes_on_from_the_highest_in_use() -> None:
    """In the order the phones are given, skipping the named ones"""
    phones = [_phone("a", "otter7"), _phone("b"), _phone("c", "KETTU9"), _phone("d")]

    assert core.numbered(phones, "OTTER", None) == [("b", "OTTER8"), ("d", "OTTER9")]
    assert core.numbered(phones, "OTTER", 20) == [("b", "OTTER20"), ("d", "OTTER21")]


@pytest.mark.parametrize(
    ("wanted", "says"),
    [
        ([("a", "OTTER4")], ""),
        ([("x", "OTTER4")], "phone x is not in the group"),
        ([("a", "OTTER4"), ("a", "OTTER5")], "phone a is named twice"),
        ([("a", "-OTTER")], "-OTTER: a callsign is 3-32 letters"),
        ([("a", "OTTER4"), ("b", "otter4")], "otter4 is given twice"),
        ([("a", "otter9")], "otter9 already belongs to phone c"),
        ([("c", "otter9")], ""),
    ],
)
def test_a_batch_is_refused_whole(wanted: list[tuple[str, str]], says: str) -> None:
    """Callsigns compared without case; a phone may keep its own"""
    phones = [_phone("a"), _phone("b"), _phone("c", "OTTER9")]

    refused = core.refusal(phones, wanted)

    assert refused.startswith(says) if says else not refused
