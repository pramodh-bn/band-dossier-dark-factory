"""Service state: the frozen clock, accounts, tokens, dossiers and idempotent responses.

One lock guards the whole state. Every mutation takes it, every read that must be
consistent takes it, and the two control endpoints replace the state atomically.
A build consults `generation` before it records anything, so a reset or an import
that lands while it runs can never change the state that follows it.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import errors, evidence, passwords
from .domain import InvalidDomain, normalised_or_raise

TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
MAX_KEY = 255
MIN_PASSWORD = 8
MAX_DISPLAY_NAME = 100
TRACK = "dossier"
FORMAT_VERSION = 1


class FixtureError(Exception):
    """A fixture or imported state that cannot be loaded; carries the API error to raise."""

    def __init__(self, api_error: errors.ApiError) -> None:
        super().__init__(api_error.message)
        self.api_error = api_error


def parse_time(text) -> datetime:
    """An RFC 3339 timestamp with an explicit offset, as an aware datetime."""
    if not isinstance(text, str) or not TIME.fullmatch(text):
        raise ValueError(f"not an RFC 3339 timestamp: {text!r}")
    return datetime.fromisoformat(text.upper().replace("Z", "+00:00"))


def format_time(moment: datetime) -> str:
    """A datetime as the RFC 3339 timestamp a response carries."""
    return moment.isoformat()


def empty_fields() -> dict:
    """All six fields of a dossier, each unknown (§4)."""
    return {name: {"value": None, "status": "unknown", "citations": []}
            for name in evidence.FIELDS}


def summary(dossier: "Dossier") -> dict:
    """The body `POST /dossiers` and `POST /dossiers/{domain}/refresh` answer with."""
    return {"dossier_id": dossier.dossier_id, "domain": dossier.domain,
            "state": dossier.state}


@dataclass
class Dossier:
    """One company's dossier and the build, if any, that is researching it."""

    dossier_id: str
    domain: str
    state: str = "building"
    fields: dict = field(default_factory=empty_fields)
    researched_at: str | None = None
    spent_units: int = 0
    running: bool = False
    generation: int = 0
    committed: dict | None = None

    def view(self) -> dict:
        """The body `GET /dossiers/{domain}` answers with."""
        return {"dossier_id": self.dossier_id, "domain": self.domain, "state": self.state,
                "researched_at": self.researched_at, "spent_units": self.spent_units,
                "fields": copy.deepcopy(self.fields)}

    def listing(self) -> dict:
        """The entry `GET /dossiers` carries for this dossier."""
        legal = self.fields["legal_name"]
        return {"dossier_id": self.dossier_id, "domain": self.domain, "state": self.state,
                "legal_name": legal["value"],
                "researched_at": self.researched_at, "spent_units": self.spent_units}

    def settled(self) -> dict:
        """The dossier as it was before its running build, for an export taken mid-build."""
        if self.committed is not None:
            return copy.deepcopy(self.committed)
        return {"dossier_id": self.dossier_id, "domain": self.domain, "state": "failed",
                "fields": empty_fields(), "researched_at": None, "spent_units": 0}

    def state_dict(self) -> dict:
        """This dossier as export stores it."""
        value = self.settled() if self.running else {
            "dossier_id": self.dossier_id, "domain": self.domain, "state": self.state,
            "fields": copy.deepcopy(self.fields), "researched_at": self.researched_at,
            "spent_units": self.spent_units}
        return value


@dataclass
class StoredResponse:
    """One completed idempotent request: its fingerprint and the response it produced."""

    body: str
    status: int
    payload: dict


