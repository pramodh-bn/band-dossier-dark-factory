"""Domain normalisation and validation (stage 1, §4).

Every domain the API takes, in a body or a path, goes through :func:`normalised`
first. The order is the spec's own: surrounding whitespace removed, lowercased,
one trailing ``.`` removed, one leading ``www.`` removed.
"""
from __future__ import annotations

import re

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
MAX_LENGTH = 253
MAX_LABEL = 63


class InvalidDomain(ValueError):
    """A domain that is not a valid host name after normalisation."""


def normalised(raw: str) -> str:
    """`raw` as the service reads a domain: trimmed, lowercased, no trailing `.` or `www.`."""
    domain = raw.strip().lower()
    if domain.endswith("."):
        domain = domain[:-1]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_valid(domain: str) -> bool:
    """Whether a normalised domain is a valid host name: two or more labels, each 1-63
    characters of a-z, 0-9 and -, neither starting nor ending with -, 253 characters at most."""
    if not domain or len(domain) > MAX_LENGTH:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if len(label) > MAX_LABEL or not _LABEL.fullmatch(label):
            return False
    return True


def normalised_or_raise(raw: str) -> str:
    """`raw` normalised, or :class:`InvalidDomain` when the result is not a valid host name."""
    domain = normalised(raw)
    if not is_valid(domain):
        raise InvalidDomain(raw)
    return domain


def company_name(domain: str) -> str:
    """The company's name as Appendix A derives it: the first label, each `-` read as a space."""
    return domain.split(".")[0].replace("-", " ")
