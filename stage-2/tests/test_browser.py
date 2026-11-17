"""The browser experience, driven with Playwright (§2, "Screens").

These run the real document against the real API; only the provider is faked.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

import fakeweb  # noqa: E402
from conftest import ALICE, FIELDS, wait_ready  # noqa: E402

ACME = "acme-robotics.test"
SCREENS = [("/", "research-domain", True), ("/dossiers/{domain}", "dossier-domain", True),
           ("/dossiers/{domain}/evidence", "evidence-legal_name", True),
           ("/contested", "contested-list|empty-contested", True),
           ("/signup", "signup-submit", False), ("/login", "login-submit", False)]


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def build(world, domain=ACME) -> dict:
    """Start a dossier through the API and wait for its build to end."""
    assert world.post("/dossiers", json={"domain": domain},
                      headers={"Idempotency-Key": key()}).status_code == 202
    return wait_ready(world, domain)


@pytest.fixture(scope="session")
def browser():
    """One headless Chromium for the whole run."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, service):
    """A fresh browser context per check, pointed at the running service."""
    context = browser.new_context(base_url=service)
    context.set_default_timeout(15_000)
    tab = context.new_page()
    yield tab
    context.close()


def sign_in(page, user=None) -> None:
    """Sign in through `/login` and wait for `current-user`."""
    user = user or ALICE
    page.goto("/login")
    page.get_by_test_id("login-email").fill(user["email"])
    page.get_by_test_id("login-password").fill(user["password"])
    page.get_by_test_id("login-submit").click()
    expect(page.get_by_test_id("current-user")).to_be_visible()


@pytest.mark.parametrize("route,anchor,signed", SCREENS,
                         ids=["list", "dossier", "evidence", "contested", "signup", "login"])
def test_screens_are_directly_navigable(world, page, route, anchor, signed):
    """Every route the spec names is reachable by URL and shows its anchor."""
    build(world)
    if signed:
        sign_in(page)
    page.goto(route.format(domain=ACME))
    found = page.locator(", ".join(f"[data-testid='{name}']" for name in anchor.split("|")))
    expect(found.first).to_be_attached()


def test_sign_in_and_sign_out(world, page):
    """Signing in shows the display name everywhere; signing out leaves the app."""
    sign_in(page)
    expect(page.get_by_test_id("current-user")).to_contain_text(ALICE["display_name"])
    page.get_by_test_id("logout-button").click()
    page.wait_for_url("**/login")
    expect(page.get_by_test_id("login-submit")).to_be_visible()


def test_a_refused_sign_in_shows_an_error(world, page):
    """A wrong password shows `auth-error` and stays on the form."""
    page.goto("/login")
    page.get_by_test_id("login-email").fill(ALICE["email"])
    page.get_by_test_id("login-password").fill("wrong horse")
    page.get_by_test_id("login-submit").click()
    expect(page.get_by_test_id("auth-error")).to_be_visible()
    expect(page.get_by_test_id("auth-error")).not_to_have_text("")


def test_signup_creates_an_account(world, page):
    """The signup form creates an account and signs it in."""
    page.goto("/signup")
    page.get_by_test_id("signup-display-name").fill("Dee")
    page.get_by_test_id("signup-email").fill("dee@example.com")
    page.get_by_test_id("signup-password").fill("correct horse")
    page.get_by_test_id("signup-submit").click()
    expect(page.get_by_test_id("current-user")).to_contain_text("Dee")


