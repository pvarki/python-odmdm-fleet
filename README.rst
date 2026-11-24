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
   common name, and nothing else.
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

The repository is named after Fleet, the first MDM the connector served. From a checkout,
``uv tool install .`` does the same. Installed this way it brings its one dependency, keyring,
which keeps the MDM token in this computer's keychain: Windows Credential Manager, the macOS
Keychain, or the Secret Service on Linux. Fleet is built in; for another MDM add its plugin in the
same line (`Plugins`_).

The page
--------

The connector's everyday face is a page in your browser, served by this computer. Start it::

    odmdm-connector web

or ``odmdm-connector`` alone, as a double-click on a downloaded file does (see `A file to
download`_). It asks your default browser to open the page, prints its address, and goes on until
Ctrl+C. ``odmdm-connector web --port N`` serves it on another port than 8737. Deploy App's **Open
in the MDM connector** reaches port 8737 only, so on another port paste the settings line (`The
settings line`_) into the page instead.

On a computer without a desktop, or with ``odmdm-connector web --no-browser``, the connector only
prints the address. If the page does not appear, or another program opens, open the printed
address yourself. That can happen with a browser installed as a snap on Linux, which may not read
the connector's own folder, on a Mac whose ``.html`` files open in another program than the
browser, and on Linux without a graphical browser, where a text browser may take the terminal: use
``--no-browser`` there. A page opened without that address says so, and goes on by itself once the
address has been opened in the same browser. Deploy App's button finds the page only in the
browser where the address was opened, so use Deploy App in your default browser, or open the
address in the browser you use Deploy App in.

The first time, the page sets up one thing at a time, on cards under a bar of three parts: Deploy
App, the MDM, Connect. Have at hand Deploy App's MDM page, open as an admin, and what your MDM
asks for. Add the Deploy App: on its MDM page press **Open in the MDM connector**, or copy its
settings line and paste it into the page, and confirm it. Choose the MDM when more than one is
installed; a plugin is asked about first, and used only once you agree, for that package and
version. Then fill in what the MDM asks, one field to a card, with the rarely changed ones under
**More settings**: for Fleet its address, an API token and the team, which the page suggests
naming after the deployment (``golden-monkey DeployApp`` for golden-monkey.example.fi).
**Connect** sets the MDM up and ticks off its steps; it says what is left to do by hand, if
anything, and **Show the phones** goes on. If it failed it says why, and **Change the settings**
goes back.

The daily work is one view of the phones, as wide as the window. At the top, whether Deploy App
accepts phones now and until when; once enrolment has closed, or when no opening is known,
**Open Deploy App** opens its MDM page in a new tab. Under it one row of buttons, the ways to
select, and the table: each phone's ID, callsign, model, when it joined and where its certificate
is. Click a row to select it, and double-click it, or select one and press **Details**, to see
that phone alone. The line at the bottom says what was done last and, at its right end, when the
phones were last listed: every minute while the page is open in a visible tab, or at once with
**Refresh**. **Settings** shows the MDM's settings, whether its token is kept on this computer,
and where the log is. For an MDM that can send its policy again, as Fleet can, it also offers
**Send the policy again** (see `The order that works`_): it ticks off its steps as **Connect**
does, and the phones keep their certificates and apps. The Deploy App's name at the top right
lists the Deploy Apps added, opens another, adds one or removes the one open. Each is a
connection of its own, with its own settings and its own token.

For every batch: open enrolment in Deploy App and press **Open in the MDM connector** again, or
**New settings** and paste the line, then **Use the new settings** on the card that follows: only
then does the MDM get the new challenge. An MDM that cannot take it by API gets it by hand: the
page then shows the challenge to copy into the MDM. Let the phones
enrol, then **Give callsigns**, to the phones selected or else to every phone waiting for a name:
numbered or from a list. The preview below follows what you type, and the button, which then
reads **Name 3 phones**, names exactly the phones the preview shows, once it has passed. **Ask
again**, where the MDM can ask, asks refused or failed phones for their certificates again once
the cause is fixed.

