"""The rules for naming phones, the same whatever the MDM"""

import re
from collections.abc import Sequence

from .mdm import Phone, State

#: The deployment's rule for a device callsign, checked here only so a typo is refused now rather
#: than as a failed certificate twenty minutes later. The deployment still decides.
CALLSIGN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,30}[A-Za-z0-9]")
#: The states the plugin's words are said beside: the certificate was never asked for, was refused,
#: could not be reached or failed. Waiting for it or having it, the state alone is true: words from
#: an earlier failure would sit beside a request that has not failed.
EXPLAINED = (State.NO_REQUEST, State.NOT_ASKED, State.REFUSED, State.UNREACHABLE, State.FAILED)


def pairs(values: Sequence[str]) -> list[tuple[str, str]]:
    """ID=CALLSIGN arguments, as ids and callsigns; ValueError saying which one is not"""
    found = []
    for value in values:
        phone, _, callsign = value.partition("=")
        if not phone or not callsign:
            raise ValueError(f"{value!r} is not ID=CALLSIGN, the id being what phones prints")
        found.append((phone, callsign))
    return found


def next_number(phones: Sequence[Phone], prefix: str) -> int:
    """One above the highest PREFIX<n> in use, compared without case; 1 when none is"""
    used = (re.fullmatch(re.escape(prefix) + r"(\d+)", phone.callsign, re.IGNORECASE) for phone in phones)
    return max((int(match.group(1)) for match in used if match), default=0) + 1


def numbered(phones: Sequence[Phone], prefix: str, start: int | None) -> list[tuple[str, str]]:
    """PREFIX<n> for every phone without a name, in the order given

    Numbered from start, else from one above the highest PREFIX<n> already in use.
    """
    if start is None:
        start = next_number(phones, prefix)
    waiting = [phone.id for phone in phones if not phone.callsign]
    return [(phone, f"{prefix}{start + n}") for n, phone in enumerate(waiting)]


def refusal(phones: Sequence[Phone], wanted: Sequence[tuple[str, str]]) -> str:
    """Why these names cannot all be given, or empty when they can

    Callsigns are compared without case, as the deployment compares them.
    """
    found = {phone.id: phone for phone in phones}
    ids: set[str] = set()
    callsigns: set[str] = set()
    for phone, callsign in wanted:
        if phone not in found:
            return f"phone {phone} is not in the group"
        if phone in ids:
            return f"phone {phone} is named twice"
        if not CALLSIGN.fullmatch(callsign):
            return f"{callsign}: a callsign is 3-32 letters, digits or hyphens, and does not start or end with a hyphen"
        if callsign.lower() in callsigns:
            return f"{callsign} is given twice"
        owner = next((p.id for p in phones if p.id != phone and p.callsign.lower() == callsign.lower()), "")
        if owner:
            return f"{callsign} already belongs to phone {owner}"
        ids.add(phone)
        callsigns.add(callsign.lower())
    return ""


def said(phone: Phone) -> str:
    """Where the phone's certificate is, as the operator reads it: the state, and the words EXPLAINED allows"""
    return f"{phone.state.value}: {phone.detail}" if phone.detail and phone.state in EXPLAINED else phone.state.value
