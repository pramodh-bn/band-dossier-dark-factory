"""A stand-in for the provider transport, so the HTTP and browser tests need no network.

`app.builds` looks `providers.Calls` up at call time, so a test replaces that
name with :class:`FakeCalls`. It subclasses the real :class:`app.providers.Calls`
and overrides only the three invocations, so the retry policy, the unit refunds,
the render cost and the 80-unit budget exercised by these tests are the shipped
ones. Each page has a served text and the text a browser shows (`render`): the
two differ where the corpus plants a stale reading. Pages carry the epochs they
are served in, so a test can move the web the way the mock's epochs do.
"""
from __future__ import annotations

import dataclasses
import threading
import time

from app import providers

ACME = "acme-robotics.test"


@dataclasses.dataclass(frozen=True)
class Page:
    """One page the fake serves: its kind, dates, epochs and two texts."""

    kind: str = "own"
    published: str | None = None
    effective: str | None = None
    searchable: bool = True
    served: str = ""
    browser: str | None = None
    epochs: tuple = (0,)

    def text(self, render: bool) -> str:
        """The text served for this page, with its header lines."""
        headers = [f"Kind: {self.kind}"]
        if self.published:
            headers.append(f"Published: {self.published}")
        if self.effective:
            headers.append(f"Effective: {self.effective}")
        prose = self.browser if (render and self.browser is not None) else self.served
        return "\n".join(headers) + "\n\n" + prose


def _pages(*variants: Page) -> list[Page]:
    """A URL's page variants, one per epoch range."""
    return list(variants)


ABOUT = "https://acme-robotics.test/about"
HQ = "https://acme-robotics.test/hq"
TEAM = "https://acme-robotics.test/team"
LEGAL = "https://acme-robotics.test/legal"
CAREERS = "https://acme-robotics.test/careers"
REGISTER = "https://companies-register.test/entity/acme"
BIZDATA = "https://bizdata.test/acme"
NEWS_CEO = "https://news.test/acme-appoints"
NEWS_CTO = "https://news.test/acme-cto"

