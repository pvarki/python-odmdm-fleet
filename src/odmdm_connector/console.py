"""CLI entrypoints for odmdm-connector

Knows the MDM only through the contract in mdm.py, and gets it only through plugins.load: the
chosen MDM is the one entry point of the group odmdm_connector.mdm named by --mdm, Fleet being the
one built in.
"""

import argparse
import codecs
import datetime
import getpass
import inspect
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlparse

from odmdm_connector import __version__

from . import browser, core, plugins, settings, store, web
from .app import App
from .mdm import ASK_AGAIN, SET_CHALLENGE, SETUP, Field, Mdm, MdmError
from .settings import Settings

#: Said when ODMDM_LOG_LEVEL is not a number
LOG_LEVEL = "ODMDM_LOG_LEVEL must be a whole number, such as 10 for debug or 20 for info: change it, or unset it."
#: Said before the page's address, when a browser was asked to open it and when not
OPENING = "The connector's page is opening in your browser. If it does not, open this address:"
OPEN = "Open this address in your browser:"
#: Said when Ctrl+C stops the page while a job is under way
STOPPED_BUSY = "Stopped while the MDM was still being worked with: refresh the phones when the connector runs again."


class _Usage(Exception):
    """A command line the connector cannot act on: said with the command's usage, exit 2"""


class _Failed(Exception):
    """A sentence on stderr, exit 1"""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and answer its exit code; a usage error exits 2 through argparse"""
    args = _parser().parse_args(argv)
    try:
        level = int(os.environ.get("ODMDM_LOG_LEVEL", "20"))
    except ValueError:
        print(LOG_LEVEL, file=sys.stderr)
        return 1
    if args.verbose == 1:
        level = logging.INFO
    if args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().setLevel(level)
    run: Callable[[argparse.Namespace], int] = args.run
    try:
        for value in args.given:
            key, equals, _ = value.partition("=")
            if not key or not equals:
                raise _Usage("--set takes KEY=VALUE")
        return run(args)
    except _Usage as exc:
        args.parser.error(str(exc))
    except _Failed as exc:
        print(exc, file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("Stopped before the command finished.", file=sys.stderr)
    return 1


def odmdm_connector_cli() -> None:
    """CLI entrypoint"""
    sys.exit(main())


def _chosen(name: str) -> type[Mdm]:
    """The class of the MDM of that name, saying first whose code it is when it is not built in"""
    try:
        plugin = plugins.find(name)
        if not plugin.built_in:
            print(
                f"The MDM {name} is a plugin from {plugin.package} {plugin.version}: code that sees the MDM token",
                file=sys.stderr,
            )
        return plugins.load(name)
    except plugins.NotInstalled as exc:
        raise _Usage(str(exc)) from exc
    except plugins.PluginError as exc:
        raise _Failed(str(exc)) from exc


def _values(name: str, fields: tuple[Field, ...], given: Mapping[str, str]) -> dict[str, str]:
    """The fields' values: from --set, else ODMDM_<KEY>, else the default

    A secret only ever from the file ODMDM_<KEY>_FILE names, so it stays out of shell history and
    process environments. No value is ever in a message.
    """
    keys = sorted(item.key for item in fields)
    unknown = sorted(set(given) - set(keys))
    if unknown:
        raise _Usage(f"{name} has no setting {unknown[0]}; it has {', '.join(keys)}")
    values: dict[str, str] = {}
    for item in fields:
        env = f"ODMDM_{item.key.upper()}"
        if item.secret:
            if item.key in given:
                raise _Usage(f"{item.key} is a secret: put it in a file and set {env}_FILE to that file")
            path = os.environ.get(f"{env}_FILE", "")
            values[item.key] = _secret_file(Path(path), item.label) if path and Path(path).is_file() else ""
            if not values[item.key].isprintable():
                raise _Failed(
                    f"The {item.label} holds a line break or another character that cannot be part of it: copy it again."
                )
        else:
            values[item.key] = given[item.key] if item.key in given else os.environ.get(env, item.default)
    return values


def _secret_file(path: Path, label: str) -> str:
    """A secret's file as text: UTF-16 by its byte order mark, as Windows PowerShell 5.1 writes, else UTF-8

    Never the bytes in a message: a decoding error quotes them.
    """
    try:
        data = path.read_bytes()
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return data.decode("utf-16").strip()
        return data.decode("utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        raise _Failed(f"The file {path} cannot be read as plain text: save the {label} in it as plain text.") from None


def _missing(fields: tuple[Field, ...], values: Mapping[str, str]) -> str:
    """How to give the first required setting that is empty; empty when none is"""
    for item in fields:
        if item.required and not values[item.key]:
            env = f"ODMDM_{item.key.upper()}"
            if item.secret:
                return f"{item.label} is missing: put it in a file and set {env}_FILE to that file"
            return f"{item.label} is missing: give --set {item.key}=... or set {env}"
    return ""


def _mdm(args: argparse.Namespace, capability: str = "", complete: bool = True) -> Mdm:
    """The chosen MDM, configured; or the MDM's sentence for lacking the capability

    The command's own options such as --team are each the same as --set team=...
    complete: every required setting must be given, which a dry run does not need.
    """
    given = {key: setting for key, _, setting in (value.partition("=") for value in args.given)}
    chosen = _chosen(args.chosen)
    if capability in chosen.missing:
        raise _Failed(chosen.missing[capability])
    for key in ("mdm_url", "team", "template"):
        if getattr(args, key, None) is not None:
            given[key] = getattr(args, key)
    values = _values(args.chosen, chosen.fields, given)
    missing = _missing(chosen.fields, values) if complete else ""
    if missing:
        raise _Failed(missing)
    try:
        return chosen(values)
    except MdmError as exc:
        raise _Failed(str(exc)) from exc


def _domain(args: argparse.Namespace) -> str:
    the_domain: str = args.domain or os.environ.get("ODMDM_DOMAIN", "")
    if not the_domain:
        raise _Failed("Give --domain or set ODMDM_DOMAIN")
    return the_domain


def _now() -> datetime.datetime:
    """Now, in this computer's time zone, which the times said are in"""
    return datetime.datetime.now().astimezone()


