"""The page's server against the fake MDM: on a free port in a thread, with http.client as the browser"""

import http.client
import json
import logging
import socket
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from odmdm_connector import app, browser, console, store, web
from odmdm_connector.mdm import ASK_AGAIN, State, Step
from odmdm_connector.settings import Settings
from odmdm_connector.testing import FakeMdm

from .test_app import CHALLENGE, KEPT, LINE, NOW, TOKEN_VALUES, VALUES, Addressed, HalfSetUp, Rig
from .test_store import MemoryBackend

Answer = tuple[int, http.client.HTTPMessage, bytes]


class WebRig(Rig):
    """The App of test_app's Rig, and a clock for the page that the test moves"""

    def __init__(self, directory: Path, backend: MemoryBackend | None = None) -> None:
        self.ticks = 0.0
        super().__init__(directory, backend)


class Served:
    """The page on a free port, and a browser that has opened the address printed"""

    def __init__(self, rig: WebRig) -> None:
        self.rig = rig
        self.page = web.Page(rig.app, 0, now=lambda: NOW, clock=lambda: rig.ticks)
        self.host = f"127.0.0.1:{self.page.port}"
        self.origin = f"http://{self.host}"
        self.key = self.page.key
        #: Every answer's body, to look for secrets in
        self.bodies: list[bytes] = []
        threading.Thread(target=self.page.serve_forever, args=(0.01,), daemon=True).start()

    def request(self, method: str, path: str, body: bytes | None = None, **headers: str) -> Answer:
        sent = {"Host": self.host, "X-ODMDM-Key": self.key, **headers}
        connection = http.client.HTTPConnection("127.0.0.1", self.page.port, timeout=5)
        try:
            connection.request(method, path, body, {name.replace("_", "-"): value for name, value in sent.items()})
            response = connection.getresponse()
            answer = (response.status, response.msg, response.read())
        finally:
            connection.close()
        self.bodies.append(answer[2])
        return answer

    def post(self, path: str, body: Any = None, status: int = 200) -> Any:
        given = json.dumps({} if body is None else body).encode()
        got, _, data = self.request("POST", path, given, Origin=self.origin, Content_Type="application/json")
        assert got == status, data
        return json.loads(data)

    def state(self) -> Any:
        self.rig.app.worker.wait()
        status, _, data = self.request("GET", "/api/state")
        assert status == 200, data
        return json.loads(data)


def _served(rig: WebRig) -> Iterator[Served]:
    made = Served(rig)
    yield made
    made.page.shutdown()
    made.page.server_close()
    made.rig.app.close()


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    yield from _served(WebRig(tmp_path / "config"))


@pytest.fixture
def keyed(tmp_path: Path) -> Iterator[Served]:
    """The page on a computer with a keychain"""
    yield from _served(WebRig(tmp_path / "config", MemoryBackend()))


def test_nothing_under_api_is_answered_without_the_key_header(served: Served) -> None:
    status, _, body = served.request("GET", "/api/state", X_ODMDM_Key="")
    assert (status, body.decode()) == (
        403,
        "Open the address that the connector printed when it started. This page then goes on by itself.",
    )
    assert served.request("GET", "/api/state", X_ODMDM_Key="wrong")[0] == 403
    assert served.request("GET", "/api/nothing", X_ODMDM_Key="")[0] == 403
    assert served.request("POST", "/api/refresh", b"{}", X_ODMDM_Key="", Origin=served.origin)[0] == 403
    assert served.request("GET", "/api/state")[0] == 200


def test_the_key_is_in_the_fragment_and_never_a_cookie(served: Served) -> None:
    """A browser sends a cookie for 127.0.0.1 to every port of it, and a fragment to no server"""
    assert served.page.address == f"http://127.0.0.1:{served.page.port}/#key={served.page.key}"
    status, headers, body = served.request("GET", f"/?key={served.page.key}", X_ODMDM_Key="")
    assert (status, headers["Set-Cookie"], headers["Location"]) == (200, None, None)
    assert body.startswith(b"<!doctype html>")
    assert served.request("GET", "/api/state", X_ODMDM_Key="", Cookie=f"odmdm={served.page.key}")[0] == 403


def test_two_key_headers_are_refused(served: Served) -> None:
    with socket.create_connection(("127.0.0.1", served.page.port), timeout=5) as raw:
        raw.sendall(
            f"GET /api/state HTTP/1.1\r\nHost: {served.host}\r\nX-ODMDM-Key: {served.key}\r\n"
            f"X-ODMDM-Key: {served.key}\r\nConnection: close\r\n\r\n".encode()
        )
        answer = raw.recv(4096)
    assert answer.startswith(b"HTTP/1.0 403")


