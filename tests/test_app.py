"""The page's core without a page: the FakeMdm, a temporary settings directory and the time given"""

import datetime
import io
import logging
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import ClassVar

import pytest

from odmdm_connector import app, plugins, store
from odmdm_connector.fleet_plugin import FleetPlugin
from odmdm_connector.mdm import ASK_AGAIN, SEND_POLICY, SET_CHALLENGE, SETUP, Field, Mdm, MdmError, Phone, State, Step
from odmdm_connector.settings import Settings
from odmdm_connector.testing import FakeMdm
from odmdm_connector.testing.fake import PHONES

from .test_store import MemoryBackend

CHALLENGE = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
LINE = f"ODMDM1 https://unit.example.fi/scep {CHALLENGE} 2026-11-21T14:00:00Z"
OTHER = LINE.replace("unit.example.fi", "other.example.fi").replace("14:00:00Z", "15:30:00Z")
NOW = datetime.datetime(2026, 11, 21, 12, 0, tzinfo=datetime.UTC)
VALUES = {"group": "Deploy App", "password": "pa55word"}  # pragma: allowlist secret
LACKING = FakeMdm.lacking(SETUP, SET_CHALLENGE, ASK_AGAIN)


class Rig:
    """An App and everything handed to it"""

    def __init__(self, directory: Path, backend: MemoryBackend | None = None) -> None:
        self.directory = directory
        #: The keychain, kept across starts; none by default, as in the one-file build
        self.backend = backend
        self.loaded: list[str] = []
        self.classes: dict[str, type[Mdm]] = {"fake": FakeMdm, "demo": LACKING}
        self.versions = {"demo": "1.0.0"}
        #: What listing the plugins raises, if anything
        self.unlisted: plugins.PluginError | None = None
        #: What plugins.load says when it cannot load the plugin
        self.refused = ""
        self.app = self.start()

    def installed(self) -> list[plugins.Plugin]:
        if self.unlisted:
            raise self.unlisted
        return [
            plugins.Plugin("fake", "odmdm-connector", "1.2.0", EntryPoint("fake", "odmdm_connector.testing:Fake", "g")),
            plugins.Plugin("demo", "odmdm-connector-demo", self.versions["demo"], EntryPoint("demo", "demo:Demo", "g")),
        ]

    def load(self, name: str) -> type[Mdm]:
        self.loaded.append(name)
        if self.refused:
            raise plugins.PluginError(self.refused)
        return self.classes[name]

    def start(self) -> app.App:
        """The app as started afresh on the same computer"""
        return app.App(
            store.Store(self.directory),
            installed=self.installed,
            load=self.load,
            log_path=self.directory / "the.log",
            keychain=store.Keychain(self.backend),
        )

    def settle(self) -> list[app.Result]:
        self.app.worker.wait()
        return self.app.drain()

    def connected(self, name: str = "fake", values: Mapping[str, str] = VALUES, remember: bool = True) -> FakeMdm:
        """Pasted, connected and the phones read"""
        self.app.paste_settings(LINE, NOW)
        if self.app.approval(name):
            self.app.approve(name)
        self.app.connect(name, values, remember)
        self.app.refresh()
        self.settle()
        assert isinstance(self.app.mdm, FakeMdm)
        return self.app.mdm


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = Rig(tmp_path / "config")
    yield made
    made.app.close()


@pytest.fixture
def keyed(tmp_path: Path) -> Iterator[Rig]:
    """A rig whose computer has a keychain"""
    made = Rig(tmp_path / "config", MemoryBackend())
    yield made
    made.app.close()


#: Where the fake MDM's password for unit.example.fi is kept
KEPT = ("odmdm-connector", "unit.example.fi:fake:password")


def test_first_setup_holds_the_secret_in_memory_and_the_rest_in_the_file(rig: Rig) -> None:
    with pytest.raises(
        app.AppError, match="^Add the Deploy App first: on its MDM page, press Open in the MDM connector.$"
    ):
        rig.app.connect("fake", VALUES)

    pasted = rig.app.paste_settings(f"  {LINE}\n", NOW)
    form = rig.app.form("fake")
    said = rig.app.connect("fake", VALUES)
    [result] = rig.settle()

    assert pasted == "Deploy App unit.example.fi accepts phones until 14:00."
    assert (form.title, [(value.field.key, value.value) for value in form.fields]) == (
        "Fake MDM",
        [("group", "Deploy App"), ("password", "")],
    )
    assert said == "Setting Fake MDM up for Deploy App unit.example.fi."
    assert (result.job, result.error) == (app.Job.SETUP, "")
    assert [step.label for step in result.steps] == [
        "Group Deploy App enrols at https://unit.example.fi/scep",
        "Certificate template stated",
    ]
    assert rig.app.status == "Fake MDM is set up for Deploy App unit.example.fi."
    assert rig.app.form("fake").fields[1].value == "pa55word"
    text = (rig.directory / "settings.json").read_text(encoding="utf-8")
    assert CHALLENGE not in text and "pa55word" not in text
    assert rig.app.saved == store.Saved(
        connections={
            "unit.example.fi": store.Connection(
                mdm="fake", values={"group": "Deploy App"}, closes="2026-11-21T14:00:00+00:00"
            )
        },
        active="unit.example.fi",
    )


@pytest.mark.parametrize(
    ("values", "says"),
    [
        ({"mdm_url": "", "token": "t0ken"}, "Fill in Fleet address."),  # pragma: allowlist secret
        ({"mdm_url": "https://mdm.example.fi", "token": " "}, "Fill in API token."),
    ],
)
def test_a_required_field_left_empty_is_said_and_nothing_kept(rig: Rig, values: dict[str, str], says: str) -> None:
    rig.classes["fake"] = FleetPlugin
    rig.app.paste_settings(LINE, NOW)
    before = rig.app.saved

    with pytest.raises(app.AppError, match=f"^{says}$"):
        rig.app.connect("fake", values)

    assert rig.app.form("fake").fields[1].value == ""
    assert rig.app.saved == before
    assert store.Store(rig.directory).load() == before
    assert rig.app.mdm is None


class Addressed(FakeMdm):
    """An MDM with an address, which records every one made, as each would send its token there"""

    fields: ClassVar[tuple[Field, ...]] = (
        Field("mdm_url", "Address", required=True),
        Field("token", "Token", secret=True, required=True),
        Field("team", "Team", default="Deploy App"),
    )
    made: ClassVar[list[dict[str, str]]] = []

    def __init__(self, values: Mapping[str, str]) -> None:
        super().__init__(values)
        Addressed.made.append(dict(values))


TOKEN_VALUES = {"mdm_url": "https://mdm.example.fi", "token": "t0ken", "team": "Deploy App"}  # pragma: allowlist secret


@pytest.mark.parametrize(
    "changed",
    [
        {"mdm_url": "https://attacker.example.fi"},
        {"team": "Other"},
        {"mdm_url": "https://mdm.example.fi", "team": ""},
    ],
)
def test_a_kept_secret_goes_only_with_the_settings_it_was_kept_for(keyed: Rig, changed: dict[str, str]) -> None:
    keyed.classes["fake"] = Addressed
    keyed.app.paste_settings(LINE, NOW)
    keyed.app.connect("fake", TOKEN_VALUES)
    keyed.settle()
    Addressed.made.clear()
    before, backend = keyed.app.saved, keyed.backend
    assert backend is not None
    kept = dict(backend.items)

    with pytest.raises(app.AppError, match="^Type the Token again: the settings changed.$"):
        keyed.app.connect("fake", {**TOKEN_VALUES, "token": "", **changed})

    assert Addressed.made == [], "no MDM made, so nothing sent"
    assert (keyed.app.saved, store.Store(keyed.directory).load(), backend.items) == (before, before, kept)
    assert not keyed.app.busy()
    assert keyed.app.connect("fake", {**TOKEN_VALUES, "token": ""}) == (
        "Setting Fake MDM up for Deploy App unit.example.fi."
    )
    assert Addressed.made == [TOKEN_VALUES], "the same settings take the kept token"
    keyed.settle()


