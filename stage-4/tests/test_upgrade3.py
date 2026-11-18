"""Upgrades (§3): stage-1, stage-2 and stage-3 exports imported by this service."""
from __future__ import annotations

import copy
import uuid

import pytest

import fakeweb
from conftest import FIELDS, NOW, fixture, wait_ready

ACME = "acme-robotics.test"
ALL = FIELDS + ("executives", "investors")
LATER = "2026-05-01T09:00:00+00:00"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain=ACME) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain)


def as_stage_two(export: dict) -> dict:
    """`export` as the stage-2 service wrote it: current state only, no builds or history."""
    state = copy.deepcopy(export["state"])
    state.pop("feed", None)
    state.pop("seq", None)
    for dossier in state["dossiers"]:
        for name in ("builds", "history", "learned"):
            dossier.pop(name, None)
    return {"track": "dossier", "format_version": 1, "state": state}


def as_stage_three(export: dict) -> dict:
    """`export` as the stage-3 service wrote it: no investors and no stage-4 keys."""
    export = copy.deepcopy(export)
    for dossier in export["state"]["dossiers"]:
        dossier.pop("standing", None)
        _strip_field(dossier.get("fields", {}))
        (dossier.get("history") or {}).pop("investors", None)
        for build in dossier.get("builds") or []:
            if build.get("fields"):
                _strip_field(build["fields"])
            for version in (build.get("versions") or {}).values():
                for candidate in version.get("candidates") or []:
                    candidate.pop("origin", None)
    return export


def _strip_field(fields: dict) -> None:
    """Remove the investors field and the stage-4 keys from a field and its candidates."""
    fields.pop("investors", None)
    for field in fields.values():
        for key in ("origins", "corroborated", "corroborated_since"):
            field.pop(key, None)
        for candidate in field.get("candidates") or []:
            candidate.pop("origin", None)


def as_stage_one(export: dict) -> dict:
    """`export` as the stage-1 service wrote it: values and citations, no candidates."""
    export = as_stage_two(export)
    for dossier in export["state"]["dossiers"]:
        for field in dossier["fields"].values():
            field.pop("candidates", None)
            field.pop("true_from", None)
            field.pop("learned_at", None)
    return export


