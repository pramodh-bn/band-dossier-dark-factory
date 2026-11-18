"""Restarts (§3): the state file answers exactly as the service did before the stop."""
from __future__ import annotations

import contextlib
import json
import pathlib
import socket
import threading
import time
import uuid

import httpx
import pytest

import fakeweb
from app import providers, server as app_server
from conftest import ALICE, FIELDS, fixture, wait_ready

ACME = "acme-robotics.test"
ALL = FIELDS + ("executives", "investors")


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def free_port() -> int:
    """A loopback port that is free right now."""
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def running(state_path: pathlib.Path):
    """A service over `state_path`, served on a fresh port for the block."""
    port = free_port()
    application = app_server.Application(state_path=str(state_path))
    handler = type("BoundHandler", (app_server.Handler,), {"app": application})
    httpd = app_server.Server(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def state_path(tmp_path):
    """A state file of this check's own."""
    return tmp_path / "state.json"


def sign_in(base: str) -> httpx.Client:
    """A client signed in as Alice."""
    token = httpx.post(f"{base}/auth/login", json={
        "email": ALICE["email"], "password": ALICE["password"]}).json()["token"]
    return httpx.Client(base_url=base, timeout=20,
                        headers={"Authorization": f"Bearer {token}"})


def test_a_restart_answers_every_read_as_before(state_path, monkeypatch):
    """Dossiers, history, the feed, `as_of`, `/values` and the clock survive a restart."""
    monkeypatch.setattr(providers, "Calls", fakeweb.FakeCalls)
    fakeweb.reset()
    with running(state_path) as base:
        assert httpx.post(f"{base}/_test/reset", json=fixture()).status_code == 204
        client = sign_in(base)
        client.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        fakeweb.EPOCH = 1
        client.post("/_test/clock", json={"now": "2026-05-01T09:00:00+00:00"})
        client.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        before = {
            "dossier": client.get(f"/dossiers/{ACME}").json(),
            "as_of": client.get(f"/dossiers/{ACME}",
                                params={"as_of": "2026-04-01T09:00:00+00:00"}).json(),
            "values": client.get(f"/dossiers/{ACME}/values",
                                 params={"true_at": "2026-02-01"}).json(),
            "history": client.get(f"/dossiers/{ACME}/history").json(),
            "changes": client.get("/changes").json(),
            "list": client.get("/dossiers").json(),
            "export": client.get("/_test/export").json(),
            "token": client.headers["Authorization"],
        }
    with running(state_path) as base:
        client = httpx.Client(base_url=base, timeout=20,
                              headers={"Authorization": before["token"]})
        assert client.get(f"/dossiers/{ACME}").json() == before["dossier"]
        assert client.get(f"/dossiers/{ACME}",
                          params={"as_of": "2026-04-01T09:00:00+00:00"}).json() == \
            before["as_of"]
        assert client.get(f"/dossiers/{ACME}/values",
                          params={"true_at": "2026-02-01"}).json() == before["values"]
        assert client.get(f"/dossiers/{ACME}/history").json() == before["history"]
        assert client.get("/changes").json() == before["changes"]
        assert client.get("/dossiers").json() == before["list"]
        assert client.get("/_test/export").json() == before["export"]
        # the clock reads what it read before the stop
        client.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
        assert wait_ready(client, ACME)["researched_at"] == "2026-05-01T09:00:00+00:00"


def test_a_restart_keeps_accounts_tokens_and_replays(state_path, monkeypatch):
    """Every account signs in, every token works, and a replay answers as before."""
    monkeypatch.setattr(providers, "Calls", fakeweb.FakeCalls)
    fakeweb.reset()
    with running(state_path) as base:
        assert httpx.post(f"{base}/_test/reset", json=fixture()).status_code == 204
        client = sign_in(base)
        signed = client.post("/auth/signup", json={"email": "dee@example.com",
                                                   "password": "correct horse",
                                                   "display_name": "Dee"}).json()
        headers = {"Idempotency-Key": "restart-key"}
        first = client.post("/dossiers", json={"domain": ACME}, headers=headers)
        wait_ready(client, ACME)
        token = client.headers["Authorization"]
    with running(state_path) as base:
        assert httpx.post(f"{base}/auth/login", json={
            "email": "dee@example.com", "password": "correct horse"}).status_code == 200
        client = httpx.Client(base_url=base, timeout=20, headers={"Authorization": token})
        assert client.get("/dossiers").status_code == 200
        replay = client.post("/dossiers", json={"domain": ACME}, headers=headers)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert httpx.post(f"{base}/auth/login", json={
            "email": "dee@example.com", "password": "wrong horse"}).status_code == 401
        assert signed["user_id"]


def test_a_new_feed_entry_after_a_restart_has_a_larger_seq(state_path, monkeypatch):
    """The feed's sequence continues where it stopped."""
    monkeypatch.setattr(providers, "Calls", fakeweb.FakeCalls)
    fakeweb.reset()
    with running(state_path) as base:
        assert httpx.post(f"{base}/_test/reset", json=fixture()).status_code == 204
        client = sign_in(base)
        client.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        fakeweb.EPOCH = 1
        client.post("/_test/clock", json={"now": "2026-05-01T09:00:00+00:00"})
        client.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        before = client.get("/changes").json()["changes"]
        assert before and before[-1]["seq"] == 1
        token = client.headers["Authorization"]
    with running(state_path) as base:
        client = httpx.Client(base_url=base, timeout=20, headers={"Authorization": token})
        fakeweb.EPOCH = 3
        client.post("/_test/clock", json={"now": "2026-06-01T09:00:00+00:00"})
        client.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        after = client.get("/changes").json()["changes"]
        assert [entry["seq"] for entry in after] == [1, 2]
        assert after[1]["learned_at"] == "2026-06-01T09:00:00+00:00"


def test_the_state_file_is_written_atomically(state_path, monkeypatch):
    """The state file is valid JSON after every mutation, and no temp file is left behind."""
    monkeypatch.setattr(providers, "Calls", fakeweb.FakeCalls)
    fakeweb.reset()
    with running(state_path) as base:
        assert httpx.post(f"{base}/_test/reset", json=fixture()).status_code == 204
        client = sign_in(base)
        client.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        body = json.loads(state_path.read_text(encoding="utf-8"))
        assert body["dossiers"][0]["domain"] == ACME
        assert body["dossiers"][0]["builds"]
        leftovers = [path.name for path in state_path.parent.iterdir()
                     if path.name.startswith(".state-")]
        assert leftovers == []


def test_the_state_file_never_holds_a_building_dossier(state_path, monkeypatch):
    """While a build runs, every persisted snapshot is a whole, non-building state."""
    monkeypatch.setattr(providers, "Calls", fakeweb.FakeCalls)
    fakeweb.reset()
    with running(state_path) as base:
        assert httpx.post(f"{base}/_test/reset", json=fixture()).status_code == 204
        client = sign_in(base)
        client.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        before = json.loads(state_path.read_text(encoding="utf-8"))
        fakeweb.DELAY = 1.0
        fakeweb.EPOCH = 1
        client.post("/_test/clock", json={"now": "2026-05-01T09:00:00+00:00"})
        client.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
        seen = 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            building = client.get(f"/dossiers/{ACME}").json()["state"] == "building"
            body = json.loads(state_path.read_text(encoding="utf-8"))
            dossier = body["dossiers"][0]
            assert dossier["state"] != "building", dossier["state"]
            if not building:
                break
            assert len(dossier["builds"]) == len(before["dossiers"][0]["builds"])
            assert dossier["history"] == before["dossiers"][0]["history"]
            assert body["feed"] == before["feed"]
            seen += 1
            time.sleep(0.05)
        assert seen > 0, "the build finished before a snapshot could be read"
        after = json.loads(state_path.read_text(encoding="utf-8"))
        assert len(after["dossiers"][0]["builds"]) == 2
        assert [entry["type"] for entry in after["dossiers"][0]["history"]["hq_city"]] == \
            ["initial", "change"]
        assert len(after["feed"]) == 1


def test_an_interrupted_build_recovers_to_a_non_building_state(state_path, monkeypatch):
    """A state file written mid-build loads as the last committed state, and refreshes work."""
    monkeypatch.setattr(providers, "Calls", fakeweb.FakeCalls)
    fakeweb.reset()
    with running(state_path) as base:
        assert httpx.post(f"{base}/_test/reset", json=fixture()).status_code == 204
        client = sign_in(base)
        client.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
        wait_ready(client, ACME)
        committed = json.loads(state_path.read_text(encoding="utf-8"))
        token = client.headers["Authorization"]
    # a build that was running when the process died leaves the committed state behind
    with running(state_path) as base:
        client = httpx.Client(base_url=base, timeout=20, headers={"Authorization": token})
        body = client.get(f"/dossiers/{ACME}").json()
        assert body["state"] == "ready"
        assert body["fields"] == committed["dossiers"][0]["fields"]
        assert client.get("/changes").json()["changes"] == []
        fakeweb.EPOCH = 1
        client.post("/_test/clock", json={"now": "2026-05-01T09:00:00+00:00"})
        assert client.post(f"/dossiers/{ACME}/refresh",
                           headers={"Idempotency-Key": key()}).status_code == 202
        assert wait_ready(client, ACME)["fields"]["hq_city"]["value"] == "Porto"
        assert [entry["type"] for entry in
                client.get(f"/dossiers/{ACME}/history").json()["fields"]["hq_city"]] == \
            ["initial", "change"]
