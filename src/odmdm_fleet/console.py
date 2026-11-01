"""CLI entrypoints for odmdm-fleet"""

import logging
import re
from pathlib import Path

import click
from libadvian.logging import init_logging

from odmdm_fleet import __version__

from . import config, template
from .fleet import FleetError, FleetMdm

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


def odmdm_fleet_cli() -> None:
    """CLI entrypoint"""
    cli_group()  # pylint: disable=no-value-for-parameter


def _fleet(ctx: click.Context, url: str | None) -> FleetMdm | None:
    """Fleet's API, or None after saying what is missing"""
    the_url = url or config.URL
    if not the_url:
        click.echo("Give --fleet-url or set ODMDM_FLEET_URL", err=True)
        ctx.exit(1)
        return None
    if not config.TOKEN_FILE or not config.TOKEN_FILE.is_file():
        click.echo("Set ODMDM_FLEET_TOKEN_FILE to a file holding the Fleet API token", err=True)
        ctx.exit(1)
        return None
    return FleetMdm(the_url, config.TOKEN_FILE.read_text(encoding="utf-8").strip())


def _template(ctx: click.Context, path: str | None, domain: str | None) -> template.MdmTemplate | None:
    """The deployment's template with its values filled in, or None after saying why not"""
    the_path = Path(path) if path else config.TEMPLATE
    the_domain = domain or config.DOMAIN
    if not the_path or not the_domain:
        click.echo("Give --template and --domain or set ODMDM_FLEET_TEMPLATE and ODMDM_FLEET_DOMAIN", err=True)
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
@click.option("--template", "path", default=None, help="What to install (default: ODMDM_FLEET_TEMPLATE)")
@click.option("--fleet-url", default=None, help="Base URL of Fleet's API (default: ODMDM_FLEET_URL)")
@click.option("--team", default=None, help="The team devices get the certificate in (default: ODMDM_FLEET_TEAM)")
@click.option(
    "--prep-team",
    default=None,
    help="Also set up this team, where devices enrol and are named first (default: ODMDM_FLEET_PREP_TEAM)",
)
@click.option("--domain", default=None, help="The deployment's DNS name (default: ODMDM_FLEET_DOMAIN)")
@click.option(
    "--force-policy", is_flag=True, help="Re-upload the policy even if unchanged, to reach a host that just joined"
)
@click.option(
    "--force-certificate",
    is_flag=True,
    help="Ask again for the certificate of a host that was named after it enrolled",
)
@click.option("--dry-run", is_flag=True, help="Read and validate the template, then stop")
def apply(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context,
    path: str | None,
    fleet_url: str | None,
    team: str | None,
    prep_team: str | None,
    domain: str | None,
    force_policy: bool,
    force_certificate: bool,
    dry_run: bool,
) -> None:
    """Tell Fleet what this deployment's devices need

    Run it when a deployment is set up and whenever its template changes. It rewrites the policy,
    which stops apps installing on phones that are enrolling at that moment, so a new enrolment
    window is `challenge`, not this.

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

    fleet = _fleet(ctx, fleet_url)
    if not fleet:
        return
    the_prep_team = prep_team or config.PREP_TEAM
    try:
        result = fleet.apply(
            team or config.TEAM,
            wanted,
            force_policy=force_policy,
            force_certificate=force_certificate,
            scep_url=f"https://{domain or config.DOMAIN}/scep",
            challenge=config.CHALLENGE,
        )
        if the_prep_team:
            prep = fleet.apply_preparation(the_prep_team, wanted, result["link"], force_policy=force_policy)
    except FleetError as exc:
        click.echo(f"Fleet refused: {exc}", err=True)
        ctx.exit(1)
        return
    click.echo(f"Applied to team {result['team_id']}: {len(result['assigned'])} apps assigned")
    if the_prep_team:
        click.echo(f"Applied to preparation team {prep['team_id']}: the same apps and launcher, no certificate")
    ctx.exit(0)


@cli_group.command(name="challenge")
@click.pass_context
@click.option(
    "--challenge",
    "value",
    envvar="ODMDM_FLEET_CHALLENGE",
    prompt="Challenge from Deploy App's MDM page",
    hide_input=True,
    help="The new window's challenge (default: ODMDM_FLEET_CHALLENGE, else asked for)",
)
@click.option("--template", "path", default=None, help="What to install (default: ODMDM_FLEET_TEMPLATE)")
@click.option("--fleet-url", default=None, help="Base URL of Fleet's API (default: ODMDM_FLEET_URL)")
@click.option("--domain", default=None, help="The deployment's DNS name (default: ODMDM_FLEET_DOMAIN)")
def challenge(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context, value: str, path: str | None, fleet_url: str | None, domain: str | None
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
    fleet = _fleet(ctx, fleet_url)
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


@cli_group.command(name="plan")
@click.pass_context
@click.argument("callsign")
@click.option("--fleet-url", default=None, help="Base URL of Fleet's API (default: ODMDM_FLEET_URL)")
@click.option(
    "--team",
    default=None,
    help="The team the device waits in (default: ODMDM_FLEET_PREP_TEAM, else ODMDM_FLEET_TEAM)",
)
@click.option("--instead-of", "old", default=None, help="Rename the device now called this, such as a returning phone")
def plan(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context, callsign: str, fleet_url: str | None, team: str | None, old: str | None
) -> None:
    """Name the one device waiting to be named

    Enrol a phone, run this, then the next phone. With more than one phone waiting nothing is
    named: Fleet shows identical phones as identical rows, so the one in your hand cannot be told
    apart from the others.

    A phone that comes back after a wipe keeps its old name, so it is not waiting. Rename it with
    --instead-of and the name it had.
    """
    if not CALLSIGN.fullmatch(callsign):
        click.echo("A callsign is 3-32 letters, digits or hyphens, and does not start or end with a hyphen", err=True)
        ctx.exit(1)
        return
    fleet = _fleet(ctx, fleet_url)
    if not fleet:
        return
    try:
        team_id = fleet.team_id(team or config.PREP_TEAM or config.TEAM)
        if old:
            host = fleet.rename_host(team_id, old, callsign)
        else:
            host = fleet.name_the_waiting_host(team_id, callsign)
    except FleetError as exc:
        click.echo(f"Not named: {exc}", err=True)
        ctx.exit(1)
        return
    click.echo(f"Host {host} is {callsign}")
    ctx.exit(0)


@cli_group.command(name="reconcile")
@click.pass_context
@click.option("--template", "path", default=None, help="What to install (default: ODMDM_FLEET_TEMPLATE)")
@click.option("--fleet-url", default=None, help="Base URL of Fleet's API (default: ODMDM_FLEET_URL)")
@click.option("--team", default=None, help="The team devices get the certificate in (default: ODMDM_FLEET_TEAM)")
@click.option("--domain", default=None, help="The deployment's DNS name (default: ODMDM_FLEET_DOMAIN)")
def reconcile(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context,
    path: str | None,
    fleet_url: str | None,
    team: str | None,
    domain: str | None,
) -> None:
    """Give a certificate to any device that was named after it enrolled

    Run this on a timer. A device enrolled straight into the certificate team asks before anyone
    has named it, so that first attempt fails, and Fleet stops retrying after three.

    It only asks again when asking would change something: re-asking reaches every device in the
    team, and a template rewritten for nothing is rewritten while somebody else is still installing.
    """
    wanted = _template(ctx, path, domain)
    if not wanted:
        return
    if not wanted.certificate:
        click.echo("The template states no certificate, so there is nothing to reconcile")
        ctx.exit(0)
        return
    fleet = _fleet(ctx, fleet_url)
    if not fleet:
        return
    the_team = team or config.TEAM
    try:
        waiting = fleet.hosts_awaiting_certificate(fleet.team_id(the_team), wanted.certificate.name)
        if not waiting:
            click.echo("Nothing waiting")
            ctx.exit(0)
            return
        click.echo(f"{len(waiting)} device(s) named after enrolment and still without a certificate: {waiting}")
        fleet.apply(the_team, wanted, force_certificate=True)
    except FleetError as exc:
        click.echo(f"Fleet refused: {exc}", err=True)
        ctx.exit(1)
        return
    click.echo("Asked again")
    ctx.exit(0)
