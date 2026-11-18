"""Concurrency (§3): one build per dossier, and no change twice or missing from the feed."""
from __future__ import annotations

import concurrent.futures
import uuid

import fakeweb
from conftest import FIELDS, wait_ready

ACME = "acme-robotics.test"
LATER = "2026-05-01T09:00:00+00:00"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain=ACME) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain)


def test_concurrent_refreshes_run_one_build_and_add_one_entry(world):
    """Ten simultaneous refreshes of one dossier add each history and feed entry once."""
    build(world)
    fakeweb.EPOCH = 1
    world.post("/_test/clock", json={"now": LATER})
    auth = world.headers["Authorization"]

    def refresh(_):
        return world.post(f"/dossiers/{ACME}/refresh",
                          headers={"Idempotency-Key": key()})

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(refresh, range(10)))
    assert all(response.status_code == 202 for response in responses)
    body = wait_ready(world, ACME)
    assert body["fields"]["hq_city"]["value"] == "Porto"
    history = world.get(f"/dossiers/{ACME}/history").json()["fields"]["hq_city"]
    assert [entry["type"] for entry in history] == ["initial", "change"]
    feed = world.get("/changes").json()["changes"]
    assert [(entry["field"], entry["type"]) for entry in feed] == [("hq_city", "change")]
    assert auth


def test_concurrent_reads_during_a_build_are_consistent(world):
    """Reads while a build runs see either the old or the new state, never a torn one."""
    first = build(world)
    fakeweb.EPOCH = 1
    world.post("/_test/clock", json={"now": LATER})
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})

    def read(_):
        return world.get(f"/dossiers/{ACME}").json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        bodies = list(pool.map(read, range(20)))
    for body in bodies:
        assert set(body["fields"]) == set(FIELDS) | {"executives"}
        assert body["state"] in ("building", "ready")
        if body["state"] == "ready":
            assert body["fields"]["hq_city"]["value"] in ("Lisbon", "Porto")
    final = wait_ready(world, ACME)
    assert final["fields"]["hq_city"]["value"] == "Porto"
    assert first["fields"]["hq_city"]["value"] == "Lisbon"


def test_concurrent_builds_of_different_dossiers_keep_their_own_history(world):
    """Two dossiers building at once each get their own entries and sequence numbers."""
    build(world, ACME)
    build(world, "rival-works.test")
    fakeweb.EPOCH = 1
    world.post("/_test/clock", json={"now": LATER})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(world.post, f"/dossiers/{domain}/refresh",
                               headers={"Idempotency-Key": key()})
                   for domain in (ACME, "rival-works.test")]
        assert all(future.result().status_code == 202 for future in futures)
    wait_ready(world, ACME)
    wait_ready(world, "rival-works.test")
    feed = world.get("/changes").json()["changes"]
    seqs = [entry["seq"] for entry in feed]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert {entry["domain"] for entry in feed} <= {ACME, "rival-works.test"}