def test_a_secret_is_held_only_once_its_settings_are_written(rig: Rig) -> None:
    """Held before, a new token would stay under the old saved address and go there on the next connect"""
    rig.classes["fake"] = Addressed
    rig.app.paste_settings(LINE, NOW)
    rig.app.connect("fake", TOKEN_VALUES)
    rig.settle()
    Addressed.made.clear()
    rig.app.store = store.Store(rig.directory / "settings.json" / "not-a-directory")

    with pytest.raises(app.AppError, match="cannot be written"):
        rig.app.connect("fake", {**TOKEN_VALUES, "mdm_url": "https://new.example.fi", "token": "NEWTOKEN"})
    rig.app.store = store.Store(rig.directory)
    rig.app.connect("fake", {**TOKEN_VALUES, "token": ""})

    assert Addressed.made == [TOKEN_VALUES], "the old address gets only the token typed for it"
    rig.settle()


def test_a_kept_secret_does_not_go_to_another_mdm(rig: Rig) -> None:
    rig.connected()
    rig.app.approve("demo")

    rig.app.connect("demo", {"group": "Deploy App", "password": ""})

    assert isinstance(rig.app.mdm, FakeMdm)
    assert rig.app.mdm.values == {"group": "Deploy App", "password": ""}
    rig.settle()


@pytest.mark.parametrize("token", ["TOPSECRETA\nTOPSECRETB", "TOPSECRET\r", "TOP\x00SECRET", "TOP\x7fSECRET"])
def test_a_secret_with_a_control_character_is_refused_before_it_is_held(
    rig: Rig, caplog: pytest.LogCaptureFixture, token: str
) -> None:
    caplog.set_level(logging.DEBUG)
    rig.classes["fake"] = Addressed
    rig.app.paste_settings(LINE, NOW)
    before = rig.app.saved

    with pytest.raises(app.AppError) as raised:
        rig.app.connect("fake", {**TOKEN_VALUES, "token": token})

    assert str(raised.value) == (
        "The Token holds a line break or another character that cannot be part of it: copy it again."
    )
    assert (rig.app.saved, rig.app.mdm, rig.app.form("fake").fields[1].value) == (before, None, "")
    assert "TOPSECRET" not in caplog.text


def test_a_restart_holds_no_secret_and_connects_without_a_window(rig: Rig) -> None:
    """The token is typed again on another day, and the MDM already set up is only connected"""
    rig.connected()

    again = rig.start()
    held = again.form("fake").fields[1].value
    said = again.connect("fake", VALUES)

    assert (held, again.window) == ("", None)
    assert said == "Connected to Fake MDM for Deploy App unit.example.fi."
    assert isinstance(again.mdm, FakeMdm)
    assert (again.mdm.values, again.mdm.calls, again.busy()) == (VALUES, [], False)
    assert again.banner(NOW).text == "Deploy App unit.example.fi accepts phones until 14:00"
    again.approve("demo")
    with pytest.raises(
        app.AppError,
        match="^To set Fake MDM up, open enrolment on the MDM page of Deploy App unit.example.fi, "
        "then press Open in the MDM connector there.$",
    ):
        again.connect("demo", VALUES)
    again.close()


def test_a_damaged_settings_file_is_said_and_the_app_starts_without_it(rig: Rig) -> None:
    rig.directory.mkdir()
    (rig.directory / "settings.json").write_text("{", encoding="utf-8")

    started = rig.start()

    assert started.saved == store.Saved()
    assert started.status.endswith("is damaged, so the app starts without it.")
    started.close()


@pytest.mark.parametrize(
    ("line", "says"),
    [
        ("0123", "This is not a settings line: it has four parts and starts with ODMDM1."),
        (LINE.replace("https", "http"), "The SCEP URL in the settings line must be https://<host>/<path>"),
        (LINE.replace("14:00:00Z", "11:00:00Z"), "The enrolment window closed at 11:00"),
    ],
)
def test_a_line_that_is_not_one_is_refused_in_a_sentence(rig: Rig, line: str, says: str) -> None:
    with pytest.raises(app.AppError) as raised:
        rig.app.paste_settings(line, NOW)

    assert str(raised.value).startswith(says)
    assert (rig.app.window, rig.app.saved) == (None, store.Saved())


def test_a_line_for_another_deploy_app_adds_it_and_works_with_it(rig: Rig) -> None:
    rig.connected()
    rig.app.preview(["a1"], ["BEAR1"], NOW)

    said = rig.app.paste_settings(OTHER, NOW)

    assert said == "Deploy App other.example.fi accepts phones until 15:30."
    assert rig.app.saved.active == "other.example.fi"
    assert list(rig.app.saved.connections) == ["unit.example.fi", "other.example.fi"]
    assert rig.app.saved.connections["unit.example.fi"].values == {"group": "Deploy App"}
    assert (rig.app.conn, rig.app.mdm, rig.app.phones) == (
        store.Connection(closes="2026-11-21T15:30:00+00:00"),
        None,
        [],
    )
    assert rig.app.window is not None and rig.app.window.scep_url == "https://other.example.fi/scep"
    with pytest.raises(app.AppError, match="Connect to the MDM first."):
        rig.app.name({"a1": "BEAR1"})


def test_two_deploy_apps_side_by_side_each_with_its_own_mdm_and_token(rig: Rig) -> None:
    unit = rig.connected()
    rig.app.paste_settings(OTHER, NOW)
    rig.app.connect("fake", {"group": "Other unit", "password": "0ther"})  # pragma: allowlist secret
    rig.settle()

    back = rig.app.open("unit.example.fi", NOW)
    unit_again = rig.app.mdm
    other = rig.app.open("other.example.fi", NOW)

    assert back == "Deploy App unit.example.fi accepts phones until 14:00."
    assert isinstance(unit_again, FakeMdm) and unit_again is not unit
    assert unit_again.values == VALUES
    assert other == "Deploy App other.example.fi accepts phones until 15:30."
    assert isinstance(rig.app.mdm, FakeMdm)
    assert rig.app.mdm.values == {"group": "Other unit", "password": "0ther"}  # pragma: allowlist secret
    assert rig.app.form("fake").fields[0].value == "Other unit"


def test_the_deploy_apps_as_listed(rig: Rig) -> None:
    rig.connected()
    rig.app.paste_settings(OTHER, NOW)

    listed = rig.app.connections(NOW.replace(hour=13, minute=55))

    assert listed == [
        app.DeployApp(
            "unit.example.fi",
            app.Banner(
                app.Level.CLOSING,
                "Deploy App unit.example.fi accepts phones until 14:00, 5 minutes from now",
                "enrolment open until 14:00, 5 minutes left",
            ),
            False,
        ),
        app.DeployApp(
            "other.example.fi",
            app.Banner(
                app.Level.OPEN, "Deploy App other.example.fi accepts phones until 15:30", "enrolment open until 15:30"
            ),
            True,
        ),
    ]


