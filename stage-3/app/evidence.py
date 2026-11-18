"""Appendix A: when an excerpt supports a value.

This module is the whole definition of "supports" the service uses. A value is
asserted only when one sentence of a page's prose passes every clause of A.2,
and every citation is that sentence copied from the text the provider served.
Nothing here reads a page's header: the `Facts:` line is informative only.
"""
from __future__ import annotations

import re

from .domain import company_name

FIELDS = (
    "legal_name",
    "hq_city",
    "ceo",
    "founded_year",
    "headcount_range",
    "funding_total",
)

STRING_FIELDS = ("legal_name", "hq_city", "ceo")

#: The executives field's titles, in the order a value lists them (§3, Executives).
TITLES = ("cfo", "coo", "cto")

#: Each title is a field of its own for Appendix A, with the ceo format and these phrases.
TITLE_PHRASES = {
    "cfo": ("chief financial officer", "CFO"),
    "coo": ("chief operating officer", "COO"),
    "cto": ("chief technology officer", "CTO"),
}

# A.3: the statement phrase each field needs.
STATEMENT_PHRASES = {
    "legal_name": (
        "legal name",
        "registered name",
        "registered as",
        "registered company",
        "legal entity",
        "name of record",
    ),
    "hq_city": (
        "headquartered in",
        "headquarters in",
        "head office in",
        "based in",
        "registered office in",
        "registered seat in",
    ),
    "ceo": ("chief executive officer", "chief executive", "CEO"),
    "founded_year": ("founded", "founding", "established", "incorporated"),
    "headcount_range": ("employs", "employees", "staff", "people", "headcount", "workforce"),
    "funding_total": ("raised", "funding", "invested", "investment"),
}

# A.3: any of these anywhere in the sentence stops it naming a supported value.
NEGATING_PHRASES = (
    "in talks to",
    "plans to",
    "planning to",
    "wants to",
    "expects to",
    "hopes to",
    "aims to",
    "seeking",
    "could",
    "would",
    "might",
    "reportedly",
    "rumoured",
    "former",
    "formerly",
    "will become",
    "not",
)

# A.4: legal-form suffixes, matched exactly as written here.
LEGAL_SUFFIXES = (
    "Inc.",
    "Corp.",
    "LLC",
    "LLP",
    "L.P.",
    "Ltd",
    "Limited",
    "Pty Ltd",
    "plc",
    "PLC",
    "GmbH",
    "AG",
    "KG",
    "SE",
    "AS",
    "A/S",
    "ASA",
    "AB",
    "Oy",
    "Oyj",
    "B.V.",
    "N.V.",
    "S.A.",
    "S.A.S.",
    "S.L.",
    "S.r.l.",
    "S.p.A.",
    "s.r.o.",
    "a.s.",
    "K.K.",
)

# The suffixes whose last character is the `.` A.1 exempts from ending a sentence.
_DOTTED_SUFFIXES = tuple(sorted((s for s in LEGAL_SUFFIXES if s.endswith(".")),
                                key=len, reverse=True))

# A.4: the headcount ranges of §4, smallest first.
HEADCOUNT_RANGES = (
    (10, "1-10"),
    (50, "11-50"),
    (200, "51-200"),
    (500, "201-500"),
    (1000, "501-1000"),
    (5000, "1001-5000"),
    (10000, "5001-10000"),
)
TOP_HEADCOUNT_RANGE = "10001+"

HEADER_LINE = re.compile(r"[A-Z][a-z]*(?:-[A-Z][a-z]*)*:")
SENTENCE_CLOSERS = "\"\u201d\u2019)"

#: An excerpt is 1 to 500 characters long (§4).
MAX_EXCERPT = 500

