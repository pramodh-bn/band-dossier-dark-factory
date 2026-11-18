"""Appendix A, clause by clause, with the spec's own examples."""
from __future__ import annotations

import pytest

from app import evidence

DOMAIN = "acme-robotics.test"
NAME = "Acme Robotics"


def supports(sentence: str, field: str, domain: str = DOMAIN):
    """The value a sentence supports, or None."""
    return evidence.supported_value(sentence, field, domain)


# -- the spec's table of examples (A.2) -------------------------------------

@pytest.mark.parametrize("sentence,field,value", [
    ("Dana Ruiz has led Acme Robotics as chief executive since 2021.", "ceo", "Dana Ruiz"),
    ("Acme Robotics CEO: Sam Ortiz.", "ceo", "Sam Ortiz"),
    ("Acme Robotics was founded in 2014.", "founded_year", 2014),
    ("Acme Robotics employs about 120 people.", "headcount_range", "51-200"),
])
def test_spec_examples_support(sentence, field, value):
    """The four rows of the spec's example table that support a value."""
    assert supports(sentence, field) == value


@pytest.mark.parametrize("sentence,field", [
    ("Acme Robotics is reportedly in talks to raise $40M.", "funding_total"),
    ("Acme Robotics opened a sales office in Porto.", "hq_city"),
])
def test_spec_examples_support_nothing(sentence, field):
    """The two rows of the spec's example table that support nothing."""
    assert supports(sentence, field) is None


# -- A.1 sentences ---------------------------------------------------------

def test_legal_suffix_does_not_end_a_sentence():
    """`Inc.` written as a whole word does not split the sentence."""
    line = "Acme Robotics, Inc. was founded in 2014."
    assert evidence.sentences(line) == [line]


def test_sentence_ends_at_punctuation_then_whitespace():
    """A sentence ends after `.`, `!` or `?` with its closers, when whitespace follows."""
    prose = 'Acme Robotics was founded in 2014. "It employs people!" What next?'
    assert evidence.sentences(prose) == [
        "Acme Robotics was founded in 2014.",
        '"It employs people!"',
        "What next?",
    ]


def test_punctuation_inside_a_word_does_not_split():
    """`Inc.was` has no whitespace after the dot, so it is one sentence."""
    assert evidence.sentences("Acme Inc.was founded in 2014.") == \
        ["Acme Inc.was founded in 2014."]


def test_a_line_break_ends_a_line_and_so_its_sentences():
    """Prose is split into lines first, then each line into sentences."""
    assert evidence.sentences("Acme Robotics raised $12M\nIt employs 120 people") == \
        ["Acme Robotics raised $12M", "It employs 120 people"]


# -- A.2 naming and phrases ------------------------------------------------

def test_names_the_company_by_domain_or_name():
    """The domain or the company's name, as whole words, names the company."""
    assert evidence.names_company("halvorsen-robotics.test is backed by Marlowe.",
                                  "halvorsen-robotics.test")
    assert evidence.names_company("HALVORSEN ROBOTICS was founded in 2019.",
                                  "halvorsen-robotics.test")
    # The name is a whole-word prefix of a longer name: the characters after it are a space.
    assert evidence.names_company("Halvorsen Robotics Group was founded in 2019.",
                                  "halvorsen-robotics.test")
    # No boundary, and a hyphen where the name has a space, are not the company's name.
    assert not evidence.names_company("Halvorsen RoboticsX was founded in 2019.",
                                      "halvorsen-robotics.test")
    assert not evidence.names_company("Halvorsen-Robotics was founded in 2019.",
                                      "halvorsen-robotics.test")


def test_a_phrase_needs_its_statement_words():
    """`opened a sales office in Porto` carries no `hq_city` statement phrase."""
    assert evidence.has_statement("Acme Robotics opened a sales office in Porto.",
                                  "hq_city") is False
    assert evidence.has_statement("Acme Robotics is based in Porto.", "hq_city") is True


@pytest.mark.parametrize("phrase", ["in talks to", "plans to", "reportedly", "not",
                                    "formerly", "might", "will become"])
def test_every_negating_phrase_stops_support(phrase):
    """Any negating phrase anywhere in the sentence stops it supporting a value."""
    sentence = f"Acme Robotics {phrase} raised $12M."
    assert supports(sentence, "funding_total") is None


# -- A.4 value formats -----------------------------------------------------

@pytest.mark.parametrize("sentence,value", [
    ("Acme Robotics has raised $12M.", 12_000_000),
    ("Acme Robotics has raised $12.5 million.", 12_500_000),
    ("Acme Robotics has raised US$12 million.", 12_000_000),
    ("Acme Robotics has raised USD 12 million.", 12_000_000),
    ("Acme Robotics has raised $12m.", 12_000_000),
    ("Acme Robotics has raised $12,000,000.", 12_000_000),
    ("Acme Robotics has raised 12 million dollars.", 12_000_000),
    ("Acme Robotics has raised 12,000,000 US dollars.", 12_000_000),
    ("Acme Robotics has raised $12bn.", 12_000_000_000),
])
def test_amounts_normalise_to_whole_dollars(sentence, value):
    """Every amount form A.4 writes, in whole US dollars."""
    assert supports(sentence, "funding_total") == value


@pytest.mark.parametrize("sentence", [
    "Acme Robotics has raised €12M.",
    "Acme Robotics has raised $1.2345678M.",
    "Acme Robotics has raised money.",
])
def test_not_an_amount_means_no_value(sentence):
    """Another currency, a fraction of a dollar, and prose with no number are no value."""
    assert supports(sentence, "funding_total") is None


