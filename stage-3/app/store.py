"""Service state: the clock, accounts, dossiers, builds, history, the feed and idempotency.

One lock guards the whole state. Every mutation takes it, every read that must be
consistent takes it, and the control endpoints replace the state atomically. A
build consults `generation` before it records anything, so a reset or an import
that lands while it runs can never change the state that follows it.

From stage 3 a dossier also keeps its builds (each with the candidates it gathered
and one page version per URL), the history of each field's selected value, and the
change feed. The whole state is written to a file after every mutation, so an
abrupt restart answers exactly as the service did before it.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from . import errors, passwords
from . import candidates as candidates_module
from . import pages as pages_module
from .domain import InvalidDomain, normalised_or_raise

TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
MAX_KEY = 255
MIN_PASSWORD = 8
MAX_DISPLAY_NAME = 100
TRACK = "dossier"
FORMAT_VERSION = 1
FIELDS = candidates_module.ALL_FIELDS
SIX = candidates_module.FIELDS
TITLES = candidates_module.TITLES


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


def parse_day(text) -> str:
    """A `YYYY-MM-DD` date, as written, or ValueError."""
    if not isinstance(text, str) or not DAY.fullmatch(text):
        raise ValueError(f"not a YYYY-MM-DD date: {text!r}")
    return date.fromisoformat(text).isoformat()


def empty_fields() -> dict:
    """All seven fields of a dossier, each unknown and without candidates (§4, §2, §3)."""
    return {name: {"value": None, "status": "unknown", "citations": [], "candidates": [],
                   "true_from": None, "learned_at": None}
            for name in FIELDS}


def summary(dossier: "Dossier") -> dict:
    """The body `POST /dossiers` and `POST /dossiers/{domain}/refresh` answer with."""
    return {"dossier_id": dossier.dossier_id, "domain": dossier.domain,
            "state": dossier.state}


def candidate_identity(name: str, candidate: dict) -> str:
    """What makes two candidates the same candidate for `learned_at` (§3)."""
    return json.dumps([name, candidate.get("title"), candidates_module.canonical(
        candidate.get("value")), candidate.get("url"), candidate.get("provider")],
        sort_keys=True)


@dataclass
class Dossier:  # pylint: disable=too-many-instance-attributes
    """One company's dossier, its builds, its history and the build researching it."""

    dossier_id: str
    domain: str
    state: str = "building"
    fields: dict = field(default_factory=empty_fields)
    researched_at: str | None = None
    spent_units: int = 0
    running: bool = False
    generation: int = 0
    committed: dict | None = None
    builds: list = field(default_factory=list)
    history: dict = field(default_factory=lambda: {name: [] for name in FIELDS})
    learned: dict = field(default_factory=dict)
    initialised: bool = False

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
                "fields": empty_fields(), "researched_at": None, "spent_units": 0,
                "builds": [], "history": {name: [] for name in FIELDS}, "learned": {},
                "initialised": False}

    def state_dict(self) -> dict:
        """This dossier as export stores it."""
        if self.running:
            return self.settled()
        return {"dossier_id": self.dossier_id, "domain": self.domain, "state": self.state,
                "fields": copy.deepcopy(self.fields), "researched_at": self.researched_at,
                "spent_units": self.spent_units,
                "builds": copy.deepcopy(self.builds),
                "history": copy.deepcopy(self.history),
                "learned": dict(self.learned), "initialised": self.initialised}


@dataclass
class StoredResponse:
    """One completed idempotent request: its fingerprint and the response it produced."""

    body: str
    status: int
    payload: dict


