"""One build: the calls it makes, what it spends, and the candidates it keeps (§2).

A build gathers browser-visible page text -- a rendered extract through Nimble,
which is the text a reader sees once the page has loaded -- plus the claims a
Nimble research run answers with. Every value a page's text supports becomes a
candidate; the selection rule then picks one per field.

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
#: Searches one build makes: the first is exactly the domain, the rest name the company.
#: Seven reach essentially every page the corpus serves (a search returns at most ten),
#: and leave the budget for the rendered extracts that carry the values.
QUERIES = 7


def queries(domain: str) -> list[str]:
    """The searches one build makes: the first is exactly the domain."""
    name = company_name(domain)
    return [
        domain,
        f"{name} about contact",
        f"{domain} news press release",
        f"{name} company profile",
        f"{domain} funding raised investment total",
        f"{domain} about contact",
        f"{domain} awards recognition community",
    ][:QUERIES]


def _claimed(field: str, claimed, excerpt: str, domain: str):
    """The value a research claim asserts, when its own citation supports exactly that value."""
    if not isinstance(excerpt, str) or not excerpt.strip():
        return None
    supported = evidence.supported_value(" ".join(excerpt.split()), field, domain)
    if supported is None:
        return None
    if isinstance(supported, str):
        if not isinstance(claimed, str) or claimed.strip() != supported:
            return None
    elif isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
        return None
    elif int(claimed) != supported:
        return None
    return supported


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
        seen: set[str] = set()
        claims: list[tuple[str, object, str, str]] = []

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

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS + 1) as pool:
            research = pool.submit(calls.research, providers.research_query(domain))
            pending = [pool.submit(search, query) for query in queries(domain)]
            for future in pending:
                if not self.store.still_current(dossier, generation):
                    return
                for result in future.result() or []:
                    url = result.get("url")
                    if isinstance(url, str) and url and url not in seen:
                        seen.add(url)
                        found.append(url)
            try:
                answer = research.result()
            except Exception:  # noqa: BLE001 - a research failure is a failed call
                answer = None
            cited: list[str] = []
            if answer is not None and self.store.still_current(dossier, generation):
                structured, citations = answer
                for citation in citations:
                    if not citation.url or citation.url in cited:
                        continue
                    cited.append(citation.url)
                    field = (citation.field or "").removeprefix("$.")
                    if field not in evidence.FIELDS:
                        continue
                    value = _claimed(field, structured.get(field), citation.snippet, domain)
                    if value is not None:
                        claims.append((field, value, citation.url,
                                       " ".join(citation.snippet.split())))
            # A page only a citation names first: it is usually the filing that no search
            # returns. Then the company's own pages, then the rest.
            order = cited + sorted(
                (url for url in found if url not in cited),
                key=lambda url: (pages.host_of(url) != domain, url))
            for url in order:
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
        for name, value, url, excerpt in claims:
            if url in page_text:
                continue  # the page's own text already carries every value it supports
            rows[name].append({"value": value, "url": url, "excerpt": excerpt,
                               "provider": providers.PROVIDER,
                               "kind": "own" if pages.host_of(url) == domain
                               else "aggregator",
                               "published_at": None, "retrieved_at": when,
                               "selected": False})
        fields = {name: candidates.field(rows[name]) for name in evidence.FIELDS}
        self.store.finish_build(dossier, generation, calls.succeeded, fields, when)
