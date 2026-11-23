"""The MDM plugins installed, found through the entry point group odmdm_connector.mdm

Fleet is registered there by this package, exactly as a plugin from another package is. Listing
imports nothing; loading imports the one entry point chosen and no other, because a plugin is code
that sees the MDM token.
"""

import re
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points

from .mdm import API_VERSION, Mdm

GROUP = "odmdm_connector.mdm"
#: The distribution whose plugins are built in
CONNECTOR = "odmdm-connector"
#: What a plugin class declares besides its API_VERSION, read before any instance is made
DECLARED = ("title", "fields", "missing")


class PluginError(Exception):
    """An installed plugin the connector cannot use, said in a sentence for the operator"""


class NotInstalled(PluginError):
    """No plugin of that name is installed"""


@dataclass(frozen=True)
class Plugin:
    """One installed MDM plugin, as its package declares it"""

    name: str
    #: The distribution that declares it, and its version
    package: str
    version: str
    entry: EntryPoint = field(repr=False, compare=False)

    @property
    def built_in(self) -> bool:
        """Whether it comes with the connector itself: declared by it, and code of its own package"""
        module = self.entry.module
        return re.sub(r"[-_.]+", "-", self.package).lower() == CONNECTOR and (
            module == __package__ or module.startswith(f"{__package__}.")
        )


def _declared() -> list[Plugin]:
    """Every entry point of the group, without importing any; tests replace this"""
    return [
        Plugin(entry.name, entry.dist.name if entry.dist else "", entry.dist.version if entry.dist else "", entry)
        for entry in entry_points(group=GROUP)
    ]


def installed() -> list[Plugin]:
    """The installed plugins, the built-in ones first; PluginError when two packages claim one name"""
    found: dict[str, Plugin] = {}
    for plugin in _declared():
        other = found.get(plugin.name)
        if other:
            raise PluginError(
                f"Two packages declare the MDM {plugin.name}: {other.package} {other.version} and "
                f"{plugin.package} {plugin.version}. Uninstall one"
            )
        found[plugin.name] = plugin
    return sorted(found.values(), key=lambda plugin: (not plugin.built_in, plugin.name))


def find(name: str) -> Plugin:
    """The installed plugin of that name, not imported; NotInstalled if there is none"""
    for plugin in installed():
        if plugin.name == name:
            return plugin
    raise NotInstalled(f"No MDM named {name} is installed")


def load(name: str) -> type[Mdm]:
    """The class of the MDM of that name, importing that one entry point and no other

    PluginError when it cannot be imported, speaks another version of the contract, or does not
    declare its title, fields and missing.
    """
    plugin = find(name)
    try:
        loaded = plugin.entry.load()
    except (ImportError, AttributeError) as exc:
        raise PluginError(f"The MDM {name} from {plugin.package} {plugin.version} cannot be loaded: {exc}") from exc
    version = getattr(loaded, "API_VERSION", None)
    if version != API_VERSION:
        raise PluginError(
            f"The MDM {name} from {plugin.package} {plugin.version} needs a connector that speaks MDM API "
            f"version {version}; this one speaks version {API_VERSION}"
        )
    lacking = [attribute for attribute in DECLARED if not hasattr(loaded, attribute)]
    if lacking:
        raise PluginError(
            f"The MDM {name} from {plugin.package} {plugin.version} does not declare {', '.join(lacking)}, "
            "so it cannot be used"
        )
    chosen: type[Mdm] = loaded
    return chosen