class Store:  # pylint: disable=too-many-instance-attributes
    """The whole service state, and every operation the API performs on it."""

    def __init__(self, path: str | None = None) -> None:
        self.lock = threading.RLock()
        self.path = path
        self._clock = datetime.now(timezone.utc)
        self.generation = 0
        self.users: dict[str, dict] = {}
        self.by_email: dict[str, str] = {}
        self.tokens: dict[str, str] = {}
        self.dossiers: dict[str, Dossier] = {}
        self.idempotency: dict[tuple[str, str], dict[tuple[str, str], StoredResponse]] = {}
        self.feed: list[dict] = []
        self.seq = 0
        self._user_seq = 0
        self._dossier_seq = 0
        self._key_locks: dict[tuple[str, str], threading.Lock] = {}
        self._persist_lock = threading.Lock()
        if path:
            self.load()

    # -- persistence (§3, Restarts) ----------------------------------------

    def persist(self) -> None:
        """Write the whole state to the state file, atomically, when one is configured.

        The snapshot is taken and written under the state lock, so the file always
        matches the state a reader has just seen; the write itself is serialised by
        its own lock so two writers never interleave.
        """
        if not self.path:
            return
        with self.lock:
            with self._persist_lock:
                body = json.dumps(self.export_state(), ensure_ascii=False)
                directory = os.path.dirname(os.path.abspath(self.path)) or "."
                try:
                    os.makedirs(directory, exist_ok=True)
                    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".state-",
                                                         suffix=".json")
                except OSError as exc:
                    print(f"dossier: cannot persist state ({exc})", file=sys.stderr, flush=True)
                    return
                try:
                    with os.fdopen(handle, "w", encoding="utf-8") as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                    try:
                        folder = os.open(directory, os.O_RDONLY)
                        try:
                            os.fsync(folder)
                        finally:
                            os.close(folder)
                    except OSError:
                        pass
                except OSError as exc:
                    print(f"dossier: cannot persist state ({exc})", file=sys.stderr, flush=True)
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass

    def load(self) -> None:
        """Read the state file when it exists; a missing or unreadable file starts empty."""
        try:
            with open(self.path, encoding="utf-8") as stream:
                state = json.load(stream)
        except (OSError, ValueError):
            return
        try:
            users, tokens, dossiers, responses, sequences, feed, seq = validated_state(state)
        except FixtureError:
            return
        with self.lock:
            self._clock = parse_time(state.get("now")) if state.get("now") else self._clock
            self.users = users
            self.by_email = {user["email"].lower(): user["id"] for user in users.values()}
            self.tokens = tokens
            self.dossiers = dossiers
            self.idempotency = responses
            self.feed = feed
            self.seq = seq
            self._user_seq, self._dossier_seq = sequences

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
        self.persist()

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
            self.feed = []
            self.seq = 0
            self._key_locks = {}
            self._user_seq = 0
            self._dossier_seq = 0
            for user in users:
                self.users[user["id"]] = user
                self.by_email[user["email"].lower()] = user["id"]
        self.persist()

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
        self.persist()
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
        self.persist()
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

    def view_as_of(self, domain: str, as_of) -> dict:
        """`GET /dossiers/{domain}?as_of=`: the dossier as the latest ready build at or
        before `as_of` left it (§3)."""
        try:
            moment = parse_time(as_of)
        except ValueError as exc:
            raise errors.validation(str(exc)) from None
        with self.lock:
            dossier = self.dossiers.get(domain)
            if dossier is None:
                raise errors.not_found("no dossier for that domain")
            ready = [build for build in dossier.builds
                     if build["state"] == "ready" and parse_time(build["time"]) <= moment]
            if not ready:
                raise errors.not_found("no ready build at or before that time")
            build = ready[-1]
            spent = sum(entry["spent"] for entry in dossier.builds
                        if parse_time(entry["time"]) <= moment)
            return {"dossier_id": dossier.dossier_id, "domain": dossier.domain,
                    "state": "ready", "researched_at": build["time"],
                    "spent_units": spent, "fields": copy.deepcopy(build["fields"])}

    def values_at(self, domain: str, true_at, as_of=None) -> dict:
        """`GET /dossiers/{domain}/values`: what the evidence at `as_of` says held on `true_at`."""
        try:
            day = parse_day(true_at)
        except ValueError as exc:
            raise errors.validation(str(exc)) from None
        if as_of is None:
            moment = self.now()
            as_of_text = format_time(moment)
        else:
            try:
                moment = parse_time(as_of)
            except ValueError as exc:
                raise errors.validation(str(exc)) from None
            as_of_text = as_of
        with self.lock:
            dossier = self.dossiers.get(domain)
            if dossier is None:
                raise errors.not_found("no dossier for that domain")
            builds = [build for build in dossier.builds
                      if build["state"] == "ready" and parse_time(build["time"]) <= moment]
            if not builds:
                raise errors.not_found("no ready build at or before that time")
            versions: dict[str, list[dict]] = {}
            for build in builds:
                for url, version in build["versions"].items():
                    _add_version(versions.setdefault(url, []), version)
            rows = {name: [] for name in FIELDS}
            for url_versions in versions.values():
                for version in url_versions:
                    if not _holds(version, day):
                        continue
                    for candidate in version["candidates"]:
                        rows[candidate["field"]].append(candidate)
            fields = {}
            for name in FIELDS:
                chosen = (candidates_module.executives_field(rows[name]) if name == "executives"
                          else candidates_module.field(rows[name]))
                fields[name] = {"value": chosen["value"], "true_from": chosen["true_from"],
                                "citations": chosen["citations"]}
            return {"domain": dossier.domain, "true_at": day, "as_of": as_of_text,
                    "fields": fields}

    def history_view(self, domain: str) -> dict:
        """`GET /dossiers/{domain}/history`: every field's entries, oldest first."""
        with self.lock:
            dossier = self.dossiers.get(domain)
            if dossier is None:
                raise errors.not_found("no dossier for that domain")
            return {"domain": dossier.domain,
                    "fields": {name: copy.deepcopy(dossier.history.get(name, []))
                               for name in FIELDS}}

    def changes(self, since: int, limit: int) -> dict:
        """`GET /changes`: the feed after `since`, oldest first, paged."""
        with self.lock:
            entries = [copy.deepcopy(entry) for entry in self.feed if entry["seq"] > since]
        window = entries[:limit]
        return {"changes": window, "has_more": len(entries) > len(window)}

    def create_or_existing(self, domain: str) -> tuple[dict, Dossier, bool]:
        """The dossier for `domain`, creating it when it does not exist, and the body to answer with."""
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
        """A fresh opaque user id that no account -- seeded or signed up -- already holds."""
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
                    "builds": copy.deepcopy(dossier.builds),
                    "history": copy.deepcopy(dossier.history),
                    "learned": dict(dossier.learned),
                    "initialised": dossier.initialised,
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
                     pages: dict, when: str, spent: int) -> None:
        """Record a build's outcome, its candidates, its page versions and its changes."""
        with self.lock:
            if not self.still_current(dossier, generation):
                return
            if ready:
                fields = self._fields_from_pages(dossier, pages, when)
                self._record_changes(dossier, fields, when)
                dossier.fields = fields
                dossier.researched_at = when
                dossier.state = "ready"
                dossier.builds.append({"time": when, "state": "ready", "spent": spent,
                                       "fields": copy.deepcopy(fields),
                                       "versions": copy.deepcopy(pages)})
            else:
                dossier.state = "failed"
                dossier.builds.append({"time": when, "state": "failed", "spent": spent,
                                       "fields": None, "versions": {}})
            dossier.running = False
            dossier.committed = None
            self.persist()

    def _fields_from_pages(self, dossier: Dossier, pages: dict, when: str) -> dict:
        """The seven fields a build's page versions support, with `learned_at` assigned."""
        rows = {name: [] for name in FIELDS}
        for url in sorted(pages):
            for candidate in pages[url]["candidates"]:
                identity = candidate_identity(candidate["field"], candidate)
                learned = dossier.learned.setdefault(identity, when)
                rows[candidate["field"]].append({**candidate, "learned_at": learned})
        fields = {}
        for name in FIELDS:
            fields[name] = (candidates_module.executives_field(rows[name]) if name == "executives"
                            else candidates_module.field(rows[name]))
        self._selected_learned(dossier, fields, when)
        return fields

    @staticmethod
    def _selected_learned(dossier: Dossier, fields: dict, when: str) -> None:
        """A selected value's `learned_at` is the build that made it selected (§3).

        A candidate's own `learned_at` stays the first build that gathered it; the
        field's is the build that selected it. It therefore stays put while the
        selection does, and moves to the build that selects the value again after
        another value held the field.
        """
        for name in FIELDS:
            new = fields[name]
            if new["value"] is None:
                new["learned_at"] = None
                continue
            previous = dossier.fields.get(name) or {}
            if previous.get("value") is not None and candidates_module.same_value(
                    new["value"], previous["value"]):
                new["learned_at"] = previous.get("learned_at") or when
            else:
                new["learned_at"] = when

    def _record_changes(self, dossier: Dossier, fields: dict, when: str) -> None:
        """Append the history and feed entries one ready build makes (§3).

        The dossier's first ready build writes `initial` for each field it has a
        value for; a field that was unknown then gets its first entry, a `change`,
        when it first gets a value. Every later move is a `change` or a
        `correction`, and each one also reaches the feed.
        """
        first_ready = not dossier.initialised
        for name in FIELDS:
            new = fields[name]
            entries = dossier.history.setdefault(name, [])
            if not entries:
                if new["value"] is None:
                    continue
                if first_ready:
                    entries.append(_entry("initial", new, when))
                    continue
                entries.append(_entry("change", new, when))
                self._feed(dossier, name, "change", None, new, when)
                continue
            previous = entries[-1]
            if candidates_module.same_value(new["value"], previous["value"]):
                continue
            kind = _change_type(name, dossier.fields.get(name) or {}, new)
            entries.append(_entry(kind, new, when))
            self._feed(dossier, name, kind, previous["value"], new, when)
        dossier.initialised = True

    def _feed(self, dossier: Dossier, name: str, kind: str, previous, new: dict,
              when: str) -> None:
        """Append one feed entry for a field whose selected value moved."""
        self.seq += 1
        self.feed.append({"seq": self.seq, "domain": dossier.domain, "field": name,
                          "type": kind, "from": copy.deepcopy(previous),
                          "to": copy.deepcopy(new["value"]),
                          "true_from": new["true_from"], "learned_at": when})

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
        self.persist()

    # -- export and import (§10, §3 Upgrades) -------------------------------

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
                "feed": copy.deepcopy(self.feed),
                "seq": self.seq,
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
            users, tokens, dossiers, responses, sequences, feed, seq = validated_state(state)
        except FixtureError as exc:
            raise exc.api_error from None
        with self.lock:
            self.generation += 1
            self.users = users
            self.by_email = {user["email"].lower(): user["id"] for user in users.values()}
            self.tokens = tokens
            self.dossiers = dossiers
            self.idempotency = responses
            self.feed = feed
            self.seq = seq
            self._user_seq = sequences[0]
            self._dossier_seq = sequences[1]
            self._key_locks = {}
        self.persist()


