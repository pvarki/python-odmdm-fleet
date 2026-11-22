"""The MDM dialect, against a stubbed transport

What matters here is the ORDER and the restraint: the policy has to be in force before anything
installs, and an unchanged policy must not be re-uploaded, because a policy rewritten on every
pass never settles long enough for the device to finish installing anything.
"""

import dataclasses
import http.client
import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from typing import Any, Self
from urllib.parse import parse_qsl, urlsplit

import pytest

from odmdm_connector import __version__
from odmdm_connector import fleet as fleet_module
from odmdm_connector.fleet import FleetError, FleetMdm, authority_name
from odmdm_connector.icon import launcher_icon
from odmdm_connector.template import App, Certificate, LinkApp, MdmTemplate

TEMPLATE = MdmTemplate(
    apps=(
        App(package="com.example.one", preinstall=True, config={"k": "v"}),
        App(package="com.example.two", preinstall=False),
    ),
    policy={"defaultPermissionPolicy": "GRANT"},
)


@dataclasses.dataclass(frozen=True)
class Url:
    path: str
    params: dict[str, str]


@dataclasses.dataclass(frozen=True)
class Request:
    """What the fake Fleet reads of a request"""

    method: str
    url: Url
    headers: dict[str, str]
    content: bytes


_dumps = json.dumps