#: domain -> {url: [Page, ...]}; the first variant whose epochs hold is served.
PAGES: dict[str, dict[str, list[Page]]] = {
    ACME: {
        ABOUT: _pages(Page(kind="own", published="2026-01-10T00:00:00+00:00",
                           epochs=(0, 1, 2, 3, 4, 5),
                           served=("Acme Robotics was founded in 2014.\n"
                                   "Acme Robotics has raised $12M."))),
        HQ: _pages(
            Page(kind="own", published="2026-01-10T00:00:00+00:00", epochs=(0,),
                 served="Acme Robotics is headquartered in Lisbon."),
            Page(kind="own", published="2026-02-01T00:00:00+00:00", epochs=(1,),
                 served="Acme Robotics is headquartered in Porto."),
            Page(kind="own", published="2026-02-01T00:00:00+00:00", epochs=(2,),
                 effective="2026-02-15",
                 served="Acme Robotics keeps its offices open across the region."),
            Page(kind="own", published="2026-01-10T00:00:00+00:00", epochs=(3, 4),
                 effective="2020-01-01",
                 served="Acme Robotics is headquartered in Braga."),
            Page(kind="own", published="2026-03-01T00:00:00+00:00", epochs=(5,),
                 served="Acme Robotics is headquartered in Faro."),
        ),
        TEAM: _pages(
            Page(kind="own", published="2026-01-10T00:00:00+00:00", epochs=(0, 1, 2, 3),
                 served="Dana Ruiz has led Acme Robotics as chief executive since 2021.\n"
                        "Acme Robotics CFO: Dana Ruiz.\n"
                        "Acme Robotics CTO: Ben Okafor."),
            Page(kind="own", published="2026-03-01T00:00:00+00:00", epochs=(4,),
                 served="Dana Ruiz has led Acme Robotics as chief executive since 2021.\n"
                        "Acme Robotics CFO: Ana Silva.\n"
                        "Acme Robotics CTO: Ben Okafor."),
            Page(kind="own", published="2026-03-01T00:00:00+00:00", epochs=(5,),
                 served="Nadia Varga has led Acme Robotics as chief executive since 2021.\n"
                        "Acme Robotics CFO: Ana Silva.\n"
                        "Acme Robotics CTO: Lee Park."),
        ),
        LEGAL: _pages(Page(kind="own", published="2026-01-10T00:00:00+00:00",
                           epochs=(0, 1, 2, 3, 4, 5),
                           served="Acme Robotics trades under the registered name "
                                  "Acme Robotics, Inc.")),
        CAREERS: _pages(Page(kind="own", published="2026-01-10T00:00:00+00:00",
                             searchable=False, epochs=(0, 1, 2, 3, 4, 5),
                             served="Acme Robotics employs about 120 people across three "
                                    "offices.")),
        REGISTER: _pages(Page(kind="filing", published="2025-04-14T09:30:00+00:00",
                              searchable=False, epochs=(0, 1, 2, 3, 4, 5),
                              served="Acme Robotics was incorporated in 2014.")),
        BIZDATA: _pages(Page(kind="aggregator", published="2025-12-01T00:00:00+00:00",
                             epochs=(0, 1, 2, 3, 4, 5),
                             served="Acme Robotics CEO: Sam Ortiz.")),
        NEWS_CEO: _pages(Page(kind="news", published="2026-02-20T00:00:00+00:00",
                              epochs=(0, 1, 2, 3, 4, 5),
                              served="Acme Robotics CEO: Priya Nair.")),
        NEWS_CTO: _pages(Page(kind="news", published="2026-02-20T00:00:00+00:00",
                              epochs=(0, 1, 2, 3, 4, 5),
                              served="Acme Robotics CTO: Lee Park.")),
    },
    "second-company.test": {
        "https://second-company.test/": _pages(Page(
            kind="own", epochs=(0, 1, 2, 3, 4, 5),
            served="Second Company makes nothing anyone would write about.")),
    },
    "rival-works.test": {
        "https://rival-works.test/about": _pages(Page(
            kind="own", epochs=(0, 1, 2, 3, 4, 5),
            served="Rival Works was founded in 2011 and is based in Ghent.")),
        "https://directory.test/rival-works": _pages(Page(
            kind="aggregator", published="2026-01-02T00:00:00+00:00", epochs=(0, 1, 2, 3, 4, 5),
            served="Rival Works was founded in 1911 according to our records.")),
    },
}


def _noise(domain: str, host: str, count: int, kind: str = "own") -> dict:
    """`count` pages that support no field, for a corpus wide enough to test the budget."""
    name = domain.split(".")[0].replace("-", " ")
    return {f"https://{host}/page-{index:02d}": _pages(Page(
        kind=kind, epochs=(0, 1, 2, 3, 4, 5),
        served=f"{name} publishes note {index} about its day-to-day work."))
        for index in range(1, count + 1)}


#: A company with more pages than the budget can read: a value only a late search
#: result states, a value only a research citation states, and a page whose served
#: text disagrees with the browser's.
PAGES["wide-works.test"] = {
    **_noise("wide-works.test", "wide-works.test", 10, "own"),
    **_noise("wide-works.test", "wire.test", 10, "news"),
    "https://wide-works.test/stale": _pages(Page(
        kind="own", epochs=(0, 1, 2, 3, 4, 5),
        served="Wide Works is led by chief executive Stale Person.",
        browser="Wide Works is led by chief executive Browser Person.")),
    "https://zz-late.test/wide-profile": _pages(Page(
        kind="news", published="2026-01-01T00:00:00+00:00", epochs=(0, 1, 2, 3, 4, 5),
        served="Wide Works is led by chief executive Late Page Person.")),
    "https://filings.test/wide-works": _pages(Page(
        kind="filing", published="2025-06-01T00:00:00+00:00", searchable=False,
        epochs=(0, 1, 2, 3, 4, 5),
        served="Wide Works trades under the registered name Wide Works Ltd.")),
}

#: A company with more value-bearing pages than 80 units can render: the cited page must
#: still be read first, and the pages at the end of the queue cannot be.
_PEOPLE = ("Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
           "India", "Juliett")