def _entry(kind: str, field: dict, when: str) -> dict:
    """One history entry for a field's selected value."""
    return {"type": kind, "value": copy.deepcopy(field["value"]),
            "true_from": field["true_from"], "learned_at": when,
            "citations": copy.deepcopy(field["citations"])}


def _change_type(name: str, old: dict, new: dict) -> str:
    """`correction` or `change` for a field whose selected value moved (§3)."""
    if name == "executives":
        return _executives_type(old, new)
    old_value, new_value = old.get("value"), new.get("value")
    old_from, new_from = old.get("true_from"), new.get("true_from")
    if None in (old_value, new_value, old_from, new_from):
        return "change"
    return "correction" if new_from <= old_from else "change"


def _executives_type(old: dict, new: dict) -> str:
    """`correction` when every differing title is a correction, else `change` (§3)."""
    old_members = {member["title"]: member for member in old.get("value") or []}
    new_members = {member["title"]: member for member in new.get("value") or []}
    old_dates = {candidate["title"]: candidate.get("true_from")
                 for candidate in old.get("candidates") or [] if candidate.get("selected")}
    new_dates = {candidate["title"]: candidate.get("true_from")
                 for candidate in new.get("candidates") or [] if candidate.get("selected")}
    differing = [title for title in TITLES
                 if (title in old_members) != (title in new_members)
                 or (title in old_members
                     and old_members[title]["name"] != new_members[title]["name"])]
    if not differing:
        return "change"
    for title in differing:
        if title not in old_members or title not in new_members:
            return "change"
        if old_dates.get(title) is None or new_dates.get(title) is None:
            return "change"
        if new_dates[title] > old_dates[title]:
            return "change"
    return "correction"