The MDM token is never in the settings file, the log or the page. With **Remember on this
computer** ticked, as it is each time its card is shown, it is kept in this computer's keychain,
under ``odmdm-connector``, and the page connects to the MDM at the next start without asking for
it. Without a keychain, as in the one file for Python (`Into a network without internet`_) or on
Linux where no Secret Service runs, the page says the token is kept only until the connector
stops, and asks for it after a restart: then it shows the phones of the MDM set up before, without
new settings from Deploy App. It asks for it the same way, with the MDM's reason, when the MDM no
longer accepts it, and removes the copy kept.

To stop keeping the token on this computer, go through the MDM's settings again (**Settings**,
then **Change the Fleet settings**, or your MDM's name in Fleet's place), untick **Remember on
this computer** on the token's card and press **Connect**; or remove the Deploy App. Either
removes it from the keychain. If removing the Deploy App cannot remove it from the keychain, the
page says so: delete the ``odmdm-connector`` item of that Deploy App there by hand.

Once a deployment is over, end it in the MDM as you remove it: in the Remove dialog tick **Also
end it in Fleet**, or your MDM's name in Fleet's place, and press **End and remove**. The MDM takes
this Deploy App's certificate from the group's phones, at their next check-in, and then the
connector removes the Deploy App as Remove does; until then it changes nothing else in the MDM
for that Deploy App. The group, its phones, their names and their apps stay in the MDM, and the
next Deploy App can be connected to that group. It cannot be undone, so end a Deploy App only when
its deployment is over. The box is there while the MDM is connected and can end a Deploy App;
otherwise the dialog says why. If the MDM refuses on the way, nothing is removed from the
connector, the page says what was already taken out, and ending again finishes the rest.

The connector keeps what you set on the page, never the token or the challenge, in
``settings.json`` in its own folder, beside its log: ``%LOCALAPPDATA%\odmdm-connector`` on
Windows, ``~/Library/Application Support/odmdm-connector`` on macOS, ``~/.config/odmdm-connector``
on Linux (or under ``XDG_CONFIG_HOME``).

How the page is guarded
-----------------------

- It listens on this computer only (127.0.0.1), and answers only a request addressed to this
  computer (its Host header), so that a web page elsewhere cannot reach it through DNS rebinding.
- Its three files are public code. Everything else answers only a page that carries the key the
  address printed at start holds, new at every start. The key is in the address's fragment, after
  ``#``, which a browser sends to no server. The page takes it out of the address, keeps it only
  once the connector accepts it, so that a page elsewhere cannot put a wrong one in its place, and
  sends it with each request in a header of its own, never in a cookie, which a browser would also
  send to every other program listening on this computer.
- The address is never on another program's command line, where other users of the computer could
  read it: to open the browser, the connector writes a small file that leads on to it into its own
  folder, readable by you alone on Linux and macOS and inside your own profile on Windows, and the
  browser gets the address through that file. The file is removed when the connector stops; one
  left behind by a connector that was killed holds a key that no longer works, and the next start
  replaces it.
- The browser keeps exactly one thing, that key, in the page's own storage, so that every tab of
  the page has it. It is worthless once the connector stops. Nothing else is ever kept there. A
  browser's history may also keep the address you opened, key and all; that matters only while the
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
  on the confirm card before pressing the button that adds it.
- Another program on this computer can slow the page down, but without the key it can neither read
  nor drive it. A program that runs as you can read the key from your browser's storage, as it can
  read anything else of yours.

A file to download
------------------

For an operator who has no Python, the connector is also one self-contained file per system, which
Deploy App's MDM page offers for download when the deployment serves them. Python, the connector,
the MDM built in (Fleet) and keyring are inside, so the token can be kept in the keychain. A
plugin cannot be added to these files: for another MDM, install the connector with uv, or build
the one file for Python with the plugin inside (`Into a network without internet`_).

