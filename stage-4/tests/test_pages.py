"""Page kind and publication time (§2)."""
from __future__ import annotations

import pytest

from app import pages

DOMAIN = "acme-robotics.test"


@pytest.mark.parametrize("kind", ["filing", "news", "aggregator"])
def test_a_stated_kind_is_the_kind(kind):
    """A `Kind` line naming one of the four is the page's kind."""
    text = f"Kind: {kind}\n\nAcme Robotics was founded in 2014."
    assert pages.kind_of(text, "https://elsewhere.test/x", DOMAIN) == kind


def test_a_stated_own_page_is_own_only_for_its_registrant():
    """`Kind: own` on a page not registered to the company is an aggregator (§4)."""
    text = "Kind: own\n\nAcme Robotics was founded in 2014."
    assert pages.kind_of(text, "https://elsewhere.test/x", DOMAIN) == "aggregator"


@pytest.mark.parametrize("stated", ["", "Kind: blog", "Kind:", "Kind: OWN"])
def test_an_unusable_kind_falls_back_to_the_host(stated):
    """A missing or unlisted `Kind` is own for the company's host and aggregator otherwise."""
    text = f"{stated}\n\nAcme Robotics was founded in 2014." if stated else \
        "Acme Robotics was founded in 2014."
    assert pages.kind_of(text, "https://acme-robotics.test/about", DOMAIN) == "own"
    assert pages.kind_of(text, "https://directory.test/acme", DOMAIN) == "aggregator"


def test_the_host_is_normalised_as_a_domain():
    """`www.`, case and a trailing dot do not change whose site a page is on."""
    assert pages.host_of("https://WWW.Acme-Robotics.TEST./about") == DOMAIN
    assert pages.kind_of("", "https://www.acme-robotics.test/about", DOMAIN) == "own"


@pytest.mark.parametrize("stated,expected", [
    ("Published: 2026-02-20T00:00:00Z", "2026-02-20T00:00:00+00:00"),
    ("Published: 2026-02-20T11:30:00+02:00", "2026-02-20T11:30:00+02:00"),
    ("Published: 2026-02-20T00:00:00.500Z", "2026-02-20T00:00:00.500000+00:00"),
])
def test_a_publication_time_is_an_rfc_3339_instant(stated, expected):
    """A usable `Published` line becomes an RFC 3339 timestamp with an explicit offset."""
    text = f"{stated}\n\nAcme Robotics was founded in 2014."
    assert pages.published_of(text) == expected


@pytest.mark.parametrize("stated", ["", "Published: 20 February 2026", "Published: 2026-02-20",
                                    "Published: yesterday"])
def test_an_unusable_publication_time_is_null(stated):
    """A missing or unparseable `Published` line is no publication time."""
    text = f"{stated}\n\nAcme Robotics was founded in 2014." if stated else \
        "Acme Robotics was founded in 2014."
    assert pages.published_of(text) is None


def test_metadata_carries_both():
    """One call gives a candidate the two header facts it needs."""
    text = "Kind: news\nPublished: 2026-02-20T00:00:00Z\n\nAcme Robotics was founded in 2014."
    assert pages.metadata(text, "https://news.test/acme", DOMAIN) == {
        "kind": "news", "published_at": "2026-02-20T00:00:00+00:00"}
