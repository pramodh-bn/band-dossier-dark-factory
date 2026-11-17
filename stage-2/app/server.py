"""The HTTP surface: routing, validation, authentication, idempotency (§3-§8)."""
from __future__ import annotations

import json
import re
import socketserver
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import errors, store as state
from . import ui
from .builds import Builder
from .domain import InvalidDomain, normalised_or_raise

PLAIN_DIGITS = re.compile(r"[0-9]+")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def parse_object(raw: bytes) -> dict:
    """A request body as a JSON object: 400 `malformed_request` when it is anything else."""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise errors.malformed() from None
    if not isinstance(parsed, dict):
        raise errors.malformed()
    return parsed


def required_domain(data: dict) -> str:
    """The body's `domain`, normalised: 422 when missing or invalid, 400 when not a string."""
    if "domain" not in data:
        raise errors.validation("domain is required")
    raw = data["domain"]
    if not isinstance(raw, str):
        raise errors.malformed("domain must be a string")
    try:
        return normalised_or_raise(raw)
    except InvalidDomain:
        raise errors.validation("domain is not a valid host name") from None


def path_domain(segment: str) -> str:
    """A domain from a path segment, normalised, 422 when it is not a valid host name."""
    try:
        return normalised_or_raise(segment)
    except InvalidDomain:
        raise errors.validation("domain is not a valid host name") from None