_NUMBER_TOKEN = re.compile(r"[0-9](?:[0-9.,]*[0-9])?")
_NUMBER_GRAMMAR = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:\.[0-9]+)?")
_AMOUNT = re.compile(
    r"(?:US\$|\$|USD) ?[0-9](?:[0-9.,]*[0-9])?"
    r"(?:(?:k|mn|m|bn)| (?:thousand|million|billion))?"
    r"|"
    r"[0-9](?:[0-9.,]*[0-9])?(?: (?:thousand|million|billion))? (?:US dollars|dollars)",
    re.IGNORECASE,
)
_SCALE_MULTIPLIER = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "million": 1_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
}
_AMOUNT_SYMBOL = re.compile(r"US\$|\$|USD", re.IGNORECASE)
_AMOUNT_SCALE = re.compile(r"k|mn|m|bn|thousand|million|billion", re.IGNORECASE)
_AMOUNT_DOLLARS = re.compile(r"US dollars|dollars", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pages: the header and the prose
# ---------------------------------------------------------------------------

def split_page(text: str) -> tuple[list[str], str]:
    """A served page's header lines and its prose (stage 1, Pages).

    The header is the run of `Name: value` lines at the start, ending at the first
    empty line; a text whose first line is not a header line has no header.
    """
    lines = text.split("\n")
    if not lines or not HEADER_LINE.match(lines[0]):
        return [], text
    for index, line in enumerate(lines):
        if not line.strip():
            return lines[:index], "\n".join(lines[index + 1:])
    return lines, ""


def prose_of(text: str) -> str:
    """A served page's prose: everything after its header."""
    return split_page(text)[1]


# ---------------------------------------------------------------------------
# A.1: sentences
# ---------------------------------------------------------------------------

def _ends_legal_suffix(line: str, dot: int) -> bool:
    """Whether the `.` at `dot` closes a whole-word legal-form suffix."""
    for suffix in _DOTTED_SUFFIXES:
        start = dot - len(suffix) + 1
        if start < 0 or line[start:dot + 1] != suffix:
            continue
        if start > 0 and line[start - 1].isalnum():
            continue
        if dot + 1 < len(line) and line[dot + 1].isalnum():
            continue
        return True
    return False


def _line_sentences(line: str) -> list[str]:
    """The sentences of one line of prose."""
    found: list[str] = []
    start = 0
    index = 0
    length = len(line)
    while index < length:
        character = line[index]
        if character in ".!?":
            if character == "." and _ends_legal_suffix(line, index):
                index += 1
                continue
            end = index + 1
            while end < length and line[end] in SENTENCE_CLOSERS:
                end += 1
            if end >= length or line[end].isspace():
                sentence = line[start:end].strip()
                if sentence:
                    found.append(sentence)
                start = index = end
                continue
        index += 1
    tail = line[start:].strip()
    if tail:
        found.append(tail)
    return found


def sentences(prose: str) -> list[str]:
    """Every sentence of a page's prose, each without its surrounding whitespace."""
    found: list[str] = []
    for line in prose.split("\n"):
        found.extend(_line_sentences(line))
    return found


# ---------------------------------------------------------------------------
# A.2: naming, statements, negations
# ---------------------------------------------------------------------------

def _phrase_pattern(phrase: str) -> str:
    """`phrase` escaped for matching, its single spaces matching one space each."""
    return re.escape(phrase)


def phrase_spans(text: str, phrases: tuple[str, ...]) -> list[tuple[int, int]]:
    """(start, end) of every whole-word, case-insensitive occurrence of `phrases` in `text`."""
    spans: list[tuple[int, int]] = []
    for phrase in phrases:
        if not phrase:
            continue
        for match in re.finditer(_phrase_pattern(phrase), text, re.IGNORECASE):
            start, end = match.span()
            if start > 0 and text[start - 1].isalnum():
                continue
            if end < len(text) and text[end].isalnum():
                continue
            spans.append((start, end))
    return spans


def names_company(sentence: str, domain: str) -> bool:
    """Whether the sentence names the company: its normalised domain, or its name."""
    return bool(phrase_spans(sentence, (domain, company_name(domain))))


def has_statement(sentence: str, field: str) -> bool:
    """Whether the sentence carries a statement phrase for `field`."""
    return bool(phrase_spans(sentence, STATEMENT_PHRASES[field]))


def has_negation(sentence: str) -> bool:
    """Whether the sentence carries any negating phrase."""
    return bool(phrase_spans(sentence, NEGATING_PHRASES))


# ---------------------------------------------------------------------------
# A.4: numbers, amounts and the shapes built from them
# ---------------------------------------------------------------------------

def _number_value(token: str) -> int | float | None:
    """A number token's value, or None when the token is not a number (A.4)."""
    if not _NUMBER_GRAMMAR.fullmatch(token):
        return None
    plain = token.replace(",", "")
    if "." in plain:
        return float(plain)
    return int(plain)


def number_spans(text: str) -> list[tuple[int, int, int | float]]:
    """Every number in `text` with its span and value (A.4)."""
    found: list[tuple[int, int, int | float]] = []
    for match in _NUMBER_TOKEN.finditer(text):
        start, end = match.span()
        if start > 0 and text[start - 1].isalnum():
            continue
        if end < len(text) and text[end].isalnum():
            continue
        value = _number_value(match.group(0))
        if value is None:
            continue
        found.append((start, end, value))
    return found


def _amount_at(text: str, start: int, end: int) -> int | None:
    """The whole dollars an amount text comes to, or None when it is not a whole number."""
    body = text[start:end]
    multiplier = 1
    scale = _AMOUNT_SCALE.search(body)
    if scale is not None:
        multiplier = _SCALE_MULTIPLIER[scale.group(0).lower()]
    number = _NUMBER_TOKEN.search(body)
    if number is None:
        return None
    value = _number_value(number.group(0))
    if value is None:
        return None
    total = value * multiplier
    if isinstance(total, float):
        if not total.is_integer():
            return None
        total = int(total)
    return int(total)


def amount_spans(text: str) -> list[tuple[int, int, int]]:
    """Every amount in `text` with its span and its whole-dollar value (A.4)."""
    found: list[tuple[int, int, int]] = []
    for match in _AMOUNT.finditer(text):
        start, end = match.span()
        if start > 0 and text[start - 1].isalnum():
            continue
        if end < len(text) and text[end].isalnum():
            continue
        value = _amount_at(text, start, end)
        if value is None:
            continue
        found.append((start, end, value))
    return found


def _inside(spans: list[tuple[int, int, int]], start: int, end: int) -> bool:
    """Whether a span lies inside one of `spans` (the numbers of an amount are not numbers)."""
    return any(span_start <= start and end <= span_end for span_start, span_end, _ in spans)


def _capitalised_at(text: str, index: int) -> int | None:
    """The end of a capitalised word starting at `index`, or None (A.4)."""
    if index >= len(text) or not text[index].isupper():
        return None
    end = index + 1
    if end >= len(text) or not text[end].islower():
        return None
    while end < len(text) and text[end].islower():
        end += 1
    if index > 0 and text[index - 1].isalnum():
        return None
    if end < len(text) and text[end].isalnum():
        return None
    return end


def _uppercase_at(text: str, index: int) -> int | None:
    """The end of an uppercase word (two or more uppercase letters) starting at `index`."""
    if index >= len(text) or not text[index].isupper():
        return None
    end = index + 1
    while end < len(text) and text[end].isupper():
        end += 1
    if end - index < 2:
        return None
    if index > 0 and text[index - 1].isalnum():
        return None
    if end < len(text) and text[end].isalnum():
        return None
    return end


def _word_end(text: str, index: int) -> int | None:
    """The end of a capitalised or uppercase word starting at `index`, or None."""
    capitalised = _capitalised_at(text, index)
    if capitalised is not None:
        return capitalised
    return _uppercase_at(text, index)


def _run_end(text: str, index: int, joins: str) -> tuple[int, int] | None:
    """The end of the longest run of capitalised words starting at `index`, and its word count."""
    end = _capitalised_at(text, index)
    if end is None:
        return None
    words = 1
    while end < len(text) and text[end] in joins:
        following = _capitalised_at(text, end + 1)
        if following is None:
            break
        end = following
        words += 1
    return end, words


def _runs(text: str, joins: str) -> list[tuple[int, int, int]]:
    """Every maximal run of capitalised words joined by `joins`: (start, end, words)."""
    found: list[tuple[int, int, int]] = []
    index = 0
    while index < len(text):
        run = _run_end(text, index, joins)
        if run is None:
            index += 1
            continue
        end, words = run
        found.append((index, end, words))
        index = end
    return found


def headcount_range(number: int) -> str:
    """The §4 range that contains a headcount number."""
    for ceiling, name in HEADCOUNT_RANGES:
        if number <= ceiling:
            return name
    return TOP_HEADCOUNT_RANGE


# ---------------------------------------------------------------------------
# A.4: each field's values in one sentence
# ---------------------------------------------------------------------------

def _suffix_end(text: str, index: int) -> int | None:
    """The end of a legal-form suffix that starts at `index` and ends a whole word."""
    for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
        if not text.startswith(suffix, index):
            continue
        end = index + len(suffix)
        if end < len(text) and text[end].isalnum():
            continue
        return end
    return None


def _legal_name_values(sentence: str, domain: str) -> list[str]:
    """Every `legal_name` text the sentence states (A.4)."""
    values: list[str] = []
    for start, end in sorted(set(phrase_spans(sentence, (domain, company_name(domain))))):
        position = end
        while True:
            found = None
            if sentence.startswith(",", position):
                found = _suffix_end(sentence, position + 2) \
                    if sentence.startswith(" ", position + 1) else None
            elif sentence.startswith(" ", position):
                found = _suffix_end(sentence, position + 1)
            if found is not None:
                values.append(sentence[start:found].strip())
                break
            if not sentence.startswith(" ", position):
                break
            word_end = _word_end(sentence, position + 1)
            if word_end is None:
                break
            position = word_end
    return values


def _place_values(sentence: str) -> list[str]:
    """Every `hq_city` place name in the sentence (A.4)."""
    values: list[str] = []
    for _, end in phrase_spans(sentence, STATEMENT_PHRASES["hq_city"]):
        if not sentence.startswith(" ", end) or sentence.startswith("  ", end):
            continue
        run = _run_end(sentence, end + 1, " -")
        if run is not None:
            values.append(sentence[end + 1:run[0]].strip())
    return values


def _without_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """`text` with every span written as spaces, so words either side do not join."""
    trimmed = list(text)
    for start, end in spans:
        for index in range(start, end):
            trimmed[index] = " "
    return "".join(trimmed)


def _person_values(sentence: str, domain: str) -> list[str]:
    """Every `ceo` person name in the sentence (A.4)."""
    removed = list(phrase_spans(sentence, (domain, company_name(domain))))
    removed += phrase_spans(sentence, STATEMENT_PHRASES["ceo"])
    text = _without_spans(sentence, removed)
    values: list[str] = []
    for start, end, words in _runs(text, " "):
        if 2 <= words <= 3:
            values.append(text[start:end].strip())
    return values


def _founded_values(sentence: str) -> list[int]:
    """Every `founded_year` the sentence states (A.4)."""
    amounts = amount_spans(sentence)
    values: list[int] = []
    for start, end, value in number_spans(sentence):
        if _inside(amounts, start, end):
            continue
        token = sentence[start:end]
        if len(token) != 4 or not token.isdigit():
            continue
        year = int(token)
        if 1800 <= year <= 2099:
            values.append(year)
    return values


def _headcount_values(sentence: str) -> list[str]:
    """Every `headcount_range` the sentence states (A.4)."""
    amounts = amount_spans(sentence)
    values: list[str] = []
    for start, end, value in number_spans(sentence):
        if _inside(amounts, start, end):
            continue
        if isinstance(value, float):
            continue
        if value < 1:
            continue
        values.append(headcount_range(value))
    return values


def _funding_values(sentence: str) -> list[int]:
    """Every `funding_total` the sentence states (A.4)."""
    return [value for _, _, value in amount_spans(sentence)]


def format_values(sentence: str, field: str, domain: str):
    """Every value in `field`'s format that the sentence contains, normalised (A.4, A.5)."""
    if field == "legal_name":
        return _legal_name_values(sentence, domain)
    if field == "hq_city":
        return _place_values(sentence)
    if field == "ceo":
        return _person_values(sentence, domain)
    if field == "founded_year":
        return _founded_values(sentence)
    if field == "headcount_range":
        return _headcount_values(sentence)
    if field == "funding_total":
        return _funding_values(sentence)
    return []


# ---------------------------------------------------------------------------
# A.2: the rule
# ---------------------------------------------------------------------------

def supported_value(sentence: str, field: str, domain: str):
    """The one value `sentence` supports for `field` of the company `domain`, or None.

    All five clauses of A.2 are applied: one sentence, naming the company, a
    statement phrase for the field, no negating phrase, and at least one value in
    the field's format, every one of which normalises to the same value.
    """
    if not sentence or len(sentence) > 20_000:
        return None
    if not names_company(sentence, domain):
        return None
    if not has_statement(sentence, field):
        return None
    if has_negation(sentence):
        return None
    values = format_values(sentence, field, domain)
    if not values:
        return None
    first = values[0]
    if any(_differs(value, first) for value in values[1:]):
        return None
    return first


def _differs(one, other) -> bool:
    """Whether two normalised values are different (a string is compared exactly)."""
    if isinstance(one, str) or isinstance(other, str):
        return not (isinstance(one, str) and isinstance(other, str) and one == other)
    return one != other


def executive_values(sentence: str, title: str, domain: str) -> list[str]:
    """Every person name the sentence states for `title` (ceo's format, the title's phrases)."""
    removed = list(phrase_spans(sentence, (domain, company_name(domain))))
    removed += phrase_spans(sentence, TITLE_PHRASES[title])
    text = _without_spans(sentence, removed)
    return [text[start:end].strip() for start, end, words in _runs(text, " ")
            if 2 <= words <= 3]


def supported_executive(sentence: str, title: str, domain: str):
    """The one person `sentence` names for `title`, or None (A.2 with the title's phrases)."""
    if not sentence or len(sentence) > 20_000:
        return None
    if not names_company(sentence, domain):
        return None
    if not phrase_spans(sentence, TITLE_PHRASES[title]):
        return None
    if has_negation(sentence):
        return None
    values = executive_values(sentence, title, domain)
    if not values:
        return None
    first = values[0]
    if any(_differs(value, first) for value in values[1:]):
        return None
    return first


def _value_text(sentence: str, field: str, value, domain: str) -> str:
    """The value as the sentence writes it, for finding an excerpt window."""
    if field in STRING_FIELDS and isinstance(value, str):
        return value
    for start, end in _number_like_spans(sentence, field):
        candidate = sentence[start:end]
        if field == "founded_year":
            if candidate.isdigit() and int(candidate) == value:
                return candidate
        elif field == "headcount_range":
            if headcount_range(int(candidate.replace(",", "").split(".")[0])) == value:
                return candidate
        else:
            if _amount_at(sentence, start, end) == value:
                return candidate
    return ""


def _number_like_spans(sentence: str, field: str) -> list[tuple[int, int]]:
    """The spans of the texts that could carry `field`'s numeric value."""
    if field == "funding_total":
        return [(start, end) for start, end, _ in amount_spans(sentence)]
    return [(start, end) for start, end, _ in number_spans(sentence)]


def excerpt_for(sentence: str, field: str, value, domain: str) -> str | None:
    """The citation text for a supported value: the sentence, or a 500-character window of it.

    A sentence longer than §4's 500-character limit cannot be cited whole; a window
    that still passes A.2 on its own is cited instead.
    """
    if supported_value(sentence, field, domain) is None:
        return None
    if len(sentence) <= MAX_EXCERPT:
        return " ".join(sentence.split())
    marker = _value_text(sentence, field, value, domain)
    windows: list[tuple[int, int]] = []
    if marker:
        position = sentence.find(marker)
        if position >= 0:
            end = position + len(marker)
            windows.append((max(0, end - MAX_EXCERPT), end))
            windows.append((position, position + MAX_EXCERPT))
    windows.append((0, MAX_EXCERPT))
    windows.append((len(sentence) - MAX_EXCERPT, len(sentence)))
    for start, end in windows:
        window = sentence[start:end].strip()
        if window and supported_value(window, field, domain) == value:
            return " ".join(window.split())
    return None


def page_values(text: str, domain: str) -> dict[str, list[tuple[object, str]]]:
    """{field: [(value, excerpt)]} for every value `text`'s prose supports.

    Each excerpt is a single sentence copied from the prose, so a citation built
    from it names the value in the page's own words.
    """
    found: dict[str, list[tuple[object, str]]] = {}
    for sentence in sentences(prose_of(text)):
        if not sentence:
            continue
        for field in FIELDS:
            value = supported_value(sentence, field, domain)
            if value is None:
                continue
            excerpt = excerpt_for(sentence, field, value, domain)
            if excerpt is None:
                continue
            found.setdefault(field, []).append((value, excerpt))
    return found


def page_executives(text: str, domain: str) -> dict[str, list[tuple[str, str]]]:
    """{title: [(name, excerpt)]} for every executive `text`'s prose names (§3, Executives)."""
    found: dict[str, list[tuple[str, str]]] = {}
    for sentence in sentences(prose_of(text)):
        if not sentence:
            continue
        for title in TITLES:
            value = supported_executive(sentence, title, domain)
            if value is None:
                continue
            excerpt = " ".join(sentence.split())
            if len(excerpt) > MAX_EXCERPT:
                continue
            found.setdefault(title, []).append((value, excerpt))
    return found