def test_reading_a_line_takes_nothing_from_it(rig: Rig) -> None:
    rig.connected()
    before = (rig.app.saved, rig.app.window, rig.app.status)

    known = rig.app.read_settings(LINE, NOW)
    new = rig.app.read_settings(OTHER, NOW)

    assert known == app.Reading("unit.example.fi", "Deploy App unit.example.fi accepts phones until 14:00.", True)
    assert new == app.Reading("other.example.fi", "Deploy App other.example.fi accepts phones until 15:30.", False)
    assert (rig.app.saved, rig.app.window, rig.app.status) == before
    with pytest.raises(app.AppError, match="This is not a settings line"):
        rig.app.read_settings("ODMDM1", NOW)


def test_opening_a_deploy_app_without_its_token_says_so(rig: Rig) -> None:
    rig.connected()
    rig.app.paste_settings(OTHER, NOW)
    rig.app.close()
    rig.app = rig.start()

    said = rig.app.open("unit.example.fi", NOW)

    assert said == (
        "Deploy App unit.example.fi accepts phones until 14:00. "
        "Type the Password again to connect: it is kept only while the connector runs."
    )
    assert rig.app.mdm is None
    assert rig.app.open("unit.example.fi", NOW) == said, "said again while it is not connected"
    with pytest.raises(app.AppError, match="^There is no Deploy App nope.example.fi in this app.$"):
        rig.app.open("nope.example.fi", NOW)


def test_removing_a_deploy_app_lets_go_of_its_token(rig: Rig) -> None:
    rig.connected()
    rig.app.paste_settings(OTHER, NOW)
    rig.app.connect("fake", VALUES)
    rig.settle()

    other = rig.app.remove("unit.example.fi")
    kept = (rig.app.saved.active, rig.app.connected)
    active = rig.app.remove("other.example.fi")

    assert other == "Deploy App unit.example.fi is removed from this app, and its MDM token from this computer."
    assert kept == ("other.example.fi", True)
    assert active == "Deploy App other.example.fi is removed from this app, and its MDM token from this computer."
    assert (rig.app.saved, rig.app.mdm, rig.app.window) == (store.Saved(), None, None)
    assert rig.app.banner(NOW).level is app.Level.NONE
    with pytest.raises(app.AppError, match="^There is no Deploy App unit.example.fi in this app.$"):
        rig.app.remove("unit.example.fi")
    rig.app.paste_settings(LINE, NOW)
    assert rig.app.form("fake").fields[1].value == "", "added again, its token is typed again"


def test_a_deploy_app_whose_plugin_is_gone_is_removed(rig: Rig) -> None:
    rig.app.paste_settings(LINE, NOW)
    rig.app.approve("demo")
    rig.app.connect("demo", VALUES)
    rig.settle()
    rig.app.paste_settings(OTHER, NOW)
    rig.refused = "The MDM demo cannot be loaded"

    said = rig.app.remove("unit.example.fi")

    assert said == "Deploy App unit.example.fi is removed from this app, and its MDM token from this computer."
    assert list(rig.app.saved.connections) == ["other.example.fi"]
    assert rig.app.remove("other.example.fi") == "Deploy App other.example.fi is removed from this app.", (
        "no MDM, no token"
    )


def test_a_result_for_a_deploy_app_no_longer_active_changes_nothing(rig: Rig) -> None:
    rig.app.paste_settings(LINE, NOW)
    rig.app.keep_callsigns("", ["BEAR1"])
    rig.connected()
    rig.app.name(rig.app.preview(["a1"], ["BEAR1"], NOW).names)
    rig.app.refresh()
    rig.app.paste_settings(OTHER, NOW)

    rig.settle()

    assert (rig.app.phones, rig.app.status) == ([], "1 phone named.")
    assert rig.app.saved.connections["unit.example.fi"].callsigns == ["BEAR1"]


def test_callsigns_are_kept_only_for_a_deploy_app(rig: Rig) -> None:
    with pytest.raises(
        app.AppError, match="^Add the Deploy App first: on its MDM page, press Open in the MDM connector.$"
    ):
        rig.app.keep_callsigns("BEAR", ["BEAR1"])


def test_a_new_window_gives_the_mdm_its_challenge(rig: Rig) -> None:
    fake = rig.connected()

    said = rig.app.paste_settings(LINE, NOW + datetime.timedelta(minutes=55))
    [result] = rig.settle()

    assert said == "Deploy App unit.example.fi accepts phones until 14:00. Giving the MDM the new challenge."
    assert (result.job, fake.challenge, rig.app.status) == (
        app.Job.CHALLENGE,
        CHALLENGE,
        "Fake MDM has the new challenge.",
    )
    assert (rig.app.challenge_note(), rig.app.challenge()) == ("", "")


def test_a_plugin_is_approved_before_it_is_imported(rig: Rig) -> None:
    """Asked again when its package's version changes; only the chosen one is ever imported"""
    asked = rig.app.approval("demo")
    with pytest.raises(app.AppError, match="The MDM demo is a plugin that has not been approved in this app."):
        rig.app.form("demo")
    rig.app.approve("demo")
    rig.app.form("demo")
    rig.versions["demo"] = "1.0.1"

    assert asked == (
        "The MDM demo comes from the package odmdm-connector-demo 1.0.0, not with this app. "
        "A plugin is code that sees the MDM token: use it only if you trust where it comes from."
    )
    assert rig.app.approval("fake") == ""
    assert rig.app.approval("demo").startswith("The MDM demo comes from the package odmdm-connector-demo 1.0.1")
    assert rig.loaded == ["demo"]
    assert rig.start().saved.approved == ["demo odmdm-connector-demo 1.0.0"]
    rig.app.approve("demo")
    assert rig.app.saved.approved == ["demo odmdm-connector-demo 1.0.1"]
    with pytest.raises(app.AppError, match="No MDM named nope is installed."):
        rig.app.approval("nope")


def test_an_mdm_set_up_but_given_the_challenge_by_hand_says_so(rig: Rig) -> None:
    rig.classes["demo"] = FakeMdm.lacking(SET_CHALLENGE)
    rig.app.paste_settings(LINE, NOW)
    rig.app.approve("demo")
    rig.app.connect("demo", VALUES)
    rig.settle()

    assert rig.app.status == "Do the steps by hand to finish setting Fake MDM up for Deploy App unit.example.fi."


def test_an_mdm_lacking_everything_says_so_and_shows_the_challenge_only_to_be_copied(rig: Rig) -> None:
    rig.app.paste_settings(LINE, NOW)
    rig.app.approve("demo")
    rig.app.connect("demo", VALUES)
    [result] = rig.settle()
    status = rig.app.status
    said = rig.app.paste_settings(LINE, NOW)

    assert result.steps == (Step("The fake MDM cannot setup", False),)
    assert status == "Do the steps by hand to finish setting Fake MDM up for Deploy App unit.example.fi."
    assert said == "Deploy App unit.example.fi accepts phones until 14:00."
    assert rig.app.challenge_note() == "The fake MDM cannot set_challenge"
    assert rig.app.challenge() == CHALLENGE
    assert not rig.app.busy()
    with pytest.raises(app.AppError, match="The fake MDM cannot ask_again"):
        rig.app.ask_again([])