@pytest.mark.parametrize("path", ["/", "/?key=wrong", "/app.css", "/app.js"])
def test_the_three_files_need_no_key(served: Served, path: str) -> None:
    """They are public code, and the page explains itself to a browser that has no key yet"""
    status, headers, _ = served.request("GET", path, X_ODMDM_Key="")

    assert (status, headers["Set-Cookie"]) == (200, None)


def test_the_server_header_names_the_connector_alone(served: Served) -> None:
    assert served.request("GET", "/app.css")[1]["Server"] == "odmdm-connector"


@pytest.mark.parametrize("host", ["example.fi", "127.0.0.1", "127.0.0.1:1", "attacker.example.fi:8737"])
def test_another_host_is_refused(served: Served, host: str) -> None:
    assert served.request("GET", "/", Host=host)[0] == 403
    assert served.request("GET", "/app.js", Host=host)[0] == 403
    assert served.request("GET", "/api/state", Host=host)[0] == 403
    assert served.request("POST", "/api/refresh", b"{}", Host=host, Origin=served.origin)[0] == 403


@pytest.mark.parametrize("second", ["{host}", "example.fi"])
def test_two_host_headers_are_refused(served: Served, second: str) -> None:
    with socket.create_connection(("127.0.0.1", served.page.port), timeout=5) as raw:
        raw.sendall(
            f"GET /api/state HTTP/1.1\r\nHost: {served.host}\r\nHost: {second.format(host=served.host)}\r\n"
            f"X-ODMDM-Key: {served.key}\r\nConnection: close\r\n\r\n".encode()
        )
        answer = raw.recv(4096)
    assert answer.startswith(b"HTTP/1.0 403")


def test_localhost_is_this_computer_too(served: Served) -> None:
    host = f"localhost:{served.page.port}"
    status, _, data = served.request("POST", "/api/refresh", b"{}", Host=host, Origin=f"http://{host}")
    assert (status, json.loads(data)) == (200, {})


@pytest.mark.parametrize("origin", [None, "http://example.fi", "null", "https://127.0.0.1:8737"])
def test_a_post_from_anywhere_but_the_page_is_refused(served: Served, origin: str | None) -> None:
    headers = {"Origin": origin} if origin else {}
    status, _, body = served.request("POST", "/api/add", json.dumps({"line": LINE}).encode(), **headers)

    assert (status, body.decode()) == (403, web.FORBIDDEN)
    assert served.rig.app.saved.connections == {}


@pytest.mark.parametrize(
    ("body", "headers", "says"),
    [
        (b"{}", {"Content-Length": str(web.LIMIT + 1)}, "The request has no length or is too large."),
        (b"{", {}, "The request is not a JSON object."),
        (b"[1]", {}, "The request is not a JSON object."),
        (b"\xff", {}, "The request is not a JSON object."),
        (b"[" * 5000 + b"]" * 5000, {}, "The request is not a JSON object."),
        (b"{}", {"Content-Length": "-1"}, "The request has no length or is too large."),
    ],
)
def test_a_broken_or_oversized_body_is_refused(served: Served, body: bytes, headers: dict[str, str], says: str) -> None:
    status, _, answer = served.request("POST", "/api/refresh", body, Origin=served.origin, **headers)

    assert (status, answer.decode()) == (400, says)


def test_a_value_of_the_wrong_kind_is_said(served: Served) -> None:
    assert served.post("/api/read", {"line": 1}, 400) == {"error": "The page sent line as something other than text."}
    assert served.post("/api/again", {"ids": "a1"}, 400) == {
        "error": "The page sent ids as something other than a list of text."
    }
    assert served.post("/api/connect", {"mdm": "fake", "values": {"group": 1}}, 400) == {
        "error": "The page sent the values as something other than text."
    }
    assert served.post("/api/connect", {"mdm": "fake", "values": VALUES, "remember": "yes"}, 400) == {
        "error": "The page sent remember as something other than yes or no."
    }
    assert served.post("/api/name", {"names": ["a1"]}, 400) == {
        "error": "The page sent the names as something other than text."
    }
    assert served.post("/api/name", {"names": {}, "save": "yes"}, 400) == {
        "error": "The page sent save as something other than yes or no."
    }


