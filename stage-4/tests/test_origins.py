"""Origins, corroboration and the investors field (§4)."""
from __future__ import annotations

import uuid

import pytest

from app import candidates, evidence, pages
from conftest import FIELDS, NOW, wait_ready

ACME = "acme-robotics.test"
ORIGIN = "origin-works.test"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain: str) -> dict:
    """Start a dossier and wait for its build to end."""
    client.post("/dossiers", json={"domain": domain}, headers={"Idempotency-Key": key()})
    return wait_ready(client, domain)


# -- lineage -----------------------------------------------------------------

def test_registrant_defaults_to_the_url_host_and_reads_its_line():
    """A `Registrant` domain is the page's publisher; without one it is its host."""
    assert pages.registrant("Kind: own\n\nProse.", "https://a.test/x") == "a.test"
    assert pages.registrant("Kind: own\nRegistrant: Acme-Robotics.test.\n\nProse.",
                            "https://elsewhere.test/x") == "acme-robotics.test"


def test_a_lookalike_is_an_aggregator_with_the_company_as_origin():
    """`Kind: own` off the registrant's own host is an aggregator (§4, Own pages)."""
    own = "Kind: own\nRegistrant: acme-robotics.test\n\nAcme Robotics was founded in 2014."
    assert pages.is_lookalike(own, "https://acme-robotics.test/x", ACME) is False
    text = "Kind: own\nRegistrant: domainhold.test\n\nAcme Robotics was founded in 2014."
    assert pages.is_lookalike(text, "https://getacme.test/x", ACME) is True
    assert pages.kind_of(text, "https://getacme.test/x", ACME) == "aggregator"
    lineage = pages.Lineage({"https://getacme.test/x": text}, lambda url: None, ACME)
    assert lineage.origin("https://getacme.test/x") == ACME


def test_a_canonical_copy_takes_the_original_origin():
    """Rule 2 follows a `Canonical` URL, and a fetched original answers it."""
    original = "Kind: own\nRegistrant: acme-robotics.test\n\nProse."
    copy = ("Kind: news\nCanonical: https://acme-robotics.test/about\n\nProse.")
    texts = {"https://wire.test/copy": copy}

    def fetch(url: str):
        return original if url == "https://acme-robotics.test/about" else None

    lineage = pages.Lineage(texts, fetch, ACME)
    assert lineage.origin("https://wire.test/copy") == ACME


def test_first_publisher_wins_over_the_canonical_original():
    """Rule 1 is read before rule 2."""
    copy = ("Kind: news\nCanonical: https://acme-robotics.test/about\n"
            "First-Published-By: wire.test\n\nProse.")
    lineage = pages.Lineage({"https://syndicate.test/copy": copy}, lambda url: None, ACME)
    assert lineage.origin("https://syndicate.test/copy") == "wire.test"


def test_three_canonical_hops_are_followed_and_a_fourth_falls_back_to_the_host():
    """Rule 2 follows at most three Canonical URLs in a row."""
    texts = {
        "https://a.test/1": "Kind: news\nCanonical: https://a.test/2\n\nProse.",
        "https://a.test/2": "Kind: news\nCanonical: https://a.test/3\n\nProse.",
        "https://a.test/3": "Kind: news\nCanonical: https://a.test/4\n\nProse.",
        "https://a.test/4": "Kind: news\nCanonical: https://a.test/5\n\nProse.",
        "https://a.test/5": "Kind: news\nRegistrant: end.test\n\nProse.",
    }
    lineage = pages.Lineage(dict(texts), lambda url: texts.get(url), ACME)
    assert lineage.origin("https://a.test/1") == "a.test"


def test_a_canonical_url_no_provider_serves_falls_back_to_its_host():
    """Where no page is served at the Canonical URL, that URL's host is the origin."""
    copy = "Kind: news\nCanonical: https://nowhere.test/x\n\nProse."
    lineage = pages.Lineage({"https://wire.test/copy": copy}, lambda url: None, ACME)
    assert lineage.origin("https://wire.test/copy") == "nowhere.test"


def test_a_canonical_cycle_does_not_loop():
    """A cycle of copies has no original, so a host stands in."""
    texts = {
        "https://a.test/1": "Kind: news\nCanonical: https://a.test/2\n\nProse.",
        "https://a.test/2": "Kind: news\nCanonical: https://a.test/1\n\nProse.",
    }
    lineage = pages.Lineage(dict(texts), lambda url: texts.get(url), ACME)
    assert lineage.origin("https://a.test/1") in {"a.test"}


# -- investor parsing --------------------------------------------------------

def test_an_investor_suffix_run_is_a_name_and_a_led_by_run_needs_an_amount():
    """The two format rules of §4, Investors."""
    assert evidence.investor_names(
        "Acme Robotics raised $40M in a round led by General Atlantic.", ACME) == \
        ["General Atlantic"]
    assert evidence.investor_names("Acme Robotics is led by Dana Ruiz.", ACME) == []
    assert evidence.investor_names(
        "Acme Robotics is backed by Northwind Ventures.", ACME) == ["Northwind Ventures"]