@pytest.mark.parametrize(
    ("now", "level", "text", "short"),
    [
        (NOW, app.Level.OPEN, "Deploy App unit.example.fi accepts phones until 14:00", "enrolment open until 14:00"),
        (
            NOW.astimezone(datetime.timezone(datetime.timedelta(hours=2))),
            app.Level.OPEN,
            "Deploy App unit.example.fi accepts phones until 16:00",
            "enrolment open until 16:00",
        ),
        (
            NOW.replace(hour=13, minute=50),
            app.Level.CLOSING,
            "Deploy App unit.example.fi accepts phones until 14:00, 10 minutes from now",
            "enrolment open until 14:00, 10 minutes left",
        ),
        (
            NOW.replace(hour=13, minute=59, second=30),
            app.Level.CLOSING,
            "Deploy App unit.example.fi accepts phones until 14:00, 1 minute from now",
            "enrolment open until 14:00, 1 minute left",
        ),
        (
            NOW.replace(hour=14),
            app.Level.CLOSED,
            (
                "Enrolment closed at 14:00. Open enrolment again on the MDM page of Deploy App, "
                "then press Open in the MDM connector there."
            ),
            "enrolment closed at 14:00",
        ),
    ],
)
def test_the_banner_follows_the_window(
    rig: Rig, now: datetime.datetime, level: app.Level, text: str, short: str
) -> None:
    before = rig.app.banner(NOW)
    rig.app.paste_settings(LINE, NOW)

    assert before == app.Banner(
        app.Level.NONE,
        "On the MDM page of Deploy App, press Open in the MDM connector to let phones enrol.",
        "no enrolment window known",
    )
    assert rig.app.banner(now) == app.Banner(level, text, short)


def test_the_phones_are_counted_by_where_they_are(rig: Rig) -> None:
    assert rig.app.summary() == ""
    rig.app.paste_settings(LINE, NOW)
    rig.app.connect("fake", VALUES)
    rig.app.refresh()
    assert rig.app.summary() == "", "nothing is said of the phones before they are listed"
    rig.settle()
    fake = rig.app.mdm
    assert isinstance(fake, FakeMdm)
    assert rig.app.summary() == "3 phones: 1 ready, 2 without a name"

    fake.group = [
        *PHONES,
        replace(PHONES[2], id="a4", state=State.REFUSED),
        replace(PHONES[2], id="a5", state=State.WAITING),
        replace(PHONES[0], id="a6", state=State.REFUSED),
    ]
    rig.app.refresh()
    rig.settle()
    assert rig.app.summary() == "6 phones: 1 ready, 1 waiting for the phone, 1 failed, 3 without a name"

    fake.group = [PHONES[0]]
    rig.app.refresh()
    rig.settle()
    assert rig.app.summary() == "1 phone: 1 without a name"

    fake.group = []
    rig.app.refresh()
    rig.settle()
    assert rig.app.summary() == "No phones yet."


def test_a_listing_that_failed_is_not_no_phones(rig: Rig) -> None:
    rig.classes["fake"] = Broken
    rig.app.paste_settings(LINE, NOW)
    rig.app.connect("fake", VALUES)
    rig.app.refresh()
    rig.settle()

    assert rig.app.summary() == "", "the MDM never listed its phones, so none is not said"


def test_after_a_restart_the_app_says_why_the_token_is_asked_for(rig: Rig) -> None:
    rig.connected()
    rig.app.close()
    rig.app = rig.start()
    typed = "Type the Password again to connect: it is kept only while the connector runs."

    first = rig.app.status
    pasted = rig.app.paste_settings(LINE, NOW)

    assert (first, rig.app.mdm) == (typed, None)
    assert pasted == f"Deploy App unit.example.fi accepts phones until 14:00. {typed}"


def test_after_a_restart_an_mdm_without_a_secret_is_connected_at_once(rig: Rig) -> None:
    class NoSecret(FakeMdm):
        fields: ClassVar = FakeMdm.fields[:1]

    rig.classes["fake"] = NoSecret
    rig.connected()
    rig.app.close()
    rig.app = rig.start()

    assert (rig.app.status, rig.app.connected) == ("", True)


def test_several_secrets_not_at_hand_are_all_named(rig: Rig) -> None:
    class TwoSecrets(FakeMdm):
        fields: ClassVar = (*FakeMdm.fields, Field("pin", "PIN", secret=True))

    rig.classes["fake"] = TwoSecrets
    rig.connected(values={**VALUES, "pin": "1234"})
    rig.app.paste_settings(OTHER, NOW)
    rig.app.close()
    rig.app = rig.start()

    rig.app.open("unit.example.fi", NOW)

    assert rig.app.status.endswith(
        "Type the Password and PIN again to connect: they are kept only while the connector runs."
    )


def test_the_table(rig: Rig) -> None:
    fake = rig.connected()
    fake.group = [*PHONES[:2], replace(PHONES[2], state=State.REFUSED, detail="name taken")]
    rig.app.refresh()
    rig.settle()

    rows = rig.app.rows(NOW.astimezone(datetime.timezone(datetime.timedelta(hours=2))))

    assert rows == [
        app.Row("a1", "", "Pretend Phone 7", "2026-11-01 11:00", "waiting for a name", True, False, False, False),
        app.Row("a2", "", "Pretend Phone 7", "2026-11-01 11:05", "waiting for a name", True, False, False, False),
        app.Row(
            "a3",
            "OTTER1",
            "Pretend Tablet 2",
            "2026-11-01 11:10",
            "refused by Deploy App: name taken",
            False,
            True,
            False,
            True,
        ),
    ]


def test_a_batch_numbered_on_from_the_callsigns_in_use(rig: Rig) -> None:
    fake = rig.connected()
    ids = rig.app.targets([])
    callsigns = rig.app.numbered("OTTER", rig.app.first_number("otter"), len(ids))

    preview = rig.app.preview(ids, callsigns, NOW)
    said = rig.app.name(preview.names)
    results = rig.settle()

    assert (ids, callsigns) == (["a1", "a2"], ["OTTER2", "OTTER3"])
    assert (preview.ok, preview.button, preview.warning) == (True, "Name 2 phones", "")
    assert [(row.phone, row.joined, row.callsign, row.refusal) for row in preview.rows] == [
        ("Pretend Phone 7", "2026-11-01 09:00", "OTTER2", ""),
        ("Pretend Phone 7", "2026-11-01 09:05", "OTTER3", ""),
    ]
    assert said == "Naming 2 phones."
    assert [result.job for result in results] == [app.Job.NAME]
    assert (rig.app.status, rig.app.lines) == ("2 phones named.", ("Phone a1 is OTTER2", "Phone a2 is OTTER3"))
    rig.settle()
    assert rig.app.lines, "the lines stay while the phones are listed again"
    assert [row.callsign for row in rig.app.rows(NOW)] == ["OTTER2", "OTTER3", "OTTER1"]
    assert fake.calls[-3:] == ["phones", "name", "phones"]


def test_the_list_is_kept_and_used_up(rig: Rig) -> None:
    """Typed and saved before any phone joined; what was given is gone from it next time"""
    rig.app.paste_settings(LINE, NOW)
    rig.app.keep_callsigns("BEAR", rig.app.listed("BEAR7\n\n  BEAR9 \nBEAR11\n"))
    rig.connected()

    preview = rig.app.preview(["a2"], rig.app.conn.callsigns, NOW)
    rig.app.name(preview.names)
    rig.settle()

    assert rig.start().conn.callsigns == ["BEAR9", "BEAR11"]
    assert rig.app.conn.prefix == "BEAR"