@pytest.mark.parametrize(
    ("path", "kind", "starts"),
    [
        ("/", "text/html; charset=utf-8", b"<!doctype html>"),
        ("/app.css", "text/css; charset=utf-8", b"/*"),
        ("/app.js", "text/javascript; charset=utf-8", b'"use strict";'),
    ],
)
def test_the_three_files_and_their_headers(served: Served, path: str, kind: str, starts: bytes) -> None:
    status, headers, body = served.request("GET", path)

    assert (status, headers["Content-Type"]) == (200, kind)
    assert body.startswith(starts)
    for name, value in web.HEADERS.items():
        assert headers[name] == value


@pytest.mark.parametrize(
    "path", ["/index.html", "/static/app.js", "/../pyproject.toml", "/%2e%2e/web.py", "/api/nothing", "/web.py"]
)
def test_nothing_else_is_served(served: Served, path: str) -> None:
    status, headers, _ = served.request("GET", path)

    assert status == 404
    assert headers["Content-Security-Policy"] == web.HEADERS["Content-Security-Policy"]
    assert served.request("POST", path, b"{}", Origin=served.origin)[0] == 404


def test_http_servers_own_refusals_are_plain_and_carry_the_headers(served: Served) -> None:
    status, headers, body = served.request("PUT", "/api/state")
    assert (status, body) == (501, b"Unsupported method ('PUT')")
    assert headers["X-Content-Type-Options"] == "nosniff"

    with socket.create_connection(("127.0.0.1", served.page.port), timeout=5) as raw:
        raw.sendall(b"GET / HTTP/1.1\r\nX: " + b"a" * 70000 + b"\r\n\r\n")
        answer = raw.recv(4096)
    assert answer.startswith(b"HTTP/1.0 431") and b"Cache-Control: no-store" in answer


def test_an_idle_connection_does_not_delay_an_answer(served: Served) -> None:
    """A browser opens spare connections and leaves them idle until the timeout"""
    with (
        socket.create_connection(("127.0.0.1", served.page.port), timeout=5),
        socket.create_connection(("127.0.0.1", served.page.port), timeout=5),
    ):
        began = time.monotonic()
        assert served.request("GET", "/api/state")[0] == 200
        assert time.monotonic() - began < web.TIMEOUT / 2


@pytest.mark.parametrize("failure", [ConnectionResetError(54, "Connection reset by peer"), TimeoutError("timed out")])
def test_a_dropped_connection_is_one_line_naming_its_kind(
    served: Served, caplog: pytest.LogCaptureFixture, failure: OSError
) -> None:
    caplog.set_level(logging.DEBUG)
    try:
        raise failure
    except OSError:
        served.page.handle_error(None, ("127.0.0.1", 1))

    [record] = caplog.records
    assert record.getMessage() == f"A connection to the page ended: {type(failure).__name__}"
    assert record.exc_info is None


def test_a_request_that_failed_otherwise_is_logged_whole(served: Served, caplog: pytest.LogCaptureFixture) -> None:
    try:
        raise RuntimeError("a bug")
    except RuntimeError:
        served.page.handle_error(None, ("127.0.0.1", 1))

    [record] = caplog.records
    assert (record.getMessage(), record.exc_info is not None) == ("The page could not answer a request", True)


def test_the_app_sees_one_caller_at_a_time(served: Served, monkeypatch: pytest.MonkeyPatch) -> None:
    inside, most = [0], [0]
    approval = served.rig.app.approval

    def slow(name: str) -> str:
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        time.sleep(0.05)
        inside[0] -= 1
        return approval(name)

    monkeypatch.setattr(served.rig.app, "approval", slow)
    callers = [threading.Thread(target=served.post, args=("/api/form", {"mdm": "fake"})) for _ in range(4)]
    for each in callers:
        each.start()
    for each in callers:
        each.join()

    assert most == [1]


