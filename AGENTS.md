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

## Layout

The command line knows an MDM only through one contract; Fleet is the plugin built in.

- `src/odmdm_connector/mdm.py`: the contract (`Mdm`, `Phone`, `State`, `Field`, `Step`, `MdmError`),
  standard library only.
- `core.py`: the naming rules, the same for every MDM. `console.py`: the command line. Neither
  imports anything from `fleet.py` or `fleet_plugin.py`: the command line gets the MDM chosen with
  `--mdm` only through `plugins.load`.
- `plugins.py`: lists the entry point group `odmdm_connector.mdm` without importing anything, and
  imports only the one entry point chosen. It is the only dynamic import there is.
- `testing/`: the kit for plugin authors, shipped in the package: `FakeMdm` and `contract.CHECKS`.
  The repository runs the checks against the fake, Fleet over the stub, and the demo.
- `examples/odmdm-connector-demo/`: a plugin as a package of its own. CI builds it and the
  connector and installs both as an operator would.
- `packaging/`: the self-contained file per system that Deploy App's MDM page offers. `build.py`
  runs PyInstaller, a build tool in the `dist` dependency group only, on `launcher.py` and packs the
  result; `manifest.py DIR` writes `DIR/connector.json`, the contract with Deploy App's UI (its
  test: `tests/test_manifest.py`). The file names, `os`, `arch` and the order are that contract:
  change them only together with Deploy App. Started without a command, the connector does what
  `web` does, as a downloaded file is started by a double-click. CI builds every file with uv's own
  Python (`UV_PYTHON_PREFERENCE=only-managed`), one known build on every system instead of each
  runner's, and the Linux file in AlmaLinux 8, as it needs the glibc it was built on or newer.
- `fleet_plugin.py`: Fleet behind the contract, registered in `pyproject.toml` as `fleet`. `fleet.py`:
  Fleet's HTTP dialect. `template.py` and the shipped `template.json`: what a deployment's phones need.

## No processes

The connector starts no process and runs no code it was given: ruff bans `subprocess`, the
`os.system`/`popen`/`exec*`/`spawn*` family, `webbrowser`, `eval` and `exec`, and
`conftest.py` at the root makes every way to start a process raise for the whole test run. A missing
feature is not a reason to shell out.

The one exception, decided by the owner on 2026-11-21: `web` opens the page in the default browser
through `src/odmdm_connector/browser.py`, the only module ruff lets import `webbrowser`
(`per-file-ignores` in `pyproject.toml`); it starts nothing else. Ruff cannot exempt one API, so
that entry lifts every ban in the file: there `tests/test_no_processes.py`, not ruff, holds the rule.
It reads `browser.py` without importing it and fails on any import but `html`, `logging`, `os`, `sys`,
`webbrowser` and `pathlib`, on any `os` function that starts a process, and on anything of
`webbrowser` but `open` and `Error`. The address holds the page's key,
and on Linux and macOS a program's arguments can be read by other users of the computer, so the
address never goes to the opener: `browser.py` writes `open-page.html` into `store.directory()`,
mode 0600, which leads on to the address, and hands the opener that file's `file:` URI. Without a
desktop (no `DISPLAY` or `WAYLAND_DISPLAY` outside Windows and macOS) it opens nothing, as
`webbrowser` would start a text browser in the terminal. Tests replace the opener with a fake;
`conftest.py` still makes `webbrowser.open` raise in every test.

The self-contained file of `packaging/` is PyInstaller's one-file mode: its bootloader, not the
connector's code, unpacks Python and the package into a new temporary folder and runs them as a
child process of itself, which it removes again when the connector stops. Nothing of the
connector's code changes for it, and every rule above holds inside it.

## One dependency: keyring, for the token

