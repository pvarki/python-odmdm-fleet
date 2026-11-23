"""A pretend MDM for odmdm-connector, its phones in memory

The starting point for a plugin: every method of the contract, odmdm_connector.mdm.Mdm, with
nothing behind it. A real plugin talks to its MDM's API where this changes a list. Each command
starts afresh, so a name given here is gone by the next command.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from typing import ClassVar

from odmdm_connector.mdm import SET_CHALLENGE, Field, MdmError, Phone, State, Step
from odmdm_connector.settings import Settings

#: The states ask_again without ids picks, as the contract says
AGAIN = (State.NOT_ASKED, State.REFUSED, State.UNREACHABLE, State.FAILED)


class DemoMdm:
    """A pretend MDM with three phones, which cannot be given a challenge by API"""

    # The contract version this plugin was written for
    API_VERSION: ClassVar[int] = 1
    title: ClassVar[str] = "Demo MDM"
    fields: ClassVar[tuple[Field, ...]] = (
        # Named after the deployment: the connector fills {deployment} in, golden-monkey for golden-monkey.example.fi
        Field("group", "Group", default="{deployment} DeployApp", help="The group phones enrol into"),
    )
    missing: ClassVar[Mapping[str, str]] = {
        SET_CHALLENGE: (
            "The demo MDM takes no challenge by API: paste it in its console under Certificates > SCEP > Challenge"
        ),
    }

    def __init__(self, values: Mapping[str, str], refuse_after: int | None = None) -> None:
        """refuse_after: for tests, the pretend MDM refuses to name more phones than that"""
        self.group = values.get("group", "")
        self.refuse_after = refuse_after
        self.found = [
            Phone("d1", "", "Pretend Phone 7", "2026-11-01T09:00:00+00:00", State.NEEDS_NAME),
            Phone("d2", "", "Pretend Phone 7", "2026-11-01T09:05:00+00:00", State.NEEDS_NAME),
            Phone("d3", "OTTER1", "Pretend Tablet 2", "2026-11-01T09:10:00+00:00", State.READY),
        ]

    def _known(self, ids: Sequence[str]) -> None:
        unknown = [phone for phone in ids if phone not in {each.id for each in self.found}]
        if unknown:
            raise MdmError(f"Phone {unknown[0]} is not in the group {self.group}")

    def _waiting(self, phone: str, callsign: str) -> None:
        self.found = [
            replace(each, callsign=callsign, state=State.WAITING) if each.id == phone else each for each in self.found
        ]

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        yield Step(f"Group {self.group} would enrol at {window.scep_url}", True)
        if not dry_run:
            yield Step("Certificate template stated", True)

    def set_challenge(self, window: Settings) -> None:
        raise MdmError(self.missing[SET_CHALLENGE])

    def phones(self) -> list[Phone]:
        return list(self.found)

    def name(self, names: Mapping[str, str]) -> list[str]:
        self._known(list(names))
        named: dict[str, str] = {}
        lines: list[str] = []
        for phone, callsign in names.items():
            if self.refuse_after is not None and len(named) >= self.refuse_after:
                raise MdmError(f"The demo MDM refused to name phone {phone}", named, lines)
            self._waiting(phone, callsign)
            named[phone] = callsign
            lines.append(f"Phone {phone} is {callsign}")
        return lines

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        self._known(ids)
        again = [each for each in self.found if each.id in ids or (not ids and each.callsign and each.state in AGAIN)]
        for each in again:
            self._waiting(each.id, each.callsign)
        return [f"Phone {each.id}: certificate asked for again" for each in again]