class Response:
    """An answer of the fake Fleet, read as the real opener's answer is"""

    def __init__(self, status: int, json: Any = None, text: str = "") -> None:
        self.status = status
        self.content = _dumps(json).encode() if json is not None else text.encode()

    def read(self) -> bytes:
        return self.content

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Fleet:
    """A Fleet that records what it was told, in order"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.titles: list[dict[str, Any]] = []
        self.profiles: list[dict[str, Any]] = []
        #: When an upload lands. Fleet lists a profile's created_at to the microsecond and its
        #: updated_at to the second (a live Fleet 4.90.1).
        self.now = "2026-11-20T21:02:44.458685Z"
        self.certificates: list[dict[str, Any]] = []
        self.hosts: list[dict[str, Any]] = []
        self.web_apps: list[dict[str, Any]] = []
        self.authorities: list[dict[str, Any]] = [{"id": 3, "name": "RMSCEP", "url": "https://deployment.test/scep"}]
        self.teams: list[dict[str, Any]] = [{"id": 7, "name": "rmscep"}]
        self._next_id = 100

    def handler(self, request: Request) -> Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/hosts") and request.method == "GET":
            # Fleet answers one page at a time, the first being page 0
            per_page = int(request.url.params.get("per_page", "100"))
            first = int(request.url.params.get("page", "0")) * per_page
            return Response(200, json={"hosts": [{"id": h["id"]} for h in self.hosts[first : first + per_page]]})
        if path.endswith("/device_mapping") and request.method == "PUT":
            # The name shows in the host detail as its IdP user, which is what the certificate reads
            found = next(h for h in self.hosts if h["id"] == int(path.split("/")[-2]))
            found["end_users"] = [{"idp_username": json.loads(request.content)["email"], "idp_info_updated_at": None}]
            return Response(200, json={})
        if path.endswith("/resend") and request.method == "POST":
            # Fleet finds the template through the host's own row, so a host without one is not
            # found (read in Fleet 4.90.1's source). A row still pending is refused, and any other
            # starts over as pending; the answer has no body (both seen on a live Fleet 4.90.1).
            found = next(h for h in self.hosts if h["id"] == int(path.split("/")[-4]))
            template = int(path.split("/")[-2])
            rows = [
                r
                for r in found["mdm"]["profiles"]
                if r.get("certificate_template_id") == template and r["operation_type"] == "install"
            ]
            if not rows:
                return Response(404, json={"message": "Resource Not Found"})
            if rows[0]["status"] == "pending":
                return Response(
                    400,
                    json={
                        "message": "Bad request",
                        "errors": [{"name": "base", "reason": "Couldn't resend pending certificate template."}],
                    },
                )
            rows[0].update(status="pending", detail="")
            return Response(200)
        if "/hosts/" in path and request.method == "GET":
            which = int(path.rsplit("/", 1)[-1])
            return Response(200, json={"host": next(h for h in self.hosts if h["id"] == which)})
        if path.endswith("/certificate_authorities") and request.method == "GET":
            # Fleet's listing omits the url; only the detail carries it. Mirrored so a check
            # that reads the url off the listing fails here too.
            return Response(
                200,
                json={
                    "certificate_authorities": [{k: v for k, v in a.items() if k != "url"} for a in self.authorities]
                },
            )
        if "/certificate_authorities/" in path and request.method == "GET":
            which = int(path.rsplit("/", 1)[-1])
            matching = [a for a in self.authorities if a["id"] == which]
            return Response(200, json=matching[0] if matching else {})
        if path.endswith("/certificate_authorities") and request.method == "POST":
            body = json.loads(request.content)["custom_scep_proxy"]
            self._next_id += 1
            made = {"id": self._next_id, "name": body["name"], "url": body["url"], "challenge": body["challenge"]}
            self.authorities.append(made)
            return Response(200, json={"certificate_authority": made})
        if "/certificate_authorities/" in path and request.method == "PATCH":
            which = int(path.rsplit("/", 1)[-1])
            body = json.loads(request.content)["custom_scep_proxy"]
            if "name" in body:
                # Fleet reads a name on a PATCH as a rename and refuses it as a conflict with
                # the authority's own name. Measured against a live Fleet 4.90.1.
                return Response(409, json={"message": "Conflict"})
            for authority in self.authorities:
                if authority["id"] == which:
                    authority.update(body)
            return Response(200, json={})
        if path.endswith("/certificates") and request.method == "GET":
            # A team's own templates; one a test put there without a team is every team's
            team = int(request.url.params.get("team_id", "0"))
            return Response(
                200, json={"certificates": [c for c in self.certificates if c.get("team_id", team) == team]}
            )
        if path.endswith("/certificates") and request.method == "POST":
            body = json.loads(request.content)
            self._next_id += 1
            made = {
                "id": self._next_id,
                "name": body["name"],
                "team_id": body["team_id"],
                "subject_name": body["subject_name"],
                "certificate_authority_id": body["certificate_authority_id"],
            }
            self.certificates.append(made)
            return Response(200, json=made)
        if "/certificates/" in path and request.method == "DELETE":
            gone = int(path.rsplit("/", 1)[-1])
            self.certificates = [c for c in self.certificates if c["id"] != gone]
            return Response(200, json={})
        if path == "/api/latest/fleet/teams" and request.method == "GET":
            return Response(200, json={"teams": self.teams})
        if path == "/api/latest/fleet/teams" and request.method == "POST":
            self._next_id += 1
            made = {"id": self._next_id, "name": json.loads(request.content)["name"]}
            self.teams.append(made)
            return Response(200, json={"team": made})
        if path.endswith("/software/titles"):
            # Fleet scopes the listing to one team. A stub that ignored team_id would let a
            # lookup find another team's app and call that a pass.
            wanted_team = request.url.params.get("team_id")
            listed = [t for t in self.titles if wanted_team is None or str(t.get("team_id")) == str(wanted_team)]
            return Response(200, json={"software_titles": listed})
        if path.endswith("/software/web_apps"):
            # Publishing the SAME url twice yields TWO package names: observed against the real
            # Fleet, where one url gave x023e... and then xb017.... Google does not dedupe by
            # url, which is what makes every re-run leave a permanent orphan behind.
            body = request.content.decode("utf-8", "replace")
            url_match = re.search(r'name="url"\r?\n\r?\n([^\r\n]+)', body)
            title_match = re.search(r'name="title"\r?\n\r?\n([^\r\n]+)', body)
            wanted = url_match.group(1) if url_match else ""
            asked = title_match.group(1) if title_match else ""
            if len(asked) >= 30:
                # Google refuses a web app title of 30 characters or more, as a bare 400.
                return Response(400, json={"message": "Couldn't create. Please check the provided data"})
            if any(a["title"] == asked for a in self.web_apps):
                # Fleet refuses a name already published to the enterprise, whatever team it is
                # in and even if its team is gone. Publishing cannot be undone.
                return Response(409, json={"message": "Web app with this name already exists in this fleet"})
            self._next_id += 1
            pkg = f"com.google.enterprise.webapp.x{self._next_id:08x}"
            self.web_apps.append(
                {"app_store_id": pkg, "url": wanted, "title": title_match.group(1) if title_match else ""}
            )
            return Response(200, json={"app_store_id": pkg})
        if path.endswith("/app_store_app") and request.method == "PATCH":
            body = json.loads(request.content)
            title_id = int(path.split("/")[-2])
            for title in self.titles:
                if title["id"] == title_id and "display_name" in body:
                    title["display_name"] = body["display_name"]
            return Response(200, json={"app_store_app": {}})
        if path.endswith("/software/app_store_apps"):
            # This is what puts an app in a team. A new web app is listed under its package name,
            # not its title, until a device reports it: seen on the real Fleet.
            body = json.loads(request.content)
            self._next_id += 1
            self.titles.append(
                {
                    "id": self._next_id,
                    "name": body["app_store_id"],
                    "display_name": "",
                    "team_id": body.get("team_id"),
                    "app_store_app": {"app_store_id": body["app_store_id"]},
                }
            )
            return Response(200, json={"software_title_id": self._next_id})
        if path.endswith("/configuration_profiles"):
            if request.method == "GET":
                return Response(
                    200, json={"profiles": [{k: v for k, v in p.items() if k != "raw"} for p in self.profiles]}
                )
            # An upload the MDM keeps, so a second pass can see it is already there. Without
            # this the adapter is right to upload again: a profile that has gone missing must
            # be restored whatever we remember pushing.
            raw = re.search(rb"application/json\r\n\r\n(.*)\r\n--", request.content, re.DOTALL)
            assert raw, "the policy must be uploaded as JSON"
            self.profiles.append(
                {
                    "name": "rmscep-policy",
                    "profile_uuid": f"uuid-{len(self.profiles)}",
                    "created_at": self.now,
                    "updated_at": self.now.split(".")[0] + "Z",
                    "raw": json.loads(raw.group(1)),
                }
            )
            return Response(200, json={})
        if "/configuration_profiles/" in path and request.method == "GET":
            # Only alt=media answers with the policy; without it Fleet sends metadata.
            found = next(p for p in self.profiles if p["profile_uuid"] == path.rsplit("/", 1)[-1])
            media = request.url.params.get("alt") == "media"
            return Response(200, json=found["raw"] if media else {"name": found["name"]})
        if "/configuration_profiles/" in path and request.method == "DELETE":
            self.profiles.clear()
            return Response(200, json={})
        if "/app_store_app" in path or path.endswith("/setup_experience/software"):
            return Response(200, json={})
        return Response(200, json={})


class _Opener:
    """The opener, answering with a handler instead of the network"""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.sent: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> Any:
        self.sent.append(request)
        parts = urlsplit(request.full_url)
        return self.handler(
            Request(
                request.get_method(),
                Url(parts.path, dict(parse_qsl(parts.query))),
                {name.lower(): value for name, value in request.header_items()},
                request.data if isinstance(request.data, bytes) else b"",
            )
        )


def _answering(handler: Any, monkeypatch: pytest.MonkeyPatch) -> _Opener:
    opener = _Opener(handler)
    monkeypatch.setattr(fleet_module, "_OPENER", opener)
    return opener


def _mdm(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch) -> FleetMdm:
    # Looked up on every request, so a test can swap the handler after this
    _answering(lambda request: fleet.handler(request), monkeypatch)
    return FleetMdm("https://mdm.test", "token")


def test_the_policy_lands_before_anything_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A permission grant only applies to an app that installs while it is already in force

    Land it late and the device comes up facing a permission wizard, with a certificate no app
    is allowed to see.
    """
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("rmscep", TEMPLATE)

    paths = [p for _, p in fleet.calls]
    profile = next(i for i, p in enumerate(paths) if p.endswith("/configuration_profiles"))
    install = next(i for i, p in enumerate(paths) if p.endswith("/software/app_store_apps"))
    assert profile < install, f"policy must precede installs, got {paths}"


