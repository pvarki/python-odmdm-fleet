===============
odmdm-connector
===============

Connects an MDM, FleetDM built in, to an OpenDefence deployment's device enrolment.

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

From a checkout, ``uv tool install .`` does the same. Installed this way it brings its one
dependency, keyring, which keeps the MDM token in this computer's keychain: Windows Credential
Manager, the macOS Keychain, or the Secret Service on Linux.

The page
--------

The connector's everyday face is a page in your browser, served by this computer. Start it::

    odmdm-connector web

It opens the page in your default browser and prints its address, and goes on until Ctrl+C.
``--port N`` serves it on another port than 8737. The browser does not open by itself on a computer
without a desktop, with ``--no-browser``, or when the default browser may not read the connector's
own folder, as a browser installed as a snap on Linux cannot: then open the address it prints
yourself. On a Mac whose ``.html`` files open in another program than the browser, the connector's
file may open there instead; on Linux without a graphical opener, a text browser may take the
terminal. In both cases start it with ``--no-browser`` and open the printed address. A page opened
without that address says so, and goes on by itself once the address has been opened in the same
browser. Deploy App's button finds the page only in the browser where the
address was opened, so use Deploy App in your default browser, or open the address in the browser
you use Deploy App in.

The first time, the page sets up one thing at a time, on cards under a bar of three parts: Deploy
App, the MDM, Connect. Have at hand Deploy App's MDM page, open as an admin, your MDM's address and
an API token for it. Add the Deploy App: on its MDM page press **Open in the MDM connector**, or
copy its settings line and paste it into the page, and confirm it. Choose the MDM when more than one
is installed, then fill in what it asks, one field to a card: for Fleet its address, an API token
and the team, with the rarely changed ones under **More settings**. **Connect** sets the MDM up and
ticks off its steps; it says what is left to do by hand, if anything, or why it failed, and **Show
the phones** goes on.

The daily work is one view of the phones, as wide as the window. At the top, whether Deploy App
accepts phones now and until when; once enrolment has closed, **Open Deploy App** opens its MDM page
in a new tab. Under it one row of buttons, the ways to select, and the table: each phone's ID,
callsign, model, when it joined and where its certificate is. Click a row to select it, and
double-click it, or select one and press **Details**, to see that phone alone. The line at the
bottom says what was done last and, at its right end, when the phones were last listed: every
minute while the page is open, or at once with **Refresh**. **Settings** shows the MDM's
settings, whether its token is kept on this computer, and where the log is. The Deploy App's name
at the top right lists the Deploy Apps added, opens another, adds one or removes the one open.
Each Deploy App needs a Fleet of its own: the shipped template names one certificate
authority for every Deploy App, and the connector refuses to point an authority that serves one
Deploy App at another.

For every batch: open enrolment in Deploy App and press **Open in the MDM connector** again, or
**New settings** and paste the line, so the MDM gets the new challenge. Let the phones enrol, then
**Give callsigns**, to the phones selected or else to every phone waiting for a name: numbered or
from a list. The preview below follows what you type, and **Name** names exactly the preview
shown, once it has passed. **Ask again** asks refused or failed phones for their certificates
again once the cause is fixed.

The MDM token is never in the settings file, the log or the page. With **Remember on this
computer** ticked, as it is at first, it is kept in this computer's keychain, under
``odmdm-connector``, and the page connects to the MDM at the next start without asking for it.
Without a keychain, as in the one file, the page says the token is kept only until the connector
stops, and asks for it after a restart: then it shows the phones of the MDM set up before, without
new settings from Deploy App. It asks for it the same way, with the MDM's reason, when the MDM no
longer accepts it, and removes the copy kept.

To stop keeping the token on this computer, connect again with **Remember on this computer**
unticked, or remove the Deploy App: either removes it from the keychain. If the keychain does not
let it be removed, the page says so: delete the ``odmdm-connector`` item of that Deploy App there by
hand.

What guards the page:

- It listens on this computer only (127.0.0.1), and answers only a request addressed to this
  computer (its Host header), so that a web page elsewhere cannot reach it through DNS rebinding.
- Its three files are public code. Everything else answers only a page that carries the key the
  address printed at start holds, new at every start. The key is in the address's fragment, after
  ``#``, which a browser sends to no server. The page takes it out of the address, keeps it only
  once the connector accepts it, so that a page elsewhere cannot put a wrong one in its place, and
  sends it with each request in a header of its own, never in a cookie, which a browser would also send to every
  other program listening on this computer.
- The address is never on another program's command line, where other users of the computer could
  read it: to open the browser, the connector writes a small file that leads on to it into its own
  folder, which only you can read, and the browser gets the address through that file. The file
  holds the key while the connector runs and is removed when it stops.
