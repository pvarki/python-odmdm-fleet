"""What a deployment needs, said to FleetDM

Every request shape and every wait in here was measured against a live Fleet, and the comments
say which. None of it mentions a product: what to install arrives in the template.
"""

import dataclasses
import datetime
import http.client
import json
import logging
import secrets
import ssl
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urlparse

from . import __version__
from .icon import launcher_icon
from .template import MdmTemplate

LOGGER = logging.getLogger(__name__)

#: A web app is not installable until managed Play has published it, which is not synchronous
WEB_APP_PUBLISH_ATTEMPTS = 12
WEB_APP_PUBLISH_WAIT = 10.0


def _same_case(url: str) -> str:
    """A URL with its scheme and host lower-cased, as they compare without case"""
    parts = urlparse(url)
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower()).geturl()


class FleetError(RuntimeError):
    """Fleet refused, or answered something we cannot use"""


class Unauthorized(FleetError):
    """Fleet answered 401: it does not accept the token"""


#: The proxy the environment names, and HTTPS with certificates checked: nothing else. Without a
#: redirect handler a redirect comes back as an answer and is never followed, so the token goes
#: nowhere but the MDM URL; without the error handlers an answer of any status comes back as it is.
_OPENER = urllib.request.OpenerDirector()
_OPENER.add_handler(urllib.request.ProxyHandler())
_OPENER.add_handler(urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def _send(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> tuple[int, bytes]:
    """Send one request to the MDM and return its status and body, whatever the status"""
    if urlparse(url).scheme != "https":
        # The token goes with every request, so it must not cross the network readable
        raise FleetError("the MDM address must start with https://")
    try:
        request = urllib.request.Request(
            url, data=body, headers={**headers, "User-Agent": f"odmdm-connector/{__version__}"}, method=method
        )
        with _OPENER.open(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except ValueError:
        # Its text quotes the header it refused, the token with it
        raise FleetError("the MDM address or the token holds a character a request cannot carry") from None
    except urllib.error.URLError as exc:
        raise FleetError(f"could not reach the MDM: {exc.reason}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise FleetError(f"could not reach the MDM: {exc}") from exc


def _multipart(
    fields: dict[str, str], file_field: str, filename: str, payload: bytes, content_type: str
) -> tuple[str, bytes]:
    """A multipart/form-data body and its content type: the plain fields, then the file"""
    for name in (*fields, file_field, filename, content_type):
        if any(character in name for character in '"\r\n'):
            raise ValueError(f"{name!r} cannot go in a multipart header")
    boundary = secrets.token_hex(16)
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
        + payload
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


#: Said when Fleet answers 401. A token copied from a signed-in account is that sign-in's own, and Fleet ends it
#: after some days or at sign-out; an API-only user's token does not end.
UNAUTHORIZED = (
    "the API token is not accepted. A token copied from a signed-in account ends with that sign-in: "
    "give a new one, or an API-only user's, which does not end"
)


#: Fleet's hosts listing answers in pages; Fleet's page numbers start from 0
HOSTS_PER_PAGE = 500

#: Google refuses a managed web app title of 30 characters or more
MAX_WEB_APP_TITLE = 29

#: Named after the responder this was first applied from, and kept: Fleet knows a profile by its
#: name, so a new name would sit next to the old profile on every team instead of replacing it.
POLICY_NAME = "rmscep-policy"


@dataclasses.dataclass(frozen=True)
class Phone:
    """A device in the team, and how far its certificate has got"""

    id: int
    #: What the certificate's common name becomes: the name Fleet holds up to any "@". Empty while
    #: nobody has named the phone.
    callsign: str
    model: str
    enrolled: str
    #: The certificate row's status and Fleet's detail on it. No status: nothing was asked for.
    status: str
    detail: str


def _moment(value: str) -> datetime.datetime:
    """A time Fleet wrote, as a time

    Fleet writes a profile's created_at to the microsecond and its updated_at and a host's
    last_enrolled_at to the second, so as strings "...44Z" sorts after "...44.458685Z".
    """
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise FleetError(f"the time {value!r} could not be read") from exc


class FleetMdm:
    """What this feature needs an MDM to be able to do, in Fleet's words"""

    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        status, answer = _send(method, f"{self.base_url}{path}", headers, data, self.timeout)
        if status == 401:
            raise Unauthorized(UNAUTHORIZED)
        if not 200 <= status < 300:
            # Enough of the body to keep the reason intact: the retry below matches on it, and
            # truncating to a couple of hundred characters cut the phrase off mid-word.
            raise FleetError(f"{method} {path} answered {status}: {answer.decode('utf-8', 'replace')[:800]}")
        if not answer:
            return {}
        try:
            payload: dict[str, Any] = json.loads(answer)
        except ValueError as exc:
            raise FleetError(f"{method} {path} did not answer JSON") from exc
        return payload

    def _upload(
        self,
        path: str,
        fields: dict[str, str],
        filename: str,
        payload: bytes,
        file_field: str,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        form_type, form = _multipart(fields, file_field, filename, payload, content_type)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": form_type}
        status, answer = _send("POST", f"{self.base_url}{path}", headers, form, self.timeout)
        if status == 401:
            raise Unauthorized(UNAUTHORIZED)
        if not 200 <= status < 300:
            raise FleetError(f"upload to {path} answered {status}: {answer.decode('utf-8', 'replace')[:200]}")
        result: dict[str, Any] = json.loads(answer) if answer else {}
        return result

    # --- scopes ---------------------------------------------------------------

    def team_id(self, name: str, create: bool = False) -> int:
        """The team by name

        Only apply creates a team that is not there. Anywhere else an unknown name is a typo, and
        must not leave a team behind.
        """
        for team in self._call("GET", "/api/latest/fleet/teams").get("teams", []):
            if team.get("name") == name:
                return int(team["id"])
        if not create:
            raise FleetError(f"no team named {name}: check the team's name")
        created = self._call("POST", "/api/latest/fleet/teams", {"name": name})
        team = created.get("team", created)
        LOGGER.info("Created team %s", name)
        return int(team["id"])

    # --- what a device gets ---------------------------------------------------

    def _title_id(self, team: int, package: str) -> int | None:
        query = urlencode({"team_id": team, "available_for_install": "true"})
        titles = self._call("GET", f"/api/latest/fleet/software/titles?{query}").get("software_titles", [])
        for title in titles:
            if (title.get("app_store_app") or {}).get("app_store_id") == package:
                return int(title["id"])
        return None

    def assign_apps(self, team: int, packages: list[str], preinstall: list[str]) -> dict[str, int]:
        """Make the apps available, and install them at enrolment where that is possible

        Fleet hardcodes AVAILABLE for user-added Android apps -- FORCE_INSTALLED is reserved for
        its own agent -- so the setup experience is the only automatic path, and it applies at
        ENROLMENT. A host that has already joined will never auto-install; it has to enrol again.
        """
        ids: dict[str, int] = {}
        for package in packages:
            existing = self._title_id(team, package)
            if existing:
                ids[package] = existing
                continue
            body = {"app_store_id": package, "platform": "android", "team_id": team}
            for attempt in range(WEB_APP_PUBLISH_ATTEMPTS):
                try:
                    created = self._call("POST", "/api/latest/fleet/software/app_store_apps", body)
                    ids[package] = int(created["software_title_id"])
                    break
                except FleetError as exc:
                    # Apostrophes vary; match the part that does not.
                    if "available in Play Store" not in str(exc) or attempt == WEB_APP_PUBLISH_ATTEMPTS - 1:
                        raise
                    LOGGER.info("Waiting for managed Play to publish %s", package)
                    time.sleep(WEB_APP_PUBLISH_WAIT)
        chosen = sorted(ids[p] for p in preinstall if p in ids)
        if chosen:
            self._call(
                "PUT",
                "/api/latest/fleet/setup_experience/software",
                {"platform": "android", "team_id": team, "software_title_ids": chosen},
            )
        return ids

    def set_app_config(self, team: int, package: str, config: dict[str, Any]) -> None:
        title = self._title_id(team, package)
        if not title:
            raise FleetError(f"{package} is not assigned to team {team}; assign it before configuring it")
        self._call(
            "PATCH",
            f"/api/latest/fleet/software/titles/{title}/app_store_app",
            {"team_id": team, "configuration": {"managedConfiguration": config}},
        )

    def ensure_policy(self, team: int, fragment: dict[str, Any], name: str = POLICY_NAME, force: bool = False) -> None:
        """Android configuration profiles carry raw policy JSON

        There is no update in place, so changing one is delete then upload. Re-uploading an
        unchanged profile is not free: a policy rewritten on every pass never settles long enough
        for Play to finish an install. So Fleet's own copy is what gets compared, because this
        runs once per command and has nothing of its own to remember between runs.

        force exists because Fleet can overwrite a newly enrolled phone's policy with a bare one
        moments after sending ours, then shows ours as verified and never sends it again. Only a
        new upload reaches that phone. resend_policy makes one when a phone enrolled after the
        last upload.
        """
        profiles = self._call("GET", f"/api/latest/fleet/configuration_profiles?team_id={team}").get("profiles", [])
        here = [p for p in profiles if p.get("name") == name]
        if len(here) == 1 and not force and self._policy_in_fleet(here[0]["profile_uuid"]) == fragment:
            return
        try:
            for existing in here:
                self._call("DELETE", f"/api/latest/fleet/configuration_profiles/{existing['profile_uuid']}")
            self._upload(
                "/api/latest/fleet/configuration_profiles",
                {"team_id": str(team)},
                f"{name}.json",
                json.dumps(fragment, indent=2, sort_keys=True).encode("utf-8"),
                file_field="profile",
            )
        except FleetError as exc:
            if not here:
                raise
            # Between the delete and the upload the team has no policy, and every phone in it loses the settings.
            # A token refused meanwhile is still a token refused, which the operator is asked for again.
            kind = Unauthorized if isinstance(exc, Unauthorized) else FleetError
            raise kind(
                f"policy {name} was being replaced in team {team} and may now be missing ({exc}). "
                "Run apply to put it back"
            ) from exc
        LOGGER.info("Uploaded policy %s to team %s", name, team)

    def _policy_in_fleet(self, profile_uuid: str) -> dict[str, Any]:
        """The policy as Fleet holds it; without alt=media this answers with metadata only"""
        return self._call("GET", f"/api/latest/fleet/configuration_profiles/{profile_uuid}?alt=media")

    def resend_policy(self, team: int, phones: list[Phone], name: str = POLICY_NAME) -> int:
        """Upload the team's policy again if any of the phones enrolled after it was last sent

        Answers how many did, 0 when nothing was sent. Measured on Fleet 4.90.1: a phone that
        enrols is sent our policy, and seconds later Fleet can write one holding only its status
        reporting over it. Fleet goes on showing ours as verified there and never sends it again,
        so the phone installs its certificate and Chrome is not allowed to use it. The same
        policy uploaded again reaches the phone and leaves its certificate and apps alone.

        Fleet's own copy is what goes, so no template is needed. A phone that enrols again onto its
        old host keeps its old last_enrolled_at, and is not seen here: apply --force-policy reaches it.
        """
        profiles = self._call("GET", f"/api/latest/fleet/configuration_profiles?team_id={team}").get("profiles", [])
        profile = next((p for p in profiles if p.get("name") == name), None)
        if not profile:
            return 0
        sent = _moment(str(profile.get("updated_at") or profile.get("created_at") or ""))
        later = [phone for phone in phones if phone.enrolled and _moment(phone.enrolled) > sent]
        if later:
            self.ensure_policy(team, self._policy_in_fleet(profile["profile_uuid"]), name=name, force=True)
        return len(later)

    def ensure_link_app(self, team: int, title: str, url: str) -> str:
        """A managed web app, so the deployment has an icon on the launcher

        Looked up before publishing, because publishing twice does not yield one app. Google was
        expected to derive the package name from the url and so dedupe a repeat of the same url;
        it does not. One url published twice gave two package names, so every re-run left another
        launcher on the team and another permanent app in the enterprise, Fleet offering no way
        to delete one.

        So the deployment goes in the published title and the title is the identity -- its first
        DNS label rather than the whole host, because Google refuses a title of 30 characters or
        more; see _published_title. Fleet never hands a web app's url back, and the team does not
        identify a deployment either -- the original bug was a new deployment reusing the team
        name of the one it replaced, finding "Deploy App" already there, and handing every device
        an icon for the decommissioned host. Naming the deployment makes a re-run match and a
        move not match, which is exactly the distinction we cannot otherwise draw.

        Fleet lists a web app under its package name until a device reports the title, so apply()
        sets the display name straight after assigning it. A run that stops in between leaves an
        app this lookup cannot see; the conflict below says how to recover it.

        The icon is required rather than decorative -- without one Google never publishes the app
        and every device reports the install as NOT_FOUND.
        """
        title = self._published_title(title, url)
        published = self._link_app_in_team(team, title)
        if published:
            LOGGER.info("Launcher web app %s already published to team %s, reusing it", published, team)
            return published
        try:
            created = self._upload(
                "/api/latest/fleet/software/web_apps",
                {"title": title, "url": url},
                "icon.png",
                launcher_icon(),
                file_field="icon",
                content_type="image/png",
            )
        except FleetError as exc:
            # A conflict means the name is already published to the enterprise. Re-read the team,
            # because publishing is asynchronous and the lookup above can lose that race.
            #
            # There is nothing else to try. Checked against Fleet 4.90: a web app whose team was
            # deleted appears in no listing at all -- not unscoped, not under team_id=0 -- while
            # remaining published, because a web app cannot be deleted. So a recreated team
            # cannot get its launcher back and the name stays spent. Say so rather than retrying
            # somewhere it provably is not.
            if "409" not in str(exc) and "already exists" not in str(exc).lower():
                raise
            found = self._link_app_in_team(team, title)
            if not found:
                raise FleetError(
                    f"the MDM already has a web app named {title!r} but will not show it. A web app cannot be "
                    "deleted, and it outlives the team it was assigned to, so a team that was deleted and "
                    "recreated leaves the name taken. First look in this team for a web app with an empty "
                    "display name: a run that stopped between publishing and naming leaves one, and giving it "
                    f"the display name {title!r} in the MDM lets this run reuse it. If there is none, the name "
                    "is spent -- give this deployment a different launcher title, or a host of its own."
                ) from exc
            LOGGER.info("Launcher web app %s is already published as %r, reusing it", found, title)
            return found

        package = str(created.get("app_store_id") or created.get("package_name") or "")
        if not package:
            raise FleetError(f"the MDM did not name the web app it created: {created}")
        LOGGER.info("Launcher web app %s points at %s", package, url)
        return package

    @staticmethod
    def _published_title(title: str, url: str) -> str:
        """The title this deployment publishes under, carrying the deployment's name

        This is what the launcher icon reads on the device, so it is the deployment's name and
        not an identifier: one instance per unit means the deployment is the useful half of it.
        Its first DNS label, not the whole host, because Google refuses a web app title of 30
        characters or more and a full host rarely fits.
        """
        name = (urlparse(url).hostname or "").removeprefix("mtls.").split(".")[0]
        room = MAX_WEB_APP_TITLE - len(title) - len(" ()")
        if not name or room <= 0:
            return title[:MAX_WEB_APP_TITLE]
        return f"{title} ({name[:room]})"

    def _link_app_in_team(self, team: int, title: str) -> str:
        """This team's published web app of that name, or empty if it has none

        Scoped to the one team on purpose. Searching every team returns another deployment's app,
        whose url points at another deployment and cannot be read to find that out.
        """
        query = urlencode({"team_id": team, "available_for_install": "true"})
        for existing in self._call("GET", f"/api/latest/fleet/software/titles?{query}").get("software_titles", []):
            package = (existing.get("app_store_app") or {}).get("app_store_id", "")
            named = title in (existing.get("display_name"), existing.get("name"))
            if package.startswith("com.google.enterprise.webapp.") and named:
                return str(package)
        return ""

    def ensure_authority(self, name: str, scep_url: str, challenge: str) -> int:
        """The SCEP CA this deployment is, created if the MDM does not have it yet

        Pointing the MDM at a SCEP server is MDM configuration, not protocol, so there is no
        standard for it and every MDM words it differently. Fleet happens to expose it, so here
        it is one less thing for an operator to get wrong by hand.

        One authority of a name serves one Deploy App. An existing one with another URL is refused,
        never repointed: the shipped template names the same authority for every Deploy App, so a
        second Deploy App on the same Fleet would otherwise send the first one's phones to itself.
        One with this URL gets the challenge whenever there is one: RASENMAEHER makes a new one for
        every enrolment window, and Fleet never hands the old one back to compare against.
        """
        authorities = self._call("GET", "/api/latest/fleet/certificate_authorities").get("certificate_authorities", [])
        match = [a for a in authorities if a.get("name") == name]
        if match:
            authority_id = int(match[0]["id"])
            # The listing does not carry the url, only the detail does, so compare against the detail
            serves = self._call("GET", f"/api/latest/fleet/certificate_authorities/{authority_id}").get("url")
            if _same_case(serves or "") != _same_case(scep_url):
                raise FleetError(
                    f"certificate authority {name} already serves {serves or 'another address'}: connect this "
                    "Deploy App to a Fleet of its own, give it a template that names another authority, or delete "
                    "that authority in Fleet if the Deploy App moved"
                )
            if not challenge:
                return authority_id
            # The challenge is never logged. No name in the body: Fleet reads a name on a PATCH as
            # a rename and then refuses it as a conflict with the authority's own existing name,
            # so sending it turns every update into a 409.
            LOGGER.info("Giving certificate authority %s at %s the new challenge", name, scep_url)
            self._call(
                "PATCH",
                f"/api/latest/fleet/certificate_authorities/{authority_id}",
                {"custom_scep_proxy": {"url": scep_url, "challenge": challenge}},
            )
            return authority_id

        LOGGER.info("Creating certificate authority %s -> %s", name, scep_url)
        created = self._call(
            "POST",
            "/api/latest/fleet/certificate_authorities",
            {"custom_scep_proxy": {"name": name, "url": scep_url, "challenge": challenge}},
        )
        body = created.get("certificate_authority") or created
        return int(body["id"])

    def ensure_certificate(self, team: int, name: str, subject: str, authority: str) -> int:
        """The template the MDM fills in per device, and asks us to sign

        There is no update in place, so a changed subject is delete then create. The name is kept
        because it becomes the Android keystore alias, and a device already holding a key under one
        alias will not accept a second.

        Refused while the team has devices. Replacing the template makes each of them delete the
        certificate it has and ask again, and the deployment refuses a name that is already taken,
        so all of them would lose access (measured on Fleet 4.90).
        """
        authorities = self._call("GET", "/api/latest/fleet/certificate_authorities").get("certificate_authorities", [])
        match = [a for a in authorities if a.get("name") == authority]
        if not match:
            known = ", ".join(sorted(str(a.get("name")) for a in authorities)) or "none"
            raise FleetError(f"no certificate authority named {authority!r}; the MDM has: {known}")
        authority_id = int(match[0]["id"])

        existing = self._call("GET", f"/api/latest/fleet/certificates?team_id={team}").get("certificates", [])
        for template in existing:
            if template.get("name") != name:
                continue
            if (
                template.get("subject_name") == subject
                and int(template.get("certificate_authority_id", 0)) == authority_id
            ):
                return int(template["id"])
            devices = len(self._hosts(team))
            if devices:
                raise FleetError(
                    f"replacing certificate template {name} would delete the certificate of {devices} devices in "
                    f"team {team}, and their names would be refused when they ask again, so nothing was changed. "
                    "Use a new team, or empty this one first."
                )
            self._call("DELETE", f"/api/latest/fleet/certificates/{template['id']}?team_id={team}")
            LOGGER.info("Replacing certificate template %s", name)

        created = self._call(
            "POST",
            "/api/latest/fleet/certificates",
            {"name": name, "team_id": team, "certificate_authority_id": authority_id, "subject_name": subject},
        )
        LOGGER.info("Certificate template %s asks for %s", name, subject)
        return int(created["id"])

    # --- naming ---------------------------------------------------------------

    def _hosts(self, team: int) -> list[dict[str, Any]]:
        """The devices in a team, every page of them"""
        found: list[dict[str, Any]] = []
        page = 0
        while True:
            query = urlencode({"team_id": team, "page": page, "per_page": HOSTS_PER_PAGE})
            batch = self._call("GET", f"/api/latest/fleet/hosts?{query}").get("hosts") or []
            found.extend(batch)
            if len(batch) < HOSTS_PER_PAGE:
                return found
            page += 1

    def phones(self, team: int, certificate: str) -> list[Phone]:
        """The team's devices in enrolment order, each with its name and its certificate row

        The row is the install of the certificate template called `certificate`. A host can show
        an old remove row of the same name beside the new one for a while, and that one is history.
        """
        found = []
        for listed in self._hosts(team):
            host = self._call("GET", f"/api/latest/fleet/hosts/{listed['id']}").get("host") or {}
            users = host.get("end_users") or []
            name = next((str(user["idp_username"]) for user in users if user.get("idp_username")), "")
            rows = (host.get("mdm") or {}).get("profiles") or []
            row: dict[str, Any] = next(
                (r for r in rows if r.get("name") == certificate and r.get("operation_type") == "install"), {}
            )
            found.append(
                Phone(
                    id=int(listed["id"]),
                    callsign=name.split("@")[0],
                    model=str(host.get("hardware_model") or ""),
                    enrolled=str(host.get("last_enrolled_at") or ""),
                    status=str(row.get("status") or ""),
                    detail=str(row.get("detail") or ""),
                )
            )
        return sorted(found, key=lambda phone: (phone.enrolled, phone.id))

    def certificate_template(self, team: int, name: str) -> int:
        """The id of the team's certificate template of that name"""
        for template in self._call("GET", f"/api/latest/fleet/certificates?team_id={team}").get("certificates", []):
            if template.get("name") == name:
                return int(template["id"])
        raise FleetError(f"team {team} has no certificate template named {name}: set Fleet up first")

    def name_host(self, host: int, callsign: str) -> None:
        """Give the host its callsign

        A phone enrols before anybody can name it, so its first request has already failed for
        want of a name, and naming it does not send that request again by itself: follow with
        resend. Not for a host with no request yet: Fleet has none to send again (404, read in
        Fleet 4.90.1's source), and fills the name in when it makes one.
        """
        self._call("PUT", f"/api/latest/fleet/hosts/{host}/device_mapping", {"email": callsign, "source": "idp"})

    def resend(self, host: int, template: int) -> None:
        """Have Fleet send the host its certificate request again, for one more attempt

        The phone acts on it at its next check-in. Fleet refuses a request still pending (400), and
        that one needs nothing: it is filled in when it goes, with the name the host has by then.
        Measured on Fleet 4.90.1.
        """
        try:
            self._call("POST", f"/api/latest/fleet/hosts/{host}/certificates/{template}/resend")
        except FleetError as exc:
            if "answered 400" not in str(exc) or "pending" not in str(exc):
                raise

    # --- the whole statement --------------------------------------------------

    def apply(
        self,
        team_name: str,
        template: MdmTemplate,
        force_policy: bool = False,
        scep_url: str = "",
        challenge: str = "",
    ) -> dict[str, Any]:
        """State everything the template says, in the order that actually works

        The policy goes in BEFORE the apps: a permission grant only applies to an app that
        installs while it is already in force, and the key selection rules are what make the
        certificate visible to anything at all. Both have to be true before a device joins,
        because that is the only moment apps install.

        Given scep_url, the certificate authority is created too, so a fresh deployment needs
        nothing typed into Fleet by hand. Without it the authority must already be there, which
        is the case for an MDM we cannot create one in.
        """
        # The authority first: refusing it must leave Fleet as it was, a new team included
        if template.certificate and scep_url:
            self.ensure_authority(template.certificate.authority, scep_url, challenge)
        team = self.team_id(team_name, create=True)
        if template.certificate:
            self.ensure_certificate(
                team,
                template.certificate.name,
                template.certificate.subject,
                template.certificate.authority,
            )
        self.ensure_policy(team, template.policy, force=force_policy)

        packages = [app.package for app in template.apps]
        preinstall = list(template.preinstall_packages)
        if template.link_app:
            link = self.ensure_link_app(team, template.link_app.title, template.link_app.url)
            packages.append(link)
            preinstall.append(link)

        assigned = self.assign_apps(team, packages, preinstall)
        if template.link_app:
            # Fleet lists a new web app under its package name until a device reports it, and the
            # lookup that keeps a re-run from publishing another permanent app goes by title.
            self._call(
                "PATCH",
                f"/api/latest/fleet/software/titles/{assigned[link]}/app_store_app",
                {
                    "team_id": team,
                    "display_name": self._published_title(template.link_app.title, template.link_app.url),
                },
            )
        for app in template.apps:
            if app.config:
                self.set_app_config(team, app.package, app.config)
        return {
            "team_id": team,
            "assigned": sorted(assigned),
            "preinstall": sorted(preinstall),
        }
