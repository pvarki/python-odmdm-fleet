"""CLI entrypoints for odmdm-connector

Knows the MDM only through the contract in mdm.py, and gets it only through plugins.load: the
chosen MDM is the one entry point of the group odmdm_connector.mdm named by --mdm, Fleet being the
one built in.
"""

import datetime
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse

import click
from libadvian.logging import init_logging

from odmdm_connector import __version__

from . import config, core, plugins, settings
from .mdm import ASK_AGAIN, SET_CHALLENGE, SETUP, Field, Mdm, MdmError
from .settings import Settings

LOGGER = logging.getLogger(__name__)


@click.group()
@click.version_option(version=__version__)
@click.pass_context
@click.option("-v", "--verbose", count=True, help="Shorthand for info/debug loglevel (-v/-vv)")
@click.option(
    "--mdm",
    "chosen",
    envvar="ODMDM_MDM",
    default="fleet",
    show_default=True,
    metavar="NAME",
    help="The MDM to talk to (default: ODMDM_MDM, else fleet)",
)
@click.option(
    "--set",
    "given",
    multiple=True,
    metavar="KEY=VALUE",
    help="A setting of the MDM, such as mdm_url; never a secret, which is read from the file ODMDM_<KEY>_FILE names",
)
def cli_group(ctx: click.Context, verbose: int, chosen: str, given: tuple[str, ...]) -> None:
    """Connect an MDM, Fleet built in, to an OpenDefence deployment's device enrolment"""
    loglevel = config.LOG_LEVEL
    if verbose == 1:
        loglevel = 20
    if verbose >= 2:
        loglevel = 10
    init_logging(loglevel)
    LOGGER.setLevel(loglevel)
    # One line per MDM request buries what the command says; -vv brings them back
    logging.getLogger("httpx").setLevel(max(loglevel, logging.WARNING) if verbose < 2 else loglevel)
    sets: dict[str, str] = {}
    for value in given:
        key, equals, setting = value.partition("=")
        if not key or not equals:
            raise click.UsageError("--set takes KEY=VALUE")
        sets[key] = setting
    ctx.obj = {"mdm": chosen, "set": sets}


def odmdm_connector_cli() -> None:
    """CLI entrypoint"""
    cli_group()  # pylint: disable=no-value-for-parameter


def _fail(ctx: click.Context, message: str) -> NoReturn:
    click.echo(message, err=True)
    ctx.exit(1)


def _chosen(ctx: click.Context, name: str) -> type[Mdm]:
    """The class of the MDM of that name, saying first whose code it is when it is not built in"""
    try:
        plugin = plugins.find(name)
        if not plugin.built_in:
            click.echo(
                f"The MDM {name} is a plugin from {plugin.package} {plugin.version}: code that sees the MDM token",
                err=True,
            )
        return plugins.load(name)
    except plugins.NotInstalled as exc:
        raise click.UsageError(str(exc)) from exc
    except plugins.PluginError as exc:
        _fail(ctx, str(exc))


def _values(name: str, fields: tuple[Field, ...], given: Mapping[str, str]) -> dict[str, str]:
    """The fields' values: from --set, else ODMDM_<KEY>, else the default

    A secret only ever from the file ODMDM_<KEY>_FILE names, so it stays out of shell history and
    process environments. No value is ever in a message.
    """
    keys = sorted(item.key for item in fields)
    unknown = sorted(set(given) - set(keys))
    if unknown:
        raise click.UsageError(f"{name} has no setting {unknown[0]}; it has {', '.join(keys)}")
    values: dict[str, str] = {}
    for item in fields:
        env = f"ODMDM_{item.key.upper()}"
        if item.secret:
            if item.key in given:
                raise click.UsageError(f"{item.key} is a secret: put it in a file and set {env}_FILE to that file")
            path = os.environ.get(f"{env}_FILE", "")
            values[item.key] = Path(path).read_text(encoding="utf-8").strip() if path and Path(path).is_file() else ""
        else:
            values[item.key] = given[item.key] if item.key in given else os.environ.get(env, item.default)
    return values


