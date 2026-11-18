"""The selection rule and the three statuses (§2, "The selection rule", "Status")."""
from __future__ import annotations

import pytest

from app import candidates


def cand(value, url="https://a.test/x", kind="own", provider="nimble", published=None,
         excerpt="excerpt"):
    """One candidate with the defaults a test does not care about."""
    return {"value": value, "url": url, "excerpt": excerpt, "provider": provider,
            "kind": kind, "published_at": published, "retrieved_at": "2026-03-01T09:00:00+00:00",
            "selected": False}


def values(field: dict) -> list:
    """The candidates' values, in rank order."""
    return [entry["value"] for entry in field["candidates"]]


def test_authority_beats_recency_and_recency_orders_equal_authority():
    """Own and filing rank above news and data; the later publication wins between them."""
    field = candidates.field([
        cand("from-data", kind="aggregator", published="2026-05-01T00:00:00+00:00"),
        cand("from-news", kind="news", published="2026-04-01T00:00:00+00:00"),
        cand("from-own", kind="own", published="2025-01-01T00:00:00+00:00"),
        cand("from-filing", kind="filing", published="2024-01-01T00:00:00+00:00"),
    ])
    assert field["value"] == "from-own"
    assert values(field) == ["from-own", "from-filing", "from-news", "from-data"]


def test_own_and_filing_rank_equal_and_recency_decides():
    """Own and filing share the highest authority, so the later publication wins."""
    field = candidates.field([
        cand("own-page", kind="own", url="https://own.test/a", published="2025-01-01T00:00:00+00:00"),
        cand("filing-page", kind="filing", url="https://register.test/a",
             published="2026-01-01T00:00:00+00:00"),
    ])
    assert field["value"] == "filing-page"


def test_a_candidate_without_a_time_ranks_below_every_timed_one():
    """No publication time ranks last within the same authority."""
    field = candidates.field([
        cand("undated", url="https://a.test/x"),
        cand("dated", url="https://z.test/x", published="2020-01-01T00:00:00+00:00"),
    ])
    assert values(field) == ["dated", "undated"]


def test_later_publication_time_wins_within_authority():
    """Recency compares instants, so different offsets naming the same moment tie."""
    field = candidates.field([
        cand("older", url="https://a.test/x", published="2025-06-01T00:00:00+00:00",
             kind="news"),
        cand("newer", url="https://b.test/x", published="2026-01-01T00:00:00+02:00",
             kind="news"),
    ])
    assert values(field) == ["newer", "older"]


def test_url_then_provider_then_value_break_ties():
    """Equal authority and time fall back to URL, then provider, then the value itself."""
    field = candidates.field([
        cand(9, url="https://b.test/x", provider="nimble"),
        cand(5, url="https://a.test/x", provider="tavily"),
        cand(7, url="https://a.test/x", provider="nimble"),
    ])
    assert values(field) == [7, 5, 9]
    assert field["value"] == 7


def test_strings_compare_by_code_point_after_stripping():
    """A string value is compared stripped, by code point."""
    field = candidates.field([
        cand("  Zeta ", url="https://a.test/x"),
        cand("Alpha", url="https://a.test/x"),
    ])
    assert values(field) == ["Alpha", "Zeta"]


def test_statuses_follow_the_candidates():
    """One value is known, two or more contested, none unknown."""
    known = candidates.field([cand("same", url="https://a.test/1"),
                              cand(" same ", url="https://a.test/2")])
    assert known["status"] == "known" and known["value"] == "same"
    assert len(known["citations"]) == 2
    contested = candidates.field([cand("one", url="https://a.test/1"),
                                  cand("two", url="https://a.test/2")])
    assert contested["status"] == "contested" and contested["value"] == "one"
    assert len(contested["citations"]) == 1
    empty = candidates.field([])
    assert empty == {"value": None, "status": "unknown", "citations": [], "candidates": [],
                    "true_from": None, "learned_at": None, "origins": [],
                    "corroborated": False, "corroborated_since": None}


def test_exactly_one_candidate_is_selected():
    """`selected` is true on the first candidate only, and the list is in rank order."""
    field = candidates.field([cand("a", url="https://a.test/1"),
                              cand("b", url="https://a.test/2")])
    assert [entry["selected"] for entry in field["candidates"]] == [True, False]
    assert field["candidates"][0]["value"] == field["value"]


def test_citations_are_the_selected_values_candidates_in_rank_order():
    """Citations follow every candidate carrying the selected value, in rank order."""
    field = candidates.field([
        cand("same", url="https://a.test/1"),
        cand("other", url="https://a.test/2"),
        cand("same", url="https://b.test/1"),
    ])
    assert [cite["url"] for cite in field["citations"]] == ["https://a.test/1", "https://b.test/1"]
    assert all(cite["provider"] == "nimble" for cite in field["citations"])


def test_one_candidate_per_page_and_value():
    """A page supporting one value twice is one candidate for that page."""
    field = candidates.field([cand("same", url="https://a.test/1", excerpt="first"),
                              cand("same", url="https://a.test/1", excerpt="second")])
    assert len(field["candidates"]) == 1
    assert field["candidates"][0]["excerpt"] == "first"


def test_the_same_candidates_always_select_the_same_value():
    """Insertion order does not change the outcome."""
    items = [cand("x", url="https://b.test/1", kind="news", published="2025-01-01T00:00:00Z"),
             cand("y", url="https://a.test/1", kind="news", published="2025-01-01T00:00:00Z"),
             cand("z", url="https://c.test/1", kind="aggregator")]
    forward = candidates.field(list(items))
    backward = candidates.field(list(reversed(items)))
    assert forward == backward


def test_an_imported_candidate_has_aggregator_authority():
    """A candidate with no kind ranks as an aggregator."""
    field = candidates.field([cand("imported", kind=None, url="https://a.test/1"),
                              cand("own-page", kind="own", url="https://z.test/1")])
    assert field["value"] == "own-page"
    assert candidates.AUTHORITY[None] == candidates.AUTHORITY["aggregator"] == 0


@pytest.mark.parametrize("kind,expected", [("own", 2), ("filing", 2), ("news", 1),
                                          ("aggregator", 0)])
def test_authority_values(kind, expected):
    """The rule's authority numbers."""
    assert candidates.AUTHORITY[kind] == expected
