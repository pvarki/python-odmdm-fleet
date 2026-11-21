"""The MDM contract"""

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
    assert set(mdm.CAPABILITIES) == {"setup", "set_challenge", "ask_again"}
    assert mdm.API_VERSION == 1