- The browser keeps exactly one thing, that key, in the page's own storage, so that every tab of
  the page has it. It is worthless once the connector stops. Nothing else is ever kept there. The
  browser's history also keeps the address you opened, key and all; that matters only while the
  connector runs, as the next start makes a new key.
- A request that changes something must come from the page itself (its Origin header).
- **Open in the MDM connector** opens the page and hands it the settings line in a message, never in
  the address. The page takes a line only from the page that opened it, and the connector only from
  that Deploy App's own pages, ``https://<host>`` or ``https://mtls.<host>``.

Its limits:

- The button in Deploy App talks to whatever listens on port 8737 of this computer. On a computer
  that other users share, one of them could listen there first and take the settings line: copy the
  line by hand instead. A program that listens on port 8737 while the connector is not running can
  also send the Deploy App tab that opened it elsewhere.
- Any web page can open the connector and offer a Deploy App of its own: read the Deploy App's name
  on the confirm card before pressing **Add**.
- Another program on this computer can slow the page down, but without the key it can neither read
  nor drive it. A program that runs as you can read the key from your browser's storage, as it can
  read anything else of yours.

Into a network without internet
-------------------------------

The connector runs on Python's standard library alone, so it can be carried in as one file. On the
other computer it needs Python 3.11 or newer, and nothing else. The file leaves keyring out, so it
has no keychain: the MDM token is asked for at each start, and kept only until the connector stops.

Build the file from a checkout, on a computer that has uv. The wheel goes into the file whole, with
its metadata, which is how the connector finds Fleet and any plugin::

    uv build --wheel --out-dir dist
    uv pip install --target build/pyz --no-deps dist/odmdm_connector-*.whl
    python3 -m zipapp build/pyz -m "odmdm_connector.console:odmdm_connector_cli" -p "/usr/bin/env python3" -o dist/odmdm-connector.pyz

A plugin goes into the same file: build its wheel and name it in the same install line, after the
connector's. Start from an empty ``build/pyz`` each time, or what was installed there before stays in
the file.

Copy ``dist/odmdm-connector.pyz`` across and run it as the command, ``py`` in place of ``python3`` on
Windows::

    python3 odmdm-connector.pyz --version
    python3 odmdm-connector.pyz mdms
    python3 odmdm-connector.pyz web

An MDM whose certificate comes from a private authority is trusted by pointing the
``SSL_CERT_FILE`` environment variable at that authority's certificate file, in PEM form, before
the connector starts.

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
    odmdm-connector mdms                      # the MDMs installed: fleet built in, and any plugins

``name --all`` numbers in enrolment order from one above the highest ``OTTER<n>`` in the team, or
from ``--start N``, 1 or more. It sees only this team's phones, and Deploy App never gives a
callsign out twice: where callsigns were used by phones no longer in the team, give ``--start``
above the highest ever used. ``name`` changes nothing at all if a callsign is malformed, given twice or
already another phone's, or if a phone is not in the team. A phone that already has its certificate
under the name is left alone. Renaming a phone that has its certificate says which old callsign to
revoke.

Configuration
-------------

All configuration is via environment variables. ``--set KEY=VALUE`` before the command sets one
of the MDM's own for one run, ``mdm_url``, ``team``, ``template`` or ``key_alias`` for Fleet, as
does ``--mdm-url``, ``--team`` or ``--template`` after it. A secret, the token, is only ever read
from the file its ``_FILE`` variable names:

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
    The document saying what to install, see below. Default: the one shipped with the connector.

``ODMDM_DOMAIN``
    The deployment's DNS name.

``ODMDM_CHALLENGE``
    The settings line or the bare challenge from Deploy App's MDM page, for ``challenge`` and a
    first ``apply``. New for every enrolment window.

``ODMDM_KEY_ALIAS``
    The certificate template's name, which becomes the Android keystore alias. Default ``rmscep``,
    what the first deployments were set up with. Never change it for a deployment: a second key
    will not install over an existing alias.

Plugins
-------

Fleet is built in. Another MDM comes as a plugin: a Python package of its own, named
``odmdm-connector-<name>``, that the connector finds once it is installed beside it. A plugin is
code that sees the MDM token, so installing one means trusting it as much as the connector
itself. Install only one you know the source of. When the MDM chosen is a plugin, every command
says on stderr which package and version it comes from.

Add one with the same install line and ``--with``::

    uv tool install git+https://github.com/pvarki/python-odmdm-fleet --with odmdm-connector-<name>

See what is installed, without loading any of it::

    odmdm-connector mdms

Choose one with ``--mdm NAME`` before the command, or ``ODMDM_MDM``; the default is ``fleet``.
Its settings are given as for Fleet: ``--set KEY=VALUE``, or ``ODMDM_<KEY>``, and a secret only
from the file ``ODMDM_<KEY>_FILE`` names. A plugin's MDM may lack something Fleet can do, such as
taking the challenge by API; the command then says what to do instead and changes nothing.

