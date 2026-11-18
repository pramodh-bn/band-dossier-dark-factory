"""A stand-in for the provider transport, so the HTTP and browser tests need no network.

`app.builds` looks `providers.Calls` up at call time, so a test replaces that
name with :class:`FakeCalls`. It subclasses the real :class:`app.providers.Calls`
and overrides only the three invocations, so the retry policy, the unit refunds,
the render cost and the 80-unit budget exercised by these tests are the shipped
ones. Each page has a served text and the text a browser shows (`render`): the
two differ where the corpus plants a stale reading.
"""
from __future__ import annotations

import dataclasses
import threading
import time

from app import providers

ACME = "acme-robotics.test"


@dataclasses.dataclass(frozen=True)
class Page:
    """One page the fake serves: its kind, publication time and two texts."""

    kind: str = "own"
    published: str | None = None
    searchable: bool = True
    served: str = ""
    browser: str | None = None

    def text(self, render: bool) -> str:
        """The text served for this page, with its header lines."""
        headers = [f"Kind: {self.kind}"]
        if self.published:
            headers.append(f"Published: {self.published}")
        prose = self.browser if (render and self.browser is not None) else self.served
        return "\n".join(headers) + "\n\n" + prose


#: domain -> {url: Page}
PAGES = {
    ACME: {
        "https://acme-robotics.test/about": Page(
            kind="own", published="2026-01-10T00:00:00+00:00",
            served=("Acme Robotics is headquartered in Lisbon.\n"
                    "Acme Robotics was founded in 2014.\n"
                    "Acme Robotics has raised $12M.")),
        "https://acme-robotics.test/team": Page(
            kind="own", served="Sam Ortiz has led Acme Robotics as chief executive since 2020.",
            browser="Dana Ruiz has led Acme Robotics as chief executive since 2021."),
        "https://acme-robotics.test/legal": Page(
            kind="own",
            served="Acme Robotics trades under the registered name Acme Robotics, Inc."),
        "https://acme-robotics.test/careers": Page(
            kind="own", searchable=False,
            served="Acme Robotics employs about 120 people across three offices."),
        "https://companies-register.test/entity/acme": Page(
            kind="filing", published="2025-04-14T09:30:00+00:00", searchable=False,
            served="Acme Robotics was incorporated in 2014."),
        "https://news.test/acme-appoints": Page(
            kind="news", published="2026-02-20T00:00:00+00:00",
            served="Acme Robotics CEO: Priya Nair."),
        "https://bizdata.test/acme": Page(
            kind="aggregator", published="2025-12-01T00:00:00+00:00",
            served="Acme Robotics CEO: Sam Ortiz."),
        "https://news.test/acme-old": Page(
            kind="news", published="2025-01-05T00:00:00+00:00",
            served="Acme Robotics is based in Porto."),
    },
    "second-company.test": {
        "https://second-company.test/": Page(
            kind="own", served="Second Company makes nothing anyone would write about."),
    },
    "rival-works.test": {
        "https://rival-works.test/about": Page(
            kind="own", served="Rival Works was founded in 2011 and is based in Ghent."),
        "https://directory.test/rival-works": Page(
            kind="aggregator", published="2026-01-02T00:00:00+00:00",
            served="Rival Works was founded in 1911 according to our records."),
    },
}


def snippet(page: Page) -> str:
    """The lite search snippet: the first characters of the page's served prose."""
    return page.text(render=False).partition("\n\n")[2][:300]


def _noise(domain: str, host: str, count: int, kind: str = "own") -> dict:
    """`count` pages that support no field, for a corpus wide enough to test the budget."""
    name = domain.split(".")[0].replace("-", " ")
    return {f"https://{host}/page-{index:02d}": Page(
        kind=kind, served=f"{name} publishes note {index} about its day-to-day work.")
        for index in range(1, count + 1)}


#: A company with more pages than the budget can read: a value only a late search
#: result states, a value only a research citation states, and a page whose served
#: text disagrees with the browser's.
PAGES["wide-works.test"] = {
    **_noise("wide-works.test", "wide-works.test", 10, "own"),
    **_noise("wide-works.test", "wire.test", 10, "news"),
    "https://wide-works.test/stale": Page(
        kind="own", served="Wide Works is led by chief executive Stale Person.",
        browser="Wide Works is led by chief executive Browser Person."),
    "https://zz-late.test/wide-profile": Page(
        kind="news", published="2026-01-01T00:00:00+00:00",
        served=("Wide Works is led by chief executive Late Page Person.")),
    "https://filings.test/wide-works": Page(
        kind="filing", published="2025-06-01T00:00:00+00:00", searchable=False,
        served="Wide Works trades under the registered name Wide Works Ltd."),
}

