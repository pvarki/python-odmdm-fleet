"""Fleet behind the MDM contract, against the stubbed Fleet"""

import datetime

import pytest

from odmdm_connector.fleet_plugin import FleetPlugin
from odmdm_connector.mdm import Mdm, MdmError, State
from odmdm_connector.settings import Settings

from .test_fleet import Request, Response, _Fleet, _host, _mdm, _policy_uploads

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
        ("failed", "OTTER4", NO_NAME, State.NOT_ASKED, "ask again"),
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
        "Phone 32 is OTTER5. Fleet has no certificate request for it, so nothing was sent",
        "Phone 31 is OTTER4",
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

    def refusing(request: Request) -> Response:
        if request.url.path.endswith("/hosts/32/certificates/81/resend"):
            return Response(500, text="database is locked")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(host, "failed", detail=NO_NAME) for host in (31, 32, 33)]

    with pytest.raises(MdmError, match="Fleet refused: POST .*/hosts/32/.* answered 500") as raised:
        _plugin().name({"31": "OTTER1", "32": "OTTER2", "33": "OTTER3"})

    assert raised.value.named == {"31": "OTTER1", "32": "OTTER2"}
    assert not raised.value.unauthorized


def test_a_token_fleet_does_not_accept_is_marked(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    """So the connector lets go of it and asks for it again"""
    monkeypatch.setattr(fleet, "handler", lambda request: Response(401, json={"message": "Authentication required"}))
    _mdm(fleet, monkeypatch)

    with pytest.raises(MdmError, match="Fleet refused: the API token is not accepted") as raised:
        _plugin().phones()

    assert raised.value.unauthorized


def test_a_token_refused_while_the_policy_is_replaced_is_marked(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    """The token ended between deleting the old policy and uploading the new one"""
    answer = fleet.handler

    def refusing(request: Request) -> Response:
        if request.method == "POST" and request.url.path.endswith("/configuration_profiles"):
            return Response(401, json={"message": "Authentication required"})
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(31, "failed", detail=NO_NAME, enrolled="2026-11-21T09:00:00Z")]
    fleet.profiles = [
        {"name": "rmscep-policy", "profile_uuid": "g4bb", "created_at": "2026-11-20T21:02:44Z", "raw": {}}
    ]

    with pytest.raises(MdmError, match="was being replaced in team 7 and may now be missing") as raised:
        _plugin().name({"31": "OTTER1"})

    assert raised.value.unauthorized


@pytest.mark.parametrize(
    ("values", "says"),
    [
        ({"mdm_url": ""}, "The Fleet address is missing"),
        ({"mdm_url": "http://mdm.test"}, "must start with https://"),
        ({"token": ""}, "The Fleet API token is missing"),
        ({"team": ""}, "The Fleet team is missing"),
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


def test_the_template_and_key_alias_are_advanced() -> None:
    """Rarely changed, and the key alias never for a deployment: the window shows them only when asked"""
    assert [item.key for item in FleetPlugin.fields if item.advanced] == ["template", "key_alias"]


def test_the_token_help_says_which_token_lasts() -> None:
    """The token My account gives ends with that sign-in, as the refusal then says"""
    [token] = [each for each in FleetPlugin.fields if each.key == "token"]

    assert token.help == (
        "A global admin's API token: in Fleet, My account, Get API token. That one ends with that sign-in, "
        "after 5 days by default; an API-only user's does not."
    )


def test_the_team_is_named_after_the_deployment_by_default() -> None:
    """golden-monkey.example.fi's team is golden-monkey DeployApp; it must be given, and the connector suggests it"""
    [team] = [each for each in FleetPlugin.fields if each.key == "team"]

    assert team.required
    assert team.default_for("golden-monkey.example.fi") == "golden-monkey DeployApp"


def test_a_new_window_reaches_only_this_deploy_apps_authority(fleet: _Fleet) -> None:
    """On a Fleet that serves several Deploy Apps, each with a challenge of its own

    The names say nothing: the team's template is bound to authority 3, which serves this Deploy App.
    """
    fleet.authorities = [
        {"id": 4, "name": "UNIT_B", "url": "https://north.example.fi/scep", "challenge": "norths"},
        {"id": 3, "name": "UNIT_A", "url": "https://example.fi/scep", "challenge": "old"},
    ]

    _plugin().set_challenge(WINDOW)

    assert [authority["challenge"] for authority in fleet.authorities] == ["norths", "new-window"]
    assert [call for call in fleet.calls if call[0] != "GET"] == [
        ("PATCH", "/api/latest/fleet/certificate_authorities/3")
    ]


def test_a_new_window_is_refused_when_the_team_asks_another_address(fleet: _Fleet) -> None:
    """A Deploy App that moved, or the wrong team: said aloud, and the challenge goes nowhere"""
    fleet.authorities = [{"id": 3, "name": "RMSCEP", "url": "https://old.example.fi/scep", "challenge": "old"}]

    with pytest.raises(MdmError) as raised:
        _plugin().set_challenge(WINDOW)

    assert str(raised.value) == (
        "Fleet refused: the team takes its certificates from authority RMSCEP, which serves "
        "https://old.example.fi/scep, not https://example.fi/scep, so nothing was changed. If that authority is this "
        "Deploy App's, correct its address in Fleet; if not, choose another team."
    )
    assert fleet.authorities == [{"id": 3, "name": "RMSCEP", "url": "https://old.example.fi/scep", "challenge": "old"}]
    assert not [call for call in fleet.calls if call[0] != "GET"]


def test_a_new_window_before_the_team_is_made_finds_the_authority_by_its_address(fleet: _Fleet) -> None:
    fleet.authorities = [{"id": 3, "name": "RMSCEP", "url": "https://example.fi/scep", "challenge": "old"}]

    _plugin(team="golden-monkey DeployApp").set_challenge(WINDOW)

    assert fleet.authorities[0]["challenge"] == "new-window"


def test_sending_the_policy_again_makes_no_team(fleet: _Fleet) -> None:
    """A repair creates nothing: a team Fleet lacks is refused before the authority or anything else is changed"""
    fleet.authorities = [{"id": 3, "name": "RMSCEP", "url": "https://example.fi/scep", "challenge": "old"}]

    with pytest.raises(MdmError) as raised:
        list(_plugin(team="golden-monkey DeployApp").setup(WINDOW, force=True))

    assert str(raised.value) == (
        "Fleet has no team named golden-monkey DeployApp, so nothing was changed: sending the policy again makes "
        "no team. Check the team's name."
    )
    assert not raised.value.unauthorized
    assert not [call for call in fleet.calls if call[0] != "GET"]
    assert fleet.authorities[0]["challenge"] == "old"


def test_setup_with_force_sends_the_unchanged_policy_again_and_no_challenge(fleet: _Fleet) -> None:
    """apply --force-policy, and Send the policy again on the page: for a phone whose policy Fleet overwrote"""
    fleet.authorities = [{"id": 3, "name": "RMSCEP", "url": "https://example.fi/scep", "challenge": "old"}]
    bare = Settings("https://example.fi/scep", "")
    list(_plugin().setup(bare))
    before = _policy_uploads(fleet)

    list(_plugin().setup(bare))
    unforced = _policy_uploads(fleet)
    steps = list(_plugin().setup(bare, force=True))

    assert unforced == before, "unchanged, so not sent"
    assert _policy_uploads(fleet) == before + 1
    assert steps[-1].label.startswith("Applied to team rmscep (7): ")
    assert fleet.authorities[0]["challenge"] == "old"


#: The Deploy App the stub's authority serves; ending takes no challenge
ENDED = Settings("https://deployment.test/scep", "")
KEPT = "The team, its phones, their names and their apps stay."


def _ending(**values: str) -> FleetPlugin:
    """The plugin itself: until the contract has end, an Mdm does not"""
    return FleetPlugin({"mdm_url": "https://mdm.test", "token": "token", "team": "rmscep", **values})


def _changes(fleet: _Fleet) -> list[tuple[str, str]]:
    return [call for call in fleet.calls if call[0] != "GET"]


def test_ending_removes_the_template_and_then_the_authority(fleet: _Fleet) -> None:
    """Only those two: the team, its hosts, its policy and its apps stay"""
    fleet.hosts = [_host(31, "verified", "OTTER1"), _host(32)]

    lines = _ending().end(ENDED)

    assert lines == [
        (
            "Certificate template rmscep is removed from team rmscep: its 2 phones lose this Deploy App's certificate "
            "at their next check-in, up to 15 minutes."
        ),
        "Certificate authority RMSCEP is removed from Fleet.",
        KEPT,
    ]
    assert _changes(fleet) == [
        ("DELETE", "/api/latest/fleet/certificates/81"),
        ("DELETE", "/api/latest/fleet/certificate_authorities/3"),
    ]
    assert not fleet.certificates and not fleet.authorities
    assert [host["id"] for host in fleet.hosts] == [31, 32] and fleet.teams == [{"id": 7, "name": "rmscep"}]


@pytest.mark.parametrize(
    ("hosts", "said"),
    [
        (1, ": its phone loses this Deploy App's certificate at its next check-in, up to 15 minutes."),
        (0, ", which has no phones."),
    ],
)
def test_ending_says_how_many_phones_lose_the_certificate(fleet: _Fleet, hosts: int, said: str) -> None:
    fleet.hosts = [_host(31 + n) for n in range(hosts)]

    assert _ending().end(ENDED)[0] == f"Certificate template rmscep is removed from team rmscep{said}"


def test_an_authority_another_team_still_uses_stays(fleet: _Fleet) -> None:
    """Fleet's conflict is a line for the operator, not a failure"""
    fleet.certificates = [
        {"id": 81, "name": "rmscep", "team_id": 7, "subject_name": "CN=$X", "certificate_authority_id": 3},
        {"id": 90, "name": "rmscep", "team_id": 8, "subject_name": "CN=$X", "certificate_authority_id": 3},
    ]

    lines = _ending().end(ENDED)

    assert lines[1:] == [
        "Certificate authority RMSCEP stays in Fleet: another team's certificate template still uses it.",
        KEPT,
    ]
    assert [c["id"] for c in fleet.certificates] == [90] and [a["id"] for a in fleet.authorities] == [3]


def test_ending_again_finishes_what_an_earlier_call_left(fleet: _Fleet) -> None:
    """The template gone already, the authority that serves this Deploy App is found by its address"""
    fleet.certificates = []
    fleet.authorities = [
        {"id": 3, "name": "ODMDM_OTHER", "url": "https://other.test/scep"},
        {"id": 4, "name": "ODMDM_DEPLOYMENT", "url": "https://deployment.test/scep"},
    ]

    lines = _ending().end(ENDED)

    assert lines == [
        "Team rmscep has no certificate template rmscep: there was nothing to remove from it.",
        "Certificate authority ODMDM_DEPLOYMENT is removed from Fleet.",
        KEPT,
    ]
    assert [a["id"] for a in fleet.authorities] == [3], "another Deploy App's authority stays"


def test_ending_when_nothing_is_left_says_so(fleet: _Fleet) -> None:
    fleet.certificates = []
    fleet.authorities = [{"id": 3, "name": "ODMDM_OTHER", "url": "https://other.test/scep"}]

    assert _ending().end(ENDED) == [
        "Team rmscep has no certificate template rmscep: there was nothing to remove from it.",
        KEPT,
    ]
    assert not _changes(fleet)


def test_ending_without_the_team_changes_nothing(fleet: _Fleet) -> None:
    with pytest.raises(MdmError) as raised:
        _ending(team="golden-monkey DeployApp").end(ENDED)

    assert str(raised.value) == (
        "Fleet has no team named golden-monkey DeployApp, so nothing was changed. Check the team's name."
    )
    assert not _changes(fleet)


def test_a_team_bound_to_another_deploy_apps_authority_is_left_as_it_is(fleet: _Fleet) -> None:
    """Its phones take their certificates from another Deploy App, so removing the template would end that one"""
    with pytest.raises(MdmError) as raised:
        _ending().end(Settings("https://golden-monkey.example.fi/scep", ""))

    assert str(raised.value) == (
        "Team rmscep takes its certificates from authority RMSCEP, which serves https://deployment.test/scep, not "
        "https://golden-monkey.example.fi/scep, so nothing was changed. Check the team's name."
    )
    assert not _changes(fleet)


def test_a_refusal_half_way_carries_the_lines_already_due(fleet: _Fleet) -> None:
    """The template is gone, so the operator learns it; ending again finishes the rest"""
    answer = fleet.handler

    def refusing(request: Request) -> Response:
        if request.method == "DELETE" and "/certificate_authorities/" in request.url.path:
            return Response(500, text="database is locked")
        return answer(request)

    fleet.handler = refusing  # type: ignore[method-assign]

    with pytest.raises(MdmError) as raised:
        _ending().end(ENDED)

    assert str(raised.value).startswith(
        "Fleet refused: DELETE /api/latest/fleet/certificate_authorities/3 answered 500"
    )
    assert raised.value.lines == [
        "Certificate template rmscep is removed from team rmscep, which has no phones.",
    ]
    assert not raised.value.unauthorized


def test_a_token_refused_while_ending_is_said_as_such(fleet: _Fleet) -> None:
    fleet.handler = lambda request: Response(401, json={"message": "Authentication required"})  # type: ignore[method-assign]

    with pytest.raises(MdmError) as raised:
        _ending().end(ENDED)

    assert raised.value.unauthorized
