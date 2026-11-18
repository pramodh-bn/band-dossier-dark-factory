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

from . import evidence, pages, providers
from . import pages as pages_module
from .domain import company_name

BUDGET_UNITS = 80
WORKERS = 8
#: Units kept back from the page queue for the canonical originals lineage needs.
#: A build reads its value-bearing pages first and leaves this much for the one-unit
#: extracts that resolve a copy's original; a chain needing more still falls back to
#: the URL's host under the 80-unit cap.
LINEAGE_RESERVE = 6
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


def page_rank(url: str, snippets: dict[str, str], cited: list[str],
              domain: str) -> tuple:
    """One page's place in the read order: cited, own, triaged, then the rest."""
    if url in cited:
        return (0, cited.index(url), url)
    if pages.host_of(url) == domain:
        return (1, 0, url)
    snippet = snippets.get(url, "")
    if evidence.has_any_statement(snippet):
        return (2, 0, url)
    return (3, 0, url)


def page_priority(urls: list[str], snippets: dict[str, str], cited: list[str],
                  domain: str) -> list[str]:
    """The order gathered pages are read in, most valuable first.

    The pages research cites come first: their values exist nowhere else, and a
    claim may not stand in for a page the build did not read. Then the company's
    own pages, where a served text is most likely to differ from what a browser
    shows; then pages whose search snippet carries any field's statement vocabulary
    -- scalar, executive title or investor phrase -- the cheap triage signal that a
    page may state a value; then the rest, by URL.
    """
    return sorted(dict.fromkeys(urls),
                  key=lambda url: page_rank(url, snippets, cited, domain))


def planned_reads(ordered: list[str], snippets: dict[str, str], cited: list[str],
                  domain: str, remaining: int) -> list[str]:
    """The prefix of `ordered` a build reads, keeping lineage units back.

    Every cited, own or triaged page is read first. Only pages beyond that prefix
    give up slots for the one-unit lineage extracts; when the budget cannot even
    cover the value-bearing prefix, the prefix takes every slot it can.
    """
    hard = max(remaining // providers.RENDER_COST, 0)
    must = sum(1 for url in ordered
               if page_rank(url, snippets, cited, domain)[0] <= 2)
    spare = max(hard - must, 0)
    reserve_pages = min(spare, (LINEAGE_RESERVE + providers.RENDER_COST - 1)
                        // providers.RENDER_COST)
    slots = max(hard - reserve_pages, 0)
    if slots < must:
        slots = min(must, hard)
    return ordered[:slots]


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
            BUDGET_UNITS, lambda units: self.store.add_spend(dossier, generation, units),
            shared=self.store.shared_budget(dossier))
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
            ordered = page_priority(found + cited, snippets, cited, domain)
            for url in planned_reads(ordered, snippets, cited, domain,
                                     budget.limit - budget.spent):
                pool.submit(gather, url)

        # 4. Every page's values, then the origin of every page that states one
        #    (§4, Origins). A Canonical link to a page this build did not read is
        #    fetched through the same provider, within the units the page queue left
        #    back; a page no provider serves falls back to its host. Fetched originals
        #    answer lineage only: evidence is still what searches and research cite.
        page_data: dict[str, dict] = {}
        for url in sorted(page_text):
            text = page_text[url]
            meta = pages_module.metadata(text, url, domain)
            date = pages_module.true_from(text)
            found: list[dict] = []
            for name, pairs in evidence.page_values(text, domain).items():
                for value, excerpt in pairs:
                    found.append({"field": name, "value": value, "url": url,
                                  "excerpt": excerpt, "provider": providers.PROVIDER,
                                  "kind": meta["kind"],
                                  "published_at": meta["published_at"],
                                  "retrieved_at": when, "true_from": date,
                                  "learned_at": None, "selected": False})
            for title, pairs in evidence.page_executives(text, domain).items():
                for value, excerpt in pairs:
                    found.append({"field": "executives", "title": title, "value": value,
                                  "url": url, "excerpt": excerpt,
                                  "provider": providers.PROVIDER,
                                  "kind": meta["kind"],
                                  "published_at": meta["published_at"],
                                  "retrieved_at": when, "true_from": date,
                                  "learned_at": None, "selected": False})
            for value, excerpt in evidence.page_investors(text, domain):
                found.append({"field": "investors", "value": value, "url": url,
                              "excerpt": excerpt, "provider": providers.PROVIDER,
                              "kind": meta["kind"],
                              "published_at": meta["published_at"],
                              "retrieved_at": when, "true_from": date,
                              "learned_at": None, "selected": False})
            page_data[url] = {"true_from": date, "candidates": found}

        lineage_texts = dict(page_text)

        def fetch_lineage(url: str) -> str | None:
            if not self.store.still_current(dossier, generation):
                return None
            return calls.extract(url)

        lineage = pages_module.Lineage(lineage_texts, fetch_lineage, domain)
        origins = {url: lineage.origin(url) for url in sorted(page_data)
                   if page_data[url]["candidates"]}

        pages: dict[str, dict] = {}
        for url, data in page_data.items():
            origin = origins.get(url)
            if origin is not None:
                for candidate in data["candidates"]:
                    candidate["origin"] = origin
            # Every rendered page is a version, even one that states no value: an empty
            # version still ends or replaces the older versions of its URL (§3, /values).
            pages[url] = data
        # A batch build that the shared budget kept from even starting completes with
        # what it has; a build whose calls were all attempted and all failed is `failed`
        # (§4, Batches; §4, Provider failure).
        ready = calls.succeeded or (budget.blocked_by_shared and calls.attempted == 0)
        self.store.finish_build(dossier, generation, ready, pages, when,
                                budget.spent)