class Store:  # pylint: disable=too-many-instance-attributes
    """The whole service state, and every operation the API performs on it."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._clock = datetime.now(timezone.utc)
        self.generation = 0
        self.users: dict[str, dict] = {}
        self.by_email: dict[str, str] = {}
        self.tokens: dict[str, str] = {}
        self.dossiers: dict[str, Dossier] = {}
        self.idempotency: dict[tuple[str, str], dict[tuple[str, str], StoredResponse]] = {}
        self._user_seq = 0
        self._dossier_seq = 0
        self._key_locks: dict[tuple[str, str], threading.Lock] = {}

    # -- the clock (§3.4) ---------------------------------------------------

    def now(self) -> datetime:
        """The clock's time."""
        with self.lock:
            return self._clock

    def stamp(self) -> str:
        """The clock's time, formatted for a response."""
        return format_time(self.now())

    def set_clock(self, raw) -> None:
        """`POST /_test/clock`: move the clock forward, or 422 when `raw` is not later."""
        try:
            moment = parse_time(raw)
        except ValueError as exc:
            raise errors.validation(str(exc)) from None
        with self.lock:
            if moment < self._clock:
                raise errors.validation("now is earlier than the clock's current time")
            self._clock = moment

    # -- reset (§3.3) -------------------------------------------------------

    def reset(self, fixture) -> None:
        """Replace every byte of state with the fixture, atomically and synchronously."""
        if not isinstance(fixture, dict):
            raise errors.malformed()
        moment = self.validated_time(fixture, required=True)
        users = self.prepared_users(fixture.get("users", []))
        with self.lock:
            self.generation += 1
            self._clock = moment
            self.users = {}
            self.by_email = {}
            self.tokens = {}
            self.dossiers = {}
            self.idempotency = {}
            self._key_locks = {}
            self._user_seq = 0
            self._dossier_seq = 0
            for user in users:
                self.users[user["id"]] = user
                self.by_email[user["email"].lower()] = user["id"]

    @staticmethod
    def validated_time(fixture: dict, *, required: bool) -> datetime:
        """The fixture's `now`, which is required and must be an RFC 3339 timestamp with offset."""
        if "now" not in fixture:
            if required:
                raise errors.validation("the fixture needs a now timestamp")
            return datetime.now(timezone.utc)
        try:
            return parse_time(fixture["now"])
        except ValueError as exc:
            raise errors.validation(str(exc)) from None

    @staticmethod
    def prepared_users(raw) -> list[dict]:
        """The fixture's users, hashed and checked for the shared-email error."""
        if not isinstance(raw, list):
            raise errors.malformed("users must be a list")
        prepared: list[dict] = []
        seen: set[str] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise errors.malformed("each user must be an object")
            for name in ("id", "email", "password", "display_name"):
                if name not in entry:
                    raise errors.validation(f"each fixture user needs a {name}")
                if not isinstance(entry[name], str):
                    raise errors.malformed(f"a user's {name} must be a string")
            email = entry["email"].lower()
            if email in seen:
                raise errors.validation("two fixture users share an email")
            seen.add(email)
            prepared.append({"id": entry["id"], "email": entry["email"],
                             "password_hash": passwords.hash_password(entry["password"]),
                             "display_name": entry["display_name"]})
        return prepared

    # -- accounts (§6) ------------------------------------------------------

    def signup(self, data: dict) -> dict:
        """`POST /auth/signup`: create an account and return a token."""
        email = required_string(data, "email")
        password = required_string(data, "password")
        display_name = required_string(data, "display_name")
        if not EMAIL.fullmatch(email):
            raise errors.validation("email must be of the form local@domain")
        if len(password) < MIN_PASSWORD:
            raise errors.validation("the password is shorter than 8 characters")
        if not display_name.strip() or len(display_name) > MAX_DISPLAY_NAME:
            raise errors.validation("display_name must be 1 to 100 characters")
        with self.lock:
            if email.lower() in self.by_email:
                raise errors.email_taken()
        user = {"id": None, "email": email, "password_hash": passwords.hash_password(password),
                "display_name": display_name}
        with self.lock:
            if email.lower() in self.by_email:
                raise errors.email_taken()
            user["id"] = self._next_user_id()
            token = passwords.new_token()
            self.users[user["id"]] = user
            self.by_email[email.lower()] = user["id"]
            self.tokens[token] = user["id"]
        return {"user_id": user["id"], "display_name": display_name, "token": token}

    def login(self, data: dict) -> dict:
        """`POST /auth/login`: check a password and return a fresh token."""
        email = required_string(data, "email")
        password = required_string(data, "password")
        with self.lock:
            user_id = self.by_email.get(email.lower())
            user = self.users.get(user_id) if user_id else None
        if user is None or not passwords.verify_password(password, user["password_hash"]):
            raise errors.unauthenticated()
        with self.lock:
            token = passwords.new_token()
            self.tokens[token] = user["id"]
        return {"user_id": user["id"], "display_name": user["display_name"], "token": token}

    def user_for_token(self, token: str) -> dict | None:
        """The account a bearer token names, or None."""
        with self.lock:
            user_id = self.tokens.get(token)
            return self.users.get(user_id) if user_id else None

    # -- dossiers (§8) ------------------------------------------------------

    def dossier(self, domain: str) -> Dossier | None:
        """The dossier for a normalised domain, or None."""
        with self.lock:
            return self.dossiers.get(domain)

    def view(self, domain: str) -> dict:
        """`GET /dossiers/{domain}`, or 404 when no dossier exists for the domain."""
        with self.lock:
            dossier = self.dossiers.get(domain)
            if dossier is None:
                raise errors.not_found("no dossier for that domain")
            return dossier.view()

    def create_or_existing(self, domain: str) -> tuple[dict, Dossier, bool]:
        """The dossier for `domain`, creating it when it does not exist, and the body to answer with.

        The second value is the dossier and the third is True when this call created
        it; a dossier created here is marked `building` so a concurrent request
        cannot start a second build.
        """
        with self.lock:
            existing = self.dossiers.get(domain)
            if existing is not None:
                return summary(existing), existing, False
            dossier = Dossier(dossier_id=self._next_dossier_id(), domain=domain,
                              state="building", running=True, generation=self.generation)
            self.dossiers[domain] = dossier
            return summary(dossier), dossier, True

    def _next_dossier_id(self) -> str:
        """A fresh opaque dossier id that no dossier -- imported or created -- already holds."""
        taken = {dossier.dossier_id for dossier in self.dossiers.values()}
        while True:
            self._dossier_seq += 1
            candidate = f"d_{self._dossier_seq}"
            if candidate not in taken:
                return candidate

    def _next_user_id(self) -> str:
        """A fresh opaque user id that no account -- seeded or signed up -- already holds.

        A fixture may seed any ids it likes, `u_1` included; a new account must never
        take one of them, or the signup would overwrite a seeded user.
        """
        while True:
            self._user_seq += 1
            candidate = f"u_{self._user_seq}"
            if candidate not in self.users:
                return candidate

    def claim_build(self, dossier: Dossier) -> int | None:
        """Start a build of `dossier`, or None when one is already running."""
        with self.lock:
            if self.dossiers.get(dossier.domain) is not dossier or dossier.running:
                return None
            if dossier.committed is None:
                dossier.committed = {
                    "dossier_id": dossier.dossier_id,
                    "domain": dossier.domain,
                    "state": dossier.state if dossier.researched_at is not None else "failed",
                    "fields": copy.deepcopy(dossier.fields),
                    "researched_at": dossier.researched_at,
                    "spent_units": dossier.spent_units,
                }
            dossier.running = True
            dossier.state = "building"
            dossier.generation = self.generation
            return dossier.generation

    def still_current(self, dossier: Dossier, generation: int) -> bool:
        """Whether a build may still record calls and its outcome."""
        with self.lock:
            return (self.dossiers.get(dossier.domain) is dossier
                    and dossier.running and dossier.generation == generation)

    def add_spend(self, dossier: Dossier, generation: int, units: int) -> None:
        """Record what one provider call cost (or gave back) this dossier."""
        if not units:
            return
        with self.lock:
            if self.still_current(dossier, generation):
                dossier.spent_units = max(0, dossier.spent_units + units)

    def finish_build(self, dossier: Dossier, generation: int, ready: bool,
                     fields: dict, researched_at: str) -> None:
        """Record a build's outcome, unless a reset or import has superseded it."""
        with self.lock:
            if not self.still_current(dossier, generation):
                return
            if ready:
                dossier.fields = fields
                dossier.researched_at = researched_at
                dossier.state = "ready"
            else:
                dossier.state = "failed"
            dossier.running = False
            dossier.committed = None

    def list_dossiers(self, q: str, limit: int, offset: int) -> dict:
        """`GET /dossiers`: dossiers ordered by domain, filtered by `q`, paged."""
        needle = q.lower()
        with self.lock:
            entries = [dossier.listing() for _, dossier in sorted(self.dossiers.items())]
        if needle:
            entries = [entry for entry in entries
                       if needle in entry["domain"].lower()
                       or needle in (entry["legal_name"] or "").lower()]
        window = entries[offset:offset + limit]
        return {"dossiers": window, "has_more": len(entries) > offset + len(window)}

    # -- idempotency (§7) ---------------------------------------------------

    def key_lock(self, user_id: str, key: str) -> threading.Lock:
        """The lock serialising requests that share one user's idempotency key."""
        with self.lock:
            return self._key_locks.setdefault((user_id, key), threading.Lock())

    def claimed(self, user_id: str, key: str, method: str, path: str,
                fingerprint: str) -> StoredResponse | None:
        """The stored response for this exact request, or None; 409 when the key was reused
        with a different body on the same path."""
        with self.lock:
            stored = self.idempotency.get((user_id, key), {}).get((method, path))
            if stored is None:
                return None
            if stored.body != fingerprint:
                raise errors.key_reuse()
            return stored

    def remember(self, user_id: str, key: str, method: str, path: str, fingerprint: str,
                 status: int, payload: dict) -> None:
        """Store a successful response so a replay returns it unchanged."""
        with self.lock:
            self.idempotency.setdefault((user_id, key), {})[(method, path)] = StoredResponse(
                body=fingerprint, status=status, payload=copy.deepcopy(payload))

    # -- export and import (§10) -------------------------------------------

    def export_state(self) -> dict:
        """A snapshot of every piece of state, taken atomically."""
        with self.lock:
            dossiers = [dossier.state_dict() for _, dossier in sorted(self.dossiers.items())]
            responses = [{"user_id": user_id, "key": key, "method": method, "path": path,
                          "body": stored.body, "status": stored.status,
                          "payload": copy.deepcopy(stored.payload)}
                         for (user_id, key), entries in sorted(self.idempotency.items())
                         for (method, path), stored in sorted(entries.items())]
            return {
                "now": format_time(self._clock),
                "users": [copy.deepcopy(user) for user in self.users.values()],
                "tokens": dict(self.tokens),
                "dossiers": dossiers,
                "idempotency": responses,
                "sequences": {"user": self._user_seq, "dossier": self._dossier_seq},
            }

    def export(self) -> dict:
        """`GET /_test/export`."""
        return {"track": TRACK, "format_version": FORMAT_VERSION, "state": self.export_state()}

    def import_state(self, body) -> None:
        """`POST /_test/import`: validate the whole payload, then replace the state."""
        if not isinstance(body, dict):
            raise errors.malformed()
        if body.get("track") != TRACK or body.get("format_version") != FORMAT_VERSION:
            raise errors.validation("this is not a dossier export of format_version 1")
        state = body.get("state")
        if not isinstance(state, dict):
            raise errors.validation("state must be an object")
        try:
            users, tokens, dossiers, responses, sequences = validated_state(state)
        except FixtureError as exc:
            raise exc.api_error from None
        with self.lock:
            self.generation += 1
            self.users = users
            self.by_email = {user["email"].lower(): user["id"] for user in users.values()}
            self.tokens = tokens
            self.dossiers = dossiers
            self.idempotency = responses
            self._user_seq = sequences[0]
            self._dossier_seq = sequences[1]
            self._key_locks = {}