def test_only_the_chosen_apps_install_at_enrolment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rest stay available on demand rather than lengthening the enrolment burst"""
    fleet = _Fleet()
    result = _mdm(fleet, monkeypatch).apply("rmscep", TEMPLATE)
    assert result["preinstall"] == ["com.example.one"]
    assert len(result["assigned"]) == 2


def _policy_uploads(fleet: _Fleet) -> int:
    return len([1 for m, p in fleet.calls if m == "POST" and p.endswith("/configuration_profiles")])


def test_an_unchanged_policy_is_not_rewritten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured: a policy rewritten every pass never settles and nothing ever installs

    Each run of the command is a new process, so nothing from the last run can be remembered.
    """
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("rmscep", TEMPLATE)
    uploads_after_first = _policy_uploads(fleet)
    _mdm(fleet, monkeypatch).apply("rmscep", TEMPLATE)
    assert _policy_uploads(fleet) == uploads_after_first, "the same policy must not be uploaded twice"


def test_a_changed_policy_replaces_the_old_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise an edited template would look applied and change nothing on a phone"""
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("rmscep", TEMPLATE)
    changed = MdmTemplate(apps=TEMPLATE.apps, policy={"defaultPermissionPolicy": "PROMPT"})
    _mdm(fleet, monkeypatch).apply("rmscep", changed)
    assert [p["raw"] for p in fleet.profiles] == [changed.policy]


def test_force_reaches_a_host_that_just_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fleet can overwrite a new host's policy with a bare one, and never sends ours again by itself"""
    fleet = _Fleet()
    mdm = _mdm(fleet, monkeypatch)
    mdm.apply("rmscep", TEMPLATE)
    before = _policy_uploads(fleet)
    mdm.apply("rmscep", TEMPLATE, force_policy=True)
    assert _policy_uploads(fleet) == before + 1


def test_a_refusal_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Applying half a template is worse than applying none"""
    _answering(lambda request: Response(403, text="nope"), monkeypatch)
    with pytest.raises(FleetError):
        FleetMdm("https://mdm.test", "token").apply("rmscep", TEMPLATE)


@pytest.mark.parametrize("upload", [False, True])
def test_a_token_fleet_does_not_accept_is_said_in_plain_words(monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    """A token from a signed-in account ends with that sign-in, days later: the operator must be told what to do"""
    _answering(lambda request: Response(401, json={"message": "Authentication required"}), monkeypatch)
    mdm = FleetMdm("https://mdm.test", "token")

    with pytest.raises(FleetError, match="the API token is not accepted.*give a new one") as raised:
        if upload:
            mdm._upload("/api/latest/fleet/configuration_profiles", {}, "policy.json", b"{}", "profile")
        else:
            mdm.team_id("Deploy App")

    assert "Authentication required" not in str(raised.value)


def test_the_launcher_icon_is_a_valid_png() -> None:
    """Without one the web app is accepted and then never published, and every install says NOT_FOUND"""
    png = launcher_icon()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert width == height == 512, "square and at least 512x512 is enforced"


def test_the_link_app_is_offered_alongside_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is a browser shortcut, so it only works if the browser is installed too"""
    fleet = _Fleet()
    monkeypatch.setattr(FleetMdm, "ensure_link_app", lambda self, team, title, url: "com.google.enterprise.webapp.test")
    with_link = MdmTemplate(apps=TEMPLATE.apps, policy=TEMPLATE.policy, link_app=LinkApp("Deploy App", "https://x/"))
    result = _mdm(fleet, monkeypatch).apply("rmscep", with_link)
    assert "com.google.enterprise.webapp.test" in result["preinstall"]


def test_the_certificate_template_is_stated_before_anything_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its subject names the device, which becomes its callsign

    So it is configuration that gets reviewed and deployed, not something typed into a console.
    """
    fleet = _Fleet()
    with_cert = MdmTemplate(
        apps=TEMPLATE.apps,
        policy=TEMPLATE.policy,
        certificate=Certificate(name="rmscep", subject="CN=$VAR,OU=$OTHER", authority="RMSCEP"),
    )
    _mdm(fleet, monkeypatch).apply("rmscep", with_cert)
    paths = [p for _, p in fleet.calls]
    assert any(p.endswith("/certificates") for p in paths), "the template must be stated"
    cert = next(i for i, p in enumerate(paths) if p.endswith("/certificates"))
    install = next(i for i, p in enumerate(paths) if p.endswith("/software/app_store_apps"))
    assert cert < install


def test_an_unknown_authority_is_named_rather_than_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A template pointing at an authority that is not there would issue nothing, silently"""
    fleet = _Fleet()
    with_cert = MdmTemplate(
        apps=TEMPLATE.apps,
        policy=TEMPLATE.policy,
        certificate=Certificate(name="rmscep", subject="CN=$VAR", authority="NOT-THERE"),
    )
    with pytest.raises(FleetError, match="NOT-THERE"):
        _mdm(fleet, monkeypatch).apply("rmscep", with_cert)


