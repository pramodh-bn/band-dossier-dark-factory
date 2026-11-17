"""Domain normalisation and validation, straight from §4's rules."""
from __future__ import annotations

import pytest

from app.domain import InvalidDomain, is_valid, normalised, normalised_or_raise

LONG_LABEL = "a" * 63
LONGEST = ".".join([LONG_LABEL] * 3 + ["b" * 61])          # 253 characters


@pytest.mark.parametrize("raw,expected", [
    ("  Acme-Robotics.TEST.  ", "acme-robotics.test"),
    ("www.acme-robotics.test", "acme-robotics.test"),
    ("WWW.ACME-ROBOTICS.TEST.", "acme-robotics.test"),
    ("acme-robotics.test", "acme-robotics.test"),
    ("x-1.y2.test", "x-1.y2.test"),
])
def test_normalisation(raw, expected):
    """Whitespace, case, one trailing `.` and one leading `www.` are all removed."""
    assert normalised(raw) == expected
    assert normalised_or_raise(raw) == expected


@pytest.mark.parametrize("raw", [
    "https://acme-robotics.test",
    "acme-robotics.test/about",
    "localhost",
    "www.test",
    "-acme-robotics.test",
    "acme-robotics.test..",
    "a..test",
    "acme_co.test",
    "a" * 64 + ".test",
    LONGEST + "b",
    "",
    "   ",
    "acme co.test",
])
def test_invalid_domains(raw):
    """Everything else, a URL included, is not a valid host name."""
    with pytest.raises(InvalidDomain):
        normalised_or_raise(raw)


@pytest.mark.parametrize("domain", [LONG_LABEL + ".test", LONGEST, "x-1.y2.test"])
def test_longest_valid_domains_are_accepted(domain):
    """Labels of 63 characters and a total of 253 are inside the rule."""
    assert is_valid(domain)
    assert normalised_or_raise(domain) == domain