def validated_state(state: dict):
    """A validated export payload, ready to become the service's state."""
    try:
        parse_time(state.get("now"))
    except ValueError as exc:
        raise FixtureError(errors.validation(f"state.now: {exc}")) from None
    users = _validated_users(state.get("users"))
    tokens = _validated_tokens(state.get("tokens"), users)
    dossiers = _validated_dossiers(state.get("dossiers"))
    responses = _validated_responses(state.get("idempotency"))
    sequences = state.get("sequences", {})
    if not isinstance(sequences, dict):
        raise FixtureError(errors.validation("state.sequences must be an object"))
    try:
        user_seq = int(sequences.get("user", len(users)))
        dossier_seq = int(sequences.get("dossier", len(dossiers)))
    except (TypeError, ValueError):
        raise FixtureError(errors.validation("state.sequences must hold integers")) from None
    return users, tokens, dossiers, responses, (user_seq, dossier_seq)


def _validated_users(raw) -> dict[str, dict]:
    """The imported accounts, keyed by id."""
    if not isinstance(raw, list):
        raise FixtureError(errors.validation("state.users must be a list"))
    users: dict[str, dict] = {}
    emails: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixtureError(errors.validation("each state user must be an object"))
        values = {}
        for name in ("id", "email", "display_name", "password_hash"):
            value = entry.get(name)
            if not isinstance(value, str) or not value:
                raise FixtureError(errors.validation(f"a state user needs a string {name}"))
            values[name] = value
        if values["id"] in users:
            raise FixtureError(errors.validation("two state users share an id"))
        if values["email"].lower() in emails:
            raise FixtureError(errors.validation("two state users share an email"))
        emails.add(values["email"].lower())
        users[values["id"]] = values
    return users