def _add_version(versions: list[dict], version: dict) -> None:
    """Add one gathered page version, applying the replacement rule (§3, /values)."""
    start = version.get("true_from")
    if start is None:
        for old in versions:
            old["dead"] = True
        versions.append({"start": None, "end": None, "dead": True,
                         "candidates": version["candidates"]})
        return
    for old in versions:
        if old["start"] is not None and old["start"] >= start:
            old["dead"] = True
    holding = [old for old in versions
               if not old["dead"] and old["start"] is not None and old["end"] is None]
    if holding:
        holding[-1]["end"] = start
    versions.append({"start": start, "end": None, "dead": False,
                     "candidates": version["candidates"]})


def _holds(version: dict, day: str) -> bool:
    """Whether a page version holds on `day`."""
    if version["dead"] or version["start"] is None:
        return False
    if version["start"] > day:
        return False
    return version["end"] is None or day < version["end"]


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
    feed = _validated_feed(state.get("feed", []))
    seq = state.get("seq", 0)
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise FixtureError(errors.validation("state.seq must be a non-negative integer"))
    if feed and feed[-1]["seq"] > seq:
        seq = feed[-1]["seq"]
    sequences = state.get("sequences", {})
    if not isinstance(sequences, dict):
        raise FixtureError(errors.validation("state.sequences must be an object"))
    try:
        user_seq = int(sequences.get("user", len(users)))
        dossier_seq = int(sequences.get("dossier", len(dossiers)))
    except (TypeError, ValueError):
        raise FixtureError(errors.validation("state.sequences must hold integers")) from None
    return users, tokens, dossiers, responses, (user_seq, dossier_seq), feed, seq


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
    """The imported dossiers, keyed by domain, with their builds and history."""
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
        if not isinstance(fields, dict) or not set(SIX) <= set(fields) <= set(FIELDS):
            raise FixtureError(errors.validation("a state dossier needs its fields"))
        migrated = {name: migrated_field(name, fields.get(name), researched_at)
                    for name in FIELDS}
        dossier = Dossier(dossier_id=dossier_id, domain=domain,
                          state="failed" if state == "building" else state,
                          fields=migrated, researched_at=researched_at,
                          spent_units=spent)
        if "builds" in entry:
            dossier.builds = _validated_builds(entry.get("builds"))
            dossier.history = _validated_history(entry.get("history"), dossier.builds)
            dossier.learned = _validated_learned(entry.get("learned"))
            dossier.initialised = bool(entry.get("initialised", dossier.builds))
        else:
            # A stage-1 or stage-2 export has no executives candidates and no history:
            # the strongest recoverable state is one synthetic ready build at its
            # `researched_at`, with one `initial` entry per field that has a value.
            migrated["executives"] = {"value": None, "status": "unknown", "citations": [],
                                      "candidates": [], "true_from": None,
                                      "learned_at": None}
            dossier.fields = migrated
            dossier.builds = _synthetic_builds(dossier)
            dossier.history = _synthetic_history(dossier)
            dossier.initialised = bool(dossier.builds)
            if "history" in entry:
                _validated_history(entry.get("history"), [])
            if "learned" in entry:
                _validated_learned(entry.get("learned"))
        if dossier_id in {other.dossier_id for other in dossiers.values()}:
            raise FixtureError(errors.validation("two state dossiers share an id"))
        dossiers[domain] = dossier
    return dossiers


