"""Stage 3: two times per value, the executives field, history, the feed and `as_of`.

The fake web is the transport; the store, the build plan and the selection rule are
the shipped code. Each check drives the real HTTP API through one or more builds.
"""
from __future__ import annotations

import uuid

import pytest

import fakeweb
from app import evidence, pages
from conftest import FIELDS, NOW, wait_ready

ACME = "acme-robotics.test"
ALL = FIELDS + ("executives",)
MIDDLE = "2026-04-01T09:00:00+00:00"
LATER = "2026-05-01T09:00:00+00:00"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain=ACME, seconds: float = 20) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain, seconds)


def refresh(client, domain=ACME) -> dict:
    """Refresh a dossier and wait for the new build."""
    assert client.post(f"/dossiers/{domain}/refresh",
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain)


def move_on(client, epoch: int = 1, now: str = LATER) -> None:
    """Move the mock web to another epoch and the clock forward."""
    fakeweb.EPOCH = epoch
    assert client.post("/_test/clock", json={"now": now}).status_code == 204


# -- two times per value ---------------------------------------------------

def test_every_candidate_carries_both_times(world):
    """`true_from` and `learned_at` are on every candidate and on the selected value."""
    body = build(world)
    assert body["state"] == "ready"
    for name in FIELDS:
        field = body["fields"][name]
        if field["value"] is None:
            assert field["true_from"] is None and field["learned_at"] is None, name
            continue
        assert field["true_from"] == field["candidates"][0]["true_from"], name
        assert field["learned_at"] == field["candidates"][0]["learned_at"], name
        for candidate in field["candidates"]:
            assert "true_from" in candidate and "learned_at" in candidate, (name, candidate)
            assert candidate["learned_at"] == NOW, (name, candidate)
            if candidate["true_from"] is not None:
                assert len(candidate["true_from"]) == 10, (name, candidate)


def test_true_from_follows_the_page_header(world):
    """An `Effective` line wins; otherwise the UTC date of `Published`; otherwise null."""
    assert pages.true_from("Kind: own\nEffective: 2025-09-01\n\nprose") == "2025-09-01"
    assert pages.true_from(
        "Kind: news\nPublished: 2026-02-20T23:30:00-05:00\n\nprose") == "2026-02-21"
    assert pages.true_from("Kind: news\nPublished: nope\n\nprose") is None
    assert pages.true_from("Kind: own\n\nprose") is None


def test_learned_at_is_the_first_build_that_gathered_the_candidate(world):
    """A candidate gathered again by a later build keeps its first `learned_at`."""
    first = build(world)
    before = {(name, candidate["url"], repr(candidate["value"])): candidate["learned_at"]
              for name in FIELDS for candidate in first["fields"][name]["candidates"]}
    move_on(world)
    second = refresh(world)
    for name in FIELDS:
        for candidate in second["fields"][name]["candidates"]:
            key = (name, candidate["url"], repr(candidate["value"]))
            if key in before:
                assert candidate["learned_at"] == before[key], key


# -- executives ------------------------------------------------------------

def test_executives_are_per_title_with_their_own_selection(world):
    """CFO, COO and CTO candidates are ranked and selected separately."""
    body = build(world)
    field = body["fields"]["executives"]
    assert [member["title"] for member in field["value"]] == ["cfo", "cto"]
    assert field["value"] == [{"title": "cfo", "name": "Dana Ruiz"},
                              {"title": "cto", "name": "Ben Okafor"}]
    assert field["status"] == "contested"          # cto has two names
    assert [candidate["title"] for candidate in field["candidates"]] == \
        ["cfo", "cto", "cto"]
    assert [candidate["selected"] for candidate in field["candidates"]] == [True, True, False]
    assert field["true_from"] == "2026-01-10"
    assert [cite["excerpt"] for cite in field["citations"]] == [
        "Acme Robotics CFO: Dana Ruiz.", "Acme Robotics CTO: Ben Okafor."]


def test_executives_are_unknown_without_a_title(world):
    """A company with no executive sentence has an unknown executives field."""
    body = build(world, "second-company.test")
    assert body["fields"]["executives"] == {
        "value": None, "status": "unknown", "citations": [], "candidates": [],
        "true_from": None, "learned_at": None}