The template
------------

What to install is a property of the deployment, so it is a document rather than code: the apps,
their managed configuration, the Android Management API device policy, a launcher link, and the
certificate template. ``{domain}``, ``{mtls_url}`` and ``{key_alias}`` are filled in. Keys
beginning with ``_`` are comments and never reach Fleet. ``src/odmdm_connector/template.json``, shipped
in the package, is a working one for Deploy App, with its reasoning in the comments.

The certificate subject is the contract with the deployment. Its common name becomes the callsign:
3 to 32 letters, digits or hyphens, no hyphen at either end. It is the name ``name`` gives the
phone, followed by Fleet's id for the host, and the tool refuses any other::

    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART, OU=$FLEET_VAR_HOST_UUID
    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART

The shipped template uses the first: the id lets the deployment tell later which device a callsign
was issued to. A subject cannot change on a team that has phones, since replacing the certificate
template removes every phone's certificate, so a team set up without the id keeps the second form
in a template of its own.

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
2. The SCEP URL, ``https://<host>/<path>`` and nothing else: no user name, password, port, query,
   fragment or backslash. Deploy App writes ``https://<host>/scep``.
3. The challenge: 32 lowercase hex characters. It is a secret, and the tool never prints or logs it.
4. When the enrolment window closes, in UTC, as ``YYYY-MM-DDTHH:MM:SSZ``.

Whitespace and line breaks before and after the line are ignored. A line with another mark or
another number of fields, a URL that is not just https, a host and a path, a malformed challenge or
time, or a window that has already closed is refused. A line that a page hands to the connector is
taken only from that Deploy App's own pages, ``https://<host>`` or ``https://mtls.<host>``; one
typed or pasted by hand names no page. A value with whitespace inside it is read as a line, never
as a bare challenge, so a damaged paste is refused instead of being sent to Fleet. ``challenge``
takes the line from ``ODMDM_CHALLENGE``, else at its hidden prompt, never as an argument; it refuses
one whose SCEP URL is not on ``ODMDM_DOMAIN``, and says until when the window is open, in this
computer's time. The line above is the shared test vector, in ``tests/data/settings-line-v1.txt``.

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
- **A token copied from a signed-in account ends with that sign-in.** **My account → Get API token**
  gives the token of your own session, which Fleet ends after five days by default, or when you
  sign out. The connector then says the token is not accepted. For a connector that stays set up,
  give the token of an API-only user (``fleetctl user create --api-only``), which does not end.
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

Write a plugin
--------------

An MDM fits if it can do three things: take a static SCEP challenge for its certificate
authority, put a per-device value that its API can set into the certificate subject (the
callsign, as the common name), and ask a device for its certificate again.

A plugin is one class that satisfies ``Mdm`` in ``src/odmdm_connector/mdm.py``, which is standard
library only:

- ``API_VERSION = 1``, a ``title``, the ``fields`` it needs (each a ``Field``; mark a token
  ``secret``, one that must be given ``required``, and one rarely changed ``advanced``, which the
  window shows only when asked) and ``missing``: for each of ``setup``,
  ``set_challenge`` and ``ask_again`` it cannot do, the sentence the operator reads instead.
- ``__init__(values)`` takes the fields' values by key.
- ``setup(window, force, dry_run)`` yields ``Step`` objects; ``set_challenge(window)``; ``phones()``
  answers ``Phone`` objects, oldest first, each with a ``State``; ``name(names)`` and ``ask_again(ids)``
  answer the lines to show.
- It raises only ``MdmError``. One that stops half-way through ``name`` says in ``named`` which
  phones it had already named, and in ``lines`` the lines already due to the operator, such as an
  old callsign to revoke, which are shown before the error. One raised because the MDM does not
  accept the secret it was given, such as a token that has ended, sets ``unauthorized``: the
  connector then lets go of that secret, the copy in the keychain too, and asks for it again.
- Its sentences, in ``missing``, a ``Step``, the lines and an ``MdmError``, are shown as they are
  by the command line and the window alike, so they name no command, option or button.

Publish it as ``odmdm-connector-<name>``, depending on ``odmdm-connector>=1.1,<2``, with the class
under the entry point group ``odmdm_connector.mdm``::

    [project.entry-points."odmdm_connector.mdm"]
    <name> = "odmdm_connector_<name>:<Name>Mdm"

The connector ships a test kit: ``odmdm_connector.testing.contract.CHECKS``, the checks every
plugin must pass given a factory for it, and ``odmdm_connector.testing.FakeMdm``, an MDM in
memory. Start from ``examples/odmdm-connector-demo``: a pretend MDM with three phones that cannot
take a challenge by API, its own ``pyproject.toml`` and its contract test. The repository's CI
builds it and the connector, installs both as above and runs ``odmdm-connector --mdm demo
phones``.

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
