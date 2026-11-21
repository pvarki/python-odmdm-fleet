"""FakeMdm: an MDM in memory, implementing the whole contract, for tests"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from typing import ClassVar

from ..mdm import API_VERSION, ASK_AGAIN, SET_CHALLENGE, SETUP, Field, MdmError, Phone, State, Step
from ..settings import Settings

#: The group's phones when a test gives none: two waiting for a name, one with its certificate
PHONES = (
    Phone("a1", "", "Pretend Phone 7", "2026-11-01T09:00:00+00:00", State.NEEDS_NAME),
    Phone("a2", "", "Pretend Phone 7", "2026-11-01T09:05:00+00:00", State.NEEDS_NAME),
    Phone("a3", "OTTER1", "Pretend Tablet 2", "2026-11-01T09:10:00+00:00", State.READY),
)

#: The states ask_again without ids picks, as the contract says
AGAIN = (State.NOT_ASKED, State.REFUSED, State.UNREACHABLE, State.FAILED)


class FakeMdm:
    """An MDM whose group is a list in memory

    calls records what it was asked, by method name. refuse_after makes name refuse the phone
    after that many were named, as an MDM failing half-way through a batch does. lacking() gives a
    class without some capabilities.
    """

    API_VERSION: ClassVar[int] = API_VERSION
    title: ClassVar[str] = "Fake MDM"
    fields: ClassVar[tuple[Field, ...]] = (
        Field("group", "Group", default="Deploy App", help="The group phones enrol into"),
        Field("password", "Password", secret=True, help="Read from the file ODMDM_PASSWORD_FILE names"),
    )
    missing: ClassVar[Mapping[str, str]] = {}

    def __init__(
        self, values: Mapping[str, str], phones: Sequence[Phone] = PHONES, refuse_after: int | None = None
    ) -> None:
        self.values = dict(values)
        self.group = list(phones)
        self.refuse_after = refuse_after
        self.calls: list[str] = []
        #: The challenge last given, kept so a test can see it arrived
        self.challenge = ""

    @classmethod
    def lacking(cls, *capabilities: str) -> type["FakeMdm"]:
        """A FakeMdm class without these capabilities, each with a sentence saying so"""
        missing = {capability: f"The fake MDM cannot {capability}" for capability in capabilities}
        return type("LackingFakeMdm", (cls,), {"missing": missing})

    def _able(self, capability: str) -> None:
        self.calls.append(capability)
        if capability in self.missing:
            raise MdmError(self.missing[capability])

    def _ids(self, ids: Sequence[str]) -> None:
        known = {phone.id for phone in self.group}
        unknown = [phone for phone in ids if phone not in known]
        if unknown:
            raise MdmError(f"Phone {unknown[0]} is not in the group")

    def _waiting(self, phone: str, callsign: str | None = None) -> None:
        """The phone, under its new callsign if given, now waits for its certificate"""
        self.group = [
            replace(each, callsign=each.callsign if callsign is None else callsign, state=State.WAITING)
            if each.id == phone
            else each
            for each in self.group
        ]

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        self._able(SETUP)
        yield Step(f"Group {self.values.get('group', 'Deploy App')} enrols at {window.scep_url}", True)
        if dry_run:
            return
        yield Step("Certificate template stated", True, "forced" if force else "")

    def set_challenge(self, window: Settings) -> None:
        self._able(SET_CHALLENGE)
        self.challenge = window.challenge

    def phones(self) -> list[Phone]:
        self.calls.append("phones")
        return list(self.group)

    def name(self, names: Mapping[str, str]) -> list[str]:
        self.calls.append("name")
        self._ids(list(names))
        named: dict[str, str] = {}
        lines: list[str] = []
        for phone, callsign in names.items():
            if self.refuse_after is not None and len(named) >= self.refuse_after:
                raise MdmError(f"The fake MDM refused phone {phone}", named, lines)
            self._waiting(phone, callsign)
            named[phone] = callsign
            lines.append(f"Phone {phone} is {callsign}")
        return lines

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        self._able(ASK_AGAIN)
        self._ids(ids)
        again = list(ids) or [phone.id for phone in self.group if phone.callsign and phone.state in AGAIN]
        for phone in again:
            self._waiting(phone)
        return [f"Phone {phone}: certificate asked for again" for phone in again]
