"""The CLI

An operator runs these by hand, so a missing setting has to say what is missing and a refusal has
to say why, rather than end in a traceback.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from odmdm_fleet import config
from odmdm_fleet.console import cli_group

from .test_fleet import _Fleet, _host, _mdm


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fleet:
    """A configured connector talking to a stubbed Fleet whose team is "rmscep" """
    token = tmp_path / "token"
    token.write_text("token\n", encoding="utf-8")
    monkeypatch.setattr(config, "URL", "https://mdm.test")
    monkeypatch.setattr(config, "TOKEN_FILE", token)
    monkeypatch.setattr(config, "TEAM", "rmscep")
    stub = _Fleet()
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
        ("failed", NO_NAME, "waiting for a name"),
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