def _validated_builds(raw) -> list[dict]:
    """The builds a stage-3 export records."""
    if not isinstance(raw, list):
        raise FixtureError(errors.validation("a state dossier's builds must be a list"))
    builds = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixtureError(errors.validation("each state build must be an object"))
        try:
            parse_time(entry.get("time"))
        except ValueError as exc:
            raise FixtureError(errors.validation(f"build time: {exc}")) from None
        if entry.get("state") not in ("ready", "failed"):
            raise FixtureError(errors.validation("a state build's state is not recognised"))
        spent = entry.get("spent", 0)
        if not isinstance(spent, int) or isinstance(spent, bool) or spent < 0:
            raise FixtureError(errors.validation("a state build's spent must be an integer"))
        fields = entry.get("fields")
        versions = entry.get("versions") or {}
        if entry["state"] == "ready":
            if not isinstance(fields, dict) or set(fields) != set(FIELDS):
                raise FixtureError(errors.validation("a ready build needs its fields"))
            if not isinstance(versions, dict):
                raise FixtureError(errors.validation("a ready build's versions must be an object"))
        builds.append({"time": entry["time"], "state": entry["state"], "spent": spent,
                       "fields": copy.deepcopy(fields) if fields else None,
                       "versions": copy.deepcopy(versions)})
    return builds