Started without a command, as by a double-click, the file opens the page; every command works as
above. Started so again while the page runs, it says the connector may be running already and, in
a terminal window, waits for Enter before the window closes.

Each release has four such files and one for Python, in one archive (`Building and serving the
files`_):

- ``odmdm-connector-<version>-windows-x64.exe``, as it is;
- ``odmdm-connector-<version>-macos-arm64.zip`` for a Mac with Apple Silicon and
  ``odmdm-connector-<version>-macos-x64.zip`` for one with Intel;
- ``odmdm-connector-<version>-linux-x64.tar.gz``, which needs glibc 2.28 or newer;
- ``odmdm-connector-<version>.pyz``, for any system with Python 3.11 or newer: without keyring,
  so without a keychain (`Into a network without internet`_).

The ``.zip`` and ``.tar.gz`` hold one executable named ``odmdm-connector``, and keep it executable
through a browser's download. Beside them, ``connector.json`` says the version, the MDMs built in,
and each file's system, size and SHA-256; Deploy App's page reads it. No file is built for Linux
or Windows on ARM: use the one for Python there, or install with uv (`Install`_).

The files are NOT SIGNED yet, so Windows and macOS warn at the first start. On macOS it refuses to
open the file: open **System Settings**, **Privacy & Security**, and press **Open Anyway** for
``odmdm-connector``. On Windows, SmartScreen says it protected the PC: press **More info**, then
**Run anyway**; a computer that offers no **Run anyway** does not run unsigned files at all.
Before doing either, compare the file's SHA-256 with its line in the release's ``SHA256SUMS``:
Deploy App's page shows the same SHA-256 beside the download. With the file and ``SHA256SUMS`` in
one folder::

    sha256sum -c --ignore-missing SHA256SUMS              # Linux
    shasum -a 256 -c --ignore-missing SHA256SUMS          # macOS
    Get-FileHash odmdm-connector-*.exe -Algorithm SHA256  # Windows PowerShell: compare by eye

Building and serving the files
------------------------------

Build the file for the computer you are on from a checkout, with PyInstaller, a build tool kept in
a dependency group of its own. ``packaging/build.py`` holds the build and is what CI runs::

    uv run --locked --group dist python packaging/build.py

It writes the packed file into ``dist/mdm-connector``. CI builds it on Windows, macOS (Apple
Silicon and Intel) and Linux, starts each, adds the one file for Python, and writes the manifest
into that folder with ``uv run python packaging/manifest.py dist/mdm-connector``: the artifact
``mdm-connector`` is the folder a deployment serves. CI builds the Linux file in AlmaLinux 8
(glibc 2.28) and starts it there, the keychain included, and in Debian 11.

Each release carries that folder, built from the release's tag, as one archive that unpacks to
``mdm-connector/``, the archive's SHA-256 beside it, and ``SHA256SUMS``, one line per file of the
folder (``connector.json`` aside), all in the form ``sha256sum -c`` reads::

    https://github.com/pvarki/python-odmdm-fleet/releases/download/v<version>/mdm-connector.tar.gz
    https://github.com/pvarki/python-odmdm-fleet/releases/download/v<version>/mdm-connector.tar.gz.sha256
    https://github.com/pvarki/python-odmdm-fleet/releases/download/v<version>/SHA256SUMS

The ``+`` in the version is written ``%2B`` in the address, as GitHub writes it: the tag of
1.5.0+261122 is ``v1.5.0%2B261122`` there. Check an archive before unpacking it::

    sha256sum -c mdm-connector.tar.gz.sha256
    tar -xzf mdm-connector.tar.gz

Deploy App's UI image fetches the archive of one release, pins its SHA-256 and serves the folder
beside the app: an image refuses to build from an archive with any other SHA-256. A release's
assets are never replaced, so the SHA-256 the MDM page shows for a file is the line for that file in
the release's ``SHA256SUMS``.

Into a network without internet
-------------------------------

The connector can be carried in as one file, which needs nothing but Python 3.11 or newer on the
other computer. The file leaves out the connector's one dependency, keyring, and the connector
runs without it: it has no keychain, so the page asks for the MDM token at each start and keeps
it only until the connector stops. The commands read the token from its file, as always.

