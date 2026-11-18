"""Candidates and the selection rule (§2, "Candidates", "The selection rule", "Status").

A candidate is one value for one field, supported by one excerpt of one page.
Ranking is total and deterministic -- authority, then recency, then URL, then
provider, then value -- so the same candidates always select the same value.
"""
from __future__ import annotations

from .pages import AUTHORITY, KINDS, instant

PROVIDERS = ("nimble", "tavily", "kernel", "browserbase")
PROVIDER_ORDER = {name: index for index, name in enumerate(PROVIDERS)}
FIELDS = ("legal_name", "hq_city", "ceo", "founded_year", "headcount_range", "funding_total")
CANDIDATE_KEYS = ("value", "url", "excerpt", "provider", "kind", "published_at",
                  "retrieved_at", "selected")


def canonical(value):
    """A value as the rule compares it: strings without surrounding whitespace."""
    return value.strip() if isinstance(value, str) else value


def same_value(one, other) -> bool:
    """Whether two values are the same value: equal as JSON, strings after stripping."""
    return canonical(one) == canonical(other) and type(canonical(one)) is type(canonical(other))


def _value_key(value) -> tuple:
    """Sort key for a value: integers by number, strings by code point after stripping."""
    value = canonical(value)
    if isinstance(value, str):
        return (1, value)
    return (0, value)


def _published_key(published_at) -> tuple:
    """Sort key for recency: a later time first, and no time last.

    Times are compared as parsed instants, because two RFC 3339 strings with
    different offsets can name the same moment.
    """
    if published_at is None:
        return (1, 0.0)
    from datetime import datetime  # noqa: PLC0415 - only needed when a time exists

    try:
        moment = datetime.fromisoformat(str(published_at).upper().replace("Z", "+00:00"))
    except ValueError:
        return (1, 0.0)
    return (0, -moment.timestamp())


def rank_key(candidate: dict) -> tuple:
    """The selection rule as a sort key, highest-ranked first (§2, "The selection rule")."""
    return (-AUTHORITY.get(candidate.get("kind"), 0),
            _published_key(candidate.get("published_at")),
            str(candidate.get("url", "")),
            PROVIDER_ORDER.get(candidate.get("provider"), len(PROVIDERS)),
            _value_key(candidate.get("value")))


def distinct(candidates: list[dict]) -> list:
    """The distinct values a field's candidates carry, in no particular order."""
    found: list = []
    for candidate in candidates:
        if not any(same_value(candidate["value"], value) for value in found):
            found.append(candidate["value"])
    return found


def dedupe(candidates: list[dict]) -> list[dict]:
    """One candidate per page and value, keeping the first excerpt found for it."""
    kept: list[dict] = []
    seen: set[tuple] = set()
    for candidate in candidates:
        value = canonical(candidate.get("value"))
        key = (candidate.get("url"), repr(value))
        if key in seen:
            continue
        seen.add(key)
        kept.append(candidate)
    return kept


def field(candidates: list[dict]) -> dict:
    """A field's value, status, citations and ordered candidates from its candidates."""
    prepared = [{**candidate, "value": canonical(candidate.get("value"))}
                for candidate in candidates]
    ordered = sorted(dedupe(prepared), key=rank_key)
    if not ordered:
        return {"value": None, "status": "unknown", "citations": [], "candidates": []}
    selected = ordered[0]["value"]
    citations = [{"url": candidate["url"], "excerpt": candidate["excerpt"],
                  "provider": candidate["provider"]}
                 for candidate in ordered if same_value(candidate["value"], selected)]
    return {
        "value": selected,
        "status": "known" if len(distinct(ordered)) == 1 else "contested",
        "citations": citations,
        "candidates": [{**{key: candidate.get(key) for key in CANDIDATE_KEYS},
                        "selected": index == 0}
                       for index, candidate in enumerate(ordered)],
    }


def legacy_kind(stated):
    """A kind a legacy export records, when it names one of the four; else None."""
    return stated if isinstance(stated, str) and stated in KINDS else None


def legacy_published(stated):
    """A publication time a legacy export records, as an RFC 3339 instant, or None."""
    return instant(stated)


def candidates_from_citations(value, citations: list[dict], retrieved_at, kind=None,
                              published_at=None) -> list[dict]:
    """The candidates an imported field's citations support: one per cited page (§2, upgrade).

    A stage-1 export keeps the value and the excerpts that support it, not the
    page texts, so every citation is one recoverable candidate: its page's served
    text supported this value, and the excerpt is that text. A kind or
    publication time the legacy state records for the page is kept; anything the
    export does not carry stays null (aggregator authority), because inventing it
    would need a provider call, which import may not make.
    """
    if value is None:
        return []
    made = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        url, excerpt = citation.get("url"), citation.get("excerpt")
        provider = citation.get("provider")
        if not (isinstance(url, str) and isinstance(excerpt, str) and excerpt):
            continue
        made.append({"value": value, "url": url, "excerpt": excerpt,
                     "provider": provider if isinstance(provider, str) else PROVIDERS[0],
                     "kind": legacy_kind(citation.get("kind")) or kind,
                     "published_at": legacy_published(citation.get("published_at"))
                     or published_at,
                     "retrieved_at": retrieved_at, "selected": False})
    return made