def test_the_whole_flow(served: Served) -> None:
    first = served.state()
    assert (first["connections"], first["active"], first["connected"], first["needs"]) == ([], "", False, "")
    assert first["keeps"] is False, "no keychain on this computer"
    assert (first["steps"], first["setup_error"]) == ([], "")
    assert [mdm["name"] for mdm in first["mdms"]] == ["fake", "demo"]

    reading = served.post("/api/read", {"line": LINE})
    assert reading == {
        "host": "unit.example.fi",
        "until": "Deploy App unit.example.fi accepts phones until 14:00.",
        "known": False,
    }
    assert served.state()["connections"] == [], "reading takes nothing"

    added = served.post("/api/add", {"line": LINE})
    state = served.state()
    assert added == {"said": "Deploy App unit.example.fi accepts phones until 14:00."}
    assert (state["active"], state["needs"], state["banner"], state["mdm_page"]) == (
        "unit.example.fi",
        "mdm",
        {"level": "open", "text": "Deploy App unit.example.fi accepts phones until 14:00"},
        "https://mtls.unit.example.fi/mdm",
    )

    form = served.post("/api/form", {"mdm": "fake"})
    assert (form["title"], [(each["key"], each["value"], each["secret"], each["kept"]) for each in form["fields"]]) == (
        "Fake MDM",
        [("group", "Deploy App", False, False), ("password", "", True, False)],
    )

    connected = served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    state = served.state()
    assert connected == {"said": "Setting Fake MDM up for Deploy App unit.example.fi."}
    assert [step["label"] for step in state["steps"]] == [
        "Group Deploy App enrols at https://unit.example.fi/scep",
        "Certificate template stated",
    ]
    state = served.state()
    assert (state["connected"], state["needs"], state["summary"]) == (True, "", "3 phones: 1 ready, 2 without a name")
    assert state["rows"][2] == {
        "id": "a3",
        "callsign": "OTTER1",
        "phone": "Pretend Tablet 2",
        "joined": "2026-11-01 09:10",
        "certificate": "has its certificate",
        "waiting": False,
        "failed": False,
        "ready": True,
        "askable": False,
    }
    assert served.post("/api/form", {"mdm": "fake"})["fields"][1]["kept"] is True

    numbered = {"ids": [], "how": "numbered", "prefix": "OTTER", "first": "", "list": ""}
    preview = served.post("/api/preview", numbered)
    assert (preview["ok"], preview["button"], preview["first"]) == (True, "Name 2 phones", 2)
    assert [(row["id"], row["callsign"]) for row in preview["rows"]] == [("a1", "OTTER2"), ("a2", "OTTER3")]

    named = served.post("/api/name", {"names": {"a1": "OTTER2", "a2": "OTTER3"}, "how": "numbered", "prefix": "OTTER "})
    served.state()
    state = served.state()
    assert named == {"said": "Naming 2 phones."}
    assert (state["status"], state["lines"]) == ("2 phones named.", ["Phone a1 is OTTER2", "Phone a2 is OTTER3"])
    assert (state["prefix"], state["callsigns"]) == ("OTTER", [])

    again = served.post("/api/again", {"ids": ["a1"]})
    assert again == {"said": "Asking again."}
    detail = served.post("/api/detail", {"id": "a1"})
    assert (detail["callsign"], detail["state"], detail["can_ask_again"]) == (
        "OTTER2",
        "requested, waiting for the phone",
        True,
    )

    assert served.post("/api/remove", {"host": "unit.example.fi"}) == {
        "said": "Deploy App unit.example.fi is removed from this app, and its MDM token from this computer."
    }
    assert served.state()["connections"] == []


def test_naming_from_a_list_names_what_the_preview_held(served: Served) -> None:
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()
    listed = {"ids": ["a1"], "how": "list", "list": "BEAR1\n\n BEAR2 \nBEAR3"}
    shown = served.post("/api/preview", listed)
    assert (shown["ok"], shown["rows"][0]["callsign"]) == (True, "BEAR1")

    named = served.post("/api/name", {"names": {"a1": "BEAR1"}, "how": "list", "list": listed["list"], "save": True})
    served.state()
    state = served.state()

    assert named == {"said": "Naming 1 phone."}
    assert (state["prefix"], state["callsigns"]) == ("", ["BEAR2", "BEAR3"]), "kept, less what was named"


def test_a_list_is_kept_only_when_saved(served: Served) -> None:
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()
    served.post("/api/preview", {"ids": ["a1"], "how": "list", "list": "BEAR1\nBEAR2"})

    served.post("/api/name", {"names": {"a1": "BEAR1"}, "how": "list", "list": "BEAR1\nBEAR2", "save": False})

    assert served.state()["callsigns"] == []


def test_only_the_preview_that_passed_is_named(served: Served) -> None:
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()
    unseen = {"names": {"a1": "BEAR1"}, "how": "list", "list": "BEAR1", "save": True}
    first = "Preview the callsigns first: nothing is named without a preview that passed."

    assert served.post("/api/name", unseen, 400) == {"error": first}
    refused = served.post("/api/preview", {"ids": ["a1"], "how": "list", "list": "OTTER1"})
    assert (refused["ok"], refused["rows"][0]["refusal"]) == (False, "OTTER1 already belongs to phone a3")
    assert served.post("/api/name", {**unseen, "names": {"a1": "OTTER1"}}, 400) == {"error": first}
    served.post("/api/preview", {"ids": ["a1"], "how": "list", "list": "BEAR1"})
    assert served.post("/api/name", {**unseen, "names": {"a1": "BEAR2"}}, 400) == {"error": first}
    too_few = served.post("/api/preview", {"ids": ["a1", "a2"], "how": "list", "list": "BEAR1"})
    assert (too_few["ok"], too_few["refusal"]) == (False, "2 phones to name, but only 1 callsign.")
    assert served.post("/api/preview", {"how": "numbered", "prefix": "BEAR", "first": "x"}, 400) == {
        "error": "The first number must be a whole number."
    }
    assert served.state()["callsigns"] == [], "nothing kept from a refusal"
    assert served.request("POST", "/api/keep", b"{}", Origin=served.origin)[0] == 404