Build the file from a checkout, on a computer that has uv. The wheel goes into the file whole, with
its metadata, which is how the connector finds Fleet and any plugin::

    uv build --wheel --out-dir dist
    uv pip install --target build/pyz --no-deps dist/odmdm_connector-*.whl
    python3 -m zipapp build/pyz -m "odmdm_connector.console:odmdm_connector_cli" -p "/usr/bin/env python3" -o dist/odmdm-connector.pyz

A plugin goes into the same file: build its wheel and name it in the same install line, after the
connector's. Its own dependencies are not installed, so a plugin for this file has to run on the
standard library. Start from an empty ``build/pyz`` each time, or what was installed there before
stays in the file.

Copy ``dist/odmdm-connector.pyz`` across and run it as the command, ``py`` in place of ``python3``
on Windows::

    python3 odmdm-connector.pyz --version
    python3 odmdm-connector.pyz mdms
    python3 odmdm-connector.pyz web

Enrolling phones
----------------

The same work as on the page, by command. Phones enrol into the MDM's group for this Deploy App
before anybody names them, and each gets its certificate once it has a name. First set
``ODMDM_MDM`` to your MDM's name unless it is Fleet (``odmdm-connector mdms`` lists the names),
``ODMDM_DOMAIN`` to the deployment's DNS name, and what your MDM needs (`Configuration`_).

1. Once per deployment, and whenever what the phones need changes. The first time, open enrolment
   first, as in step 2, and give that opening's settings line in ``ODMDM_CHALLENGE``: ``apply``
   then hands the MDM that challenge too, and ``challenge`` is for the openings after it::

       odmdm-connector apply

2. Open enrolment on Deploy App's MDM page (**User Management**, then **MDM**), copy its settings
   line or its challenge, and give the MDM the new challenge. The command asks for it, and
   changes nothing else::

       odmdm-connector challenge

3. Enrol phones into that group in your MDM, as many as you like, in any order.

4. Name them, all at once or a few at a time; with Fleet, only once every phone has its apps
   (`The order that works`_)::

       odmdm-connector name --all OTTER           # OTTER1, OTTER2, ... in the order they enrolled
       odmdm-connector name 31=OTTER4 32=OTTER5   # the MDM's id of a phone = callsign, the ids being what phones prints

   Each phone gets its certificate when it next asks its MDM. ``phones`` shows where each one is.
   ``resend`` asks again for a refused phone once the cause is fixed.

Close enrolment once ``phones`` says every phone has its certificate: a request that arrives after
it closes is refused. The next opening makes a new challenge, so step 2 comes again before the
next batch.

Once the deployment is over, ``end`` takes this Deploy App out of the MDM as the page's **End and
remove** does, but leaves the page's settings as they are, and needs no challenge. Without
``--yes`` it changes nothing and says how many phones in the team would lose the certificate::

    odmdm-connector end --yes

A refusal is usually a callsign that is malformed or taken, an enrolment window that has closed,
or an MDM that still has the previous opening's challenge. A callsign is never freed, not even by
removing it in Deploy App: give the phone another. A phone that is renamed after it got its
certificate has two callsigns until the old one is removed in Deploy App, which revokes it; the
connector says which.

A command the MDM cannot do by API says what to do in the MDM instead, and changes nothing.
`Fleet`_ has Fleet's timing, its team and the same work done by hand in Fleet.

Commands
--------

.. code-block:: bash

    odmdm-connector                           # the page, as web
    odmdm-connector web                       # the page on this computer; --port N, --no-browser
    odmdm-connector apply                     # state what the phones need; --force-policy sends the policy again
    odmdm-connector apply --dry-run           # read and check what would be stated, without touching the MDM
    odmdm-connector challenge                 # give the MDM a new opening's challenge, nothing else
    odmdm-connector phones                    # each phone's id, callsign, model, enrolment day and certificate
    odmdm-connector name 31=OTTER4 32=OTTER5  # name these phones, and their certificates are asked for
    odmdm-connector name --all OTTER          # the same for every phone without a name, numbered
    odmdm-connector resend [ID ...]           # ask again; without ids, every named phone that failed
    odmdm-connector end --yes                 # once the deployment is over, take it out of the MDM; without --yes, only say what would change
    odmdm-connector mdms                      # the MDMs installed: fleet built in, and any plugins

