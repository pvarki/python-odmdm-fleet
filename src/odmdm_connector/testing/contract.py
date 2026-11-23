"""The checks every MDM plugin must pass, for its own tests

Each check takes a factory and raises AssertionError saying what is wrong. With pytest::

    import pytest
    from odmdm_connector.testing import contract

    @pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
    def test_the_contract(check: contract.Check) -> None:
        check(my_factory)

The factory answers a configured MDM whose group holds at least two phones without a name. Given
refuse_after, that MDM refuses to name any phone after that many, as an MDM failing half-way
through a batch would. Plain asserts are not used, so the checks hold under python -O too.
"""

import datetime
import re
from collections.abc import Callable
from typing import Protocol

from ..mdm import (
    API_VERSION,
    ASK_AGAIN,
    CAPABILITIES,
    END,
    SEND_POLICY,
    SET_CHALLENGE,
    SETUP,
    Mdm,
    MdmError,
    Phone,
    State,
    Step,
)
from ..settings import Settings

#: An enrolment window to hand the MDM; the challenge is not a real one
WINDOW = Settings(
    "https://unit.example.fi/scep", "contract-check-challenge", datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
)
#: A key has to fit an environment variable name, ODMDM_<KEY>
KEY = re.compile(r"[a-z][a-z0-9_]*")


class Factory(Protocol):
    """Makes the MDM under test"""

    def __call__(self, refuse_after: int | None = None) -> Mdm: ...


Check = Callable[[Factory], None]


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _unnamed(mdm: Mdm) -> list[str]:
    ids = [phone.id for phone in mdm.phones() if not phone.callsign]
    _require(len(ids) >= 2, "the factory's MDM must hold at least two phones without a name")
    return ids


def _lines(lines: list[str], call: str) -> None:
    _require(isinstance(lines, list), f"{call} must answer a list of lines")
    _require(all(isinstance(line, str) and line for line in lines), f"{call}'s lines must be non-empty strings")


def _raises(call: Callable[[], object], message: str) -> None:
    try:
        call()
    except MdmError:
        return
    raise AssertionError(message)


def _lacking(mdm: Mdm, capability: str, call: Callable[[], object]) -> bool:
    """Whether the MDM lacks the capability, having checked that calling it then raises its sentence"""
    if capability not in mdm.missing:
        return False
    try:
        call()
    except MdmError as exc:
        _require(str(exc) == mdm.missing[capability], f"{capability}, lacking, must raise MdmError with its sentence")
        return True
    raise AssertionError(f"{capability} is listed as missing but did not raise MdmError")


def declares_the_contract_version(factory: Factory) -> None:
    mdm = factory()
    _require(mdm.API_VERSION == API_VERSION, f"API_VERSION must be {API_VERSION}")
    _require(isinstance(mdm.title, str) and mdm.title, "title must be the MDM's name")


def declares_its_fields(factory: Factory) -> None:
    fields = factory().fields
    _require(isinstance(fields, tuple), "fields must be a tuple of Field")
    keys = [item.key for item in fields]
    labels = [item.label for item in fields]
    _require(all(KEY.fullmatch(key) for key in keys), "a field's key is lower case letters, digits and _")
    _require(len(set(keys)) == len(keys), "field keys must be unique")
    _require(all(labels) and len(set(labels)) == len(labels), "field labels must be given and unique")


def says_what_it_lacks(factory: Factory) -> None:
    missing = factory().missing
    _require(set(missing) <= set(CAPABILITIES), f"missing names only capabilities among {', '.join(CAPABILITIES)}")
    _require(all(isinstance(text, str) and text for text in missing.values()), "each lacking capability has a sentence")