def paging(query: dict) -> tuple[int, int]:
    """`limit` and `offset`: plain decimal digits, within §5's ranges."""
    return (_paging_value(query, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
            _paging_value(query, "offset", 0, 0, None))


def _paging_value(query: dict, name: str, default: int, minimum: int,
                  maximum: int | None) -> int:
    """One integer-valued query parameter, refused unless it is written in plain digits."""
    values = query.get(name)
    if not values:
        return default
    raw = values[0]
    if not PLAIN_DIGITS.fullmatch(raw):
        raise errors.validation(f"{name} must be an integer written in plain decimal digits")
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        raise errors.validation(f"{name} is out of range")
    return value


class Html:
    """A response body that is the browser document rather than JSON."""

    def __init__(self, text: str) -> None:
        self.text = text


def wants_html(headers) -> bool:
    """Whether the caller asked for the browser document (§2, Screens)."""
    return "text/html" in (headers.get("Accept") or "").lower()


def browser_page(path: str) -> str | None:
    """The document a browser route serves, or None for an API route."""
    if path in ("/", "/signup", "/login", "/contested"):
        return ui.page()
    if path.startswith("/dossiers/"):
        return ui.page()
    return None


class Application:
    """The service's state and the build runner, and every route over them."""

    def __init__(self) -> None:
        self.store = state.Store()
        self.builder = Builder(self.store)

    # -- routing -----------------------------------------------------------

    def route(self, method: str, path: str, query: dict, headers, raw: bytes):
        """Answer one request as (status, body); raise :class:`ApiError` for a failure."""
        when = self.store.stamp()
        path = path.rstrip("/") or "/"
        if method == "GET" and wants_html(headers):
            document = browser_page(path)
            if document is not None:
                return 200, Html(document)
        if path == "/health" and method == "GET":
            return 200, {"status": "ok"}
        if path == "/_test/reset" and method == "POST":
            self.store.reset(parse_object(raw))
            return 204, None
        if path == "/_test/clock" and method == "POST":
            data = parse_object(raw)
            if "now" not in data:
                raise errors.validation("now is required")
            self.store.set_clock(data["now"])
            return 204, None
        if path == "/_test/export" and method == "GET":
            return 200, self.store.export()
        if path == "/_test/import" and method == "POST":
            self.store.import_state(parse_object(raw))
            return 204, None
        if path == "/auth/signup" and method == "POST":
            return 201, self.store.signup(parse_object(raw))
        if path == "/auth/login" and method == "POST":
            return 200, self.store.login(parse_object(raw))
        segments = [segment for segment in path.split("/") if segment]
        if segments == ["dossiers"] and method == "POST":
            user = self.authenticate(headers)
            return self.create_dossier(user, path, headers, raw, when)
        if segments == ["dossiers"] and method == "GET":
            self.authenticate(headers)
            limit, offset = paging(query)
            return 200, self.store.list_dossiers(_first(query, "q", ""), limit, offset)
        if len(segments) == 2 and segments[0] == "dossiers" and method == "GET":
            self.authenticate(headers)
            domain = path_domain(segments[1])
            return 200, self.store.view(domain)
        if len(segments) == 3 and segments[0] == "dossiers" and segments[2] == "refresh" \
                and method == "POST":
            user = self.authenticate(headers)
            return self.refresh(user, path, segments[1], headers, when)
        raise errors.not_found("no such endpoint")

    # -- authentication ----------------------------------------------------

    def authenticate(self, headers) -> dict:
        """The account a bearer token names, or 401 `unauthenticated`."""
        header = (headers.get("Authorization") or "").strip()
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            raise errors.unauthenticated()
        token = token.strip()
        if not token or " " in token:
            raise errors.unauthenticated()
        user = self.store.user_for_token(token)
        if user is None:
            raise errors.unauthenticated()
        return user

    # -- the two write paths (§7, §8) --------------------------------------

    def create_dossier(self, user: dict, path: str, headers, raw: bytes, when: str):
        """`POST /dossiers`: create and build a dossier, or report the existing one."""
        key = state.parse_idempotency_key(headers.get("Idempotency-Key"))
        data = parse_object(raw)
        fingerprint = state.canonical({"domain": data.get("domain")})
        with self.store.key_lock(user["id"], key):
            stored = self.store.claimed(user["id"], key, "POST", path, fingerprint)
            if stored is not None:
                return 200, stored.payload
            domain = required_domain(data)
            payload, dossier, created = self.store.create_or_existing(domain)
            status = 202 if created else 200
            if created:
                self.builder.start(dossier, dossier.generation, when)
            self.store.remember(user["id"], key, "POST", path, fingerprint, status, payload)
            return status, payload

    def refresh(self, user: dict, path: str, raw_domain: str, headers, when: str):
        """`POST /dossiers/{domain}/refresh`: start a new build unless one is running."""
        key = state.parse_idempotency_key(headers.get("Idempotency-Key"))
        fingerprint = state.canonical({})
        with self.store.key_lock(user["id"], key):
            stored = self.store.claimed(user["id"], key, "POST", path, fingerprint)
            if stored is not None:
                return 200, stored.payload
            domain = path_domain(raw_domain)
            dossier = self.store.dossier(domain)
            if dossier is None:
                raise errors.not_found("no dossier for that domain")
            generation = self.store.claim_build(dossier)
            payload = {"dossier_id": dossier.dossier_id, "domain": dossier.domain,
                       "state": "building"}
            if generation is not None:
                self.builder.start(dossier, generation, when)
            self.store.remember(user["id"], key, "POST", path, fingerprint, 202, payload)
            return 202, payload


def _first(query: dict, name: str, default: str) -> str:
    """The first value of a query parameter, or `default`."""
    values = query.get(name)
    return values[0] if values else default


class Handler(BaseHTTPRequestHandler):
    """Reads one request, hands it to the application and writes the answer."""

    protocol_version = "HTTP/1.1"
    server_version = "Dossier/1.0"
    app: Application | None = None

    def log_message(self, *args):  # pylint: disable=arguments-differ
        """Keep the container's log to our own lines."""

    def do_GET(self):  # noqa: N802 - the stdlib's method name
        """Route a GET."""
        self._dispatch("GET")

    def do_HEAD(self):  # noqa: N802 - the stdlib's method name
        """Route a HEAD, which answers like a GET without a body."""
        self._dispatch("HEAD")

    def do_OPTIONS(self):  # noqa: N802 - the stdlib's method name
        """Route an OPTIONS to the 404 envelope."""
        self._dispatch("OPTIONS")

    def do_POST(self):  # noqa: N802 - the stdlib's method name
        """Route a POST."""
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802 - the stdlib's method name
        """Route an unsupported method to the 404 envelope."""
        self._dispatch("PUT")

    def do_DELETE(self):  # noqa: N802 - the stdlib's method name
        """Route an unsupported method to the 404 envelope."""
        self._dispatch("DELETE")

    def do_PATCH(self):  # noqa: N802 - the stdlib's method name
        """Route an unsupported method to the 404 envelope."""
        self._dispatch("PATCH")

    def _dispatch(self, method: str) -> None:
        """Parse the request, answer it, and always send a body of the right shape."""
        parsed = urlsplit(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            status, payload = self.app.route(method, unquote(parsed.path), query,
                                             self.headers, raw)
        except errors.ApiError as exc:
            status, payload = exc.status, exc.body()
        except Exception:  # noqa: BLE001 - never answer 5xx: this is the last-resort log
            traceback.print_exc(file=sys.stderr)
            status = 500
            payload = {"error": {"code": "internal_error", "message": "internal error"}}
        self._reply(status, payload)

    def _reply(self, status: int, payload) -> None:
        """Write a JSON answer, the browser document, or no body at all for 204."""
        if isinstance(payload, Html):
            data = payload.text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)


class Server(ThreadingHTTPServer):
    """A threaded server whose backlog takes a burst of concurrent requests (§11)."""

    request_queue_size = 256
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        """Bind without resolving the host's name: no DNS lookup at run time, and no
        slow start on a machine whose resolver is far away."""
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def serve(port: int) -> None:
    """Serve the API on `0.0.0.0:port` until the process is stopped."""
    application = Application()
    handler = type("BoundHandler", (Handler,), {"app": application})
    server = Server(("0.0.0.0", port), handler)
    print(f"dossier listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