``name --all`` numbers in enrolment order from one above the highest ``OTTER<n>`` among the
phones it sees, or from ``--start N``, 1 or more. It sees only this group's phones, and Deploy App
never gives a callsign out twice: where callsigns were used by phones no longer in the group, give
``--start`` above the highest ever used. ``name`` changes nothing at all if a callsign is
malformed, given twice or already another phone's, or if a phone is not in the group.

Configuration
-------------

The commands are configured by environment variables, which options override for one run. The
page keeps its own settings instead (`The page`_).

``ODMDM_MDM``
    Which MDM to talk to, as ``--mdm NAME`` before the command. Default ``fleet``; ``mdms`` lists
    the names.

``ODMDM_DOMAIN``
    The deployment's DNS name, as ``--domain`` after ``apply`` and ``challenge``.

``ODMDM_CHALLENGE``
    The settings line or the bare challenge from Deploy App's MDM page, for ``challenge`` and a
    first ``apply``. New for every enrolment window. Without it, ``challenge`` asks at a hidden
    prompt. It is never taken as an argument.

``ODMDM_LOG_LEVEL``
    How much the commands log to stderr, as a whole number: 20 by default, 10 for more. For one
    run, ``-vv`` before the command says 10 and ``-v`` 20. The page's own log is always at 20.

Each MDM has settings of its own, which its plugin names. A setting ``<key>`` is read from
``ODMDM_<KEY>``, or given for one run with ``--set <key>=VALUE`` before the command; a command
says which one is missing. A secret, such as a token, is only ever read from the file that
``ODMDM_<KEY>_FILE`` names. Keep that file outside any checkout, readable only by you: the token
can change every enrolled phone. The tool reads it and never prints, logs or writes it. A default
may name the deployment, the first part of ``ODMDM_DOMAIN`` in lower case. Fleet's settings are
under `Fleet`_.

The settings line
-----------------

Deploy App's MDM page copies one line for the connector. It is the same for every MDM, and it
carries no list of names: phones are named in the MDM. Version 1 is exactly four fields
separated by whitespace::

    ODMDM1 https://unit.example.fi/scep 0123456789abcdef0123456789abcdef 2026-11-21T14:00:00Z

1. The mark ``ODMDM1``.
2. The SCEP URL, ``https://<host>/<path>`` with a plain DNS host name, and nothing else: no user
   name, password, port, query, fragment or backslash. Deploy App writes ``https://<host>/scep``.
3. The challenge: 32 lowercase hex characters. It is a secret: the tool never logs it and never
   prints it on the command line. For an MDM that cannot take it by API, the page shows it for
   you to copy into that MDM.
4. When the enrolment window closes, in UTC, as ``YYYY-MM-DDTHH:MM:SSZ``.

Whitespace and line breaks before and after the line are ignored. A line with another mark or
another number of fields, a URL that is not just https, a host and a path, a malformed challenge or
time, or a window that has already closed is refused. A line that a page hands to the connector is
taken only from that Deploy App's own pages, ``https://<host>`` or ``https://mtls.<host>``; one
typed or pasted by hand names no page. The page takes only a whole line. The commands ``apply``
and ``challenge`` also take a bare challenge, one word, as typed, for ``https://<domain>/scep``;
it says nothing of when the window closes. They read a value with whitespace inside it as a line,
never as a bare challenge, so a damaged paste is refused instead of being sent to the MDM, and
they refuse a line whose SCEP URL is not on ``ODMDM_DOMAIN``. ``challenge`` says until when the
window is open, in this computer's time. The line above is the shared test
vector, in ``tests/data/settings-line-v1.txt``; its window closed long ago, so the connector
refuses it as it is.

