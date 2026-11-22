===============
odmdm-connector
===============

Connects your MDM, the mobile device management system that sets up your unit's Android phones,
to a Deploy App deployment (OpenDefence), so that the phones join Deploy App by themselves, each
under its own callsign, the name Deploy App knows a device by. Fleet (FleetDM) is built in. Any
other MDM that can do what `What an MDM needs`_ lists needs a plugin, a small Python package:
install one that exists, or write one (`Write a plugin`_).

It is for the admin who runs a unit's MDM and is an admin in its Deploy App. It runs on that
admin's own computer, as a page in the browser or from the command line, and keeps one secret: the
MDM's API token. It:

- sets the MDM up for one Deploy App, as far as the MDM's API allows: where phones ask for their
  certificates and, with Fleet, also what they install and the policy that lets the apps use the
  certificate;
- hands the MDM the challenge of each enrolment window: the secret phones must show while an
  admin has enrolment open in Deploy App, which the connector holds in memory only;
- lists the phones that enrolled, and where each one's certificate is;
- gives phones their callsigns, any number at once, and asks again for the ones that failed.

The deployment knows no MDM. It answers standard SCEP (RFC 8894) at ``https://<domain>/scep``,
asks for a challenge that is new every time an admin opens enrolment in Deploy App, and makes the
certificate's common name the device's callsign. Everything that knows an MDM lives here, behind
one contract, and each MDM in a plugin of its own: Fleet's is built in (`Fleet`_), another MDM's
is a package beside the connector (`Plugins`_).

Run it from an operator's machine, never inside the deployment: it holds the MDM's API token, and
something that can reach every device a unit owns has no business running next to an internet
facing service.

What an MDM needs
-----------------

An MDM fits, for managed Android phones, if an operator can do five things in it:

1. Keep one Deploy App's phones in a group of its own, with its own enrolment.
2. Send each phone's certificate request to Deploy App's SCEP URL with one challenge, the same
   for every phone of an enrolment window.
3. Put a value that can be set for each phone into the certificate subject: the callsign, as the
   common name.
4. Take a new challenge for each enrolment window.
5. Ask one phone for its certificate again and leave the others alone.

A plugin does through the MDM's API what the MDM lets it do, and at least lists the phones and
names them. For what it cannot do, the connector says what to do in the MDM by hand, and changes
nothing. Fleet can do all five by API.

The shortest way
----------------

1. Get the connector. In Deploy App, as an admin, open **User Management**, then **MDM**, and
   press **Install the ODMDM connector**: download the file for your computer and start it (`A
   file to download`_). Or install it with uv (`Install`_) and type ``odmdm-connector``: for an
   MDM other than Fleet that is the way, as the downloaded files take no plugin (`Plugins`_).
2. Its page opens in your browser. On Deploy App's MDM page press **Open for 2 hours**, then
   **Open in the MDM connector**, and confirm your Deploy App on the connector's page.
3. Give what your MDM asks for, with Fleet its address, an API token and a team (`Fleet`_), and
   press **Connect**.
4. Enrol the phones in your MDM, then press **Give callsigns**: with Fleet, only once every phone
   has its apps (`The order that works`_).
5. Each phone gets its certificate when it next asks its MDM, with Fleet within about 15 minutes.
   Close enrolment in Deploy App once the page shows every phone with its certificate: a request
   that arrives after it closes is refused.

`The page`_ says more of each step, and `Enrolling phones`_ does the same by command.

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

or ``odmdm-connector`` alone, as a double-click on a downloaded file does (see `A file to download`_).
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
and the team, which it suggests naming after the deployment (``golden-monkey DeployApp`` for
golden-monkey.example.fi), with the rarely changed ones under **More settings**. **Connect** sets the MDM up and
ticks off its steps; it says what is left to do by hand, if anything, or why it failed, and **Show
the phones** goes on.