def _window(value: str, domain: str) -> Settings:
    """The enrolment window, from a settings line or a bare challenge, or why not

    A bare challenge says nothing of when the window closes. The challenge is never in a message.
    """
    if not settings.is_line(value):
        return Settings(f"https://{domain}/scep", value.strip())
    try:
        line = settings.parse(value, _now())
    except settings.SettingsError as exc:
        raise _Failed(str(exc)) from exc
    host = urlparse(line.scep_url).hostname or ""
    if host != domain.lower():
        raise _Failed(f"The settings line is for {host}, not {domain}: copy it from this deployment's MDM page")
    return line


def apply(args: argparse.Namespace) -> int:
    """Tell the MDM what this deployment's devices need

    Run it when a deployment is set up and whenever its template changes. A changed policy stops
    apps installing on phones that are enrolling at that moment, so a new enrolment window is
    `challenge`, not this.

    Order matters and is handled for you. Apps install at ENROLMENT and at no other time, so a
    device that has already joined will not pick up a template applied afterwards -- it has to
    enrol again, under a callsign that has not been spent.
    """
    the_domain = _domain(args)
    if args.dry_run:
        window = Settings(f"https://{the_domain}/scep", "")
    else:
        window = _window(os.environ.get("ODMDM_CHALLENGE", ""), the_domain)
    mdm = _mdm(args, SETUP, complete=not args.dry_run)
    try:
        for step in mdm.setup(window, force=args.force_policy, dry_run=args.dry_run):
            print(step.label if step.done else f"{step.label} (not done)")
            if step.message:
                print(step.message)
    except MdmError as exc:
        raise _Failed(str(exc)) from exc
    return 0


def challenge(args: argparse.Namespace) -> int:
    """Give the MDM the challenge of the enrolment window just opened

    Every opening makes a new one. Only the certificate authority changes, so phones still
    installing apps are left alone. Taken from ODMDM_CHALLENGE, else asked for, never as an
    argument, so it stays out of shell history and the process list. Given the whole settings line,
    its SCEP URL must be on this deployment's domain.
    """
    the_domain = _domain(args)
    mdm = _mdm(args, SET_CHALLENGE)
    value = os.environ.get("ODMDM_CHALLENGE", "")
    while not value.strip():
        value = getpass.getpass("Settings line or challenge from Deploy App's MDM page: ")
    window = _window(value, the_domain)
    try:
        mdm.set_challenge(window)
    except MdmError as exc:
        raise _Failed(str(exc)) from exc
    until = ""
    if window.closes:
        until = f". The enrolment window is open until {window.closes.astimezone(_now().tzinfo):%Y-%m-%d %H:%M}"
    print(f"{mdm.title} has the new challenge{until}")
    return 0


