"""Fixtures for the service's own tests: a real HTTP server over a fake provider."""
from __future__ import annotations

import contextlib
import pathlib
import socket
import sys
import threading
import time

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import providers, server as app_server  # noqa: E402

import fakeweb  # noqa: E402  (tests/ is on sys.path when pytest runs here)


def free_port() -> int:
    """A loopback port that is free right now."""
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def service():
    """A running service whose builds answer from `fakeweb`; yields its base URL.

    Session-scoped: starting a server costs a second on a slow resolver, and every
    check that cares about state resets it first. The fake's own state is cleared
    before each check.
    """
    original = providers.Calls
    providers.Calls = fakeweb.FakeCalls
    port = free_port()
    application = app_server.Application()
    handler = type("BoundHandler", (app_server.Handler,), {"app": application})
    httpd = app_server.Server(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    try:
        yield base_url
    finally:
        providers.Calls = original
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def fake_reset():
    """Clear the fake's faults, delay and call log before each check."""
    fakeweb.reset()


@pytest.fixture
def client(service):
    """An unauthenticated client for the running service."""
    with httpx.Client(base_url=service, timeout=10) as http:
        yield http


ALICE = {"id": "u_alice", "email": "alice@example.com", "password": "correct horse",
         "display_name": "Alice"}
BOB = {"id": "u_bob", "email": "bob@example.com", "password": "correct horse",
       "display_name": "Bob"}
NOW = "2026-03-01T09:00:00+00:00"
FIELDS = ("legal_name", "hq_city", "ceo", "founded_year", "headcount_range", "funding_total")


def fixture() -> dict:
    """The reset body the organizers' fixture format uses."""
    return {"now": NOW, "users": [ALICE, BOB]}


@pytest.fixture
def world(client):
    """A reset service with Alice signed in."""
    client.post("/_test/reset", json=fixture())
    token = client.post("/auth/login", json={"email": ALICE["email"],
                                             "password": ALICE["password"]}).json()["token"]
    with httpx.Client(base_url=str(client.base_url), timeout=10,
                      headers={"Authorization": f"Bearer {token}"}) as authed:
        yield authed


def wait_ready(client, domain: str, seconds: float = 10.0) -> dict:
    """Poll a dossier until its build has ended, and return its body."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = client.get(f"/dossiers/{domain}").json()
        if body["state"] != "building":
            return body
        time.sleep(0.02)
    raise AssertionError(f"{domain} still building after {seconds}s")