@pytest.mark.parametrize(
    ("ids", "callsigns", "refusals", "batch"),
    [
        (["a1", "a2"], ["BEAR1"], ["", "no callsign left in the list"], "2 phones to name, but only 1 callsign."),
        (["a1", "a2"], ["BEAR1", "bear1"], ["", "bear1 is given twice"], ""),
        (["a1", "a1"], ["BEAR1", "BEAR2"], ["", "phone a1 is named twice"], ""),
        (["a1"], ["otter1"], ["otter1 already belongs to phone a3"], ""),
        (["a1", "zz"], ["-BEAR", "BEAR2"], ["-BEAR: a callsign is 3-32", "phone zz is not in the group"], ""),
        ([], ["BEAR1"], [], "No phone is waiting for a name. To give a phone another callsign, select it first."),
    ],
)
def test_every_refusal_is_said_and_stops_the_naming(
    rig: Rig, ids: list[str], callsigns: list[str], refusals: list[str], batch: str
) -> None:
    rig.connected()

    preview = rig.app.preview(ids, callsigns, NOW)

    assert [row.refusal[: len(said)] for row, said in zip(preview.rows, refusals, strict=True)] == refusals
    assert (preview.refusal, preview.ok) == (batch, False)
    with pytest.raises(
        app.AppError, match="Preview the callsigns first: nothing is named without a preview that passed."
    ):
        rig.app.name(dict(zip(ids, callsigns, strict=False)))


def test_only_the_names_previewed_are_given(rig: Rig) -> None:
    rig.connected()
    rig.app.preview(["a1"], ["BEAR1"], NOW)

    with pytest.raises(app.AppError, match="Preview the callsigns first"):
        rig.app.name({"a1": "BEAR2"})
    rig.app.name({"a1": "BEAR1"})
    with pytest.raises(app.AppError, match="Preview the callsigns first"):
        rig.app.name({"a1": "BEAR1"})


def test_a_phone_that_just_joined_is_warned_of(rig: Rig) -> None:
    fake = rig.connected()
    just = (NOW - datetime.timedelta(minutes=4)).isoformat()
    fake.group = [replace(phone, joined=just) for phone in PHONES]
    rig.app.refresh()
    rig.settle()

    one = rig.app.preview(["a1"], ["BEAR1"], NOW)
    fake.group = [*PHONES[:2], replace(PHONES[2], joined=just)]
    rig.app.refresh()
    rig.settle()
    single = rig.app.preview(["a1"], ["BEAR1"], NOW)

    assert one.warning == (
        "3 phones joined less than 10 minutes ago and may still be installing their apps. Naming now sends the "
        "policy to every phone again, which stops apps that are still installing: wait until they have their apps."
    )
    assert single.warning == (
        "A phone joined less than 10 minutes ago and may still be installing its apps. Naming now sends the "
        "policy to every phone again, which stops apps that are still installing: wait until it has its apps."
    )
    assert single.ok


def test_phones_changed_since_the_preview_name_nothing(rig: Rig) -> None:
    fake = rig.connected()
    preview = rig.app.preview(["a1"], ["BEAR1"], NOW)
    fake.group = [replace(PHONES[1], callsign="BEAR1"), *PHONES[::2]]

    rig.app.name(preview.names)
    [result] = rig.settle()

    assert result.error == "Nothing named: BEAR1 already belongs to phone a2. Refresh and give callsigns again."
    assert "name" not in fake.calls


class Refusing(FakeMdm):
    """Refuses after the first phone, as an MDM failing half-way does"""

    def __init__(self, values: Mapping[str, str]) -> None:
        super().__init__(values, refuse_after=1)


def test_a_batch_stopped_half_way_says_what_it_named(rig: Rig) -> None:
    rig.classes["fake"] = Refusing
    rig.app.paste_settings(LINE, NOW)
    rig.app.keep_callsigns("", ["BEAR1", "BEAR2", "BEAR3"])
    rig.connected()

    rig.app.name(rig.app.preview(["a1", "a2"], ["BEAR1", "BEAR2"], NOW).names)
    [result] = rig.settle()

    assert result.named == {"a1": "BEAR1"}
    assert rig.app.status == "The fake MDM refused phone a2 Named before it stopped: a1 as BEAR1."
    assert rig.app.lines == ("Phone a1 is BEAR1",)
    assert rig.app.conn.callsigns == ["BEAR2", "BEAR3"]


def test_the_lines_go_once_anything_else_is_said(rig: Rig) -> None:
    rig.connected()
    rig.app.name(rig.app.preview(["a1"], ["BEAR1"], NOW).names)
    rig.settle()
    assert rig.app.lines == ("Phone a1 is BEAR1",)

    rig.app.paste_settings(LINE, NOW)

    assert not rig.app.lines


def test_asking_again(rig: Rig) -> None:
    fake = rig.connected()
    fake.group[2] = replace(fake.group[2], state=State.REFUSED)
    rig.app.refresh()
    rig.settle()

    rig.app.ask_again(["a3"])
    rig.settle()
    first = (rig.app.status, rig.app.lines)
    said = rig.app.ask_again([])
    rig.settle()

    assert first == ("Asked again.", ("Phone a3: certificate asked for again",))
    assert said == "Asking every named phone whose certificate failed again."
    assert (rig.app.status, rig.app.lines) == ("No phone needed asking again.", ())
    assert "ask_again" in fake.calls


def test_a_phone_with_its_certificate_is_not_asked_again(rig: Rig) -> None:
    fake = rig.connected()
    calls = len(fake.calls)

    with pytest.raises(
        app.AppError,
        match="Nothing to ask again: the selected phones have their certificates, or have no callsign yet.",
    ):
        rig.app.ask_again(["a3"])

    assert len(fake.calls) == calls
    assert not rig.app.detail("a3", NOW).can_ask_again


def test_a_phone_without_a_callsign_is_not_asked_again(rig: Rig) -> None:
    fake = rig.connected()
    fake.group[0] = replace(fake.group[0], state=State.FAILED)
    rig.app.refresh()
    rig.settle()

    with pytest.raises(app.AppError, match="Nothing to ask again"):
        rig.app.ask_again(["a1"])

    assert not rig.app.detail("a1", NOW).can_ask_again


def test_a_mixed_selection_asks_only_the_failed_again(rig: Rig) -> None:
    fake = rig.connected()
    fake.group[2] = replace(fake.group[2], state=State.WAITING)
    rig.app.refresh()
    rig.settle()

    said = rig.app.ask_again(["a1", "a2", "a3"])
    rig.settle()

    assert said == "Asking 1 phone again; 2 phones left alone."
    assert rig.app.detail("a3", NOW).can_ask_again
    assert (rig.app.status, rig.app.lines) == ("Asked again.", ("Phone a3: certificate asked for again",))


class Broken(FakeMdm):
    """Fails the way no MDM is meant to"""

    failure: ClassVar[type[Exception]] = RuntimeError

    def phones(self) -> list[Phone]:
        raise self.failure("the token is pa55word")

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        return ["Asked"]


class Unreachable(Broken):
    failure = ConnectionRefusedError


@pytest.mark.parametrize(
    ("broken", "says"),
    [(Unreachable, "Cannot reach the MDM."), (Broken, "The MDM plugin failed; the log says more: ")],
)
def test_any_failure_is_one_sentence_and_the_worker_goes_on(rig: Rig, broken: type[FakeMdm], says: str) -> None:
    rig.classes["fake"] = broken
    rig.app.paste_settings(LINE, NOW)
    rig.app.connect("fake", VALUES)
    rig.settle()

    rig.app.refresh()
    [result] = rig.settle()
    rig.app.ask_again([])
    rig.settle()

    assert result.error.startswith(says)
    assert "Traceback" not in result.error
    assert (rig.app.status, rig.app.lines) == ("Asked again.", ("Asked",))