def _validated_history(raw, builds: list[dict]) -> dict:
    """The history a stage-3 export records, or one initial entry per valued field."""
    if raw is None:
        return _synthetic_history_from_builds(builds)
    if not isinstance(raw, dict):
        raise FixtureError(errors.validation("a state dossier's history must be an object"))
    history = {}
    for name in FIELDS:
        entries = raw.get(name, [])
        if not isinstance(entries, list):
            raise FixtureError(errors.validation(f"history of {name} must be a list"))
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") not in (
                    "initial", "change", "correction"):
                raise FixtureError(errors.validation(f"a history entry of {name} is malformed"))
            try:
                parse_time(entry.get("learned_at"))
            except ValueError as exc:
                raise FixtureError(errors.validation(f"history learned_at: {exc}")) from None
        history[name] = copy.deepcopy(entries)
    return history


def _validated_learned(raw) -> dict:
    """The first-gathered times a stage-3 export records."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise FixtureError(errors.validation("a state dossier's learned map must be an object"))
    learned = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise FixtureError(errors.validation("a learned entry must be a string pair"))
        try:
            parse_time(value)
        except ValueError as exc:
            raise FixtureError(errors.validation(f"learned_at: {exc}")) from None
        learned[key] = value
    return learned


def _synthetic_builds(dossier: Dossier) -> list[dict]:
    """One ready build at `researched_at` holding the imported candidates (§3, Upgrades)."""
    if dossier.researched_at is None:
        return []
    versions: dict[str, dict] = {}
    for name in FIELDS:
        for candidate in dossier.fields[name]["candidates"]:
            url = candidate["url"]
            version = versions.setdefault(url, {"true_from": candidate.get("true_from"),
                                                "candidates": []})
            version["candidates"].append({**candidate, "field": name})
    return [{"time": dossier.researched_at, "state": "ready", "spent": dossier.spent_units,
             "fields": copy.deepcopy(dossier.fields), "versions": versions}]


def _synthetic_history(dossier: Dossier) -> dict:
    """One `initial` entry per imported field that has a value (§3, Upgrades)."""
    history = {name: [] for name in FIELDS}
    if dossier.researched_at is None:
        return history
    for name in FIELDS:
        field = dossier.fields[name]
        if field["value"] is None:
            continue
        history[name].append({"type": "initial", "value": copy.deepcopy(field["value"]),
                              "true_from": field.get("true_from"),
                              "learned_at": dossier.researched_at,
                              "citations": copy.deepcopy(field["citations"])})
    return history


def _synthetic_history_from_builds(builds: list[dict]) -> dict:
    """One `initial` entry per field the last ready build has a value for."""
    history = {name: [] for name in FIELDS}
    ready = [build for build in builds if build["state"] == "ready"]
    if not ready:
        return history
    build = ready[-1]
    for name in FIELDS:
        field = build["fields"][name]
        if field["value"] is None:
            continue
        history[name].append({"type": "initial", "value": copy.deepcopy(field["value"]),
                              "true_from": field.get("true_from"),
                              "learned_at": build["time"],
                              "citations": copy.deepcopy(field["citations"])})
    return history


def _validated_feed(raw) -> list[dict]:
    """The change feed a stage-3 export records."""
    if not isinstance(raw, list):
        raise FixtureError(errors.validation("state.feed must be a list"))
    feed = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixtureError(errors.validation("each feed entry must be an object"))
        seq = entry.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise FixtureError(errors.validation("a feed entry needs a positive seq"))
        if entry.get("type") not in ("change", "correction"):
            raise FixtureError(errors.validation("a feed entry's type is not recognised"))
        if not isinstance(entry.get("domain"), str) or entry.get("field") not in FIELDS:
            raise FixtureError(errors.validation("a feed entry needs a domain and a field"))
        try:
            parse_time(entry.get("learned_at"))
        except ValueError as exc:
            raise FixtureError(errors.validation(f"feed learned_at: {exc}")) from None
        feed.append(copy.deepcopy(entry))
    feed.sort(key=lambda entry: entry["seq"])
    return feed


def migrated_field(name: str, value, researched_at) -> dict:
    """One imported field, as a stage-3 field with candidates derived by the selection rule.

    A stage-3 or stage-2 export carries candidates already; they are re-ranked so that
    the value, status and citations always follow from them. A stage-1 export carries
    the value and the citations that supported it -- one per page whose served text
    did -- so each citation becomes one candidate, with the imported authority of
    `aggregator` and the dossier's `researched_at` as its retrieval time.
    """
    if value is None:
        return {"value": None, "status": "unknown", "citations": [], "candidates": [],
                "true_from": None, "learned_at": None}
    if not isinstance(value, dict) or not isinstance(value.get("citations", []), list):
        raise FixtureError(errors.validation(f"a state field {name} is malformed"))
    imported = value.get("candidates")
    if isinstance(imported, list) and imported:
        candidates = [_validated_candidate(name, candidate) for candidate in imported]
    else:
        candidates = candidates_module.candidates_from_citations(
            value.get("value"), value.get("citations") or [], researched_at,
            kind=candidates_module.legacy_kind(value.get("kind")),
            published_at=candidates_module.legacy_published(value.get("published_at")))
    if name == "executives":
        return candidates_module.executives_field(candidates)
    return candidates_module.field(candidates)


def _validated_candidate(name: str, candidate) -> dict:
    """One imported candidate, checked against the shape §2 and §3 give it."""
    if not isinstance(candidate, dict):
        raise FixtureError(errors.validation(f"a state candidate of {name} is malformed"))
    for key in ("url", "excerpt", "provider"):
        if not isinstance(candidate.get(key), str) or not candidate.get(key):
            raise FixtureError(
                errors.validation(f"a state candidate of {name} needs a string {key}"))
    kind = candidate.get("kind")
    if kind is not None and kind not in pages_module.KINDS:
        raise FixtureError(errors.validation(f"a state candidate of {name} has a bad kind"))
    published = candidate.get("published_at")
    if published is not None:
        if not isinstance(published, str):
            raise FixtureError(
                errors.validation(f"a state candidate of {name} has a bad published_at"))
        try:
            parse_time(published)
        except ValueError:
            raise FixtureError(
                errors.validation(f"a state candidate of {name} has a bad published_at")) \
                from None
    retrieved = candidate.get("retrieved_at")
    if retrieved is not None:
        try:
            parse_time(retrieved)
        except ValueError:
            raise FixtureError(
                errors.validation(f"a state candidate of {name} has a bad retrieved_at")) \
                from None
    true_from = candidate.get("true_from")
    if true_from is not None:
        try:
            parse_day(true_from)
        except ValueError:
            raise FixtureError(
                errors.validation(f"a state candidate of {name} has a bad true_from")) from None
    learned = candidate.get("learned_at")
    if learned is not None:
        try:
            parse_time(learned)
        except ValueError:
            raise FixtureError(
                errors.validation(f"a state candidate of {name} has a bad learned_at")) from None
    title = candidate.get("title")
    if name == "executives" and title not in TITLES:
        raise FixtureError(errors.validation("an executives candidate needs a title"))
    return {"value": candidate.get("value"), "url": candidate["url"],
            "excerpt": candidate["excerpt"], "provider": candidate["provider"],
            "kind": kind if isinstance(kind, str) else None,
            "published_at": published, "retrieved_at": retrieved,
            "true_from": true_from, "learned_at": learned or retrieved,
            "title": title if name == "executives" else None, "selected": False}


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