def test_two_amounts_must_agree():
    """Every value in the field's format in the sentence must normalise to one value."""
    assert supports("Acme Robotics has raised $12M and $20M.", "funding_total") is None
    assert supports("Acme Robotics has raised $12M and 12 million dollars.",
                    "funding_total") == 12_000_000


@pytest.mark.parametrize("number,range_name", [
    (1, "1-10"), (10, "1-10"), (11, "11-50"), (50, "11-50"), (51, "51-200"),
    (200, "51-200"), (201, "201-500"), (500, "201-500"), (501, "501-1000"),
    (1000, "501-1000"), (1001, "1001-5000"), (5000, "1001-5000"),
    (5001, "5001-10000"), (10000, "5001-10000"), (10001, "10001+"),
])
def test_headcount_range_boundaries(number, range_name):
    """The range that contains a headcount number, at every boundary."""
    assert evidence.headcount_range(number) == range_name


def test_headcount_range_needs_every_number_to_agree():
    """`40-120` spans two ranges, so it states no single range."""
    assert supports("Acme Robotics employs 40-120 people.", "headcount_range") is None
    assert supports("Acme Robotics employs 51-200 people.", "headcount_range") == "51-200"


def test_a_year_is_not_part_of_an_amount():
    """A four-digit year inside an amount is not a `founded_year`."""
    assert supports("Acme Robotics has raised $2014 million.", "founded_year") is None


@pytest.mark.parametrize("sentence,value", [
    ("Acme Robotics is headquartered in Lisbon.", "Lisbon"),
    ("Acme Robotics is headquartered in New York City.", "New York City"),
    ("Acme Robotics has its registered office in Baden-Württemberg.", "Baden-Württemberg"),
])
def test_place_names_take_the_longest_run(sentence, value):
    """A place name is the longest run of capitalised words after the phrase."""
    assert supports(sentence, "hq_city") == value


def test_place_name_must_follow_the_phrase_directly():
    """A phrase followed by anything but one space and a capitalised word states no city."""
    assert supports("Acme Robotics opened a sales office in Porto.", "hq_city") is None
    assert supports("Acme Robotics is based in  Porto.", "hq_city") is None


def test_person_name_is_two_or_three_capitalised_words():
    """A four-word run is not a person's name, and its parts are not either."""
    assert supports("Acme Robotics chief executive Anna Maria Van Der Berg leads it.",
                    "ceo") is None
    assert supports("Acme Robotics chief executive Anna Maria leads it.", "ceo") == \
        "Anna Maria"


def test_legal_name_takes_the_shortest_suffix_match():
    """Name, optional capitalised words, an optional comma and a legal-form suffix."""
    assert supports("The legal name of Acme Robotics is Acme Robotics, Inc.",
                    "legal_name") == "Acme Robotics, Inc."
    assert supports("Acme Robotics trades under the registered name Acme Robotics AS.",
                    "legal_name") == "Acme Robotics AS"
    assert supports("Acme Robotics is a legal entity.", "legal_name") is None


def test_legal_name_by_domain():
    """The company's domain may stand where its name does."""
    assert supports("acme-robotics.test trades under the legal name acme-robotics.test Ltd.",
                    "legal_name") == "acme-robotics.test Ltd"
    # The domain followed by prose states no legal name; the company's name later does.
    assert supports("The legal name of acme-robotics.test is Acme Robotics Ltd.",
                    "legal_name") == "Acme Robotics Ltd"


def test_company_name_with_a_hyphen_reads_as_a_space():
    """`acme-robotics.test` is named by `Acme Robotics`."""
    assert supports("Acme Robotics was founded in 2014.", "founded_year",
                    "acme-robotics.test") == 2014


# -- Pages: the header is not prose ---------------------------------------

def test_header_facts_are_not_evidence():
    """A `Facts:` header line never supplies a value: only the prose does."""
    text = ('Kind: own\nFacts: {"founded_year": 1998}\n\n'
            "Our team page lists everyone who works here.")
    assert evidence.page_values(text, DOMAIN) == {}


def test_prose_is_everything_after_the_first_empty_line():
    """The header ends at the first empty line."""
    text = "Kind: news\nPublished: 2026-01-01T00:00:00Z\n\nAcme Robotics was founded in 2014."
    assert evidence.prose_of(text).strip() == "Acme Robotics was founded in 2014."


def test_text_whose_first_line_is_not_a_header_is_all_prose():
    """A text that does not start with a header line has no header at all."""
    assert evidence.prose_of("Acme Robotics was founded in 2014.") == \
        "Acme Robotics was founded in 2014."


# -- citations ------------------------------------------------------------

def test_citations_are_sentences_of_the_prose_and_support_the_value():
    """Each citation is one sentence, copied from the prose, that supports a value."""
    text = ("Kind: own\n\nAcme Robotics is headquartered in Lisbon. Lisbon is a city in "
            "Portugal. Acme Robotics employs about 120 people.")
    found = evidence.page_values(text, DOMAIN)
    assert ("Lisbon") in [value for value, _ in found["hq_city"]]
    for field, pairs in found.items():
        for value, excerpt in pairs:
            assert 1 <= len(excerpt) <= evidence.MAX_EXCERPT, (field, excerpt)
            assert len(evidence.sentences(excerpt)) == 1, (field, excerpt)
            assert excerpt in evidence.prose_of(text) or \
                " ".join(excerpt.split()) in " ".join(evidence.prose_of(text).split())
