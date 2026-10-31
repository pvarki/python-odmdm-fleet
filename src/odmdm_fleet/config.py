"""Configuration, from the environment

Every setting is prefixed ODMDM_FLEET_.
"""

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"ODMDM_FLEET_{name}", default)


LOG_LEVEL: int = int(_env("LOG_LEVEL", "20"))

#: Where Fleet's API answers
URL: str = _env("URL")

#: A file holding the Fleet API token. A path rather than the value, so it can be a mounted secret
#: and never appears in a process environment.
TOKEN_FILE: Path | None = Path(_env("TOKEN_FILE")) if _env("TOKEN_FILE") else None

#: The team whose devices get the deployment's certificate
TEAM: str = _env("TEAM", "Deploy App")

#: The team devices enrol into and are named in before they move to TEAM. Empty for none.
PREP_TEAM: str = _env("PREP_TEAM")

#: What this deployment wants installed, as a document. See odmdm_fleet.template.
TEMPLATE: Path | None = Path(_env("TEMPLATE")) if _env("TEMPLATE") else None

#: The deployment's DNS name, which the template substitutes into URLs
DOMAIN: str = _env("DOMAIN")

#: The challenge shown on Deploy App's MDM page. It is new for every enrolment window.
CHALLENGE: str = _env("CHALLENGE")

#: The certificate template name, which becomes the Android keystore alias the certificate lands
#: under. Installing a second key under an existing alias fails, so it never changes for a
#: deployment; the default is what the first deployments were set up with.
KEY_ALIAS: str = _env("KEY_ALIAS", "rmscep")
