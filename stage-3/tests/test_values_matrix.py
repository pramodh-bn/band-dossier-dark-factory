"""`/values`: the page-version replacement rule, as a reference model and through the API.

A page version is what one ready build gathered from one URL: its candidates and its
`true_from`. A later version replaces earlier ones whose `true_from` is on or after its
own, and the latest one it does not replace stops holding at its `true_from`. A
version with a null `true_from` holds from no date and replaces every earlier one.
"""
from __future__ import annotations

import uuid

import pytest

import fakeweb
from app import store
from conftest import FIELDS, wait_ready

ACME = "acme-robotics.test"
ALL = FIELDS + ("executives",)


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain=ACME) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain)


def move(client, epoch: int, now: str) -> None:
    """Move the fake web's epoch and the service's clock."""
    fakeweb.EPOCH = epoch
    assert client.post("/_test/clock", json={"now": now}).status_code == 204


# -- the reference model ---------------------------------------------------

def holds(versions: list[dict], day: str) -> list[dict]:
    """The versions of one URL that hold on `day`, by the rule under test."""
    return [version for version in versions if store._holds(version, day)]


def test_a_later_version_ends_the_one_it_does_not_replace():
    """A version with a later `true_from` ends its predecessor on its own date."""
    versions: list[dict] = []
    store._add_version(versions, {"true_from": "2021-01-01", "candidates": ["first"]})
    store._add_version(versions, {"true_from": "2026-03-17", "candidates": ["second"]})
    assert holds(versions, "2020-12-31") == []
    assert [v["candidates"] for v in holds(versions, "2021-01-01")] == [["first"]]
    assert [v["candidates"] for v in holds(versions, "2026-03-16")] == [["first"]]
    assert [v["candidates"] for v in holds(versions, "2026-03-17")] == [["second"]]
    assert [v["candidates"] for v in holds(versions, "2030-01-01")] == [["second"]]


def test_a_version_with_an_earlier_date_replaces_the_later_ones():
    """A correction replaces every earlier version whose date is on or after its own."""
    versions: list[dict] = []
    store._add_version(versions, {"true_from": "2026-01-10", "candidates": ["late"]})
    store._add_version(versions, {"true_from": "2020-01-01", "candidates": ["corrected"]})
    assert [v["candidates"] for v in holds(versions, "2019-12-31")] == []
    assert [v["candidates"] for v in holds(versions, "2020-01-01")] == [["corrected"]]
    assert [v["candidates"] for v in holds(versions, "2026-01-10")] == [["corrected"]]


def test_a_null_version_holds_never_and_replaces_everything():
    """A version with no `true_from` holds from no date and ends every earlier version."""
    versions: list[dict] = []
    store._add_version(versions, {"true_from": "2021-01-01", "candidates": ["first"]})
    store._add_version(versions, {"true_from": None, "candidates": []})
    for day in ("2020-01-01", "2021-01-01", "2030-01-01"):
        assert holds(versions, day) == []


def test_an_empty_version_still_ends_the_older_one():
    """A gathered version with no candidates ends its predecessor by the same rule."""
    versions: list[dict] = []
    store._add_version(versions, {"true_from": "2021-01-01", "candidates": ["first"]})
    store._add_version(versions, {"true_from": "2026-02-15", "candidates": []})
    assert [v["candidates"] for v in holds(versions, "2026-02-14")] == [["first"]]
    assert [candidate for version in holds(versions, "2026-02-15")
            for candidate in version["candidates"]] == []


def test_versions_from_one_build_keep_their_order():
    """Two versions gathered by builds with the same clock are applied in build order."""
    versions: list[dict] = []
    store._add_version(versions, {"true_from": "2021-01-01", "candidates": ["first"]})
    store._add_version(versions, {"true_from": "2022-01-01", "candidates": ["second"]})
    store._add_version(versions, {"true_from": "2020-01-01", "candidates": ["third"]})
    assert [v["candidates"] for v in holds(versions, "2025-01-01")] == [["third"]]
    assert [v["candidates"] for v in holds(versions, "2020-06-01")] == [["third"]]


# -- through the API -------------------------------------------------------

