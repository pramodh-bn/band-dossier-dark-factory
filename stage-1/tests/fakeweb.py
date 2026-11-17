"""A stand-in for the provider layer, so the HTTP tests need no network.

`app.builds` looks `providers.Calls` up at call time, so a test replaces that
name with :class:`FakeCalls`; everything above it -- routing, the store, the
background build, the 80-unit accounting -- is the real implementation.
"""
from __future__ import annotations

import dataclasses
import time

ACME = "acme-robotics.test"

#: domain -> {url: (text, searchable)}
PAGES = {
    ACME: {
        "https://acme-robotics.test/about":
            ("Acme Robotics is headquartered in Lisbon.\n"
             "Acme Robotics was founded in 2014.\n"
             "Acme Robotics has raised $12M.",
             True),
        "https://acme-robotics.test/team":
            ("Dana Ruiz has led Acme Robotics as chief executive since 2021.", True),
        "https://acme-robotics.test/legal":
            ("Acme Robotics trades under the registered name Acme Robotics, Inc.", True),
        "https://acme-robotics.test/careers":
            ("Acme Robotics employs about 120 people across three offices.", True),
        "https://companies-register.test/entity/acme":
            ("Acme Robotics was incorporated in 2014.", False),
        "https://news.test/acme-raises":
            ("Acme Robotics is reportedly in talks to raise $40M.", True),
    },
    "second-company.test": {
        "https://second-company.test/":
            ("Second Company makes nothing anyone would write about.", True),
    },
}

#: domain -> research answer: (field, value, url, excerpt)
RESEARCH = {
    ACME: [("founded_year", 2014, "https://companies-register.test/entity/acme",
             "Acme Robotics was incorporated in 2014.")],
}

#: Methods the fake provider refuses; a test sets this to simulate an outage.
FAULTS: set[str] = set()
#: Every provider call the fake has answered, for the "no provider call from a read" check.
CALLS: list[str] = []
#: Seconds each call takes, for a test that needs to see a build in flight.
DELAY = 0.0


def reset() -> None:
    """Clear the fault injection, the call log and any delay."""
    global DELAY  # pylint: disable=global-statement
    FAULTS.clear()
    CALLS.clear()
    DELAY = 0.0


@dataclasses.dataclass
class FakeCitation:
    """The part of a research citation a build reads."""

    url: str
    field: str
    snippet: str


def _named(query: str) -> str | None:
    """The domain a query names, by domain or by first label."""
    lowered = query.lower()
    for domain in PAGES:
        if domain in lowered or domain.split(".")[0].replace("-", " ") in lowered:
            return domain
    return None


class FakeCalls:  # pylint: disable=too-few-public-methods
    """The three calls a build makes, answered from :data:`PAGES` at the same unit costs."""

    def __init__(self, budget) -> None:
        self.budget = budget
        self.succeeded = False

    def search(self, query: str, depth: str = "lite") -> list[dict] | None:
        """Answer a search with the named company's searchable pages."""
        CALLS.append("search")
        time.sleep(DELAY)
        if "search" in FAULTS or not self.budget.reserve(1):
            return None
        self.succeeded = True
        domain = _named(query)
        if domain is None:
            return []
        return [{"url": url, "title": "", "snippet": ""}
                for url, (_, searchable) in PAGES[domain].items() if searchable]

    def extract(self, url: str) -> str | None:
        """Answer an extract with a page's full served text."""
        CALLS.append("extract")
        time.sleep(DELAY)
        if "extract" in FAULTS or not self.budget.reserve(1):
            return None
        for pages in PAGES.values():
            if url in pages:
                self.succeeded = True
                return pages[url][0]
        return None

    def research(self, query: str) -> tuple[dict, list] | None:
        """Answer a research run with the named company's claims."""
        CALLS.append("research")
        time.sleep(DELAY)
        if "research" in FAULTS or not self.budget.reserve(25):
            return None
        self.succeeded = True
        domain = _named(query)
        claims = RESEARCH.get(domain, [])
        structured = {name: None for name in
                      ("legal_name", "hq_city", "ceo", "founded_year", "headcount_range",
                       "funding_total")}
        for field, value, _, _ in claims:
            structured[field] = value
        return structured, [FakeCitation(url=url, field=f"$.{field}", snippet=excerpt)
                            for field, _, url, excerpt in claims]