def test_executive_statements_use_the_titles_own_phrases(world):
    """A sentence naming a chief executive is not a CFO statement."""
    assert evidence.supported_executive(
        "Acme Robotics chief executive Dana Ruiz leads it.", "cfo", ACME) is None
    assert evidence.supported_executive(
        "Acme Robotics CFO: Dana Ruiz.", "cfo", ACME) == "Dana Ruiz"
    assert evidence.supported_executive(
        "Acme Robotics chief operating officer: Bo Lin.", "coo", ACME) == "Bo Lin"


# -- history and feed ------------------------------------------------------

def test_the_first_build_writes_one_initial_entry_per_value(world):
    """Every valued field starts with `initial`, and the feed stays empty."""
    body = build(world)
    history = world.get(f"/dossiers/{ACME}/history").json()
    assert history["domain"] == ACME and set(history["fields"]) == set(ALL)
    for name in ALL:
        entries = history["fields"][name]
        field = body["fields"][name]
        if field["value"] is None:
            assert entries == [], name
            continue
        assert [entry["type"] for entry in entries] == ["initial"], name
        assert entries[0]["learned_at"] == NOW
        assert entries[0]["true_from"] == field["true_from"]
    assert world.get("/changes").json() == {"changes": [], "has_more": False}


def test_a_changed_value_adds_one_history_entry_and_one_feed_entry(world):
    """A later build whose selected value differs appends exactly one of each."""
    before = build(world)
    move_on(world)
    after = refresh(world)
    assert after["fields"]["hq_city"]["value"] == "Porto"
    history = world.get(f"/dossiers/{ACME}/history").json()["fields"]["hq_city"]
    assert [entry["type"] for entry in history] == ["initial", "change"]
    assert history[1]["value"] == "Porto" and history[1]["learned_at"] == LATER
    feed = world.get("/changes").json()
    assert feed["has_more"] is False
    assert [(entry["field"], entry["type"], entry["from"], entry["to"])
            for entry in feed["changes"]] == [("hq_city", "change", "Lisbon", "Porto")]
    assert feed["changes"][0]["seq"] == 1
    assert feed["changes"][0]["learned_at"] == LATER
    assert before["fields"]["hq_city"]["value"] == "Lisbon"


def test_a_build_with_the_same_selected_values_adds_nothing(world):
    """Re-gathering the same web adds no history and no feed entry."""
    build(world)
    again = refresh(world)
    assert again["fields"]["hq_city"]["value"] == "Lisbon"
    history = world.get(f"/dossiers/{ACME}/history").json()["fields"]
    assert all(len(entries) <= 1 for entries in history.values())
    assert world.get("/changes").json()["changes"] == []


def test_a_failed_build_adds_nothing(world):
    """A build that fails records no history and no feed entry."""
    build(world)
    before = world.get(f"/dossiers/{ACME}/history").json()
    fakeweb.FAULTS.update({"search", "extract", "research"})
    move_on(world)
    failed = refresh(world)
    assert failed["state"] == "failed"
    assert world.get(f"/dossiers/{ACME}/history").json() == before
    assert world.get("/changes").json()["changes"] == []


def test_a_value_that_disappears_and_returns_is_a_change_both_times(world):
    """A value disappearing and coming back is a `change` each way."""
    build(world)
    move_on(world, epoch=2)                     # epoch 2 serves no hq_city sentence
    gone = refresh(world)
    assert gone["fields"]["hq_city"]["value"] is None
    move_on(world, epoch=1, now="2026-06-01T09:00:00+00:00")
    back = refresh(world)
    assert back["fields"]["hq_city"]["value"] == "Porto"
    history = [entry["type"] for entry in
               world.get(f"/dossiers/{ACME}/history").json()["fields"]["hq_city"]]
    assert history == ["initial", "change", "change"]
    feed = world.get("/changes").json()["changes"]
    assert [(entry["type"], entry["to"]) for entry in feed] == \
        [("change", None), ("change", "Porto")]