def test_an_unchanged_certificate_template_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recreating it re-asks every device in the team, so it is not done for nothing"""
    fleet = _Fleet()
    with_cert = MdmTemplate(
        apps=TEMPLATE.apps,
        policy=TEMPLATE.policy,
        certificate=Certificate(name="rmscep", subject="CN=$VAR,OU=$OTHER", authority="RMSCEP"),
    )
    mdm = _mdm(fleet, monkeypatch)
    mdm.apply("rmscep", with_cert)
    before = len([1 for method, path in fleet.calls if path.endswith("/certificates") and method == "POST"])
    mdm.apply("rmscep", with_cert)
    after = len([1 for method, path in fleet.calls if path.endswith("/certificates") and method == "POST"])
    assert after == before, "an unchanged template must not be recreated"


def test_a_changed_template_on_a_team_without_devices_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no update in place, and the name has to survive as the keystore alias"""
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert())
    changed = MdmTemplate(
        apps=TEMPLATE.apps,
        policy=TEMPLATE.policy,
        certificate=Certificate(name="rmscep", subject="CN=$OTHER", authority="RMSCEP"),
    )
    _mdm(fleet, monkeypatch).apply("rmscep", changed)

    assert [(c["name"], c["subject_name"]) for c in fleet.certificates] == [("rmscep", "CN=$OTHER")]


def test_a_changed_template_on_a_team_with_devices_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured: every device deletes its certificate, asks again, and is refused its own name"""
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert())
    fleet.hosts = [_host(1, "verified", "OTTER1")]
    fleet.calls.clear()
    changed = MdmTemplate(
        apps=TEMPLATE.apps,
        policy=TEMPLATE.policy,
        certificate=Certificate(name="rmscep", subject="CN=$OTHER", authority="RMSCEP"),
    )

    with pytest.raises(FleetError, match="1 devices in team 7"):
        _mdm(fleet, monkeypatch).apply("rmscep", changed)

    assert not [call for call in fleet.calls if call[0] == "DELETE"]
    assert fleet.certificates[0]["subject_name"] == "CN=$VAR,OU=$OTHER"


def _host(
    host_id: int, status: str = "", name: str = "", detail: str = "", enrolled: str = "2026-11-01T19:38:12Z"
) -> dict[str, Any]:
    """A phone as Fleet's host detail shows it, its certificate row left out when there is no status

    The policy's row is there too, so that only the certificate's row is read as the certificate.
    """
    rows: list[dict[str, Any]] = [
        {"profile_uuid": "g4bb", "name": "rmscep-policy", "status": "verified", "operation_type": "install"}
    ]
    if status:
        rows.append(
            {
                "profile_uuid": "fleet-host-certificate-template",
                "name": "rmscep",
                "status": status,
                "operation_type": "install",
                "detail": detail,
                "certificate_template_id": 81,
            }
        )
    return {
        "id": host_id,
        "hardware_model": "Google sdk_gphone64_arm64",
        "last_enrolled_at": enrolled,
        "end_users": [{"idp_username": name, "idp_info_updated_at": None}] if name else [],
        "mdm": {"profiles": rows},
    }


def test_a_resend_refused_for_another_reason_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a request still pending needs nothing; a host with no request at all is not found"""
    fleet = _Fleet()
    fleet.hosts = [_host(31)]

    with pytest.raises(FleetError, match="answered 404"):
        _mdm(fleet, monkeypatch).resend(31, 81)


def _with_cert(authority: str = "RMSCEP") -> MdmTemplate:
    """A template that asks for a certificate, which is what drags the authority in"""
    return MdmTemplate(
        apps=TEMPLATE.apps,
        policy=TEMPLATE.policy,
        certificate=Certificate(name="rmscep", subject="CN=$VAR,OU=$OTHER", authority=authority),
    )


def test_a_fresh_mdm_gets_the_authority_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pointing the MDM at us is configuration, and Fleet lets us do it, so nobody should type it

    Without this a deployment is not set up by running one command: it is one command plus a
    console, and the console step is the one people get wrong.
    """
    fleet = _Fleet()
    fleet.authorities = []
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(), scep_url="https://new.test/scep", challenge="not-a-secret")
    created = [c for c in fleet.calls if c == ("POST", "/api/latest/fleet/certificate_authorities")]
    assert created, "the authority should have been created"
    assert fleet.authorities[0]["url"] == "https://new.test/scep"


def test_an_authority_already_pointing_at_us_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a challenge to hand over, rewriting it is churn on something every device depends on"""
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(), scep_url="https://deployment.test/scep")
    touched = [c for c in fleet.calls if c[0] in ("POST", "PATCH") and "certificate_authorities" in c[1]]
    assert not touched, "an authority already pointing at us must not be rewritten"


