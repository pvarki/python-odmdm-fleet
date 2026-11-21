"""The CLI

An operator runs these by hand, so a missing setting has to say what is missing and a refusal has
to say why, rather than end in a traceback.
"""

import codecs
import datetime
import getpass
import json
import logging
import runpy
import sys
from dataclasses import dataclass
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from odmdm_connector import __version__, console, plugins
from odmdm_connector import fleet as fleet_module
from odmdm_connector.fleet_plugin import FleetPlugin

from .test_fleet import Request, Response, _Fleet, _host, _mdm


@dataclass
class Ran:
    """What a run of the command line ended with and printed"""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


class Run:
    """console.main on a list of arguments, what is typed at its prompt and extra environment"""

    def __init__(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        self.capsys = capsys
        self.monkeypatch = monkeypatch

    def __call__(self, arguments: list[str], typed: str | None = None, env: dict[str, str] | None = None) -> Ran:
        for key, value in (env or {}).items():
            self.monkeypatch.setenv(key, value)
        if typed is not None:
            self.monkeypatch.setattr(getpass, "getpass", lambda prompt: typed)
        self.capsys.readouterr()
        try:
            code = console.main(arguments)
        except SystemExit as exc:
            code = int(exc.code or 0)
        out, err = self.capsys.readouterr()
        return Ran(code, out, err)


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> Run:
    return Run(capsys, monkeypatch)


#: The certificate subject the template loader accepts: the callsign the operator gave the phone
NAMED = "CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART"


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fleet:
    """A configured connector talking to a stubbed Fleet whose team "rmscep" has its certificate template

    And its policy, last sent after the time _host gives a phone by default.
    """
    token = tmp_path / "token"
    token.write_text("token\n", encoding="utf-8")
    monkeypatch.setenv("ODMDM_MDM_URL", "https://mdm.test")
    monkeypatch.setenv("ODMDM_TOKEN_FILE", str(token))
    monkeypatch.setenv("ODMDM_TEAM", "rmscep")
    stub = _Fleet()
    stub.authorities = [{"id": 3, "name": "RMSCEP", "url": "https://example.fi/scep"}]
    stub.certificates = [{"id": 81, "name": "rmscep", "subject_name": NAMED, "certificate_authority_id": 3}]
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


@pytest.mark.parametrize("command", [["apply"], ["challenge"]])
def test_a_domain_is_required(command: list[str], monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """Without it there is nothing to state to Fleet; the template defaults to the one shipped"""
    monkeypatch.delenv("ODMDM_DOMAIN", raising=False)

    result = run(command, env={"ODMDM_CHALLENGE": "new-window"})

    assert result.exit_code == 1
    assert "ODMDM_DOMAIN" in result.stderr


def _template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "template.json"
    path.write_text(
        json.dumps(
            {
                "apps": [{"package": "com.example.one"}],
                "policy": {"defaultPermissionPolicy": "GRANT"},
                "certificate": {"name": "{key_alias}", "subject": NAMED, "authority": "RMSCEP"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ODMDM_TEMPLATE", str(path))
    monkeypatch.setenv("ODMDM_DOMAIN", "example.fi")


def test_apply_states_the_template(fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """One team: phones enrol into the team that has the certificate template"""
    _template(tmp_path, monkeypatch)
    monkeypatch.setenv("ODMDM_CHALLENGE", "new-window")

    result = run(["apply"])

    assert result.exit_code == 0, result.output
    assert "Applied to team 7" in result.output
    assert fleet.authorities[0]["challenge"] == "new-window"
    assert fleet.authorities[0]["url"] == "https://example.fi/scep"


def test_a_dry_run_never_reaches_fleet(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """Checking an edited template must not change the estate"""
    _template(tmp_path, monkeypatch)

    result = run(["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not fleet.calls


def test_a_new_window_touches_only_the_authority(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """A rewritten policy stops apps installing on phones enrolling right now"""
    _template(tmp_path, monkeypatch)

    result = run(["challenge"], typed="new-window")

    assert result.exit_code == 0, result.output
    assert "new-window" not in result.output, "asked for without echoing it"
    assert fleet.authorities[0]["challenge"] == "new-window"
    changed = {path for method, path in fleet.calls if method != "GET"}
    assert changed == {"/api/latest/fleet/certificate_authorities/3"}


def test_phones_needs_somewhere_to_ask(monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """No Fleet configured is a sentence, not a traceback"""
    monkeypatch.delenv("ODMDM_MDM_URL", raising=False)

    result = run(["phones"])

    assert result.exit_code == 1
    assert "Fleet address is missing: give --set mdm_url=... or set ODMDM_MDM_URL" in result.stderr


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
        ("failed", NO_NAME, "named, but its certificate was not asked for again: ask again"),
        ("pending", "", "requested, waiting for the phone"),
        ("delivering", "", "requested, waiting for the phone"),
        ("delivered", "", "requested, waiting for the phone"),
        ("pending", REFUSED, "requested, waiting for the phone"),
        ("delivered", UNREACHED, "requested, waiting for the phone"),
        ("verified", "", "has its certificate"),
        (
            "failed",
            REFUSED,
            "refused by Deploy App: name taken or not allowed, enrolment closed, or Fleet has an old challenge",
        ),
        ("failed", UNREACHED, "could not reach Deploy App"),
        ("failed", "", "failed"),
        (
            "failed",
            "Something Fleet says that nobody has seen yet",
            "failed: Something Fleet says that nobody has seen yet",
        ),
    ],
)
def test_phones_says_where_each_certificate_is(fleet: _Fleet, status: str, detail: str, says: str, run: Run) -> None:
    """In words an operator can act on, rather than Fleet's status and detail"""
    fleet.hosts = [_host(31, status, "OTTER4", detail)]

    result = run(["phones"])

    assert result.exit_code == 0, result.output
    assert result.output.split()[:4] == ["31", "OTTER4", "Google", "sdk_gphone64_arm64"]
    assert result.output.rstrip("\n").split("2026-11-01  ", 1)[1] == says


def test_a_phone_fresh_from_enrolment_waits_for_a_name(fleet: _Fleet, run: Run) -> None:
    """Its first request fails for want of one"""
    fleet.hosts = [_host(31, "failed", detail=NO_NAME)]

    result = run(["phones"])

    assert result.exit_code == 0, result.output
    assert result.output.split()[:2] == ["31", "-"]
    assert result.output.rstrip().endswith("waiting for a name")


def test_phones_lists_in_enrolment_order_with_the_callsign_fleet_issues(fleet: _Fleet, run: Run) -> None:
    """The certificate carries the name up to its "@"; an old remove row is history, not state"""
    fleet.hosts = [
        _host(32, "verified", "OTTER1@CODE", enrolled="2026-11-02T08:00:00Z"),
        _host(33, enrolled="2026-11-01T08:00:00Z"),
    ]
    fleet.hosts[0]["mdm"]["profiles"].insert(
        0, {"name": "rmscep", "status": "failed", "operation_type": "remove", "detail": REFUSED}
    )

    result = run(["phones"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split()[:2] == ["33", "-"]
    assert lines[1].split()[:2] == ["32", "OTTER1"]
    assert lines[1].endswith("has its certificate")


def test_an_empty_team_says_so(fleet: _Fleet, run: Run) -> None:
    """Rather than printing nothing, which reads as a failure"""
    result = run(["phones"])

    assert result.exit_code == 0, result.output
    assert "No phones in the team" in result.output


def _named(fleet: _Fleet) -> dict[int, str]:
    return {h["id"]: h["end_users"][0]["idp_username"] if h["end_users"] else "" for h in fleet.hosts}


def _changes(fleet: _Fleet) -> list[tuple[str, str]]:
    return [(method, path.removeprefix("/api/latest/fleet")) for method, path in fleet.calls if method != "GET"]


def test_name_gives_any_number_of_phones_their_callsigns(fleet: _Fleet, run: Run) -> None:
    """In any order, each named and then asked for its certificate again under the name"""
    fleet.hosts = [_host(host, "failed", detail=NO_NAME) for host in (31, 32, 33)]

    result = run(["name", "33=OTTER5", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Phone 33 is OTTER5", "Phone 31 is OTTER4"]
    assert _named(fleet) == {31: "OTTER4", 32: "", 33: "OTTER5"}
    assert _changes(fleet) == [
        ("PUT", "/hosts/33/device_mapping"),
        ("POST", "/hosts/33/certificates/81/resend"),
        ("PUT", "/hosts/31/device_mapping"),
        ("POST", "/hosts/31/certificates/81/resend"),
    ]


def test_a_request_still_pending_takes_the_name_without_a_resend(fleet: _Fleet, run: Run) -> None:
    """Fleet refuses to resend it, and it needs none: it is filled in when it goes"""
    fleet.hosts = [_host(31, "pending")]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Phone 31 is OTTER4"]
    assert _named(fleet) == {31: "OTTER4"}


def test_a_phone_with_its_certificate_under_the_name_is_left_alone(fleet: _Fleet, run: Run) -> None:
    """Asked again under the name it holds, it is refused as taken and then reads as refused"""
    fleet.hosts = [_host(31, "verified", "OTTER4@CODE"), _host(32, "failed", detail=NO_NAME)]

    result = run(["name", "31=otter4", "32=OTTER5"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Phone 31 is already OTTER4", "Phone 32 is OTTER5"]
    assert _changes(fleet) == [("PUT", "/hosts/32/device_mapping"), ("POST", "/hosts/32/certificates/81/resend")]
    assert _named(fleet)[31] == "OTTER4@CODE"


def test_a_phone_with_no_request_is_named_and_the_batch_goes_on(fleet: _Fleet, run: Run) -> None:
    """Fleet has no request to send it again, and fills the name in when it makes one"""
    fleet.hosts = [_host(31, "failed", detail=NO_NAME), _host(32), _host(33, "failed", detail=NO_NAME)]

    result = run(["name", "--all", "OTTER"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Phone 31 is OTTER1",
        "Phone 32 is OTTER2. Fleet has no certificate request for it, so nothing was sent",
        "Phone 33 is OTTER3",
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
    fleet: _Fleet, first: str, start: list[str], expected: dict[int, str], run: Run
) -> None:
    """In enrolment order, then host id, from one above the highest number in use unless told"""
    fleet.hosts = [
        _host(40, "verified", first, enrolled="2026-11-01T08:00:00Z"),
        _host(41, "failed", detail=NO_NAME, enrolled="2026-11-03T08:00:00Z"),
        _host(43, "failed", detail=NO_NAME, enrolled="2026-11-02T08:00:00Z"),
        _host(42, "failed", detail=NO_NAME, enrolled="2026-11-02T08:00:00Z"),
    ]

    result = run(["name", "--all", "OTTER", *start])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [f"Phone {host} is {callsign}" for host, callsign in expected.items()]
    assert _named(fleet) == {40: first, **expected}


def test_name_all_with_every_phone_named_names_nothing(fleet: _Fleet, run: Run) -> None:
    """A second run finds nobody waiting, and changes nothing"""
    fleet.hosts = [_host(31, "verified", "OTTER1")]

    result = run(["name", "--all", "OTTER"])

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
        (["31=OTTER4", "31=OTTER5"], "phone 31 is named twice"),
        (["31=otter9"], "otter9 already belongs to phone 33"),
        (["--all", "OTTER", "--start", "9"], "OTTER9 already belongs to phone 33"),
        (["31=OTTER4", "99=OTTER5"], "phone 99 is not in the group"),
    ],
)
def test_name_changes_nothing_unless_every_name_can_be_given(
    fleet: _Fleet, arguments: list[str], says: str, run: Run
) -> None:
    """A typo refuses the whole command, rather than leaving half a batch named"""
    fleet.hosts = [
        _host(31, "failed", detail=NO_NAME),
        _host(32, "failed", detail=NO_NAME),
        _host(33, "verified", "OTTER9@CODE"),
    ]

    result = run(["name", *arguments])

    assert result.exit_code == 1
    assert f"Nothing named: {says}" in result.stderr
    assert not _changes(fleet)


@pytest.mark.parametrize("arguments", [[], ["31=OTTER4", "--all", "OTTER"], ["31=OTTER4", "--start", "3"], ["OTTER4"]])
def test_name_says_how_it_is_used(fleet: _Fleet, arguments: list[str], run: Run) -> None:
    """Before Fleet is asked anything"""
    result = run(["name", *arguments])

    assert result.exit_code == 2
    assert not fleet.calls


def test_renaming_a_phone_with_its_certificate_says_what_to_revoke(fleet: _Fleet, run: Run) -> None:
    """The old callsign stays valid in Deploy App until an admin revokes it there"""
    fleet.hosts = [_host(31, "verified", "OTTER3")]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Phone 31 is OTTER4. It was OTTER3 and still has that certificate: revoke OTTER3 in Deploy App."
    ]


def test_renaming_a_refused_phone_says_nothing_to_revoke(fleet: _Fleet, run: Run) -> None:
    """A refused name was never issued to this phone, and may be another phone's"""
    fleet.hosts = [_host(31, "failed", "OTTER3", REFUSED)]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Phone 31 is OTTER4"]


def test_name_without_a_certificate_template_says_so(fleet: _Fleet, run: Run) -> None:
    """Before a phone is named, so nothing is left half done"""
    fleet.certificates = []
    fleet.hosts = [_host(31, "failed", detail=NO_NAME)]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 1
    assert "no certificate template named rmscep: set Fleet up first" in result.stderr
    assert not _changes(fleet)


def test_resend_asks_again_for_every_named_phone_that_failed(fleet: _Fleet, run: Run) -> None:
    """A phone named in Fleet by hand is one of them: naming alone does not send the request again"""
    fleet.hosts = [
        _host(31, "failed", "OTTER4", REFUSED),
        _host(32, "failed", detail=NO_NAME),
        _host(33, "verified", "OTTER5"),
        _host(34, "failed", "OTTER6", NO_NAME),
        _host(35, "pending", "OTTER7"),
    ]

    result = run(["resend"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Phone 31 (OTTER4): certificate asked for again",
        "Phone 34 (OTTER6): certificate asked for again",
    ]
    assert _changes(fleet) == [
        ("POST", "/hosts/31/certificates/81/resend"),
        ("POST", "/hosts/34/certificates/81/resend"),
    ]


def test_resend_given_hosts_asks_for_those(fleet: _Fleet, run: Run) -> None:
    """Whatever state they are in: the operator chose them"""
    fleet.hosts = [_host(31, "failed", "OTTER4", REFUSED), _host(32, "failed", detail=NO_NAME)]

    result = run(["resend", "32"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Phone 32 (no name): certificate asked for again"]
    assert _changes(fleet) == [("POST", "/hosts/32/certificates/81/resend")]


@pytest.mark.parametrize(("arguments", "says", "code"), [([], "Nothing to resend", 0), (["99"], "not in the team", 1)])
def test_resend_says_when_it_sends_nothing(fleet: _Fleet, arguments: list[str], says: str, code: int, run: Run) -> None:
    """Nobody failed, or a host that is not in the team: said, and nothing sent"""
    fleet.hosts = [_host(31, "verified", "OTTER4")]

    result = run(["resend", *arguments])

    assert result.exit_code == code, result.output
    assert says in result.output
    assert not _changes(fleet)


#: After the fixture's policy was last sent
LATER = "2026-11-20T21:10:02Z"


def _policy_changes(fleet: _Fleet) -> list[tuple[str, str]]:
    return [change for change in _changes(fleet) if "/configuration_profiles" in change[1]]


def test_name_sends_the_policy_again_when_a_phone_enrolled_after_it(fleet: _Fleet, run: Run) -> None:
    """Fleet can write a bare policy over a new phone's, and never sends ours again by itself

    Measured: the certificate installs, Chrome is not allowed to use it, and Deploy App says
    "Certificate Authentication Failed". Fleet's own copy uploaded again reaches the phone.
    """
    fleet.hosts = [
        _host(31, "failed", detail=NO_NAME, enrolled=LATER),
        _host(32, "failed", detail=NO_NAME, enrolled=LATER),
        _host(33, "verified", "OTTER1"),
    ]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Phone 31 is OTTER4",
        "Policy sent again: 2 phones enrolled after it was last sent",
    ]
    assert _policy_changes(fleet) == [("DELETE", "/configuration_profiles/g4bb"), ("POST", "/configuration_profiles")]
    assert [profile["raw"] for profile in fleet.profiles] == [{"defaultPermissionPolicy": "GRANT"}]


def test_resend_sends_the_policy_again_when_a_phone_enrolled_after_it(fleet: _Fleet, run: Run) -> None:
    """The phone asked again may be one whose policy Fleet overwrote"""
    fleet.hosts = [_host(31, "failed", "OTTER4", REFUSED, enrolled=LATER), _host(32, "verified", "OTTER5")]

    result = run(["resend"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Phone 31 (OTTER4): certificate asked for again",
        "Policy sent again: 1 phone enrolled after it was last sent",
    ]
    assert _policy_changes(fleet) == [("DELETE", "/configuration_profiles/g4bb"), ("POST", "/configuration_profiles")]


def test_a_policy_sent_again_is_then_left_alone(fleet: _Fleet, run: Run) -> None:
    """Fleet stamps an upload with its own clock, so the policy is newer than every phone already there

    Sent on every run, it would be rewritten under phones still installing their apps.
    """
    fleet.now = "2026-11-20T21:30:00.123456Z"
    fleet.hosts = [
        _host(31, "failed", detail=NO_NAME, enrolled=LATER),
        _host(32, "failed", detail=NO_NAME, enrolled=LATER),
    ]

    first = run(["name", "31=OTTER4"])
    fleet.calls.clear()
    second = run(["name", "32=OTTER5"])

    assert first.stdout.splitlines()[-1] == "Policy sent again: 2 phones enrolled after it was last sent"
    assert second.exit_code == 0, second.output
    assert second.stdout.splitlines() == ["Phone 32 is OTTER5"]
    assert not _policy_changes(fleet)


@pytest.mark.parametrize(("policy", "enrolled"), [(True, "2026-11-01T19:38:12Z"), (False, LATER)])
def test_the_policy_is_left_alone_unless_a_phone_enrolled_after_it(
    fleet: _Fleet, policy: bool, enrolled: str, run: Run
) -> None:
    """Every phone enrolled before it was last sent, or the team has no policy to send"""
    if not policy:
        fleet.profiles = []
    fleet.hosts = [_host(31, "failed", detail=NO_NAME, enrolled=enrolled)]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["Phone 31 is OTTER4"]
    assert _changes(fleet) == [("PUT", "/hosts/31/device_mapping"), ("POST", "/hosts/31/certificates/81/resend")]


@pytest.mark.parametrize(
    ("command", "said"),
    [
        (["name", "31=OTTER4"], "Phone 31 is already OTTER4"),
        (["name", "--all", "OTTER"], "Nothing to name"),
        (["resend"], "Nothing to resend"),
    ],
)
def test_the_policy_is_sent_again_only_after_a_phone_is_named_or_asked_again(
    fleet: _Fleet, command: list[str], said: str, run: Run
) -> None:
    """A run that changed nothing leaves the policy alone, as it would a phone still installing apps"""
    fleet.hosts = [_host(31, "verified", "OTTER4", enrolled=LATER)]

    result = run(command)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [said]
    assert not _changes(fleet)


def test_a_policy_fleet_refuses_is_said_after_the_names(
    fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """The phones are named all the same, and the refusal is said as any other"""
    answer = fleet.handler

    def refusing(request: Request) -> Response:
        if request.url.params.get("alt") == "media":
            return Response(500, text="database is locked")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(31, "failed", detail=NO_NAME, enrolled=LATER)]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 1
    assert result.output.splitlines()[0] == "Phone 31 is OTTER4"
    assert "Fleet refused: GET /api/latest/fleet/configuration_profiles/g4bb?alt=media answered 500" in result.stderr
    assert _named(fleet) == {31: "OTTER4"}
    assert not _policy_changes(fleet)


def test_a_policy_lost_between_delete_and_upload_is_said(
    fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """The team then has no policy, every phone in it loses the settings, and a later run sees nothing"""
    answer = fleet.handler

    def refusing(request: Request) -> Response:
        if request.method == "POST" and request.url.path.endswith("/configuration_profiles"):
            return Response(502, text="Bad Gateway")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(31, "failed", detail=NO_NAME, enrolled=LATER)]

    result = run(["name", "31=OTTER4"])

    assert result.exit_code == 1
    assert result.output.splitlines()[0] == "Phone 31 is OTTER4"
    assert "may now be missing" in result.stderr
    assert "Run apply to put it back" in result.stderr
    assert not fleet.profiles


@pytest.mark.parametrize("command", [["phones"], ["name", "31=OTTER4"], ["resend"]])
def test_an_unknown_team_is_named_not_created(fleet: _Fleet, command: list[str], run: Run) -> None:
    """A typo must not leave an empty team behind for the next phone to enrol into"""
    fleet.hosts = [_host(31, "failed", detail=NO_NAME)]

    result = run([*command, "--team", "Typo"])

    assert result.exit_code == 1
    assert "no team named Typo" in result.stderr
    assert not _changes(fleet)


def test_the_mdm_address_must_be_https(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """The token goes with every request, so it must not cross the network readable"""
    monkeypatch.setenv("ODMDM_MDM_URL", "http://mdm.test")

    result = run(["phones"])

    assert result.exit_code == 1
    assert "must start with https://" in result.stderr
    assert not fleet.calls


@pytest.mark.parametrize("start", ["0", "-3"])
def test_name_all_starts_from_one_or_more(fleet: _Fleet, start: str, run: Run) -> None:
    """Callsigns are numbered from 1"""
    result = run(["name", "--all", "OTTER", "--start", start])

    assert result.exit_code == 2
    assert not fleet.calls


@pytest.mark.parametrize("count", [4, 5])
def test_phones_reads_every_page_of_hosts(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, count: int, run: Run) -> None:
    """A team bigger than one page is listed whole, also when the last page is full"""
    monkeypatch.setattr(fleet_module, "HOSTS_PER_PAGE", 2)
    fleet.hosts = [_host(31 + n, "verified", f"OTTER{n + 1}") for n in range(count)]

    result = run(["phones"])

    assert result.exit_code == 0, result.output
    assert [line.split()[0] for line in result.output.splitlines()] == [str(31 + n) for n in range(count)]


def test_a_batch_stopped_half_way_says_which_phones_are_named(
    fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """So the operator names only the rest, rather than guessing"""
    answer = fleet.handler

    def refusing(request: Request) -> Response:
        if request.url.path.endswith("/hosts/32/certificates/81/resend"):
            return Response(500, text="database is locked")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(host, "failed", detail=NO_NAME) for host in (31, 32, 33)]

    result = run(["name", "--all", "OTTER"])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == ["Phone 31 is OTTER1"]
    assert "Fleet refused: POST /api/latest/fleet/hosts/32/certificates/81/resend answered 500" in result.stderr
    assert "Already named before it stopped: 31=OTTER1 32=OTTER2. phones shows where each is" in result.stderr
    assert _named(fleet) == {31: "OTTER1", 32: "OTTER2", 33: ""}


def test_a_rename_stopped_half_way_still_says_what_to_revoke(
    fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """Nothing shows the old callsign afterwards, so its line must not be lost with the error"""
    answer = fleet.handler

    def refusing(request: Request) -> Response:
        if request.url.path.endswith("/hosts/32/certificates/81/resend"):
            return Response(500, text="database is locked")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(31, "verified", "OTTER3"), _host(32, "failed", detail=NO_NAME)]

    result = run(["name", "31=OTTER4", "32=OTTER5"])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        "Phone 31 is OTTER4. It was OTTER3 and still has that certificate: revoke OTTER3 in Deploy App."
    ]
    assert "Fleet refused: POST /api/latest/fleet/hosts/32/certificates/81/resend answered 500" in result.stderr


def test_a_resend_stopped_half_way_says_what_was_asked(
    fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """The phones asked before the error are said as on success"""
    answer = fleet.handler

    def refusing(request: Request) -> Response:
        if request.url.path.endswith("/hosts/34/certificates/81/resend"):
            return Response(500, text="database is locked")
        return answer(request)

    monkeypatch.setattr(fleet, "handler", refusing)
    _mdm(fleet, monkeypatch)
    fleet.hosts = [_host(31, "failed", "OTTER31", REFUSED), _host(34, "failed", "OTTER34", REFUSED)]

    result = run(["resend"])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == ["Phone 31 (OTTER31): certificate asked for again"]
    assert "Fleet refused: POST /api/latest/fleet/hosts/34/certificates/81/resend answered 500" in result.stderr


LINE = (Path(__file__).parent / "data" / "settings-line-v1.txt").read_text(encoding="utf-8").strip()
SECRET = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret


@pytest.fixture
def window(fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fleet:
    """The stub Fleet, a template, the vector's domain, and a clock before its window closes"""
    _template(tmp_path, monkeypatch)
    monkeypatch.setenv("ODMDM_DOMAIN", "unit.example.fi")
    monkeypatch.setattr(console, "_now", lambda: datetime.datetime(2026, 11, 21, 12, 0, tzinfo=datetime.UTC))
    fleet.authorities[0]["url"] = "https://unit.example.fi/scep"
    return fleet


@pytest.mark.parametrize(
    ("arguments", "typed", "env"),
    [
        ([], LINE, {}),
        ([], None, {"ODMDM_CHALLENGE": LINE}),
    ],
)
def test_challenge_takes_the_settings_line(
    window: _Fleet,
    caplog: pytest.LogCaptureFixture,
    arguments: list[str],
    typed: str | None,
    env: dict[str, str],
    run: Run,
) -> None:
    """Typed at the hidden prompt or from the environment"""
    result = run(["-vv", "challenge", *arguments], typed=typed, env=env)

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == (
        "Fleet has the new challenge. The enrolment window is open until 2026-11-21 14:00"
    )
    assert window.authorities[0]["challenge"] == SECRET
    assert window.authorities[0]["url"] == "https://unit.example.fi/scep"
    assert SECRET not in result.output
    assert SECRET not in caplog.text


def test_challenge_is_never_an_argument(window: _Fleet, run: Run) -> None:
    """An argument is in the process list and the shell history"""
    result = run(["challenge", "--challenge", LINE])

    assert result.exit_code == 2
    assert "unrecognized arguments: --challenge" in result.stderr
    assert not window.calls


@pytest.mark.parametrize(
    ("line", "says"),
    [
        (LINE.replace("unit.example.fi", "other.example.fi"), "is for other.example.fi, not unit.example.fi"),
        (LINE.replace("https://", "http://"), "must be https://<host>/<path>"),
        (LINE.replace("2026-11-21T14", "2026-11-21T11"), "closed at 11:00"),
        (LINE.replace("ODMDM1", "ODMDM2"), "not a settings line"),
        (LINE.replace("ODMDM1", "odmdm1"), "not a settings line"),
        (LINE.split(None, 1)[1], "not a settings line"),
        ('"' + LINE + '"', "not a settings line"),
    ],
)
def test_challenge_refuses_a_line_it_cannot_use(window: _Fleet, line: str, says: str, run: Run) -> None:
    """Before Fleet is asked anything, and without the challenge in the message"""
    result = run(["challenge"], typed=line)

    assert result.exit_code == 1
    assert says in result.stderr
    assert SECRET not in result.output
    assert not window.calls


def test_apply_takes_the_settings_line_from_the_environment(
    window: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    """ODMDM_CHALLENGE may hold the line; apply must not send the whole line as the challenge"""
    monkeypatch.setenv("ODMDM_CHALLENGE", LINE)

    result = run(["apply"])

    assert result.exit_code == 0, result.output
    assert window.authorities[0]["challenge"] == SECRET
    assert SECRET not in result.output


def test_apply_refuses_a_line_for_another_deployment(window: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    monkeypatch.setenv("ODMDM_CHALLENGE", LINE.replace("unit.example.fi", "other.example.fi"))

    result = run(["apply"])

    assert result.exit_code == 1
    assert "is for other.example.fi" in result.stderr
    assert not window.calls


@pytest.mark.parametrize(
    ("options", "env", "says"),
    [
        (["--mdm", "nope"], {}, "No MDM named nope is installed"),
        ([], {"ODMDM_MDM": "nope"}, "No MDM named nope is installed"),
        (["--set", "nope=1"], {}, "fleet has no setting nope; it has key_alias, mdm_url, team, template, token"),
        (["--set", "team"], {}, "--set takes KEY=VALUE"),
        (["--set", f"token={SECRET}"], {}, "token is a secret: put it in a file and set ODMDM_TOKEN_FILE"),
    ],
)
def test_the_mdm_and_its_settings_are_checked_first(
    fleet: _Fleet, options: list[str], env: dict[str, str], says: str, run: Run
) -> None:
    """Before the MDM is asked anything, and without a value in the message"""
    result = run([*options, "phones"], env=env)

    assert result.exit_code == 2
    assert says in result.stderr
    assert SECRET not in result.output
    assert not fleet.calls


def test_set_gives_an_mdm_setting(fleet: _Fleet, run: Run) -> None:
    """The same as the command's own shorthand, here --team"""
    result = run(["--set", "team=Typo", "phones"])

    assert result.exit_code == 1
    assert "no team named Typo" in result.stderr


def test_a_secret_is_never_read_from_a_plain_variable(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """Only from the file its _FILE variable names"""
    monkeypatch.delenv("ODMDM_TOKEN_FILE")

    result = run(["phones"], env={"ODMDM_TOKEN": "token"})

    assert result.exit_code == 1
    assert "API token is missing: put it in a file and set ODMDM_TOKEN_FILE to that file" in result.stderr
    assert not fleet.calls


def test_a_secret_with_a_line_break_is_refused_and_never_shown(
    fleet: _Fleet, tmp_path: Path, run: Run, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token = tmp_path / "broken.token"
    token.write_text("TOPSECRETA\nTOPSECRETB\n", encoding="utf-8")

    result = run(["phones"], env={"ODMDM_TOKEN_FILE": str(token)})

    assert result.exit_code == 1
    assert result.stderr == (
        "The API token holds a line break or another character that cannot be part of it: copy it again.\n"
    )
    assert "TOPSECRET" not in result.output + caplog.text
    assert not fleet.calls


@pytest.mark.parametrize(
    "written",
    [
        b"TOPSECRET\n",
        codecs.BOM_UTF8 + b"TOPSECRET\r\n",
        "TOPSECRET\r\n".encode("utf-16"),
        codecs.BOM_UTF16_BE + "TOPSECRET".encode("utf-16-be"),
    ],
    ids=["utf-8", "utf-8 with its mark", "utf-16 as PowerShell 5.1 writes", "utf-16 big-endian"],
)
def test_a_token_file_is_read_in_the_encodings_editors_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: bytes
) -> None:
    token = tmp_path / "token"
    token.write_bytes(written)
    monkeypatch.setenv("ODMDM_TOKEN_FILE", str(token))

    assert console._values("fleet", FleetPlugin.fields, {})["token"] == "TOPSECRET"


@pytest.mark.parametrize("unreadable", ["not text", "not readable"])
def test_a_token_file_that_cannot_be_read_as_text_is_said_without_the_token(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Run, unreadable: str
) -> None:
    token = tmp_path / "broken.token"
    token.write_bytes(b"TOPSECRET\xff\xfe")
    if unreadable == "not readable":
        token.write_text("TOPSECRET", encoding="utf-8")

        def refuse(self: Path) -> bytes:
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "read_bytes", refuse)

    result = run(["phones"], env={"ODMDM_TOKEN_FILE": str(token)})

    assert result.exit_code == 1
    assert result.stderr == f"The file {token} cannot be read as plain text: save the API token in it as plain text.\n"
    assert "TOPSECRET" not in result.output
    assert not fleet.calls


@pytest.mark.parametrize(
    ("command", "capability"),
    [(["challenge"], "set_challenge"), (["apply"], "setup"), (["resend"], "ask_again")],
)
def test_a_capability_the_mdm_lacks_is_its_sentence(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str], capability: str, run: Run
) -> None:
    """Said instead of calling the MDM, and before a challenge is asked for"""
    _template(tmp_path, monkeypatch)
    monkeypatch.setattr(FleetPlugin, "missing", {capability: "Paste the challenge in the MDM's console by hand"})

    result = run(command)

    assert result.exit_code == 1
    assert result.stderr == "Paste the challenge in the MDM's console by hand\n"
    assert not fleet.calls


def test_a_dry_run_needs_no_mdm(monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """The template shipped with the connector, checked with nothing configured but the domain"""
    for setting in ("MDM_URL", "TOKEN_FILE", "TEMPLATE"):
        monkeypatch.delenv(f"ODMDM_{setting}", raising=False)
    monkeypatch.setenv("ODMDM_DOMAIN", "example.fi")

    result = run(["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "Template names 3 apps, 3 installed at enrolment"
    assert result.stdout.splitlines()[-1] == "  launcher link 'Deploy App' -> https://mtls.example.fi/"


def _declare(monkeypatch: pytest.MonkeyPatch, *found: tuple[str, str, str]) -> None:
    """These MDMs installed and no other, each as name, package and module:class"""
    declared = [
        plugins.Plugin(name, package, "0.1.0", EntryPoint(name, value, plugins.GROUP)) for name, package, value in found
    ]
    monkeypatch.setattr(plugins, "_declared", lambda: declared)


FAKE = ("fake", "odmdm-connector-fake", "odmdm_connector.testing.fake:FakeMdm")


def test_mdms_lists_what_is_installed_without_loading_it(monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    _declare(
        monkeypatch,
        ("zeta", "odmdm-connector-zeta", "never_imported:Mdm"),
        ("fleet", "odmdm-connector", "odmdm_connector.never_imported:Mdm"),
    )

    result = run(["mdms"])

    assert result.exit_code == 0, result.output
    assert [line.split() for line in result.stdout.splitlines()] == [
        ["fleet", "odmdm-connector", "0.1.0", "built", "in"],
        ["zeta", "odmdm-connector-zeta", "0.1.0", "plugin"],
    ]


def test_a_plugin_says_whose_code_it_is_before_it_runs(monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """On stderr, so what the command prints stays the same"""
    _declare(monkeypatch, FAKE)

    result = run(["--mdm", "fake", "phones"])

    assert result.exit_code == 0, result.output
    assert result.stderr == "The MDM fake is a plugin from odmdm-connector-fake 0.1.0: code that sees the MDM token\n"
    assert [line.split()[:2] for line in result.stdout.splitlines()] == [["a1", "-"], ["a2", "-"], ["a3", "OTTER1"]]


def test_the_built_in_mdm_says_nothing_of_itself(fleet: _Fleet, run: Run) -> None:
    fleet.hosts = [_host(31)]

    result = run(["phones"])

    assert result.exit_code == 0, result.output
    assert not result.stderr


@pytest.mark.parametrize(
    ("found", "command", "says"),
    [
        ([FAKE, ("fake", "odmdm-connector-other", "never_imported:Mdm")], ["mdms"], "odmdm-connector-fake 0.1.0 and"),
        ([FAKE, ("fake", "odmdm-connector-other", "never_imported:Mdm")], ["--mdm", "fake", "phones"], "Uninstall one"),
        (
            [("old", "odmdm-connector-old", "tests.test_plugins:Old")],
            ["--mdm", "old", "phones"],
            "speaks MDM API version 2",
        ),
    ],
)
def test_a_plugin_the_connector_cannot_use_is_said(
    monkeypatch: pytest.MonkeyPatch, found: list[tuple[str, str, str]], command: list[str], says: str, run: Run
) -> None:
    _declare(monkeypatch, *found)

    result = run(command)

    assert result.exit_code == 1
    assert says in result.stderr


def test_python_m_runs_the_command_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The way to run it from a zip application or a checkout, without the installed script"""
    monkeypatch.setattr(sys, "argv", ["odmdm-connector", "--version"])

    with pytest.raises(SystemExit) as ended:
        runpy.run_module("odmdm_connector", run_name="__main__")

    assert ended.value.code == 0
    assert capsys.readouterr().out == f"odmdm-connector, version {__version__}\n"


def test_ctrl_c_at_the_prompt_says_it_stopped(window: _Fleet, monkeypatch: pytest.MonkeyPatch, run: Run) -> None:
    """A sentence rather than a traceback, and the MDM is not asked"""

    def interrupted(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(getpass, "getpass", interrupted)

    result = run(["challenge"])

    assert result.exit_code == 1
    assert result.stderr == "Stopped before the command finished.\n"
    assert not window.calls


@pytest.mark.parametrize("level", ["debug", "", "1.5"])
def test_a_log_level_that_is_not_a_number_is_said(run: Run, level: str) -> None:
    result = run(["mdms"], env={"ODMDM_LOG_LEVEL": level})

    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"{console.LOG_LEVEL}\n")