def test_an_earlier_true_from_is_a_correction(world):
    """A new value whose `true_from` is on or before the old one is a `correction`."""
    build(world)
    move_on(world, epoch=3)                     # a source that corrects Lisbon to 2020
    corrected = refresh(world)
    assert corrected["fields"]["hq_city"]["value"] == "Braga"
    kind = world.get("/changes").json()["changes"][-1]["type"]
    assert kind == "correction"


def test_executives_history_and_feed_are_typed_by_title(world):
    """A later build that moves a title records the array and a per-title type."""
    build(world)
    move_on(world, epoch=4)                     # the CFO changes
    after = refresh(world)
    assert after["fields"]["executives"]["value"][0] == {"title": "cfo", "name": "Ana Silva"}
    entries = world.get(f"/dossiers/{ACME}/history").json()["fields"]["executives"]
    assert [entry["type"] for entry in entries] == ["initial", "change"]
    assert entries[1]["value"][0] == {"title": "cfo", "name": "Ana Silva"}
    feed = [entry for entry in world.get("/changes").json()["changes"]
            if entry["field"] == "executives"]
    assert [(entry["field"], entry["type"]) for entry in feed] == [("executives", "change")]


def test_feed_entries_from_one_build_are_ordered_by_field(world):
    """Several fields changing in one build are numbered in the §4 order, then executives."""
    build(world)
    move_on(world, epoch=5)                     # hq_city, ceo and cfo all move
    refresh(world)
    feed = world.get("/changes").json()["changes"]
    assert [entry["field"] for entry in feed] == ["hq_city", "ceo", "executives"]
    assert [entry["seq"] for entry in feed] == [1, 2, 3]


def test_the_feed_pages_by_seq(world):
    """`since` skips entries and `limit` pages them, with `has_more`."""
    build(world)
    move_on(world, epoch=5)
    refresh(world)
    whole = world.get("/changes").json()
    assert len(whole["changes"]) == 3 and whole["has_more"] is False
    assert len(world.get("/changes", params={"limit": 2}).json()["changes"]) == 2
    assert world.get("/changes", params={"limit": 2}).json()["has_more"] is True
    tail = world.get("/changes", params={"since": 2}).json()
    assert [entry["seq"] for entry in tail["changes"]] == [3]
    assert world.get("/changes", params={"since": 3}).json() == \
        {"changes": [], "has_more": False}


@pytest.mark.parametrize("params", [{"since": "-1"}, {"since": "x"}, {"since": "1.0"},
                                    {"limit": "0"}, {"limit": "201"}, {"limit": "ten"}])
