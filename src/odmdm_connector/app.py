"""The connector without a command line: what the page shows and does, with no user interface in it

The App works with several Deploy Apps, each a connection by the host of its SCEP URL, and with one
of them at a time: the active one, whose MDM, enrolment window and phones it holds.

A view calls an App and renders the plain data it answers: a Banner, Rows, a Form, a Preview, a
Detail and Results. Work that talks to the MDM runs on the App's one worker thread, and its Results
wait in a queue that the view drains on a timer with drain(), which is also where the App takes
them in. The App never touches a user interface and a view never touches the network or the file
system. Time is passed in.

Every failure the operator meets is one sentence: an AppError raised at once, or a Result's error.
"""

import datetime
import logging
import math
import queue
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import core, plugins, settings, store
from .mdm import ASK_AGAIN, SEND_POLICY, SET_CHALLENGE, SETUP, Field, Mdm, MdmError, Phone, State, Step
from .settings import Settings

LOGGER = logging.getLogger(__name__)

#: How long before the window closes the banner warns
CLOSING = datetime.timedelta(minutes=10)
#: A phone that joined this recently may still be installing its apps
RECENT = datetime.timedelta(minutes=10)
#: The states of a phone whose certificate failed, which asking again or another callsign may cure
FAILED = (State.NOT_ASKED, State.REFUSED, State.UNREACHABLE, State.FAILED)


class AppError(Exception):
    """What the operator asked cannot be done, said in one sentence"""


class Level(Enum):
    """How the banner reads"""

    #: No enrolment window known yet
    NONE = "none"
    OPEN = "open"
    #: Closing within ten minutes
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True)
class Banner:
    level: Level
    text: str
    #: The same in a few words, beside the Deploy App's name in a list of them
    short: str


@dataclass(frozen=True)
class DeployApp:
    """A connection as the list of them shows it"""

    host: str
    #: What the banner would say of it
    banner: Banner
    active: bool


@dataclass(frozen=True)
class Reading:
    """What a settings line says, before it is taken"""

    host: str
    #: Until when that Deploy App accepts phones, as the banner says it
    until: str
    #: Whether a connection for that Deploy App is saved already
    known: bool


@dataclass(frozen=True)
class Row:
    """One phone as the table shows it"""

    id: str
    #: Empty while nobody has named it
    callsign: str
    phone: str
    joined: str
    #: The state's sentence and the plugin's words
    certificate: str
    #: It has no callsign yet
    waiting: bool
    #: Its certificate was refused or failed: ask it again, or give it another callsign
    failed: bool
    #: It has its certificate
    ready: bool
    #: Asking it again can help, and the MDM can ask
    askable: bool


@dataclass(frozen=True)
class FieldValue:
    """A field of the chosen MDM and its value now; a secret's as typed, or as kept in the keychain"""

    field: Field
    value: str
    #: A secret kept in this computer's keychain, not only held until the connector stops
    remembered: bool = False


@dataclass(frozen=True)
class Form:
    """What setup asks for the chosen MDM"""

    name: str
    title: str
    fields: tuple[FieldValue, ...]


@dataclass(frozen=True)
class PreviewRow:
    id: str
    phone: str
    joined: str
    callsign: str
    #: Why this one cannot be named, empty when it can
    refusal: str


@dataclass(frozen=True)
class Preview:
    """What naming would do, and every reason it cannot"""

    rows: tuple[PreviewRow, ...]
    #: Why the batch as a whole cannot be named, empty when it can
    refusal: str = ""
    #: Said before naming, without stopping it
    warning: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.rows) and not self.refusal and not any(row.refusal for row in self.rows)

    @property
    def names(self) -> dict[str, str]:
        return {row.id: row.callsign for row in self.rows}

    @property
    def button(self) -> str:
        return f"Name {_count(len(self.rows), 'phone')}"


@dataclass(frozen=True)
class Detail:
    """One phone, with what the app itself knows"""

    id: str
    callsign: str
    phone: str
    joined: str
    #: The state's sentence
    state: str
    #: The plugin's own words, may be empty
    detail: str
    #: Whether Deploy App accepts phones now, as the banner says it
    window: str
    can_ask_again: bool


class Job(Enum):
    """What the worker was asked to do"""

    SETUP = "setup"
    CHALLENGE = "challenge"
    PHONES = "phones"
    NAME = "name"
    ASK_AGAIN = "ask again"
    #: Setup again with the policy sent even if unchanged
    POLICY = "policy"


@dataclass(frozen=True)
class Result:
    """A job done; error is its one sentence when it failed"""

    job: Job
    error: str = ""
    lines: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    phones: tuple[Phone, ...] | None = None
    #: The phones named, by id, also when naming stopped half-way
    named: dict[str, str] = field(default_factory=dict)
    #: The Deploy App the job was for, which may no longer be the active one
    host: str = ""
    #: The MDM did not accept its secret: the operator has to type it again
    unauthorized: bool = False
    #: The MDM that did the job, so that a refusal of an MDM no longer held drops nothing
    by: object = field(default=None, compare=False, repr=False)


