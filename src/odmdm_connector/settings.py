"""The settings line Deploy App's MDM page copies for the connector

Version 1 is four fields separated by whitespace::

    ODMDM1 <SCEP URL, https> <challenge, 32 lowercase hex> <window closes, YYYY-MM-DDTHH:MM:SSZ>

It is the same for every MDM and carries no names. The challenge is a secret: nothing here puts it
in a message.
"""

import datetime
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

MARK = "ODMDM1"
CHALLENGE = re.compile(r"[0-9a-f]{32}")
CLOSES = "%Y-%m-%dT%H:%M:%SZ"


class SettingsError(ValueError):
    """The line is not one the connector can use"""


@dataclass(frozen=True)
class Settings:
    """What one enrolment window gives the MDM"""

    scep_url: str
    challenge: str = field(repr=False)
    closes: datetime.datetime


def is_line(value: str) -> bool:
    """Whether the value is meant as a settings line rather than a bare challenge

    A challenge is one word, so a value with whitespace inside it, or with the mark in any case,
    is meant as a line.
    """
    return len(value.split()) > 1 or value.strip().upper().startswith("ODMDM")


def parse(line: str, now: datetime.datetime) -> Settings:
    """The line's values, or SettingsError saying why not

    now is the current time with its time zone, given by the caller.
    """
    fields = line.split()
    if len(fields) != 4 or fields[0] != MARK:
        raise SettingsError(f"This is not a settings line: it has four parts and starts with {MARK}.")
    _, url, challenge, closes = fields
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SettingsError("The SCEP URL in the settings line must start with https://.")
    if not CHALLENGE.fullmatch(challenge):
        raise SettingsError("The challenge in the settings line must be 32 lowercase hex characters.")
    try:
        when = datetime.datetime.strptime(closes, CLOSES).replace(tzinfo=datetime.UTC)
    except ValueError as exc:
        raise SettingsError(f"The closing time {closes!r} is not YYYY-MM-DDTHH:MM:SSZ.") from exc
    if when <= now:
        raise SettingsError(f"The enrolment window closed at {closes}: open a new one in Deploy App.")
    return Settings(scep_url=url, challenge=challenge, closes=when)
