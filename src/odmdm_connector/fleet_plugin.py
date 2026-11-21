"""Fleet behind the MDM contract: the plugin built into the connector

Registered under the entry point group odmdm_connector.mdm as fleet, as a plugin from another
package would be. What Fleet does on the wire is fleet.FleetMdm's; this turns it into the
contract's phones, states, steps and sentences.
"""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from . import template
from .fleet import FleetError, FleetMdm, Unauthorized
from .fleet import Phone as FleetPhone
from .mdm import API_VERSION, Field, MdmError, Phone, State, Step
from .settings import Settings

#: A phone acts on a request at its next check-in, every 15 minutes
CHECK_IN = "up to 15 minutes"


def _state(phone: FleetPhone) -> tuple[State, str]:
    """Where the certificate is, and Fleet's words beside it

    The details matched are the ones seen on a live Fleet 4.90.1. After a failure a phone reports,
    Fleet asks again by itself up to three times, and that request keeps the failure's detail while
    it waits.
    """
    if not phone.status:
        return State.NO_REQUEST, ""
    if phone.status == "verified":
        return State.READY, ""
    if phone.status == "failed":
        return _failure(phone)
    if not phone.detail:
        return State.WAITING, CHECK_IN
    state, detail = _failure(phone)
    return State.WAITING, f"{CHECK_IN}; last time: {state.value}{': ' + detail if detail else ''}"


def _failure(phone: FleetPhone) -> tuple[State, str]:
    """What Fleet's detail on a failed request means"""
    if "does not have an IDP user" in phone.detail:
        # Naming does not send the request again, so a name beside this detail was given without a resend
        return (State.NOT_ASKED, "ask again") if phone.callsign else (State.NEEDS_NAME, "")
    if "SCEP enrollment failed" in phone.detail:
        return State.REFUSED, "name taken or not allowed, enrolment closed, or Fleet has an old challenge"
    if "Network error" in phone.detail:
        return State.UNREACHABLE, ""
    # Nothing seen on a live Fleet yet: Fleet's own words, as a failure the phone can be asked again after
    return State.FAILED, phone.detail


def _refused(exc: FleetError, named: Mapping[str, str] | None = None, lines: Sequence[str] = ()) -> MdmError:
    """Fleet's refusal as the contract's, marked when Fleet does not accept the token"""
    return MdmError(f"Fleet refused: {exc}", named, lines, unauthorized=isinstance(exc, Unauthorized))