def _askable(phone: Phone) -> bool:
    """Whether asking again can help: it has a callsign, and its certificate failed or is still asked for

    A phone that has its certificate is left alone: asked again under the name it holds, it is refused as taken.
    """
    return bool(phone.callsign) and (phone.state in FAILED or phone.state is State.WAITING)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _sentence(text: str) -> str:
    return text if text.endswith(".") else f"{text}."


def _banner(host: str, closes: datetime.datetime | None, now: datetime.datetime) -> Banner:
    """Whether the Deploy App accepts phones, in the operator's time"""
    if closes is None or not host:
        return Banner(
            Level.NONE,
            "On the MDM page of Deploy App, press Open in the MDM connector to let phones enrol.",
            "no enrolment window known",
        )
    at = closes.astimezone(now.tzinfo).strftime("%H:%M")
    if now >= closes:
        return Banner(
            Level.CLOSED,
            f"Enrolment closed at {at}. Open enrolment again on the MDM page of Deploy App, "
            "then press Open in the MDM connector there.",
            f"enrolment closed at {at}",
        )
    if closes - now <= CLOSING:
        left = _count(math.ceil((closes - now).total_seconds() / 60), "minute")
        return Banner(
            Level.CLOSING,
            f"Deploy App {host} accepts phones until {at}, {left} from now",
            f"enrolment open until {at}, {left} left",
        )
    return Banner(Level.OPEN, f"Deploy App {host} accepts phones until {at}", f"enrolment open until {at}")


def _admin(host: str) -> str:
    """Where the Deploy App's admin pages are, its MDM page among them: the mtls. name of the deployment"""
    return f"https://mtls.{host}"


def _read(text: str, now: datetime.datetime, sender: str) -> tuple[Settings, str]:
    """The line's enrolment window and its Deploy App's host

    sender is the origin of the page that sent the line, such as https://mtls.unit.example.fi, or
    empty for a line typed or pasted by hand. A line is taken only from its own Deploy App's pages.
    """
    try:
        window = settings.parse(text, now)
    except settings.SettingsError as exc:
        raise AppError(str(exc)) from exc
    host = urlsplit(window.scep_url).hostname or ""
    if sender and sender not in (f"https://{host}", _admin(host)):
        raise AppError(
            f"These settings are for Deploy App {host}, but the page that sent them is {sender}: "
            "use the button on that Deploy App's own MDM page."
        )
    return window, host


class Redact(logging.Formatter):
    """Formats a log line, tracebacks included, with every secret it was told of blanked out"""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets: set[str] = set()

    def add(self, secret: str) -> None:
        if secret:
            self.secrets.add(secret)

    def scrub(self, text: str) -> str:
        """The text with every secret blanked out"""
        for secret in self.secrets:
            text = text.replace(secret, "[secret]")
        return text

    def format(self, record: logging.LogRecord) -> str:
        return self.scrub(super().format(record))


def open_log(directory: Path, redact: Redact) -> Path:
    """Log to a file in the directory, through redact, and answer its path

    The handlers already there, such as the command line's on stderr, go through redact too.
    """
    for handler in logging.getLogger().handlers:
        handler.setFormatter(redact)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "odmdm-connector.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(redact)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return path


class Worker:
    """Runs jobs one at a time, in order, on one thread of its own; their results wait in a queue"""

    def __init__(self) -> None:
        self._jobs: queue.Queue[Callable[[], Result] | None] = queue.Queue()
        self.results: queue.Queue[Result] = queue.Queue()
        threading.Thread(target=self._run, name="odmdm-connector-mdm", daemon=True).start()

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                if job is None:
                    return
                self.results.put(job())
            finally:
                self._jobs.task_done()

    def submit(self, job: Callable[[], Result]) -> None:
        """job must not raise"""
        self._jobs.put(job)

    def wait(self) -> None:
        """Until every job submitted has finished"""
        self._jobs.join()

    def stop(self) -> None:
        self._jobs.put(None)


def _run(job: Job, work: Callable[[], Result], log_path: Path) -> Result:
    """The work's result, or its failure as one sentence"""
    try:
        return work()
    except MdmError as exc:
        LOGGER.warning("%s failed: %s", job.value, exc)
        return Result(job, error=str(exc), lines=tuple(exc.lines), named=exc.named, unauthorized=exc.unauthorized)
    except OSError:
        LOGGER.exception("%s failed", job.value)
        return Result(job, error="Cannot reach the MDM.")
    except Exception:  # A plugin's bug reaches the operator as a sentence and leaves the worker running
        LOGGER.exception("%s failed", job.value)
        return Result(job, error=f"The MDM plugin failed; the log says more: {log_path}")


def _setup(mdm: Mdm, window: Settings, seen: Callable[[tuple[Step, ...]], None], force: bool = False) -> Result:
    """The MDM's setup, each step shown as it is done, with the steps done before it stopped if it did

    force: the policy is sent again even if unchanged, as a POLICY job.
    """
    job = Job.POLICY if force else Job.SETUP
    steps: list[Step] = []
    try:
        for step in mdm.setup(window, force=force):
            steps.append(step)
            seen(tuple(steps))
    except MdmError as exc:
        LOGGER.warning("%s failed: %s", job.value, exc)
        return Result(job, error=str(exc), steps=tuple(steps), unauthorized=exc.unauthorized)
    return Result(job, steps=tuple(steps))


