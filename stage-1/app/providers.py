"""Every provider call, through WebRouter (§2, §9).

The service reaches `nimble` only through the `web_router` package: one router
built from the environment the deployment provides, and one wrapper per call
that charges the call's units against a build's budget before making it.

Nimble alone is enough for every build. Tavily is never called, so its absence
cannot prevent research.
"""
from __future__ import annotations

import threading
import time

PROVIDER = "nimble"
SEARCH_COST = {"lite": 1, "fast": 5, "deep": 15}
EXTRACT_COST = 1
RESEARCH_COST = 25
MAX_RESULTS = 10
#: A search may wait 30 s before the service gives up on it (live providers are slow).
CALL_TIMEOUT = 30.0
#: A live research run takes about a minute, and the build must end within 300 s.
RESEARCH_TIMEOUT = 180.0
#: No new call starts after this many seconds: the whole build ends well inside 300 s.
BUILD_BUDGET_SECONDS = 240.0

FIELDS = ("legal_name", "hq_city", "ceo", "founded_year", "headcount_range", "funding_total")
SCHEMA = {
    "type": "object",
    "properties": {
        "legal_name": {"type": "string", "description": "the company's registered name"},
        "hq_city": {"type": "string", "description": "the city of its headquarters"},
        "ceo": {"type": "string", "description": "the full name of its chief executive"},
        "founded_year": {"type": "integer", "description": "the year it was founded"},
        "headcount_range": {"type": "string", "description": "its headcount range"},
        "funding_total": {"type": "integer",
                          "description": "total equity funding raised, in whole US dollars"},
    },
    "required": list(FIELDS),
}

_router_lock = threading.Lock()
_router = None


class RouterUnavailable(RuntimeError):
    """WebRouter could not be constructed; no provider call is possible."""


def router():
    """The process's one WebRouter, over `nimble` and whatever the environment points it at."""
    global _router  # pylint: disable=global-statement
    with _router_lock:
        if _router is None:
            try:
                from web_router import WebRouter  # pylint: disable=import-outside-toplevel
            except Exception as exc:  # noqa: BLE001 - a missing package is a build failure, not a 5xx
                raise RouterUnavailable(str(exc)) from exc
            _router = WebRouter(PROVIDER)
        return _router


def refused(error: BaseException) -> bool:
    """Whether a provider refused a call with an error status, which costs nothing (§9).

    A call whose answer did not arrive -- a timeout, a dropped connection -- keeps
    its cost, and so does an extract of a page the provider has no answer for.
    """
    status = getattr(error, "status_code", None)
    kind = getattr(error, "kind", None)
    if not isinstance(status, int) or status < 400:
        return False
    return kind != "target"


class Budget:
    """One build's spending, never above §9's 80 units, charged before each call."""

    def __init__(self, limit: int, on_spend) -> None:
        self.limit = limit
        self.on_spend = on_spend
        self.spent = 0
        self._lock = threading.Lock()

    def reserve(self, units: int) -> bool:
        """Claim `units` for a call; False when the call would take spending over the limit."""
        with self._lock:
            if self.spent + units > self.limit:
                return False
            self.spent += units
        self.on_spend(units)
        return True

    def refund(self, units: int) -> None:
        """Give back the units of a call the provider refused."""
        with self._lock:
            self.spent -= units
        self.on_spend(-units)


class Calls:
    """The three provider calls a build makes, each paid for before it is sent."""

    def __init__(self, budget: Budget, seconds: float = BUILD_BUDGET_SECONDS) -> None:
        self.budget = budget
        self.succeeded = False
        self.deadline = time.monotonic() + seconds
        self._lock = threading.Lock()

    def _note(self, success: bool) -> None:
        if success:
            with self._lock:
                self.succeeded = True

    def _ready(self) -> bool:
        """Whether the build may start one more call inside §8's 300-second bound."""
        return time.monotonic() < self.deadline

    def search(self, query: str, depth: str = "lite") -> list[dict] | None:
        """One search; its results as {url, title, snippet}, or None when it failed."""
        cost = SEARCH_COST[depth]
        if not self._ready() or not self.budget.reserve(cost):
            return None
        try:
            response = router().search(query, provider=PROVIDER, max_results=MAX_RESULTS,
                                       search_depth=depth, data_apis=False,
                                       timeout=CALL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - a provider failure is a failed call, not a 5xx
            if refused(exc):
                self.budget.refund(cost)
            return None
        self._note(True)
        return [{"url": result.url, "title": result.title,
                 "snippet": result.description or result.content} for result in response.results]

    def extract(self, url: str) -> str | None:
        """One page's full served text, or None when the provider returned no page."""
        cost = EXTRACT_COST
        if not self._ready() or not self.budget.reserve(cost):
            return None
        try:
            # §9: an extract through nimble does not set render.
            response = router().extract([url], provider=PROVIDER, format="markdown",
                                        timeout=CALL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - a provider failure is a failed call, not a 5xx
            if refused(exc):
                self.budget.refund(cost)
            return None
        document = response.documents[0] if response.documents else None
        if document is None or not document.content:
            return None
        self._note(True)
        return document.content

    def research(self, query: str) -> tuple[dict, list] | None:
        """One nimble research run over all six fields: (structured answer, citations)."""
        cost = RESEARCH_COST
        if not self._ready() or not self.budget.reserve(cost):
            return None
        try:
            response = router().research(query, provider=PROVIDER, output_schema=SCHEMA,
                                         effort="low", timeout=RESEARCH_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - a provider failure is a failed call, not a 5xx
            if refused(exc):
                self.budget.refund(cost)
            return None
        self._note(True)
        structured = response.structured if isinstance(response.structured, dict) else {}
        return structured, list(response.citations)


def research_query(domain: str) -> str:
    """A research request that names the company by its domain and asks for the six fields."""
    return (f"Find the company profile for {domain}: its legal name, headquarters city, "
            f"chief executive officer, founding year, headcount range and total funding raised.")