Plugins
-------

Fleet is built in. Another MDM comes as a plugin: a Python package of its own, by convention named
``odmdm-connector-<name>``, that the connector finds through its entry point once it is installed
in the same environment. A plugin is code that sees the MDM token, so installing one means
trusting it as much as the connector itself. Install only one you know the source of. The page
asks before it uses a plugin, once for each package and version, and the commands that use the
MDM say on stderr which package and version it comes from.

Add one with the same install line and ``--with`` and the plugin's package: its name if it is on a
package index, else its wheel file or its ``git+https://`` address::

    uv tool install git+https://github.com/pvarki/python-odmdm-fleet --with <the plugin's package>

Into a network without internet a plugin goes inside the one file for Python (`Into a network
without internet`_).

See what is installed, without loading any of it::

    odmdm-connector mdms

Choose one with ``--mdm NAME`` before the command, or ``ODMDM_MDM``; the default is ``fleet``. Its
settings are given as `Configuration`_ says. A plugin's MDM may lack something Fleet can do, such
as taking the challenge by API; the command or the page then says what to do instead and changes
nothing. To write one, see `Write a plugin`_.

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

When a deployment ends
~~~~~~~~~~~~~~~~~~~~~~

Ending a Deploy App, **End and remove** on the page or ``end --yes``, deletes two things in Fleet:
the team's certificate template, named as ``ODMDM_KEY_ALIAS`` says, and then the certificate
authority that template was bound to. Fleet then has every phone in the team that holds that
certificate remove it at its next check-in, within about 15 minutes. The team, its phones, their names, its
policy, its apps and its launcher stay, and the next Deploy App can be connected to that team.
Fleet keeps an authority while another team's certificate template still uses it: the connector
then says the authority stays. A team whose template is bound to an authority that serves another
address is left as it is, with both addresses said, and so is Fleet when it has no team of that
name. When the team has no such template, as after an ending that stopped half-way, ending again
deletes the authority that serves this Deploy App's address, never one that serves another.

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
phone, and the subject holds nothing else::

    CN=$FLEET_VAR_HOST_END_USER_IDP_USERNAME_LOCAL_PART

The tool refuses any other subject; it takes ``$FLEET_VAR_HOST_END_USER_IDP_USERNAME`` in place of
the ``_LOCAL_PART`` form, which is the same text for a callsign. Deploy App refuses a request
whose subject holds more than the callsign. Version 1.5 put Fleet's id for the host after it, as
``OU``: a team set up that way needs a new team, because a subject cannot change on a team that
has phones, since replacing the certificate template removes every phone's certificate.

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

A plugin makes an MDM that can do what `What an MDM needs`_ lists work with the connector. It is
one class that satisfies ``Mdm`` in ``src/odmdm_connector/mdm.py``, which is standard library
only:

- Its class attributes are ``API_VERSION = 1``, a ``title``, the ``fields`` it needs and
  ``missing``; a class without them is not used. Each field is a ``Field``: mark a token
  ``secret``, one that must be given ``required``, and one rarely changed ``advanced``, which the
  page shows only under **More settings**. A field's ``key``, in lower case, is its setting:
  ``--set key=VALUE``, ``ODMDM_<KEY>`` in capitals, and ``ODMDM_<KEY>_FILE`` for a secret. A
  default may hold ``{deployment}``, which the connector fills in with the deployment's name, the
  first label of the Deploy App's host: ``{deployment} DeployApp`` is Fleet's team. Without a
  host there is no such default, so make that field ``required``.
- ``missing`` holds, for each of ``setup``, ``set_challenge``, ``ask_again``, ``send_policy`` and
  ``end`` the MDM cannot do, the sentence the operator reads instead; it is an empty mapping when the MDM
  can do them all. The connector never calls what is listed there; called anyway, it raises
  ``MdmError`` with that sentence, ``send_policy`` being ``setup`` with ``force``.
