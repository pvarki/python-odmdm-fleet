"""The MDM contract"""

import pytest

from odmdm_connector import mdm
from odmdm_connector.settings import Settings
from odmdm_connector.testing import FakeMdm


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
    assert set(mdm.CAPABILITIES) == {"setup", "set_challenge", "ask_again", "send_policy", "end"}
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


class _Older(FakeMdm):
    """A plugin made before END was in the contract: no end, and END not in missing"""

    end = None  # type: ignore[assignment]


@pytest.mark.parametrize(
    ("chosen", "says"),
    [
        (FakeMdm, ""),
        (FakeMdm.lacking(mdm.END), "The fake MDM cannot end"),
        (_Older, "This Fake MDM plugin cannot end a Deploy App in Fake MDM: it was made before the connector could."),
    ],
)
def test_why_an_mdm_cannot_end(chosen: type[FakeMdm], says: str) -> None:
    """Its own sentence when it lists END, the connector's when it is older than END; the class and an MDM alike"""
    assert mdm.cannot_end(chosen) == says
    assert mdm.cannot_end(chosen({})) == says


def test_the_fake_ends_once_and_then_says_nothing_was_left() -> None:
    """Its phones keep their names and lose the certificate; a second call finds nothing to take out"""
    fake = FakeMdm({"group": "golden-monkey DeployApp"})
    window = Settings("https://golden-monkey.example.fi/scep", "")

    first = fake.end(window)
    again = fake.end(window)

    assert first[0].startswith(
        "Group golden-monkey DeployApp no longer enrols at https://golden-monkey.example.fi/scep"
    )
    assert again[0].startswith("Group golden-monkey DeployApp has nothing of")
    assert [phone.callsign for phone in fake.phones()] == ["", "", "OTTER1"]
    assert {phone.state for phone in fake.phones()} == {mdm.State.NO_REQUEST}
    assert fake.calls.count("end") == 2