def test_a_stage_two_export_becomes_one_build_and_initial_history(client, world):
    """A stage-2 export imports as one ready build at `researched_at`, with initial history."""
    body = build(world)
    snapshot = as_stage_two(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    assert client.post("/_test/import", json=snapshot).status_code == 204
    restored = world.get(f"/dossiers/{ACME}").json()
    assert restored["state"] == "ready" and restored["researched_at"] == NOW
    for name in FIELDS:
        assert restored["fields"][name]["value"] == body["fields"][name]["value"], name
        assert restored["fields"][name]["candidates"], name
    assert restored["fields"]["executives"]["value"] is None
    history = world.get(f"/dossiers/{ACME}/history").json()["fields"]
    for name in FIELDS:
        entries = history[name]
        if restored["fields"][name]["value"] is None:
            assert entries == [], name
            continue
        assert [entry["type"] for entry in entries] == ["initial"], name
        assert entries[0]["learned_at"] == NOW
    assert history["executives"] == []
    assert world.get("/changes").json() == {"changes": [], "has_more": False}


def test_a_stage_two_export_answers_as_of_and_values(client, world):
    """`as_of` at or after the imported build returns it; earlier is 404."""
    build(world)
    snapshot = as_stage_two(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    client.post("/_test/import", json=snapshot)
    at = world.get(f"/dossiers/{ACME}", params={"as_of": NOW}).json()
    assert at["state"] == "ready" and at["researched_at"] == NOW
    assert at["fields"]["ceo"]["value"] == "Dana Ruiz"
    early = world.get(f"/dossiers/{ACME}", params={"as_of": "2026-02-01T00:00:00+00:00"})
    assert early.status_code == 404
    values = world.get(f"/dossiers/{ACME}/values", params={"true_at": "2026-02-01"}).json()
    assert values["fields"]["ceo"]["value"] == "Dana Ruiz"


def test_a_stage_one_export_becomes_candidates_with_retrieval_times(client, world):
    """A stage-1 export's citations become candidates, learned at their retrieval time."""
    build(world)
    snapshot = as_stage_one(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    assert client.post("/_test/import", json=snapshot).status_code == 204
    restored = world.get(f"/dossiers/{ACME}").json()
    for name in FIELDS:
        field = restored["fields"][name]
        if field["value"] is None:
            assert field["candidates"] == [], name
            continue
        assert field["candidates"], name
        for candidate in field["candidates"]:
            assert candidate["kind"] is None and candidate["published_at"] is None, name
            assert candidate["learned_at"] == NOW and candidate["true_from"] is None, name
    history = world.get(f"/dossiers/{ACME}/history").json()["fields"]
    assert all(len(entries) <= 1 for entries in history.values())


def test_the_first_build_after_an_import_compares_with_the_imported_values(client, world):
    """A build after import adds a `change` where the value moved, not an `initial`."""
    build(world)
    snapshot = as_stage_two(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    client.post("/_test/import", json=snapshot)
    fakeweb.EPOCH = 1
    client.post("/_test/clock", json={"now": LATER})
    assert world.post(f"/dossiers/{ACME}/refresh",
                      headers={"Idempotency-Key": key()}).status_code == 202
    after = wait_ready(world, ACME)
    assert after["fields"]["hq_city"]["value"] == "Porto"
    history = world.get(f"/dossiers/{ACME}/history").json()["fields"]["hq_city"]
    assert [entry["type"] for entry in history] == ["initial", "change"]
    feed = world.get("/changes").json()["changes"]
    assert [(entry["field"], entry["type"], entry["from"], entry["to"])
            for entry in feed] == [
        ("hq_city", "change", "Lisbon", "Porto"),
        ("executives", "change", None,
         [{"title": "cfo", "name": "Dana Ruiz"}, {"title": "cto", "name": "Ben Okafor"}])]


def test_a_stage_three_export_round_trips_everything(client, world):
    """Builds, history, the feed and the sequence survive a stage-3 import unchanged."""
    build(world)
    fakeweb.EPOCH = 1
    client.post("/_test/clock", json={"now": LATER})
    assert world.post(f"/dossiers/{ACME}/refresh",
                      headers={"Idempotency-Key": key()}).status_code == 202
    wait_ready(world, ACME)
    before = {
        "dossier": world.get(f"/dossiers/{ACME}").json(),
        "as_of": world.get(f"/dossiers/{ACME}",
                           params={"as_of": "2026-04-01T09:00:00+00:00"}).json(),
        "values": world.get(f"/dossiers/{ACME}/values",
                            params={"true_at": "2026-02-01",
                                    "as_of": LATER}).json(),
        "history": world.get(f"/dossiers/{ACME}/history").json(),
        "changes": world.get("/changes").json(),
    }
    snapshot = client.get("/_test/export").json()
    client.post("/_test/reset", json=fixture())
    assert client.post("/_test/import", json=snapshot).status_code == 204
    assert world.get(f"/dossiers/{ACME}").json() == before["dossier"]
    assert world.get(f"/dossiers/{ACME}",
                     params={"as_of": "2026-04-01T09:00:00+00:00"}).json() == before["as_of"]
    assert world.get(f"/dossiers/{ACME}/values",
                     params={"true_at": "2026-02-01",
                             "as_of": LATER}).json() == before["values"]
    assert world.get(f"/dossiers/{ACME}/history").json() == before["history"]
    assert world.get("/changes").json() == before["changes"]
    # a later build continues the feed's sequence
    fakeweb.EPOCH = 3
    client.post("/_test/clock", json={"now": "2026-06-01T09:00:00+00:00"})
    assert world.post(f"/dossiers/{ACME}/refresh",
                      headers={"Idempotency-Key": key()}).status_code == 202
    wait_ready(world, ACME)
    seqs = [entry["seq"] for entry in world.get("/changes").json()["changes"]]
    assert seqs == sorted(seqs) and seqs[-1] > before["changes"]["changes"][-1]["seq"]


def test_import_keeps_tokens_and_replays(client, world):
    """Accounts, tokens and completed idempotent responses survive an import."""
    headers = {"Idempotency-Key": "upgrade-key"}
    first = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    wait_ready(world, ACME)
    snapshot = as_stage_two(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    client.post("/_test/import", json=snapshot)
    assert world.get("/dossiers").status_code == 200
    replay = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()


def test_an_imported_dossier_without_a_build_answers_no_as_of(client, world):
    """A dossier whose `researched_at` is null answers every `as_of` with 404."""
    export = client.get("/_test/export").json()
    export["state"]["dossiers"] = [{
        "dossier_id": "d_1", "domain": ACME, "state": "failed", "researched_at": None,
        "spent_units": 0,
        "fields": {name: {"value": None, "status": "unknown", "citations": [],
                          "candidates": [], "true_from": None, "learned_at": None}
                   for name in ALL}}]
    assert client.post("/_test/import", json=export).status_code == 204
    response = world.get(f"/dossiers/{ACME}", params={"as_of": NOW})
    assert response.status_code == 404
    assert world.get(f"/dossiers/{ACME}").json()["state"] == "failed"


def test_a_stage_three_export_leaves_investors_unknown_until_a_build(world, client):
    """A stage-3 export has no investors: no value, no history, and each kept build unknown."""
    domain = "origin-works.test"
    build(world, domain)
    export = as_stage_three(client.get("/_test/export").json())
    client.post("/_test/reset", json=fixture())
    assert client.post("/_test/import", json=export).status_code == 204
    imported = world.get(f"/dossiers/{domain}").json()
    investors = imported["fields"]["investors"]
    assert investors["value"] is None and investors["status"] == "unknown"
    assert investors["candidates"] == [] and investors["origins"] == []
    history = world.get(f"/dossiers/{domain}/history").json()["fields"]["investors"]
    assert history == []
    as_of = world.get(f"/dossiers/{domain}",
                      params={"as_of": imported["researched_at"]}).json()
    assert as_of["fields"]["investors"]["value"] is None
    world.post("/_test/clock", json={"now": LATER})
    world.post(f"/dossiers/{domain}/refresh", headers={"Idempotency-Key": key()})
    after = wait_ready(world, domain)
    assert after["fields"]["investors"]["value"] == ["Bluebird Capital", "General Atlantic",
                                                    "Northwind Ventures"]
    entries = world.get(f"/dossiers/{domain}/history").json()["fields"]["investors"]
    assert [entry["type"] for entry in entries] == ["change"]


def test_a_stage_three_import_rebuilds_standing_origins_and_their_time(world, client):
    """Origins the export lacked become each URL's host, and corroboration is replayed."""
    domain = "origin-works.test"
    registry = "https://registry.test/origin-works"
    original = fakeweb.PAGES[domain].pop(registry)
    try:
        build(world, domain)
        fakeweb.PAGES[domain][registry] = original
        world.post("/_test/clock", json={"now": LATER})
        world.post(f"/dossiers/{domain}/refresh", headers={"Idempotency-Key": key()})
        second = wait_ready(world, domain)
        assert second["researched_at"] == LATER
        snapshot = as_stage_three(client.get("/_test/export").json())
        client.post("/_test/reset", json=fixture())
        assert client.post("/_test/import", json=snapshot).status_code == 204
        legal = world.get(f"/dossiers/{domain}").json()["fields"]["legal_name"]
        assert legal["origins"] == [domain, "registry.test"]
        assert legal["corroborated"] is True
        assert legal["corroborated_since"] == LATER
    finally:
        fakeweb.PAGES[domain][registry] = original


@pytest.mark.parametrize("bad", [
    {"builds": "nope"},
    {"history": {"ceo": [{"type": "bogus"}]}},
    {"feed": [{"seq": 0}]},
])
def test_a_malformed_stage_three_state_is_refused(client, bad):
    """A stage-3 export whose builds, history or feed are malformed is 422."""
    export = client.get("/_test/export").json()
    export["state"]["dossiers"] = [{
        "dossier_id": "d_1", "domain": ACME, "state": "ready", "researched_at": NOW,
        "spent_units": 1,
        "fields": {name: {"value": None, "status": "unknown", "citations": [],
                          "candidates": [], "true_from": None, "learned_at": None}
                   for name in ALL},
        **bad}]
    if "feed" in bad:
        export["state"]["feed"] = bad["feed"]
        export["state"]["dossiers"][0].pop("feed", None)
    response = client.post("/_test/import", json=export)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
