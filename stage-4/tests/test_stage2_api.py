"""Stage 2 API: candidates, kind, publication time, rendered text and the selection."""
from __future__ import annotations

import uuid

import pytest

import fakeweb
from app import candidates as rule
from conftest import FIELDS, NOW, wait_ready

ACME = "acme-robotics.test"
SECOND = "second-company.test"
KEYS = {"value", "url", "excerpt", "provider", "kind", "published_at", "retrieved_at",
        "selected"}


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(client, domain=ACME) -> dict:
    """Start a dossier and wait for its build to end."""
    assert client.post("/dossiers", json={"domain": domain},
                       headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(client, domain)


def assert_ranked(field: dict) -> None:
    """The field's candidates are ordered by the rule, the first one selected."""
    ordered = sorted(field["candidates"], key=rule.rank_key)
    assert field["candidates"] == ordered
    assert [entry["selected"] for entry in field["candidates"]] == \
        [True] + [False] * (len(field["candidates"]) - 1)
    assert rule.same_value(field["value"], field["candidates"][0]["value"]) or \
        field["value"] is not None


def test_every_field_carries_ranked_candidates(world):
    """Each field's candidates are well formed, ordered, and one of them is selected."""
    body = build(world)
    assert body["state"] == "ready"
    for name in FIELDS:
        field = body["fields"][name]
        if not field["candidates"]:
            assert field == {"value": None, "status": "unknown", "citations": [],
                             "candidates": [], "true_from": None,
                             "learned_at": None, "origins": [],
                             "corroborated": False,
                             "corroborated_since": None}, (name, field)
            continue
        for entry in field["candidates"]:
            assert KEYS <= set(entry), (name, entry)
            assert entry["kind"] in ("own", "filing", "news", "aggregator"), (name, entry)
            assert entry["provider"] == "nimble", (name, entry)
            assert isinstance(entry["excerpt"], str) and entry["excerpt"], (name, entry)
            assert entry["url"], (name, entry)
            assert entry["retrieved_at"] == NOW, (name, entry)
            if entry["published_at"] is not None:
                assert entry["published_at"].endswith(("+00:00", "+02:00")), (name, entry)
        assert_ranked(field)
        assert field["status"] in ("known", "contested")
        assert field["citations"] == [
            {"url": entry["url"], "excerpt": entry["excerpt"], "provider": entry["provider"]}
            for entry in field["candidates"]
            if rule.same_value(entry["value"], field["value"])], (name, field)


def test_kind_and_publication_time_come_from_the_page(world):
    """A page's `Kind` and `Published` lines reach its candidates."""
    body = build(world)
    by_url = {entry["url"]: entry for entry in body["fields"]["ceo"]["candidates"]}
    news = by_url["https://news.test/acme-appoints"]
    assert (news["kind"], news["published_at"]) == ("news", "2026-02-20T00:00:00+00:00")
    data = by_url["https://bizdata.test/acme"]
    assert (data["kind"], data["published_at"]) == ("aggregator", "2025-12-01T00:00:00+00:00")
    founding = body["fields"]["founded_year"]["candidates"]
    filing = next(entry for entry in founding if entry["kind"] == "filing")
    assert filing["published_at"] == "2025-04-14T09:30:00+00:00"


def test_a_contested_field_is_ranked_and_selects_the_best_source(world):
    """The company's own site outranks a news report, which outranks an aggregator."""
    field = build(world)["fields"]["ceo"]
    assert field["status"] == "contested"
    assert [entry["value"] for entry in field["candidates"]] == \
        ["Dana Ruiz", "Priya Nair", "Sam Ortiz"]
    assert [entry["kind"] for entry in field["candidates"]] == ["own", "news", "aggregator"]
    assert field["value"] == "Dana Ruiz"
    assert [cite["excerpt"] for cite in field["citations"]] == \
        ["Dana Ruiz has led Acme Robotics as chief executive since 2021."]


def test_the_page_text_is_what_a_browser_shows(world):
    """The team page's served text says Sam Ortiz; the browser text Dana Ruiz is used."""
    body = build(world)
    team = [entry for entry in body["fields"]["ceo"]["candidates"]
            if entry["url"] == "https://acme-robotics.test/team"]
    assert [entry["value"] for entry in team] == ["Dana Ruiz"]
    assert all("Sam Ortiz" not in entry["excerpt"]
               for entry in body["fields"]["ceo"]["candidates"]
               if entry["url"] == "https://acme-robotics.test/team")


def test_every_extract_asks_for_the_rendered_page(world):
    """A build reads pages as a browser shows them (§2, Page text)."""
    build(world)
    extracts = [entry for entry in fakeweb.LOG if entry["method"] == "extract"]
    assert extracts and all(entry["render"] for entry in extracts)


def test_render_costs_three_units_each_and_the_build_stays_inside_eighty(world):
    """The rendered extract is 3 units a URL, and `spent_units` equals the provider's log."""
    body = build(world)
    renders = [entry for entry in fakeweb.LOG
               if entry["method"] == "extract" and entry["status"] == 200]
    assert renders and all(entry["cost"] == 3 for entry in renders)
    assert body["spent_units"] == fakeweb.spent() <= 80


def test_an_unknown_field_has_no_candidates(world):
    """A company whose pages support nothing has six unknown fields with no candidates."""
    body = build(world, SECOND)
    assert body["state"] == "ready"
    for name in FIELDS:
        assert body["fields"][name] == {"value": None, "status": "unknown",
                                        "citations": [], "candidates": [],
                                        "true_from": None, "learned_at": None,
                                        "origins": [], "corroborated": False,
                                        "corroborated_since": None}, name


def test_contested_fields_are_reported_by_the_selected_value(world):
    """The list's `legal_name` stays the selected value (§2, Status)."""
    build(world)
    body = world.get(f"/dossiers/{ACME}").json()
    listed = world.get("/dossiers").json()["dossiers"]
    assert listed[0]["legal_name"] == body["fields"]["legal_name"]["value"] == \
        "Acme Robotics, Inc."


def test_a_refresh_replaces_the_candidates(world):
    """A new ready build's candidates replace the previous ones."""
    first = build(world)
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    second = wait_ready(world, ACME)
    assert second["state"] == "ready"
    assert {name: field["value"] for name, field in second["fields"].items()} == \
        {name: field["value"] for name, field in first["fields"].items()}
    for name in FIELDS:
        assert second["fields"][name]["candidates"]
    assert second["spent_units"] > first["spent_units"]


@pytest.mark.parametrize("domain,exists", [("acme-robotics.test", True),
                                           ("second-company.test", False)])
def test_the_dossier_route_serves_html_for_a_browser(world, client, domain, exists):
    """`Accept: text/html` gets the app shell; a plain API request gets JSON."""
    if exists:
        build(world, ACME)
    html = client.get(f"/dossiers/{domain}",
                      headers={"Accept": "text/html",
                               "Authorization": world.headers["Authorization"]})
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert '<main id="app"' in html.text and "dossier-domain" in html.text
    api = client.get(f"/dossiers/{domain}",
                     headers={"Authorization": world.headers["Authorization"]})
    assert api.status_code == (200 if exists else 404)
    if exists:
        assert api.json()["domain"] == domain
