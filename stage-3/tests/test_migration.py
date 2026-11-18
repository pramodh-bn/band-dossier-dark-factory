"""Upgrading a Stage 1 export (§2, "Existing clients after an upgrade").

A Stage 1 export keeps each field's value and the citations that supported it,
not the gathered page texts. The strongest deterministic migration is therefore
one candidate per stored citation -- exact value, URL, excerpt and provider --
with the dossier's `researched_at` as the retrieval time and any kind or
publication time the legacy state records; everything else stays null, because
filling it in would need a provider call, which import may not make. Accounts,
tokens, ids and stored idempotent responses survive, and the next ready build
replaces the imported candidates.

The unavoidable limitation is stated as a test too: a legacy export cannot grow
a candidate its citations do not name. Evidence the Stage 1 service gathered but
never cited is not recoverable from the export at all.
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


def empty_field() -> dict:
    """An unknown field as a legacy export writes it."""
    return {"value": None, "status": "unknown", "citations": []}


def legacy_export(client, fields: dict, dossier_id: str = "d_legacy") -> dict:
    """A valid export whose one dossier carries `fields`, in the Stage 1 shape."""
    export = client.get("/_test/export").json()
    export["state"]["dossiers"] = [{
        "dossier_id": dossier_id, "domain": ACME, "state": "ready",
        "researched_at": NOW, "spent_units": 47,
        "fields": {**{name: empty_field() for name in FIELDS}, **fields}}]
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
                             "candidates": [], "true_from": None,
                             "learned_at": None}, name
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


def test_every_stored_citation_becomes_one_candidate(client, world):
    """All citations a legacy field stores migrate exactly, in rank order, with one build."""
    legal = [
        {"url": "https://acme-robotics.test/legal",
         "excerpt": "Acme Robotics trades under the registered name Acme Robotics, Inc.",
         "provider": "nimble"},
        {"url": "https://companies-register.test/entity/acme",
         "excerpt": "The register records Acme Robotics, Inc. as the registered name.",
         "provider": "nimble"},
    ]
    ceo = [
        {"url": "https://acme-robotics.test/team",
         "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
         "provider": "nimble"},
    ]
    snapshot = legacy_export(client, {
        "legal_name": {"value": "Acme Robotics, Inc.", "status": "known", "citations": legal},
        "ceo": {"value": "Dana Ruiz", "status": "known", "citations": ceo},
    })
    assert client.post("/_test/import", json=snapshot).status_code == 204
    restored = world.get(f"/dossiers/{ACME}").json()
    assert restored["dossier_id"] == "d_legacy"
    assert restored["researched_at"] == NOW and restored["spent_units"] == 47
    legal_field = restored["fields"]["legal_name"]
    assert legal_field["status"] == "known" and legal_field["value"] == "Acme Robotics, Inc."
    assert [(c["url"], c["excerpt"], c["provider"]) for c in legal_field["candidates"]] == \
        [(c["url"], c["excerpt"], c["provider"]) for c in legal]
    assert all(c["kind"] is None and c["published_at"] is None
               for c in legal_field["candidates"])
    assert all(c["retrieved_at"] == NOW for c in legal_field["candidates"])
    assert [c["selected"] for c in legal_field["candidates"]] == [True, False]
    assert legal_field["citations"] == [{"url": c["url"], "excerpt": c["excerpt"],
                                         "provider": c["provider"]}
                                        for c in legal_field["candidates"]]
    assert restored["fields"]["ceo"]["candidates"] == [{
        "value": "Dana Ruiz", "url": "https://acme-robotics.test/team",
        "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
        "provider": "nimble", "kind": None, "published_at": None,
        "retrieved_at": NOW, "true_from": None, "learned_at": NOW, "selected": True}]
    assert restored["fields"]["ceo"]["citations"] == [{
        "url": "https://acme-robotics.test/team",
        "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
        "provider": "nimble"}]


def test_a_legacy_export_cannot_grow_candidates_it_did_not_cite(client, world):
    """The migration keeps exactly the citations the export carries -- no more, no fewer.

    This is the documented limitation: evidence Stage 1 gathered but never cited is
    not in the export, and import may not call a provider to fetch it again.
    """
    two = [{"url": "https://acme-robotics.test/about",
            "excerpt": "Acme Robotics is headquartered in Lisbon.", "provider": "nimble"},
           {"url": "https://directory.test/acme",
            "excerpt": "Acme Robotics is headquartered in Lisbon, our records say.",
            "provider": "nimble"}]
    snapshot = legacy_export(client, {
        "hq_city": {"value": "Lisbon", "status": "known", "citations": two}})
    client.post("/_test/import", json=snapshot)
    restored = world.get(f"/dossiers/{ACME}").json()
    assert len(restored["fields"]["hq_city"]["candidates"]) == len(two)
    assert {c["url"] for c in restored["fields"]["hq_city"]["candidates"]} == \
        {c["url"] for c in two}
    for name in FIELDS:
        if name != "hq_city":
            assert restored["fields"][name] == {"value": None, "status": "unknown",
                                               "citations": [], "candidates": [],
                                               "true_from": None, "learned_at": None}


def test_a_legacy_kind_and_publication_time_are_kept_when_present(client, world):
    """Facts the legacy state records for a page reach its candidate and steer the ranking."""
    own = {"url": "https://acme-robotics.test/team",
           "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
           "provider": "nimble", "kind": "own"}
    news = {"url": "https://news.test/acme-appoints",
            "excerpt": "Acme Robotics CEO: Priya Nair.", "provider": "nimble",
            "kind": "news", "published_at": "2026-02-20T00:00:00+00:00"}
    snapshot = legacy_export(client, {
        "ceo": {"value": "Dana Ruiz", "status": "known", "citations": [news, own]}})
    assert client.post("/_test/import", json=snapshot).status_code == 204
    field = world.get(f"/dossiers/{ACME}").json()["fields"]["ceo"]
    assert [(c["kind"], c["published_at"]) for c in field["candidates"]] == \
        [("own", None), ("news", "2026-02-20T00:00:00+00:00")]
    assert field["value"] == "Dana Ruiz"                 # own outranks news
    assert field["status"] == "known"


@pytest.mark.parametrize("stated,normalised", [
    ("2026-02-20T00:00:00Z", "2026-02-20T00:00:00+00:00"),
    ("2026-02-20T11:30:00+02:00", "2026-02-20T11:30:00+02:00"),
    ("2026-02-20T00:00:00.250Z", "2026-02-20T00:00:00.250000+00:00"),
])
def test_a_valid_legacy_publication_time_is_kept_and_normalised(client, world, stated,
                                                               normalised):
    """An RFC 3339 `published_at` in a legacy citation reaches its candidate, with its offset."""
    citation = {"url": "https://acme-robotics.test/team",
                "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
                "provider": "nimble", "kind": "own", "published_at": stated}
    snapshot = legacy_export(client, {
        "ceo": {"value": "Dana Ruiz", "status": "known", "citations": [citation]}})
    assert client.post("/_test/import", json=snapshot).status_code == 204
    [candidate] = world.get(f"/dossiers/{ACME}").json()["fields"]["ceo"]["candidates"]
    assert candidate["published_at"] == normalised
    assert candidate["kind"] == "own"


@pytest.mark.parametrize("stated", ["2026-02-20", "2026-02-20T00:00:00", "2026-02-30T00:00:00Z",
                                    "yesterday", "", 5])
def test_an_unusable_legacy_publication_time_becomes_null(client, world, stated):
    """A date without an offset, an impossible date or prose is no publication time."""
    citation = {"url": "https://acme-robotics.test/team",
                "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
                "provider": "nimble", "published_at": stated}
    snapshot = legacy_export(client, {
        "ceo": {"value": "Dana Ruiz", "status": "known", "citations": [citation]}})
    response = client.post("/_test/import", json=snapshot)
    assert response.status_code == 204
    [candidate] = world.get(f"/dossiers/{ACME}").json()["fields"]["ceo"]["candidates"]
    assert candidate["published_at"] is None and candidate["value"] == "Dana Ruiz"


@pytest.mark.parametrize("stated,kept", [("own", "own"), ("filing", "filing"),
                                         ("news", "news"), ("aggregator", "aggregator"),
                                         ("blog", None), ("OWN", None), ("", None), (7, None)])
def test_an_unusable_legacy_kind_becomes_null(client, world, stated, kept):
    """Only the four kinds survive; anything else has the authority of an aggregator."""
    citation = {"url": "https://acme-robotics.test/team",
                "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
                "provider": "nimble", "kind": stated}
    snapshot = legacy_export(client, {
        "ceo": {"value": "Dana Ruiz", "status": "known", "citations": [citation]}})
    assert client.post("/_test/import", json=snapshot).status_code == 204
    [candidate] = world.get(f"/dossiers/{ACME}").json()["fields"]["ceo"]["candidates"]
    assert candidate["kind"] == kept


def test_a_legacy_field_level_kind_and_time_reach_every_citation(client, world):
    """A legacy field may record the page facts itself; every one of its candidates keeps them."""
    first = {"url": "https://acme-robotics.test/team",
             "excerpt": "Dana Ruiz has led Acme Robotics as chief executive since 2021.",
             "provider": "nimble"}
    second = {"url": "https://news.test/acme-appoints",
              "excerpt": "Acme Robotics CEO: Priya Nair.", "provider": "nimble"}
    snapshot = legacy_export(client, {
        "ceo": {"value": "Dana Ruiz", "status": "known", "citations": [first, second],
                "kind": "own", "published_at": "2026-01-05T00:00:00Z"}})
    assert client.post("/_test/import", json=snapshot).status_code == 204
    candidates = world.get(f"/dossiers/{ACME}").json()["fields"]["ceo"]["candidates"]
    assert [(c["kind"], c["published_at"]) for c in candidates] == \
        [("own", "2026-01-05T00:00:00+00:00")] * 2


def test_a_duplicated_page_and_value_is_one_candidate(client, world):
    """Two citations of the same page for the same value are one candidate."""
    same = {"url": "https://acme-robotics.test/about",
            "excerpt": "Acme Robotics was founded in 2014.", "provider": "nimble"}
    snapshot = legacy_export(client, {
        "founded_year": {"value": 2014, "status": "known", "citations": [same, dict(same)]}})
    client.post("/_test/import", json=snapshot)
    field = world.get(f"/dossiers/{ACME}").json()["fields"]["founded_year"]
    assert len(field["candidates"]) == 1 and field["value"] == 2014


def test_imported_ids_and_accounts_are_preserved(client, world):
    """The dossier's id, the accounts and their tokens survive the migration."""
    signed_up = client.post("/auth/signup", json={"email": "dee@example.com",
                                                  "password": "correct horse",
                                                  "display_name": "Dee"}).json()
    snapshot = legacy_export(client, {"ceo": {"value": "Dana Ruiz", "status": "known",
                                              "citations": [
                                                  {"url": "https://acme-robotics.test/team",
                                                   "excerpt": "Dana Ruiz has led Acme "
                                                              "Robotics as chief executive.",
                                                   "provider": "nimble"}]}})
    client.post("/_test/import", json=snapshot)
    assert world.get(f"/dossiers/{ACME}").json()["dossier_id"] == "d_legacy"
    users = {user["id"]: user for user in client.get("/_test/export").json()["state"]["users"]}
    assert users["u_alice"]["email"] == "alice@example.com"
    assert users[signed_up["user_id"]]["email"] == "dee@example.com"
    assert user_password_works(client, "dee@example.com")


def user_password_works(client, email: str) -> bool:
    """Whether a migrated account still logs in with its password."""
    return client.post("/auth/login", json={"email": email,
                                            "password": "correct horse"}).status_code == 200


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
