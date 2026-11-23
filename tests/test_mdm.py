"""The MDM contract"""

import pytest

from odmdm_connector import mdm


def test_an_error_carries_the_phones_named_before_it() -> None:
    """So the operator names only the rest"""
    named = {"31": "OTTER1"}

    error = mdm.MdmError("stopped", named)
    named["32"] = "OTTER2"

    assert str(error) == "stopped"
    assert error.named == {"31": "OTTER1"}
    assert not mdm.MdmError("stopped").named


def test_every_state_is_a_sentence_and_every_capability_is_named() -> None:
    """The state's value is what the operator reads"""
    assert all(state.value and state.value == state.value.strip() for state in mdm.State)
    assert set(mdm.CAPABILITIES) == {"setup", "set_challenge", "ask_again", "send_policy"}
    assert mdm.API_VERSION == 1


def test_a_field_is_shown_unless_marked_advanced() -> None:
    """The window hides a field marked advanced until asked; the command line takes it as any other"""
    assert not mdm.Field("team", "Team").advanced
    assert mdm.Field("key_alias", "Key alias", advanced=True).advanced


def test_a_default_may_name_the_deployment() -> None:
    """The first label of the Deploy App's host; without a host such a default is none, and any other is itself"""
    team = mdm.Field("team", "Team", default="{deployment} DeployApp")

    assert team.default_for("golden-monkey.example.fi") == "golden-monkey DeployApp"
    assert team.default_for("") == ""
    assert mdm.Field("key_alias", "Key alias", default="rmscep").default_for("") == "rmscep"
    assert mdm.Field("plain", "Plain", default="{x} {deployment}").default_for("a.example.fi") == "{x} a"


@pytest.mark.parametrize(
    ("host", "default"),
    [
        ("Golden-Monkey.Example.fi", "golden-monkey DeployApp"),
        (" golden-monkey.example.fi\n", "golden-monkey DeployApp"),
        ("https://golden-monkey.example.fi", ""),
        ("golden-monkey.example.fi/scep", ""),
        ("golden monkey.example.fi", ""),
        ("localhost", ""),
    ],
)
def test_a_deployment_default_takes_only_a_plain_host_in_lower_case(host: str, default: str) -> None:
    """The page and the command line give one team for one Deploy App; a host the settings would refuse gives none"""
    assert mdm.Field("team", "Team", default="{deployment} DeployApp").default_for(host) == default
