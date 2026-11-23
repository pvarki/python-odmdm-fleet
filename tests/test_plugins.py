"""Finding MDM plugins through their entry points"""

from importlib.metadata import EntryPoint

import pytest

from odmdm_connector import plugins
from odmdm_connector.fleet_plugin import FleetPlugin

#: A module that does not exist, so importing it fails the test that does
NEVER = "odmdm_connector_never_imported:Mdm"


class Old:
    """A plugin written for another version of the contract"""

    API_VERSION = 2


class Bare:
    """A plugin that speaks the contract's version and declares nothing else"""

    API_VERSION = 1


def _plugin(name: str, value: str, package: str = "odmdm-connector-demo", version: str = "1.0.0") -> plugins.Plugin:
    return plugins.Plugin(name, package, version, EntryPoint(name, value, plugins.GROUP))


@pytest.fixture
def declared(monkeypatch: pytest.MonkeyPatch) -> list[plugins.Plugin]:
    """The entry points the plugins module sees, whatever is installed"""
    found: list[plugins.Plugin] = []
    monkeypatch.setattr(plugins, "_declared", lambda: found)
    return found


def test_fleet_is_registered_as_a_plugin_would_be() -> None:
    """Through the package's own entry point, read from the installed metadata"""
    fleet = plugins.find("fleet")

    assert (fleet.package, fleet.built_in) == ("odmdm-connector", True)
    assert plugins.load("fleet") is FleetPlugin


def test_listing_imports_no_plugin(declared: list[plugins.Plugin]) -> None:
    """Built-in first, then by name"""
    declared += [
        _plugin("zeta", NEVER),
        _plugin("demo", NEVER),
        _plugin("fleet", "odmdm_connector.never_imported:Mdm", "odmdm_connector", "1.1.0"),
    ]

    found = plugins.installed()

    assert [(p.name, p.built_in) for p in found] == [("fleet", True), ("demo", False), ("zeta", False)]


def test_loading_imports_only_the_one_chosen(declared: list[plugins.Plugin]) -> None:
    declared += [_plugin("demo", NEVER), _plugin("fleet", "odmdm_connector.fleet_plugin:FleetPlugin")]

    assert plugins.load("fleet") is FleetPlugin
    with pytest.raises(plugins.PluginError, match="The MDM demo from odmdm-connector-demo 1.0.0 cannot be loaded"):
        plugins.load("demo")


def test_two_packages_claiming_one_name_are_both_named(declared: list[plugins.Plugin]) -> None:
    declared += [_plugin("demo", NEVER), _plugin("demo", NEVER, "odmdm-connector-other", "2.0.0")]

    for call in (plugins.installed, lambda: plugins.load("demo")):
        with pytest.raises(plugins.PluginError) as raised:
            call()
        assert "odmdm-connector-demo 1.0.0 and odmdm-connector-other 2.0.0" in str(raised.value)


def test_another_contract_version_is_refused(declared: list[plugins.Plugin]) -> None:
    declared.append(_plugin("old", "tests.test_plugins:Old"))

    with pytest.raises(
        plugins.PluginError, match="needs a connector that speaks MDM API version 2; this one speaks version 1"
    ):
        plugins.load("old")


def test_an_unknown_name_is_not_installed(declared: list[plugins.Plugin]) -> None:
    with pytest.raises(plugins.NotInstalled, match="No MDM named nope is installed"):
        plugins.load("nope")


def test_a_class_declaring_too_little_is_refused(declared: list[plugins.Plugin]) -> None:
    """Said before anything reads its title, fields or missing"""
    declared.append(_plugin("bare", "tests.test_plugins:Bare"))

    with pytest.raises(plugins.PluginError) as raised:
        plugins.load("bare")

    assert str(raised.value) == (
        "The MDM bare from odmdm-connector-demo 1.0.0 does not declare title, fields, missing, so it cannot be used"
    )


def test_built_in_means_the_connectors_own_code_too() -> None:
    """The connector's distribution name on an entry point outside its package is no proof"""
    assert not _plugin("odd", NEVER, "odmdm-connector", "1.1.0").built_in
    assert not _plugin("odd", "odmdm_connector_x.mdm:Mdm", "odmdm-connector", "1.1.0").built_in
    assert _plugin("fleet", "odmdm_connector:Mdm", "odmdm-connector", "1.1.0").built_in
