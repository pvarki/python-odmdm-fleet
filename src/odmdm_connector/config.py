"""Configuration, from the environment

Every setting is prefixed ODMDM_. These are the deployment's; the MDM's own come from its plugin's
fields, read by the command line as ODMDM_<KEY>.
"""

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"ODMDM_{name}", default)


LOG_LEVEL: int = int(_env("LOG_LEVEL", "20"))

#: The deployment's DNS name, which the template substitutes into URLs
DOMAIN: str = _env("DOMAIN")

#: The challenge shown on Deploy App's MDM page. It is new for every enrolment window.
CHALLENGE: str = _env("CHALLENGE")
