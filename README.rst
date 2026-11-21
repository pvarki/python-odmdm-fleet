===============
odmdm-connector
===============

Connects FleetDM to an OpenDefence deployment's device enrolment.

The deployment knows no MDM. It answers standard SCEP (RFC 8894) at ``https://<domain>/scep``,
asks for a challenge that is new every time an admin opens enrolment in Deploy App, and makes the
certificate's common name the device's callsign. Everything that knows Fleet lives here: the
certificate authority, the certificate template, the apps, the device policy, the launcher icon,
and naming the phones.

Run it from an operator's machine, never inside the deployment: it holds a Fleet API token, and
something that can reach every device a unit owns has no business running next to an internet
facing service.

Install
-------

Install it with uv (https://docs.astral.sh/uv/getting-started/installation/); git must be
installed too::

    uv tool install git+https://github.com/pvarki/python-odmdm-fleet
    odmdm-connector --help

From a checkout, ``uv tool install .`` does the same.

Enrolling phones
----------------

One Fleet team, the one with the certificate template. Phones enrol into it before anybody names
them, and each gets its certificate once it has a name.

1. Once per deployment, and whenever the template changes::

       odmdm-connector apply

2. Open enrolment in Deploy App (**User management → MDM**), copy its settings line or its
   challenge, and give Fleet the new challenge. It asks for it, and changes nothing else::

       odmdm-connector challenge

3. Enrol phones into the ``Deploy App`` team, as many as you like, in any order. Name them once
   every phone in the team has its apps: naming can send the policy to all of them again.

4. Name them, all at once or a few at a time::

       odmdm-connector name --all OTTER           # OTTER1, OTTER2, ... in the order they enrolled
       odmdm-connector name 31=OTTER4 32=OTTER5   # host id = callsign, the ids being what phones prints

   Each phone gets its certificate at its next check-in, within about 15 minutes, and greets its
   callsign in Deploy App. ``phones`` shows where each one is. ``resend`` asks again for a refused
   phone once the cause is fixed.

Close enrolment once ``phones`` says every phone has its certificate: a request that arrives after
it closes is refused. The next opening makes a new challenge, so step 2 comes again before the
next batch.

Without this tool, in Fleet: open the phone, **Edit user** to give it its callsign, then under
**OS settings** pick the certificate and **Resend**. Naming alone does not send the request again.
Once the phones have enrolled and their apps have arrived, delete the policy in Fleet and upload
it again: Fleet can overwrite a new phone's policy, see `The order that works`_.

Phones already enrolled in another Fleet team: transfer them into this team in Fleet, then name
them like the rest. Apps install only at enrolment, so a transferred phone gets its certificate but
not this team's apps or launcher. For those, remove its work profile after the transfer and enrol
it again into this team; see `The order that works`_.

Commands
--------

.. code-block:: bash

    odmdm-connector apply                     # state what devices need; --force-policy sends the policy again
    odmdm-connector apply --dry-run           # validate the template without touching Fleet
    odmdm-connector challenge                 # give Fleet a new window's challenge, nothing else
    odmdm-connector phones                    # each phone's host id, callsign, model and certificate
    odmdm-connector name 31=OTTER4 32=OTTER5  # name these phones, and Fleet sends their certificates
    odmdm-connector name --all OTTER          # the same for every phone without a name, numbered
    odmdm-connector resend [HOST_ID ...]      # ask again; without ids, every named phone that failed

``name --all`` numbers in enrolment order from one above the highest ``OTTER<n>`` in the team, or
from ``--start N``, 1 or more. It sees only this team's phones, and Deploy App never gives a
callsign out twice: where callsigns were used by phones no longer in the team, give ``--start``
above the highest ever used. ``name`` changes nothing at all if a callsign is malformed, given twice or
already another phone's, or if a phone is not in the team. A phone that already has its certificate
under the name is left alone. Renaming a phone that has its certificate says which old callsign to
revoke.

Configuration
-------------

All configuration is via environment variables:

``ODMDM_MDM_URL``
    Base URL of Fleet's API. Only https: the token goes with every request.

``ODMDM_TOKEN_FILE``
    A file holding the Fleet API token. Changing the certificate authority's challenge needs a
    global admin token, because Fleet keeps certificate authorities outside teams.
    Keep the file outside this checkout, readable only by you: the token can change every
    enrolled phone. The tool reads it and never prints, logs or writes it.

``ODMDM_TEAM``
    The team phones enrol into and get the certificate in. Default ``Deploy App``.

``ODMDM_TEMPLATE``
    The document saying what to install, see below.

``ODMDM_DOMAIN``
    The deployment's DNS name.

``ODMDM_CHALLENGE``
    The settings line or the bare challenge from Deploy App's MDM page, for ``challenge`` and a
    first ``apply``. New for every enrolment window.

``ODMDM_KEY_ALIAS``
    The certificate template's name, which becomes the Android keystore alias. Default ``rmscep``,
    what the first deployments were set up with. Never change it for a deployment: a second key
    will not install over an existing alias.

The template
------------

What to install is a property of the deployment, so it is a document rather than code: the apps,
their managed configuration, the Android Management API device policy, a launcher link, and the
certificate template. ``{domain}``, ``{mtls_url}`` and ``{key_alias}`` are filled in. Keys
beginning with ``_`` are comments and never reach Fleet. ``src/odmdm_connector/template.json``, shipped
in the package, is a working one for Deploy App, with its reasoning in the comments.

The certificate subject is the contract with the deployment. Its common name becomes the callsign:
3 to 32 letters, digits or hyphens, no hyphen at either end. It is the name ``name`` gives the
phone, optionally followed by Fleet's id for the host, and the tool refuses any other::

    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART
    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART, OU=$FLEET_VAR_HOST_UUID

A name the phone supplies, such as its serial number, would make a leaked enrolment link enough
for an identity.

The tool also refuses a policy that lets an app use the key without being named in
``packageNames``, lets the user pick a key for any app (``privateKeySelectionEnabled``), or
allows installs from unknown sources (``untrustedAppsPolicy`` other than ``DISALLOW_INSTALL``).

The settings line
-----------------

Deploy App's MDM page copies one line for the connector. It is the same for every MDM, and it
carries no list of names: phones are named in the MDM. Version 1 is exactly four fields
separated by whitespace::

    ODMDM1 https://unit.example.fi/scep 0123456789abcdef0123456789abcdef 2026-11-21T14:00:00Z

1. The mark ``ODMDM1``.
2. The SCEP URL. It must be https.
3. The challenge: 32 lowercase hex characters. It is a secret, and the tool never prints or logs it.
4. When the enrolment window closes, in UTC, as ``YYYY-MM-DDTHH:MM:SSZ``.

Whitespace and line breaks before and after the line are ignored. A line with another mark or
another number of fields, a URL that is not https, a malformed challenge or time, or a window that
has already closed is refused. A value with whitespace inside it is read as a line, never as a
bare challenge, so a damaged paste is refused instead of being sent to Fleet. ``challenge`` takes
the line at its prompt, from ``--challenge`` or from ``ODMDM_CHALLENGE``, refuses one whose SCEP
URL is not on ``ODMDM_DOMAIN``, and says until when the window is open. The line above is the
shared test vector, in ``tests/data/settings-line-v1.txt``.

The order that works
--------------------

Three facts about managed Android and Fleet pull against each other. Each was learned by getting
it wrong.

1. **Apps install at enrolment and at no other time.** The team must carry them before a phone
   joins. A phone transferred in from a team with another launcher package loses that launcher
   and never gets this one.
2. **Fleet can overwrite a new phone's policy moments after sending it.** It sends a phone that
   enrols the team's policy, and seconds later can write a bare one over it. It goes on showing
   the policy as verified on that phone and never sends it again, so the phone has its certificate
   but Chrome may not use it, and Deploy App says "Certificate Authentication Failed". ``name`` and
   ``resend`` send the policy again, to every phone in the team, when a phone enrolled after it
   was last sent. By hand it is ``apply --force-policy``, or deleting the policy in Fleet and
   uploading it again.
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
