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

One Fleet team, the one with the certificate template. Phones enrol into it before anybody names
them, and each gets its certificate once it has a name.

1. Once per deployment, and whenever the template changes::

       odmdm-fleet apply

2. Open enrolment in Deploy App (**User management → MDM**) and give Fleet the new challenge. It
   asks for it, and changes nothing else::

       odmdm-fleet challenge

3. Enrol phones into the ``Deploy App`` team, as many as you like, in any order.

4. Name them, all at once or a few at a time::

       odmdm-fleet name --all OTTER           # OTTER1, OTTER2, ... in the order they enrolled
       odmdm-fleet name 31=OTTER4 32=OTTER5   # host id = callsign, the ids being what phones prints

   Each phone gets its certificate at its next check-in, within about 15 minutes, and greets its
   callsign in Deploy App. ``phones`` shows where each one is. ``resend`` asks again for a refused
   phone once the cause is fixed.

Close enrolment once ``phones`` says every phone has its certificate: a request that arrives after
it closes is refused. The next opening makes a new challenge, so step 2 comes again before the
next batch.

Without this tool, in Fleet: open the phone, **Edit user** to give it its callsign, then under
**OS settings** pick the certificate and **Resend**. Naming alone does not send the request again.

Phones already enrolled in another Fleet team: transfer them into this team in Fleet, then name
them like the rest. Apps install only at enrolment, so a transferred phone gets its certificate but
not this team's apps or launcher. For those, remove its work profile after the transfer and enrol
it again into this team; see `The order that works`_.

Commands
--------

.. code-block:: bash

    odmdm-fleet apply                     # state what devices need; --force-policy after one joins
    odmdm-fleet apply --dry-run           # validate the template without touching Fleet
    odmdm-fleet challenge                 # give Fleet a new window's challenge, nothing else
    odmdm-fleet phones                    # each phone's host id, callsign, model and certificate
    odmdm-fleet name 31=OTTER4 32=OTTER5  # name these phones, and Fleet sends their certificates
    odmdm-fleet name --all OTTER          # the same for every phone without a name, numbered
    odmdm-fleet resend [HOST_ID ...]      # ask again; without ids, every named phone that failed

``name --all`` numbers in enrolment order from one above the highest ``OTTER<n>`` in the team, or
from ``--start N``. It sees only this team's phones, and Deploy App never gives a callsign out
twice: where callsigns were used by phones no longer in the team, give ``--start`` above the
highest ever used. ``name`` changes nothing at all if a callsign is malformed, given twice or
already another phone's, or if a host is not in the team. A phone that already has its certificate
under the name is left alone. Renaming a phone that has its certificate says which old callsign to
revoke.

Configuration
-------------

All configuration is via environment variables:

``ODMDM_FLEET_URL``
    Base URL of Fleet's API.

``ODMDM_FLEET_TOKEN_FILE``
    A file holding the Fleet API token. Changing the certificate authority's challenge needs a
    global admin token, because Fleet keeps certificate authorities outside teams.

``ODMDM_FLEET_TEAM``
    The team phones enrol into and get the certificate in. Default ``Deploy App``.

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
   joins. A phone transferred in from a team with another launcher package loses that launcher
   and never gets this one.
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
- **A phone enrols before it has a name**, so its first certificate request fails (``does not
  have an IDP user``) until it is named, and naming it does not send the request again. ``name``
  resends. A resend is one more attempt, which the phone acts on at its next check-in, every 15
  minutes.
- **A returning phone keeps its old team and its old name.** Transfer it into this team if it was
  in another, and give it a new name with ``name``, or it asks for a name that is already taken. It
  can also come back as a new device with no name.
- **Renaming a phone that has its certificate gives it a second callsign.** Revoke the old one in
  Deploy App.
- **Replacing a team's certificate template locks its phones out.** Every phone deletes the
  certificate it has and asks again, and the deployment refuses a name that is already taken. So
  this repository never replaces one to retry a phone: ``resend`` asks for that phone alone.
- **A refusal shows only as** ``SCEP enrollment failed ... failInfo: badMessageCheck`` on the
  phone's certificate. Usually a bad or taken name, or Fleet still has the previous window's
  challenge. A taken name is never freed, not even by revoking it, so give the phone another with
  ``name``. For the challenge, ``challenge`` and then ``resend``.

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
