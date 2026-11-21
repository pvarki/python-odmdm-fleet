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


@pytest.mark.parametrize(
    "url",
    [
        "http://unit.example.fi/scep",
        "https:///scep",
        "https://unit.example.fi",
        "https://unit.example.fi/",
        "https://evil.example\\@unit.example.fi/scep",
        "https://unit.example.fi\\scep",
        "https://user:pass@unit.example.fi/scep",  # pragma: allowlist secret
        "https://user@unit.example.fi/scep",
        "https://unit.example.fi:8443/scep",
        "https://unit.example.fi:/scep",
        "https://unit.example.fi/scep?x=1",
        "https://unit.example.fi/scep?",
        "https://unit.example.fi/scep#x",
        "https://[unit.example.fi/scep",
        "ftp://unit.example.fi/scep",
        "https://un\u0456t.example.fi/scep",
        "https://\uff55nit.example.fi/scep",
        "https://unit.example.fi\u200b/scep",
        "https://unit%2eexample.fi/scep",
        "https://unit.example.fi;x/scep",
        "https://unit.example.fi\x7f/scep",
        "https://unit_1.example.fi/scep",
        "https://-unit.example.fi/scep",
        "https://unit-.example.fi/scep",
        "https://unit..example.fi/scep",
        "https://unit.example.fi./scep",
        "https://localhost/scep",
    ],
)
def test_a_scep_url_that_is_not_just_a_host_and_a_path_is_refused(url: str) -> None:
    line = f"ODMDM1 {url} {CHALLENGE} 2026-11-21T14:00:00Z"

    with pytest.raises(SettingsError) as raised:
        parse(line, BEFORE)

    assert str(raised.value) == (
        "The SCEP URL in the settings line must be https://<host>/<path>, with a plain host name and nothing else "
        "before the path."
    )


def test_a_host_in_capitals_is_the_same_host() -> None:
    assert (
        parse(VECTOR.replace("unit.example.fi", "Unit.Example.FI"), BEFORE).scep_url == "https://unit.example.fi/scep"
    )


@pytest.mark.parametrize("host", ["unit.example.fi", "a.b", "x-1.unit-2.example.fi", "192.0.2.10"])
def test_a_plain_host_is_taken(host: str) -> None:
    assert parse(VECTOR.replace("unit.example.fi", host), BEFORE).scep_url == f"https://{host}/scep"


@pytest.mark.parametrize(
    ("now", "at"),
    [
        (CLOSES, "14:00"),
        (CLOSES + datetime.timedelta(seconds=1), "14:00"),
        (CLOSES.astimezone(datetime.timezone(datetime.timedelta(hours=2))), "16:00"),
    ],
)
def test_a_closed_window_is_refused_at_the_operators_time(now: datetime.datetime, at: str) -> None:
    with pytest.raises(SettingsError, match=f"^The enrolment window closed at {at}: open a new one in Deploy App.$"):
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