Runtime code under `src/` imports nothing but Python's standard library and the package itself,
with one exception: `store.py` imports keyring, inside `platform_backend()`, to keep the MDM token in
the system's keychain. Nothing else uses keyring, and the connector must run without it: the
one-file Python build (`.pyz`) leaves it out, and there `platform_backend()` is None and the token is held until the
connector stops. `tests/test_standard_library.py` fails on any other import, and on keyring anywhere
but there; CI runs the one file without it. The owner decided on 2026-11-21 first that the connector
goes into a network without internet as one file with no dependencies (README.rst, "Into a network
without internet"), as every dependency is one more thing to carry in and keep up, and then: "We
may have a dependency to manage the token." The operating systems' keychains cannot be reached from
the standard library without starting a process. The connector departs from the organisation's
Python template, which uses click and libadvian: the command line is argparse and Fleet is reached
with urllib. A file the package ships is read through `importlib.resources`, never beside `__file__`,
so that it is found inside the zip too. A missing feature is not a reason to add a dependency: say
it is missing and let the owner decide. Development tools are not runtime code and stay in the `dev`
group.

## The page

`web.py` serves the connector as a page on the operator's computer; `static/` holds its three files.
The page draws what `App` answers and decides nothing: a rule that a test of `App` cannot reach does
not belong in `web.py` or `app.js`. Setting up is a row of cards, one thing each, with a bar of three
parts (Deploy App, the MDM's title, Connect) during a first set-up; the daily work is one view of
the phones in the earlier desktop window's layout, with the browser's own `dialog` element for
giving callsigns, one phone, the settings and removing a Deploy App. Keep these when changing either:

- Standard library only, no framework, no library and no build step: three static files.
  `ThreadingHTTPServer` bound to 127.0.0.1, a daemon thread per connection, so that a connection a
  browser leaves idle holds nothing up. `Page.lock` is held around every call into `App`, the state
  and every action, so `App` sees one caller at a time; the static files take no lock. No timer:
  `Page.state()` asks for the phones once `web.REFRESH` seconds have passed, so they are listed only
  while a page is open and asking, and never beside a request.
- Every request: the Host header is `127.0.0.1:<port>` or `localhost:<port>` (no DNS rebinding).
  The three files in `STATIC` are public code and need nothing more, so that a browser without the
  key gets a page that says what to do; `/` reads no query. Everything under `/api/` carries one
  header `X-ODMDM-Key` that equals the key, compared with `secrets.compare_digest`. Never a cookie:
  a browser sends a cookie for 127.0.0.1 to every port of it, so any program listening on another
  port would receive the key as soon as a web page sends the browser there. Every POST: the Origin
  is the page's own and the body a JSON object of at most 64 kB. GET changes nothing.
- Every answer carries `web.HEADERS`: no caching, a Content-Security-Policy of `'self'` only, no
  framing, no sniffing, no referrer. Hence no inline script or style, and no external resource.
- Only the three files in `STATIC` are served, by a fixed table. No path from a request reaches the
  file system.
- No request log on the terminal: the first request's address holds the key. Failures go to the
  app's log.
- The page builds elements with `createElement` and text nodes, never `innerHTML` with anything from
  the server. The browser keeps exactly one thing, the page's key, in `localStorage`, which a
  browser keeps apart for each port, so that every tab of the page has it; it is worthless once the
  connector stops. Nothing else may ever be kept there: no other storage, no cookie. Every sentence it
  shows comes from an answer; `app.js` adds labels, headings, the lines of the Add card that say
  where Deploy App's button is, that it opens a new tab and what to have at hand, the Remove
  dialog's line on what removing changes, and the sentences for a connector that refuses this
  browser or does not answer. A tab waits for Deploy App only when Deploy App's button opened it,
  and a card that holds nothing typed follows a Deploy App added, opened or connected in another
  tab. Focus starts in the first field, else on the main button; in the Remove dialog and on the
  confirm card for a new Deploy App, on Cancel. The picture of Deploy App's button on the Add card
  is no control: a `span`, hidden from assistive technology. Each card and each dialog is a form whose
  main button submits it; a button that cannot act is disabled, not hidden.
- Deploy App's MDM page is the address `App.mdm_page` hands in the state, opened in a new tab with
  `rel="noopener noreferrer"`; the page makes no address of its own.
- A secret, the token, the settings line or the challenge, is a text input masked by the style
  (`-webkit-text-security`), never `type=password`, so that no password manager offers to keep it
  and sync it away.
- A settings line arrives by window message from the page that opened this one, never in the
  address: the page takes it only when `event.source` is `window.opener`, and passes the message's
  origin to `/api/read` and `/api/add` as `from`, where `App` takes a line only from its own Deploy
  App's pages. A line typed or pasted has an empty `from`.
- `/api/name` sends the names the preview shown held, and `App` names them only if they are the
  last preview that passed. The page asks for one preview at a time, a moment after the typing
  stops, and shows only the answer to the last one asked; **Name** waits while one is asked.
  Nothing previews again behind the operator's back.
- No answer holds the MDM token or the settings line. A secret field's value never goes to the
  page: left empty, the kept one is used. The challenge is answered only where `App.challenge()`
  gives it, for an MDM given it by hand.
- Known limits: the button in Deploy App talks to whatever listens on port 8737 of this computer,
  so on a computer shared with other users the settings line is copied by hand instead. Another
  program on this computer can slow the page down, but cannot read or drive it without the key.
- `web`, or no command at all, opens the page in the default browser through `browser.py` only, and prints the address;
  `--no-browser` only prints it. The address is printed first and the browser asked from a daemon
  thread as the page starts to serve, since some openers wait for the browser or never return. Deploy App's button finds the page only in the browser where the
  address was opened.

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
- Test: `uv run pytest` (coverage floor: 88%, set in `pyproject.toml`). Needs no network, no Fleet
  and no phone.
- Hooks, before every commit: `uv run prek run --all-files`. Once per clone:
  `uv run prek install --hook-type pre-commit --hook-type commit-msg`
- Self-contained file for this computer: `uv run --locked --group dist python packaging/build.py`
- Version bump, once per pull request, in its own commit: `uv run bump-my-version bump patch|minor|major`

## Crown jewels

- **The MDM API token** (today a Fleet global admin token): it can change what every enrolled
  phone of every team installs and trusts. The only place that reads it is `_values` in
  `src/odmdm_connector/console.py`, from the file `ODMDM_TOKEN_FILE` names: a plugin field marked
  secret is read only from the file `ODMDM_<KEY>_FILE` names, never from `--set` or a plain variable.
  The chosen plugin gets it in memory; `FleetPlugin` hands it to `FleetMdm`, which sends it only as
  the `Authorization` header to the MDM URL. A plugin from another package sees it too, so the
  command line names that package on stderr before loading it.
- The page takes it typed, and `App` holds it in memory, by Deploy App, MDM and field, until the
  connector stops. With "Remember on this computer" ticked and a keychain at hand (`App.keeps`), it
  is kept in the keychain too, service `odmdm-connector`, account `<host>:<mdm>:<field key>`, through
  the one keyring back end `store.platform_backend()` names for the system and sets as keyring's, so
  that neither `PYTHON_KEYRING_BACKEND`, keyring's configuration file nor another back end installed
  chooses where it goes. A secret is looked for in memory first, then in the keychain. Connecting
  with remember off, removing the Deploy App and the MDM refusing it (`MdmError.unauthorized`) all
  remove the kept copy; there is no other way to forget it. Whatever a back end raises is "the
  keychain did not answer", logged by its kind only. Never a plaintext fallback.
- The tool never logs it, prints it, takes it as an option or argument, or writes it to a file: only
  the keychain keeps it.
  Keep it that way: no `--token` option, no token in an environment variable, no token in an error
  message. `.gitignore` ignores `*.token` so a token file in the checkout cannot be committed.
- **The page's key**, made at start by `odmdm-connector web` and printed once, in the address's
  fragment (`/#key=…`), which a browser sends to no server: whoever has it drives the connector and
  so the MDM. `app.js` takes it out of the address, keeps it in `localStorage` only once
  `/api/state` accepts it (any web page can send the browser to the address with a wrong one), and
  sends it only as the `X-ODMDM-Key` header to the page's own `/api/`. Never log it, never put it in
  an answer, never in a cookie, a query or another program's arguments.
- **`open-page.html`** in `store.directory()`: it holds the address, key and all, while the
  connector runs, so it is written mode 0600 and removed in the `web` command's `finally`, only by
  a run that asked the browser: with `--no-browser`, without a desktop or on a taken port it may be
  a running connector's.