def test_the_company_name_and_the_statement_phrases_separate_runs():
    """Removing them stops a run spanning the company name."""
    assert evidence.investor_names(
        "Bluebird Capital participated alongside Northwind Ventures in Acme Robotics.", ACME) == \
        ["Bluebird Capital", "Northwind Ventures"]


def test_investors_field_membership_and_order():
    """An own candidate or two origins makes a name; the value is sorted."""
    def candidate(value, url, kind, origin):
        return {"value": value, "url": url, "excerpt": value, "provider": "nimble",
                "kind": kind, "published_at": None, "retrieved_at": NOW,
                "true_from": "2026-01-01", "learned_at": None, "origin": origin,
                "selected": False}

    field = candidates.investors_field([
        candidate("Zeta Capital", "https://a.test/1", "own", "a.test"),
        candidate("Alpha Ventures", "https://b.test/1", "aggregator", "b.test"),
        candidate("Alpha Ventures", "https://c.test/1", "aggregator", "c.test"),
        candidate("Solo Partners", "https://d.test/1", "aggregator", "d.test"),
    ])
    assert field["value"] == ["Alpha Ventures", "Zeta Capital"]
    assert field["status"] == "known"
    assert field["origins"] == ["a.test", "b.test", "c.test"]
    assert field["corroborated"] is False
    selected = [(c["value"], c["selected"]) for c in field["candidates"]]
    assert selected.count(("Solo Partners", False)) == 1
    assert field["true_from"] == "2026-01-01"
    # citations are grouped by member in value order, then rank order within a member
    assert [c["url"] for c in field["citations"]] == \
        ["https://b.test/1", "https://c.test/1", "https://a.test/1"]


# -- the built dossier -------------------------------------------------------

def test_every_candidate_carries_the_origin_of_its_page(world):
    """A build resolves each page's origin, fetching the originals rule 2 needs."""
    body = build(world, ORIGIN)
    assert body["state"] == "ready"
    got = {candidate["url"]: candidate["origin"]
           for field in body["fields"].values() for candidate in field["candidates"]}
    assert got["https://origin-works.test/about"] == ORIGIN
    assert got["https://registry.test/origin-works"] == "registry.test"
    assert got["https://syndicate.test/origin-works-about"] == ORIGIN
    assert got["https://copychain-one.test/origin-works"] == ORIGIN
    assert got["https://lookalike.test/about"] == ORIGIN
    assert got["https://missing.test/origin-works"] == "nowhere.test"
    assert got["https://firstpub.test/origin-works"] == "wireorigin.test"


def test_a_lookalike_reports_aggregator_authority(world):
    """The own statement on a lookalike is an aggregator for the selection rule."""
    body = build(world, ORIGIN)
    city = body["fields"]["hq_city"]
    assert city["value"] == "Ghent"
    assert [candidate["kind"] for candidate in city["candidates"]] == ["aggregator"]
    assert city["origins"] == [ORIGIN]


def test_origins_and_corroboration_follow_the_selected_value(world):
    """Two independent origins for one value corroborate it at this build."""
    body = build(world, ORIGIN)
    legal = body["fields"]["legal_name"]
    assert legal["value"] == "Origin Works Ltd"
    assert legal["origins"] == [ORIGIN, "registry.test"]
    assert legal["corroborated"] is True
    assert legal["corroborated_since"] == NOW
    for name, field in body["fields"].items():
        assert field["origins"] == sorted(set(field["origins"])), name
        if field["value"] is None:
            assert field["origins"] == [] and field["corroborated"] is False, name


def test_the_first_build_names_the_investors(world):
    """Investors are their names, sorted, each with its own candidates' citations."""
    body = build(world, ORIGIN)
    investors = body["fields"]["investors"]
    assert investors["value"] == ["Bluebird Capital", "General Atlantic",
                                 "Northwind Ventures"]
    assert investors["status"] == "known"
    assert investors["true_from"] == "2026-02-01"
    assert investors["origins"] == ["north-one.test", "north-two.test", ORIGIN]
    assert investors["corroborated"] is False
    assert [c["url"] for c in investors["citations"]] == [
        "https://origin-works.test/team",
        "https://origin-works.test/team",
        "https://north-two.test/origin-works",
        "https://north-one.test/origin-works",
    ]
    by_value = {}
    for candidate in investors["candidates"]:
        by_value.setdefault(candidate["value"], []).append(candidate["selected"])
    assert by_value["Bluebird Capital"] == [True]
    assert by_value["General Atlantic"] == [True]
    assert by_value["Northwind Ventures"] == [True, True]
    assert by_value["Eastwind Partners"] == [False]


