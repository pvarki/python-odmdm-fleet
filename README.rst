===========
odmdm-fleet
===========

Connects FleetDM to an OpenDefence deployment's device enrolment.

The deployment knows no MDM. It answers standard SCEP (RFC 8894) at ``https://<domain>/scep``,
asks for a challenge that is new every time an admin opens enrolment in Deploy App, and makes the
certificate's common name the device's callsign. Everything that knows Fleet lives here: the
certificate authority, the certificate template, the apps, the device policy, the launcher icon,
and naming the phones.

Run it from an operator's machine, never inside the deployment: it holds a Fleet API token, and
something that can reach every device a unit owns has no business running next to an internet
facing service.

Enrolling phones
----------------

Two Fleet teams: phones enrol into a preparation team and are named there, then move to the team
that gets the certificate. Naming first means the first certificate request already carries the
callsign.

1. Once per deployment, and whenever the template changes::

       odmdm-fleet apply --team "Deploy App" --prep-team Preparation

2. Enrol a phone into the preparation team, then name it::

       odmdm-fleet plan OTTER4

   ``plan`` names the one phone in the preparation team that has no name yet. With two waiting it
   names neither, because Fleet shows identical phones as identical rows and nothing tells you
   which is the one in your hand. Enrol, plan, next phone.

3. Open enrolment in Deploy App (**User management → MDM**) and give Fleet the new challenge. It
   asks for it, and changes nothing else::

       odmdm-fleet challenge

4. In Fleet, transfer the named phones to the ``Deploy App`` team. Each gets its certificate 7 to
   20 minutes later and greets its callsign in Deploy App.

Close enrolment when the last phone is in. The next opening makes a new challenge, so step 3
comes again before the next batch.

Commands
--------

.. code-block:: bash

    odmdm-fleet apply              # state what devices need; --force-policy after one joins
    odmdm-fleet apply --dry-run    # validate the template without touching Fleet
    odmdm-fleet challenge          # give Fleet a new enrolment window's challenge, nothing else
    odmdm-fleet plan CALLSIGN      # name the one phone waiting to be named

With a single team a phone enrols straight into the certificate team before anyone has named it,
and that first request fails. Name it with ``plan --team``, then transfer it out and back.

Configuration
-------------

All configuration is via environment variables:

``ODMDM_FLEET_URL``
    Base URL of Fleet's API.

``ODMDM_FLEET_TOKEN_FILE``
    A file holding the Fleet API token. Changing the certificate authority's challenge needs a
    global admin token, because Fleet keeps certificate authorities outside teams.

``ODMDM_FLEET_TEAM``
    The team whose phones get the certificate. Default ``Deploy App``.

``ODMDM_FLEET_PREP_TEAM``
    The team phones enrol into and are named in. Empty for a single team.

``ODMDM_FLEET_TEMPLATE``
    The document saying what to install, see below.

``ODMDM_FLEET_DOMAIN``
    The deployment's DNS name.

``ODMDM_FLEET_CHALLENGE``
    The challenge on Deploy App's MDM page, for ``challenge`` and a first ``apply``. New for every
    enrolment window.

``ODMDM_FLEET_KEY_ALIAS``
    The certificate template's name, which becomes the Android keystore alias. Default ``rmscep``,
    what the first deployments were set up with. Never change it for a deployment: a second key
    will not install over an existing alias.

The template
------------

What to install is a property of the deployment, so it is a document rather than code: the apps,
their managed configuration, the Android Management API device policy, a launcher link, and the
certificate template. ``{domain}``, ``{mtls_url}`` and ``{key_alias}`` are filled in. Keys
beginning with ``_`` are comments and never reach Fleet. ``examples/template.json`` is a working one
for Deploy App, with its reasoning in the comments.

The certificate subject is the contract with the deployment. Its common name becomes the callsign:
3 to 32 letters, digits or hyphens, no hyphen at either end. For named phones::

    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART

The order that works
--------------------

Three facts about managed Android pull against each other. Each was learned by getting it wrong.

1. **Apps install at enrolment and at no other time.** The team must carry them before a phone
   joins. So both teams carry the **same** launcher package: a transfer to a team with another
   package uninstalls the launcher and never installs the other.
2. **A profile is delivered when the profile changes, not when a phone arrives.** One uploaded
   before a phone joined is never sent to it, so a first enrolment needs ``--force-policy``.
3. **A policy rewritten while apps are installing stops them installing.** Leave the phones alone
   until the apps have arrived.

What Fleet does that you would not guess
----------------------------------------

- **A launcher web app's URL can never change.** Google publishes it permanently and Fleet cannot
  delete it. The title carries the deployment's name, so a moved deployment publishes its own
  launcher and leaves the old one orphaned. Google refuses a title of 30 characters or more.
- **A web app's display name is unique within a team** (409). A title in a team's setup experience
  has to be removed there before it can be removed from the team.
- **A returning phone keeps its old team and its old name.** Transfer it to the preparation team
  and rename it with ``odmdm-fleet plan NEWNAME --instead-of OLDNAME``, or it asks for a name that
  is already taken. It can also come back as a new device with no name, so ``--instead-of`` refuses
  while one is waiting: name that one with plain ``plan``.
- **Renaming a phone that has its certificate gives it a second callsign.** Revoke the old one in
  Deploy App.
- **Replacing a team's certificate template locks its phones out.** Every phone deletes the
  certificate it has and asks again, and the deployment refuses a name that is already taken. So
  this repository never replaces one to retry a phone: a transfer out and back asks for that phone
  alone.
- **A refusal shows only as** ``SCEP enrollment failed ... failInfo: badMessageCheck`` on the
  phone's certificate. Usually a bad or taken name, or Fleet still has the previous window's
  challenge. Fix it, then transfer the phone out and back.

Docker
------

The ``production`` target runs the CLI::

    docker build --ssh default --target production -t odmdm-fleet:latest .
    docker run --rm -e ODMDM_FLEET_URL -e ODMDM_FLEET_TOKEN_FILE=/run/token \
      -v "$PWD/fleet.token:/run/token:ro" -v "$PWD/template.json:/run/template.json:ro" \
      -e ODMDM_FLEET_TEMPLATE=/run/template.json -e ODMDM_FLEET_DOMAIN \
      odmdm-fleet:latest odmdm-fleet apply

For the test suite in docker use the ``tox`` target, and for a development shell ``devel_shell``.

Development
-----------

- install uv: https://docs.astral.sh/uv/getting-started/installation/
- Install project deps and pre-commit hooks::

    uv sync
    uv run prek install
    uv run prek run --all-files

- Run the tests::

    uv run pytest

The ``.pre-commit-config.yaml`` is generated by config-ci-library_ (common + python components), do
not hand edit the autogenerated section.

.. _config-ci-library: https://github.com/pvarki/config-ci-library

Versioning is handled with bump-my-version_: ``bump-my-version bump <patch/minor/major>``.

.. _bump-my-version: https://github.com/callowayproject/bump-my-version
