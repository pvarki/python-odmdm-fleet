"""The settings file and the keychain, against a temporary directory and a keychain in memory"""

import datetime
import json
import logging
import os
import stat
import sys
from pathlib import Path

import keyring
import keyring.core
import pytest

from odmdm_connector import store

TOKEN = "s3cret"  # pragma: allowlist secret


class MemoryBackend:
    """A keychain in memory; broken makes every call raise, as a keychain that does not answer does"""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.broken = False
        #: Deleting is refused while reading still answers, as a locked item is
        self.undeletable = False

    def _check(self) -> None:
        if self.broken:
            raise RuntimeError(f"no keychain here for {TOKEN}")

    def get_password(self, service: str, username: str) -> str | None:
        self._check()
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._check()
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._check()
        if self.undeletable or (service, username) not in self.items:
            raise keyring.errors.PasswordDeleteError(username)
        del self.items[(service, username)]


def test_settings_survive_a_restart(tmp_path: Path) -> None:
    unit = store.Connection(
        mdm="fleet",
        values={"mdm_url": "https://mdm.example.fi"},
        closes="2026-11-21T14:00:00+00:00",
        prefix="OTTER",
        callsigns=["BEAR1", "BEAR2"],
    )
    saved = store.Saved(
        approved=["demo odmdm-connector-demo 1.0.0"],
        connections={"unit.example.fi": unit, "other.example.fi": store.Connection(mdm="demo")},
        active="unit.example.fi",
    )

    store.Store(tmp_path / "config").save(saved)

    assert store.Store(tmp_path / "config").load() == saved
    assert store.closes(unit) == datetime.datetime(2026, 11, 21, 14, tzinfo=datetime.UTC)


def test_the_file_holds_no_more_than_said(tmp_path: Path) -> None:
    """Written whole, readable only by the user, nothing left beside it"""
    settings = store.Store(tmp_path)
    settings.save(store.Saved(active=""))
    settings.save(store.Saved(connections={"unit.example.fi": store.Connection()}, active="unit.example.fi"))

    assert [path.name for path in tmp_path.iterdir()] == ["settings.json"]
    assert json.loads(settings.path.read_text(encoding="utf-8"))["active"] == "unit.example.fi"
    if os.name == "posix":
        assert stat.S_IMODE(settings.path.stat().st_mode) == 0o600


def test_no_file_is_empty_settings(tmp_path: Path) -> None:
    assert store.Store(tmp_path).load() == store.Saved()
    assert store.closes(store.Connection()) is None
    assert store.closes(store.Connection(closes="soon")) is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"version": 2}',
        '{"version": 1, "active": 3}',
        '{"version": 1, "approved": "demo"}',
        '{"version": 1, "connections": []}',
        '{"version": 1, "connections": {"unit.example.fi": "fleet"}}',
        '{"version": 1, "connections": {"unit.example.fi": {"mdm": 3}}}',
        '{"version": 1, "connections": {"unit.example.fi": {"values": {"team": 1}}}}',
        '{"version": 1, "connections": {"unit.example.fi": {"callsigns": "BEAR1"}}}',
        '{"version": 1, "connections": {}, "active": "unit.example.fi"}',
    ],
)
def test_a_damaged_file_is_said_in_a_sentence(tmp_path: Path, text: str) -> None:
    settings = store.Store(tmp_path)
    settings.path.write_text(text, encoding="utf-8")

    with pytest.raises(store.StoreError, match=r"is damaged, so the app starts without it\.$"):
        settings.load()


def test_a_file_that_cannot_be_read_or_written_is_said(tmp_path: Path) -> None:
    (tmp_path / "settings.json").mkdir()
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the directory would be", encoding="utf-8")

    with pytest.raises(store.StoreError, match="cannot be read"):
        store.Store(tmp_path).load()
    with pytest.raises(store.StoreError, match="cannot be written"):
        store.Store(blocked).save(store.Saved())
    with pytest.raises(store.StoreError, match="cannot be written"):
        store.Store(tmp_path).save(store.Saved())
    assert [path.name for path in tmp_path.iterdir() if path.name.startswith(".")] == []


