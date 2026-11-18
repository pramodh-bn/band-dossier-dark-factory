"""Refusals, throttles and timeouts: bounded retries, cost parity, and the unit cap.

The fake provider subclasses the real `app.providers.Calls`, so the retry policy,
the refunds and the 80-unit budget under test are the shipped ones; only the
transport is replaced. `spent_units` must always equal what the provider says the
calls cost, which is the same log the graded harness reads.
"""
from __future__ import annotations

import uuid

import pytest

import fakeweb
from app import builds, providers
from conftest import FIELDS, wait_ready

ACME = "acme-robotics.test"
EXPECTED = {"legal_name": "Acme Robotics, Inc.", "hq_city": "Lisbon", "ceo": "Dana Ruiz",
            "founded_year": 2014, "headcount_range": "51-200", "funding_total": 12_000_000,
            "executives": [{"title": "cfo", "name": "Dana Ruiz"},
                           {"title": "cto", "name": "Ben Okafor"}]}


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain: str = ACME) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain)


def successful_domain_searches(domain: str) -> int:
    """Searches the provider answered with the query exactly equal to `domain`."""
    return sum(1 for entry in fakeweb.LOG
               if entry["method"] == "search" and entry["status"] == 200
               and entry["key"] == domain)


def test_throttled_first_attempts_still_build_the_same_dossier(world):
    """A provider that refuses the first attempt of every call still builds the dossier."""
    fakeweb.LIMIT = 1
    body = build(world)
    assert body["state"] == "ready"
    assert {name: field["value"] for name, field in body["fields"].items()} == EXPECTED
    for name in FIELDS:
        field = body["fields"][name]
        assert field["status"] in ("known", "contested") and field["citations"], name
    refused = [entry for entry in fakeweb.LOG if entry["status"] == 429]
    assert refused and all(entry["cost"] == 0 for entry in refused)
    assert body["spent_units"] == fakeweb.spent() > 0
    assert body["spent_units"] <= 80
    assert successful_domain_searches(ACME) == 1


def test_a_persistent_refusal_is_bounded_and_costs_nothing(world):
    """A provider that always fails ends the build as failed, after a bounded number of tries."""
    fakeweb.FAULTS.update({"search", "extract", "research"})
    body = build(world)
    assert body["state"] == "failed" and body["researched_at"] is None
    assert body["spent_units"] == 0 and fakeweb.spent() == 0
    assert all(field["value"] is None for field in body["fields"].values())
    attempts = len(fakeweb.LOG)
    calls = len({entry["key"] for entry in fakeweb.LOG})
    assert calls == len(builds.queries(ACME)) + 1  # ten searches and one research run
    assert calls * 2 <= attempts <= calls * providers.ATTEMPTS
    assert successful_domain_searches(ACME) == 0


@pytest.mark.parametrize("refusals", [1, 3])
def test_a_rate_limited_call_is_retried_until_it_answers(world, refusals):
    """`n` refusals in a row are retried, including the mock's three 429s before an answer."""
    fakeweb.LIMIT = refusals
    body = build(world)
    assert body["state"] == "ready"
    first_search = [entry for entry in fakeweb.LOG
                    if entry["method"] == "search" and entry["key"] == ACME]
    assert [entry["status"] for entry in first_search] == [429] * refusals + [200]
    assert body["spent_units"] == fakeweb.spent()
    assert successful_domain_searches(ACME) == 1


def test_a_timeout_costs_its_units_and_its_retries_stay_within_the_cap(world):
    """A call that never answers keeps its cost on every attempt, and 80 units still hold."""
    fakeweb.TIMEOUTS.add("extract")
    body = build(world)
    assert body["state"] == "ready"          # the searches and the research run answered
    extract_attempts = [entry for entry in fakeweb.LOG if entry["method"] == "extract"]
    assert len(extract_attempts) > 1         # it was retried, not surrendered after one try
    assert all(entry["cost"] == providers.RENDER_COST for entry in extract_attempts)
    assert body["spent_units"] == fakeweb.spent()
    assert 0 < body["spent_units"] <= 80


def test_a_throttled_research_run_is_retried_without_duplicating_its_claims(world):
    """A refused research start costs nothing and one successful run answers."""
    fakeweb.LIMIT = 1
    body = build(world)
    assert body["state"] == "ready"
    research = [entry for entry in fakeweb.LOG if entry["method"] == "research"]
    assert [entry["status"] for entry in research] == [429, 200]
    assert body["fields"]["founded_year"]["value"] == 2014
    assert body["spent_units"] == fakeweb.spent()
