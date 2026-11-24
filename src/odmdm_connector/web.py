"""The connector as a page served on this computer, the second face of App beside the command line

A standard library HTTP server on 127.0.0.1 only, a thread per connection, so that a connection a
browser opens and leaves idle holds nothing up; the App is called by one of them at a time. It
serves three fixed files and JSON answers; every decision is App's, and the page draws what they say.

A request is answered only when it names this computer's address in its Host header, which stops
a page elsewhere from reaching this one through DNS rebinding. The three files are public code and
need nothing more. Everything under /api/ carries the header X-ODMDM-Key, holding the key made at
start that the address printed hands the page in its fragment, which no server is sent: so another
user or program on the computer cannot drive the page. A cookie would not do, as a browser sends a
cookie for 127.0.0.1 to every port of it. A POST must come from the page itself, by its Origin
header. No answer and
no log line holds the MDM token or the settings line; the challenge is in an answer only when the
MDM has to be given it by hand.
"""

import datetime
import json
import logging
import secrets
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .app import App, AppError, Banner, Job, Result

LOGGER = logging.getLogger(__name__)

PORT = 8737
#: The largest request body taken
LIMIT = 64 * 1024
#: The request header that carries the page's key
KEY = "X-ODMDM-Key"
#: Seconds between listings of the phones while the page asks for its state
REFRESH = 60.0
#: Seconds a connection may stay silent before it is dropped
TIMEOUT = 5
#: The only files served, by request path: no path from a request reaches the file system
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
FORBIDDEN = "Open the address that the connector printed when it started. This page then goes on by itself."

Body = dict[str, Any]


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def _text(body: Body, key: str) -> str:
    value = body.get(key, "")
    if not isinstance(value, str):
        raise AppError(f"The page sent {key} as something other than text.")
    return value


def _texts(body: Body, key: str) -> list[str]:
    value = body.get(key, [])
    if not isinstance(value, list) or not all(isinstance(each, str) for each in value):
        raise AppError(f"The page sent {key} as something other than a list of text.")
    return value


def _banner(banner: Banner) -> dict[str, str]:
    return {"level": banner.level.value, "text": banner.text}