def test_a_new_windows_challenge_reaches_an_authority_already_pointing_at_us(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every enrolment window has a new challenge, and Fleet never shows the old one to compare"""
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply(
        "rmscep", _with_cert(), scep_url="https://deployment.test/scep", challenge="new-window"
    )
    assert len(fleet.authorities) == 1
    assert fleet.authorities[0]["challenge"] == "new-window"
    assert fleet.authorities[0]["url"] == "https://deployment.test/scep"


def test_an_authority_is_named_after_its_deploy_app() -> None:
    """Fleet takes letters, digits and underscores only"""
    assert authority_name("https://golden-monkey.example.fi/scep") == "ODMDM_GOLDEN_MONKEY"
    assert authority_name("https://North.example.fi/scep") == "ODMDM_NORTH"


def test_a_second_deploy_app_gets_an_authority_and_a_team_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """One Fleet serves several Deploy Apps, and the first one's authority, team and template stay as they are"""
    fleet = _Fleet()
    mdm = _mdm(fleet, monkeypatch)
    mdm.apply("rmscep", _with_cert(""), scep_url="https://deployment.test/scep", challenge="first")
    first = [dict(c) for c in fleet.certificates]

    mdm.apply("second DeployApp", _with_cert(""), scep_url="https://second.test/scep", challenge="second")

    assert [(a["name"], a["url"], a["challenge"]) for a in fleet.authorities] == [
        ("RMSCEP", "https://deployment.test/scep", "first"),
        ("ODMDM_SECOND", "https://second.test/scep", "second"),
    ]
    assert [team["name"] for team in fleet.teams] == ["rmscep", "second DeployApp"]
    assert fleet.certificates[: len(first)] == first
    [made] = fleet.certificates[len(first) :]
    assert (made["team_id"], made["certificate_authority_id"]) == (fleet.teams[1]["id"], fleet.authorities[1]["id"])


def test_an_authority_under_the_old_shared_name_is_found_by_its_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Deploy App's authority was once RMSCEP: a template that names none must not make a second one

    A second one would get the team's template replaced, and every phone asked again under a taken name.
    """
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(""), scep_url="https://deployment.test/scep", challenge="c9")

    assert [(a["id"], a["name"], a["challenge"]) for a in fleet.authorities] == [(3, "RMSCEP", "c9")]
    assert [c["certificate_authority_id"] for c in fleet.certificates] == [3]


def test_an_authority_made_by_hand_is_found_by_its_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever it was called in Fleet's console; another Deploy App's is asked and left alone"""
    fleet = _Fleet()
    fleet.authorities = [
        {"id": 4, "name": "OTHER_UNIT", "url": "https://other.test/scep"},
        {"id": 9, "name": "Typed_by_hand", "url": "https://deployment.test/scep"},
    ]
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(""), scep_url="https://deployment.test/scep", challenge="c9")

    assert [a.get("challenge") for a in fleet.authorities] == [None, "c9"]
    assert [c["certificate_authority_id"] for c in fleet.certificates] == [9]


def test_the_likely_names_are_asked_for_their_address_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listing has no address, so each one costs a request: another Deploy App's is not asked"""
    fleet = _Fleet()
    fleet.authorities.insert(0, {"id": 4, "name": "OTHER_UNIT", "url": "https://other.test/scep"})
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(""), scep_url="https://deployment.test/scep")

    assert ("GET", "/api/latest/fleet/certificate_authorities/3") in fleet.calls
    assert ("GET", "/api/latest/fleet/certificate_authorities/4") not in fleet.calls


def test_an_authority_of_another_kind_has_no_address_to_ask_for(monkeypatch: pytest.MonkeyPatch) -> None:
    fleet = _Fleet()
    fleet.authorities = [{"id": 2, "name": "DIGI", "type": "digicert"}]
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(""), scep_url="https://deployment.test/scep", challenge="c")

    assert ("GET", "/api/latest/fleet/certificate_authorities/2") not in fleet.calls
    assert [a["name"] for a in fleet.authorities] == ["DIGI", "ODMDM_DEPLOYMENT"]


def test_a_template_may_name_a_new_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    """For two Deploy Apps whose hosts begin alike"""
    fleet = _Fleet()
    _mdm(fleet, monkeypatch).apply("north", _with_cert("NORTH_2"), scep_url="https://north.other.test/scep")

    assert [(a["name"], a["url"]) for a in fleet.authorities[1:]] == [("NORTH_2", "https://north.other.test/scep")]


def test_a_name_that_serves_another_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two Deploy Apps whose hosts begin alike would share a name, and it is never repointed"""
    fleet = _Fleet()
    fleet.authorities = [{"id": 4, "name": "ODMDM_NORTH", "url": "https://north.example.fi/scep"}]
    with pytest.raises(FleetError) as raised:
        _mdm(fleet, monkeypatch).apply("north", _with_cert(""), scep_url="https://north.other.test/scep", challenge="c")

    assert str(raised.value) == (
        "certificate authority ODMDM_NORTH already serves https://north.example.fi/scep: give this Deploy App a "
        "template that names another authority, or delete that authority in Fleet if the Deploy App moved"
    )
    assert not [c for c in fleet.calls if c[0] != "GET"], "nothing changed in Fleet"


def test_a_team_of_another_deploy_app_is_refused_even_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing its template would enrol that Deploy App's phones into this one"""
    fleet = _Fleet()
    mdm = _mdm(fleet, monkeypatch)
    mdm.apply("rmscep", _with_cert(""), scep_url="https://deployment.test/scep")
    before = [dict(c) for c in fleet.certificates]
    fleet.calls.clear()

    with pytest.raises(FleetError) as raised:
        mdm.apply("rmscep", _with_cert(""), scep_url="https://second.test/scep", challenge="c2")

    assert str(raised.value) == (
        "the team takes its certificates from authority RMSCEP, which serves https://deployment.test/scep, not "
        "https://second.test/scep, so nothing was changed. If that authority is this Deploy App's, correct its "
        "address in Fleet; if not, choose another team."
    )
    assert fleet.certificates == before
    assert fleet.authorities == [{"id": 3, "name": "RMSCEP", "url": "https://deployment.test/scep"}]
    assert not [c for c in fleet.calls if c[0] != "GET"], "nothing changed in Fleet, no authority made beside it"