The daily work is one view of the phones, as wide as the window. At the top, whether Deploy App
accepts phones now and until when; once enrolment has closed, **Open Deploy App** opens its MDM page
in a new tab. Under it one row of buttons, the ways to select, and the table: each phone's ID,
callsign, model, when it joined and where its certificate is. Click a row to select it, and
double-click it, or select one and press **Details**, to see that phone alone. The line at the
bottom says what was done last and, at its right end, when the phones were last listed: every
minute while the page is open, or at once with **Refresh**. **Settings** shows the MDM's
settings, whether its token is kept on this computer, and where the log is, and offers **Send the
policy again** for a phone whose browser refuses its certificate (see `The order that works`_): it
ticks off its steps as **Connect** does, and the phones keep their certificates and apps. The Deploy App's name
at the top right lists the Deploy Apps added, opens another, adds one or removes the one open.
One Fleet can serve several Deploy Apps. Each has a certificate authority of its own in Fleet and
a team of its own; the connector never points an authority that serves one Deploy App at another,
and refuses a team whose certificate template takes its certificates from another address (see
`The template`_). A challenge goes only to the authority the team's template is bound to.
The API token is a global admin's, so whoever runs the connector for one Deploy App can change
every team in that Fleet: share a Fleet only among people who trust each other with it.

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

A file to download
------------------

For an operator who has no Python, the connector is also one self-contained file per system, which
Deploy App's MDM page offers for download when a deployment serves them: Python, the connector,
Fleet and keyring are inside, so the token can be kept in the keychain. Started without a command,
as by a double-click, it opens the page; every command works as above. Started so again while the
page runs, it says the connector may be running already and, in a terminal window, waits for Enter
before the window closes. Each system has its own
file: ``odmdm-connector-<version>-windows-x64.exe`` as it is, and for macOS and Linux one
executable named ``odmdm-connector`` in a ``.zip`` or ``.tar.gz``, which keeps it executable through
a browser's download. Beside them, ``connector.json`` says the version, the MDMs built in, and each
file's system, size and SHA-256; Deploy App's page reads it.

The files are NOT SIGNED yet, so the system warns at the first start. On macOS it refuses to open
the file: open **System Settings**, **Privacy & Security**, and press **Open Anyway** for
``odmdm-connector``. On Windows, SmartScreen says it protected the PC: press **More info**, then
**Run anyway**. Before doing either, compare the file's SHA-256 with its line in the release's
``SHA256SUMS`` (below).

Build the file for the computer you are on from a checkout, with PyInstaller, a build tool kept in
a dependency group of its own. ``packaging/build.py`` holds the build and is what CI runs::

    uv run --locked --group dist python packaging/build.py

It writes the packed file into ``dist/mdm-connector``. CI builds it on Windows, macOS (Apple
Silicon and Intel) and Linux, starts each, adds the one file for Python below, and writes the
manifest into that folder with ``uv run python packaging/manifest.py dist/mdm-connector``: the
artifact ``mdm-connector`` is the folder a deployment serves. The Linux file needs glibc 2.28 or
newer: CI builds it in AlmaLinux 8 (glibc 2.28) and starts it there, the keychain included, and in
Debian 11.

Each release carries that folder, built from the release's tag, as one archive that unpacks to
``mdm-connector/``, the archive's SHA-256 beside it, and ``SHA256SUMS``, one line per file of the
folder (``connector.json`` aside), all in the form ``sha256sum -c`` reads::

    https://github.com/pvarki/python-odmdm-fleet/releases/download/v<version>/mdm-connector.tar.gz
    https://github.com/pvarki/python-odmdm-fleet/releases/download/v<version>/mdm-connector.tar.gz.sha256
    https://github.com/pvarki/python-odmdm-fleet/releases/download/v<version>/SHA256SUMS

The ``+`` in the version is written ``%2B`` in the address, as GitHub writes it: the tag of
1.3.0+261122 is ``v1.3.0%2B261122`` there. Check an archive before unpacking it::

    sha256sum -c mdm-connector.tar.gz.sha256
    tar -xzf mdm-connector.tar.gz