def _validated_tokens(raw, users: dict[str, dict]) -> dict[str, str]:
    """The imported bearer tokens, each naming an imported account."""
    if not isinstance(raw, dict):
        raise FixtureError(errors.validation("state.tokens must be an object"))
    tokens: dict[str, str] = {}
    for token, user_id in raw.items():
        if not isinstance(token, str) or not isinstance(user_id, str) or user_id not in users:
            raise FixtureError(errors.validation("a state token does not name a user"))
        tokens[token] = user_id
    return tokens


def _validated_dossiers(raw) -> dict[str, Dossier]:
    """The imported dossiers, keyed by domain."""
    if not isinstance(raw, list):
        raise FixtureError(errors.validation("state.dossiers must be a list"))
    dossiers: dict[str, Dossier] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixtureError(errors.validation("each state dossier must be an object"))
        dossier_id = entry.get("dossier_id")
        domain = entry.get("domain")
        state = entry.get("state")
        researched_at = entry.get("researched_at")
        spent = entry.get("spent_units")
        fields = entry.get("fields")
        if not isinstance(dossier_id, str) or not isinstance(domain, str):
            raise FixtureError(errors.validation("a state dossier needs an id and a domain"))
        try:
            if normalised_or_raise(domain) != domain:
                raise InvalidDomain(domain)
        except InvalidDomain:
            raise FixtureError(errors.validation("a state dossier's domain is not normalised")) \
                from None
        if state not in ("ready", "failed", "building"):
            raise FixtureError(errors.validation("a state dossier's state is not recognised"))
        if researched_at is not None:
            try:
                parse_time(researched_at)
            except ValueError as exc:
                raise FixtureError(errors.validation(f"researched_at: {exc}")) from None
        if not isinstance(spent, int) or isinstance(spent, bool) or spent < 0:
            raise FixtureError(errors.validation("spent_units must be a non-negative integer"))
        if not isinstance(fields, dict) or set(fields) != set(evidence.FIELDS):
            raise FixtureError(errors.validation("a state dossier needs all six fields"))
        for name, value in fields.items():
            if not isinstance(value, dict) or not isinstance(value.get("citations"), list):
                raise FixtureError(errors.validation(f"a state field {name} is malformed"))
        dossier = Dossier(dossier_id=dossier_id, domain=domain,
                          state="failed" if state == "building" else state,
                          fields=copy.deepcopy(fields), researched_at=researched_at,
                          spent_units=spent)
        if dossier_id in {other.dossier_id for other in dossiers.values()}:
            raise FixtureError(errors.validation("two state dossiers share an id"))
        dossiers[domain] = dossier
    return dossiers


