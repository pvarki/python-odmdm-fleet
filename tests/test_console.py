"""The CLI

An operator runs these by hand, so a missing setting has to say what is missing and a refusal has
to say why, rather than end in a traceback.
"""

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from odmdm_fleet import config
from odmdm_fleet.console import cli_group

from .test_fleet import _Fleet, _host, _mdm


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fleet:
    """A configured connector talking to a stubbed Fleet whose team "rmscep" has its certificate template

    And its policy, last sent after the time _host gives a phone by default.
    """
    token = tmp_path / "token"
    token.write_text("token\n", encoding="utf-8")
    monkeypatch.setattr(config, "URL", "https://mdm.test")
    monkeypatch.setattr(config, "TOKEN_FILE", token)
    monkeypatch.setattr(config, "TEAM", "rmscep")
    stub = _Fleet()
    stub.certificates = [{"id": 81, "name": "rmscep", "subject_name": "CN=$VAR", "certificate_authority_id": 3}]
    stub.profiles = [
        {
            "name": "rmscep-policy",
            "profile_uuid": "g4bb",
            "created_at": "2026-11-20T21:02:44.458685Z",
            "updated_at": "2026-11-20T21:02:44Z",
            "raw": {"defaultPermissionPolicy": "GRANT"},
        }
    ]
    _mdm(stub, monkeypatch)
    return stub


