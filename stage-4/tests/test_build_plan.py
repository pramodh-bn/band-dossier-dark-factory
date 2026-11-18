"""The build plan: both evidence sources fit in 80 units, and nothing is guessed.

The fake web is the transport; the plan, the retries and the cost accounting are
the shipped code. A corpus wide enough to exceed the render budget proves that a
value stated only by a late search result is still a candidate, that a value only
a research citation states is too, and that a page the build never read supplies
no candidate at all.
"""
from __future__ import annotations

import uuid

import pytest

import fakeweb
from app import builds, evidence, pages, providers
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


def browser_texts(domain: str) -> dict[str, str]:
    """Every page of the fake company, as {url: browser text}."""
    return {url: page.text(render=True)
            for url, page in fakeweb.served_pages(domain).items()}


def expected_candidates(domain: str, urls=None) -> dict[str, set]:
    """{field: {(url, value)}} that those pages' browser text supports."""
    tokens: dict[str, set] = {}
    for url, text in browser_texts(domain).items():
        if urls is not None and url not in urls:
            continue
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


def assert_no_missing(body: dict, expected: dict[str, set]) -> None:
    """Every value the expected pages support is a candidate of its field."""
    got = candidates_of(body)
    missing = {name: sorted(values - got.get(name, set()))
               for name, values in expected.items()}
    assert not {name: values for name, values in missing.items() if values}, missing


def research_urls(domain: str) -> list[str]:
    """The pages a research run for `domain` cites in the current epoch."""
    claims = fakeweb.RESEARCH.get(domain, {}).get(str(fakeweb.EPOCH), [])
    return [url for _, _, url, _ in claims]


def read_urls() -> set[str]:
    """The pages the last build read as browser text."""
    return {entry["key"] for entry in fakeweb.LOG
            if entry["method"] == "extract" and entry["status"] == 200}


def test_a_large_company_keeps_both_search_and_research_candidates(world):
    """A late search result's value and a research-only value are both candidates."""
    expected = expected_candidates(WIDE)
    body = build(world, WIDE)
    assert body["state"] == "ready"
    assert_no_missing(body, expected)
    ceo_values = {candidate["value"] for candidate in body["fields"]["ceo"]["candidates"]}
    assert ceo_values == {"Browser Person", "Late Page Person"}
    legal = body["fields"]["legal_name"]
    assert [candidate["url"] for candidate in legal["candidates"]] == research_urls(WIDE)
    assert legal["value"] == "Wide Works Ltd" and legal["candidates"][0]["kind"] == "filing"
    assert body["spent_units"] == fakeweb.spent() <= 80


def test_a_stale_served_text_never_becomes_a_candidate(world):
    """The page's served text names a different chief executive; only the browser value counts."""
    served = fakeweb.page_at(WIDE, "https://wide-works.test/stale").served
    assert evidence.page_values(served, WIDE)["ceo"][0][0] == "Stale Person"
    body = build(world, WIDE)
    values = {candidate["value"] for candidate in body["fields"]["ceo"]["candidates"]}
    assert "Stale Person" not in values and "Browser Person" in values
    assert all("Stale Person" not in candidate["excerpt"]
               for candidate in body["fields"]["ceo"]["candidates"])


def test_the_cited_page_is_read_before_optional_pages(world):
    """The research citation is read first, so its value survives a cap on reading."""
    body = build(world, HUGE)
    reads = read_urls()
    assert research_urls(HUGE)[0] in reads
    assert body["fields"]["legal_name"]["value"] == "Huge Group Holdings Ltd"
    assert body["spent_units"] == fakeweb.spent() <= 80


def test_the_budget_is_the_only_cap(world):
    """With more value-bearing pages than 80 units can read, the queue stops at the cap."""
    body = build(world, HUGE)
    assert body["state"] == "ready"
    renders = [entry for entry in fakeweb.LOG
               if entry["method"] == "extract" and entry["status"] == 200]
    assert len(renders) == (80 - len(builds.queries(HUGE)) - providers.RESEARCH_COST) // \
        providers.RENDER_COST
    assert body["spent_units"] == len(builds.queries(HUGE)) + \
        providers.RESEARCH_COST + len(renders) * providers.RENDER_COST <= 80
    ceo = body["fields"]["ceo"]
    assert 1 <= len(ceo["candidates"]) < 20          # the tail of the queue was not read
    read = read_urls()
    assert all(candidate["url"] in read for field in body["fields"].values()
               for candidate in field["candidates"])