- ``__init__(values)`` takes the fields' values by key. It does not call the MDM: on a dry run a
  required value may still be empty.
- ``setup(window, force, dry_run)`` yields ``Step`` objects, one whose ``done`` is false being a
  step left to the operator; ``force`` sends the policy again even if unchanged.
  ``set_challenge(window)`` gives the MDM a new challenge and changes nothing else. ``window`` is
  the enrolment window: its SCEP URL, its challenge, and when it closes. In ``setup`` the
  challenge is empty on a dry run, when none was given and when only the policy is sent again:
  the MDM's own is then left as it is.
- ``phones()`` answers ``Phone`` objects, oldest first, each with a ``State``. ``name(names)``
  takes callsigns by phone id, and ``ask_again(ids)`` phone ids, none meaning every named phone
  whose certificate failed or was not asked for. Both answer the lines to show, and raise
  ``MdmError`` for an id that is not in the group.
- ``end(window)`` takes this Deploy App out of the MDM once its deployment is over: the group's
  phones lose its certificate, and the group, its phones, their names and their apps stay, ready
  for the group's next Deploy App. ``window`` names the Deploy App by its SCEP URL; its challenge
  is not used. It answers the lines to show, what was taken out and what stays, and called again
  it takes out what an earlier call left, saying so when nothing was left. A plugin made before
  ``end`` was in the contract has no ``end`` and does not list it in ``missing``: the connector
  says that plugin cannot end a Deploy App, and never calls it.
- It raises only ``MdmError``. One that stops half-way through ``name`` says in ``named`` which
  phones it had already named, and in ``lines`` the lines already due to the operator, such as an
  old callsign to remove: the command line prints them before the error, the page lists them
  under it. One raised because the MDM does not accept the secret it was given, such as a token
  that has ended, sets ``unauthorized``: the page then lets go of that secret, the copy in the
  keychain too, and asks for it again.
- Its sentences, in ``missing``, a ``Step``, a ``Phone``'s ``detail``, the lines and an
  ``MdmError``, are shown as they are by the command line and the page alike, so they name no
  command, option or button, and never hold the challenge or a secret.

Name the package ``odmdm-connector-<name>``, depending on ``odmdm-connector>=1.4,<2``, the first
release that has everything named above but ``end``, which came in 1.7 (a connector before it
never calls it), with the class under the entry point group ``odmdm_connector.mdm``::

    [project.entry-points."odmdm_connector.mdm"]
    <name> = "odmdm_connector_<name>:<Name>Mdm"

The connector ships a test kit: ``odmdm_connector.testing.contract.CHECKS``, the checks every
plugin must pass, and ``odmdm_connector.testing.FakeMdm``, an MDM in memory. Each check is given a
factory: a function that answers your MDM, configured, with at least two phones without a name in
its group, and that takes ``refuse_after=N`` to make it refuse to name any phone after N. Start
from ``examples/odmdm-connector-demo``: a pretend MDM with three phones that cannot take a
challenge by API, its own ``pyproject.toml`` and its contract test. The repository's CI builds it
and the connector, installs both and runs ``odmdm-connector --mdm demo phones``.

Development
-----------

- install uv: https://docs.astral.sh/uv/getting-started/installation/
- Install project deps and the hooks, for commits and for commit messages::

    uv sync
    uv run prek install --hook-type pre-commit --hook-type commit-msg
    uv run prek run --all-files

- Run the tests. They fail below 88 % coverage, and they fail a test that starts a process: the
  connector starts none, and imports only the standard library, keyring in ``store.py`` alone::

    uv run pytest

The ``.pre-commit-config.yaml`` is generated by config-ci-library_ (common + python components), do
not hand edit the autogenerated section.

.. _config-ci-library: https://github.com/pvarki/config-ci-library

Versioning is handled with bump-my-version_: ``uv run bump-my-version bump <patch/minor/major>``.

.. _bump-my-version: https://github.com/callowayproject/bump-my-version
