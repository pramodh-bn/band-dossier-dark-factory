"""Candidates, the selection rule, and the executives field (§2, §3).

A candidate is one value for one field, supported by one excerpt of one page. From
stage 3 it also carries `true_from` (the date the evidence says the value holds)
and `learned_at` (when this service first had it). Ranking is total and
deterministic -- authority, then recency, then URL, then provider, then value --
so the same candidates always select the same value.

`executives` is a field of its own: its candidates are per title, each title is
ranked separately, and its value lists the selected person of each title in the
order cfo, coo, cto.
"""
from __future__ import annotations

from .pages import AUTHORITY, KINDS, host_of, instant

PROVIDERS = ("nimble", "tavily", "kernel", "browserbase")
PROVIDER_ORDER = {name: index for index, name in enumerate(PROVIDERS)}
FIELDS = ("legal_name", "hq_city", "ceo", "founded_year", "headcount_range", "funding_total")
TITLES = ("cfo", "coo", "cto")
EXECUTIVES = "executives"
INVESTORS = "investors"
ALL_FIELDS = FIELDS + (EXECUTIVES, INVESTORS)
CANDIDATE_KEYS = ("value", "url", "excerpt", "provider", "kind", "published_at",
                  "retrieved_at", "true_from", "learned_at", "selected", "origin")
EXECUTIVE_KEYS = CANDIDATE_KEYS + ("title",)
#: Kinds that make a candidate enough for an investor name to be a member on its own.
MEMBER_KINDS = ("own", "filing")


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
    """Sort key for recency: a later time first, and no time last."""
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


def _citation(candidate: dict) -> dict:
    """The `url`, `excerpt` and `provider` of a candidate."""
    return {"url": candidate["url"], "excerpt": candidate["excerpt"],
            "provider": candidate["provider"]}


def _origins(candidates: list[dict]) -> list[str]:
    """The distinct origins of some candidates, sorted ascending (§4, Origins)."""
    return sorted({candidate.get("origin") for candidate in candidates
                   if candidate.get("origin")})


def _corroborated(groups: list[list[dict]]) -> bool:
    """Whether every group's candidates have two or more distinct origins (§4)."""
    return bool(groups) and all(len(_origins(group)) >= 2 for group in groups)


def _empty_field() -> dict:
    """An `unknown` field: no value, no candidates, no origins (§4, §2)."""
    return {"value": None, "status": "unknown", "citations": [], "candidates": [],
            "true_from": None, "learned_at": None, "origins": [],
            "corroborated": False, "corroborated_since": None}


def field(candidates: list[dict], corroborated_since: str | None = None) -> dict:
    """A field's value, status, citations and ordered candidates from its candidates."""
    prepared = [{**candidate, "value": canonical(candidate.get("value"))}
                for candidate in candidates]
    ordered = sorted(dedupe(prepared), key=rank_key)
    if not ordered:
        return _empty_field()
    selected = ordered[0]["value"]
    selected_candidates = [candidate for candidate in ordered
                           if same_value(candidate["value"], selected)]
    citations = [_citation(candidate) for candidate in selected_candidates]
    origins = _origins(selected_candidates)
    return {
        "value": selected,
        "status": "known" if len(distinct(ordered)) == 1 else "contested",
        "citations": citations,
        "candidates": [{**{key: candidate.get(key) for key in CANDIDATE_KEYS},
                        "selected": index == 0}
                       for index, candidate in enumerate(ordered)],
        "true_from": ordered[0].get("true_from"),
        "learned_at": ordered[0].get("learned_at"),
        "origins": origins,
        "corroborated": len(origins) >= 2,
        "corroborated_since": corroborated_since,
    }