def test_a_kept_secret_is_used_without_going_to_the_page(served: Served) -> None:
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()

    served.post("/api/connect", {"mdm": "fake", "values": {"group": "Deploy App", "password": ""}})
    served.state()

    assert isinstance(served.rig.app.mdm, FakeMdm)
    assert served.rig.app.mdm.values == VALUES


def test_a_kept_secret_is_not_sent_with_another_address(served: Served) -> None:
    """Whoever can post to the page cannot have the kept token sent to a host of their choosing"""
    served.rig.classes["fake"] = Addressed
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": TOKEN_VALUES})
    served.state()
    Addressed.made.clear()
    before = served.rig.app.saved

    moved = {**TOKEN_VALUES, "mdm_url": "https://attacker.example.fi", "token": ""}
    refused = served.post("/api/connect", {"mdm": "fake", "values": moved}, 400)

    assert refused == {"error": "Type the Token again: the settings changed."}
    assert Addressed.made == []
    assert served.rig.app.saved == before


def test_a_plugin_is_approved_and_its_challenge_given_by_hand(served: Served) -> None:
    served.post("/api/add", {"line": LINE})

    asked = served.post("/api/form", {"mdm": "demo"})
    assert asked["approval"].startswith("The MDM demo comes from the package odmdm-connector-demo 1.0.0")
    assert served.post("/api/approve", {"mdm": "demo"}) == {}
    assert served.post("/api/form", {"mdm": "demo"})["title"] == "Fake MDM"

    served.post("/api/connect", {"mdm": "demo", "values": VALUES})
    state = served.state()
    assert [(step["label"], step["done"]) for step in state["steps"]] == [("The fake MDM cannot setup", False)]
    assert state["setup_error"] == ""
    assert (state["challenge_note"], state["challenge"]) == ("The fake MDM cannot set_challenge", CHALLENGE)
    assert served.post("/api/again", {"ids": []}, 400) == {"error": "The fake MDM cannot ask_again"}


def test_opening_a_deploy_app_and_the_refusals(served: Served) -> None:
    other = LINE.replace("unit.example.fi", "other.example.fi")
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.post("/api/add", {"line": other})

    assert served.post("/api/read", {"line": LINE})["known"] is True
    assert served.post("/api/open", {"host": "unit.example.fi"}) == {
        "said": "Deploy App unit.example.fi accepts phones until 14:00."
    }
    state = served.state()
    assert state["connections"] == [
        {"host": "unit.example.fi", "level": "open", "short": "enrolment open until 14:00", "active": True},
        {"host": "other.example.fi", "level": "open", "short": "enrolment open until 14:00", "active": False},
    ]
    assert state["mdm_page"] == "https://mtls.unit.example.fi/mdm"
    assert state["connected"]
    assert served.post("/api/open", {"host": "nowhere.example.fi"}, 400) == {
        "error": "There is no Deploy App nowhere.example.fi in this app."
    }
    assert served.post("/api/read", {"line": "not a line"}, 400)["error"].startswith("This is not a settings line")


def test_the_page_that_sent_a_line_is_checked(served: Served) -> None:
    sent = {"line": LINE, "from": "https://mtls.unit.example.fi"}
    forged = {"line": LINE, "from": "https://other.example.fi"}

    assert served.post("/api/read", sent)["host"] == "unit.example.fi"
    assert served.post("/api/add", forged, 400)["error"].startswith(
        "These settings are for Deploy App unit.example.fi, but the page that sent them is https://other.example.fi"
    )
    assert served.state()["connections"] == []
    assert served.post("/api/add", sent) == {"said": "Deploy App unit.example.fi accepts phones until 14:00."}