def test_a_settings_file_that_cannot_be_written_is_said(rig: Rig) -> None:
    rig.app.store = store.Store(rig.directory / "settings.json" / "not-a-directory")
    rig.directory.mkdir()
    (rig.directory / "settings.json").write_text("{}", encoding="utf-8")

    with pytest.raises(app.AppError, match="cannot be written"):
        rig.app.paste_settings(LINE, NOW)
    assert rig.app.window is None


def test_a_listing_of_the_plugins_that_failed_is_said(rig: Rig) -> None:
    rig.unlisted = plugins.PluginError("Two packages declare the MDM demo")
    with pytest.raises(app.AppError, match="Two packages declare the MDM demo"):
        rig.app.mdms()


def test_a_phones_detail(rig: Rig) -> None:
    fake = rig.connected()
    fake.group = [replace(PHONES[0], callsign="OTTER2", state=State.FAILED, detail="Fleet's own words")]
    rig.app.refresh()
    rig.settle()

    detail = rig.app.detail("a1", NOW)

    assert detail == app.Detail(
        "a1",
        "OTTER2",
        "Pretend Phone 7",
        "2026-11-01 09:00",
        "failed",
        "Fleet's own words",
        "Deploy App unit.example.fi accepts phones until 14:00",
        True,
    )
    with pytest.raises(app.AppError, match="Phone zz is no longer in the MDM's list: refresh."):
        rig.app.detail("zz", NOW)


def test_the_log_holds_no_secret(tmp_path: Path) -> None:
    redact = app.Redact()
    path = app.open_log(tmp_path / "logs", redact)
    redact.add(CHALLENGE)
    redact.add("")
    try:
        logging.getLogger("odmdm_connector.test").info("pasted %s", CHALLENGE)
        try:
            raise RuntimeError(f"challenge {CHALLENGE}")
        except RuntimeError:
            logging.getLogger("odmdm_connector.test").exception("failed")
    finally:
        handlers = [
            handler for handler in logging.getLogger().handlers if getattr(handler, "formatter", None) is redact
        ]
        for handler in handlers:
            logging.getLogger().removeHandler(handler)
            handler.close()

    text = path.read_text(encoding="utf-8")
    assert CHALLENGE not in text
    assert text.count("[secret]") == 2


class Echoing(FakeMdm):
    """An MDM whose answers repeat the secrets it was given"""

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        yield Step(f"Signed in with {self.values['password']}", True, f"challenge {window.challenge}")
        raise MdmError(f"Refused {window.challenge} for {self.values['password']}")

    def set_challenge(self, window: Settings) -> None:
        raise MdmError(f"Bad challenge {window.challenge}", lines=[f"password {self.values['password']}"])

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        return [f"Asked with {self.values['password']}"]


def test_no_secret_reaches_the_operator_or_the_log(rig: Rig, tmp_path: Path) -> None:
    rig.classes["fake"] = Echoing
    terminal = io.StringIO()
    stderr = logging.StreamHandler(terminal)
    logging.getLogger().addHandler(stderr)
    redact = app.Redact()
    path = app.open_log(tmp_path / "logs", redact)
    rig.app.redact = redact
    try:
        rig.app.paste_settings(LINE, NOW)
        rig.app.connect("fake", VALUES)
        results = rig.settle()
        statuses = [rig.app.status]
        rig.app.paste_settings(LINE, NOW)
        results += rig.settle()
        statuses.append(rig.app.status)
        rig.app.ask_again([])
        rig.settle()
    finally:
        for handler in [stderr, *(each for each in logging.getLogger().handlers if each.formatter is redact)]:
            logging.getLogger().removeHandler(handler)
            handler.close()

    shown = [
        *statuses,
        *(result.error for result in results),
        *(line for result in results for line in result.lines),
        *(part for result in results for step in result.steps for part in (step.label, step.message)),
    ]
    assert statuses == ["Refused [secret] for [secret]", "Bad challenge [secret]"]
    assert [step.label for result in results for step in result.steps] == ["Signed in with [secret]"]
    for text in [*shown, path.read_text(encoding="utf-8"), terminal.getvalue()]:
        assert CHALLENGE not in text
        assert VALUES["password"] not in text
    assert "[secret]" in terminal.getvalue()
    assert rig.app.lines == ("Asked with [secret]",)


def test_the_app_on_this_computer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Its settings file and log in one directory, here a temporary one, and the system's keychain"""
    backend = MemoryBackend()
    monkeypatch.setattr(store, "directory", lambda: tmp_path)
    monkeypatch.setattr(store, "platform_backend", lambda: backend)
    before = list(logging.getLogger().handlers)

    started = app.App.default()
    try:
        assert started.log_path == tmp_path / "odmdm-connector.log"
        assert started.store.path == tmp_path / "settings.json"
        assert (started.keychain.backend, started.keeps) == (backend, True)
    finally:
        started.close()
        for handler in set(logging.getLogger().handlers) - set(before):
            logging.getLogger().removeHandler(handler)
            handler.close()


class HalfSetUp(FakeMdm):
    """Stops its setup after the first step"""

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        yield Step("Group found", True)
        raise MdmError("The fake MDM refused the template")


def test_a_setup_that_stops_shows_the_steps_done(rig: Rig) -> None:
    rig.classes["fake"] = HalfSetUp
    rig.app.paste_settings(LINE, NOW)
    rig.app.connect("fake", VALUES)

    [result] = rig.settle()

    assert (result.steps, result.error) == ((Step("Group found", True),), "The fake MDM refused the template")
    assert rig.app.status == "The fake MDM refused the template"


class Unconfigurable(FakeMdm):
    def __init__(self, values: Mapping[str, str]) -> None:
        raise MdmError("The fake MDM needs a group")


def test_a_plugin_that_cannot_be_used_is_said(rig: Rig) -> None:
    rig.connected()
    rig.classes["fake"] = Unconfigurable

    with pytest.raises(app.AppError, match="The fake MDM needs a group"):
        rig.app.connect("fake", VALUES)
    later = rig.start()
    with pytest.raises(app.AppError, match="The fake MDM needs a group"):
        later.connect("fake", VALUES)
    rig.refused = "The MDM fake cannot be loaded"

    assert later.mdm is None
    with pytest.raises(app.AppError, match="The MDM fake cannot be loaded"):
        rig.start().form("fake")
    later.close()


def test_nothing_reaches_the_mdm_before_it_is_connected(rig: Rig) -> None:
    rig.app.refresh()

    with pytest.raises(app.AppError, match="Connect to the MDM first."):
        rig.app.name({})
    with pytest.raises(app.AppError, match="Connect to the MDM first."):
        rig.app.ask_again([])
    assert not rig.app.busy()


def test_one_listing_at_a_time(rig: Rig) -> None:
    """A refresh asked while one is under way is not queued behind it"""
    fake = rig.connected()
    fake.calls.clear()

    rig.app.refresh()
    rig.app.refresh()
    rig.settle()

    assert fake.calls == ["phones"]


def test_a_time_that_is_not_one_is_shown_as_it_is(rig: Rig) -> None:
    fake = rig.connected()
    fake.group = [replace(PHONES[0], joined="yesterday"), replace(PHONES[1], joined="2026-11-01T09:05:00")]
    rig.app.refresh()
    rig.settle()

    assert [row.joined for row in rig.app.rows(NOW)] == ["yesterday", "2026-11-01 09:05"]
    assert not rig.app.preview(["a1"], ["BEAR1"], NOW).warning


