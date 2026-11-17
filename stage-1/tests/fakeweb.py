"""A stand-in for the provider transport, so the HTTP tests need no network.

`app.builds` looks `providers.Calls` up at call time, so a test replaces that
name with :class:`FakeCalls`. It subclasses the real :class:`app.providers.Calls`
and overrides only the three invocations, so the retry policy, the unit refunds
and the 80-unit budget exercised by these tests are the shipped ones.
"""
from __future__ import annotations

import dataclasses
import threading
import time

from app import providers

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

#: Methods that always refuse with a 500: a persistent provider error.
FAULTS: set[str] = set()
#: Methods that never answer: each attempt costs its units and raises a timeout.
TIMEOUTS: set[str] = set()
#: Refuse the first :data:`LIMIT` attempts of every distinct call with a 429, then answer.
LIMIT = 0
#: Seconds each attempt takes.
DELAY = 0.0
#: Every attempt the fake has answered: {"method", "status", "cost", "key"}.
LOG: list[dict] = []
_lock = threading.Lock()
_attempts: dict[tuple, int] = {}


def reset() -> None:
    """Clear the injected faults, the attempt counts, the delay and the log."""
    global DELAY, LIMIT  # pylint: disable=global-statement
    FAULTS.clear()
    TIMEOUTS.clear()
    LOG.clear()
    _attempts.clear()
    DELAY = 0.0
    LIMIT = 0


def spent() -> int:
    """What the provider says these calls cost, the way a provider's own log would."""
    return sum(entry["cost"] for entry in LOG)


class Refusal(Exception):
    """A provider answering with an error status, which costs nothing."""

    def __init__(self, status: int, kind: str = "upstream") -> None:
        super().__init__(f"provider answered {status}")
        self.status_code = status
        self.kind = kind


class NoAnswer(Exception):
    """A call whose answer never arrived, which costs its units."""

    def __init__(self) -> None:
        super().__init__("no answer")
        self.status_code = None
        self.kind = "timeout"


@dataclasses.dataclass
class FakeResult:
    """The part of a search result a build reads."""

    url: str
    title: str = ""
    description: str = ""
    content: str = ""


@dataclasses.dataclass
class FakeSearch:
    """The part of a search response a build reads."""

    results: list


@dataclasses.dataclass
class FakeDocument:
    """The part of an extracted document a build reads."""

    content: str


@dataclasses.dataclass
class FakeExtract:
    """The part of an extract response a build reads."""

    documents: list


@dataclasses.dataclass
class FakeCitation:
    """The part of a research citation a build reads."""

    url: str
    field: str
    snippet: str


@dataclasses.dataclass
class FakeResearch:
    """The part of a research response a build reads."""

    structured: dict
    citations: list


def _named(query: str) -> str | None:
    """The domain a query names, by domain or by first label."""
    lowered = query.lower()
    for domain in PAGES:
        if domain in lowered or domain.split(".")[0].replace("-", " ") in lowered:
            return domain
    return None


def _attempted(method: str, key: str, cost: int, status: int) -> None:
    """Record one attempt as the provider would: a refusal costs nothing, a timeout does."""
    with _lock:
        LOG.append({"method": method, "status": status,
                    "cost": 0 if status == 429 or status == 500 else cost, "key": key})


def _gate(method: str, key: tuple, cost: int) -> None:
    """Refuse or time out this attempt as the test's fault injection says."""
    time.sleep(DELAY)
    if method in TIMEOUTS:
        _attempted(method, key[0], cost, 200)
        raise NoAnswer()
    if method in FAULTS:
        _attempted(method, key[0], cost, 500)
        raise Refusal(500)
    if LIMIT:
        with _lock:
            seen = _attempts.get(key, 0)
            _attempts[key] = seen + 1
        if seen < LIMIT:
            _attempted(method, key[0], cost, 429)
            raise Refusal(429, "rate_limit")


class FakeCalls(providers.Calls):
    """The three calls a build makes, answered from :data:`PAGES` through the real retry code."""

    def _account(self, method: str, key: str, cost: int) -> None:
        """Log a successful attempt at its cost."""
        _attempted(method, key, cost, 200)

    def _invoke_search(self, query: str, depth: str):
        cost = providers.SEARCH_COST[depth]
        _gate("search", (query,), cost)
        self._account("search", query, cost)
        domain = _named(query)
        if domain is None:
            return FakeSearch(results=[])
        return FakeSearch(results=[
            FakeResult(url=url) for url, (_, searchable) in PAGES[domain].items() if searchable
        ])

    def _invoke_extract(self, url: str):
        cost = providers.EXTRACT_COST
        _gate("extract", (url,), cost)
        for pages in PAGES.values():
            if url in pages:
                self._account("extract", url, cost)
                return FakeExtract(documents=[FakeDocument(content=pages[url][0])])
        self._account("extract", url, cost)
        return FakeExtract(documents=[])

    def _invoke_research(self, query: str):
        cost = providers.RESEARCH_COST
        _gate("research", (query,), cost)
        self._account("research", query, cost)
        claims = RESEARCH.get(_named(query), [])
        structured = {name: None for name in providers.FIELDS}
        for field, value, _, _ in claims:
            structured[field] = value
        return FakeResearch(structured=structured,
                            citations=[FakeCitation(url=url, field=f"$.{field}", snippet=excerpt)
                                       for field, _, url, excerpt in claims])