@pytest.mark.parametrize("command", [["apply"], ["challenge", "--challenge", "new-window"]])
def test_a_template_and_domain_are_required(command: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Without them there is nothing to state to Fleet"""
    monkeypatch.setattr(config, "TEMPLATE", None)

    result = CliRunner().invoke(cli_group, command)

    assert result.exit_code == 1
    assert "ODMDM_FLEET_TEMPLATE" in result.output


def _template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "template.json"
    path.write_text(
        json.dumps(
            {
                "apps": [{"package": "com.example.one"}],
                "policy": {"defaultPermissionPolicy": "GRANT"},
                "certificate": {"name": "{key_alias}", "subject": "CN=$VAR", "authority": "RMSCEP"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "TEMPLATE", path)
    monkeypatch.setattr(config, "DOMAIN", "example.fi")


def test_apply_states_the_template(fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One team: phones enrol into the team that has the certificate template"""
    _template(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "CHALLENGE", "new-window")

    result = CliRunner().invoke(cli_group, ["apply"])

    assert result.exit_code == 0, result.output
    assert "Applied to team 7" in result.output
    assert fleet.authorities[0]["challenge"] == "new-window"
    assert fleet.authorities[0]["url"] == "https://example.fi/scep"


def test_a_dry_run_never_reaches_fleet(fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checking an edited template must not change the estate"""
    _template(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli_group, ["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not fleet.calls


def test_a_new_window_touches_only_the_authority(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rewritten policy stops apps installing on phones enrolling right now"""
    _template(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli_group, ["challenge"], input="new-window\n")

    assert result.exit_code == 0, result.output
    assert "new-window" not in result.output, "asked for without echoing it"
    assert fleet.authorities[0]["challenge"] == "new-window"
    changed = {path for method, path in fleet.calls if method != "GET"}
    assert changed == {"/api/latest/fleet/certificate_authorities/3"}


def test_phones_needs_somewhere_to_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Fleet configured is a sentence, not a traceback"""
    monkeypatch.setattr(config, "URL", "")

    result = CliRunner().invoke(cli_group, ["phones"])

    assert result.exit_code == 1
    assert "ODMDM_FLEET_URL" in result.output


NO_NAME = (
    "Could not replace certificate variables: host 6f1c0e52-9a6b-4b8e-8f43-2d0c5f6a1b7e does not have an IDP user "
    "for variable FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART"
)
REFUSED = "SCEP enrollment failed: Enrollment failed with SCEP failInfo: badMessageCheck"
UNREACHED = "Network error during SCEP enrollment: Failed to communicate with SCEP server"


@pytest.mark.parametrize(
    ("status", "detail", "says"),
    [
        ("", "", "no certificate requested"),
        ("failed", NO_NAME, "named, but its certificate was not asked for again: run resend"),
        ("pending", "", "requested, waiting for the phone (up to 15 minutes)"),
        ("delivering", "", "requested, waiting for the phone (up to 15 minutes)"),
        ("delivered", "", "requested, waiting for the phone (up to 15 minutes)"),
        ("pending", REFUSED, "requested, waiting for the phone (up to 15 minutes); last time: refused by Deploy App"),
        ("delivered", UNREACHED, "waiting for the phone (up to 15 minutes); last time: could not reach Deploy App"),
        ("verified", "", "has its certificate"),
        ("failed", REFUSED, "refused by Deploy App: name taken or not allowed, enrolment closed, or Fleet has an old"),
        ("failed", UNREACHED, "could not reach Deploy App"),
        ("failed", "Something Fleet says that nobody has seen yet", "Something Fleet says that nobody has seen yet"),
    ],
)
def test_phones_says_where_each_certificate_is(fleet: _Fleet, status: str, detail: str, says: str) -> None:
    """In words an operator can act on, rather than Fleet's status and detail"""
    fleet.hosts = [_host(31, status, "OTTER4", detail)]

    result = CliRunner().invoke(cli_group, ["phones"])

    assert result.exit_code == 0, result.output
    assert result.output.split()[:4] == ["31", "OTTER4", "Google", "sdk_gphone64_arm64"]
    assert "2026-11-01" in result.output
    assert says in result.output


def test_a_phone_fresh_from_enrolment_waits_for_a_name(fleet: _Fleet) -> None:
    """Its first request fails for want of one"""
    fleet.hosts = [_host(31, "failed", detail=NO_NAME)]

    result = CliRunner().invoke(cli_group, ["phones"])

    assert result.exit_code == 0, result.output
    assert result.output.split()[:2] == ["31", "-"]
    assert result.output.rstrip().endswith("waiting for a name")


def test_phones_lists_in_enrolment_order_with_the_callsign_fleet_issues(fleet: _Fleet) -> None:
    """The certificate carries the name up to its "@"; an old remove row is history, not state"""
    fleet.hosts = [
        _host(32, "verified", "OTTER1@CODE", enrolled="2026-11-02T08:00:00Z"),
        _host(33, enrolled="2026-11-01T08:00:00Z"),
    ]
    fleet.hosts[0]["mdm"]["profiles"].insert(
        0, {"name": "rmscep", "status": "failed", "operation_type": "remove", "detail": REFUSED}
    )

    result = CliRunner().invoke(cli_group, ["phones"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split()[:2] == ["33", "-"]
    assert lines[1].split()[:2] == ["32", "OTTER1"]
    assert lines[1].endswith("has its certificate")


def test_an_empty_team_says_so(fleet: _Fleet) -> None:
    """Rather than printing nothing, which reads as a failure"""
    result = CliRunner().invoke(cli_group, ["phones"])

    assert result.exit_code == 0, result.output
    assert "No phones in the team" in result.output


def _named(fleet: _Fleet) -> dict[int, str]:
    return {h["id"]: h["end_users"][0]["idp_username"] if h["end_users"] else "" for h in fleet.hosts}


def _changes(fleet: _Fleet) -> list[tuple[str, str]]:
    return [(method, path.removeprefix("/api/latest/fleet")) for method, path in fleet.calls if method != "GET"]


def test_name_gives_any_number_of_phones_their_callsigns(fleet: _Fleet) -> None:
    """In any order, each named and then asked for its certificate again under the name"""
    fleet.hosts = [_host(host, "failed", detail=NO_NAME) for host in (31, 32, 33)]

    result = CliRunner().invoke(cli_group, ["name", "33=OTTER5", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Host 33 is OTTER5", "Host 31 is OTTER4"]
    assert _named(fleet) == {31: "OTTER4", 32: "", 33: "OTTER5"}
    assert _changes(fleet) == [
        ("PUT", "/hosts/33/device_mapping"),
        ("POST", "/hosts/33/certificates/81/resend"),
        ("PUT", "/hosts/31/device_mapping"),
        ("POST", "/hosts/31/certificates/81/resend"),
    ]


def test_a_request_still_pending_takes_the_name_without_a_resend(fleet: _Fleet) -> None:
    """Fleet refuses to resend it, and it needs none: it is filled in when it goes"""
    fleet.hosts = [_host(31, "pending")]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Host 31 is OTTER4"]
    assert _named(fleet) == {31: "OTTER4"}


def test_a_phone_with_its_certificate_under_the_name_is_left_alone(fleet: _Fleet) -> None:
    """Asked again under the name it holds, it is refused as taken and then reads as refused"""
    fleet.hosts = [_host(31, "verified", "OTTER4@CODE"), _host(32, "failed", detail=NO_NAME)]

    result = CliRunner().invoke(cli_group, ["name", "31=otter4", "32=OTTER5"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Host 31 is already OTTER4", "Host 32 is OTTER5"]
    assert _changes(fleet) == [("PUT", "/hosts/32/device_mapping"), ("POST", "/hosts/32/certificates/81/resend")]
    assert _named(fleet)[31] == "OTTER4@CODE"


def test_a_phone_with_no_request_is_named_and_the_batch_goes_on(fleet: _Fleet) -> None:
    """Fleet has no request to send it again, and fills the name in when it makes one"""
    fleet.hosts = [_host(31, "failed", detail=NO_NAME), _host(32), _host(33, "failed", detail=NO_NAME)]

    result = CliRunner().invoke(cli_group, ["name", "--all", "OTTER"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Host 31 is OTTER1",
        "Host 32 is OTTER2. Fleet has no certificate request for it, so nothing was sent",
        "Host 33 is OTTER3",
    ]
    assert _named(fleet) == {31: "OTTER1", 32: "OTTER2", 33: "OTTER3"}
    assert ("POST", "/hosts/32/certificates/81/resend") not in _changes(fleet)


@pytest.mark.parametrize(
    ("first", "start", "expected"),
    [
        ("KETTU1", [], {42: "OTTER1", 43: "OTTER2", 41: "OTTER3"}),
        ("otter7", [], {42: "OTTER8", 43: "OTTER9", 41: "OTTER10"}),
        ("OTTER7", ["--start", "20"], {42: "OTTER20", 43: "OTTER21", 41: "OTTER22"}),
    ],
)
def test_name_all_numbers_the_phones_without_a_name(
    fleet: _Fleet, first: str, start: list[str], expected: dict[int, str]
) -> None:
    """In enrolment order, then host id, from one above the highest number in use unless told"""
    fleet.hosts = [
        _host(40, "verified", first, enrolled="2026-11-01T08:00:00Z"),
        _host(41, "failed", detail=NO_NAME, enrolled="2026-11-03T08:00:00Z"),
        _host(43, "failed", detail=NO_NAME, enrolled="2026-11-02T08:00:00Z"),
        _host(42, "failed", detail=NO_NAME, enrolled="2026-11-02T08:00:00Z"),
    ]

    result = CliRunner().invoke(cli_group, ["name", "--all", "OTTER", *start])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [f"Host {host} is {callsign}" for host, callsign in expected.items()]
    assert _named(fleet) == {40: first, **expected}


def test_name_all_with_every_phone_named_names_nothing(fleet: _Fleet) -> None:
    """A second run finds nobody waiting, and changes nothing"""
    fleet.hosts = [_host(31, "verified", "OTTER1")]

    result = CliRunner().invoke(cli_group, ["name", "--all", "OTTER"])

    assert result.exit_code == 0, result.output
    assert "Nothing to name" in result.output
    assert not _changes(fleet)


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        (["31=OT"], "OT: a callsign is 3-32 letters"),
        (["31=OTTER-"], "OTTER-: a callsign is 3-32 letters"),
        (["--all", "O"], "O1: a callsign is 3-32 letters"),
        (["31=OTTER4", "32=otter4"], "otter4 is given twice"),
        (["31=OTTER4", "31=OTTER5"], "host 31 is named twice"),
        (["31=otter9"], "otter9 already belongs to host 33"),
        (["--all", "OTTER", "--start", "9"], "OTTER9 already belongs to host 33"),
        (["31=OTTER4", "99=OTTER5"], "host 99 is not in the team"),
    ],
)
def test_name_changes_nothing_unless_every_name_can_be_given(fleet: _Fleet, arguments: list[str], says: str) -> None:
    """A typo refuses the whole command, rather than leaving half a batch named"""
    fleet.hosts = [
        _host(31, "failed", detail=NO_NAME),
        _host(32, "failed", detail=NO_NAME),
        _host(33, "verified", "OTTER9@CODE"),
    ]

    result = CliRunner().invoke(cli_group, ["name", *arguments])

    assert result.exit_code == 1
    assert f"Nothing named: {says}" in result.output
    assert not _changes(fleet)


@pytest.mark.parametrize("arguments", [[], ["31=OTTER4", "--all", "OTTER"], ["31=OTTER4", "--start", "3"], ["OTTER4"]])
def test_name_says_how_it_is_used(fleet: _Fleet, arguments: list[str]) -> None:
    """Before Fleet is asked anything"""
    result = CliRunner().invoke(cli_group, ["name", *arguments])

    assert result.exit_code == 2
    assert not fleet.calls


def test_renaming_a_phone_with_its_certificate_says_what_to_revoke(fleet: _Fleet) -> None:
    """The old callsign stays valid in Deploy App until an admin revokes it there"""
    fleet.hosts = [_host(31, "verified", "OTTER3")]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Host 31 is OTTER4. It was OTTER3 and still has that certificate: revoke OTTER3 in Deploy App."
    ]


def test_renaming_a_refused_phone_says_nothing_to_revoke(fleet: _Fleet) -> None:
    """A refused name was never issued to this phone, and may be another phone's"""
    fleet.hosts = [_host(31, "failed", "OTTER3", REFUSED)]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Host 31 is OTTER4"]


def test_name_without_a_certificate_template_says_so(fleet: _Fleet) -> None:
    """Before a phone is named, so nothing is left half done"""
    fleet.certificates = []
    fleet.hosts = [_host(31, "failed", detail=NO_NAME)]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 1
    assert "no certificate template named rmscep: run apply first" in result.output
    assert not _changes(fleet)


def test_resend_asks_again_for_every_named_phone_that_failed(fleet: _Fleet) -> None:
    """A phone named in Fleet by hand is one of them: naming alone does not send the request again"""
    fleet.hosts = [
        _host(31, "failed", "OTTER4", REFUSED),
        _host(32, "failed", detail=NO_NAME),
        _host(33, "verified", "OTTER5"),
        _host(34, "failed", "OTTER6", NO_NAME),
        _host(35, "pending", "OTTER7"),
    ]

    result = CliRunner().invoke(cli_group, ["resend"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Host 31 (OTTER4): certificate asked for again",
        "Host 34 (OTTER6): certificate asked for again",
    ]
    assert _changes(fleet) == [
        ("POST", "/hosts/31/certificates/81/resend"),
        ("POST", "/hosts/34/certificates/81/resend"),
    ]


def test_resend_given_hosts_asks_for_those(fleet: _Fleet) -> None:
    """Whatever state they are in: the operator chose them"""
    fleet.hosts = [_host(31, "failed", "OTTER4", REFUSED), _host(32, "failed", detail=NO_NAME)]

    result = CliRunner().invoke(cli_group, ["resend", "32"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Host 32 (no name): certificate asked for again"]
    assert _changes(fleet) == [("POST", "/hosts/32/certificates/81/resend")]


@pytest.mark.parametrize(("arguments", "says", "code"), [([], "Nothing to resend", 0), (["99"], "not in the team", 1)])
def test_resend_says_when_it_sends_nothing(fleet: _Fleet, arguments: list[str], says: str, code: int) -> None:
    """Nobody failed, or a host that is not in the team: said, and nothing sent"""
    fleet.hosts = [_host(31, "verified", "OTTER4")]

    result = CliRunner().invoke(cli_group, ["resend", *arguments])

    assert result.exit_code == code, result.output
    assert says in result.output
    assert not _changes(fleet)


#: After the fixture's policy was last sent
LATER = "2026-11-20T21:10:02Z"


def _policy_changes(fleet: _Fleet) -> list[tuple[str, str]]:
    return [change for change in _changes(fleet) if "/configuration_profiles" in change[1]]


def test_name_sends_the_policy_again_when_a_phone_enrolled_after_it(fleet: _Fleet) -> None:
    """Fleet can write a bare policy over a new phone's, and never sends ours again by itself

    Measured: the certificate installs, Chrome is not allowed to use it, and Deploy App says
    "Certificate Authentication Failed". Fleet's own copy uploaded again reaches the phone.
    """
    fleet.hosts = [
        _host(31, "failed", detail=NO_NAME, enrolled=LATER),
        _host(32, "failed", detail=NO_NAME, enrolled=LATER),
        _host(33, "verified", "OTTER1"),
    ]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Host 31 is OTTER4",
        "Policy sent again: 2 phones enrolled after it was last sent",
    ]
    assert _policy_changes(fleet) == [("DELETE", "/configuration_profiles/g4bb"), ("POST", "/configuration_profiles")]
    assert [profile["raw"] for profile in fleet.profiles] == [{"defaultPermissionPolicy": "GRANT"}]


def test_resend_sends_the_policy_again_when_a_phone_enrolled_after_it(fleet: _Fleet) -> None:
    """The phone asked again may be one whose policy Fleet overwrote"""
    fleet.hosts = [_host(31, "failed", "OTTER4", REFUSED, enrolled=LATER), _host(32, "verified", "OTTER5")]

    result = CliRunner().invoke(cli_group, ["resend"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Host 31 (OTTER4): certificate asked for again",
        "Policy sent again: 1 phone enrolled after it was last sent",
    ]
    assert _policy_changes(fleet) == [("DELETE", "/configuration_profiles/g4bb"), ("POST", "/configuration_profiles")]


def test_a_policy_sent_again_is_then_left_alone(fleet: _Fleet) -> None:
    """Fleet stamps an upload with its own clock, so the policy is newer than every phone already there

    Sent on every run, it would be rewritten under phones still installing their apps.
    """
    fleet.now = "2026-11-20T21:30:00.123456Z"
    fleet.hosts = [
        _host(31, "failed", detail=NO_NAME, enrolled=LATER),
        _host(32, "failed", detail=NO_NAME, enrolled=LATER),
    ]

    first = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])
    fleet.calls.clear()
    second = CliRunner().invoke(cli_group, ["name", "32=OTTER5"])

    assert first.stdout.splitlines()[-1] == "Policy sent again: 2 phones enrolled after it was last sent"
    assert second.exit_code == 0, second.output
    assert second.stdout.splitlines() == ["Host 32 is OTTER5"]
    assert not _policy_changes(fleet)


@pytest.mark.parametrize(("policy", "enrolled"), [(True, "2026-11-01T19:38:12Z"), (False, LATER)])
def test_the_policy_is_left_alone_unless_a_phone_enrolled_after_it(fleet: _Fleet, policy: bool, enrolled: str) -> None:
    """Every phone enrolled before it was last sent, or the team has no policy to send"""
    if not policy:
        fleet.profiles = []
    fleet.hosts = [_host(31, "failed", detail=NO_NAME, enrolled=enrolled)]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Host 31 is OTTER4"]
    assert _changes(fleet) == [("PUT", "/hosts/31/device_mapping"), ("POST", "/hosts/31/certificates/81/resend")]


@pytest.mark.parametrize(
    ("command", "said"),
    [
        (["name", "31=OTTER4"], "Host 31 is already OTTER4"),
        (["name", "--all", "OTTER"], "Nothing to name"),
        (["resend"], "Nothing to resend"),
    ],
)
def test_the_policy_is_sent_again_only_after_a_phone_is_named_or_asked_again(
    fleet: _Fleet, command: list[str], said: str
) -> None:
    """A run that changed nothing leaves the policy alone, as it would a phone still installing apps"""
    fleet.hosts = [_host(31, "verified", "OTTER4", enrolled=LATER)]

    result = CliRunner().invoke(cli_group, command)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [said]
    assert not _changes(fleet)


def test_a_policy_fleet_refuses_is_said_after_the_names(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    """The phones are named all the same, and the refusal is said as any other"""
    answer = fleet.handler

    def refusing(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("alt") == "media":
            return httpx.Response(500, text="database is locked")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(31, "failed", detail=NO_NAME, enrolled=LATER)]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 1
    assert result.output.splitlines()[0] == "Host 31 is OTTER4"
    assert "Fleet refused: GET /api/latest/fleet/configuration_profiles/g4bb?alt=media answered 500" in result.output
    assert _named(fleet) == {31: "OTTER4"}
    assert not _policy_changes(fleet)


def test_a_policy_lost_between_delete_and_upload_is_said(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    """The team then has no policy, every phone in it loses the settings, and a later run sees nothing"""
    answer = fleet.handler

    def refusing(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/configuration_profiles"):
            return httpx.Response(502, text="Bad Gateway")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(31, "failed", detail=NO_NAME, enrolled=LATER)]

    result = CliRunner().invoke(cli_group, ["name", "31=OTTER4"])

    assert result.exit_code == 1
    assert result.output.splitlines()[0] == "Host 31 is OTTER4"
    assert "may now be missing" in result.output
    assert "Run apply to put it back" in result.output
    assert not fleet.profiles


@pytest.mark.parametrize("command", [["phones"], ["name", "31=OTTER4"], ["resend"]])
def test_an_unknown_team_is_named_not_created(fleet: _Fleet, command: list[str]) -> None:
    """A typo must not leave an empty team behind for the next phone to enrol into"""
    fleet.hosts = [_host(31, "failed", detail=NO_NAME)]

    result = CliRunner().invoke(cli_group, [*command, "--team", "Typo"])

    assert result.exit_code == 1
    assert "no team named Typo" in result.output
    assert not _changes(fleet)