PAGES["huge-group.test"] = {
    **{f"https://huge-group.test/p{index:02d}": _pages(Page(
        kind="own", epochs=(0, 1, 2, 3, 4, 5),
        served=f"Huge Group is led by chief executive Owner {_PEOPLE[index - 1]}."))
       for index in range(1, 11)},
    **{f"https://wire.test/h{index:02d}": _pages(Page(
        kind="news", epochs=(0, 1, 2, 3, 4, 5),
        served=f"Huge Group is led by chief executive Wire {_PEOPLE[index - 1]}."))
       for index in range(1, 11)},
    "https://filings.test/huge-group": _pages(Page(
        kind="filing", published="2025-06-01T00:00:00+00:00", searchable=False,
        epochs=(0, 1, 2, 3, 4, 5),
        served="Huge Group trades under the registered name Huge Group Holdings Ltd.")),
}

#: domain -> research answer per epoch: (field, value, url, excerpt)
RESEARCH = {
    ACME: {str(epoch): [("founded_year", 2014, REGISTER,
                         "Acme Robotics was incorporated in 2014."),
                        ("headcount_range", "51-200", CAREERS,
                         "Acme Robotics employs about 120 people across three offices.")]
           for epoch in range(6)},
    "wide-works.test": {"0": [
        ("legal_name", "Wide Works Ltd", "https://filings.test/wide-works",
         "Wide Works trades under the registered name Wide Works Ltd.")]},
    "huge-group.test": {"0": [
        ("legal_name", "Huge Group Holdings Ltd", "https://filings.test/huge-group",
         "Huge Group trades under the registered name Huge Group Holdings Ltd.")]},
}

#: The epoch the fake serves: what the organizer's mock calls its epoch.
EPOCH = 0

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
    """Clear the injected faults, the attempt counts, the delay, the epoch and the log."""
    global DELAY, LIMIT, EPOCH  # pylint: disable=global-statement
    FAULTS.clear()
    TIMEOUTS.clear()
    LOG.clear()
    _attempts.clear()
    DELAY = 0.0
    LIMIT = 0
    EPOCH = 0


def spent() -> int:
    """What the provider says these calls cost, the way a provider's own log would."""
    return sum(entry["cost"] for entry in LOG)


def page_at(domain: str, url: str, epoch: int | None = None) -> Page | None:
    """The page variant `domain` serves at `url` in `epoch` (the current one by default)."""
    epoch = EPOCH if epoch is None else epoch
    for variant in PAGES.get(domain, {}).get(url, []):
        if epoch in variant.epochs:
            return variant
    return None


def served_pages(domain: str, epoch: int | None = None) -> dict[str, Page]:
    """Every page `domain` serves in `epoch`, by URL."""
    found = {}
    for url in PAGES.get(domain, {}):
        variant = page_at(domain, url, epoch)
        if variant is not None:
            found[url] = variant
    return found


def _snippet(page: Page) -> str:
    """The lite search snippet: the first characters of the page's served prose."""
    return page.text(render=False).partition("\n\n")[2][:300]


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
            FakeResult(url=url, description=_snippet(page))
            for url, page in served_pages(domain).items() if page.searchable])

    def _invoke_extract(self, url: str, render: bool = False):
        cost = providers.RENDER_COST if render else providers.EXTRACT_COST
        _gate("extract", (url, render), cost, render)
        for domain in PAGES:
            page = page_at(domain, url)
            if page is not None:
                _attempted("extract", url, cost, 200, render)
                return FakeExtract(documents=[FakeDocument(content=page.text(render))])
        _attempted("extract", url, cost, 200, render)
        return FakeExtract(documents=[])

    def _invoke_research(self, query: str):
        cost = providers.RESEARCH_COST
        _gate("research", (query,), cost)
        _attempted("research", query, cost, 200)
        domain = _named(query)
        claims = RESEARCH.get(domain, {}).get(str(EPOCH), []) if domain else []
        structured = {name: None for name in providers.FIELDS}
        for field, value, _, _ in claims:
            structured[field] = value
        return FakeResearch(structured=structured,
                            citations=[FakeCitation(url=url, field=f"$.{field}", snippet=excerpt)
                                       for field, _, url, excerpt in claims])