def test_the_phones_are_refreshed_every_minute_while_the_page_asks(served: Served) -> None:
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()
    fake = served.rig.app.mdm
    assert isinstance(fake, FakeMdm)
    fake.calls.clear()

    served.state()
    served.rig.ticks += web.REFRESH
    served.state()
    served.state()

    assert fake.calls == ["phones"]
    assert served.post("/api/refresh") == {}
    served.state()
    assert fake.calls == ["phones", "phones"]


def test_the_state_says_when_the_phones_were_last_listed(served: Served) -> None:
    assert served.state()["updated"] == ""
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()

    assert served.state()["updated"] == "12:00"
    served.post("/api/add", {"line": LINE.replace("unit.example.fi", "other.example.fi")})
    assert served.state()["updated"] == "", "not the other Deploy App's"


def test_a_failure_is_one_sentence_pointing_at_the_log(served: Served, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str) -> str:
        raise RuntimeError("a bug")

    monkeypatch.setattr(served.rig.app, "approval", broken)

    assert served.post("/api/form", {"mdm": "fake"}, 500) == {
        "error": f"The connector failed; the log says more: {served.rig.app.log_path}"
    }


def test_no_answer_and_no_log_line_holds_a_secret(served: Served, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    served.post("/api/read", {"line": LINE})
    served.post("/api/add", {"line": LINE})
    served.post("/api/form", {"mdm": "fake"})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()
    served.post("/api/add", {"line": LINE})
    state = served.state()
    served.post("/api/detail", {"id": "a1"})
    served.post("/api/read", {"line": LINE.replace("2026", "1999")}, 400)

    assert isinstance(served.rig.app.mdm, FakeMdm)
    assert served.rig.app.mdm.challenge == CHALLENGE, "the MDM takes the challenge by API"
    assert state["challenge"] == ""
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (VALUES["password"], CHALLENGE, LINE, served.page.key):
        assert secret not in logged
        assert all(secret.encode() not in body for body in served.bodies)


def _web(
    rig: WebRig, monkeypatch: pytest.MonkeyPatch, busy: bool = False, asked: threading.Event | None = None
) -> list[web.Page]:
    """The web command will serve rig's App until a Ctrl+C, at once, with a job under way or none

    The connector's directory is rig's. Without asked, there is no desktop and no browser is asked;
    with it, the Ctrl+C comes once the browser has been asked.
    """
    monkeypatch.setattr(app.App, "default", lambda: rig.app)
    monkeypatch.setattr(store, "directory", lambda: rig.directory)
    if asked is None:
        monkeypatch.setattr(browser, "can_open", lambda: False)
    monkeypatch.setattr(rig.app, "busy", lambda job=None: busy)
    started: list[web.Page] = []

    def serve(page: web.Page, poll_interval: float = 0.5) -> None:
        started.append(page)
        if asked is not None:
            assert asked.wait(5), "the browser was never asked"
        raise KeyboardInterrupt

    monkeypatch.setattr(web.Page, "serve_forever", serve)
    return started


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def test_the_web_command_prints_where_and_how_to_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started = _web(WebRig(tmp_path / "config"), monkeypatch)
    port = _free_port()

    assert console.main(["web", "--port", str(port)]) == 0
    printed = capsys.readouterr()
    assert printed.out.splitlines() == [
        f"Open this address in your browser: http://127.0.0.1:{port}/#key={started[0].key}",
        "Press Ctrl+C to stop it.",
    ]
    assert not printed.err


#: The real browser.can_open, which _web replaces
CAN_OPEN = browser.can_open


class Opener:
    """The opener on a desktop: what it was handed and whether the file was there then, and its answer

    Once asked, it sets asked; it answers only once released is set.
    """

    def __init__(self, rig: WebRig, monkeypatch: pytest.MonkeyPatch, answer: bool = True) -> None:
        self.rig, self.answer = rig, answer
        self.handed: list[tuple[str, bool]] = []
        self.asked, self.released = threading.Event(), threading.Event()
        self.released.set()
        monkeypatch.setattr(browser, "can_open", CAN_OPEN)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("webbrowser.open", self)

    def __call__(self, url: str, new: int = 0, autoraise: bool = True) -> bool:
        self.handed.append((url, (self.rig.directory / browser.NAME).is_file()))
        self.asked.set()
        self.released.wait(5)
        return self.answer


def test_the_web_command_opens_the_page_in_the_browser_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = WebRig(tmp_path / "config")
    opener = Opener(rig, monkeypatch)
    started = _web(rig, monkeypatch, asked=opener.asked)
    port = _free_port()

    assert console.main(["web", "--port", str(port)]) == 0

    assert opener.handed == [((rig.directory / browser.NAME).as_uri(), True)]
    assert started[0].key not in opener.handed[0][0]
    assert capsys.readouterr().out.splitlines() == [
        f"{console.OPENING} http://127.0.0.1:{port}/#key={started[0].key}",
        "Press Ctrl+C to stop it.",
    ]
    assert not (rig.directory / browser.NAME).exists(), "removed when the connector stops"


def test_an_opener_that_waits_holds_up_neither_the_address_nor_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A BROWSER command webbrowser does not know, a text browser or osascript waits for the browser"""
    rig = WebRig(tmp_path / "config")
    opener = Opener(rig, monkeypatch)
    opener.released.clear()
    monkeypatch.setattr(app.App, "default", lambda: rig.app)
    monkeypatch.setattr(store, "directory", lambda: rig.directory)
    pages: list[web.Page] = []
    serving = threading.Event()
    serve = web.Page.serve_forever

    def watched(page: web.Page, poll_interval: float = 0.5) -> None:
        pages.append(page)
        serving.set()
        serve(page, 0.01)

    monkeypatch.setattr(web.Page, "serve_forever", watched)
    codes: list[int] = []
    command = threading.Thread(target=lambda: codes.append(console.main(["web", "--port", str(_free_port())])))
    command.start()
    try:
        assert opener.asked.wait(5) and serving.wait(5)
        page = pages[0]
        assert capsys.readouterr().out.splitlines() == [f"{console.OPENING} {page.address}", "Press Ctrl+C to stop it."]
        connection = http.client.HTTPConnection("127.0.0.1", page.port, timeout=5)
        try:
            connection.request("GET", "/api/state", headers={"Host": f"127.0.0.1:{page.port}", "X-ODMDM-Key": page.key})
            assert connection.getresponse().status == 200
        finally:
            connection.close()
        assert command.is_alive() and not codes, "still serving while the opener waits"
    finally:
        opener.released.set()
        if serving.wait(5):
            pages[0].shutdown()
        command.join(5)
    assert codes == [0]


def test_without_a_desktop_the_address_is_printed_to_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = WebRig(tmp_path / "config")
    started = _web(rig, monkeypatch)
    opener = Opener(rig, monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    port = _free_port()

    assert console.main(["web", "--port", str(port)]) == 0

    assert opener.handed == []
    assert capsys.readouterr().out.splitlines() == [
        f"{console.OPEN} http://127.0.0.1:{port}/#key={started[0].key}",
        "Press Ctrl+C to stop it.",
    ]


def test_with_no_browser_the_address_is_printed_to_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = WebRig(tmp_path / "config")
    started = _web(rig, monkeypatch)
    opener = Opener(rig, monkeypatch)
    port = _free_port()

    assert console.main(["web", "--no-browser", "--port", str(port)]) == 0

    assert opener.handed == []
    assert capsys.readouterr().out.splitlines() == [
        f"{console.OPEN} http://127.0.0.1:{port}/#key={started[0].key}",
        "Press Ctrl+C to stop it.",
    ]
    assert not (rig.directory / browser.NAME).exists()


def test_when_the_browser_says_no_the_file_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The address was printed before the answer, with the sentence that says what to do if it does not open"""
    rig = WebRig(tmp_path / "config")
    opener = Opener(rig, monkeypatch, answer=False)
    started = _web(rig, monkeypatch, asked=opener.asked)
    port = _free_port()

    assert console.main(["web", "--port", str(port)]) == 0

    assert len(opener.handed) == 1
    assert capsys.readouterr().out.splitlines()[0] == f"{console.OPENING} http://127.0.0.1:{port}/#key={started[0].key}"
    assert not (rig.directory / browser.NAME).exists()


def test_ctrl_c_during_a_job_says_what_to_do_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The MDM may be half way through it, and the phones' rows are from before"""
    _web(WebRig(tmp_path / "config"), monkeypatch, busy=True)

    assert console.main(["web", "--port", str(_free_port())]) == 0
    assert capsys.readouterr().err == f"{console.STOPPED_BUSY}\n"


def test_the_web_command_says_when_the_port_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = WebRig(tmp_path / "config")
    _web(rig, monkeypatch)
    rig.directory.mkdir(parents=True, exist_ok=True)
    (rig.directory / browser.NAME).write_text("left from an earlier start", encoding="utf-8")
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]

        code = console.main(["web", "--port", str(port)])

    said = capsys.readouterr().err
    assert code == 1
    assert said.startswith(f"The page cannot listen on port {port} of this computer: ")
    assert said.endswith(". Give another with --port.\n")
    assert not (rig.directory / browser.NAME).exists(), "removed when the server could not start too"


