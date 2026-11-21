"""The template document

What a deployment installs is data, not code. These tests pin that the connector stays ignorant
of it and that a malformed document is refused before it can reach an MDM.
"""

import json
from pathlib import Path

import pytest

from odmdm_connector.template import EXAMPLE, TemplateError, load

GOOD = {
    "apps": [
        {"package": "com.example.one", "preinstall": True},
        {
            "package": "com.example.two",
            "preinstall": False,
            "config": {"AutoSelectCertificateForUrls": ['{"pattern":"{mtls_url}","filter":{}}']},
        },
    ],
    "link_app": {"title": "Deploy App", "url": "{mtls_url}/"},
    "policy": {
        "choosePrivateKeyRules": [
            {"urlPattern": "{mtls_url}", "privateKeyAlias": "{key_alias}", "packageNames": ["com.example.two"]}
        ],
        "defaultPermissionPolicy": "GRANT",
    },
}


def _write(tmp_path: Path, body: object) -> Path:
    path = tmp_path / "template.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_placeholders_are_filled_at_every_depth(tmp_path: Path) -> None:
    """The document is written once and used against whatever deployment it is pointed at"""
    template = load(_write(tmp_path, GOOD), domain="example.fi", key_alias="rmscep")

    assert template.preinstall_packages == ("com.example.one",)
    inside_a_list = template.apps[1].config["AutoSelectCertificateForUrls"][0]
    assert json.loads(inside_a_list)["pattern"] == "https://mtls.example.fi"
    assert template.policy["choosePrivateKeyRules"][0]["urlPattern"] == "https://mtls.example.fi"
    assert template.policy["choosePrivateKeyRules"][0]["privateKeyAlias"] == "rmscep"
    assert template.link_app is not None
    assert template.link_app.url == "https://mtls.example.fi/"


@pytest.mark.parametrize(
    ("body", "because"),
    [
        ({"apps": []}, "a template that installs nothing is a mistake, not a choice"),
        ({"apps": [{"preinstall": True}]}, "an app with no package cannot be acted on"),
        ({"apps": [{"package": "x", "config": []}]}, "config must be an object"),
        ({"apps": [{"package": "x"}], "policy": []}, "policy must be an object"),
        ({"apps": [{"package": "x"}], "link_app": {"title": "t"}}, "a link with no url is useless"),
        ([], "the document must be an object"),
    ],
)
def test_a_malformed_document_is_refused(tmp_path: Path, body: object, because: str) -> None:
    """A half applied template is worse than none: apps install at enrolment only, so a device
    that joined against a broken one has to be enrolled again, and its callsign is spent."""
    with pytest.raises(TemplateError):
        load(_write(tmp_path, body), domain="example.fi", key_alias="rmscep")


NAMED = "CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART"


def _with(policy: dict[str, object] | None = None, subject: str = NAMED) -> dict[str, object]:
    """GOOD with a certificate, and with these policy keys changed"""
    certificate = {"name": "{key_alias}", "subject": subject, "authority": "RMSCEP"}
    return {**GOOD, "certificate": certificate, "policy": {**GOOD["policy"], **(policy or {})}}  # type: ignore[dict-item]


@pytest.mark.parametrize(
    "subject",
    [
        NAMED,
        "CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME",
        "CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART, OU=$FLEET_VAR_HOST_UUID",
        "CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME,OU=$FLEET_VAR_HOST_UUID",
    ],
)
def test_the_callsign_is_the_name_the_operator_gave(tmp_path: Path, subject: str) -> None:
    template = load(_write(tmp_path, _with(subject=subject)), domain="example.fi", key_alias="rmscep")
    assert template.certificate is not None and template.certificate.subject == subject