def executives_field(candidates: list[dict],
                     corroborated_since: str | None = None) -> dict:
    """The executives field: each title ranked on its own, members in cfo, coo, cto order."""
    prepared = [{**candidate, "value": canonical(candidate.get("value"))}
                for candidate in candidates]
    ranked = {title: sorted(dedupe([candidate for candidate in prepared
                                    if candidate.get("title") == title]), key=rank_key)
              for title in TITLES}
    members = [{"title": title, "name": ranked[title][0]["value"]}
               for title in TITLES if ranked[title]]
    ordered = [candidate for title in TITLES for candidate in ranked[title]]
    if not members:
        return _empty_field()
    citations = [_citation(candidate) for member in members
                 for candidate in ranked[member["title"]]
                 if same_value(candidate["value"], member["name"])]
    groups = [[candidate for candidate in ranked[member["title"]]
               if same_value(candidate["value"], member["name"])] for member in members]
    contested = any(len(distinct(ranked[title])) > 1 for title in TITLES)
    member_dates = [ranked[member["title"]][0].get("true_from") for member in members]
    true_from = None if any(date is None for date in member_dates) else max(member_dates)
    return {
        "value": members,
        "status": "contested" if contested else "known",
        "citations": citations,
        "candidates": [{**{key: candidate.get(key) for key in EXECUTIVE_KEYS},
                        "selected": candidate is ranked[candidate["title"]][0]}
                       for candidate in ordered],
        "true_from": true_from,
        "learned_at": ordered[0].get("learned_at"),
        "origins": _origins([candidate for group in groups for candidate in group]),
        "corroborated": _corroborated(groups),
        "corroborated_since": corroborated_since,
    }


def member_true_from(candidates: list[dict]) -> str | None:
    """A member's `true_from`: the earliest not-null one among its candidates (§4)."""
    dates = [candidate.get("true_from") for candidate in candidates
             if candidate.get("true_from") is not None]
    return min(dates) if dates else None


def investors_field(candidates: list[dict],
                    corroborated_since: str | None = None) -> dict:
    """The investors field: the member names, sorted, each with its candidates (§4).

    A name is a member when one of its candidates has kind `own` or `filing`, or its
    candidates have two or more distinct origins. The field is `known` exactly when
    it has a member, and `unknown` otherwise, whatever candidates exist.
    """
    prepared = [{**candidate, "value": canonical(candidate.get("value"))}
                for candidate in candidates]
    ordered = sorted(dedupe(prepared), key=rank_key)
    groups: dict[str, list[dict]] = {}
    for candidate in ordered:
        groups.setdefault(candidate["value"], []).append(candidate)
    members = sorted(name for name, group in groups.items()
                     if any(candidate.get("kind") in MEMBER_KINDS for candidate in group)
                     or len(_origins(group)) >= 2)
    if not members:
        empty = _empty_field()
        empty["candidates"] = [{**{key: candidate.get(key) for key in CANDIDATE_KEYS},
                                "selected": False} for candidate in ordered]
        empty["origins"] = []
        empty["corroborated"] = False
        return empty
    member_groups = [groups[name] for name in members]
    citations = [_citation(candidate) for group in member_groups for candidate in group]
    member_dates = [member_true_from(group) for group in member_groups]
    true_from = None if any(date is None for date in member_dates) else max(member_dates)
    return {
        "value": members,
        "status": "known",
        "citations": citations,
        "candidates": [{**{key: candidate.get(key) for key in CANDIDATE_KEYS},
                        "selected": candidate["value"] in members}
                       for candidate in ordered],
        "true_from": true_from,
        "learned_at": ordered[0].get("learned_at") if ordered else None,
        "origins": _origins([candidate for group in member_groups for candidate in group]),
        "corroborated": _corroborated(member_groups),
        "corroborated_since": corroborated_since,
    }


def legacy_kind(stated):
    """A kind a legacy export records, when it names one of the four; else None."""
    return stated if isinstance(stated, str) and stated in KINDS else None


def legacy_published(stated):
    """A publication time a legacy export records, as an RFC 3339 instant, or None."""
    return instant(stated)


def candidates_from_citations(value, citations: list[dict], retrieved_at, kind=None,
                              published_at=None, origin=None) -> list[dict]:
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
                     "retrieved_at": retrieved_at, "true_from": None,
                     "learned_at": retrieved_at, "selected": False,
                     "origin": origin if isinstance(origin, str) and origin
                     else host_of(url)})
    return made
