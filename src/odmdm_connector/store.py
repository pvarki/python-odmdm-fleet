"""What the connector keeps between runs: a settings file without secrets, beside its log, and the keychain

The settings file holds the plugins approved and, for each Deploy App connected by its host, the MDM
chosen, the MDM's fields that are not secret, when its last enrolment window closes and the
operator's callsigns for next time, and which Deploy App was worked with last. Never a secret, never
the challenge. A field marked secret, the MDM token, goes only to the operating system's keychain,
through keyring's one back end for the system, fixed here so that neither PYTHON_KEYRING_BACKEND,
keyring's configuration file nor another back end installed can choose where it goes. keyring is
the connector's one dependency and is imported here alone: without it, as in the one-file build,
there is no keychain and the App holds a secret until the connector stops. Never a plaintext fallback.
"""

import datetime
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)

#: The name of the directory the settings file and the log go in, and the keychain service of the secrets
APP = "odmdm-connector"
#: The settings file's format
VERSION = 1


class StoreError(Exception):
    """The settings file cannot be read or written, said in a sentence for the operator"""


def directory() -> Path:
    """Where the settings file and the log live: the user's own application directory, named, not created"""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP


@dataclass
class Connection:
    """One Deploy App, by the host of its SCEP URL, and the MDM set up for it"""

    #: The MDM chosen, by its plugin name
    mdm: str = ""
    #: The chosen MDM's fields that are not secret, by key
    values: dict[str, str] = field(default_factory=dict)
    #: When the last enrolment window pasted closes, as an ISO time, so the banner survives a restart
    closes: str = ""
    #: The callsign prefix last used, and the list of callsigns still to give
    prefix: str = ""
    callsigns: list[str] = field(default_factory=list)


@dataclass
class Saved:
    """The settings file's contents"""

    #: The plugins approved by the operator, each as "name package version"; asked again when it changes
    approved: list[str] = field(default_factory=list)
    #: The Deploy Apps connected, by the host of their SCEP URL
    connections: dict[str, Connection] = field(default_factory=dict)
    #: The host of the Deploy App worked with last, or empty
    active: str = ""


def _typed(key: str, value: Any, default: Any) -> Any:
    """The value, if it is of its default's type: a string, a list of strings or a mapping of strings"""
    if isinstance(default, dict):
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise TypeError(f"{key} is not a mapping of strings")
    elif isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise TypeError(f"{key} is not a list of strings")
    elif not isinstance(value, str):
        raise TypeError(f"{key} is not a string")
    return value


def _connection(host: str, raw: Any) -> Connection:
    if not isinstance(raw, dict):
        raise TypeError(f"connection {host} is not a mapping")
    return Connection(
        **{
            key: _typed(f"{host} {key}", raw.get(key, default), default)
            for key, default in asdict(Connection()).items()
        }
    )


def _checked(raw: Any) -> Saved:
    """The file's values, each of the type it must be; TypeError otherwise"""
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise TypeError("not a version 1 settings file")
    connections = raw.get("connections", {})
    if not isinstance(connections, dict):
        raise TypeError("connections is not a mapping")
    found = Saved(
        approved=_typed("approved", raw.get("approved", []), []),
        connections={host: _connection(host, each) for host, each in connections.items()},
        active=_typed("active", raw.get("active", ""), ""),
    )
    if found.active and found.active not in found.connections:
        raise TypeError("active is not a connection")
    return found


class Store:
    """The settings file in one directory, written whole and readable only by the user"""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "settings.json"

    def load(self) -> Saved:
        """The saved settings; empty ones when there is no file yet"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Saved()
        except OSError as exc:
            raise StoreError(f"The settings file {self.path} cannot be read: {exc.strerror}.") from exc
        try:
            return _checked(json.loads(text))
        except (ValueError, TypeError) as exc:
            LOGGER.warning("Settings file %s refused: %s", self.path, exc)
            raise StoreError(f"The settings file {self.path} is damaged, so the app starts without it.") from exc

    def save(self, saved: Saved) -> None:
        """Write the file whole: a new file beside it, readable only by the user, then renamed over it"""
        text = json.dumps({"version": VERSION, **asdict(saved)}, indent=2)
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-", suffix=".json")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as written:
                    written.write(text)
                    written.flush()
                    os.fsync(written.fileno())
                os.replace(name, self.path)
            except BaseException:
                Path(name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise StoreError(f"The settings file {self.path} cannot be written: {exc.strerror}.") from exc


def closes(connection: Connection) -> datetime.datetime | None:
    """When the Deploy App's last enrolment window pasted closes, if one was"""
    try:
        return datetime.datetime.fromisoformat(connection.closes) if connection.closes else None
    except ValueError:
        return None


class Backend(Protocol):
    """The part of a keyring back end the connector uses"""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


def platform_backend() -> Backend | None:
    """The system's keychain, or None when keyring is not installed or the keychain cannot be used here

    The one back end per system is named here and set as keyring's, so keyring never looks at
    PYTHON_KEYRING_BACKEND, its configuration file or the other back ends installed.
    """
    try:
        import keyring
        from keyring.backend import KeyringBackend

        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring as Chosen
        elif sys.platform == "darwin":
            from keyring.backends.macOS import Keyring as Chosen
        else:
            from keyring.backends.SecretService import Keyring as Chosen
    except ImportError:
        LOGGER.debug("keyring is not installed, so no keychain is used")
        return None
    if not Chosen.viable:
        LOGGER.warning("The keychain back end %s cannot be used here", Chosen.__name__)
        return None
    # keyring leaves the back ends' constructors unannotated
    make: Callable[[], KeyringBackend] = Chosen
    backend = make()
    keyring.set_keyring(backend)
    return backend


class KeychainError(Exception):
    """The keychain did not answer; it says only the kind of what the back end raised, never a value"""


def _asked(backend: Backend, call: Callable[[Backend], str | None]) -> str | None:
    """The back end's answer

    A back end raises kinds of its own besides keyring's, D-Bus's and Windows' among them, so any
    exception means the keychain did not answer, and becomes a KeychainError that names its kind.
    """
    try:
        return call(backend)
    except Exception as exc:
        raise KeychainError(type(exc).__name__) from exc


class Keychain:
    """Secrets by account, under the service APP, in the system's keychain; no back end is no keychain"""

    def __init__(self, backend: Backend | None) -> None:
        self.backend = backend

    @property
    def usable(self) -> bool:
        return self.backend is not None

    def _call(self, call: Callable[[Backend], str | None]) -> tuple[bool, str]:
        """Whether the keychain answered, and its answer; when it did not, that is logged by its kind only"""
        if self.backend is None:
            return False, ""
        try:
            return True, _asked(self.backend, call) or ""
        except KeychainError as exc:
            LOGGER.warning("The keychain did not answer: %s", exc)
            return False, ""

    def get(self, account: str) -> str:
        """The secret kept, empty when there is none or the keychain did not answer"""
        return self._call(lambda backend: backend.get_password(APP, account))[1]

    def keep(self, account: str, secret: str) -> bool:
        """Keep the secret; whether the keychain took it"""
        return self._call(lambda backend: backend.set_password(APP, account, secret))[0]

    def forget(self, account: str) -> bool:
        """Remove the secret; whether the keychain no longer holds it, which is so without a keychain

        Deleting what is not there raises, so the keychain is read first, and again after deleting to tell.
        """
        if self.backend is None:
            return True
        answered, left = self._call(lambda backend: backend.get_password(APP, account))
        if answered and left:
            self._call(lambda backend: backend.delete_password(APP, account))
            answered, left = self._call(lambda backend: backend.get_password(APP, account))
        return answered and not left
