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
#: An extract through nimble that loads the page in a browser costs 3 (§2, Web access).
RENDER_COST = 3
RESEARCH_COST = 25
MAX_RESULTS = 10
#: A search may wait 30 s before the service gives up on it (live providers are slow).
CALL_TIMEOUT = 30.0
#: A live research run takes about a minute, and the build must end within 300 s.
RESEARCH_TIMEOUT = 180.0
#: No new call starts after this many seconds: the whole build ends well inside 300 s.
BUILD_BUDGET_SECONDS = 240.0
#: A refused call gets at most this many attempts in total (the first try plus retries).
ATTEMPTS = 4
#: Seconds waited before attempt 2, 3, 4. The mock answers a repeat of a refused call
#: made less than a second earlier with another refusal, so the shortest wait clears 1 s.
BACKOFF = (0.0, 1.05, 1.6, 2.4)
#: A refusal with one of these statuses costs nothing and may be tried again.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Failures where no answer arrived: they keep their units, and are retried only while
#: another full call still fits in the build's time.
NO_ANSWER_KINDS = frozenset({"timeout", "upstream"})

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
    """One build's spending, never above §9's 80 units, charged before each call.

    A batch's build also spends from the batch's shared budget: a call is made only
    when both this build's 80 units and the batch's remaining units cover it.
    """

    def __init__(self, limit: int, on_spend, shared=None) -> None:
        self.limit = limit
        self.on_spend = on_spend
        self.shared = shared
        self.spent = 0
        #: True when a call was refused because the batch's shared budget was spent.
        self.blocked_by_shared = False
        self._lock = threading.Lock()

    def reserve(self, units: int) -> bool:
        """Claim `units` for a call; False when the call would take spending over a limit."""
        with self._lock:
            if self.spent + units > self.limit:
                return False
            if self.shared is not None and not self.shared.reserve(units):
                self.blocked_by_shared = True
                return False
            self.spent += units
        self.on_spend(units)
        return True

    def refund(self, units: int) -> None:
        """Give back the units of a call the provider refused.

        The batch's counted spend is given back by the same `on_spend` checkpoint
        that records the dossier and build refund, so a snapshot and the journal
        always agree about the batch too.
        """
        with self._lock:
            self.spent -= units
        self.on_spend(-units)