def test_a_page_that_loses_its_value_ends_the_old_version(world):
    """A later build's version of the same page ends the earlier value where it starts."""
    build(world)                                  # epoch 0: Lisbon, true_from 2026-01-10
    move(world, 2, "2026-04-02T09:00:00+00:00")   # epoch 2: the page states no city
    after = wait_ready(world, ACME) if world.post(
        f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()}).status_code == 202 \
        else None
    assert after["fields"]["hq_city"]["value"] is None
    before_end = world.get(f"/dossiers/{ACME}/values",
                           params={"true_at": "2026-02-14"}).json()["fields"]["hq_city"]
    assert before_end["value"] == "Lisbon" and before_end["true_from"] == "2026-01-10"
    ended = world.get(f"/dossiers/{ACME}/values",
                      params={"true_at": "2026-02-15"}).json()["fields"]["hq_city"]
    assert ended == {"value": None, "true_from": None, "citations": []}
    later = world.get(f"/dossiers/{ACME}/values",
                      params={"true_at": "2026-03-01"}).json()["fields"]["hq_city"]
    assert later["value"] is None


def test_values_use_the_other_pages_that_still_hold(world):
    """When one page's version ends, another page's evidence still answers."""
    build(world)
    move(world, 1, "2026-04-02T09:00:00+00:00")
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    city = world.get(f"/dossiers/{ACME}/values",
                     params={"true_at": "2026-02-20"}).json()["fields"]["hq_city"]
    assert city["value"] == "Porto" and city["true_from"] == "2026-02-01"


def test_values_matrix_of_null_ended_and_replaced_versions(world):
    """A matrix of dates over three builds maps to the versions that hold on each."""
    build(world)                                  # Lisbon from 2026-01-10
    move(world, 1, "2026-04-02T09:00:00+00:00")   # Porto from 2026-02-01
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    move(world, 3, "2026-05-02T09:00:00+00:00")   # Braga from 2020-01-01 (a correction)
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    expected = {
        "2019-12-31": (None, None),
        "2020-01-01": ("Braga", "2020-01-01"),
        "2025-12-31": ("Braga", "2020-01-01"),
        "2026-01-20": ("Braga", "2020-01-01"),
        "2026-03-01": ("Braga", "2020-01-01"),
    }
    for day, (value, true_from) in expected.items():
        field = world.get(f"/dossiers/{ACME}/values",
                          params={"true_at": day}).json()["fields"]["hq_city"]
        assert (field["value"], field["true_from"]) == (value, true_from), day


def test_values_at_a_date_before_any_version_are_empty(world):
    """A date before every version has no candidate, so no value and no citations."""
    build(world)
    field = world.get(f"/dossiers/{ACME}/values",
                      params={"true_at": "2000-01-01"}).json()["fields"]["ceo"]
    assert field == {"value": None, "true_from": None, "citations": []}


def test_empty_versions_survive_an_export_and_import(client, world):
    """A ready build's empty page version is part of the exported state."""
    build(world)
    move(world, 2, "2026-04-02T09:00:00+00:00")
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    snapshot = client.get("/_test/export").json()
    versions = snapshot["state"]["dossiers"][0]["builds"][-1]["versions"]
    assert any(version["candidates"] == [] for version in versions.values())
    values = world.get(f"/dossiers/{ACME}/values",
                       params={"true_at": "2026-03-01",
                               "as_of": "2026-04-02T09:00:00+00:00"}).json()
    client.post("/_test/reset", json={"now": "2026-03-01T09:00:00+00:00", "users": [
        {"id": "u_a", "email": "a@example.com", "password": "correct horse",
         "display_name": "A"}]})
    assert client.post("/_test/import", json=snapshot).status_code == 204
    assert world.get(f"/dossiers/{ACME}/values",
                     params={"true_at": "2026-03-01",
                             "as_of": "2026-04-02T09:00:00+00:00"}).json() == values


def test_a_build_that_gathers_a_page_with_no_value_still_records_its_version(world):
    """Every rendered page is a version, even one that states nothing."""
    build(world)
    move(world, 2, "2026-04-02T09:00:00+00:00")
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    export = world.get("/_test/export").json()
    versions = export["state"]["dossiers"][0]["builds"][-1]["versions"]
    assert "https://acme-robotics.test/hq" in versions
    assert versions["https://acme-robotics.test/hq"]["candidates"] == []
    assert versions["https://acme-robotics.test/hq"]["true_from"] == "2026-02-15"


@pytest.mark.parametrize("field", ALL)
def test_every_field_answers_values(world, field):
    """Every field of `/values` carries the three keys, even when unknown."""
    build(world)
    values = world.get(f"/dossiers/{ACME}/values", params={"true_at": "2026-03-01"}).json()
    assert set(values["fields"]) == set(ALL)
    assert set(values["fields"][field]) == {"value", "true_from", "citations"}
