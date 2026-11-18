"""The build plan: every gathered page is read, and the 80-unit cap is respected.

The fake web here is the transport; the plan, the retries and the cost accounting
are the shipped code. A corpus wide enough to exceed the old plan's sixteen
rendered pages proves that no gathered page is left unread, that a value only a
late page states still becomes a candidate, and that the budget is the only cap.
"""
from __future__ import annotations

import uuid

import pytest

import fakeweb
from app import builds, evidence, providers
from conftest import wait_ready

ACME = "acme-robotics.test"
WIDE = "wide-works.test"
HUGE = "huge-group.test"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain: str) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain, seconds=20)


def searchable(domain: str) -> dict[str, str]:
    """Every searchable page of the fake company, as {url: browser text}."""
    return {url: page.text(render=True) for url, page in fakeweb.PAGES[domain].items()
            if page.searchable}


def supported(domain: str, pages: dict[str, str]) -> dict[str, set]:
    """{field: {(url, value)}} that those pages' browser text supports."""
    tokens: dict[str, set] = {}
    for url, text in pages.items():
        for name, pairs in evidence.page_values(text, domain).items():
            for value, _ in pairs:
                tokens.setdefault(name, set()).add((url, repr(value)))
    return tokens


def candidates_of(body: dict) -> dict[str, set]:
    """{field: {(url, value)}} the dossier's candidates carry."""
    found: dict[str, set] = {}
    for name, field in body["fields"].items():
        for candidate in field["candidates"]:
            found.setdefault(name, set()).add((candidate["url"], repr(candidate["value"])))
    return found


def assert_no_missing(body: dict, domain: str, pages: dict[str, str]) -> None:
    """Every value a read page supports is a candidate of its field."""
    missing = {name: sorted(values - candidates_of(body).get(name, set()))
               for name, values in supported(domain, pages).items()}
    assert not {name: values for name, values in missing.items() if values}, missing


def rendered_pages(domain: str) -> list[str]:
    """The URLs this company's build actually read, in the order it read them."""
    return [url for url, page in fakeweb.PAGES[domain].items() if page.searchable]


def test_a_corpus_past_the_old_page_cap_loses_no_candidate(world):
    """A 22-page company is read whole, and a value only the last page states is a candidate."""
    pages = searchable(WIDE)
    assert len(pages) >= 20
    body = build(world, WIDE)
    assert body["state"] == "ready"
    assert_no_missing(body, WIDE, pages)
    ceo = body["fields"]["ceo"]
    assert [(candidate["value"], candidate["url"]) for candidate in ceo["candidates"]] == \
        [("Browser Person", "https://wide-works.test/stale"),
         ("Late Page Person", "https://zz-late.test/wide-profile")]
    assert ceo["value"] == "Browser Person" and ceo["status"] == "contested"
    assert body["spent_units"] == fakeweb.spent() <= 80
    rendered = {entry["key"] for entry in fakeweb.LOG
                if entry["method"] == "extract" and entry["status"] == 200}
    assert set(pages) <= rendered          # every gathered page was read as browser text


def test_a_stale_served_text_never_becomes_a_candidate(world):
    """The page's served text names a different chief executive; only the browser value counts."""
    served = fakeweb.PAGES[WIDE]["https://wide-works.test/stale"].served
    assert evidence.page_values(served, WIDE)["ceo"][0][0] == "Stale Person"
    body = build(world, WIDE)
    values = {candidate["value"] for candidate in body["fields"]["ceo"]["candidates"]}
    assert "Stale Person" not in values and "Browser Person" in values
    assert all("Stale Person" not in candidate["excerpt"]
               for candidate in body["fields"]["ceo"]["candidates"])


def test_a_page_repeated_by_every_search_is_read_once(world):
    """Ten searches that all return the same pages still read each page once."""
    body = build(world, WIDE)
    reads = [entry["key"] for entry in fakeweb.LOG
             if entry["method"] == "extract" and entry["status"] == 200]
    assert len(reads) == len(set(reads)) == len(searchable(WIDE))
    assert body["spent_units"] == 10 + 3 * len(searchable(WIDE))