def test_the_web_command_says_the_connector_may_be_running_when_its_own_port_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deploy App's button reaches the default port only, so another port is not the first advice"""
    rig = WebRig(tmp_path / "config")
    _web(rig, monkeypatch)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        monkeypatch.setattr(web, "PORT", taken.getsockname()[1])

        code = console.main(["web"])

    said = capsys.readouterr().err
    assert code == 1
    assert "The connector may be running already: use its page." in said
    assert said.endswith("as Deploy App's button reaches this port.\n")


def test_after_a_restart_the_token_is_typed_again_and_the_phones_listed(served: Served) -> None:
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()
    again = served.rig.start()

    page = web.Page(again, 0)
    try:
        needs = page.state()["needs"]
        said = page.connect({"mdm": "fake", "values": VALUES})
        again.worker.wait()
        again.drain()
        assert needs == "secret"
        assert said == {"said": "Connected to Fake MDM for Deploy App unit.example.fi."}
        assert [row.id for row in again.rows(NOW)] == ["a1", "a2", "a3"], "listed once connected"
    finally:
        page.server_close()
        again.close()


def test_a_failed_setup_is_said_beside_its_steps(served: Served) -> None:
    served.post("/api/add", {"line": LINE})
    served.rig.classes["fake"] = HalfSetUp

    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    state = served.state()

    assert [step["label"] for step in state["steps"]] == ["Group found"]
    assert (state["setup_error"], state["connected"]) == ("The fake MDM refused the template", True)
    served.rig.classes["fake"] = FakeMdm
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    assert served.state()["setup_error"] == "", "a new setup starts with no error"