Deploy App's UI image fetches the archive of one release, pins its SHA-256 and serves the folder
beside the app: an image refuses to build from an archive with any other SHA-256. A release's
assets are never replaced, so the SHA-256 the MDM page shows for a file is the line for that file in
the release's ``SHA256SUMS``. Check a downloaded file in its folder before the first start::

    sha256sum -c --ignore-missing SHA256SUMS

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

3. Enrol phones into the team, ``<deployment> DeployApp`` by default, as many as you like, in
   any order. Name them once every phone in the team has its apps: naming can send the policy to all of them again.

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
    The team phones enrol into and get the certificate in. Default ``<deployment> DeployApp``, the
    deployment being the first label of ``--domain`` or ``ODMDM_DOMAIN``: ``golden-monkey DeployApp``
    for golden-monkey.example.fi. Without either, give the team. A deployment set up with the
    earlier default keeps its team only when it is given: ``--team "Deploy App"``.

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

Fleet
-----

Fleet (FleetDM) works as it comes: the connector does in it by API everything `What an MDM
needs`_ lists. Fleet's behaviour as told here was measured on Fleet 4.90, and the menu names are
that version's.

Fleet needs three things:

- **Android MDM turned on**: Fleet's **Settings**, **Integrations**, **MDM**. Whoever runs Fleet
  connects Android Enterprise there once, for all teams.
- **Fleet Premium**: teams and certificate authorities are part of it.
- **A global admin's API token.** Fleet keeps certificate authorities outside teams, so giving
  one a new challenge takes a global admin. **My account**, **Get API token** gives the token of
  your own sign-in, which Fleet ends after five days by default, or when you sign out; the
  connector then says the token is not accepted. For a connector that stays set up, whoever runs
  Fleet makes an API-only user with the role Admin for all of Fleet (**Settings**, **Users**,
  **Add user**, **API-only user**), whose token does not end.

Fleet's settings
~~~~~~~~~~~~~~~~

On the page these are its cards; for the commands, each is ``ODMDM_<KEY>`` or ``--set``.
``--mdm-url``, ``--team`` and ``--template`` after a command that takes them say the same as
``--set mdm_url``, ``team`` and ``template``.

``ODMDM_MDM_URL``
    The address you open Fleet at, such as ``https://fleet.example.fi``, without an API path. Only
    https: the token goes with every request.

``ODMDM_TOKEN_FILE``
    A file holding the API token.

``ODMDM_TEAM``
    The team phones enrol into and get the certificate in. Default ``<deployment> DeployApp``:
    ``golden-monkey DeployApp`` for golden-monkey.example.fi. Without ``ODMDM_DOMAIN``, give the
    team. A deployment set up with the earlier default keeps its team only when it is given:
    ``--set team="Deploy App"``.

``ODMDM_TEMPLATE``
    The document saying what to install, see `The template`_. Default: the one shipped with the
    connector.

``ODMDM_KEY_ALIAS``
    The certificate template's name, which becomes the Android keystore alias. Default ``rmscep``,
    what the first deployments were set up with. Never change it for a deployment: a second key
    will not install over an existing alias.

A Fleet whose certificate comes from a private authority is trusted by pointing the
``SSL_CERT_FILE`` environment variable at that authority's certificate file, in PEM form, before
the connector starts.

What Connect sets up
~~~~~~~~~~~~~~~~~~~~

**Connect**, or ``apply``, changes Fleet: it makes the team if it is not there, Deploy App as a
certificate authority, the team's certificate template, its policy, its apps and the **Deploy
App** launcher icon. The launcher is a web app that Google publishes for good (`What Fleet does
that you would not guess`_). Sending the policy again is a repair and makes no team.

Each phone gets its certificate at its next check-in with Fleet, within about 15 minutes of being
named. One that Fleet has made no certificate request for yet only gets its name, which Fleet
fills in when it makes the request. Name the phones once every phone in the team has its apps:
naming can send the policy to all of them again (`The order that works`_). A phone that already
has its certificate under the name it is given is left alone, and renaming one that has its
certificate says which old callsign to revoke: remove it in Deploy App.