@pytest.mark.parametrize(
    ("platform", "environment", "expected"),
    [
        ("win32", {"LOCALAPPDATA": "local"}, ("local", "odmdm-connector")),
        ("win32", {}, ("home", "AppData", "Local", "odmdm-connector")),
        ("darwin", {"XDG_CONFIG_HOME": "xdg"}, ("home", "Library", "Application Support", "odmdm-connector")),
        ("linux", {"XDG_CONFIG_HOME": "xdg"}, ("xdg", "odmdm-connector")),
        ("linux", {}, ("home", ".config", "odmdm-connector")),
    ],
)
def test_the_directory_is_the_users_own(
    monkeypatch: pytest.MonkeyPatch, platform: str, environment: dict[str, str], expected: tuple[str, ...]
) -> None:
    """Named, not created"""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: Path("home"))
    for name in ("LOCALAPPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert store.directory() == Path(*expected)


def test_a_secret_kept_and_forgotten(caplog: pytest.LogCaptureFixture) -> None:
    backend = MemoryBackend()
    keychain = store.Keychain(backend)

    assert keychain.usable
    assert keychain.keep("unit.example.fi:fleet:token", TOKEN)
    assert backend.items == {("odmdm-connector", "unit.example.fi:fleet:token"): TOKEN}
    assert store.Keychain(backend).get("unit.example.fi:fleet:token") == TOKEN
    assert keychain.forget("unit.example.fi:fleet:token")
    assert (backend.items, keychain.get("unit.example.fi:fleet:token")) == ({}, "")
    with caplog.at_level(logging.WARNING):
        assert keychain.forget("unit.example.fi:fleet:token")
    assert not caplog.records, "nothing to forget is not a keychain that did not answer"


def test_a_keychain_that_does_not_answer_is_logged_by_its_kind_only(caplog: pytest.LogCaptureFixture) -> None:
    backend = MemoryBackend()
    backend.items[("odmdm-connector", "unit.example.fi:fleet:token")] = TOKEN
    backend.broken = True
    keychain = store.Keychain(backend)

    with caplog.at_level(logging.WARNING):
        assert not keychain.keep("unit.example.fi:fleet:token", TOKEN)
        assert keychain.get("unit.example.fi:fleet:token") == ""
        assert not keychain.forget("unit.example.fi:fleet:token")

    assert [record.getMessage() for record in caplog.records] == ["The keychain did not answer: RuntimeError"] * 3
    assert all(record.exc_info is None for record in caplog.records)


def test_a_secret_the_keychain_will_not_delete_is_said_to_be_left() -> None:
    backend = MemoryBackend()
    backend.items[("odmdm-connector", "unit.example.fi:fleet:token")] = TOKEN
    backend.undeletable = True

    assert not store.Keychain(backend).forget("unit.example.fi:fleet:token")


def test_no_back_end_is_no_keychain() -> None:
    missing = store.Keychain(None)

    assert not missing.usable
    assert not missing.keep("unit.example.fi:fleet:token", TOKEN)
    assert missing.get("unit.example.fi:fleet:token") == ""
    assert missing.forget("unit.example.fi:fleet:token"), "nothing is kept where there is no keychain"


def _chosen() -> type[keyring.backend.KeyringBackend]:
    """keyring's back end for this system, as store names it"""
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring as MacOS

        return MacOS
    from keyring.backends.SecretService import Keyring as SecretService

    return SecretService


def test_the_systems_keychain_is_fixed_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the environment nor keyring's configuration chooses it; nothing is read or written"""
    monkeypatch.setattr(keyring.core, "_keyring_backend", None)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    backend = store.platform_backend()

    if backend is None:
        pytest.skip(f"{_chosen().__name__} cannot be used on this machine")
    assert type(backend) is _chosen()
    assert keyring.get_keyring() is backend


def test_a_keychain_that_cannot_be_used_here_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring.core, "_keyring_backend", None)
    monkeypatch.setattr(_chosen(), "viable", False)

    assert store.platform_backend() is None
    assert keyring.core._keyring_backend is None


def test_without_keyring_there_is_no_keychain(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """As in the one-file build, which leaves keyring out; said only to the debug log, as nothing is wrong"""
    for name in [name for name in sys.modules if name == "keyring" or name.startswith("keyring.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "keyring", None)
    caplog.set_level(logging.DEBUG)

    assert store.platform_backend() is None
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.DEBUG, "keyring is not installed, so no keychain is used")
    ]
