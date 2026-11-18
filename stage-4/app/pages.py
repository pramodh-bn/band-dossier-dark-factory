"""Page kind, publication time and lineage (§2-§4).

A page's header lines name its kind, when it was published, and from stage 4 who
publishes it (`Registrant`), the URL it copies (`Canonical`) and the domain that
first published its material (`First-Published-By`). A page that states `Kind: own`
but is not registered to the company is a lookalike: it is an aggregator for the
selection rule, and the company's own domain is its origin.

`origin` follows the first applicable rule: the first publisher, the original's
origin through at most three `Canonical` links (the URL's host where no provider
serves the original), the company's domain for a lookalike, else the registrant.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from . import evidence
from .domain import InvalidDomain, normalised, normalised_or_raise

KINDS = ("own", "filing", "news", "aggregator")
AUTHORITY = {"own": 2, "filing": 2, "news": 1, "aggregator": 0, None: 0}
TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_CANONICAL_HOPS = 3


def headers(text: str) -> dict[str, str]:
    """A page text's header lines as {lowercased name: value}."""
    found: dict[str, str] = {}
    for line in evidence.split_page(text)[0]:
        name, separator, value = line.partition(":")
        if separator:
            found[name.strip().lower()] = value.strip()
    return found


def host_of(url: str) -> str:
    """A URL's host, normalised as §4 of stage 1 normalises a domain."""
    try:
        return normalised(urlsplit(url).hostname or "")
    except ValueError:
        return ""


def stated_domain(value, fallback: str | None = None) -> str | None:
    """A header's domain, normalised (§4), or `fallback` when it is absent or invalid."""
    if not isinstance(value, str):
        return fallback
    try:
        return normalised_or_raise(value)
    except InvalidDomain:
        return fallback


def registrant(text: str, url: str) -> str:
    """The domain whose owner publishes the page: its `Registrant`, else its URL's host."""
    return stated_domain(headers(text).get("registrant"), host_of(url))


def first_published_by(text: str) -> str | None:
    """The page's `First-Published-By` domain, normalised, or None."""
    return stated_domain(headers(text).get("first-published-by"))


def canonical(text: str) -> str | None:
    """The page's `Canonical` URL, when it names a URL with a host."""
    value = headers(text).get("canonical")
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return value if host_of(value) else None


def is_lookalike(text: str, url: str, domain: str) -> bool:
    """A page that states `Kind: own` and is not registered to its own URL's host."""
    stated = headers(text).get("kind", "").strip()
    return stated == "own" and registrant(text, url) != host_of(url)


def kind_of(text: str, url: str, domain: str) -> str:
    """The page's kind for the selection rule (§2, §4, Own pages).

    A page that states `Kind: own` is `own` only when its registrant is the
    company's domain; any other such page is an `aggregator`. A page without a
    usable `Kind` is the company's site when its URL's host is the company's
    domain, and an aggregator otherwise.
    """
    stated = headers(text).get("kind", "").strip()
    if stated == "own":
        return "own" if registrant(text, url) == domain else "aggregator"
    if stated in KINDS:
        return stated
    return "own" if host_of(url) == domain else "aggregator"


def instant(stated) -> str | None:
    """An RFC 3339 timestamp with an explicit offset, normalised, or None.

    A bare date, a time without an offset, an impossible date or prose is not a
    timestamp: a candidate's `published_at` must always be an RFC 3339 instant
    with an offset.
    """
    if not isinstance(stated, str) or not TIMESTAMP.fullmatch(stated.strip()):
        return None
    try:
        moment = datetime.fromisoformat(stated.strip().upper().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.isoformat()


def published_of(text: str) -> str | None:
    """The page's publication time as an RFC 3339 timestamp with an offset, or None."""
    return instant(headers(text).get("published", ""))


def metadata(text: str, url: str, domain: str) -> dict:
    """The kind and publication time a candidate carries for this page."""
    return {"kind": kind_of(text, url, domain), "published_at": published_of(text)}


def true_from(text: str) -> str | None:
    """The date from which the page says its facts hold (§3, Two times per value).

    A valid `Effective: <YYYY-MM-DD>` line wins; otherwise the date part, in UTC, of
    the page's `published_at`; with neither, there is no date.
    """
    stated = headers(text).get("effective", "").strip()
    if DATE.fullmatch(stated):
        try:
            return date.fromisoformat(stated).isoformat()
        except ValueError:
            pass
    published = published_of(text)
    if published is None:
        return None
    return datetime.fromisoformat(published).astimezone(timezone.utc).date().isoformat()


class Lineage:
    """Resolves the origin of pages, fetching the originals rule 2 needs.

    `texts` holds the text of every page already read; `fetch(url)` obtains one more
    page through a provider (or None when no provider serves it or the budget does
    not allow the call). A fetched original is kept in `texts` so it is fetched at
    most once, and it is used only for lineage: evidence is still only what the
    build's searches and research cite.
    """

    def __init__(self, texts: dict[str, str], fetch, company: str) -> None:
        self.texts = texts
        self.fetch = fetch
        self.company = company

    def text_at(self, url: str) -> str | None:
        """The text at `url`, from the build's pages or one fetched original."""
        if url in self.texts:
            return self.texts[url]
        fetched = self.fetch(url)
        if fetched is not None:
            self.texts[url] = fetched
        return fetched

    def origin(self, url: str) -> str:
        """The origin of a page this build read, by §4's rules."""
        return self._origin(url, self.texts[url], 0, set())

    def _origin(self, url: str, text: str, hops: int, seen: set[str]) -> str:
        if url in seen:  # a cycle of copies: no original, so the host stands in
            return host_of(url)
        seen = {*seen, url}
        first = first_published_by(text)
        if first is not None:
            return first
        target = canonical(text)
        if target is not None and target != url:
            if hops >= MAX_CANONICAL_HOPS:
                return host_of(target)
            target_text = self.text_at(target)
            if target_text is None:
                return host_of(target)
            return self._origin(target, target_text, hops + 1, seen)
        if is_lookalike(text, url, self.company):
            return self.company
        return registrant(text, url)