Several Deploy Apps on one Fleet
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One Fleet can serve several Deploy Apps. Each has a certificate authority of its own in Fleet and
a team of its own. The connector finds a Deploy App's authority by the address it serves, whatever
it is called, and makes one when there is none: ``ODMDM_`` and the first part of the Deploy App's
host name, in capitals, as ``ODMDM_GOLDEN_MONKEY`` for ``golden-monkey.example.fi``. Two Deploy
Apps whose host names have the same first part, such as ``unit.example.fi`` and
``unit.example.org``, would get the same authority name, the same default team and the same
launcher title, and the second is refused. Give it a team of its own and a template of its own
(`The template`_) that names another authority, ``"authority": "NAME"`` beside the certificate's
``name`` and ``subject``, in letters, digits and underscores, and another ``title`` in its
``link_app``.

The connector never points an authority that serves one Deploy App at another. An authority made
earlier as ``RMSCEP``, or by hand, is found by its address and kept: the address has to be the
Deploy App's exactly, ``https://<host>/scep`` with no slash after it. Once a team has its
certificate template, a challenge goes only to the authority that template is bound to, and a
team whose template is bound to an authority that serves another address is refused before
anything is changed, with both addresses said.

The API token is a global admin's, so whoever runs the connector for one Deploy App can change
every team in that Fleet: share a Fleet only among people who trust each other with it.

The template
~~~~~~~~~~~~

With Fleet, what to install is a property of the deployment, so it is a document rather than
code: the apps, their managed configuration, the Android Management API device policy, a launcher
link, and the certificate template. ``{domain}``, ``{mtls_url}`` and ``{key_alias}`` are filled
in. Keys beginning with ``_`` are comments and never reach Fleet.
The one shipped in the package, ``src/odmdm_connector/template.json`` in the repository, is a
working one for Deploy App, with its reasoning in the comments: copy it from there to change what
a deployment installs, and give your copy as the template.

The certificate subject is the contract with the deployment. Its common name becomes the callsign:
3 to 32 letters, digits or hyphens, no hyphen at either end. It is the name ``name`` gives the
phone, optionally followed by Fleet's id for the host::

    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART, OU=$FLEET_VAR_HOST_UUID
    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART

The tool refuses any other subject; it takes ``$FLEET_VAR_HOST_END_USER_IDP_USERNAME`` in place of
the ``_LOCAL_PART`` form, which is the same text for a callsign. The shipped template uses the
first: the id travels in the certificate request, so the deployment can tell later which device a
callsign was issued to. A subject cannot change on a team that has phones, since replacing the
certificate template removes every phone's certificate, so a team set up without the id keeps the
second form in a template of its own.

A name the phone supplies, such as its serial number, would make a leaked enrolment link enough
for an identity.

The tool also refuses a policy that lets an app use the key without being named in
``packageNames``, lets the user pick a key for any app (``privateKeySelectionEnabled``), or
allows installs from unknown sources (an ``untrustedAppsPolicy`` that begins ``ALLOW_``, or
``installUnknownSourcesAllowed``).

The order that works
~~~~~~~~~~~~~~~~~~~~

Three facts about managed Android and Fleet pull against each other. Each was learned by getting
it wrong.

1. **Apps install at enrolment and at no other time.** The team must carry them before a phone
   joins. A phone moved in from a team that has another launcher loses that launcher and never
   gets this one.