def test_the_dossier_screen_shows_values_statuses_and_citations(world, page):
    """Every field, its data attributes, its citations and the contested marker."""
    body = build(world)
    sign_in(page)
    page.goto(f"/dossiers/{ACME}")
    expect(page.get_by_test_id("dossier-domain")).to_have_text(ACME)
    expect(page.get_by_test_id("dossier-state")).to_have_text("ready")
    expect(page.get_by_test_id("dossier-spent")).to_have_attribute(
        "data-units", str(body["spent_units"]))
    expect(page.get_by_test_id("dossier-researched-at")).to_be_visible()
    for name in FIELDS:
        field = body["fields"][name]
        expect(page.get_by_test_id(f"field-{name}")).to_have_attribute(
            "data-status", field["status"])
        expect(page.get_by_test_id(f"field-value-{name}")).to_have_attribute(
            "data-value", str(field["value"]))
        if field["status"] == "contested":
            expect(page.get_by_test_id(f"field-contested-{name}")).to_be_visible()
        expect(page.get_by_test_id(f"field-citations-{name}")).to_be_visible()
        first = field["citations"][0]
        citation = page.get_by_test_id(f"citation-{name}-1")
        expect(citation).to_contain_text(first["excerpt"])
        expect(citation).to_have_attribute("data-provider", first["provider"])
        link = citation.locator("a[href]").first
        assert link.get_attribute("href") == first["url"]


def test_research_from_the_list_opens_the_dossier(world, page):
    """A research request opens the normalised dossier without a manual reload."""
    sign_in(page)
    page.goto("/")
    page.get_by_test_id("research-domain").fill(f"WWW.{ACME.upper()}")
    page.get_by_test_id("research-submit").click()
    page.wait_for_url(f"**/dossiers/{ACME}")
    expect(page.get_by_test_id("dossier-domain")).to_have_text(ACME)
    expect(page.get_by_test_id("dossier-state")).to_have_text("ready")


def test_an_invalid_domain_shows_a_research_error(world, page):
    """An invalid domain is a refusal, not an uncertain outcome."""
    sign_in(page)
    page.goto("/")
    page.get_by_test_id("research-domain").fill("https://acme-robotics.test/about")
    page.get_by_test_id("research-submit").click()
    expect(page.get_by_test_id("research-error")).to_be_visible()
    expect(page.get_by_test_id("research-error")).not_to_have_text("")
    expect(page.get_by_test_id("research-uncertain")).to_have_count(0)


def test_a_lost_research_response_is_uncertain_and_retryable(world, page):
    """A response lost after the request committed is uncertain; the retry replays its key."""
    sign_in(page)
    page.goto("/")
    seen = []

    def lose_first_response(route):
        seen.append({"body": route.request.post_data,
                     "key": route.request.headers.get("idempotency-key")})
        if len(seen) == 1:
            route.fetch()          # the request commits on the server ...
            route.abort()          # ... and its response is lost
        else:
            route.continue_()

    page.route("**/dossiers", lose_first_response)
    page.get_by_test_id("research-domain").fill(ACME)
    page.get_by_test_id("research-submit").click()
    expect(page.get_by_test_id("research-uncertain")).to_be_visible()
    expect(page.get_by_test_id("research-error")).to_have_count(0)
    page.get_by_test_id("research-submit").click()
    page.wait_for_url(f"**/dossiers/{ACME}")
    expect(page.get_by_test_id("research-uncertain")).to_have_count(0)
    expect(page.get_by_test_id("research-error")).to_have_count(0)
    expect(page.get_by_test_id("dossier-domain")).to_have_text(ACME)
    assert len(world.get("/dossiers").json()["dossiers"]) == 1
    assert len(seen) >= 2
    assert seen[0] == seen[1], "the retry must send the same body and the same key"
    searches = [entry for entry in fakeweb.LOG if entry["method"] == "search"]
    assert len(searches) == 7, "exactly one build ran"


def test_refresh_follows_the_build_without_a_manual_reload(world, page):
    """A refresh starts a build and the screen polls it to ready with the new spending."""
    before = build(world)
    sign_in(page)
    page.goto(f"/dossiers/{ACME}")
    expect(page.get_by_test_id("dossier-state")).to_have_text("ready")
    page.get_by_test_id("dossier-refresh").click()
    expect(page.get_by_test_id("dossier-spent")).not_to_have_attribute(
        "data-units", str(before["spent_units"]), timeout=20_000)
    expect(page.get_by_test_id("dossier-state")).to_have_text("ready")
    assert wait_ready(world, ACME)["state"] == "ready"