def _validated_responses(raw) -> dict[tuple[str, str], dict[tuple[str, str], StoredResponse]]:
    """The imported idempotent responses, keyed as the live store keys them."""
    if not isinstance(raw, list):
        raise FixtureError(errors.validation("state.idempotency must be a list"))
    stored: dict[tuple[str, str], dict[tuple[str, str], StoredResponse]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixtureError(errors.validation("each stored response must be an object"))
        user_id, key, method, path = (entry.get(name) for name in
                                      ("user_id", "key", "method", "path"))
        body, status, payload = entry.get("body"), entry.get("status"), entry.get("payload")
        if not all(isinstance(value, str) for value in (user_id, key, method, path, body)):
            raise FixtureError(errors.validation("a stored response is missing a string field"))
        if not isinstance(status, int) or not isinstance(payload, dict):
            raise FixtureError(errors.validation("a stored response is missing its answer"))
        stored.setdefault((user_id, key), {})[(method, path)] = StoredResponse(
            body=body, status=status, payload=copy.deepcopy(payload))
    return stored


def required_string(data: dict, name: str) -> str:
    """A required string field of a request body: 422 when missing, 400 when of a wrong type."""
    if name not in data:
        raise errors.validation(f"{name} is required")
    value = data[name]
    if not isinstance(value, str):
        raise errors.malformed(f"{name} must be a string")
    return value


def canonical(value) -> str:
    """A JSON value's canonical text, for comparing two requests' bodies."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def parse_idempotency_key(raw: str | None) -> str:
    """The `Idempotency-Key` header: 400 when absent or empty, 422 outside 1..255 characters."""
    key = (raw or "").strip()
    if not key:
        raise errors.missing_key()
    if len(key) > MAX_KEY:
        raise errors.validation("an Idempotency-Key is 1 to 255 characters")
    return key