class Page(ThreadingHTTPServer):
    """The page's server: on this computer only, with a key made at start, and the App called under one lock"""

    # On Windows the option lets another program bind the same port and take the page's requests
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def __init__(
        self,
        app: App,
        port: int = PORT,
        now: Callable[[], datetime.datetime] = _now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.app, self.now, self.clock = app, now, clock
        self.port: int = self.server_address[1]
        self.key = secrets.token_urlsafe(32)
        #: Held around every call into the App, so that it sees one caller at a time
        self.lock = threading.Lock()
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.origins = {f"http://{host}" for host in self.hosts}
        #: The steps of the last setup or sending of the policy, as its result brought them, and its error
        self.steps: list[dict[str, Any]] = []
        self.setup_error = ""
        #: When the phones were last asked for, by clock; None before the first time
        self.listed: float | None = None
        #: The Deploy App whose phones arrived last, and the hour and minute they did, in the operator's time
        self.updated = ("", "")

    @property
    def address(self) -> str:
        """Where the operator opens the page: the key in the fragment, which the browser sends to no server"""
        return f"http://127.0.0.1:{self.port}/#key={self.key}"

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A request that failed outside the answers goes to the log, not the terminal

        A connection the browser dropped or left silent is one line naming its kind.
        """
        failure = sys.exception()
        if isinstance(failure, OSError):
            LOGGER.info("A connection to the page ended: %s", type(failure).__name__)
        else:
            LOGGER.exception("The page could not answer a request")

    def _take(self, results: list[Result], now: datetime.datetime) -> None:
        for result in results:
            if result.job in (Job.SETUP, Job.POLICY):
                self.steps, self.setup_error = [asdict(step) for step in result.steps], result.error
            elif result.job is Job.PHONES and result.phones is not None:
                self.updated = (result.host, now.strftime("%H:%M"))

    def _list(self) -> None:
        """Ask for the phones, when an MDM is connected"""
        if self.app.connected:
            self.app.refresh()
            self.listed = self.clock()

    def state(self) -> Body:
        """Everything the page draws, after taking in the worker's results

        The phones are asked for again once REFRESH seconds have passed, so they are listed only
        while a page is open and asking.
        """
        app, now = self.app, self.now()
        self._take(app.drain(), now)
        if self.listed is None or self.clock() - self.listed >= REFRESH:
            self._list()
        conn = app.conn
        return {
            "version": __version__,
            # Each Deploy App with a few words on its enrolment window, for the list of them
            "connections": [
                {"host": each.host, "level": each.banner.level.value, "short": each.banner.short, "active": each.active}
                for each in app.connections(now)
            ],
            "active": app.saved.active,
            "banner": _banner(app.banner(now)),
            # Where the operator opens enrolment again, in a tab of its own
            "mdm_page": app.mdm_page,
            "mdm": conn.mdm,
            "connected": app.connected,
            # Whether a secret can be kept in this computer's keychain, so the page offers to remember it
            "keeps": app.keeps,
            # What stops the MDM being connected: none chosen yet, or its secret not at hand
            "needs": "" if app.connected or not app.saved.active else ("secret" if conn.mdm else "mdm"),
            "mdms": [
                {"name": each.name, "package": each.package, "version": each.version, "built_in": each.built_in}
                for each in app.mdms()
            ],
            # What sending the policy again does, empty when the MDM cannot
            "policy": app.policy_note(),
            # The MDM connected's name as the operator knows it, empty while none is
            "title": app.mdm.title if app.mdm else "",
            # Whether removing the Deploy App can end it in its MDM too, and why not when it has an MDM and cannot
            "can_end": app.can_end,
            "end_note": app.end_note(),
            # The active Deploy App's end waits or runs: nothing more goes to the MDM for it
            "ending": app.ending,
            # While a setup runs, the steps it has done so far; after it, the steps its result brought
            "steps": self.steps or [asdict(step) for step in app.progress],
            "setup_error": self.setup_error,
            "summary": app.summary(),
            "rows": [asdict(row) for row in app.rows(now)],
            "status": app.status,
            # When the phones of the active Deploy App were last listed, as HH:MM; empty before the first time
            "updated": self.updated[1] if self.updated[0] == app.saved.active and app.connected else "",
            "lines": list(app.lines),
            "busy": [job.value for job in Job if app.busy(job)],
            "challenge_note": app.challenge_note(),
            "challenge": app.challenge(),
            "prefix": conn.prefix,
            "callsigns": conn.callsigns,
            "log": str(app.log_path),
        }

    # The answers to the page's POSTs, each from its JSON body

    def read(self, body: Body) -> Body:
        return asdict(self.app.read_settings(_text(body, "line"), self.now(), _text(body, "from")))

    def add(self, body: Body) -> Body:
        said = self.app.paste_settings(_text(body, "line"), self.now(), _text(body, "from"))
        self._list()
        return {"said": said}

    def open(self, body: Body) -> Body:
        said = self.app.open(_text(body, "host"), self.now())
        self._list()
        return {"said": said}

    def remove(self, body: Body) -> Body:
        """Remove the Deploy App from this app; with end, end it in its MDM first, which removes it once done"""
        end = body.get("end", False)
        if not isinstance(end, bool):
            raise AppError("The page sent end as something other than yes or no.")
        host = _text(body, "host")
        return {"said": self.app.end(host) if end else self.app.remove(host)}

    def form(self, body: Body) -> Body:
        """The question before a plugin is used, or the MDM's fields with their values, never a secret's"""
        name = _text(body, "mdm")
        question = self.app.approval(name)
        if question:
            return {"approval": question}
        form = self.app.form(name)
        return {
            "name": form.name,
            "title": form.title,
            "fields": [
                {
                    **asdict(each.field),
                    "value": "" if each.field.secret else each.value,
                    "kept": each.field.secret and bool(each.value),
                    "remembered": each.remembered,
                }
                for each in form.fields
            ],
        }

    def approve(self, body: Body) -> Body:
        self.app.approve(_text(body, "mdm"))
        return {}

    def connect(self, body: Body) -> Body:
        """Connect with the values typed; a secret left empty is the one kept, as App.connect allows

        remember keeps the secrets in this computer's keychain; without it, any kept copy is removed.
        """
        name = _text(body, "mdm")
        given, remember = body.get("values", {}), body.get("remember", False)
        if not isinstance(given, dict) or not all(isinstance(each, str) for each in given.values()):
            raise AppError("The page sent the values as something other than text.")
        if not isinstance(remember, bool):
            raise AppError("The page sent remember as something other than yes or no.")
        self.steps, self.setup_error = [], ""
        said = self.app.connect(name, given, remember)
        self._list()
        return {"said": said}

    def refresh(self, body: Body) -> Body:
        self._list()
        return {}

    def policy(self, body: Body) -> Body:
        """Send the policy again, its steps shown as a setup's"""
        said = self.app.send_policy()
        self.steps, self.setup_error = [], ""
        return {"said": said}

    def preview(self, body: Body) -> Body:
        """What naming the phones chosen would do, and the first number a numbered batch starts from"""
        app = self.app
        ids = app.targets(_texts(body, "ids"))
        if _text(body, "how") == "list":
            found, start = app.preview(ids, app.listed(_text(body, "list")), self.now()), 0
        else:
            prefix, first = _text(body, "prefix").strip(), _text(body, "first").strip()
            if first and not first.isdigit():
                raise AppError("The first number must be a whole number.")
            start = int(first) if first else app.first_number(prefix)
            found = app.preview(ids, app.numbered(prefix, start, len(ids)), self.now())
        return {**asdict(found), "ok": found.ok, "button": found.button, "first": start}

    def name(self, body: Body) -> Body:
        """Name what the preview shown held, which the App takes only if it is the preview that passed last

        The callsigns are kept for next time too: a numbered batch's prefix, and a list if asked to save it.
        """
        names, save = body.get("names"), body.get("save", False)
        if not isinstance(names, dict) or not all(isinstance(each, str) for each in names.values()):
            raise AppError("The page sent the names as something other than text.")
        if not isinstance(save, bool):
            raise AppError("The page sent save as something other than yes or no.")
        app = self.app
        said = app.name(names)
        if _text(body, "how") == "list":
            if save:
                app.keep_callsigns(app.conn.prefix, app.listed(_text(body, "list")))
        else:
            app.keep_callsigns(_text(body, "prefix").strip(), app.conn.callsigns)
        return {"said": said}

    def again(self, body: Body) -> Body:
        return {"said": self.app.ask_again(_texts(body, "ids"))}

    def detail(self, body: Body) -> Body:
        return asdict(self.app.detail(_text(body, "id"), self.now()))


#: The POST answers by path
ACTIONS: Mapping[str, Callable[[Page, Body], Body]] = {
    "/api/read": Page.read,
    "/api/add": Page.add,
    "/api/open": Page.open,
    "/api/remove": Page.remove,
    "/api/form": Page.form,
    "/api/approve": Page.approve,
    "/api/connect": Page.connect,
    "/api/refresh": Page.refresh,
    "/api/policy": Page.policy,
    "/api/preview": Page.preview,
    "/api/name": Page.name,
    "/api/again": Page.again,
    "/api/detail": Page.detail,
}


class Handler(BaseHTTPRequestHandler):
    """One request: checked, then a fixed file, the state or an answer"""

    server: Page
    timeout = TIMEOUT
    server_version = "odmdm-connector"

    def version_string(self) -> str:
        """The Server header: the name alone, without Python's version"""
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        """No request log: the first request's address holds the key"""

    def _answer(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        headers = {**HEADERS, "Content-Type": kind, "Content-Length": str(len(body))}
        for header, value in headers.items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(body)

    def _plain(self, status: int, sentence: str) -> None:
        self._answer(status, sentence.encode(), "text/plain; charset=utf-8")

    def _json(self, status: int, data: Body) -> None:
        self._answer(status, json.dumps(data).encode(), "application/json")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """http.server's own refusals, such as a malformed request, in the same plain form"""
        self.close_connection = True
        self._plain(code, message or HTTPStatus(code).phrase)

    def _refuse(self) -> None:
        self._plain(HTTPStatus.FORBIDDEN, FORBIDDEN)

    def _here(self) -> bool:
        """Whether the request came to this computer's address, in its one Host header; refused if not"""
        hosts = self.headers.get_all("Host") or []
        if len(hosts) == 1 and hosts[0] in self.server.hosts:
            return True
        self._refuse()
        return False

    def _admitted(self) -> bool:
        """Whether the request came to this computer's address with the key in its one key header; refused if not"""
        if not self._here():
            return False
        given = self.headers.get_all(KEY) or []
        if len(given) == 1 and secrets.compare_digest(given[0].encode(), self.server.key.encode()):
            return True
        self._refuse()
        return False

    def do_GET(self) -> None:
        if not self._here():
            return
        url = urlsplit(self.path)
        if url.path in STATIC:
            name, kind = STATIC[url.path]
            self._answer(HTTPStatus.OK, resources.files(__package__).joinpath("static", name).read_bytes(), kind)
        elif self._admitted():
            if url.path == "/api/state":
                self._call(lambda: self.server.state())
            else:
                self._plain(HTTPStatus.NOT_FOUND, "Not found.")

    def do_POST(self) -> None:
        if not self._admitted():
            return
        if self.headers.get("Origin") not in self.server.origins:
            self._refuse()
            return
        action = ACTIONS.get(self.path)
        if action is None:
            self._plain(HTTPStatus.NOT_FOUND, "Not found.")
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > LIMIT:
            self._plain(HTTPStatus.BAD_REQUEST, "The request has no length or is too large.")
            return
        try:
            body = json.loads(self.rfile.read(int(length)) or b"{}")
        except (ValueError, RecursionError):
            body = None
        if not isinstance(body, dict):
            self._plain(HTTPStatus.BAD_REQUEST, "The request is not a JSON object.")
            return
        self._call(lambda: action(self.server, body))

    def _call(self, answer: Callable[[], Body]) -> None:
        """The answer as JSON, made under the lock; an AppError is its sentence, anything else points at the log"""
        try:
            with self.server.lock:
                data = answer()
        except AppError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:  # A bug reaches the operator as a sentence, and the page goes on
            LOGGER.exception("The page's request %s failed", urlsplit(self.path).path)
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"The connector failed; the log says more: {self.server.app.log_path}"},
            )
        else:
            self._json(HTTPStatus.OK, data)
