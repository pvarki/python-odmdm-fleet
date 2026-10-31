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
    """A configured connector talking to a stubbed Fleet whose preparation team is "prep" """
    token = tmp_path / "token"
    token.write_text("token\n", encoding="utf-8")
    monkeypatch.setattr(config, "URL", "https://mdm.test")
    monkeypatch.setattr(config, "TOKEN_FILE", token)
    monkeypatch.setattr(config, "PREP_TEAM", "prep")
    stub = _Fleet()
    _mdm(stub, monkeypatch)
    return stub


@pytest.mark.parametrize("callsign", ["OT", "OTTER-", "OTTER_1", "X" * 33])
def test_plan_refuses_a_callsign_the_deployment_would(callsign: str) -> None:
    """Now, rather than as a failed certificate twenty minutes later, and before Fleet is touched"""
    result = CliRunner().invoke(cli_group, ["plan", callsign])

    assert result.exit_code == 1
    assert "3-32 letters" in result.output


def test_plan_names_the_waiting_phone(fleet: _Fleet) -> None:
    """The one in your hand is the only one waiting"""
    fleet.hosts = [_host(4, "failed", named=False)]

    result = CliRunner().invoke(cli_group, ["plan", "OTTER4"])

    assert result.exit_code == 0, result.output
    assert "Host 4 is OTTER4" in result.output
    assert fleet.hosts[0]["device_mapping"][0]["email"] == "OTTER4"


def test_plan_says_why_it_named_nobody(fleet: _Fleet) -> None:
    """Two phones waiting: the operator learns to enrol them one at a time"""
    fleet.hosts = [_host(4, "failed", named=False), _host(5, "failed", named=False)]

    result = CliRunner().invoke(cli_group, ["plan", "OTTER4"])

    assert result.exit_code == 1
    assert "one at a time" in result.output


def test_plan_needs_somewhere_to_send_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Fleet configured is a sentence, not a traceback"""
    monkeypatch.setattr(config, "URL", "")

    result = CliRunner().invoke(cli_group, ["plan", "OTTER4"])

    assert result.exit_code == 1
    assert "ODMDM_FLEET_URL" in result.output


@pytest.mark.parametrize("command", ["apply", "reconcile"])
def test_a_template_and_domain_are_required(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without them there is nothing to state to Fleet"""
    monkeypatch.setattr(config, "TEMPLATE", None)

    result = CliRunner().invoke(cli_group, [command])

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


def test_apply_states_the_template_to_both_teams(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The certificate team gets the certificate, the preparation team everything else"""
    _template(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "CHALLENGE", "new-window")

    result = CliRunner().invoke(cli_group, ["apply", "--team", "rmscep"])

    assert result.exit_code == 0, result.output
    assert "Applied to team 7" in result.output
    assert "Applied to preparation team 8" in result.output
    assert fleet.authorities[0]["challenge"] == "new-window"
    assert fleet.authorities[0]["url"] == "https://example.fi/scep"


def test_a_dry_run_never_reaches_fleet(fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checking an edited template must not change the estate"""
    _template(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli_group, ["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not fleet.calls


def test_reconcile_leaves_fleet_alone_when_nobody_waits(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-asking reaches every phone in the team, so it happens only when it changes something"""
    _template(tmp_path, monkeypatch)
    fleet.hosts = [_host(4, "verified", named=True)]

    result = CliRunner().invoke(cli_group, ["reconcile", "--team", "rmscep"])

    assert result.exit_code == 0, result.output
    assert "Nothing waiting" in result.output
    assert not [call for call in fleet.calls if call[0] != "GET"]


def test_reconcile_asks_again_for_a_phone_named_late(
    fleet: _Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named after its first request failed: recreating the template is what asks again"""
    _template(tmp_path, monkeypatch)
    fleet.hosts = [_host(4, "failed", named=True)]

    result = CliRunner().invoke(cli_group, ["reconcile", "--team", "rmscep"])

    assert result.exit_code == 0, result.output
    assert "Asked again" in result.output
    assert ("POST", "/api/latest/fleet/certificates") in fleet.calls


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