@pytest.mark.parametrize(
    "held", ["https://deployment.test/scep/", "https://deployment.test:443/scep", "https://deployment.test/SCEP"]
)
def test_an_address_that_differs_by_little_is_refused_aloud(monkeypatch: pytest.MonkeyPatch, held: str) -> None:
    """An authority typed by hand with a slash too many is not this address

    Making a new one beside it would give that one the challenge, and the team's phones, which ask
    the one their template is bound to, would be refused with nothing to show why.
    """
    fleet = _Fleet()
    fleet.authorities = [{"id": 3, "name": "Typed_by_hand", "url": held}]
    fleet.certificates = [
        {"id": 81, "name": "rmscep", "subject_name": "CN=$VAR,OU=$OTHER", "certificate_authority_id": 3}
    ]

    with pytest.raises(FleetError, match="which serves " + re.escape(held) + ", not https://deployment.test/scep"):
        _mdm(fleet, monkeypatch).apply(
            "rmscep", _with_cert(""), scep_url="https://deployment.test/scep", challenge="c2"
        )

    assert not [c for c in fleet.calls if c[0] != "GET"], "nothing changed in Fleet"


def test_the_challenge_goes_to_the_authority_the_team_is_bound_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two authorities typed for one address: the team's phones ask only the one its template names"""
    fleet = _Fleet()
    fleet.authorities = [
        {"id": 3, "name": "RMSCEP", "url": "https://deployment.test/scep"},
        {"id": 9, "name": "Second_of_the_same", "url": "https://deployment.test/scep"},
    ]
    fleet.certificates = [
        {"id": 81, "name": "rmscep", "subject_name": "CN=$VAR,OU=$OTHER", "certificate_authority_id": 9}
    ]

    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(""), scep_url="https://deployment.test/scep", challenge="c2")

    assert [a.get("challenge") for a in fleet.authorities] == [None, "c2"]
    assert [c["certificate_authority_id"] for c in fleet.certificates] == [9], "the template was left as it is"


def test_an_authority_that_serves_another_deploy_app_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A template that names an authority gets that one or none

    Repointing it would send the phones of the Deploy App it serves to the one connected now.
    """
    fleet = _Fleet()
    with pytest.raises(FleetError) as raised:
        _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(), scep_url="https://moved.test/scep", challenge="rotated")

    assert str(raised.value) == (
        "certificate authority RMSCEP already serves https://deployment.test/scep: give this Deploy App a "
        "template that names another authority, or delete that authority in Fleet if the Deploy App moved"
    )
    assert fleet.authorities == [{"id": 3, "name": "RMSCEP", "url": "https://deployment.test/scep"}]
    assert not [c for c in fleet.calls if c[0] != "GET"], "nothing changed in Fleet"


def test_an_authority_kept_with_an_upper_case_host_is_still_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    """An earlier version kept the host as the line had it, and a host compares without case"""
    fleet = _Fleet()
    fleet.authorities[0]["url"] = "HTTPS://Deployment.TEST/scep"
    _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(), scep_url="https://deployment.test/scep", challenge="c3")

    assert fleet.authorities[0]["challenge"] == "c3"


def test_a_refused_authority_makes_no_team(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second Deploy App naturally gets a team of its own, and a refusal must not leave it behind empty"""
    fleet = _Fleet()
    with pytest.raises(FleetError, match="already serves https://deployment.test/scep"):
        _mdm(fleet, monkeypatch).apply("Second unit", _with_cert(), scep_url="https://second.test/scep", challenge="c2")

    assert not [c for c in fleet.calls if c[0] != "GET"], "nothing changed in Fleet"


def test_without_a_scep_url_the_authority_must_already_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """The path for an MDM we cannot create one in: state what is there, or say what is missing"""
    fleet = _Fleet()
    fleet.authorities = []
    with pytest.raises(FleetError, match="RMSCEP"):
        _mdm(fleet, monkeypatch).apply("rmscep", _with_cert())
    assert not [c for c in fleet.calls if c[0] != "GET"], "no team was made for it"


def test_without_a_scep_url_a_template_must_name_its_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    fleet = _Fleet()
    with pytest.raises(FleetError, match="names no certificate authority"):
        _mdm(fleet, monkeypatch).apply("rmscep", _with_cert(""))
    assert not fleet.calls


def test_applying_twice_publishes_one_web_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-run reuses the launcher instead of leaving another permanent one behind

    Google does not dedupe by url: the same url published twice came back as two package names,
    so every mdm-apply added another launcher to the team. Fleet cannot delete a web app, so each
    one is permanent, and the third publish of a name is refused outright.
    """
    fleet = _Fleet()
    template = MdmTemplate(
        apps=TEMPLATE.apps, policy=TEMPLATE.policy, link_app=LinkApp(title="Deploy App", url="https://same.test/")
    )
    mdm = _mdm(fleet, monkeypatch)
    mdm.apply("rmscep", template)
    mdm.apply("rmscep", template)

    assert len(fleet.web_apps) == 1, f"the re-run must not publish again, got {[a['title'] for a in fleet.web_apps]}"
    launchers = [t for t in fleet.titles if t["display_name"].startswith("Deploy App")]
    assert len(launchers) == 1, "the team must end up with one launcher, not one per run"


def test_a_wiped_team_says_what_is_wrong_instead_of_just_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name published to a team that no longer exists must fail legibly

    A full fresh run deletes the team and makes it again. The web app outlives it, because Fleet
    cannot delete one, so publishing the same name answers 409. Whether Fleet still lists an app
    whose team is gone is not something this stub can decide, so the contract tested here is the
    one that holds either way: when it cannot be found, the operator is told the name is taken
    and what to do about it, rather than being handed a bare conflict.
    """
    fleet = _Fleet()
    template = MdmTemplate(
        apps=TEMPLATE.apps, policy=TEMPLATE.policy, link_app=LinkApp(title="Deploy App", url="https://same.test/")
    )
    mdm = _mdm(fleet, monkeypatch)
    mdm.apply("rmscep", template)
    published = fleet.web_apps[-1]["app_store_id"]

    # The team is deleted and recreated: its software titles go with it, the web app does not.
    fleet.titles = [t for t in fleet.titles if (t.get("app_store_app") or {}).get("app_store_id") != published]

    with pytest.raises(FleetError) as caught:
        mdm.apply("rmscep", template)
    said = str(caught.value)
    assert "Deploy App (same)" in said, f"it must name the title that is taken, said: {said}"
    assert "deleted" in said.lower() or "cannot be deleted" in said.lower(), f"it must say why: {said}"
    assert "display name" in said.lower(), f"it must offer the recoverable case before giving up: {said}"
    assert len(fleet.web_apps) == 1, "it must not have published a second app before failing"