def lists_its_phones(factory: Factory) -> None:
    found = factory().phones()
    _require(isinstance(found, list), "phones must answer a list")
    for phone in found:
        _require(isinstance(phone, Phone), "phones must answer Phone objects")
        _require(isinstance(phone.id, str) and phone.id, "a phone's id is a non-empty string")
        _require(isinstance(phone.state, State), "a phone's state is a State")
        _require(isinstance(phone.callsign, str) and isinstance(phone.detail, str), "callsign and detail are strings")
        try:
            datetime.datetime.fromisoformat(phone.joined)
        except ValueError as exc:
            raise AssertionError(f"phone {phone.id} joined {phone.joined!r}, not an ISO time") from exc


def names_phones(factory: Factory) -> None:
    mdm = factory()
    first = _unnamed(mdm)[0]
    _lines(mdm.name({first: "CONTRACT1"}), "name")
    callsigns = {phone.id: phone.callsign for phone in mdm.phones()}
    _require(callsigns.get(first) == "CONTRACT1", "a phone named must show its callsign in phones")


def refuses_an_unknown_id(factory: Factory) -> None:
    mdm = factory()
    before = mdm.phones()
    _raises(lambda: mdm.name({"no-such-phone": "CONTRACT9"}), "name of an id not in the group must raise MdmError")
    _raises(lambda: mdm.ask_again(["no-such-phone"]), "ask_again of an id not in the group must raise MdmError")
    _require(mdm.phones() == before, "an id not in the group must change nothing")


def says_what_a_batch_stopped_half_way_named(factory: Factory) -> None:
    mdm = factory(refuse_after=1)
    first, second = _unnamed(mdm)[:2]
    try:
        mdm.name({first: "CONTRACT1", second: "CONTRACT2"})
    except MdmError as exc:
        _require(exc.named == {first: "CONTRACT1"}, f"MdmError.named must be the phones named, not {exc.named}")
        return
    raise AssertionError("name must raise MdmError when the MDM refuses a phone half-way")


def asks_again(factory: Factory) -> None:
    mdm = factory()
    if not _lacking(mdm, ASK_AGAIN, lambda: mdm.ask_again([])):
        _lines(mdm.ask_again([]), "ask_again")


def sets_the_challenge(factory: Factory) -> None:
    mdm = factory()
    if not _lacking(mdm, SET_CHALLENGE, lambda: mdm.set_challenge(WINDOW)):
        mdm.set_challenge(WINDOW)


def sets_up(factory: Factory) -> None:
    mdm = factory()
    if not _lacking(mdm, SETUP, lambda: list(mdm.setup(WINDOW, dry_run=True))):
        steps = list(mdm.setup(WINDOW, dry_run=True))
        _require(steps and all(isinstance(step, Step) for step in steps), "setup must report Steps")


def sends_the_policy_again(factory: Factory) -> None:
    mdm = factory()
    if SETUP in mdm.missing or _lacking(mdm, SEND_POLICY, lambda: list(mdm.setup(WINDOW, force=True, dry_run=True))):
        return
    steps = list(mdm.setup(WINDOW, force=True, dry_run=True))
    _require(steps and all(isinstance(step, Step) for step in steps), "setup with force must report Steps")


def ends(factory: Factory) -> None:
    """Ends the Deploy App twice over, its phones and their names left as they were; or says it cannot"""
    mdm = factory()
    if _lacking(mdm, END, lambda: mdm.end(WINDOW)):
        return
    _require(callable(getattr(mdm, "end", None)), "end must be a method, or END listed in missing with its sentence")
    before = [(phone.id, phone.callsign) for phone in mdm.phones()]
    _lines(mdm.end(WINDOW), "end")
    again = mdm.end(WINDOW)
    _lines(again, "end, called again")
    _require(again, "end, called again, must say what it found left")
    after = [(phone.id, phone.callsign) for phone in mdm.phones()]
    _require(after == before, "end must leave the group's phones and their names as they were")


CHECKS: tuple[Check, ...] = (
    declares_the_contract_version,
    declares_its_fields,
    says_what_it_lacks,
    lists_its_phones,
    names_phones,
    refuses_an_unknown_id,
    says_what_a_batch_stopped_half_way_named,
    asks_again,
    sets_the_challenge,
    sets_up,
    sends_the_policy_again,
    ends,
)