class Expired(FakeMdm):
    """Does not accept its password, as an MDM does once a token has ended"""

    def phones(self) -> list[Phone]:
        raise MdmError("The fake MDM does not accept the password", unauthorized=True)


def test_a_secret_the_mdm_does_not_accept_is_typed_again(rig: Rig) -> None:
    rig.connected()
    rig.classes["fake"] = Expired
    rig.app.connect("fake", VALUES)
    rig.settle()

    rig.app.refresh()
    [result] = rig.settle()

    assert result.unauthorized
    assert (rig.app.connected, rig.app.phones, rig.app.summary()) == (False, [], "")
    assert rig.app.status == "The fake MDM does not accept the password"
    assert rig.app.form("fake").fields[1].value == ""
    assert rig.app.connect("fake", VALUES) == "Setting Fake MDM up for Deploy App unit.example.fi."


def test_a_refusal_of_an_mdm_no_longer_held_lets_go_of_nothing(rig: Rig) -> None:
    """The token was typed again while a listing with the old one was under way"""
    rig.connected()
    rig.classes["fake"] = Expired
    rig.app.connect("fake", VALUES)
    rig.settle()
    rig.app.refresh()
    rig.classes["fake"] = FakeMdm
    rig.app.connect("fake", VALUES)

    rig.settle()

    assert rig.app.connected
    assert rig.app.form("fake").fields[1].value == "pa55word"


@pytest.mark.parametrize("sender", ["", "https://unit.example.fi", "https://mtls.unit.example.fi"])
def test_a_line_is_taken_from_its_own_deploy_app(rig: Rig, sender: str) -> None:
    """Or typed by hand, which names no sender"""
    assert rig.app.read_settings(LINE, NOW, sender).host == "unit.example.fi"
    assert rig.app.paste_settings(LINE, NOW, sender) == "Deploy App unit.example.fi accepts phones until 14:00."


@pytest.mark.parametrize(
    "sender",
    [
        "https://other.example.fi",
        "http://mtls.unit.example.fi",
        "https://mtls.unit.example.fi:8443",
        "https://evil.example.fi.unit.example.fi",
        "https://unit.example.fi.evil.example",
        "null",
    ],
)
def test_a_line_sent_by_another_page_is_refused(rig: Rig, sender: str) -> None:
    says = (
        f"These settings are for Deploy App unit.example.fi, but the page that sent them is {sender}: "
        "use the button on that Deploy App's own MDM page."
    )

    for take in (rig.app.read_settings, rig.app.paste_settings):
        with pytest.raises(app.AppError) as raised:
            take(LINE, NOW, sender)
        assert str(raised.value) == says
    assert (rig.app.window, rig.app.saved) == (None, store.Saved())


def test_a_remembered_token_connects_at_the_next_start(keyed: Rig) -> None:
    assert keyed.app.keeps
    keyed.connected()
    keyed.app.paste_settings(OTHER, NOW)
    keyed.app.connect("fake", {"group": "Other unit", "password": "0ther"})  # pragma: allowlist secret
    keyed.settle()
    assert keyed.backend is not None and keyed.backend.items[KEPT] == VALUES["password"]
    keyed.app.close()

    keyed.app = keyed.start()
    at_start = (keyed.app.status, keyed.app.connected)
    opened = keyed.app.open("unit.example.fi", NOW)

    assert at_start == ("", True)
    assert opened == "Deploy App unit.example.fi accepts phones until 14:00."
    assert isinstance(keyed.app.mdm, FakeMdm) and keyed.app.mdm.values == VALUES
    assert keyed.app.form("fake").fields[1].value == VALUES["password"]
    keyed.app.close()
    keyed.app = keyed.start()
    assert keyed.app.paste_settings(LINE, NOW).endswith("Giving the MDM the new challenge."), "connected, no typing"


