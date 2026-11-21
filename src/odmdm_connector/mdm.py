"""What the connector needs an MDM to be able to do: the one contract every MDM plugin implements

Standard library only, so a plugin depends on nothing but this package. A plugin is a class that
satisfies Mdm, published under the entry point group ``odmdm_connector.mdm``. The connector checks
the class's API_VERSION before it uses it.

Every sentence a plugin hands back, a Step's message, a line from name or ask_again, an MdmError,
is shown to the operator as it is. The SCEP challenge is never in one.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Protocol

from .settings import Settings

API_VERSION = 1

#: The capabilities an MDM may lack, as named in Mdm.missing
SETUP = "setup"
SET_CHALLENGE = "set_challenge"
ASK_AGAIN = "ask_again"
CAPABILITIES = (SETUP, SET_CHALLENGE, ASK_AGAIN)


class State(Enum):
    """Where a phone's certificate is, its value being the sentence shown to the operator"""

    NO_REQUEST = "no certificate requested"
    NEEDS_NAME = "waiting for a name"
    NOT_ASKED = "named, but its certificate was not asked for again"
    WAITING = "requested, waiting for the phone"
    READY = "has its certificate"
    REFUSED = "refused by Deploy App"
    UNREACHABLE = "could not reach Deploy App"
    #: A failure that is neither a refusal nor Deploy App out of reach
    FAILED = "failed"


@dataclass(frozen=True)
class Phone:
    """A phone in the MDM's group for one Deploy App"""

    #: The MDM's own id for it, opaque to the connector
    id: str
    #: Empty while nobody has named it
    callsign: str
    model: str
    #: When it joined, as an ISO time
    joined: str
    state: State
    #: The plugin's own extra words for the operator, shown after the state; may be empty
    detail: str = ""


@dataclass(frozen=True)
class Field:
    """A setup value a plugin needs, such as its MDM's address

    A secret is never taken from an option or a plain environment variable, only from a file.
    """

    key: str
    label: str
    secret: bool = False
    default: str = ""
    help: str = ""


@dataclass(frozen=True)
class Step:
    """One step of setup, as it is reported"""

    label: str
    done: bool
    message: str = ""


class MdmError(Exception):
    """The one error a plugin raises, its text a sentence for the operator

    named holds the phones a batch had already named when it stopped, by id, so the operator names
    only the rest. lines holds the lines already due to the operator when it stopped, such as which
    old callsign to revoke, shown before the error.
    """

    def __init__(self, message: str, named: Mapping[str, str] | None = None, lines: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.named: dict[str, str] = dict(named or {})
        self.lines: list[str] = list(lines)


class Mdm(Protocol):
    """One MDM, configured for one Deploy App's group of phones"""

    API_VERSION: ClassVar[int]
    #: The MDM's name as an operator knows it
    title: ClassVar[str]
    fields: ClassVar[tuple[Field, ...]]
    #: For each capability in CAPABILITIES this MDM lacks, what to tell the operator instead. The
    #: command line never calls a capability listed here; called anyway, it raises MdmError.
    missing: ClassVar[Mapping[str, str]]

    def __init__(self, values: Mapping[str, str]) -> None:
        """values: the fields' values by key"""

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        """Set the MDM up for this Deploy App, first time or again

        Idempotent. Never replaces a certificate template on a group that has phones. dry_run
        checks what would be stated and stops before the MDM is changed.
        """

    def set_challenge(self, window: Settings) -> None:
        """Give the MDM the challenge of the enrolment window just opened, and change nothing else"""

    def phones(self) -> list[Phone]:
        """The group's phones, oldest first"""

    def name(self, names: Mapping[str, str]) -> list[str]:
        """Give phones their callsigns, by id, and do what the MDM needs for their certificates

        Answers the lines to show the operator. Raises MdmError for an id not in the group, and with
        named filled in when it stopped half-way.
        """

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        """Have the phones ask for their certificates again

        No ids means every named phone whose state is NOT_ASKED, REFUSED, UNREACHABLE or FAILED.
        Answers the lines to show the operator, none when nothing was asked.
        """


__all__ = [
    "API_VERSION",
    "ASK_AGAIN",
    "CAPABILITIES",
    "SETUP",
    "SET_CHALLENGE",
    "Field",
    "Mdm",
    "MdmError",
    "Phone",
    "Settings",
    "State",
    "Step",
]
