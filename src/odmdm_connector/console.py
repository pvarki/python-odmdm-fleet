"""CLI entrypoints for odmdm-connector"""

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import click
from libadvian.logging import init_logging

from odmdm_connector import __version__

from . import config, template
from .fleet import FleetError, FleetMdm, Phone

LOGGER = logging.getLogger(__name__)

#: The deployment's rule for a device callsign, checked here only so a typo is refused now rather
#: than as a failed certificate twenty minutes later. The deployment still decides.
CALLSIGN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,30}[A-Za-z0-9]")


@click.group()
@click.version_option(version=__version__)
@click.pass_context
@click.option("-v", "--verbose", count=True, help="Shorthand for info/debug loglevel (-v/-vv)")
def cli_group(ctx: click.Context, verbose: int) -> None:
    """Connect FleetDM to an OpenDefence deployment's device enrolment"""
    _ = ctx
    loglevel = config.LOG_LEVEL
    if verbose == 1:
        loglevel = 20
    if verbose >= 2:
        loglevel = 10
    init_logging(loglevel)
    LOGGER.setLevel(loglevel)
    # One line per Fleet request buries what the command says; -vv brings them back
    logging.getLogger("httpx").setLevel(max(loglevel, logging.WARNING) if verbose < 2 else loglevel)


def odmdm_connector_cli() -> None:
    """CLI entrypoint"""
    cli_group()  # pylint: disable=no-value-for-parameter


def _fleet(ctx: click.Context, url: str | None) -> FleetMdm | None:
    """Fleet's API, or None after saying what is missing"""
    the_url = url or config.URL
    if not the_url:
        click.echo("Give --mdm-url or set ODMDM_MDM_URL", err=True)
        ctx.exit(1)
        return None
    if urlparse(the_url).scheme != "https":
        click.echo(
            "The MDM address must start with https://, or the API token would cross the network readable", err=True
        )
        ctx.exit(1)
        return None
    if not config.TOKEN_FILE or not config.TOKEN_FILE.is_file():
        click.echo("Set ODMDM_TOKEN_FILE to a file holding the Fleet API token", err=True)
        ctx.exit(1)
        return None
    return FleetMdm(the_url, config.TOKEN_FILE.read_text(encoding="utf-8").strip())


def _template(ctx: click.Context, path: str | None, domain: str | None) -> template.MdmTemplate | None:
    """The deployment's template with its values filled in, or None after saying why not"""
    the_path = Path(path) if path else config.TEMPLATE
    the_domain = domain or config.DOMAIN
    if not the_path or not the_domain:
        click.echo("Give --template and --domain or set ODMDM_TEMPLATE and ODMDM_DOMAIN", err=True)
        ctx.exit(1)
        return None
    try:
        return template.load(the_path, domain=the_domain, key_alias=config.KEY_ALIAS)
    except template.TemplateError as exc:
        click.echo(f"Template refused: {exc}", err=True)
        ctx.exit(1)
        return None


@cli_group.command(name="apply")
@click.pass_context
@click.option("--template", "path", default=None, help="What to install (default: ODMDM_TEMPLATE)")
@click.option("--mdm-url", default=None, help="Base URL of Fleet's API (default: ODMDM_MDM_URL)")
@click.option("--team", default=None, help="The team devices get the certificate in (default: ODMDM_TEAM)")
@click.option("--domain", default=None, help="The deployment's DNS name (default: ODMDM_DOMAIN)")
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
    """Tell Fleet what this deployment's devices need

    Run it when a deployment is set up and whenever its template changes. A changed policy stops
    apps installing on phones that are enrolling at that moment, so a new enrolment window is
    `challenge`, not this.

    Order matters and is handled for you. Apps install at ENROLMENT and at no other time, so a
    device that has already joined will not pick up a template applied afterwards -- it has to
    enrol again, under a callsign that has not been spent.
    """
    wanted = _template(ctx, path, domain)
    if not wanted:
        return
    click.echo(f"Template names {len(wanted.apps)} apps, {len(wanted.preinstall_packages)} installed at enrolment")
    for app in wanted.apps:
        click.echo(f"  {app.package}{' (at enrolment)' if app.preinstall else ' (on demand)'}")
    if wanted.link_app:
        click.echo(f"  launcher link {wanted.link_app.title!r} -> {wanted.link_app.url}")
    if dry_run:
        ctx.exit(0)
        return

    fleet = _fleet(ctx, mdm_url)
    if not fleet:
        return
    try:
        result = fleet.apply(
            team or config.TEAM,
            wanted,
            force_policy=force_policy,
            scep_url=f"https://{domain or config.DOMAIN}/scep",
            challenge=config.CHALLENGE,
        )
    except FleetError as exc:
        click.echo(f"Fleet refused: {exc}", err=True)
        ctx.exit(1)
        return
    click.echo(f"Applied to team {result['team_id']}: {len(result['assigned'])} apps assigned")
    ctx.exit(0)