def test_the_launcher_points_at_this_deployment_not_the_last_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web app is identified by the host it serves, never by a bare title

    Fleet cannot read, change or delete a web app's url, so reusing one because the title matched
    kept a decommissioned deployment's link and handed every device an icon that opened a host
    which no longer existed. Everything else about the apply was correct, which is what made it
    so quiet. The deployment that replaced it reused the team name too, so the team is no help:
    the host in the title is what tells the two apart.
    """
    fleet = _Fleet()
    template = MdmTemplate(
        apps=TEMPLATE.apps, policy=TEMPLATE.policy, link_app=LinkApp(title="Deploy App", url="https://old.test/")
    )
    mdm = _mdm(fleet, monkeypatch)
    mdm.apply("rmscep", template)
    first = fleet.web_apps[-1]["app_store_id"]

    moved = MdmTemplate(
        apps=TEMPLATE.apps, policy=TEMPLATE.policy, link_app=LinkApp(title="Deploy App", url="https://new.test/")
    )
    mdm.apply("rmscep", moved)
    second = fleet.web_apps[-1]["app_store_id"]

    assert fleet.web_apps[-1]["url"] == "https://new.test/", "the moved deployment must publish its own url"
    assert second != first, "same title, different url: it must not reuse the old app"


def test_the_launcher_title_names_the_deployment_and_fits() -> None:
    """Google refuses a web app title of 30 characters or more, and a full host rarely fits"""
    title = FleetMdm._published_title("Deploy App", "https://mtls.quiet-otter.example.fi/")
    assert title == "Deploy App (quiet-otter)"
    long_name = FleetMdm._published_title("Deploy App", "https://mtls.thoughtful-hippopotamus.example.org/")
    assert len(long_name) < 30
    assert long_name.startswith("Deploy App (thoughtful")


@pytest.mark.parametrize(
    ("created", "updated", "enrolled", "later"),
    [
        ("2026-11-20T21:02:44.458685Z", "2026-11-20T21:02:44Z", "2026-11-20T21:02:44Z", 0),
        ("2026-11-20T21:02:44.458685Z", "2026-11-20T21:02:44Z", "2026-11-20T21:10:02Z", 1),
        ("2026-11-20T21:02:44.458685Z", None, "2026-11-20T21:02:44Z", 0),
        ("2026-11-20T21:02:44.458685Z", None, "2026-11-20T21:02:45Z", 1),
        ("2026-11-20T21:02:44.458685Z", "2026-11-20T21:20:00Z", "2026-11-20T21:10:02Z", 0),
    ],
)
def test_enrolment_and_upload_are_compared_as_times(
    monkeypatch: pytest.MonkeyPatch, created: str, updated: str | None, enrolled: str, later: int
) -> None:
    """Fleet writes created_at to the microsecond, updated_at and last_enrolled_at to the second

    As strings, "...44Z" sorts after "...44.458685Z", so a phone that enrolled before the upload
    would read as later. updated_at is the last upload when Fleet gives it.
    """
    fleet = _Fleet()
    profile = {"name": "rmscep-policy", "profile_uuid": "g4bb", "created_at": created, "raw": TEMPLATE.policy}
    fleet.profiles = [profile | ({"updated_at": updated} if updated else {})]
    fleet.hosts = [_host(31, enrolled=enrolled), _host(32, enrolled="")]
    mdm = _mdm(fleet, monkeypatch)

    assert mdm.resend_policy(7, mdm.phones(7, "rmscep")) == later
    assert _policy_uploads(fleet) == later
    assert [p["raw"] for p in fleet.profiles] == [TEMPLATE.policy]


def test_a_policy_without_a_time_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rather than ending a command that has already named phones in a traceback"""
    fleet = _Fleet()
    fleet.profiles = [{"name": "rmscep-policy", "profile_uuid": "g4bb", "raw": TEMPLATE.policy}]
    fleet.hosts = [_host(31)]
    mdm = _mdm(fleet, monkeypatch)

    with pytest.raises(FleetError, match="the time '' could not be read"):
        mdm.resend_policy(7, mdm.phones(7, "rmscep"))
    assert not _policy_uploads(fleet)


# --- the transport --------------------------------------------------------------

TOKEN = "secret-token-4f2a"