def test_a_running_setups_steps_are_in_the_state_as_they_are_done(served: Served) -> None:
    """A setup can take minutes: the page ticks each step when it is done, not all of them at the end"""
    go = threading.Event()

    class Slow(FakeMdm):
        def setup(self, window: Settings, force: bool = False, dry_run: bool = False) -> Iterator[Step]:
            yield Step("Group found", True)
            go.wait(5)
            yield Step("Certificate template stated", True)

    served.post("/api/add", {"line": LINE})
    served.rig.classes["fake"] = Slow
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    running: Any = {}
    for _ in range(500):
        running = json.loads(served.request("GET", "/api/state")[2])
        if running["steps"]:
            break
        time.sleep(0.01)

    assert ([step["label"] for step in running["steps"]], "setup" in running["busy"]) == (["Group found"], True)
    go.set()
    done = served.state()
    assert [step["label"] for step in done["steps"]] == ["Group found", "Certificate template stated"]
    assert "setup" not in done["busy"]


def test_the_token_is_kept_in_the_keychain_only_when_remembered(keyed: Served) -> None:
    backend = keyed.rig.backend
    assert backend is not None and keyed.state()["keeps"] is True
    keyed.post("/api/add", {"line": LINE})

    keyed.post("/api/connect", {"mdm": "fake", "values": VALUES, "remember": True})
    remembered = dict(backend.items)
    said = [(each["kept"], each["remembered"]) for each in keyed.post("/api/form", {"mdm": "fake"})["fields"]]
    keyed.post("/api/connect", {"mdm": "fake", "values": {"group": "Deploy App", "password": ""}})

    assert remembered == {KEPT: VALUES["password"]}
    assert said == [(False, False), (True, True)]
    assert backend.items == {}, "remember left out is not remembered, and the kept copy goes"
    assert keyed.post("/api/form", {"mdm": "fake"})["fields"][1]["remembered"] is False, (
        "held until the connector stops"
    )
    assert keyed.state()["connected"] is True


@pytest.mark.parametrize(("mdm", "askable"), [(FakeMdm, True), (FakeMdm.lacking(ASK_AGAIN), False)])
def test_the_phones_say_which_have_their_certificates_and_which_asking_again_can_help(
    served: Served, mdm: type[FakeMdm], askable: bool
) -> None:
    served.rig.classes["fake"] = mdm
    served.post("/api/add", {"line": LINE})
    served.post("/api/connect", {"mdm": "fake", "values": VALUES})
    served.state()
    fake = served.rig.app.mdm
    assert isinstance(fake, FakeMdm)
    fake.group = [replace(fake.group[0], callsign="OTTER2", state=State.REFUSED), *fake.group[1:]]
    served.post("/api/refresh")

    rows = [(row["id"], row["ready"], row["askable"]) for row in served.state()["rows"]]

    assert rows == [("a1", False, askable), ("a2", False, False), ("a3", True, False)]
