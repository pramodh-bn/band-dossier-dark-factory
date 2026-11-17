"""One build: the calls it makes, what it spends, and the six fields it decides.

A build gathers evidence -- the full text of every page a search returns, and the
claims a nimble research run answers with -- and settles each field from the
sentences Appendix A proves. It never makes a provider call for a read, never
spends past §9's 80 units, and records nothing after a reset or import.
"""
from __future__ import annotations

import concurrent.futures
import threading

from . import evidence, providers
from .domain import company_name

BUDGET_UNITS = 80
#: Pages that are not searchable surface only through a research citation's URL.
EXTRACT_LIMIT = 34
WORKERS = 8
MAX_CITATIONS = 6


def queries(domain: str) -> list[str]:
    """The searches one build makes: the first is exactly the domain, the rest name the
    company with other queries (searches return at most ten results each)."""
    name = company_name(domain)
    return [
        domain,
        f"{name} about contact",
        f"{domain} news press release",
        f"{name} company profile",
        f"{domain} funding raised investment total",
        f"{domain} about contact",
        f"{domain} awards recognition community",
        f"{name} news press release",
        f"{name} products services customers",
        f"{domain} solutions platform technology",
    ]


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


def decide(items: list[tuple[str, object, str, str]]) -> dict:
    """The six fields, from every (field, value, url, excerpt) the evidence supports.

    When the evidence supports several values for a field the field holds the one
    the most excerpts support, and cites only the excerpts that support it (§4).
    """
    grouped: dict[str, dict[object, list[dict]]] = {name: {} for name in evidence.FIELDS}
    order: dict[str, dict[object, int]] = {name: {} for name in evidence.FIELDS}
    for field, value, url, excerpt in items:
        citations = grouped[field].setdefault(value, [])
        order[field].setdefault(value, len(order[field]))
        citation = {"url": url, "excerpt": excerpt, "provider": providers.PROVIDER}
        if citation not in citations:
            citations.append(citation)
    fields = {}
    for name in evidence.FIELDS:
        values = grouped[name]
        if not values:
            fields[name] = {"value": None, "status": "unknown", "citations": []}
            continue
        best = min(values, key=lambda value: (-len(values[value]), order[name][value]))
        citations = values[best][:MAX_CITATIONS]
        fields[name] = {"value": best, "status": "known", "citations": citations}
    return fields


class Builder:
    """Starts one background build per request that begins one."""

    def __init__(self, store) -> None:
        self.store = store

    def start(self, dossier, generation: int, when: str) -> None:
        """Run the build for `dossier` on its own thread."""
        threading.Thread(target=self._run, args=(dossier, generation, when),
                         name=f"build-{dossier.domain}", daemon=True).start()

    def _run(self, dossier, generation: int, when: str) -> None:
        """Gather, decide, and record the outcome -- unless a reset superseded this build."""
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
        pages: dict[str, str] = {}
        urls: list[str] = []
        claims: list[tuple[str, object, str, str]] = []
        found: set[str] = set()
        extracted: set[str] = set()
        counter = threading.Lock()

        def remember(url: str) -> bool:
            """Keep this URL for extraction; False once the build has left its extract budget."""
            with counter:
                if url in extracted or len(extracted) >= EXTRACT_LIMIT:
                    return False
                extracted.add(url)
                return True

        def gather(url: str) -> None:
            if not self.store.still_current(dossier, generation) or not remember(url):
                return
            text = calls.extract(url)
            if text is not None:
                pages[url] = text

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS + 1) as pool:
            research = pool.submit(calls.research, providers.research_query(domain))
            for query in queries(domain):
                if not self.store.still_current(dossier, generation):
                    return
                results = calls.search(query)
                if not results:
                    continue
                for result in results:
                    url = result.get("url")
                    if isinstance(url, str) and url and url not in found:
                        found.add(url)
                        urls.append(url)
            # Own pages first, then the rest, so a cap on extracts costs the least.
            for url in sorted(urls, key=lambda url: (not url.startswith("https://" + domain),
                                                     url)):
                if self.store.still_current(dossier, generation):
                    pool.submit(gather, url)
            try:
                answer = research.result()
            except Exception:  # noqa: BLE001 - a research failure is a failed call
                answer = None
            if answer is not None and self.store.still_current(dossier, generation):
                structured, citations = answer
                for citation in citations:
                    field = (citation.field or "").removeprefix("$.")
                    if field not in evidence.FIELDS:
                        continue
                    value = _claimed(field, structured.get(field), citation.snippet, domain)
                    if value is not None and citation.url:
                        claims.append((field, value, citation.url,
                                       " ".join(citation.snippet.split())))
                    if citation.url:
                        pool.submit(gather, citation.url)

        items: list[tuple[str, object, str, str]] = []
        for url in sorted(pages):
            for field, pairs in evidence.page_values(pages[url], domain).items():
                for value, excerpt in pairs:
                    items.append((field, value, url, excerpt))
        items.extend(claims)
        self.store.finish_build(dossier, generation, calls.succeeded, decide(items), when)