def phones(args: argparse.Namespace) -> int:
    """List the team's phones and where each one's certificate is

    One line each: the id that name and resend take, the callsign, the model, the day it
    enrolled, and the certificate in plain words.
    """
    mdm = _mdm(args)
    try:
        found = mdm.phones()
    except MdmError as exc:
        raise _Failed(str(exc)) from exc
    for phone in found:
        print(f"{phone.id:<5} {phone.callsign or '-':<12} {phone.model:<26} {phone.joined[:10]}  {core.said(phone)}")
    if not found:
        print("No phones in the team")
    return 0


def name(args: argparse.Namespace) -> int:
    """Name phones, and have the MDM send them certificates

    ID=CALLSIGN for as many phones as you like, in any order, the id being what phones prints. Or
    --all PREFIX, which numbers every phone that has no name yet. Each phone gets its certificate
    at its next check-in, within about 15 minutes. A phone that already has its certificate under
    the name is left alone.

    Nothing is changed if a name is not a callsign, is given twice or belongs to another phone in
    the team, or if a phone is not in the team.

    With Fleet, the team's policy is sent again, to every phone in it, if a phone enrolled after it
    was last sent, because Fleet can overwrite a new phone's policy. So name phones once every phone in the
    team has its apps.
    """
    if bool(args.pairs) == bool(args.prefix) or (args.start is not None and not args.prefix):
        raise _Usage("Give ID=CALLSIGN for each phone, or --all PREFIX")
    try:
        wanted = core.pairs(args.pairs)
    except ValueError as exc:
        raise _Usage(str(exc)) from exc
    mdm = _mdm(args)
    try:
        found = mdm.phones()
        if args.prefix:
            wanted = core.numbered(found, args.prefix, args.start)
        refused = core.refusal(found, wanted)
        if refused:
            raise _Failed(f"Nothing named: {refused}")
        if not wanted:
            print("Nothing to name")
            return 0
        lines = mdm.name(dict(wanted))
    except MdmError as exc:
        for line in exc.lines:
            print(line)
        print(exc, file=sys.stderr)
        if exc.named:
            done = " ".join(f"{phone}={callsign}" for phone, callsign in exc.named.items())
            print(f"Already named before it stopped: {done}. phones shows where each is", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


def resend(args: argparse.Namespace) -> int:
    """Have phones ask for their certificates again

    For a refused phone once the cause is fixed, such as enrolment opened again and its new
    challenge given to the MDM. A taken name is not one of those: Deploy App never frees a name, so
    give the phone another with name, which sends the request itself. Without ids, every phone
    that has a name and whose certificate failed. Each acts on it at its next check-in, within
    about 15 minutes.

    An id is asked whatever its state. A phone that has its certificate asks again under the
    name it holds and is refused, and reads as refused from then on though that certificate still
    works. As with name, the team's policy is sent again if a phone enrolled after it was last sent.
    """
    mdm = _mdm(args, ASK_AGAIN)
    try:
        lines = mdm.ask_again(list(args.ids))
    except MdmError as exc:
        for line in exc.lines:
            print(line)
        raise _Failed(str(exc)) from exc
    for line in lines:
        print(line)
    if not lines:
        print("Nothing to resend")
    return 0


def mdms(args: argparse.Namespace) -> int:
    """List the MDMs installed: the name --mdm takes, the package and its version, built in or a plugin

    Lists them without loading any. A plugin is code that sees the MDM token: install only one you trust.
    """
    try:
        found = plugins.installed()
    except plugins.PluginError as exc:
        raise _Failed(str(exc)) from exc
    for plugin in found:
        kind = "built in" if plugin.built_in else "plugin"
        print(f"{plugin.name:<12} {plugin.package:<28} {plugin.version:<16} {kind}")
    return 0


def page(args: argparse.Namespace) -> int:
    """Serve the connector as a page that only this computer can open, until Ctrl+C

    The page opens in your default browser; if it does not, open the address it prints: it holds a
    key that the page needs.
    """
    started = App.default()
    directory = store.directory()
    try:
        try:
            server = web.Page(started, args.port)
        except OSError as exc:
            # Deploy App's button reaches the default port only, so another port is not the first advice there
            advice = (
                "The connector may be running already: use its page. Another port, given with --port, works "
                "for a pasted settings line only, as Deploy App's button reaches this port."
                if args.port == web.PORT
                else "Give another with --port."
            )
            raise _Failed(
                f"The page cannot listen on port {args.port} of this computer: {exc.strerror}. {advice}"
            ) from exc
        if args.browser and browser.open_page(server.address, directory):
            print(f"{OPENING} {server.address}")
        else:
            print(f"{OPEN} {server.address}")
        print("Press Ctrl+C to stop it.", flush=True)
        with server:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                if started.busy():
                    print(STOPPED_BUSY, file=sys.stderr)
    finally:
        browser.remove(directory)
        started.close()
    return 0


def _between(low: int, high: int | None = None) -> Callable[[str], int]:
    """An argparse type: a whole number from low, up to high when given"""

    def number(value: str) -> int:
        try:
            given = int(value)
        except ValueError:
            given = low - 1
        if given < low or (high is not None and given > high):
            upto = f" to {high}" if high is not None else " up"
            raise argparse.ArgumentTypeError(f"{value} is not a whole number from {low}{upto}")
        return given

    return number


#: The options more than one command takes, with their help
SHARED = {
    "--mdm-url": "The MDM's address, as --set mdm_url (default: ODMDM_MDM_URL)",
    "--team": "The team the phones are in, as --set team (default: ODMDM_TEAM)",
    "--template": "What to install, as --set template (default: ODMDM_TEMPLATE)",
    "--domain": "The deployment's DNS name (default: ODMDM_DOMAIN)",
}


def _parser() -> argparse.ArgumentParser:
    """The command line, its defaults read from the environment now"""
    parser = argparse.ArgumentParser(
        prog="odmdm-connector",
        description="Connect an MDM, Fleet built in, to an OpenDefence deployment's device enrolment",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s, version {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Shorthand for info/debug loglevel (-v/-vv)")
    parser.add_argument(
        "--mdm",
        dest="chosen",
        default=os.environ.get("ODMDM_MDM", "fleet"),
        metavar="NAME",
        help="The MDM to talk to (default: ODMDM_MDM, else fleet)",
    )
    parser.add_argument(
        "--set",
        dest="given",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="A setting of the MDM, such as mdm_url; never a secret, which is read from the file ODMDM_<KEY>_FILE names",
    )
    commands = parser.add_subparsers(title="commands", metavar="COMMAND", required=True)

    def command(called: str, run: Callable[[argparse.Namespace], int], *shared: str) -> argparse.ArgumentParser:
        doc = inspect.cleandoc(run.__doc__ or "")
        sub = commands.add_parser(
            called,
            help=doc.splitlines()[0],
            description=doc,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub.set_defaults(run=run, parser=sub)
        for option in shared:
            sub.add_argument(option, default=None, help=SHARED[option])
        return sub

    sub = command("apply", apply, "--template", "--mdm-url", "--team", "--domain")
    sub.add_argument(
        "--force-policy",
        action="store_true",
        help="Re-upload the policy even if unchanged, to reach a host that just joined",
    )
    sub.add_argument("--dry-run", action="store_true", help="Read and validate the template, then stop")
    command("challenge", challenge, "--template", "--mdm-url", "--domain")
    command("phones", phones, "--mdm-url", "--team")
    sub = command("name", name, "--mdm-url", "--team")
    sub.add_argument("pairs", nargs="*", metavar="ID=CALLSIGN")
    sub.add_argument(
        "--all", dest="prefix", metavar="PREFIX", help="Name every phone that has no name PREFIX<n>, in enrolment order"
    )
    sub.add_argument(
        "--start",
        type=_between(1),
        metavar="N",
        help="The first n for --all (default: one above the highest in use)",
    )
    sub = command("resend", resend, "--mdm-url", "--team")
    sub.add_argument("ids", nargs="*", metavar="ID")
    command("mdms", mdms)
    sub = command("web", page)
    sub.add_argument(
        "--port", type=_between(1, 65535), default=web.PORT, help="The port on 127.0.0.1 (default: %(default)s)"
    )
    sub.add_argument("--no-browser", dest="browser", action="store_false", help="Only print the address")
    return parser
