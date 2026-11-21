"""The settings line Deploy App's MDM page copies, version 1

The same line for every MDM, so the vector in tests/data is shared with Deploy App.
"""

import datetime
from pathlib import Path

import pytest

from odmdm_connector.settings import Settings, SettingsError, is_line, parse

VECTOR = (Path(__file__).parent / "data" / "settings-line-v1.txt").read_text(encoding="utf-8")
CHALLENGE = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
BEFORE = datetime.datetime(2026, 11, 21, 12, 0, tzinfo=datetime.UTC)
CLOSES = datetime.datetime(2026, 11, 21, 14, 0, tzinfo=datetime.UTC)


def test_the_shared_vector() -> None:
    assert VECTOR.split() == ["ODMDM1", "https://unit.example.fi/scep", CHALLENGE, "2026-11-21T14:00:00Z"]
    assert parse(VECTOR, BEFORE) == Settings("https://unit.example.fi/scep", CHALLENGE, CLOSES)


@pytest.mark.parametrize("around", ["  {}  ", "\n{}\r\n", "\t{}\n\n"])
def test_whitespace_and_line_breaks_around_it_are_ignored(around: str) -> None:
    assert parse(around.format(VECTOR.strip()), BEFORE).challenge == CHALLENGE


def test_the_challenge_never_shows_in_its_repr() -> None:
    assert CHALLENGE not in repr(parse(VECTOR, BEFORE))


@pytest.mark.parametrize(
    ("line", "says"),
    [
        ("", "not a settings line"),
        ("ODMDM2 https://unit.example.fi/scep " + CHALLENGE + " 2026-11-21T14:00:00Z", "not a settings line"),
        ("odmdm1 https://unit.example.fi/scep " + CHALLENGE + " 2026-11-21T14:00:00Z", "not a settings line"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE, "not a settings line"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE + " 2026-11-21T14:00:00Z OTTER1", "not a settings line"),
        ("ODMDM1 http://unit.example.fi/scep " + CHALLENGE + " 2026-11-21T14:00:00Z", "must start with https://"),
        ("ODMDM1 https:///scep " + CHALLENGE + " 2026-11-21T14:00:00Z", "must start with https://"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE.upper() + " 2026-11-21T14:00:00Z", "32 lowercase hex"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE[:-1] + " 2026-11-21T14:00:00Z", "32 lowercase hex"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE[:-1] + "g 2026-11-21T14:00:00Z", "32 lowercase hex"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE + " 2026-11-21T14:00:00", "is not YYYY-MM-DDTHH:MM:SSZ"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE + " 2026-11-21 14:00:00Z", "not a settings line"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE + " 2026-11-31T14:00:00Z", "is not YYYY-MM-DDTHH:MM:SSZ"),
        ("ODMDM1 https://unit.example.fi/scep " + CHALLENGE + " 2026-11-21T14:00:00+00:00", "is not YYYY-MM-DD"),
    ],
)
def test_a_line_that_cannot_be_used_says_why(line: str, says: str) -> None:
    with pytest.raises(SettingsError, match=says) as raised:
        parse(line, BEFORE)
    assert CHALLENGE[:-1] not in str(raised.value), "the challenge is never in a message"


@pytest.mark.parametrize("now", [CLOSES, CLOSES + datetime.timedelta(seconds=1)])
def test_a_closed_window_is_refused(now: datetime.datetime) -> None:
    with pytest.raises(SettingsError, match="closed at 2026-11-21T14:00:00Z: open a new one in Deploy App"):
        parse(VECTOR, now)


@pytest.mark.parametrize(
    ("value", "line"),
    [
        (VECTOR, True),
        ("  ODMDM2 x", True),
        ("odmdm1 x", True),
        (VECTOR.split(None, 1)[1], True),
        (CHALLENGE, False),
        ("  " + CHALLENGE + "\n", False),
        ("", False),
    ],
)
def test_a_line_is_told_from_a_bare_challenge(value: str, line: bool) -> None:
    assert is_line(value) is line
