"""Upgrading a Stage 1 export (§2, "Existing clients after an upgrade").

A Stage 1 export keeps each field's value and the citations that supported it,
not the gathered page texts. Importing it must produce one candidate per cited
page, keep accounts and tokens, keep idempotent responses replayable, and let
the next ready build replace the imported candidates.
"""
from __future__ import annotations

import uuid

import pytest

from app import candidates as rule
from conftest import FIELDS, NOW, fixture, wait_ready

ACME = "acme-robotics.test"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain=ACME) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain)


def as_stage_one(export: dict) -> dict:
    """`export` as the Stage 1 service would have written it: no `candidates` anywhere."""
    state = export["state"]
    for dossier in state["dossiers"]:
        for field in dossier["fields"].values():
            field.pop("candidates", None)
    return export


def test_a_stage_one_export_becomes_candidates(client, world):
    """Every citation of a Stage 1 field becomes one candidate, with null kind."""
    body = build(world)
    snapshot = as_stage_one(client.get("/_test/export").json())
    assert all("candidates" not in field
               for dossier in snapshot["state"]["dossiers"]
               for field in dossier["fields"].values())
    client.post("/_test/reset", json=fixture())
    assert client.post("/_test/import", json=snapshot).status_code == 204
    imported = world.get(f"/dossiers/{ACME}")
    assert imported.status_code == 200
    restored = imported.json()
    for name in FIELDS:
        field = restored["fields"][name]
        original = body["fields"][name]
        assert field["value"] == original["value"], name
        assert field["status"] in ("known", "contested", "unknown"), name
        if original["value"] is None:
            assert field == {"value": None, "status": "unknown", "citations": [],
                             "candidates": []}, name
            continue
        assert field["candidates"], name
        assert {candidate["value"] for candidate in field["candidates"]} == {original["value"]}
        for candidate in field["candidates"]:
            assert candidate["kind"] is None, (name, candidate)
            assert candidate["published_at"] is None, (name, candidate)
            assert candidate["retrieved_at"] == body["researched_at"], (name, candidate)
            assert candidate["excerpt"] and candidate["url"], (name, candidate)
            assert candidate["provider"] == "nimble", (name, candidate)
        assert len(field["candidates"]) == len(original["citations"]), name
        assert field["status"] == "known" and field["value"] == original["value"], name
        assert field["citations"] == [
            {"url": candidate["url"], "excerpt": candidate["excerpt"],
             "provider": candidate["provider"]} for candidate in field["candidates"]], name


def test_tokens_and_replays_survive_the_upgrade(client, world):
    """A browser signed in before the upgrade stays signed in, and a retry replays."""
    headers = {"Idempotency-Key": "upgrade-key"}
    first = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert first.status_code == 202
    wait_ready(world, ACME)
    snapshot = as_stage_one(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    assert client.post("/_test/import", json=snapshot).status_code == 204
    assert world.get(f"/dossiers/{ACME}").status_code == 200
    replay = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert world.get("/dossiers").status_code == 200


def test_a_lost_write_stays_retryable_after_the_upgrade(client, world):
    """A request whose response was lost before export replays after import."""
    headers = {"Idempotency-Key": "lost-then-imported"}
    started = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert started.status_code == 202
    snapshot = as_stage_one(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    client.post("/_test/import", json=snapshot)
    retried = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert retried.status_code == 200 and retried.json() == started.json()
    assert len(world.get("/dossiers").json()["dossiers"]) == 1


def test_the_next_ready_build_replaces_imported_candidates(client, world):
    """A refresh's completed build supplies candidates with kinds and fresh retrieval times."""
    build(world)
    snapshot = as_stage_one(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    client.post("/_test/import", json=snapshot)
    assert world.post(f"/dossiers/{ACME}/refresh",
                      headers={"Idempotency-Key": key()}).status_code == 202
    rebuilt = wait_ready(world, ACME)
    assert rebuilt["state"] == "ready"
    kinds = {candidate["kind"] for name in FIELDS
             for candidate in rebuilt["fields"][name]["candidates"]}
    assert kinds <= {"own", "filing", "news", "aggregator"} and "own" in kinds
    assert all(candidate["retrieved_at"] == rebuilt["researched_at"]
               for name in FIELDS
               for candidate in rebuilt["fields"][name]["candidates"])
    assert rebuilt["fields"]["ceo"]["status"] == "contested"


def test_a_stage_two_export_round_trips_its_candidates(client, world):
    """Exporting and importing a Stage 2 state keeps kinds, times and ranking."""
    body = build(world)
    snapshot = client.get("/_test/export").json()
    client.post("/_test/reset", json=fixture())
    assert client.post("/_test/import", json=snapshot).status_code == 204
    restored = world.get(f"/dossiers/{ACME}").json()
    assert restored["fields"] == body["fields"]
    for name in FIELDS:
        field = restored["fields"][name]
        if field["candidates"]:
            assert field["candidates"] == sorted(field["candidates"], key=rule.rank_key)


def test_import_rejects_a_malformed_candidate(client):
    """A candidate of the wrong shape makes the whole import a validation failure."""
    export = client.get("/_test/export").json()
    export["state"]["dossiers"] = [{
        "dossier_id": "d_1", "domain": "acme-robotics.test", "state": "ready",
        "researched_at": NOW, "spent_units": 1,
        "fields": {name: {"value": None, "status": "unknown", "citations": [],
                          "candidates": [{"value": 1}]} if name == "ceo"
                   else {"value": None, "status": "unknown", "citations": [],
                         "candidates": []}
                   for name in FIELDS}}]
    response = client.post("/_test/import", json=export)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


@pytest.mark.parametrize("bad", [{"kind": "blog"}, {"published_at": "yesterday"}])
def test_import_rejects_a_bad_candidate_fact(client, bad):
    """A candidate's kind and publication time are checked on import."""
    export = client.get("/_test/export").json()
    candidate = {"value": "Dana Ruiz", "url": "https://acme-robotics.test/team",
                 "excerpt": "Dana Ruiz has led Acme Robotics.", "provider": "nimble",
                 "kind": "own", "published_at": None, "retrieved_at": NOW, "selected": True}
    candidate.update(bad)
    export["state"]["dossiers"] = [{
        "dossier_id": "d_1", "domain": "acme-robotics.test", "state": "ready",
        "researched_at": NOW, "spent_units": 1,
        "fields": {name: {"value": candidate["value"] if name == "ceo" else None,
                          "status": "known" if name == "ceo" else "unknown",
                          "citations": [], "candidates": [candidate] if name == "ceo" else []}
                   for name in FIELDS}}]
    response = client.post("/_test/import", json=export)
    assert response.status_code == 422
