"""Page kind and publication time (§2, "Page kind and publication time").

Two header lines carry requirements from this stage: `Kind` names the page's
kind, `Published` when it was published. A page without a usable `Kind` is the
company's own site when its host is the company's domain, and an aggregator
otherwise.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from . import evidence
from .domain import normalised

KINDS = ("own", "filing", "news", "aggregator")
AUTHORITY = {"own": 2, "filing": 2, "news": 1, "aggregator": 0, None: 0}
TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


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
    return normalised(urlsplit(url).hostname or "")


def kind_of(text: str, url: str, domain: str) -> str:
    """The page's kind: its `Kind` line when it names one of the four, else by host."""
    stated = headers(text).get("kind", "").strip()
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
