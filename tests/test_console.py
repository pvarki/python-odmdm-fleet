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

from .test_fleet import _Fleet, _mdm


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
