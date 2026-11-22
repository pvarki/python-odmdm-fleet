"""The plugin authors' test kit, run against the fake and against Fleet over the stub"""

from collections.abc import Iterator, Mapping, Sequence
from typing import ClassVar

import pytest

from odmdm_connector.fleet_plugin import FleetPlugin
from odmdm_connector.mdm import CAPABILITIES, Mdm, MdmError, Phone, State, Step
from odmdm_connector.settings import Settings
from odmdm_connector.testing import FakeMdm, contract

from .test_fleet import Request, Response, _Fleet, _host, _mdm

NO_NAME = "Could not replace certificate variables: host 6f1c does not have an IDP user for variable FLEET_VAR_X"
CHECKS = pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)


def _fake(refuse_after: int | None = None) -> Mdm:
    return FakeMdm({}, refuse_after=refuse_after)


def _lacking(refuse_after: int | None = None) -> Mdm:
    return FakeMdm.lacking(*CAPABILITIES)({}, refuse_after=refuse_after)


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch) -> contract.Factory:
    """Fleet over the stub, two phones waiting for a name and one with its certificate"""

    def factory(refuse_after: int | None = None) -> Mdm:
        stub = _Fleet()
        stub.authorities = [{"id": 3, "name": "RMSCEP", "url": contract.WINDOW.scep_url}]
        stub.certificates = [{"id": 81, "name": "rmscep", "subject_name": "CN=$X", "certificate_authority_id": 3}]
        stub.hosts = [
            _host(31, "failed", detail=NO_NAME),
            _host(32, "failed", detail=NO_NAME),
            _host(33, "verified", "A"),
        ]
        answer = stub.handler
        named: list[str] = []

        def refusing(request: Request) -> Response:
            if request.url.path.endswith("/device_mapping"):
                if refuse_after is not None and len(named) >= refuse_after:
                    return Response(500, text="database is locked")
                named.append(request.url.path)
            return answer(request)

        stub.handler = refusing  # type: ignore[method-assign]
        _mdm(stub, monkeypatch)
        return FleetPlugin({"mdm_url": "https://mdm.test", "token": "token", "team": "rmscep"})

    return factory


@CHECKS
def test_the_fake_keeps_the_contract(check: contract.Check) -> None:
    check(_fake)


@CHECKS
def test_a_fake_lacking_every_capability_keeps_the_contract(check: contract.Check) -> None:
    check(_lacking)


@CHECKS
def test_fleet_keeps_the_contract(check: contract.Check, fleet: contract.Factory) -> None:
    check(fleet)


class _Careless(FakeMdm):
    """Breaks the contract in every way a check looks for"""

    API_VERSION = 2
    title = ""
    missing: ClassVar[Mapping[str, str]] = {"teleport": ""}

    def name(self, names: Mapping[str, str]) -> list[str]:
        try:
            return super().name(names)
        except MdmError as exc:
            raise MdmError(str(exc)) from exc

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        return [""]


class _Forcing(FakeMdm):
    """Says it cannot send the policy again, then sets up with force anyway"""

    missing: ClassVar[Mapping[str, str]] = {"send_policy": "Not here"}

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        return super().setup(window, dry_run=dry_run)


class _Lying(FakeMdm):
    """Says it lacks asking again, then does it anyway"""

    missing: ClassVar[Mapping[str, str]] = {"ask_again": "Not here", "set_challenge": "Not here"}

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        return []

    def set_challenge(self, window: object) -> None:
        raise MdmError("Something else")


@pytest.mark.parametrize(
    ("factory", "check", "says"),
    [
        (_Careless, contract.declares_the_contract_version, "API_VERSION must be 1"),
        (_Careless, contract.says_what_it_lacks, "missing names only capabilities"),
        (_Careless, contract.says_what_a_batch_stopped_half_way_named, "MdmError.named must be"),
        (_Careless, contract.asks_again, "lines must be non-empty"),
        (_Lying, contract.asks_again, "listed as missing but did not raise"),
        (_Lying, contract.sets_the_challenge, "must raise MdmError with its sentence"),
        (_Forcing, contract.sends_the_policy_again, "send_policy is listed as missing but did not raise"),
    ],
)
def test_a_plugin_breaking_the_contract_fails_its_check(
    factory: type[FakeMdm], check: contract.Check, says: str
) -> None:
    with pytest.raises(AssertionError, match=says):
        check(lambda refuse_after=None: factory({}, refuse_after=refuse_after))


def test_the_phones_a_check_needs() -> None:
    """Times an operator can read, and enough phones to name"""
    odd = [Phone("a1", "", "m", "yesterday", State.NEEDS_NAME)]

    with pytest.raises(AssertionError, match="not an ISO time"):
        contract.lists_its_phones(lambda refuse_after=None: FakeMdm({}, odd))
    with pytest.raises(AssertionError, match="at least two phones without a name"):
        contract.names_phones(lambda refuse_after=None: FakeMdm({}, odd))


def test_a_name_that_does_not_stick_fails() -> None:
    class Forgetful(FakeMdm):
        def name(self, names: Mapping[str, str]) -> list[str]:
            return ["Named"]

    with pytest.raises(AssertionError, match="must show its callsign"):
        contract.names_phones(lambda refuse_after=None: Forgetful({}))
    with pytest.raises(AssertionError, match="must raise MdmError when the MDM refuses"):
        contract.says_what_a_batch_stopped_half_way_named(lambda refuse_after=None: Forgetful({}))
    with pytest.raises(AssertionError, match="name of an id not in the group"):
        contract.refuses_an_unknown_id(lambda refuse_after=None: Forgetful({}))