def test_no_candidate_comes_from_a_page_the_build_did_not_read(world):
    """With every extract refused, the build keeps no candidate and invents no metadata."""
    fakeweb.FAULTS.add("extract")
    body = build(world, WIDE)
    assert body["state"] == "ready"                  # the searches and research answered
    assert body["spent_units"] == fakeweb.spent() == 10 + 25
    for name, field in body["fields"].items():
        assert field == {"value": None, "status": "unknown", "citations": [],
                         "candidates": [], "true_from": None, "learned_at": None,
                         "origins": [], "corroborated": False,
                         "corroborated_since": None}, name


def test_every_candidate_matches_the_page_it_cites(world):
    """No candidate carries a kind or time the page's own header does not state."""
    body = build(world, WIDE)
    for name, field in body["fields"].items():
        for candidate in field["candidates"]:
            page = fakeweb.page_at(WIDE, candidate["url"])
            header = pages.metadata(page.text(render=True), candidate["url"], WIDE)
            assert (candidate["kind"], candidate["published_at"]) == \
                (header["kind"], header["published_at"]), (name, candidate)


def test_planned_reads_keep_a_lineage_reserve_and_every_value_page():
    """The value-bearing prefix is read; only spare rest pages give up lineage units."""
    nine = {f"https://v{i}.test/p": "Acme Robotics is headquartered in Lisbon."
            for i in range(9)}
    nine.update({f"https://r{i}.test/p": "Acme Robotics publishes a note."
                 for i in range(8)})
    ordered = builds.page_priority(list(nine), nine, [], ACME)
    assert len(builds.planned_reads(ordered, nine, [], ACME, 45)) == 13
    assert builds.planned_reads(ordered, nine, [], ACME, 45) == ordered[:13]
    # Fourteen value-bearing pages still all fit, leaving one extra page for lineage.
    fourteen = {f"https://v{i}.test/p": "Acme Robotics is headquartered in Lisbon."
                for i in range(13)}
    fourteen["https://t.test/p"] = "Acme Robotics CTO: Ben Okafor."
    fourteen.update({f"https://r{i}.test/p": "Acme Robotics publishes a note."
                     for i in range(4)})
    ordered = builds.page_priority(list(fourteen), fourteen, [], ACME)
    assert len(builds.planned_reads(ordered, fourteen, [], ACME, 45)) == 14
    # A prefix larger than the affordable slots is capped by the budget.
    assert len(builds.planned_reads(ordered, fourteen, [], ACME, 9)) == 3


def test_triage_prioritises_executive_and_investor_only_pages(world):
    """An executive or investor statement in a snippet is value-bearing, not `rest`."""
    snippets = {
        "https://plain.test/page": "Acme Robotics publishes a note.",
        "https://titles.test/page": "Acme Robotics CTO: Ben Okafor.",
        "https://investors.test/page": "Acme Robotics is backed by Northwind Ventures.",
    }
    ordered = builds.page_priority(list(snippets), snippets, [], ACME)
    assert ordered[-1] == "https://plain.test/page"
    assert set(ordered[:2]) == {"https://titles.test/page",
                                "https://investors.test/page"}


def test_a_page_repeated_by_every_search_is_read_once(world):
    """Ten searches over the same pages read each selected page once."""
    body = build(world, WIDE)
    reads = [entry["key"] for entry in fakeweb.LOG
             if entry["method"] == "extract" and entry["status"] == 200]
    assert len(reads) == len(set(reads))
    assert body["spent_units"] == 10 + 25 + 3 * len(reads)


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
    """Refused attempts cost nothing and the retries still read the priority pages."""
    fakeweb.LIMIT = refusals
    expected = expected_candidates(WIDE)
    body = build(world, WIDE)
    assert body["state"] == "ready"
    assert_no_missing(body, expected)
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


def test_a_small_company_reads_every_page_and_runs_research(world):
    """A company that fits the budget reads all its pages and its cited page."""
    body = build(world, ACME)
    assert body["state"] == "ready"
    assert body["spent_units"] == fakeweb.spent() <= 80
    assert research_urls(ACME)[0] in read_urls()
    expected = expected_candidates(ACME)
    assert_no_missing(body, expected)
