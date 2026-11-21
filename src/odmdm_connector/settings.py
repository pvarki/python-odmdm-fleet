"""The settings line Deploy App's MDM page copies for the connector

Version 1 is four fields separated by whitespace::

    ODMDM1 <SCEP URL, https://<host>/<path>> <challenge, 32 lowercase hex> <window closes, YYYY-MM-DDTHH:MM:SSZ>

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
#: A plain DNS name: letters, digits and inner hyphens in labels separated by dots, at least two labels
HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
URL = (
    "The SCEP URL in the settings line must be https://<host>/<path>, with a plain host name and nothing else "
    "before the path."
)


class SettingsError(ValueError):
    """The line is not one the connector can use"""


@dataclass(frozen=True)
class Settings:
    """What one enrolment window gives the MDM

    closes is None for a bare challenge typed without its line, which says nothing of the window.
    """

    scep_url: str
    challenge: str = field(repr=False)
    closes: datetime.datetime | None = None


def is_line(value: str) -> bool:
    """Whether the value is meant as a settings line rather than a bare challenge

    A challenge is one word, so a value with whitespace inside it, or with the mark in any case,
    is meant as a line.
    """
    return len(value.split()) > 1 or value.strip().upper().startswith("ODMDM")


def _scep_url(url: str) -> str:
    """The URL with its host in lower case, if it is https://<host>/<path> and nothing else; else empty

    No user name, password, port, query or fragment, and no backslash anywhere: Python and a browser
    read a URL such as https://evil.example\\@unit.example.fi/scep as two different hosts, which is how a
    crafted line could pass for a known Deploy App. The host is a plain DNS name, so that no
    look-alike letter, percent sign, semicolon or control character makes a name that looks like
    a real one.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = parts.hostname or ""
    if (
        parts.scheme == "https"
        and HOST.fullmatch(host)
        and parts.netloc.lower() == host
        and parts.path.strip("/")
        and not any(mark in url for mark in "\\?#")
    ):
        return f"https://{host}{parts.path}"
    return ""


def parse(line: str, now: datetime.datetime) -> Settings:
    """The line's values, or SettingsError saying why not

    now is the current time with its time zone, given by the caller; a closing time in a message is
    in that time zone.
    """
    fields = line.split()
    if len(fields) != 4 or fields[0] != MARK:
        raise SettingsError(f"This is not a settings line: it has four parts and starts with {MARK}.")
    _, given, challenge, closes = fields
    url = _scep_url(given)
    if not url:
        raise SettingsError(URL)
    if not CHALLENGE.fullmatch(challenge):
        raise SettingsError("The challenge in the settings line must be 32 lowercase hex characters.")
    try:
        when = datetime.datetime.strptime(closes, CLOSES).replace(tzinfo=datetime.UTC)
    except ValueError as exc:
        raise SettingsError(f"The closing time {closes!r} is not YYYY-MM-DDTHH:MM:SSZ.") from exc
    if when <= now:
        at = when.astimezone(now.tzinfo).strftime("%H:%M")
        raise SettingsError(f"The enrolment window closed at {at}: open a new one in Deploy App.")
    return Settings(scep_url=url, challenge=challenge, closes=when)