2. **Fleet can overwrite a new phone's policy moments after sending it.** It sends a phone that
   enrols the team's policy, and seconds later can write a bare one over it. It goes on showing
   the policy as verified on that phone and never sends it again, so the phone has its certificate
   but Chrome may not use it, and Deploy App says "Certificate Authentication Failed". ``name``
   and ``resend`` send the policy again, to every phone in the team, when a phone enrolled after
   it was last sent. A phone that enrols again onto its old host keeps its old enrolment time and
   is not seen: send the policy again by hand for it. By hand it is **Send the policy again** in
   the page's **Settings**, ``apply --force-policy``, or deleting the policy in Fleet and
   uploading it again. Sending it again is a repair and makes no team: a team Fleet lacks is
   refused before anything is changed.
3. **A policy rewritten while apps are installing stops them installing.** Leave the phones alone
   until the apps have arrived.

What Fleet does that you would not guess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **A launcher web app's URL can never change, and its title is taken for good.** Google publishes
  it permanently and Fleet cannot delete it. The title carries the first part of the deployment's
  host name, so a deployment moved to a host with another first part publishes its own launcher
  and leaves the old one orphaned. One moved to a host with the same first part keeps the old
  launcher, which still opens the old host: give the template's ``link_app`` another ``title``.
  Fleet refuses a title already published, in any team and even in one since deleted. Google
  refuses a title of 30 characters or more.
- **A phone enrols before it has a name**, so its first certificate request fails (``does not
  have an IDP user``) until it is named, and naming it does not send the request again. ``name``
  resends. A resend is one more attempt, which the phone acts on at its next check-in, every 15
  minutes.
- **A returning phone keeps its old team and its old name.** Transfer it into this team if it was
  in another, and give it a new name with ``name``, or it asks for a name that is already taken. It
  can also come back as a new device with no name.
- **Replacing a team's certificate template locks its phones out.** Every phone deletes the
  certificate it has and asks again, and the deployment refuses a name that is already taken. So
  this repository never replaces one to retry a phone: ``resend`` asks for that phone alone.
- **A refusal shows only as** ``SCEP enrollment failed ... failInfo: badMessageCheck`` on the
  phone's certificate. For the causes see `Enrolling phones`_; for an old challenge, ``challenge``
  and then ``resend``.

By hand in Fleet
~~~~~~~~~~~~~~~~

Without the connector, in Fleet's console: open the phone, **Edit user** to give it its callsign,
then under **OS settings** pick the certificate and **Resend**. Naming alone does not send the
request again. Once the phones have enrolled and their apps have arrived, delete the policy in
Fleet and upload it again: Fleet can overwrite a new phone's policy, see `The order that works`_.

Phones already enrolled in another Fleet team: transfer them into this team in Fleet, then name
them like the rest. Apps install only at enrolment, so a transferred phone gets its certificate but
not this team's apps or launcher. For those, remove its work profile after the transfer and enrol
it again into this team.

Write a plugin
--------------

An MDM fits if it can do five things:

1. Keep one Deploy App's phones in a group of its own, with its own enrolment.
2. Send each phone's certificate request to Deploy App's SCEP URL with one static challenge, the
   same for every phone.
3. Put a value that its API can set for each phone into the certificate subject: the callsign, as
   the common name.
4. Take a new challenge for each enrolment window, by API or by hand.
5. Ask one phone for its certificate again and leave the others alone.

A plugin is one class that satisfies ``Mdm`` in ``src/odmdm_connector/mdm.py``, which is standard
library only:

- ``API_VERSION = 1``, a ``title``, the ``fields`` it needs (each a ``Field``; mark a token
  ``secret``, one that must be given ``required``, and one rarely changed ``advanced``, which the
  window shows only when asked) and ``missing``: for each of ``setup``,
  ``set_challenge``, ``ask_again`` and ``send_policy`` it cannot do, the sentence the operator reads
  instead. A default may hold ``{deployment}``, which the connector fills in with the deployment's
  name, the first label of the Deploy App's host: ``{deployment} DeployApp`` is Fleet's team.
- ``__init__(values)`` takes the fields' values by key.
- ``setup(window, force, dry_run)`` yields ``Step`` objects, ``force`` sending the policy again
  even if unchanged, unless ``send_policy`` is missing; ``set_challenge(window)``; ``phones()``
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