@pytest.mark.parametrize("url", ["http://mdm.example.fi", "file:///etc", "ftp://192.0.2.10"])
def test_only_https_is_sent(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """The token goes with every request, so it must not cross the network readable"""
    opener = _answering(lambda request: Response(200, json={}), monkeypatch)

    with pytest.raises(FleetError, match="must start with https://"):
        FleetMdm(url, TOKEN).team_id("rmscep")

    assert not opener.sent


def test_the_opener_only_proxies_and_speaks_https() -> None:
    """No redirect is followed, no other scheme is spoken, no status is turned into an exception

    The proxy handler is there only while the environment names a proxy: without one it has nothing to open.
    """
    handlers: list[object] = vars(fleet_module._OPENER)["handlers"]

    assert {type(handler) for handler in handlers} <= {urllib.request.ProxyHandler, urllib.request.HTTPSHandler}
    https = next(handler for handler in handlers if isinstance(handler, urllib.request.HTTPSHandler))
    context: ssl.SSLContext = vars(https)["_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_a_request_says_who_asks_and_what_it_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Request] = []

    def answer(request: Request) -> Response:
        seen.append(request)
        return Response(200, json={"team": {"id": 9}})

    _answering(answer, monkeypatch)

    assert FleetMdm("https://mdm.example.fi/", TOKEN).team_id("Ålands", create=True) == 9

    created = seen[-1]
    assert (created.method, created.url.path) == ("POST", "/api/latest/fleet/teams")
    assert created.headers["authorization"] == f"Bearer {TOKEN}"
    assert created.headers["user-agent"] == f"odmdm-connector/{__version__}"
    assert created.headers["content-type"] == "application/json"
    assert json.loads(created.content) == {"name": "Ålands"}


def test_an_empty_answer_is_an_empty_object(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(lambda request: Response(204), monkeypatch)
    assert FleetMdm("https://mdm.example.fi", TOKEN)._call("DELETE", "/api/latest/fleet/teams/9") == {}


@pytest.mark.parametrize("status", [301, 302, 307, 404, 500])
def test_an_answer_outside_2xx_is_a_refusal(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """A redirect is not followed either: it would carry the token on to wherever it points"""
    opener = _answering(lambda request: Response(status, text="x" * 1000), monkeypatch)

    with pytest.raises(FleetError) as raised:
        FleetMdm("https://mdm.example.fi", TOKEN)._call("GET", "/api/latest/fleet/teams")

    assert str(raised.value) == f"GET /api/latest/fleet/teams answered {status}: {'x' * 800}"
    assert len(opener.sent) == 1


def test_an_upload_refused_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(lambda request: Response(302, text="y" * 300), monkeypatch)

    with pytest.raises(FleetError) as raised:
        FleetMdm("https://mdm.example.fi", TOKEN)._upload("/api/x", {}, "p.json", b"{}", "profile")

    assert str(raised.value) == f"upload to /api/x answered 302: {'y' * 200}"


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
        urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed")),
        TimeoutError("timed out"),
        ssl.SSLError("record layer failure"),
        http.client.RemoteDisconnected("Remote end closed connection without response"),
    ],
)
def test_an_mdm_out_of_reach_is_said_without_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    def failing(request: Request) -> Response:
        raise failure

    _answering(failing, monkeypatch)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(FleetError, match="^could not reach the MDM: ") as raised:
        FleetMdm("https://mdm.example.fi", TOKEN).team_id("rmscep")

    assert TOKEN not in str(raised.value)
    assert TOKEN not in caplog.text


def test_a_request_that_cannot_be_carried_is_said_without_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """http.client refuses a header with a line break in a ValueError whose text quotes it, token and all"""

    def refusing(request: Request) -> Response:
        raise ValueError(f"Invalid header value {request.headers['authorization'].encode()!r}")

    _answering(refusing, monkeypatch)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(FleetError) as raised:
        FleetMdm("https://mdm.example.fi", TOKEN).team_id("rmscep")

    assert str(raised.value) == "the MDM address or the token holds a character a request cannot carry"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert TOKEN not in caplog.text


def test_the_upload_is_a_form_with_the_file_last(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Request] = []

    def answer(request: Request) -> Response:
        seen.append(request)
        return Response(200, json={"ok": True})

    _answering(answer, monkeypatch)

    result = FleetMdm("https://mdm.example.fi", TOKEN)._upload(
        "/api/x",
        {"title": "Deploy App", "url": "https://deploy.example.fi"},
        "icon.png",
        b"\x89PNG",
        "icon",
        "image/png",
    )

    assert result == {"ok": True}
    kind, _, boundary = seen[0].headers["content-type"].partition("; boundary=")
    assert kind == "multipart/form-data"
    assert (
        seen[0].content
        == (
            f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\nDeploy App\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="url"\r\n\r\nhttps://deploy.example.fi\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="icon"; filename="icon.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode()
        + b"\x89PNG\r\n"
        + f"--{boundary}--\r\n".encode()
    )


@pytest.mark.parametrize(
    "fields, filename, content_type",
    [
        ({'ti"tle': "x"}, "p.json", "application/json"),
        ({"title\r\n": "x"}, "p.json", "application/json"),
        ({}, 'p".json', "application/json"),
        ({}, "p\n.json", "application/json"),
        ({}, "p.json", "application/json\r\nX-Evil: 1"),
    ],
)
def test_a_name_that_would_break_the_form_is_refused(
    monkeypatch: pytest.MonkeyPatch, fields: dict[str, str], filename: str, content_type: str
) -> None:
    opener = _answering(lambda request: Response(200), monkeypatch)

    with pytest.raises(ValueError, match="cannot go in a multipart header"):
        FleetMdm("https://mdm.example.fi", TOKEN)._upload("/api/x", fields, filename, b"{}", "profile", content_type)

    assert not opener.sent