def test_the_keychain_is_read_once_until_the_token_changes(keyed: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """A keychain may ask the operator before it answers, and every request would wait for that"""
    keyed.connected()
    keyed.app.close()
    backend = keyed.backend
    assert backend is not None
    reads: list[str] = []
    read = backend.get_password

    def counted(service: str, account: str) -> str | None:
        reads.append(account)
        return read(service, account)

    monkeypatch.setattr(backend, "get_password", counted)
    keyed.app = keyed.start()
    for _ in range(3):
        assert [each.remembered for each in keyed.app.form("fake").fields] == [False, True]
        keyed.app.banner(NOW)
    assert reads == ["unit.example.fi:fake:password"]

    keyed.app.connect("fake", VALUES, remember=False)
    keyed.settle()
    reads.clear()
    assert [[each.remembered for each in keyed.app.form("fake").fields] for _ in range(2)] == [[False, False]] * 2
    assert reads == ["unit.example.fi:fake:password"], "read once more after connecting"


def test_a_token_not_remembered_is_removed_from_the_keychain(keyed: Rig) -> None:
    keyed.connected()

    said = keyed.app.connect("fake", VALUES, remember=False)

    assert keyed.backend is not None and keyed.backend.items == {}
    assert said == "Setting Fake MDM up for Deploy App unit.example.fi."
    assert keyed.app.connected, "held until the connector stops"
    keyed.app.close()
    keyed.app = keyed.start()
    assert (keyed.app.status, keyed.app.connected) == (
        "Type the Password again to connect: it is not kept on this computer.",
        False,
    )


def test_a_keychain_that_does_not_take_the_token_is_said(keyed: Rig) -> None:
    assert keyed.backend is not None
    keyed.backend.broken = True
    keyed.app.paste_settings(LINE, NOW)

    said = keyed.app.connect("fake", VALUES)

    assert said == (
        "This computer's keychain cannot be used, so the Password is kept only until the connector stops. "
        "Setting Fake MDM up for Deploy App unit.example.fi."
    )
    # The page shows the line while the setup runs, beside a heading that already says what the sentence does
    assert (keyed.app.status, keyed.app.lines) == (
        "Setting Fake MDM up for Deploy App unit.example.fi.",
        ("This computer's keychain cannot be used, so the Password is kept only until the connector stops.",),
    )
    assert keyed.app.connected
    assert VALUES["password"] not in (keyed.directory / "settings.json").read_text(encoding="utf-8")


def test_a_token_the_mdm_does_not_accept_leaves_the_keychain(keyed: Rig) -> None:
    keyed.connected()
    keyed.classes["fake"] = Expired
    keyed.app.connect("fake", VALUES)
    keyed.settle()

    keyed.app.refresh()
    keyed.settle()

    assert keyed.backend is not None and keyed.backend.items == {}
    assert not keyed.app.connected
    keyed.app.close()
    keyed.app = keyed.start()
    assert keyed.app.status == "Type the Password again to connect: it is not kept on this computer."


def test_removing_a_deploy_app_removes_its_kept_token(keyed: Rig) -> None:
    keyed.connected()

    said = keyed.app.remove("unit.example.fi")

    assert said == "Deploy App unit.example.fi is removed from this app, and its MDM token from this computer."
    assert keyed.backend is not None and keyed.backend.items == {}


@pytest.mark.parametrize("why", ["undeletable", "plugin gone"])
def test_a_kept_token_that_cannot_be_removed_is_said(keyed: Rig, why: str) -> None:
    keyed.connected()
    assert keyed.backend is not None
    if why == "undeletable":
        keyed.backend.undeletable = True
    else:
        keyed.refused = "The MDM fake cannot be loaded"

    said = keyed.app.remove("unit.example.fi")

    assert said == (
        "Deploy App unit.example.fi is removed from this app, but its MDM token may still be in this computer's "
        "keychain: delete the odmdm-connector item of unit.example.fi there by hand."
    )
    assert keyed.app.saved == store.Saved()


def test_without_keyring_the_app_holds_the_token_for_the_session(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """As in the one-file build, which leaves keyring out"""
    monkeypatch.setitem(sys.modules, "keyring", None)

    def start() -> app.App:
        keychain = store.Keychain(store.platform_backend())
        return app.App(store.Store(rig.directory), installed=rig.installed, load=rig.load, keychain=keychain)

    rig.app.close()
    rig.app = start()
    assert not rig.app.keeps
    assert rig.connected().values == VALUES
    rig.app.close()
    rig.app = start()
    assert (rig.app.status, rig.app.connected) == (
        "Type the Password again to connect: it is kept only while the connector runs.",
        False,
    )


def test_a_setup_shows_its_steps_as_they_are_done(rig: Rig) -> None:
    """A setup can take minutes, so each step is there to tick as soon as it is done, secrets blanked out"""
    go = threading.Event()

    class Slow(FakeMdm):
        def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
            yield Step("Group found", True, f"with {self.values['password']}")
            go.wait(5)
            yield Step("Certificate template stated", True)

    rig.classes["fake"] = Slow
    rig.app.paste_settings(LINE, NOW)
    rig.app.connect("fake", VALUES)
    for _ in range(500):
        if rig.app.progress:
            break
        time.sleep(0.01)

    assert rig.app.progress == (Step("Group found", True, "with [secret]"),)
    go.set()
    (result,) = rig.settle()
    assert [step.label for step in result.steps] == ["Group found", "Certificate template stated"]

    go.clear()
    rig.app.connect("fake", VALUES)
    assert rig.app.progress in ((), (Step("Group found", True, "with [secret]"),)), "the last setup's steps are gone"
    go.set()
    rig.settle()


class Forced(FakeMdm):
    """Records the window and force of every setup"""

    setups: ClassVar[list[tuple[Settings, bool]]] = []

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        Forced.setups.append((window, force))
        return super().setup(window, force, dry_run)


def test_the_policy_is_sent_again_as_apply_force_policy_with_its_steps(keyed: Rig) -> None:
    """A setup with force and no challenge, so a window pasted earlier never replaces a newer one"""
    keyed.classes["fake"] = Forced
    keyed.connected()
    Forced.setups.clear()

    note = keyed.app.policy_note()
    said = keyed.app.send_policy()
    [result] = keyed.settle()

    assert note == (
        "For a phone whose browser refuses its certificate: the MDM sends the policy to every phone again. "
        "Phones keep their certificates. Not while phones are still installing apps: it stops their installs."
    )
    assert said == "Sending the policy again for Deploy App unit.example.fi."
    assert Forced.setups == [(Settings("https://unit.example.fi/scep", ""), True)]
    assert (result.job, result.error) == (app.Job.POLICY, "")
    assert result.steps[-1] == Step("Certificate template stated", True, "forced")
    assert keyed.app.status == "Fake MDM has sent the policy again to the phones of Deploy App unit.example.fi."

    # After a restart, with enrolment closed and no window pasted: the command line needs none either
    keyed.app.close()
    keyed.app = keyed.start()
    keyed.app.send_policy()
    keyed.settle()
    assert Forced.setups[-1] == (Settings("https://unit.example.fi/scep", ""), True)


def test_the_policy_is_sent_again_one_job_at_a_time(keyed: Rig) -> None:
    """Every send rewrites the policy: asked again while one waits or runs, or a setup does, nothing more is sent"""
    keyed.classes["fake"] = Forced
    keyed.connected()
    Forced.setups.clear()

    first = keyed.app.send_policy()
    second = keyed.app.send_policy()
    keyed.settle()

    assert first == second == "Sending the policy again for Deploy App unit.example.fi."
    assert Forced.setups == [(Settings("https://unit.example.fi/scep", ""), True)]

    keyed.app.paste_settings(LINE, NOW)
    keyed.app.connect("fake", VALUES)
    during_setup = keyed.app.send_policy()
    keyed.settle()

    assert during_setup == "Sending the policy again for Deploy App unit.example.fi."
    assert [force for _, force in Forced.setups] == [True, False], "the setup only, nothing forced beside it"


class PolicyRefused(FakeMdm):
    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        yield Step("Group found", True)
        if force:
            raise MdmError("Fleet refused: the policy cannot be uploaded")


def test_a_policy_the_mdm_refuses_is_its_reason(rig: Rig) -> None:
    rig.classes["fake"] = PolicyRefused
    rig.connected()

    rig.app.send_policy()
    [result] = rig.settle()

    assert (result.job, result.steps) == (app.Job.POLICY, (Step("Group found", True),))
    assert result.error == rig.app.status == "Fleet refused: the policy cannot be uploaded"


@pytest.mark.parametrize(
    ("lacking", "says"), [(SEND_POLICY, "The fake MDM cannot send_policy"), (SETUP, "The fake MDM cannot setup")]
)
def test_the_policy_is_not_offered_by_an_mdm_that_cannot_send_it(rig: Rig, lacking: str, says: str) -> None:
    with pytest.raises(app.AppError, match="^Connect to the MDM first.$"):
        rig.app.send_policy()
    assert rig.app.policy_note() == ""
    rig.classes["fake"] = FakeMdm.lacking(lacking)
    rig.app.paste_settings(LINE, NOW)
    rig.app.connect("fake", VALUES)
    rig.settle()

    assert rig.app.policy_note() == ""
    with pytest.raises(app.AppError, match=f"^{says}$"):
        rig.app.send_policy()
    assert not rig.app.busy()


#: The Fleet plugin's fields on the fake MDM: whatever reads them gets the team's default
FleetFields = type("FleetFields", (FakeMdm,), {"fields": FleetPlugin.fields})
GOLDEN = LINE.replace("unit.example.fi", "golden-monkey.example.fi")


def test_the_team_suggested_is_named_after_the_deployment(rig: Rig) -> None:
    rig.classes["fake"] = FleetFields
    rig.app.paste_settings(GOLDEN, NOW)

    form = {each.field.key: each for each in rig.app.form("fake").fields}
    rig.app.connect("fake", {"mdm_url": "https://mdm.example.fi", "token": "t0ken"})  # pragma: allowlist secret
    rig.settle()

    assert (form["team"].value, form["team"].field.default) == ("golden-monkey DeployApp", "golden-monkey DeployApp")
    assert form["key_alias"].value == "rmscep"
    assert rig.app.saved.connections["golden-monkey.example.fi"].values["team"] == "golden-monkey DeployApp"
    assert isinstance(rig.app.mdm, FakeMdm) and rig.app.mdm.values["team"] == "golden-monkey DeployApp"


def test_a_team_saved_or_typed_stays_as_it_is(rig: Rig) -> None:
    rig.classes["fake"] = FleetFields
    rig.app.paste_settings(GOLDEN, NOW)
    rig.app.connect("fake", {"mdm_url": "https://mdm.example.fi", "token": "t0ken", "team": "Deploy App"})
    rig.settle()

    [team] = [each for each in rig.app.form("fake").fields if each.field.key == "team"]

    assert team.value == "Deploy App"
    assert rig.app.saved.connections["golden-monkey.example.fi"].values["team"] == "Deploy App"
    with pytest.raises(app.AppError, match="^Fill in Team.$"):
        rig.app.connect("fake", {"mdm_url": "https://mdm.example.fi", "token": "t0ken", "team": " "})