@cli_group.command(name="challenge")
@click.pass_context
@click.option(
    "--challenge",
    "value",
    envvar="ODMDM_CHALLENGE",
    prompt="Challenge from Deploy App's MDM page",
    hide_input=True,
    help="The new window's challenge (default: ODMDM_CHALLENGE, else asked for)",
)
@click.option("--template", "path", default=None, help="What to install (default: ODMDM_TEMPLATE)")
@click.option("--mdm-url", default=None, help="Base URL of Fleet's API (default: ODMDM_MDM_URL)")
@click.option("--domain", default=None, help="The deployment's DNS name (default: ODMDM_DOMAIN)")
def challenge(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context, value: str, path: str | None, mdm_url: str | None, domain: str | None
) -> None:
    """Give Fleet the challenge of the enrolment window just opened

    Every opening makes a new one. Only the certificate authority changes, so phones still
    installing apps are left alone. Asked for rather than taken as an argument, so it stays out of
    shell history.
    """
    wanted = _template(ctx, path, domain)
    if not wanted:
        return
    if not wanted.certificate:
        click.echo("The template states no certificate, so there is no authority to give it to", err=True)
        ctx.exit(1)
        return
    fleet = _fleet(ctx, mdm_url)
    if not fleet:
        return
    try:
        fleet.ensure_authority(wanted.certificate.authority, f"https://{domain or config.DOMAIN}/scep", value)
    except FleetError as exc:
        click.echo(f"Fleet refused: {exc}", err=True)
        ctx.exit(1)
        return
    click.echo(f"{wanted.certificate.authority} has the new challenge")
    ctx.exit(0)


@cli_group.command(name="phones")
@click.pass_context
@click.option("--mdm-url", default=None, help="Base URL of Fleet's API (default: ODMDM_MDM_URL)")
@click.option("--team", default=None, help="The team the phones are in (default: ODMDM_TEAM)")
def phones(ctx: click.Context, mdm_url: str | None, team: str | None) -> None:
    """List the team's phones and where each one's certificate is

    One line each: the host id that name and resend take, the callsign, the model, the day it
    enrolled, and the certificate in plain words.
    """
    fleet = _fleet(ctx, mdm_url)
    if not fleet:
        return
    try:
        found = fleet.phones(fleet.team_id(team or config.TEAM), config.KEY_ALIAS)
    except FleetError as exc:
        click.echo(f"Fleet refused: {exc}", err=True)
        ctx.exit(1)
        return
    for phone in found:
        click.echo(f"{phone.id:<5} {phone.callsign or '-':<12} {phone.model:<26} {phone.enrolled[:10]}  {phone.state}")
    if not found:
        click.echo("No phones in the team")
    ctx.exit(0)


def _pairs(values: tuple[str, ...]) -> list[tuple[int, str]]:
    """HOST=CALLSIGN arguments, as host ids and callsigns"""
    pairs = []
    for value in values:
        host, _, callsign = value.partition("=")
        if not host.isdigit() or not callsign:
            raise click.UsageError(f"{value!r} is not HOST=CALLSIGN, the host being the id that phones prints")
        pairs.append((int(host), callsign))
    return pairs


def _numbered(found: list[Phone], prefix: str, start: int | None) -> list[tuple[int, str]]:
    """PREFIX<n> for every phone without a name, in enrolment order

    Numbered from start, else from one above the highest PREFIX<n> already in the team.
    """
    if start is None:
        used = (re.fullmatch(re.escape(prefix) + r"(\d+)", phone.callsign, re.IGNORECASE) for phone in found)
        start = max((int(match.group(1)) for match in used if match), default=0) + 1
    waiting = [phone.id for phone in found if not phone.callsign]
    return [(host, f"{prefix}{start + n}") for n, host in enumerate(waiting)]


def _refusal(found: dict[int, Phone], wanted: list[tuple[int, str]]) -> str:
    """Why these names cannot all be given, or empty when they can

    Callsigns are compared without case, as the deployment compares them.
    """
    hosts: set[int] = set()
    callsigns: set[str] = set()
    for host, callsign in wanted:
        if host not in found:
            return f"host {host} is not in the team"
        if host in hosts:
            return f"host {host} is named twice"
        if not CALLSIGN.fullmatch(callsign):
            return f"{callsign}: a callsign is 3-32 letters, digits or hyphens, and does not start or end with a hyphen"
        if callsign.lower() in callsigns:
            return f"{callsign} is given twice"
        owner = next((p.id for p in found.values() if p.id != host and p.callsign.lower() == callsign.lower()), 0)
        if owner:
            return f"{callsign} already belongs to host {owner}"
        hosts.add(host)
        callsigns.add(callsign.lower())
    return ""


def _policy_again(fleet: FleetMdm, team_id: int, found: dict[int, Phone]) -> None:
    """Send the policy again if a phone enrolled after it was last sent, as Fleet may have overwritten it there"""
    later = fleet.resend_policy(team_id, list(found.values()))
    if later:
        click.echo(f"Policy sent again: {later} phone{'' if later == 1 else 's'} enrolled after it was last sent")