@pytest.mark.parametrize(
    ("body", "says"),
    [
        (_with(subject="CN=$FLEET_VAR_HOST_HARDWARE_SERIAL"), "certificate subject must be CN="),
        (_with(subject="CN=OTTER1"), "certificate subject must be CN="),
        (_with(subject=NAMED + ", OU=$FLEET_VAR_HOST_HARDWARE_SERIAL"), "certificate subject must be CN="),
        (_with(subject="OU=$FLEET_VAR_HOST_UUID, " + NAMED), "certificate subject must be CN="),
        (
            _with({"choosePrivateKeyRules": [{"urlPattern": "x", "privateKeyAlias": "rmscep"}]}),
            "must name its apps in packageNames",
        ),
        (
            _with({"choosePrivateKeyRules": [{"urlPattern": "x", "packageNames": []}]}),
            "must name its apps in packageNames",
        ),
        (_with({"choosePrivateKeyRules": {"packageNames": ["a"]}}), "choosePrivateKeyRules must be a list"),
        (_with({"privateKeySelectionEnabled": True}), "privateKeySelectionEnabled must not be true"),
        (
            _with({"advancedSecurityOverrides": {"untrustedAppsPolicy": "ALLOW_INSTALL_DEVICE_WIDE"}}),
            "must not allow installs from unknown sources",
        ),
        (
            _with({"advancedSecurityOverrides": {"untrustedAppsPolicy": "ALLOW_INSTALL_IN_PERSONAL_PROFILE_ONLY"}}),
            "must not allow installs from unknown sources",
        ),
        (_with({"installUnknownSourcesAllowed": True}), "must not allow installs from unknown sources"),
    ],
)
def test_a_template_that_hands_out_too_much_is_refused(tmp_path: Path, body: object, says: str) -> None:
    """A named phone's key for named apps only, and no side door onto the phone"""
    with pytest.raises(TemplateError, match=says):
        load(_write(tmp_path, body), domain="example.fi", key_alias="rmscep")


def test_safe_settings_are_accepted(tmp_path: Path) -> None:
    body = _with(
        {"privateKeySelectionEnabled": False, "advancedSecurityOverrides": {"untrustedAppsPolicy": "DISALLOW_INSTALL"}}
    )
    assert load(_write(tmp_path, body), domain="example.fi", key_alias="rmscep").certificate is not None


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):
        load(tmp_path / "nope.json", domain="example.fi", key_alias="rmscep")


def test_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "template.json"
    path.write_text("this is not json", encoding="utf-8")
    with pytest.raises(TemplateError):
        load(path, domain="example.fi", key_alias="rmscep")


def test_annotations_never_reach_the_device(tmp_path: Path) -> None:
    """The document explains itself, and none of that explanation is a setting

    An annotation left in an app's configuration would be pushed to the phone as a real managed
    configuration key.
    """
    body = {
        "_comment": "why this file exists",
        "apps": [
            {
                "_comment": "why this app",
                "package": "com.example.one",
                "config": {"_comment": "why this key", "real": "value", "nested": {"_x": 1, "keep": 2}},
            }
        ],
        "policy": {"_comment": "why this policy", "defaultPermissionPolicy": "GRANT"},
        "link_app": {"_comment": "why a link", "title": "Deploy App", "url": "{mtls_url}/"},
    }
    template = load(_write(tmp_path, body), domain="example.fi", key_alias="rmscep")
    assert template.apps[0].config == {"real": "value", "nested": {"keep": 2}}
    assert template.policy == {"defaultPermissionPolicy": "GRANT"}
    assert template.link_app is not None and template.link_app.title == "Deploy App"


def test_the_example_is_a_template_we_accept() -> None:
    """Operators start from it, so it must never drift out of what load() takes"""
    template = load(EXAMPLE, domain="example.fi", key_alias="rmscep")

    assert template.certificate is not None
    assert template.certificate.name == "rmscep", "the keystore alias comes from the setting"
    assert not [p for p in ("{domain}", "{mtls_url}", "{key_alias}") if p in json.dumps(template.policy)]
    assert template.policy["advancedSecurityOverrides"]["untrustedAppsPolicy"] == "DISALLOW_INSTALL"
    assert template.policy["privateKeySelectionEnabled"] is False
    assert "defaultPermissionPolicy" not in template.policy, "nothing granted to every app"
    rules = template.policy["choosePrivateKeyRules"]
    assert [r["packageNames"] for r in rules] == [["com.android.chrome", "com.atakmap.app.civ", "im.vector.app"]]
