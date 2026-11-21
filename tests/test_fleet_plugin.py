"""Fleet behind the MDM contract, against the stubbed Fleet"""

import datetime

import httpx
import pytest

from odmdm_connector.fleet_plugin import FleetPlugin
from odmdm_connector.mdm import Mdm, MdmError, State
from odmdm_connector.settings import Settings

from .test_fleet import _Fleet, _host, _mdm

NO_NAME = "Could not replace certificate variables: host 6f1c does not have an IDP user for variable FLEET_VAR_X"
REFUSED = "SCEP enrollment failed: Enrollment failed with SCEP failInfo: badMessageCheck"
UNREACHED = "Network error during SCEP enrollment: Failed to communicate with SCEP server"
WINDOW = Settings("https://example.fi/scep", "new-window", datetime.datetime(2026, 11, 21, 14, tzinfo=datetime.UTC))


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch) -> _Fleet:
    """A stubbed Fleet whose team has its certificate template"""
    stub = _Fleet()
    stub.certificates = [{"id": 81, "name": "rmscep", "subject_name": "CN=$X", "certificate_authority_id": 3}]
    _mdm(stub, monkeypatch)
    return stub


def _plugin(**values: str) -> Mdm:
    return FleetPlugin({"mdm_url": "https://mdm.test", "token": "token", "team": "rmscep", **values})


@pytest.mark.parametrize(
    ("status", "callsign", "detail", "state", "words"),
    [
        ("", "", "", State.NO_REQUEST, ""),
        ("verified", "OTTER4", "", State.READY, ""),
        ("failed", "", NO_NAME, State.NEEDS_NAME, ""),
        ("failed", "OTTER4", NO_NAME, State.NOT_ASKED, "run resend"),
        ("failed", "OTTER4", REFUSED, State.REFUSED, "name taken or not allowed"),
        ("failed", "OTTER4", UNREACHED, State.UNREACHABLE, ""),
        ("failed", "OTTER4", "Something new", State.FAILED, "Something new"),
        ("pending", "OTTER4", "", State.WAITING, "up to 15 minutes"),
        ("delivered", "OTTER4", UNREACHED, State.WAITING, "up to 15 minutes; last time: could not reach Deploy App"),
    ],
)
def test_fleets_status_and_detail_become_a_state(
    fleet: _Fleet, status: str, callsign: str, detail: str, state: State, words: str
) -> None:
    """Fleet's words are kept beside the state as its detail"""
    fleet.hosts = [_host(31, status, callsign, detail)]

    [phone] = _plugin().phones()

    assert (phone.id, phone.callsign, phone.joined) == ("31", callsign, "2026-11-01T19:38:12Z")
    assert phone.state is state
    assert phone.detail.startswith(words) if words else not phone.detail


def _listings(fleet: _Fleet) -> int:
    return len([path for _, path in fleet.calls if path.endswith("/hosts")])


def test_name_answers_its_lines_and_reads_the_phones_once(fleet: _Fleet) -> None:
    """Read once, and the phones checked are the ones named"""
    fleet.hosts = [_host(31, "failed", detail=NO_NAME), _host(32)]

    lines = _plugin().name({"32": "OTTER5", "31": "OTTER4"})

    assert lines == [
        "Host 32 is OTTER5. Fleet has no certificate request for it, so nothing was sent",
        "Host 31 is OTTER4",
    ]
    assert _listings(fleet) == 1


def test_phones_are_read_afresh_on_every_call(fleet: _Fleet) -> None:
    """A refresh never shows what an earlier one read"""
    fleet.hosts = [_host(31)]
    plugin = _plugin()

    first = plugin.phones()
    fleet.hosts.append(_host(32))
    second = plugin.phones()

    assert ([p.id for p in first], [p.id for p in second]) == (["31"], ["31", "32"])
    assert _listings(fleet) == 2


@pytest.mark.parametrize("call", ["name", "ask_again"])
def test_an_id_not_in_the_team_changes_nothing(fleet: _Fleet, call: str) -> None:
    fleet.hosts = [_host(31, "failed", detail=NO_NAME)]
    plugin = _plugin()

    with pytest.raises(MdmError, match="host 99 is not in the team"):
        plugin.name({"99": "OTTER4"}) if call == "name" else plugin.ask_again(["99"])

    assert not [method for method, _ in fleet.calls if method != "GET"]


def test_a_batch_stopped_half_way_names_the_phones_named(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = fleet.handler

    def refusing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/hosts/32/certificates/81/resend"):
            return httpx.Response(500, text="database is locked")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(host, "failed", detail=NO_NAME) for host in (31, 32, 33)]

    with pytest.raises(MdmError, match="Fleet refused: POST .*/hosts/32/.* answered 500") as raised:
        _plugin().name({"31": "OTTER1", "32": "OTTER2", "33": "OTTER3"})

    assert raised.value.named == {"31": "OTTER1", "32": "OTTER2"}


@pytest.mark.parametrize(
    ("values", "says"),
    [
        ({"mdm_url": ""}, "ODMDM_MDM_URL"),
        ({"mdm_url": "http://mdm.test"}, "must start with https://"),
        ({"token": ""}, "ODMDM_TOKEN_FILE"),
    ],
)
def test_fleet_is_reached_only_when_configured(fleet: _Fleet, values: dict[str, str], says: str) -> None:
    """Said when Fleet is first needed, so a dry run needs no token"""
    plugin = _plugin(**values)

    [step] = plugin.setup(WINDOW, dry_run=True)
    with pytest.raises(MdmError, match=says):
        plugin.phones()

    assert step.label == "Template names 3 apps, 3 installed at enrolment"
    assert "launcher link 'Deploy App' -> https://mtls.example.fi/" in step.message
    assert not fleet.calls


def test_ask_again_without_ids_and_nobody_failed_answers_nothing(fleet: _Fleet) -> None:
    fleet.hosts = [_host(31, "verified", "OTTER4")]

    assert _plugin().ask_again([]) == []
    assert not [method for method, _ in fleet.calls if method != "GET"]