def _mdm(ctx: click.Context, capability: str = "", **shorthands: str | None) -> Mdm:
    """The chosen MDM, configured; or the MDM's sentence for lacking the capability, and exit

    The shorthands are the per-command options such as --team, each the same as --set team=...
    """
    name = str(ctx.obj["mdm"])
    chosen = _chosen(ctx, name)
    if capability in chosen.missing:
        _fail(ctx, chosen.missing[capability])
    given = {**ctx.obj["set"], **{key: value for key, value in shorthands.items() if value is not None}}
    try:
        return chosen(_values(name, chosen.fields, given))
    except MdmError as exc:
        _fail(ctx, str(exc))


def _domain(ctx: click.Context, domain: str | None) -> str:
    the_domain = domain or config.DOMAIN
    if not the_domain:
        _fail(ctx, "Give --domain or set ODMDM_DOMAIN")
    return the_domain


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _window(ctx: click.Context, value: str, domain: str) -> Settings:
    """The enrolment window, from a settings line or a bare challenge, or exit after saying why not

    A bare challenge says nothing of when the window closes. The challenge is never in a message.
    """
    if not settings.is_line(value):
        return Settings(f"https://{domain}/scep", value.strip())
    try:
        line = settings.parse(value, _now())
    except settings.SettingsError as exc:
        _fail(ctx, str(exc))
    host = urlparse(line.scep_url).hostname or ""
    if host != domain.lower():
        _fail(ctx, f"The settings line is for {host}, not {domain}: copy it from this deployment's MDM page")
    return line


MDM_URL = click.option("--mdm-url", default=None, help="The MDM's address, as --set mdm_url (default: ODMDM_MDM_URL)")
TEAM = click.option("--team", default=None, help="The team the phones are in, as --set team (default: ODMDM_TEAM)")
TEMPLATE = click.option(
    "--template", "path", default=None, help="What to install, as --set template (default: ODMDM_TEMPLATE)"
)
DOMAIN = click.option("--domain", default=None, help="The deployment's DNS name (default: ODMDM_DOMAIN)")


@cli_group.command(name="apply")
@click.pass_context
@TEMPLATE
@MDM_URL
@TEAM
@DOMAIN
@click.option(
    "--force-policy", is_flag=True, help="Re-upload the policy even if unchanged, to reach a host that just joined"
)
@click.option("--dry-run", is_flag=True, help="Read and validate the template, then stop")
def apply(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context,
    path: str | None,
    mdm_url: str | None,
    team: str | None,
    domain: str | None,
    force_policy: bool,
    dry_run: bool,
) -> None:
    """Tell the MDM what this deployment's devices need

    Run it when a deployment is set up and whenever its template changes. A changed policy stops
    apps installing on phones that are enrolling at that moment, so a new enrolment window is
    `challenge`, not this.

    Order matters and is handled for you. Apps install at ENROLMENT and at no other time, so a
    device that has already joined will not pick up a template applied afterwards -- it has to
    enrol again, under a callsign that has not been spent.
    """
    the_domain = _domain(ctx, domain)
    window = Settings(f"https://{the_domain}/scep", "") if dry_run else _window(ctx, config.CHALLENGE, the_domain)
    mdm = _mdm(ctx, SETUP, mdm_url=mdm_url, team=team, template=path)
    try:
        for step in mdm.setup(window, force=force_policy, dry_run=dry_run):
            click.echo(step.label if step.done else f"{step.label} (not done)")
            if step.message:
                click.echo(step.message)
    except MdmError as exc:
        _fail(ctx, str(exc))
    ctx.exit(0)


@cli_group.command(name="challenge")
@click.pass_context
@click.option(
    "--challenge",
    "value",
    envvar="ODMDM_CHALLENGE",
    default=None,
    help="The new window's settings line or challenge (default: ODMDM_CHALLENGE, else asked for)",
)
@TEMPLATE
@MDM_URL
@DOMAIN
def challenge(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context, value: str | None, path: str | None, mdm_url: str | None, domain: str | None
) -> None:
    """Give the MDM the challenge of the enrolment window just opened

    Every opening makes a new one. Only the certificate authority changes, so phones still
    installing apps are left alone. Asked for rather than taken as an argument, so it stays out of
    shell history. Given the whole settings line, its SCEP URL must be on this deployment's domain.
    """
    the_domain = _domain(ctx, domain)
    mdm = _mdm(ctx, SET_CHALLENGE, mdm_url=mdm_url, template=path)
    if value is None:
        value = str(click.prompt("Settings line or challenge from Deploy App's MDM page", hide_input=True))
    window = _window(ctx, value, the_domain)
    try:
        mdm.set_challenge(window)
    except MdmError as exc:
        _fail(ctx, str(exc))
    until = f". The enrolment window is open until {window.closes:%Y-%m-%d %H:%M} UTC" if window.closes else ""
    click.echo(f"{mdm.title} has the new challenge{until}")
    ctx.exit(0)