class Calls:
    """The three provider calls a build makes, each paid for before it is sent.

    A refusal that costs nothing -- a 429, or a transient 5xx -- is retried with a
    bounded backoff, because a provider's throttle or a single bad gateway answer
    must not turn a researchable company into a failed build. A call that never
    answered (a timeout or a dropped connection) keeps its units, and is retried
    only while the build can afford another full call. Every attempt is charged
    against the same 80-unit budget, so retries can never take a build past it.
    """

    def __init__(self, budget: Budget, seconds: float = BUILD_BUDGET_SECONDS) -> None:
        self.budget = budget
        self.succeeded = False
        #: How many provider calls were actually sent (a budget-blocked call is not one).
        self.attempted = 0
        self.deadline = time.monotonic() + seconds
        self._lock = threading.Lock()

    def _note(self, success: bool) -> None:
        if success:
            with self._lock:
                self.succeeded = True

    def _ready(self) -> bool:
        """Whether the build may start one more call inside §8's 300-second bound."""
        return time.monotonic() < self.deadline

    def _wait_before(self, attempt: int, delay: float | None = None) -> bool:
        """Sleep the backoff before `attempt` (1-based), or False when it would not fit."""
        if delay is None:
            delay = BACKOFF[attempt] if attempt < len(BACKOFF) else BACKOFF[-1]
        if delay <= 0:
            return True
        if self.deadline - time.monotonic() <= delay:
            return False
        time.sleep(delay)
        return True

    def _retryable(self, error: BaseException, attempt: int, refused_it: bool,
                   retry_no_answer: bool) -> bool:
        """Whether one more attempt can help: a refusal with a retryable status, or a call
        that never answered while the build still has room for another one."""
        if attempt + 1 >= ATTEMPTS:
            return False
        status = getattr(error, "status_code", None)
        if isinstance(status, int) and status in RETRY_STATUSES:
            return True
        if refused_it:
            return False
        if getattr(error, "kind", None) in NO_ANSWER_KINDS:
            return retry_no_answer and self.deadline - time.monotonic() > CALL_TIMEOUT
        return False

    def _call(self, cost: int, invoke, *, retry_no_answer: bool = True):
        """Run one provider call and its bounded retries; None when every attempt failed."""
        delay = 0.0
        for attempt in range(ATTEMPTS):
            if attempt and not self._wait_before(attempt, delay):
                return None
            if not self._ready() or not self.budget.reserve(cost):
                return None
            with self._lock:
                self.attempted += 1
            try:
                answer = invoke()
            except Exception as exc:  # noqa: BLE001 - a provider failure is a failed call, not a 5xx
                refused_it = refused(exc)
                if refused_it:
                    self.budget.refund(cost)
                if not self._retryable(exc, attempt, refused_it, retry_no_answer):
                    return None
                # Only a 429 requires a pause: the same call repeated within a second is
                # refused again. A transient 5xx may be tried again at once, so a build
                # whose calls all fail still ends inside §8's ten seconds.
                status = getattr(exc, "status_code", None)
                delay = BACKOFF[attempt] if status == 429 else 0.05
                continue
            self._note(True)
            return answer
        return None

    def _invoke_search(self, query: str, depth: str):
        """The provider's search answer for `query`."""
        return router().search(query, provider=PROVIDER, max_results=MAX_RESULTS,
                               search_depth=depth, data_apis=False, timeout=CALL_TIMEOUT)

    def _invoke_extract(self, url: str, render: bool = False):
        """The provider's extract answer for `url`, optionally the text a browser shows."""
        options = {"render": True} if render else None
        return router().extract([url], provider=PROVIDER, format="markdown",
                                provider_options=options, timeout=CALL_TIMEOUT)

    def _invoke_research(self, query: str):
        """The provider's research answer for `query`."""
        return router().research(query, provider=PROVIDER, output_schema=SCHEMA,
                                 effort="low", timeout=RESEARCH_TIMEOUT)

    def search(self, query: str, depth: str = "lite") -> list[dict] | None:
        """One search; its results as {url, title, snippet}, or None when it failed."""
        response = self._call(SEARCH_COST[depth],
                              lambda: self._invoke_search(query, depth))
        if response is None:
            return None
        return [{"url": result.url, "title": result.title,
                 "snippet": result.description or result.content} for result in response.results]

    def extract(self, url: str, render: bool = False) -> str | None:
        """One page's text -- the served text, or with `render` the text a browser shows
        once the page has loaded -- or None when the provider returned no page."""
        cost = RENDER_COST if render else EXTRACT_COST
        response = self._call(cost, lambda: self._invoke_extract(url, render))
        if response is None:
            return None
        document = response.documents[0] if response.documents else None
        if document is None or not document.content:
            return None
        return document.content

    def research(self, query: str) -> tuple[dict, list] | None:
        """One nimble research run over all six fields: (structured answer, citations).

        A research run bills its 25 units when it starts, so a run that started and
        then timed out is never restarted: only a refusal -- which cost nothing --
        is tried again.
        """
        response = self._call(RESEARCH_COST, lambda: self._invoke_research(query),
                              retry_no_answer=False)
        if response is None:
            return None
        structured = response.structured if isinstance(response.structured, dict) else {}
        return structured, list(response.citations)


def research_query(domain: str) -> str:
    """A research request that names the company by its domain and asks for the six fields."""
    return (f"Find the company profile for {domain}: its legal name, headquarters city, "
            f"chief executive officer, founding year, headcount range and total funding raised.")
