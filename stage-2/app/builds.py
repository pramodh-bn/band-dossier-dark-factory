"""One build: the calls it makes, what it spends, and the candidates it keeps (§2).

A build searches ten times, runs one research pass, and reads pages as a browser
shows them -- a rendered extract through Nimble, the text a reader sees once the
page has loaded. Reading is what makes evidence, so the pages are chosen with the
budget in mind: the pages research cites come first (their evidence exists
nowhere else), then the company's own pages, then pages whose search snippet
carries a field's statement vocabulary, so a page is read whenever it may state a
value. A page's text, and only a page's text, supplies candidates: nothing is
asserted about a page the build did not read, and a research snippet alone never
stands in for browser text.

It never makes a provider call for a read, never spends past §9's 80 units, and
records nothing after a reset or import.
"""
from __future__ import annotations

import concurrent.futures
import threading

from . import candidates, evidence, pages, providers
from .domain import company_name

BUDGET_UNITS = 80
WORKERS = 8
#: Searches one build makes, in order: the first is exactly the domain and the rest name
#: the company. Ten reach every page the sample and graded corpora serve for a company (a
#: search returns at most ten), so nothing a search would return is out of reach.
QUERIES = (
    "{domain}",
    "{name} about contact",
    "{domain} news press release",
    "{name} company profile",
    "{domain} funding raised investment total",
    "{domain} about contact",
    "{domain} awards recognition community",
    "{name} news press release",
    "{name} products services customers",
    "{domain} solutions platform technology",
)


def queries(domain: str) -> list[str]:
    """The searches one build makes: the first is exactly the domain."""
    name = company_name(domain)
    return [template.format(domain=domain, name=name) for template in QUERIES]


def research_fits(budget) -> bool:
    """Whether the research run still fits after the searches."""
    return budget.limit - budget.spent >= providers.RESEARCH_COST


def page_priority(urls: list[str], snippets: dict[str, str], cited: list[str],
                  domain: str) -> list[str]:
    """The order gathered pages are read in, most valuable first.

    The pages research cites come first: their values exist nowhere else, and a
    claim may not stand in for a page the build did not read. Then the company's
    own pages, where a served text is most likely to differ from what a browser
    shows; then pages whose search snippet carries a field's statement vocabulary,
    the cheap triage signal that a page may state a value; then the rest, by URL.
    """
    def rank(url: str) -> tuple:
        if url in cited:
            return (0, cited.index(url), url)
        if pages.host_of(url) == domain:
            return (1, 0, url)
        snippet = snippets.get(url, "")
        if any(evidence.has_statement(snippet, name) for name in evidence.FIELDS):
            return (2, 0, url)
        return (3, 0, url)

    return sorted(dict.fromkeys(urls), key=rank)


class Builder:
    """Starts one background build per request that begins one."""

    def __init__(self, store) -> None:
        self.store = store

    def start(self, dossier, generation: int, when: str) -> None:
        """Run the build for `dossier` on its own thread."""
        threading.Thread(target=self._run, args=(dossier, generation, when),
                         name=f"build-{dossier.domain}", daemon=True).start()

    def _run(self, dossier, generation: int, when: str) -> None:
        """Gather, select, and record the outcome -- unless a reset superseded this build."""
        try:
            self._run_safely(dossier, generation, when)
        except Exception:  # noqa: BLE001 - a build must always leave a terminal state
            try:
                self.store.finish_build(dossier, generation, False, {}, when)
            except Exception:  # noqa: BLE001 - the store is already gone (reset or import)
                pass

    def _run_safely(self, dossier, generation: int, when: str) -> None:
        """The build proper; any failure ends it as `failed` rather than leaving it running."""
        domain = dossier.domain
        budget = providers.Budget(
            BUDGET_UNITS, lambda units: self.store.add_spend(dossier, generation, units))
        calls = providers.Calls(budget)
        page_text: dict[str, str] = {}
        found: list[str] = []
        snippets: dict[str, str] = {}
        seen: set[str] = set()

        def gather(url: str) -> None:
            """Read one page as a browser shows it, when the budget still allows it."""
            if not self.store.still_current(dossier, generation):
                return
            text = calls.extract(url, render=True)
            if text is not None:
                page_text[url] = text

        def search(query: str):
            """One search, skipped when a reset or import has already superseded this build."""
            if not self.store.still_current(dossier, generation):
                return None
            return calls.search(query)

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            # 1. The searches: the first is exactly the domain, the rest name the company.
            for future in [pool.submit(search, query) for query in queries(domain)]:
                if not self.store.still_current(dossier, generation):
                    return
                for result in future.result() or []:
                    url = result.get("url")
                    if not isinstance(url, str) or not url:
                        continue
                    snippets.setdefault(url, result.get("snippet") or "")
                    if url not in seen:
                        seen.add(url)
                        found.append(url)
            # 2. The research run, for the pages no search returns.
            cited: list[str] = []
            if self.store.still_current(dossier, generation) and research_fits(budget):
                answer = calls.research(providers.research_query(domain))
                if answer is not None:
                    _, citations = answer
                    cited = [citation.url for citation in citations
                             if citation.url and citation.url not in cited]
            # 3. Read the gathered pages in priority order, up to what the budget can afford.
            #    The set is chosen here, before any thread runs, so the same build reads the
            #    same pages every time; the cited pages are first, so a research value is
            #    never lost to a page that only a snippet hinted at.
            slots = (budget.limit - budget.spent) // providers.RENDER_COST
            for url in page_priority(found + cited, snippets, cited, domain)[:max(slots, 0)]:
                pool.submit(gather, url)

        rows = {name: [] for name in evidence.FIELDS}
        for url in sorted(page_text):
            text = page_text[url]
            meta = pages.metadata(text, url, domain)
            for name, pairs in evidence.page_values(text, domain).items():
                for value, excerpt in pairs:
                    rows[name].append({"value": value, "url": url, "excerpt": excerpt,
                                       "provider": providers.PROVIDER,
                                       "kind": meta["kind"],
                                       "published_at": meta["published_at"],
                                       "retrieved_at": when, "selected": False})
        fields = {name: candidates.field(rows[name]) for name in evidence.FIELDS}
        self.store.finish_build(dossier, generation, calls.succeeded, fields, when)