def test_reload_reads_again_and_latest_read_wins(world, page):
    """Two reads that arrive out of order leave the later one on screen."""
    build(world)
    sign_in(page)
    page.goto(f"/dossiers/{ACME}")
    expect(page.get_by_test_id("dossier-state")).to_have_text("ready")
    body = world.get(f"/dossiers/{ACME}").json()
    older = json.loads(json.dumps(body))
    older["fields"]["legal_name"]["value"] = "Older Reading Ltd"
    newer = json.loads(json.dumps(body))
    newer["fields"]["legal_name"]["value"] = "Newer Reading Ltd"
    calls = {"n": 0}

    def out_of_order(route):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.7)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(older))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(newer))

    page.route(f"**/dossiers/{ACME}", out_of_order)
    page.get_by_test_id("dossier-reload").click()
    page.get_by_test_id("dossier-reload").click()
    expect(page.get_by_test_id("field-value-legal_name")).to_have_attribute(
        "data-value", "Newer Reading Ltd", timeout=10_000)
    page.unroute(f"**/dossiers/{ACME}")


def test_the_evidence_screen_lists_candidates_in_api_order(world, page):
    """Each field's candidates are rendered with their data attributes, in API order."""
    body = build(world)
    sign_in(page)
    page.goto(f"/dossiers/{ACME}/evidence")
    for name in FIELDS:
        field = body["fields"][name]
        expect(page.get_by_test_id(f"evidence-{name}")).to_be_visible()
        if not field["candidates"]:
            expect(page.get_by_test_id(f"evidence-empty-{name}")).to_be_visible()
            continue
        for index, cand in enumerate(field["candidates"], start=1):
            item = page.get_by_test_id(f"candidate-{name}-{index}")
            expect(item).to_have_attribute("data-value", str(cand["value"]))
            expect(item).to_have_attribute("data-provider", cand["provider"])
            expect(item).to_have_attribute("data-kind", cand["kind"] or "")
            expect(item).to_have_attribute("data-url", cand["url"])
            expect(item).to_have_attribute("data-published-at", cand["published_at"] or "")
            expect(item).to_have_attribute("data-selected",
                                           "true" if cand["selected"] else "false")
            expect(item).to_contain_text(cand["excerpt"])


def test_the_contested_screen_lists_each_disputed_field(world, page):
    """A contested field appears with its domain, field, values and a link to its evidence."""
    body = build(world)
    sign_in(page)
    page.goto("/contested")
    row = page.get_by_test_id(f"contested-item-{ACME}-ceo")
    expect(row).to_be_visible()
    expect(row).to_have_attribute("data-value", str(body["fields"]["ceo"]["value"]))
    values = {candidate["value"] for candidate in body["fields"]["ceo"]["candidates"]}
    text = row.inner_text()
    assert ACME in text and "ceo" in text
    for value in values:
        assert str(value) in text
    assert row.evaluate(
        "(el) => !!el.closest('a[href]') || el.getAttribute('href') !== null")


def test_the_contested_screen_is_empty_when_nothing_is_disputed(world, page):
    """With no contested field the screen shows its empty state."""
    build(world, "second-company.test")
    sign_in(page)
    page.goto("/contested")
    expect(page.get_by_test_id("empty-contested")).to_be_visible()


def test_the_list_filters_and_shows_states(world, page):
    """The dossier list is ordered, carries each state, and the search filters it."""
    build(world)
    build(world, "second-company.test")
    sign_in(page)
    page.goto("/")
    items = page.locator("[data-testid^=dossier-item-]")
    expect(items).to_have_count(2)
    assert items.nth(0).get_attribute("data-testid") == f"dossier-item-{ACME}"
    expect(page.get_by_test_id(f"dossier-item-{ACME}")).to_have_attribute("data-state", "ready")
    page.get_by_test_id("dossier-search").fill("second")
    expect(page.locator("[data-testid^=dossier-item-]")).to_have_count(1)
    page.get_by_test_id("dossier-search").fill("nothing-matches")
    expect(page.get_by_test_id("empty-dossiers")).to_be_visible()