def _name(mdm: Mdm, wanted: list[tuple[str, str]]) -> Result:
    """Check the names against the phones as they are now, then name them"""
    refused = core.refusal(mdm.phones(), wanted)
    if refused:
        return Result(Job.NAME, error=f"Nothing named: {refused}. Refresh and give callsigns again.")
    return Result(Job.NAME, lines=tuple(mdm.name(dict(wanted))), named=dict(wanted))


class App:
    """The connector for one operator, as the page drives it"""

    def __init__(
        self,
        settings_store: store.Store,
        *,
        installed: Callable[[], list[plugins.Plugin]] = plugins.installed,
        load: Callable[[str], type[Mdm]] = plugins.load,
        redact: Redact | None = None,
        log_path: Path = Path("odmdm-connector.log"),
        keychain: store.Keychain | None = None,
    ) -> None:
        self.store = settings_store
        #: Where a secret is kept between runs when the operator says remember; none without one
        self.keychain = keychain if keychain is not None else store.Keychain(None)
        #: The secrets typed, or taken from the keychain, since the start, by Deploy App, MDM and field
        self._secrets: dict[tuple[str, str, str], str] = {}
        #: Whether the keychain keeps each one, as read once, so that the keychain is asked again only after a
        #: change: a keychain may ask the operator before it answers, and every request would wait for that
        self._kept: dict[tuple[str, str, str], bool] = {}
        self.redact = redact or Redact()
        self.log_path = log_path
        self._installed = installed
        self._load_plugin = load
        self.worker = Worker()
        #: The last thing to tell the operator
        self.status = ""
        #: Every line the MDM handed back for the last naming or asking again, while status is about it
        self.lines: tuple[str, ...] = ()
        try:
            self.saved = settings_store.load()
        except store.StoreError as exc:
            self.saved = store.Saved()
            self.status = str(exc)
        self.mdm: Mdm | None = None
        self.mdm_class: type[Mdm] | None = None
        #: The enrolment window last pasted, challenge and all, in memory only
        self.window: Settings | None = None
        self.phones: list[Phone] = []
        #: Whether the MDM held now has listed its phones, so that none means none
        self._listed = False
        self._passed: dict[str, str] | None = None
        #: The steps the setup under way has done so far, as the worker hands them over one by one
        self._progress: tuple[Step, ...] = ()
        self._pending = dict.fromkeys(Job, 0)
        # Say at once why the MDM set up before is not connected: its secrets went with the last run
        if not self.status:
            self.status = self._take_up()

    @classmethod
    def default(cls) -> "App":
        """The app on this computer: its settings file and log in the connector's own directory, and its keychain"""
        redact = Redact()
        log_path = open_log(store.directory(), redact)
        keychain = store.Keychain(store.platform_backend())
        return cls(store.Store(store.directory()), redact=redact, log_path=log_path, keychain=keychain)

    @property
    def keeps(self) -> bool:
        """Whether a secret can be kept in this computer's keychain"""
        return self.keychain.usable

    def close(self) -> None:
        self.worker.stop()

    def _save(self, changed: store.Saved) -> None:
        """Write the settings, and only then hold them"""
        try:
            self.store.save(changed)
        except store.StoreError as exc:
            raise AppError(str(exc)) from exc
        self.saved = changed

    @property
    def conn(self) -> store.Connection:
        """The active connection, or an empty one when none is active"""
        return self.saved.connections.get(self.saved.active, store.Connection())

    def _save_conn(self, **changes: Any) -> None:
        """Write the active connection with the changes"""
        if not self.saved.active:
            raise AppError("Add the Deploy App first: on its MDM page, press Open in the MDM connector.")
        connections = {**self.saved.connections, self.saved.active: replace(self.conn, **changes)}
        self._save(replace(self.saved, connections=connections))

    def _drop(self) -> None:
        """Let go of what is held for the active connection: its MDM, its enrolment window and its phones"""
        self.mdm = self.mdm_class = self.window = self._passed = None
        self.phones, self._listed, self._progress = [], False, ()

    def _say(self, sentence: str, *lines: str) -> str:
        """Say the sentence, with lines under it; what was said, the lines first"""
        self.status, self.lines = sentence, lines
        return " ".join([*lines, sentence])

    # The MDM

    def mdms(self) -> list[plugins.Plugin]:
        """The MDMs installed, none of them imported"""
        try:
            return self._installed()
        except plugins.PluginError as exc:
            raise AppError(str(exc)) from exc

    def _plugin(self, name: str) -> plugins.Plugin:
        for plugin in self.mdms():
            if plugin.name == name:
                return plugin
        raise AppError(f"No MDM named {name} is installed.")

    def approval(self, name: str) -> str:
        """What to ask before the MDM's plugin is used; empty when it is built in or approved as it is"""
        plugin = self._plugin(name)
        if plugin.built_in or f"{plugin.name} {plugin.package} {plugin.version}" in self.saved.approved:
            return ""
        return (
            f"The MDM {name} comes from the package {plugin.package} {plugin.version}, not with this app. "
            "A plugin is code that sees the MDM token: use it only if you trust where it comes from."
        )

    def approve(self, name: str) -> None:
        plugin = self._plugin(name)
        others = [each for each in self.saved.approved if each.split()[0] != plugin.name]
        self._save(replace(self.saved, approved=[*others, f"{plugin.name} {plugin.package} {plugin.version}"]))
        LOGGER.info("Plugin approved: %s %s %s", plugin.name, plugin.package, plugin.version)

    def _class(self, name: str) -> type[Mdm]:
        """The MDM's class, importing its plugin and no other, once it is approved"""
        if self.approval(name):
            raise AppError(f"The MDM {name} is a plugin that has not been approved in this app.")
        try:
            return self._load_plugin(name)
        except plugins.PluginError as exc:
            raise AppError(str(exc)) from exc

    @staticmethod
    def _account(host: str, name: str, item: Field) -> tuple[str, str, str]:
        """Whose secret it is: per Deploy App, so that two never share a token by accident"""
        return host, name, item.key

    def _look(self, account: tuple[str, str, str]) -> None:
        """Read the account's secret from the keychain, once, and hold it if none is held"""
        if account in self._kept:
            return
        kept = self.keychain.get(":".join(account))
        self._kept[account] = bool(kept)
        if kept and not self._secrets.get(account):
            self._secrets[account] = kept

    def _secret(self, name: str, item: Field) -> str:
        """The active Deploy App's secret: as held since the start, else as kept in the keychain"""
        account = self._account(self.saved.active, name, item)
        self._look(account)
        return self._secrets.get(account, "")

    def _keep(self, name: str, chosen: type[Mdm], remember: bool) -> list[str]:
        """Keep the active Deploy App's secrets in the keychain if remember, else remove any kept

        What to say of a secret the keychain did not take.
        """
        notices = []
        for item in chosen.fields:
            if not item.secret:
                continue
            account = self._account(self.saved.active, name, item)
            secret = self._secrets.get(account, "")
            self._kept.pop(account, None)
            if not (remember and secret and self.keeps):
                self.keychain.forget(":".join(account))
            elif not self.keychain.keep(":".join(account), secret):
                notices.append(
                    f"This computer's keychain cannot be used, so the {item.label} is kept only until the connector "
                    "stops."
                )
        return notices

    def _forget(self, host: str, name: str) -> bool:
        """Let go of the secrets of the Deploy App's MDM, held and kept; whether the keychain holds none of them"""
        self._secrets = {account: secret for account, secret in self._secrets.items() if account[0] != host}
        self._kept = {account: kept for account, kept in self._kept.items() if account[0] != host}
        if not name or not self.keeps:
            return True
        try:
            chosen = self._class(name)
        except AppError:
            return False
        accounts = [":".join(self._account(host, name, item)) for item in chosen.fields if item.secret]
        left = [account for account in accounts if not self.keychain.forget(account)]
        return not left

    def _default(self, item: Field) -> str:
        """The field's default for the active Deploy App, the deployment's name filled in"""
        return item.default_for(self.saved.active)

    def form(self, name: str) -> Form:
        """The MDM's fields with their values: saved ones if it is the active connection's MDM, else the defaults

        Each field's default is the active Deploy App's.
        """
        chosen = self._class(name)
        saved = self.conn.values if self.conn.mdm == name else {}
        fields = [replace(item, default=self._default(item)) for item in chosen.fields]
        return Form(
            name,
            chosen.title,
            tuple(
                FieldValue(item, self._secret(name, item), self._remembered(name, item))
                if item.secret
                else FieldValue(item, saved.get(item.key, item.default))
                for item in fields
            ),
        )

    def _remembered(self, name: str, item: Field) -> bool:
        """Whether the active Deploy App's secret is kept in the keychain"""
        account = self._account(self.saved.active, name, item)
        self._look(account)
        return self.keeps and self._kept[account]

    def _make(self, name: str, chosen: type[Mdm]) -> Mdm:
        """The MDM, made from the saved values and the secrets, and held"""
        values = {}
        for item in chosen.fields:
            values[item.key] = (
                self._secret(name, item) if item.secret else self.conn.values.get(item.key, self._default(item))
            )
            if item.secret:
                self.redact.add(values[item.key])
        try:
            mdm = chosen(values)
        except MdmError as exc:
            raise AppError(str(exc)) from exc
        self.mdm, self.mdm_class, self._listed = mdm, chosen, False
        return mdm

    def _take_up(self) -> str:
        """Take up the active connection's MDM, if still approved and its secrets are at hand; why not, or empty"""
        name = self.conn.mdm
        if not name:
            return ""
        try:
            chosen = self._class(name)
            lacking = [item.label for item in chosen.fields if item.secret and not self._secret(name, item)]
            if lacking:
                kept = "it is" if len(lacking) == 1 else "they are"
                if self.keeps:
                    return f"Type the {' and '.join(lacking)} again to connect: {kept} not kept on this computer."
                return f"Type the {' and '.join(lacking)} again to connect: {kept} kept only while the connector runs."
            self._make(name, chosen)
        except AppError as exc:
            return str(exc)
        return ""

    def connect(self, name: str, values: Mapping[str, str], remember: bool = True) -> str:
        """Use this MDM with these values: secrets held in memory, the rest in the settings file

        A secret is kept in the keychain too if remember and one can be used; not remembered, any
        copy kept there is removed. A secret given empty stands for the one held or kept for this
        Deploy App and MDM only when every other value given is the one saved for it, so that a kept
        secret never goes to an address it was not kept for.

        With an enrolment window held, its setup runs on the worker and its Result carries the steps.
        Without one, an MDM already set up for the Deploy App is only connected, so its phones can be
        seen on any day; one not set up yet is refused, as setup needs the window.
        """
        chosen = self._class(name)
        if not self.saved.active:
            raise AppError("Add the Deploy App first: on its MDM page, press Open in the MDM connector.")
        if self.window is None and self.conn.mdm != name:
            raise AppError(
                f"To set {chosen.title} up, open enrolment on the MDM page of Deploy App {self.saved.active}, "
                "then press Open in the MDM connector there."
            )
        plain = {item.key: values.get(item.key, self._default(item)) for item in chosen.fields if not item.secret}
        saved = {
            item.key: self.conn.values.get(item.key, self._default(item)) for item in chosen.fields if not item.secret
        }
        same = self.conn.mdm == name and plain == saved
        given = dict(plain)
        for item in chosen.fields:
            if not item.secret:
                continue
            given[item.key] = values.get(item.key, "")
            if not given[item.key].isprintable():
                raise AppError(
                    f"The {item.label} holds a line break or another character that cannot be part of it: copy it again."
                )
            if not given[item.key] and self._secret(name, item):
                if not same:
                    raise AppError(f"Type the {item.label} again: the settings changed.")
                given[item.key] = self._secret(name, item)
        for item in chosen.fields:
            if item.required and not given[item.key].strip():
                raise AppError(f"Fill in {item.label}.")
        self._save_conn(mdm=name, values=plain)
        for item in chosen.fields:
            if item.secret:
                self._secrets[self._account(self.saved.active, name, item)] = given[item.key]
        mdm, window = self._make(name, chosen), self.window
        notices = self._keep(name, chosen, remember)
        if window is None:
            LOGGER.info("Connected to the MDM %s for Deploy App %s", name, self.saved.active)
            return self._say(f"Connected to {chosen.title} for Deploy App {self.saved.active}.", *notices)
        LOGGER.info("Connecting to the MDM %s for Deploy App %s", name, self.saved.active)
        if SETUP in chosen.missing:
            self._submit(Job.SETUP, lambda: Result(Job.SETUP, steps=(Step(chosen.missing[SETUP], False),)))
        else:
            self._progress = ()
            self._submit(Job.SETUP, lambda: _setup(mdm, window, self._show))
        return self._say(f"Setting {chosen.title} up for Deploy App {self.saved.active}.", *notices)

    @property
    def connected(self) -> bool:
        return self.mdm is not None

    def policy_note(self) -> str:
        """What sending the policy again does, while the MDM connected can; empty when it cannot"""
        if self.mdm is None or SETUP in self.mdm.missing or SEND_POLICY in self.mdm.missing:
            return ""
        return (
            "For a phone whose browser refuses its certificate: the MDM sends the policy to every phone again. "
            "Phones keep their certificates. Not while phones are still installing apps: it stops their installs."
        )

    def send_policy(self) -> str:
        """Set the MDM up again with its policy sent even if unchanged, on the worker, as apply --force-policy does

        For a phone whose policy the MDM overwrote. No challenge goes with it, so a window pasted
        earlier never takes the place of a newer one, and enrolment need not be open. One at a
        time: asked again while it or a setup waits or runs, nothing more is sent, as every send
        rewrites the policy and a policy rewritten again and again stops the phones' installs.
        """
        if self.mdm is None:
            raise AppError("Connect to the MDM first.")
        for capability in (SETUP, SEND_POLICY):
            if capability in self.mdm.missing:
                raise AppError(self.mdm.missing[capability])
        said = f"Sending the policy again for Deploy App {self.saved.active}."
        if self._pending[Job.POLICY] or self._pending[Job.SETUP]:
            return self._say(said)
        mdm = self.mdm
        window = Settings(self.window.scep_url if self.window else f"https://{self.saved.active}/scep", "")
        self._progress = ()
        self._submit(Job.POLICY, lambda: _setup(mdm, window, self._show, force=True))
        return self._say(said)

    # The Deploy Apps

    def connections(self, now: datetime.datetime) -> list[DeployApp]:
        """The Deploy Apps saved, in the order they were added"""
        return [
            DeployApp(
                host,
                self.banner(now) if host == self.saved.active else _banner(host, store.closes(each), now),
                host == self.saved.active,
            )
            for host, each in self.saved.connections.items()
        ]

    def read_settings(self, text: str, now: datetime.datetime, sender: str = "") -> Reading:
        """What the settings line says, taking nothing from it; sender as for paste_settings"""
        window, host = _read(text, now, sender)
        return Reading(host, _sentence(_banner(host, window.closes, now).text), host in self.saved.connections)

    def _activate(self, host: str, connection: store.Connection) -> bool:
        """Save the connection and make it the active one; whether that changed which one is"""
        changed = host != self.saved.active
        self._save(replace(self.saved, connections={**self.saved.connections, host: connection}, active=host))
        if changed:
            self._drop()
            LOGGER.info("Working with Deploy App %s", host)
        return changed

    def open(self, host: str, now: datetime.datetime) -> str:
        """Make the saved connection to the Deploy App the active one, and take up its MDM"""
        if host not in self.saved.connections:
            raise AppError(f"There is no Deploy App {host} in this app.")
        changed = self._activate(host, self.saved.connections[host])
        failed = self._take_up() if changed or self.mdm is None else ""
        return self._say(" ".join(filter(None, [_sentence(self.banner(now).text), failed])))

    def remove(self, host: str) -> str:
        """Delete the connection to the Deploy App and its secrets, held and kept; if it was the active one, none is"""
        gone = self.saved.connections.get(host)
        if gone is None:
            raise AppError(f"There is no Deploy App {host} in this app.")
        active = host == self.saved.active
        connections = {each: kept for each, kept in self.saved.connections.items() if each != host}
        self._save(replace(self.saved, connections=connections, active="" if active else self.saved.active))
        if active:
            self._drop()
        forgotten = self._forget(host, gone.mdm)
        LOGGER.info("Deploy App %s removed", host)
        if not gone.mdm:
            return self._say(f"Deploy App {host} is removed from this app.")
        if not forgotten:
            return self._say(
                f"Deploy App {host} is removed from this app, but its MDM token may still be in this computer's "
                f"keychain: delete the odmdm-connector item of {host} there by hand."
            )
        return self._say(f"Deploy App {host} is removed from this app, and its MDM token from this computer.")

    # The enrolment window

    def paste_settings(self, text: str, now: datetime.datetime, sender: str = "") -> str:
        """Take the settings line Deploy App's MDM page copied, and give the MDM its challenge

        A line for a Deploy App not saved yet adds a connection to it. Its Deploy App becomes the
        active one. An MDM that cannot take a challenge by API gets none: challenge_note then says
        what to do. sender is the origin of the page that sent the line, empty for one typed or
        pasted by hand; a line sent from a page other than its own Deploy App's is refused.
        """
        window, host = _read(text, now, sender)
        closes = window.closes.isoformat() if window.closes else ""
        connection = replace(self.saved.connections.get(host, store.Connection()), closes=closes)
        changed = self._activate(host, connection)
        failed = self._take_up() if changed or self.mdm is None else ""
        self.redact.add(window.challenge)
        self.window = window
        LOGGER.info("Settings pasted for Deploy App %s, open until %s", host, closes)
        until = self.banner(now).text
        if self.mdm is None or self.challenge_note():
            return self._say(" ".join(filter(None, [f"{until}.", failed])))
        mdm = self.mdm

        def give() -> Result:
            mdm.set_challenge(window)
            return Result(Job.CHALLENGE)

        self._submit(Job.CHALLENGE, give)
        return self._say(f"{until}. Giving the MDM the new challenge.")

    def challenge_note(self) -> str:
        """The plugin's sentence for an MDM that cannot take the challenge by API, when one is held"""
        if self.window is None or self.mdm_class is None:
            return ""
        return self.mdm_class.missing.get(SET_CHALLENGE, "")

    def challenge(self) -> str:
        """The challenge, only for an MDM that has to be given it by hand"""
        return self.window.challenge if self.window and self.challenge_note() else ""

    def banner(self, now: datetime.datetime) -> Banner:
        """Whether the active Deploy App accepts phones, in the operator's time"""
        closes = self.window.closes if self.window else store.closes(self.conn)
        return _banner(self.saved.active, closes, now)

    @property
    def mdm_page(self) -> str:
        """The active Deploy App's MDM page, where enrolment is opened; empty while none is active"""
        return f"{_admin(self.saved.active)}/mdm" if self.saved.active else ""

    # The phones

    def refresh(self) -> None:
        """List the phones again on the worker, unless a listing is under way"""
        if self.mdm is None or self._pending[Job.PHONES]:
            return
        mdm = self.mdm
        self._submit(Job.PHONES, lambda: Result(Job.PHONES, phones=tuple(mdm.phones())))

    @staticmethod
    def _joined(phone: Phone, now: datetime.datetime) -> tuple[str, datetime.datetime | None]:
        """When it joined, in the operator's time, and as a time if it is one"""
        try:
            when = datetime.datetime.fromisoformat(phone.joined)
        except ValueError:
            return phone.joined, None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.UTC)
        return when.astimezone(now.tzinfo).strftime("%Y-%m-%d %H:%M"), when

    def rows(self, now: datetime.datetime) -> list[Row]:
        """The table, oldest first as the MDM lists them"""
        return [
            Row(
                phone.id,
                phone.callsign,
                phone.model,
                self._joined(phone, now)[0],
                core.said(phone),
                not phone.callsign,
                phone.state in FAILED,
                phone.state is State.READY,
                self._askable(phone),
            )
            for phone in self.phones
        ]

    def _askable(self, phone: Phone) -> bool:
        """Whether asking the phone again can help, with an MDM that can ask"""
        return bool(self.mdm and ASK_AGAIN not in self.mdm.missing) and _askable(phone)

    def summary(self) -> str:
        """How many phones there are and where they are; empty while no MDM is connected or none is listed yet"""
        if self.mdm is None:
            return ""
        if not self.phones:
            return "No phones yet." if self._listed else ""
        named = [phone for phone in self.phones if phone.callsign]
        ready = sum(phone.state is State.READY for phone in named)
        failed = sum(phone.state in FAILED for phone in named)
        parts = [
            (ready, "ready"),
            (len(named) - ready - failed, "waiting for the phone"),
            (failed, "failed"),
            (len(self.phones) - len(named), "without a name"),
        ]
        return f"{_count(len(self.phones), 'phone')}: " + ", ".join(f"{n} {word}" for n, word in parts if n)

    def detail(self, phone_id: str, now: datetime.datetime) -> Detail:
        phone = next((each for each in self.phones if each.id == phone_id), None)
        if phone is None:
            raise AppError(f"Phone {phone_id} is no longer in the MDM's list: refresh.")
        return Detail(
            phone.id,
            phone.callsign,
            phone.model,
            self._joined(phone, now)[0],
            phone.state.value,
            phone.detail,
            self.banner(now).text,
            self._askable(phone),
        )

    # Naming

    def targets(self, selected: Sequence[str]) -> list[str]:
        """The phones to name, in table order: the selected ones, or with none, every one without a name"""
        chosen = set(selected)
        return [phone.id for phone in self.phones if (phone.id in chosen if chosen else not phone.callsign)]

    def first_number(self, prefix: str) -> int:
        """The number to start from: one above the highest prefix<n> in use"""
        return core.next_number(self.phones, prefix)

    @staticmethod
    def numbered(prefix: str, start: int, count: int) -> list[str]:
        return [f"{prefix}{start + n}" for n in range(count)]

    @staticmethod
    def listed(text: str) -> list[str]:
        """A list of callsigns as typed, one per line"""
        return [line.strip() for line in text.splitlines() if line.strip()]

    def keep_callsigns(self, prefix: str, callsigns: Sequence[str]) -> None:
        """Keep the prefix and the list for the active Deploy App next time, also before any phone has joined"""
        self._save_conn(prefix=prefix, callsigns=list(callsigns))

    def preview(self, ids: Sequence[str], callsigns: Sequence[str], now: datetime.datetime) -> Preview:
        """What naming the phones with the callsigns, in order, would do, by the naming rules

        Only a preview that passed can then be named.
        """
        found = {phone.id: phone for phone in self.phones}
        rows: list[PreviewRow] = []
        fine: list[tuple[str, str]] = []
        for index, phone_id in enumerate(ids):
            callsign = callsigns[index] if index < len(callsigns) else ""
            phone = found.get(phone_id)
            if not callsign:
                refused = "no callsign left in the list"
            else:
                refused = core.refusal(self.phones, [(phone_id, callsign)]) or core.refusal(
                    self.phones, [*fine, (phone_id, callsign)]
                )
            if not refused:
                fine.append((phone_id, callsign))
            joined = self._joined(phone, now)[0] if phone else ""
            rows.append(PreviewRow(phone_id, phone.model if phone else "", joined, callsign, refused))
        refusal = ""
        if not ids:
            refusal = "No phone is waiting for a name. To give a phone another callsign, select it first."
        elif len(callsigns) < len(ids):
            refusal = f"{_count(len(ids), 'phone')} to name, but only {_count(len(callsigns), 'callsign')}."
        recent = [phone for phone in self.phones if self._recent(phone, now)]
        warning = ""
        if len(recent) == 1:
            warning = (
                "A phone joined less than 10 minutes ago and may still be installing its apps. Naming now sends "
                "the policy to every phone again, which stops apps that are still installing: wait until it has "
                "its apps."
            )
        elif recent:
            warning = (
                f"{len(recent)} phones joined less than 10 minutes ago and may still be installing their apps. "
                "Naming now sends the policy to every phone again, which stops apps that are still installing: "
                "wait until they have their apps."
            )
        result = Preview(tuple(rows), refusal, warning)
        self._passed = result.names if result.ok else None
        return result

    def _recent(self, phone: Phone, now: datetime.datetime) -> bool:
        when = self._joined(phone, now)[1]
        return when is not None and now - when < RECENT

    def name(self, names: Mapping[str, str]) -> str:
        """Name the phones of the preview that passed last, on the worker"""
        if self.mdm is None:
            raise AppError("Connect to the MDM first.")
        if self._passed is None or dict(names) != self._passed:
            raise AppError("Preview the callsigns first: nothing is named without a preview that passed.")
        self._passed = None
        mdm, wanted = self.mdm, list(names.items())
        self._submit(Job.NAME, lambda: _name(mdm, wanted))
        return self._say(f"Naming {_count(len(wanted), 'phone')}.")

    def ask_again(self, ids: Sequence[str]) -> str:
        """Have the phones ask for their certificates again; no ids: every named one that failed"""
        if self.mdm is None:
            raise AppError("Connect to the MDM first.")
        if ASK_AGAIN in self.mdm.missing:
            raise AppError(self.mdm.missing[ASK_AGAIN])
        if not ids:
            mdm = self.mdm
            self._submit(Job.ASK_AGAIN, lambda: Result(Job.ASK_AGAIN, lines=tuple(mdm.ask_again([]))))
            return self._say("Asking every named phone whose certificate failed again.")
        wanted = set(ids)
        chosen = [phone.id for phone in self.phones if phone.id in wanted and _askable(phone)]
        if not chosen:
            raise AppError(
                "Nothing to ask again: the selected phones have their certificates, or have no callsign yet."
            )
        mdm, left = self.mdm, len(wanted) - len(chosen)
        self._submit(Job.ASK_AGAIN, lambda: Result(Job.ASK_AGAIN, lines=tuple(mdm.ask_again(chosen))))
        if left:
            return self._say(f"Asking {_count(len(chosen), 'phone')} again; {_count(left, 'phone')} left alone.")
        return self._say("Asking again.")

    # The worker

    def _submit(self, job: Job, work: Callable[[], Result]) -> None:
        self._pending[job] += 1
        log_path, host, by = self.log_path, self.saved.active, self.mdm
        self.worker.submit(lambda: replace(_run(job, work, log_path), host=host, by=by))

    def busy(self, job: Job | None = None) -> bool:
        """Whether a job, or that kind of job, is under way"""
        return bool(self._pending[job]) if job else any(self._pending.values())

    def drain(self) -> list[Result]:
        """The results finished since the last drain, taken in; called on the view's timer"""
        done: list[Result] = []
        while True:
            try:
                result = self.worker.results.get_nowait()
            except queue.Empty:
                return done
            self._pending[result.job] -= 1
            result = self._scrubbed(result)
            self._take(result)
            done.append(result)

    def _show(self, steps: tuple[Step, ...]) -> None:
        """Called on the worker: the steps the setup has done so far"""
        self._progress = steps

    @property
    def progress(self) -> tuple[Step, ...]:
        """The steps the setup under way has done so far, for a view to tick them as they come"""
        return self._clean(self._progress)

    def _clean(self, steps: tuple[Step, ...]) -> tuple[Step, ...]:
        scrub = self.redact.scrub
        return tuple(replace(step, label=scrub(step.label), message=scrub(step.message)) for step in steps)

    def _scrubbed(self, result: Result) -> Result:
        """The result with every secret the app was told of blanked out, as an MDM's answer may echo one"""
        scrub = self.redact.scrub
        return replace(
            result,
            error=scrub(result.error),
            lines=tuple(scrub(line) for line in result.lines),
            steps=self._clean(result.steps),
        )

    def _take(self, result: Result) -> None:
        """What a result changes in the app, and what the status line says of it

        A result for a Deploy App no longer active changes nothing the app holds, and is still said.
        """
        for line in result.lines:
            LOGGER.info("%s: %s", result.job.value, line)
        current = result.host == self.saved.active
        if current and result.unauthorized and result.by is self.mdm is not None:
            # The secret is not accepted, so it is let go of, kept copy and all, and the MDM with it, until typed again
            self._forget(result.host, self.conn.mdm)
            self.mdm, self.phones, self._listed = None, [], False
        if current and result.phones is not None and self.mdm is not None:
            self.phones, self._listed = list(result.phones), True
        if current and result.named and self.conn.callsigns:
            used = {callsign.lower() for callsign in result.named.values()}
            try:
                self._save_conn(callsigns=[each for each in self.conn.callsigns if each.lower() not in used])
            except AppError as exc:
                LOGGER.warning("%s", exc)
        if current and result.job in (Job.NAME, Job.ASK_AGAIN):
            self.refresh()
        title = self.mdm_class.title if self.mdm_class else "The MDM"
        if result.error:
            stopped = ""
            if result.named:
                done = ", ".join(f"{phone} as {callsign}" for phone, callsign in result.named.items())
                stopped = f" Named before it stopped: {done}."
            self._say(result.error + stopped)
        elif result.job is Job.SETUP and (any(not step.done for step in result.steps) or self.challenge_note()):
            self._say(f"Do the steps by hand to finish setting {title} up for Deploy App {result.host}.")
        elif result.job is Job.SETUP:
            self._say(f"{title} is set up for Deploy App {result.host}.")
        elif result.job is Job.POLICY:
            self._say(f"{title} has sent the policy again to the phones of Deploy App {result.host}.")
        elif result.job is Job.CHALLENGE:
            self._say(f"{title} has the new challenge.")
        elif result.job is Job.NAME:
            self._say(f"{_count(len(result.named), 'phone')} named.")
        elif result.job is Job.ASK_AGAIN:
            self._say("Asked again." if result.lines else "No phone needed asking again.")
        if result.job in (Job.NAME, Job.ASK_AGAIN):
            self.lines = result.lines