#: A company with more value-bearing pages than 80 units can render: the cited page must
#: still be read first, and the pages at the end of the queue cannot be.
_PEOPLE = ("Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
           "India", "Juliett")
PAGES["huge-group.test"] = {
    **{f"https://huge-group.test/p{index:02d}": Page(
        kind="own", served=f"Huge Group is led by chief executive Owner {_PEOPLE[index - 1]}.")
       for index in range(1, 11)},
    **{f"https://wire.test/h{index:02d}": Page(
        kind="news", served=f"Huge Group is led by chief executive Wire {_PEOPLE[index - 1]}.")
       for index in range(1, 11)},
    "https://filings.test/huge-group": Page(
        kind="filing", published="2025-06-01T00:00:00+00:00", searchable=False,
        served="Huge Group trades under the registered name Huge Group Holdings Ltd."),
}

#: domain -> research answer: (field, value, url, excerpt)
RESEARCH = {
    ACME: [("founded_year", 2014, "https://companies-register.test/entity/acme",
            "Acme Robotics was incorporated in 2014."),
           ("headcount_range", "51-200", "https://acme-robotics.test/careers",
            "Acme Robotics employs about 120 people across three offices.")],
    "wide-works.test": [
        ("legal_name", "Wide Works Ltd", "https://filings.test/wide-works",
         "Wide Works trades under the registered name Wide Works Ltd."),
    ],
    "huge-group.test": [
        ("legal_name", "Huge Group Holdings Ltd", "https://filings.test/huge-group",
         "Huge Group trades under the registered name Huge Group Holdings Ltd."),
    ],
}

#: Methods that always refuse with a 500: a persistent provider error.
FAULTS: set[str] = set()
#: Methods that never answer: each attempt costs its units and raises a timeout.
TIMEOUTS: set[str] = set()
#: Refuse the first :data:`LIMIT` attempts of every distinct call with a 429, then answer.
LIMIT = 0
#: Seconds each attempt takes.
DELAY = 0.0
#: Every attempt the fake has answered: {"method", "status", "cost", "key", "render"}.
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


def kind_of(url: str) -> str:
    """The kind the fake serves for `url`."""
    for pages in PAGES.values():
        if url in pages:
            return pages[url].kind
    return "aggregator"


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


def _attempted(method: str, key: str, cost: int, status: int, render: bool = False) -> None:
    """Record one attempt as the provider would: a refusal costs nothing."""
    with _lock:
        LOG.append({"method": method, "status": status,
                    "cost": 0 if status in (429, 500) else cost, "key": key,
                    "render": render})


def _gate(method: str, key: tuple, cost: int, render: bool = False) -> None:
    """Refuse or time out this attempt as the test's fault injection says."""
    time.sleep(DELAY)
    if method in TIMEOUTS:
        _attempted(method, key[0], cost, 200, render)
        raise NoAnswer()
    if method in FAULTS:
        _attempted(method, key[0], cost, 500, render)
        raise Refusal(500)
    if LIMIT:
        with _lock:
            seen = _attempts.get(key, 0)
            _attempts[key] = seen + 1
        if seen < LIMIT:
            _attempted(method, key[0], cost, 429, render)
            raise Refusal(429, "rate_limit")


class FakeCalls(providers.Calls):
    """The three calls a build makes, answered from :data:`PAGES` through the real retry code."""

    def _invoke_search(self, query: str, depth: str):
        cost = providers.SEARCH_COST[depth]
        _gate("search", (query,), cost)
        _attempted("search", query, cost, 200)
        domain = _named(query)
        if domain is None:
            return FakeSearch(results=[])
        return FakeSearch(results=[
            FakeResult(url=url, description=snippet(page))
            for url, page in PAGES[domain].items() if page.searchable])

    def _invoke_extract(self, url: str, render: bool = False):
        cost = providers.RENDER_COST if render else providers.EXTRACT_COST
        _gate("extract", (url, render), cost, render)
        for pages in PAGES.values():
            if url in pages:
                _attempted("extract", url, cost, 200, render)
                return FakeExtract(documents=[FakeDocument(content=pages[url].text(render))])
        _attempted("extract", url, cost, 200, render)
        return FakeExtract(documents=[])

    def _invoke_research(self, query: str):
        cost = providers.RESEARCH_COST
        _gate("research", (query,), cost)
        _attempted("research", query, cost, 200)
        claims = RESEARCH.get(_named(query), [])
        structured = {name: None for name in providers.FIELDS}
        for field, value, _, _ in claims:
            structured[field] = value
        return FakeResearch(structured=structured,
                            citations=[FakeCitation(url=url, field=f"$.{field}", snippet=excerpt)
                                       for field, _, url, excerpt in claims])
