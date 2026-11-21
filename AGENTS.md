# Agent guidance

Connects FleetDM to an OpenDefence deployment's SCEP enrolment. Read `README.rst` first.

## The boundary

The deployment speaks standard SCEP and knows no MDM; this repository is the only place that knows
Fleet. Keep it that way in both directions:

- Nothing here calls the deployment's API. What crosses over is a SCEP URL, the challenge an
  operator copies from Deploy App, and the callsign in the certificate subject.
- Nothing Fleet-specific goes back into the deployment's repositories. A Fleet behaviour that
  needs handling is handled here.
- No product knowledge in code. What a deployment installs arrives in the template.

## Facts measured against a live Fleet

Comments in `fleet.py` say what each request shape and wait was measured against. Keep that habit:
Fleet's Android behaviour is under-documented, and a stub that disagrees with the real thing
passes tests and fails phones. The stub in `tests/test_fleet.py` mirrors the quirks on purpose
(the authority listing without its url, the 409 on a named PATCH, per-team title listings).

Names already in use on live Fleets are identities, not cosmetics: the policy profile name and the
certificate template name (the Android keystore alias). Changing either duplicates the profile or
strands every enrolled phone's key.

## Commands

- Setup: `uv sync`
- Test: `uv run pytest` (coverage floor: 65%, set in `pyproject.toml`). Needs no network, no Fleet
  and no phone.
- Hooks, before every commit: `uv run prek run --all-files`. Once per clone:
  `uv run prek install --hook-type pre-commit --hook-type commit-msg`
- Version bump, once per pull request, in its own commit: `uv run bump-my-version bump patch|minor|major`

## Crown jewels

- **The MDM API token** (today a Fleet global admin token): it can change what every enrolled
  phone of every team installs and trusts. The only place that reads it is `_fleet` in
  `src/odmdm_connector/console.py`, from the file `ODMDM_TOKEN_FILE` names; `FleetMdm` keeps it in
  memory and sends it only as the `Authorization` header to the MDM URL.
- The tool never logs it, prints it, takes it as an option or argument, or writes it to a file.
  Keep it that way: no `--token` option, no token in an environment variable, no token in an error
  message. `.gitignore` ignores `*.token` so a token file in the checkout cannot be committed.