class FleetPlugin:
    """FleetDM, one team per Deploy App"""

    API_VERSION: ClassVar[int] = API_VERSION
    title: ClassVar[str] = "Fleet"
    fields: ClassVar[tuple[Field, ...]] = (
        Field("mdm_url", "Fleet address", help="The address you open Fleet at, starting with https://", required=True),
        Field(
            "token",
            "API token",
            secret=True,
            help=(
                "A global admin's API token: in Fleet, My account, Get API token. That one ends after some days; "
                "an API-only user's does not."
            ),
            required=True,
        ),
        Field("team", "Team", default="Deploy App", help="The team phones enrol into and get the certificate in"),
        Field(
            "template",
            "Template",
            help="A template file; without one, the one shipped with the connector",
            advanced=True,
        ),
        Field(
            "key_alias",
            "Key alias",
            default="rmscep",
            help="The certificate template's name, the Android keystore alias. Never change it for a deployment",
            advanced=True,
        ),
    )
    missing: ClassVar[Mapping[str, str]] = {}

    def __init__(self, values: Mapping[str, str]) -> None:
        given = {item.key: values.get(item.key) or item.default for item in self.fields}
        self._url = given["mdm_url"]
        self._token = given["token"]
        self.team = given["team"]
        self.template = Path(given["template"]) if given["template"] else template.EXAMPLE
        self.key_alias = given["key_alias"]
        self._api: FleetMdm | None = None
        self._team_id = 0

    def _fleet(self) -> FleetMdm:
        """Fleet's API, checked only once it is needed, so a dry run needs no token"""
        if self._api:
            return self._api
        if not self._url:
            raise MdmError("The Fleet address is missing")
        if urlparse(self._url).scheme != "https":
            raise MdmError(
                "The MDM address must start with https://, or the API token would cross the network readable"
            )
        if not self._token:
            raise MdmError("The Fleet API token is missing")
        self._api = FleetMdm(self._url, self._token)
        return self._api

    def _template(self, window: Settings) -> template.MdmTemplate:
        """The template with this deployment's values filled in"""
        try:
            return template.load(
                self.template, domain=urlparse(window.scep_url).hostname or "", key_alias=self.key_alias
            )
        except template.TemplateError as exc:
            raise MdmError(f"Template refused: {exc}") from exc

    def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
        """State what the template says, in the order FleetMdm.apply knows works

        force sends the policy again even if Fleet's copy is unchanged, to reach a phone that just
        joined.
        """
        wanted = self._template(window)
        lines = [f"  {app.package}{' (at enrolment)' if app.preinstall else ' (on demand)'}" for app in wanted.apps]
        if wanted.link_app:
            lines.append(f"  launcher link {wanted.link_app.title!r} -> {wanted.link_app.url}")
        label = f"Template names {len(wanted.apps)} apps, {len(wanted.preinstall_packages)} installed at enrolment"
        yield Step(label, True, "\n".join(lines))
        if dry_run:
            return
        try:
            result = self._fleet().apply(
                self.team, wanted, force_policy=force, scep_url=window.scep_url, challenge=window.challenge
            )
        except FleetError as exc:
            raise _refused(exc) from exc
        yield Step(f"Applied to team {result['team_id']}: {len(result['assigned'])} apps assigned", True)

    def set_challenge(self, window: Settings) -> None:
        """Give the template's certificate authority the window's challenge, and change nothing else"""
        wanted = self._template(window)
        if not wanted.certificate:
            raise MdmError("The template states no certificate, so there is no authority to give it to")
        try:
            self._fleet().ensure_authority(wanted.certificate.authority, window.scep_url, window.challenge)
        except FleetError as exc:
            raise _refused(exc) from exc

    def _phones(self) -> dict[str, FleetPhone]:
        """The team's phones by id, read now: never kept between calls, so nothing shown is stale"""
        api = self._fleet()
        try:
            self._team_id = api.team_id(self.team)
            return {str(phone.id): phone for phone in api.phones(self._team_id, self.key_alias)}
        except FleetError as exc:
            raise _refused(exc) from exc

    def phones(self) -> list[Phone]:
        return [
            Phone(key, phone.callsign, phone.model, phone.enrolled, *_state(phone))
            for key, phone in self._phones().items()
        ]

    def _policy_again(self, found: dict[str, FleetPhone]) -> list[str]:
        """Send the policy again if a phone enrolled after it was last sent, as Fleet may have overwritten it there"""
        later = self._fleet().resend_policy(self._team_id, list(found.values()))
        if not later:
            return []
        return [f"Policy sent again: {later} phone{'' if later == 1 else 's'} enrolled after it was last sent"]

    def name(self, names: Mapping[str, str]) -> list[str]:
        """Name each host, have Fleet send its certificate request again, then the policy if needed

        A phone that has its certificate under the name is left alone: asked again under the name
        it holds, it is refused as taken, and a working phone then shows as refused.
        """
        found = self._phones()
        unknown = [key for key in names if key not in found]
        if unknown:
            raise MdmError(f"Nothing named: host {unknown[0]} is not in the team")
        api = self._fleet()
        lines: list[str] = []
        named: dict[str, str] = {}
        try:
            certificate = api.certificate_template(self._team_id, self.key_alias)
            for key, callsign in names.items():
                old = found[key]
                if old.status == "verified" and old.callsign.lower() == callsign.lower():
                    lines.append(f"Phone {key} is already {old.callsign}")
                    continue
                api.name_host(old.id, callsign)
                named[key] = callsign
                if old.status:
                    api.resend(old.id, certificate)
                line = f"Phone {key} is {callsign}"
                if not old.status:
                    line += ". Fleet has no certificate request for it, so nothing was sent"
                elif old.status == "verified" and old.callsign:
                    line += (
                        f". It was {old.callsign} and still has that certificate: revoke {old.callsign} in Deploy App."
                    )
                lines.append(line)
            if named:
                lines += self._policy_again(found)
        except FleetError as exc:
            raise _refused(exc, named, lines) from exc
        return lines

    def ask_again(self, ids: Sequence[str]) -> list[str]:
        """Resend the hosts' certificate requests; no ids: every named phone whose request failed"""
        found = self._phones()
        unknown = [key for key in ids if key not in found]
        if unknown:
            raise MdmError(f"Nothing sent: host {unknown[0]} is not in the team")
        chosen = list(ids) or [key for key, phone in found.items() if phone.status == "failed" and phone.callsign]
        if not chosen:
            return []
        api = self._fleet()
        lines: list[str] = []
        try:
            certificate = api.certificate_template(self._team_id, self.key_alias)
            for key in chosen:
                api.resend(found[key].id, certificate)
                lines.append(f"Phone {key} ({found[key].callsign or 'no name'}): certificate asked for again")
            lines += self._policy_again(found)
        except FleetError as exc:
            raise _refused(exc, lines=lines) from exc
        return lines