def test_an_investor_with_only_one_aggregator_origin_is_not_a_member():
    """A candidate that is not `own` or `filing` needs a second origin."""
    field = candidates.investors_field([{
        "value": "Solo Partners", "url": "https://a.test/x", "excerpt": "x",
        "provider": "nimble", "kind": "aggregator", "published_at": None,
        "retrieved_at": NOW, "true_from": None, "learned_at": None,
        "origin": "a.test", "selected": False}])
    assert field["value"] is None and field["status"] == "unknown"
    assert field["citations"] == [] and field["origins"] == []
    assert field["candidates"][0]["selected"] is False


def test_corroborated_since_is_the_build_that_reached_two_standing_origins(world):
    """Standing origins survive a later build that gathers only one of them."""
    first = build(world, ORIGIN)
    assert first["fields"]["legal_name"]["corroborated_since"] == NOW
    # The registry page is no longer served; the own page alone still supports the name.
    world.post("/_test/clock", json={"now": "2026-05-01T09:00:00+00:00"})
    fake_page = __import__("fakeweb")
    original = fake_page.PAGES[ORIGIN].pop("https://registry.test/origin-works")
    try:
        world.post(f"/dossiers/{ORIGIN}/refresh", headers={"Idempotency-Key": key()})
        second = wait_ready(world, ORIGIN)
        legal = second["fields"]["legal_name"]
        assert legal["value"] == "Origin Works Ltd"
        assert legal["origins"] == [ORIGIN]
        assert legal["corroborated"] is False
        # The standing origin from the first build still corroborates the value.
        assert legal["corroborated_since"] == NOW
    finally:
        fake_page.PAGES[ORIGIN]["https://registry.test/origin-works"] = original


def test_investors_history_is_typed_by_name(world):
    """A name added with an earlier date is a correction; a name that leaves is a change."""
    domain = "investors-change.test"
    first = build(world, domain)
    assert first["fields"]["investors"]["value"] == ["Bluebird Capital"]
    world.post("/_test/clock", json={"now": "2026-03-02T09:00:00+00:00"})
    import fakeweb
    fakeweb.EPOCH = 1
    world.post(f"/dossiers/{domain}/refresh", headers={"Idempotency-Key": key()})
    second = wait_ready(world, domain)
    assert second["fields"]["investors"]["value"] == ["Bluebird Capital",
                                                      "Northwind Ventures"]
    history = world.get(f"/dossiers/{domain}/history").json()["fields"]["investors"]
    assert [entry["type"] for entry in history] == ["initial", "correction"]
    feed = world.get("/changes").json()["changes"]
    assert [(entry["field"], entry["type"]) for entry in feed] == [("investors", "correction")]
    fakeweb.EPOCH = 2
    world.post("/_test/clock", json={"now": "2026-05-01T09:00:00+00:00"})
    world.post(f"/dossiers/{domain}/refresh", headers={"Idempotency-Key": key()})
    third = wait_ready(world, domain)
    assert third["fields"]["investors"]["value"] == ["Northwind Ventures"]
    history = world.get(f"/dossiers/{domain}/history").json()["fields"]["investors"]
    assert [entry["type"] for entry in history] == ["initial", "correction", "change"]


def test_values_decides_investor_membership_from_the_candidates_at_the_date(world):
    """`/values` gives the members the evidence at `true_at` supports, with their citations."""
    build(world, ORIGIN)
    field = world.get(f"/dossiers/{ORIGIN}/values",
                      params={"true_at": "2026-03-01"}).json()["fields"]["investors"]
    assert field["value"] == ["Bluebird Capital", "General Atlantic",
                             "Northwind Ventures"]
    assert field["true_from"] == "2026-02-01"
    assert len(field["citations"]) == 4
    early = world.get(f"/dossiers/{ORIGIN}/values",
                      params={"true_at": "2025-01-01"}).json()["fields"]["investors"]
    assert early == {"value": None, "true_from": None, "citations": []}


def test_a_stage_four_export_round_trips_origins_and_investors(world, client):
    """Importing this service's own export preserves origins, corroboration and investors."""
    body = build(world, ORIGIN)
    history = world.get(f"/dossiers/{ORIGIN}/history").json()
    snapshot = client.get("/_test/export").json()
    client.post("/_test/reset", json={
        "now": NOW, "users": [{"id": "u_alice", "email": "alice@example.com",
                               "password": "correct horse", "display_name": "Alice"}]})
    assert client.post("/_test/import", json=snapshot).status_code == 204
    restored = world.get(f"/dossiers/{ORIGIN}").json()
    assert restored == body
    assert world.get(f"/dossiers/{ORIGIN}/history").json() == history
    assert client.get("/_test/export").json() == snapshot


def test_field_origins_cover_every_member_of_a_list_field(world):
    """`origins` for executives and investors is the members' candidates' origins."""
    body = build(world, ORIGIN)
    for name in ("executives", "investors"):
        field = body["fields"][name]
        groups = []
        if field["value"] is not None:
            groups = [c for c in field["candidates"] if c["selected"]]
        assert field["origins"] == sorted({c["origin"] for c in groups}), name
    for name in FIELDS:
        if body["fields"][name]["value"] is not None:
            assert body["fields"][name]["origins"], name