def test_the_feed_validates_its_numbers(world, params):
    """`since` is a non-negative integer and `limit` 1..200, both in plain digits."""
    response = world.get("/changes", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# -- as_of -----------------------------------------------------------------

def test_as_of_returns_the_build_of_that_moment(world):
    """`as_of` answers with the latest ready build at or before it, never by true_from."""
    first = build(world)
    move_on(world)
    refresh(world)
    mid = world.get(f"/dossiers/{ACME}", params={"as_of": MIDDLE}).json()
    assert mid["state"] == "ready"
    assert mid["researched_at"] == first["researched_at"]
    assert mid["fields"] == first["fields"]
    assert mid["spent_units"] == first["spent_units"]


def test_as_of_spends_only_the_builds_before_it(world):
    """`spent_units` at `as_of` totals the builds at or before that time."""
    first = build(world)
    move_on(world)
    second = refresh(world)
    mid = world.get(f"/dossiers/{ACME}", params={"as_of": MIDDLE}).json()
    now = world.get(f"/dossiers/{ACME}").json()
    assert mid["spent_units"] == first["spent_units"]
    assert now["spent_units"] == second["spent_units"] > mid["spent_units"]


def test_as_of_takes_the_last_build_of_the_same_moment(world):
    """Builds that share a time are ordered by build order, and the last one wins."""
    build(world)
    refresh(world)                               # same clock, same time
    later = world.get(f"/dossiers/{ACME}").json()
    as_of = world.get(f"/dossiers/{ACME}", params={"as_of": NOW}).json()
    assert as_of["researched_at"] == later["researched_at"] == NOW
    assert as_of["spent_units"] == later["spent_units"]


@pytest.mark.parametrize("as_of", ["2026-03-02T09:00:00", "not-a-time", "2026-03-02"])
def test_as_of_needs_an_offset(world, as_of):
    """`as_of` must be an RFC 3339 timestamp with an explicit offset."""
    build(world)
    response = world.get(f"/dossiers/{ACME}", params={"as_of": as_of})
    assert response.status_code == 422 and response.json()["error"]["code"] == \
        "validation_failed"


def test_as_of_before_the_first_build_is_not_found(world):
    """No ready build at or before `as_of` is 404."""
    build(world)
    response = world.get(f"/dossiers/{ACME}",
                         params={"as_of": "2026-02-01T00:00:00+00:00"})
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


# -- /values ---------------------------------------------------------------

def test_values_answer_the_selected_value_of_a_date(world):
    """`/values?true_at=` gives each field's value, true_from and citations for that date."""
    body = build(world)
    day = "2026-03-01"
    values = world.get(f"/dossiers/{ACME}/values", params={"true_at": day}).json()
    assert values["domain"] == ACME and values["true_at"] == day
    assert set(values["fields"]) == set(ALL)
    for name in ALL:
        field = values["fields"][name]
        assert set(field) == {"value", "true_from", "citations"}, name
        assert field["value"] == body["fields"][name]["value"], name
        assert field["true_from"] == body["fields"][name]["true_from"], name


def test_values_respect_effective_replacement(world):
    """A version with an earlier true_from replaces the one it corrects."""
    build(world)
    move_on(world, epoch=3)                    # hq_city Braga, true_from 2020-01-01
    refresh(world)
    values = world.get(f"/dossiers/{ACME}/values", params={"true_at": "2025-01-01"}).json()
    assert values["fields"]["hq_city"]["value"] == "Braga"
    assert values["fields"]["hq_city"]["true_from"] == "2020-01-01"
    before = world.get(f"/dossiers/{ACME}/values", params={"true_at": "2019-01-01"}).json()
    assert before["fields"]["hq_city"]["value"] != "Braga"


def test_values_as_of_hides_later_builds(world):
    """`as_of` narrows the versions to the builds at or before it."""
    build(world)
    move_on(world, epoch=3)
    refresh(world)
    then = world.get(f"/dossiers/{ACME}/values",
                     params={"true_at": "2026-02-01", "as_of": MIDDLE}).json()
    assert then["as_of"] == MIDDLE
    assert then["fields"]["hq_city"]["value"] == "Lisbon"


def test_values_validate_their_parameters(world):
    """`true_at` is required and a date; `as_of` an RFC 3339 instant."""
    build(world)
    for params, status in [
            ({}, 422), ({"true_at": "2025-06"}, 422), ({"true_at": "06/01/2025"}, 422),
            ({"true_at": "2025-06-01", "as_of": "2025-06-01T00:00:00"}, 422),
            ({"true_at": "2025-06-01", "as_of": "2026-04-01T00:00:00+00:00"}, 200)]:
        response = world.get(f"/dossiers/{ACME}/values", params=params)
        assert response.status_code == status, (params, response.text)
    missing = world.get("/dossiers/nothing.test/values", params={"true_at": "2025-06-01"})
    assert missing.status_code == 404


def test_values_before_the_first_build_are_not_found(world):
    """No ready build at or before `as_of` is 404."""
    build(world)
    response = world.get(f"/dossiers/{ACME}/values",
                         params={"true_at": "2025-06-01",
                                 "as_of": "2026-02-01T00:00:00+00:00"})
    assert response.status_code == 404


# -- history shape and missing dossier -------------------------------------

def test_history_and_values_need_a_dossier(world):
    """An unknown domain is 404 on both temporal reads."""
    for path in ("/dossiers/nothing.test/history", "/dossiers/nothing.test/values"):
        response = world.get(path, params={"true_at": "2025-06-01"})
        assert response.status_code == 404 and response.json()["error"]["code"] == \
            "not_found"
