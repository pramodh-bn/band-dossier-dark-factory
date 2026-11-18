"""Batches (§4, Batches): validation, budgets, ownership, idempotency and concurrency."""
from __future__ import annotations

import concurrent.futures
import time
import uuid

import httpx

import fakeweb
from conftest import BOB, drain, wait_ready

ACME = "acme-robotics.test"
SECOND = "second-company.test"
RIVAL = "rival-works.test"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def bob_client(world) -> httpx.Client:
    """Bob's authenticated client on the same service."""
    login = world.post("/auth/login", json={"email": BOB["email"],
                                            "password": BOB["password"]})
    return httpx.Client(base_url=str(world.base_url), timeout=20,
                        headers={"Authorization": f"Bearer {login.json()['token']}"})


def post_batch(client, domains, budget, idem=None):
    """POST /batches with a fresh key unless one is given."""
    return client.post("/batches", json={"domains": domains, "budget_units": budget},
                       headers={"Idempotency-Key": idem or key()})


def finish(client, batch_id: str) -> dict:
    """Poll a batch until it is done (or the test times out)."""
    for _ in range(300):
        body = client.get(f"/batches/{batch_id}").json()
        if body["state"] == "done":
            return body
        time.sleep(0.05)
    raise AssertionError(f"batch {batch_id} never finished")


def test_a_batch_researches_its_companies_in_order(world):
    """A fresh batch builds each new dossier and reaches `done`."""
    created = post_batch(world, [RIVAL, SECOND], 200)
    assert created.status_code == 202, created.text
    body = created.json()
    assert [entry["domain"] for entry in body["dossiers"]] == [RIVAL, SECOND]
    assert [entry["state"] for entry in body["dossiers"]] == ["building", "building"]
    assert body["state"] == "running" and body["budget_units"] == 200
    done = finish(world, body["batch_id"])
    assert [entry["state"] for entry in done["dossiers"]] == ["ready", "ready"]
    assert 0 <= done["spent_units"] <= 200
    assert done["spent_units"] == fakeweb.spent()
    dossiers = [world.get(f"/dossiers/{domain}").json() for domain in (RIVAL, SECOND)]
    assert sum(dossier["spent_units"] for dossier in dossiers) == done["spent_units"]


def test_an_existing_dossier_is_not_rebuilt(world):
    """A domain that already has a dossier costs the batch nothing and starts no build."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    before = wait_ready(world, ACME)
    calls = len(fakeweb.LOG)
    spent = before["spent_units"]
    created = post_batch(world, [ACME, SECOND], 100).json()
    assert created["dossiers"][0]["state"] == "ready"
    done = finish(world, created["batch_id"])
    after = world.get(f"/dossiers/{ACME}").json()
    assert after["spent_units"] == spent
    assert after["researched_at"] == before["researched_at"]
    assert not any(entry["method"] == "search" and entry.get("key") == ACME
                   for entry in fakeweb.LOG[calls:])
    assert done["spent_units"] == fakeweb.spent() - sum(
        entry["cost"] for entry in fakeweb.LOG[:calls])


def test_a_batch_never_spends_past_its_budget(world):
    """Three builds share a small budget, and every unit is attributed to the batch."""
    created = post_batch(world, [RIVAL, SECOND, "origin-works.test"], 12).json()
    done = finish(world, created["batch_id"])
    assert done["spent_units"] <= 12
    assert done["spent_units"] == fakeweb.spent() <= 12
    for entry in done["dossiers"]:
        dossier = world.get(f"/dossiers/{entry['domain']}").json()
        assert dossier["state"] in ("ready", "failed")
        assert dossier["spent_units"] <= 80


def test_another_users_batch_is_not_found(world):
    """A batch belongs to its creator."""
    created = post_batch(world, [SECOND], 100).json()
    bob = bob_client(world)
    try:
        assert bob.get(f"/batches/{created['batch_id']}").status_code == 404
        drain(world)
    finally:
        bob.close()


def test_an_unknown_batch_is_not_found(world):
    """An id no batch holds is 404."""
    assert world.get("/batches/b_nope").status_code == 404


def test_batches_need_a_key_and_valid_input(world):
    """The batch table's validation rows, and the missing-key error."""
    assert world.post("/batches", json={"domains": [SECOND], "budget_units": 10}).status_code == 400
    cases = [
        {"budget_units": 10},
        {"domains": [], "budget_units": 10},
        {"domains": [SECOND] * 51, "budget_units": 10},
        {"domains": ["not a domain"], "budget_units": 10},
        {"domains": [SECOND, "WWW." + SECOND.upper()], "budget_units": 10},
        {"domains": [SECOND]},
        {"domains": [SECOND], "budget_units": "10"},
        {"domains": [SECOND], "budget_units": True},
        {"domains": [SECOND], "budget_units": 1.5},
        {"domains": [SECOND], "budget_units": 0},
        {"domains": [SECOND], "budget_units": 100_001},
    ]
    for body in cases:
        response = world.post("/batches", json=body,
                              headers={"Idempotency-Key": key()})
        assert (response.status_code, response.json()["error"]["code"]) == \
            (422, "validation_failed"), (body, response.text)


def test_a_replay_answers_the_original_body_and_starts_nothing(world):
    """The same key and body replays the creation body; a different body is a 409."""
    idem = key()
    first = post_batch(world, [SECOND], 100, idem)
    assert first.status_code == 202
    replay = post_batch(world, [SECOND], 100, idem)
    assert replay.status_code == 200 and replay.json() == first.json()
    reuse = post_batch(world, [SECOND], 101, idem)
    assert reuse.status_code == 409
    finish(world, first.json()["batch_id"])
    again = post_batch(world, [SECOND], 100, idem)
    assert again.status_code == 200 and again.json() == first.json()


def test_concurrent_batches_create_one_dossier_and_one_build(world):
    """Two batches naming a new domain start one build between them."""
    def post(_):
        return post_batch(world, ["rival-works.test"], 100)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(post, range(2)))
    assert all(response.status_code == 202 for response in responses)
    ids = {response.json()["batch_id"] for response in responses}
    assert len(ids) == 2
    for batch_id in ids:
        finish(world, batch_id)
    openings = [entry for entry in fakeweb.LOG if entry["method"] == "search"
                and entry.get("key") == "rival-works.test"]
    assert len(openings) == 1


def test_a_batch_with_every_provider_failing_is_not_a_server_error(world):
    """Builds fail cleanly and the batch is done, without a 5xx."""
    fakeweb.FAULTS.update({"search", "extract", "research"})
    response = post_batch(world, [SECOND], 10)
    assert response.status_code == 202
    done = finish(world, response.json()["batch_id"])
    assert done["state"] == "done"
    assert [entry["state"] for entry in done["dossiers"]] == ["failed"]
    assert done["spent_units"] == 0