@cli_group.command(name="phones")
@click.pass_context
@MDM_URL
@TEAM
def phones(ctx: click.Context, mdm_url: str | None, team: str | None) -> None:
    """List the team's phones and where each one's certificate is

    One line each: the id that name and resend take, the callsign, the model, the day it
    enrolled, and the certificate in plain words.
    """
    mdm = _mdm(ctx, mdm_url=mdm_url, team=team)
    try:
        found = mdm.phones()
    except MdmError as exc:
        _fail(ctx, str(exc))
    for phone in found:
        click.echo(
            f"{phone.id:<5} {phone.callsign or '-':<12} {phone.model:<26} {phone.joined[:10]}  {core.said(phone)}"
        )
    if not found:
        click.echo("No phones in the team")
    ctx.exit(0)


@cli_group.command(name="name")
@click.pass_context
@click.argument("pairs", nargs=-1, metavar="[ID=CALLSIGN]...")
@click.option(
    "--all",
    "prefix",
    metavar="PREFIX",
    default=None,
    help="Name every phone that has no name PREFIX<n>, in enrolment order",
)
@click.option(
    "--start",
    type=click.IntRange(min=1),
    metavar="N",
    default=None,
    help="The first n for --all (default: one above the highest in use)",
)
@MDM_URL
@TEAM
def name(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context,
    pairs: tuple[str, ...],
    prefix: str | None,
    start: int | None,
    mdm_url: str | None,
    team: str | None,
) -> None:
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
    if bool(pairs) == bool(prefix) or (start is not None and not prefix):
        raise click.UsageError("Give ID=CALLSIGN for each phone, or --all PREFIX")
    try:
        wanted = core.pairs(pairs)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    mdm = _mdm(ctx, mdm_url=mdm_url, team=team)
    try:
        found = mdm.phones()
        if prefix:
            wanted = core.numbered(found, prefix, start)
        refused = core.refusal(found, wanted)
        if refused:
            _fail(ctx, f"Nothing named: {refused}")
        if not wanted:
            click.echo("Nothing to name")
            ctx.exit(0)
        lines = mdm.name(dict(wanted))
    except MdmError as exc:
        for line in exc.lines:
            click.echo(line)
        click.echo(str(exc), err=True)
        if exc.named:
            done = " ".join(f"{phone}={callsign}" for phone, callsign in exc.named.items())
            click.echo(f"Already named before it stopped: {done}. phones shows where each is", err=True)
        ctx.exit(1)
    for line in lines:
        click.echo(line)
    ctx.exit(0)


@cli_group.command(name="resend")
@click.pass_context
@click.argument("ids", nargs=-1)
@MDM_URL
@TEAM
def resend(ctx: click.Context, ids: tuple[str, ...], mdm_url: str | None, team: str | None) -> None:
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
    mdm = _mdm(ctx, ASK_AGAIN, mdm_url=mdm_url, team=team)
    try:
        lines = mdm.ask_again(list(ids))
    except MdmError as exc:
        for line in exc.lines:
            click.echo(line)
        _fail(ctx, str(exc))
    for line in lines:
        click.echo(line)
    if not lines:
        click.echo("Nothing to resend")
    ctx.exit(0)


@cli_group.command(name="mdms")
@click.pass_context
def mdms(ctx: click.Context) -> None:
    """List the MDMs installed: the name --mdm takes, the package and its version, built in or a plugin

    Lists them without loading any. A plugin is code that sees the MDM token: install only one you trust.
    """
    try:
        found = plugins.installed()
    except plugins.PluginError as exc:
        _fail(ctx, str(exc))
    for plugin in found:
        kind = "built in" if plugin.built_in else "plugin"
        click.echo(f"{plugin.name:<12} {plugin.package:<28} {plugin.version:<16} {kind}")
    ctx.exit(0)