def test_the_budget_is_the_only_cap(world):
    """With more pages than 80 units can read, the build stops at the cap and says so."""
    pages = searchable(HUGE)
    assert len(pages) > 23
    body = build(world, HUGE)
    assert body["state"] == "ready"
    assert body["spent_units"] == fakeweb.spent() <= 80
    renders = [entry for entry in fakeweb.LOG
               if entry["method"] == "extract" and entry["status"] == 200]
    assert len(renders) == (80 - len(builds.queries(HUGE))) // providers.RENDER_COST
    assert body["spent_units"] == len(builds.queries(HUGE)) + len(renders) * 3
    assert body["fields"]["ceo"]["value"] is None       # the capped page was never read
    assert "Capped Person" not in {
        candidate["value"] for candidate in body["fields"]["ceo"]["candidates"]}


def test_reading_the_gathered_pages_comes_before_research(world):
    """Research runs only when it cannot crowd out a gathered page; else it is skipped."""
    wide = build(world, WIDE)
    research = [entry for entry in fakeweb.LOG if entry["method"] == "research"]
    assert research == []                                # 10 + 66 leaves no room for 25 + 3
    before = fakeweb.spent()
    small = build(world, ACME)
    research = [entry for entry in fakeweb.LOG if entry["method"] == "research"]
    assert len(research) == 1 and research[0]["status"] == 200
    assert small["spent_units"] == fakeweb.spent() - before <= 80
    assert wide["spent_units"] <= 80


def test_a_cited_page_is_read_when_research_runs(world):
    """The research run's cited pages are read as browser text too."""
    body = build(world, ACME)
    cited = {url for claim in fakeweb.RESEARCH[ACME] for url in (claim[2],)}
    reads = {entry["key"] for entry in fakeweb.LOG
             if entry["method"] == "extract" and entry["status"] == 200}
    assert cited <= reads
    assert body["spent_units"] == fakeweb.spent()


def test_the_plan_keeps_exactly_one_successful_domain_search(world):
    """Ten searches are made; exactly one is the normalised domain and it answered once."""
    build(world, WIDE)
    domain_searches = [entry for entry in fakeweb.LOG
                       if entry["method"] == "search" and entry["status"] == 200
                       and entry["key"] == WIDE]
    assert len(domain_searches) == 1
    assert len([entry for entry in fakeweb.LOG if entry["method"] == "search"]) == 10


@pytest.mark.parametrize("refusals", [1, 2])
def test_a_throttled_provider_still_builds_every_candidate(world, refusals):
    """Refused attempts cost nothing and the retries still read every gathered page."""
    fakeweb.LIMIT = refusals
    pages = searchable(WIDE)
    body = build(world, WIDE)
    assert body["state"] == "ready"
    assert_no_missing(body, WIDE, pages)
    refused = [entry for entry in fakeweb.LOG if entry["status"] == 429]
    assert refused and all(entry["cost"] == 0 for entry in refused)
    assert body["spent_units"] == fakeweb.spent() <= 80


def test_a_persistent_refusal_fails_the_build_and_costs_nothing(world):
    """With every call refused the build fails, spends nothing, and stops retrying."""
    fakeweb.FAULTS.update({"search", "extract", "research"})
    body = build(world, WIDE)
    assert body["state"] == "failed" and body["spent_units"] == 0
    assert fakeweb.spent() == 0
    attempts = len(fakeweb.LOG)
    calls = len({entry["key"] for entry in fakeweb.LOG})
    assert calls * 2 <= attempts <= calls * providers.ATTEMPTS


def test_a_timeout_keeps_its_units_and_the_cap_holds(world):
    """A call that never answers pays every attempt, and the plan never spends over 80."""
    fakeweb.TIMEOUTS.add("extract")
    body = build(world, WIDE)
    assert fakeweb.spent() == body["spent_units"] <= 80
    timeouts = [entry for entry in fakeweb.LOG if entry["method"] == "extract"]
    assert len(timeouts) > 1 and all(entry["cost"] == 3 for entry in timeouts)
