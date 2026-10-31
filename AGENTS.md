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

## Checks

`uv run pytest` needs no network, no Fleet and no phone. `uv run prek run --all-files` before
committing.