@cli_group.command(name="name")
@click.pass_context
@click.argument("pairs", nargs=-1, metavar="[HOST=CALLSIGN]...")
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
@click.option("--mdm-url", default=None, help="Base URL of Fleet's API (default: ODMDM_MDM_URL)")
@click.option("--team", default=None, help="The team the phones are in (default: ODMDM_TEAM)")
def name(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context,
    pairs: tuple[str, ...],
    prefix: str | None,
    start: int | None,
    mdm_url: str | None,
    team: str | None,
) -> None:
    """Name phones, and have Fleet send them certificates

    HOST=CALLSIGN for as many phones as you like, in any order, the host being the id that phones
    prints. Or --all PREFIX, which numbers every phone that has no name yet. Each phone gets its
    certificate at its next check-in, within about 15 minutes. A phone that already has its
    certificate under the name is left alone.

    Nothing is changed if a name is not a callsign, is given twice or belongs to another phone in
    the team, or if a host is not in the team.

    The team's policy is sent again, to every phone in it, if a phone enrolled after it was last
    sent, because Fleet can overwrite a new phone's policy. So name phones once every phone in the
    team has its apps.
    """
    if bool(pairs) == bool(prefix) or (start is not None and not prefix):
        raise click.UsageError("Give HOST=CALLSIGN for each phone, or --all PREFIX")
    wanted = _pairs(pairs)
    fleet = _fleet(ctx, mdm_url)
    if not fleet:
        return
    named: list[str] = []
    try:
        team_id = fleet.team_id(team or config.TEAM)
        found = {phone.id: phone for phone in fleet.phones(team_id, config.KEY_ALIAS)}
        if prefix:
            wanted = _numbered(list(found.values()), prefix, start)
        refused = _refusal(found, wanted)
        if refused:
            click.echo(f"Nothing named: {refused}", err=True)
            ctx.exit(1)
            return
        if not wanted:
            click.echo("Nothing to name")
            ctx.exit(0)
            return
        template = fleet.certificate_template(team_id, config.KEY_ALIAS)
        for host, callsign in wanted:
            old = found[host]
            # Asked again under the name it holds, it is refused as taken: a working phone then shows as refused
            if old.status == "verified" and old.callsign.lower() == callsign.lower():
                click.echo(f"Host {host} is already {old.callsign}")
                continue
            fleet.name_host(host, callsign)
            named.append(f"{host}={callsign}")
            if old.status:
                fleet.resend(host, template)
            line = f"Host {host} is {callsign}"
            if not old.status:
                line += ". Fleet has no certificate request for it, so nothing was sent"
            elif old.status == "verified" and old.callsign:
                line += f". It was {old.callsign} and still has that certificate: revoke {old.callsign} in Deploy App."
            click.echo(line)
        if named:
            _policy_again(fleet, team_id, found)
    except FleetError as exc:
        click.echo(f"Fleet refused: {exc}", err=True)
        if named:
            click.echo(f"Already named before it stopped: {' '.join(named)}. phones shows where each is", err=True)
        ctx.exit(1)
        return
    ctx.exit(0)


@cli_group.command(name="resend")
@click.pass_context
@click.argument("hosts", nargs=-1, type=int)
@click.option("--mdm-url", default=None, help="Base URL of Fleet's API (default: ODMDM_MDM_URL)")
@click.option("--team", default=None, help="The team the phones are in (default: ODMDM_TEAM)")
def resend(ctx: click.Context, hosts: tuple[int, ...], mdm_url: str | None, team: str | None) -> None:
    """Have Fleet send phones their certificate requests again

    For a refused phone once the cause is fixed, such as enrolment opened again and its new
    challenge given to Fleet. A taken name is not one of those: Deploy App never frees a name, so
    give the phone another with name, which sends the request itself. Without host ids, every phone
    that has a name and whose certificate failed. Each acts on it at its next check-in, within
    about 15 minutes.

    A host id is asked whatever its state. A phone that has its certificate asks again under the
    name it holds and is refused, and reads as refused from then on though that certificate still
    works. As with name, the team's policy is sent again if a phone enrolled after it was last sent.
    """
    fleet = _fleet(ctx, mdm_url)
    if not fleet:
        return
    try:
        team_id = fleet.team_id(team or config.TEAM)
        found = {phone.id: phone for phone in fleet.phones(team_id, config.KEY_ALIAS)}
        unknown = [host for host in hosts if host not in found]
        if unknown:
            click.echo(f"Nothing sent: host {unknown[0]} is not in the team", err=True)
            ctx.exit(1)
            return
        chosen = list(hosts) or [phone.id for phone in found.values() if phone.status == "failed" and phone.callsign]
        if not chosen:
            click.echo("Nothing to resend")
            ctx.exit(0)
            return
        template = fleet.certificate_template(team_id, config.KEY_ALIAS)
        for host in chosen:
            fleet.resend(host, template)
            click.echo(f"Host {host} ({found[host].callsign or 'no name'}): certificate asked for again")
        _policy_again(fleet, team_id, found)
    except FleetError as exc:
        click.echo(f"Fleet refused: {exc}", err=True)
        ctx.exit(1)
        return
    ctx.exit(0)
