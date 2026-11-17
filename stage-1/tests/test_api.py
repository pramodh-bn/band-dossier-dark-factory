"""The HTTP contract: health, auth, reset, clock, dossiers, list, idempotency, export."""
from __future__ import annotations

import concurrent.futures
import uuid

import httpx
import pytest

import fakeweb
from conftest import BOB, FIELDS, NOW, fixture, wait_ready

ACME = "acme-robotics.test"


def key() -> str:
    """A fresh idempotency key."""
    return uuid.uuid4().hex


def error_code(response: httpx.Response) -> str:
    """The `error.code` of a failure response."""
    return response.json()["error"]["code"]


# -- health ----------------------------------------------------------------

def test_health(client):
    """`GET /health` answers the spec's body."""
    response = client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


# -- signup and login ------------------------------------------------------

def test_signup_then_login(client):
    """A new account can use its token at once, and log in again."""
    signed = client.post("/auth/signup", json={"email": "dee@example.com",
                                               "password": "correct horse",
                                               "display_name": "Dee"})
    assert signed.status_code == 201
    body = signed.json()
    assert body["display_name"] == "Dee" and body["token"] and body["user_id"]
    assert client.get("/dossiers", headers={
        "Authorization": f"Bearer {body['token']}"}).status_code == 200
    logged = client.post("/auth/login", json={"email": "dee@example.com",
                                              "password": "correct horse"})
    assert logged.status_code == 200 and logged.json()["user_id"] == body["user_id"]


@pytest.mark.parametrize("over,status,code", [
    ({"email": "dee@example.com"}, 409, "email_taken"),
    ({"password": "short12"}, 422, "validation_failed"),
    ({"password": ""}, 422, "validation_failed"),
    ({"email": "plain"}, 422, "validation_failed"),
    ({"email": "@example.com"}, 422, "validation_failed"),
    ({"display_name": ""}, 422, "validation_failed"),
    ({"display_name": "x" * 101}, 422, "validation_failed"),
    ({"email": 7}, 400, "malformed_request"),
    ({"display_name": False}, 400, "malformed_request"),
])
def test_signup_validation(client, over, status, code):
    """Every signup rule of §6, with the status and code the spec gives it."""
    base = {"email": "dee@example.com", "password": "correct horse", "display_name": "Dee"}
    if over == {"email": "dee@example.com"}:
        client.post("/auth/signup", json=base)
    response = client.post("/auth/signup", json={**base, **over})
    assert (response.status_code, error_code(response)) == (status, code)


@pytest.mark.parametrize("missing", ["email", "password", "display_name"])
def test_signup_missing_field_is_422(client, missing):
    """A required field of the right type but absent is a validation failure."""
    base = {"email": "dee@example.com", "password": "correct horse", "display_name": "Dee"}
    response = client.post("/auth/signup",
                           json={k: v for k, v in base.items() if k != missing})
    assert (response.status_code, error_code(response)) == (422, "validation_failed")


def test_unparseable_bodies(client):
    """A body that is not JSON is 400 on the endpoints that take one."""
    for path in ("/auth/signup", "/auth/login", "/_test/reset"):
        response = client.post(path, content="{not json",
                               headers={"Content-Type": "application/json"})
        assert (response.status_code, error_code(response)) == (400, "malformed_request")


def test_login_failures(client):
    """A wrong password and an unknown email are both 401."""
    client.post("/_test/reset", json=fixture())
    for email, password in (("alice@example.com", "wrong horse"),
                            ("nobody@example.com", "correct horse")):
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert (response.status_code, error_code(response)) == (401, "unauthenticated")


def test_every_endpoint_needs_a_valid_token(client, world):
    """Missing and unknown tokens are 401 on every authenticated endpoint; the rest are open."""
    requests = [("GET", "/dossiers", None), ("GET", f"/dossiers/{ACME}", None),
                ("POST", "/dossiers", {"domain": ACME}),
                ("POST", f"/dossiers/{ACME}/refresh", None)]
    for method, path, body in requests:
        for headers in ({}, {"Authorization": "Bearer not-a-token"},
                        {"Authorization": "Bearer"}, {"Authorization": "Basic YWxpY2U6eA=="}):
            response = client.request(method, path, json=body, headers={
                **headers, "Idempotency-Key": key()})
            assert (response.status_code, error_code(response)) == (401, "unauthenticated")


def test_several_tokens_stay_valid(client):
    """An account may hold several live tokens at once."""
    client.post("/_test/reset", json=fixture())
    first = client.post("/auth/login", json={"email": "alice@example.com",
                                             "password": "correct horse"}).json()["token"]
    second = client.post("/auth/login", json={"email": "alice@example.com",
                                              "password": "correct horse"}).json()["token"]
    for token in (first, second):
        assert client.get("/dossiers",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200


# -- reset -----------------------------------------------------------------

def test_reset_replaces_everything(client, world):
    """Reset removes signed-up accounts, dossiers and idempotent responses."""
    client.post("/auth/signup", json={"email": "dee@example.com", "password": "correct horse",
                                      "display_name": "Dee"})
    assert world.post("/dossiers", json={"domain": ACME},
                      headers={"Idempotency-Key": "shared"}).status_code == 202
    wait_ready(world, ACME)
    assert world.get(f"/dossiers/{ACME}").status_code == 200
    assert client.post("/_test/reset", json=fixture()).status_code == 204
    token = client.post("/auth/login", json={"email": "alice@example.com",
                                             "password": "correct horse"}).json()["token"]
    authed = {"Authorization": f"Bearer {token}"}
    assert client.post("/auth/login", json={"email": "dee@example.com",
                                            "password": "correct horse"}).status_code == 401
    assert client.get("/dossiers", headers=authed).json()["dossiers"] == []
    again = client.post("/dossiers", json={"domain": ACME},
                        headers={**authed, "Idempotency-Key": "shared"})
    assert again.status_code == 202


def test_reset_with_a_shared_email_changes_nothing(client):
    """A fixture with two users sharing an email is 422 and leaves the state alone."""
    client.post("/_test/reset", json=fixture())
    clash = dict(BOB, email="alice@example.com")
    response = client.post("/_test/reset", json={"now": NOW, "users": [dict(clash)] * 2})
    assert (response.status_code, error_code(response)) == (422, "validation_failed")
    assert client.post("/auth/login", json={"email": "alice@example.com",
                                            "password": "correct horse"}).status_code == 200


def test_reset_needs_now(client):
    """A fixture without `now` is a validation failure."""
    response = client.post("/_test/reset", json={"users": []})
    assert (response.status_code, error_code(response)) == (422, "validation_failed")


def test_repeated_resets(client):
    """Reset is idempotent and may be repeated."""
    for _ in range(3):
        assert client.post("/_test/reset", json=fixture()).status_code == 204


# -- clock -----------------------------------------------------------------

def test_clock_moves_forward_only(client, world):
    """The clock moves forward, refuses an earlier time, and needs an explicit offset."""
    assert client.post("/_test/clock",
                       json={"now": "2026-03-02T10:30:00+02:00"}).status_code == 204
    refused = client.post("/_test/clock", json={"now": "2026-03-01T08:59:59+00:00"})
    assert (refused.status_code, error_code(refused)) == (422, "validation_failed")
    no_offset = client.post("/_test/clock", json={"now": "2026-03-02T09:00:00"})
    assert (no_offset.status_code, error_code(no_offset)) == (422, "validation_failed")
    body = world.post("/dossiers", json={"domain": ACME}, headers={
        "Idempotency-Key": key()})
    assert body.status_code == 202
    assert wait_ready(world, ACME)["researched_at"] == "2026-03-02T10:30:00+02:00"


def test_clock_stands_still(client, world):
    """Two dossiers built without a clock call carry the same timestamp."""
    domains = (ACME, "second-company.test")
    stamps = set()
    for domain in domains:
        world.post("/dossiers", json={"domain": domain}, headers={"Idempotency-Key": key()})
        stamps.add(wait_ready(world, domain)["researched_at"])
    assert stamps == {NOW}


def test_reset_sets_the_clock(client, world):
    """Reset puts the clock back to the fixture's `now`."""
    client.post("/_test/clock", json={"now": "2026-04-01T00:00:00+00:00"})
    client.post("/_test/reset", json=fixture())
    token = client.post("/auth/login", json={"email": "alice@example.com",
                                             "password": "correct horse"}).json()["token"]
    with httpx.Client(base_url=str(client.base_url),
                      headers={"Authorization": f"Bearer {token}"}) as fresh:
        fresh.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
        assert wait_ready(fresh, ACME)["researched_at"] == NOW


# -- creating and reading a dossier ---------------------------------------

def test_new_domain_is_202_building(world):
    """A first build answers 202 and `building`, with an opaque dossier id."""
    response = world.post("/dossiers", json={"domain": ACME},
                          headers={"Idempotency-Key": key()})
    assert response.status_code == 202
    body = response.json()
    assert body["domain"] == ACME and body["state"] == "building"
    assert isinstance(body["dossier_id"], str) and 1 <= len(body["dossier_id"]) <= 64


def test_a_built_dossier_holds_the_six_supported_fields(world):
    """Every field the evidence supports is known, with citations that state it."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    body = wait_ready(world, ACME)
    assert body["state"] == "ready" and body["researched_at"] == NOW
    assert set(body["fields"]) == set(FIELDS)
    values = {name: body["fields"][name]["value"] for name in FIELDS}
    assert values == {"legal_name": "Acme Robotics, Inc.", "hq_city": "Lisbon",
                      "ceo": "Dana Ruiz", "founded_year": 2014,
                      "headcount_range": "51-200", "funding_total": 12_000_000}
    for field in body["fields"].values():
        assert field["status"] == "known" and field["citations"]
        for citation in field["citations"]:
            assert citation["provider"] == "nimble" and citation["url"]
            assert 1 <= len(citation["excerpt"]) <= 500


def test_a_negated_claim_is_not_a_value(world):
    """The `reportedly ... in talks to raise $40M` sentence supports nothing."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    body = wait_ready(world, ACME)
    assert body["fields"]["funding_total"]["value"] == 12_000_000
    assert all("40M" not in citation["excerpt"]
               for citation in body["fields"]["funding_total"]["citations"])


def test_existing_domain_is_200_with_its_state(world):
    """A second request for a built domain starts no build and answers 200."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    first = wait_ready(world, ACME)
    response = world.post("/dossiers", json={"domain": ACME},
                          headers={"Idempotency-Key": key()})
    assert response.status_code == 200
    assert response.json() == {"dossier_id": first["dossier_id"], "domain": ACME,
                               "state": "ready"}


@pytest.mark.parametrize("spelling", ["{D}", "www.{d}", "{d}.", "  {d}  "])
def test_every_spelling_names_one_dossier(world, spelling):
    """The normalised domain is what a request names, whichever spelling it uses."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    first = wait_ready(world, ACME)
    raw = spelling.format(d=ACME, D=ACME.upper())
    assert world.post("/dossiers", json={"domain": raw},
                      headers={"Idempotency-Key": key()}).json()["dossier_id"] == \
        first["dossier_id"]
    read = world.get(f"/dossiers/{raw}")
    assert read.status_code == 200 and read.json() == first


def test_every_user_sees_the_same_dossier(client, world):
    """Dossiers are shared: both seeded accounts read the same body."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    first = wait_ready(world, ACME)
    bob = client.post("/auth/login", json={"email": BOB["email"],
                                           "password": BOB["password"]}).json()["token"]
    response = client.get(f"/dossiers/{ACME}", headers={"Authorization": f"Bearer {bob}"})
    assert response.status_code == 200 and response.json() == first


@pytest.mark.parametrize("domain", ["localhost", "acme_co.test", "-a.test"])
def test_invalid_domain_in_the_path_is_422(world, domain):
    """A path domain is normalised and validated before anything else."""
    response = world.get(f"/dossiers/{domain}")
    assert (response.status_code, error_code(response)) == (422, "validation_failed")


def test_unknown_dossier_is_404(world):
    """No dossier for a valid domain is 404."""
    response = world.get("/dossiers/nothing-here.test")
    assert (response.status_code, error_code(response)) == (404, "not_found")


@pytest.mark.parametrize("body,status,code", [
    ({}, 422, "validation_failed"),
    ({"domain": "https://acme-robotics.test"}, 422, "validation_failed"),
    ({"domain": "acme-robotics.test/about"}, 422, "validation_failed"),
    ({"domain": 5}, 400, "malformed_request"),
    ({"domain": ["acme-robotics.test"]}, 400, "malformed_request"),
])
def test_create_validation(world, body, status, code):
    """A missing or invalid domain is 422; a domain of the wrong type is 400."""
    response = world.post("/dossiers", json=body, headers={"Idempotency-Key": key()})
    assert (response.status_code, error_code(response)) == (status, code)


def test_unknown_body_fields_are_ignored(world):
    """An unknown field is ignored, never an error."""
    response = world.post("/dossiers", json={"domain": ACME, "fields": ["ceo"]},
                          headers={"Idempotency-Key": key()})
    assert response.status_code == 202 and response.json()["domain"] == ACME


def test_a_failed_build_spends_nothing_and_keeps_fields_unknown(world):
    """When no provider call succeeds the build fails, with no spend and no values."""
    fakeweb.FAULTS.update({"search", "extract", "research"})
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    body = wait_ready(world, ACME)
    assert body["state"] == "failed" and body["researched_at"] is None
    assert body["spent_units"] == 0
    for name in FIELDS:
        assert body["fields"][name] == {"value": None, "status": "unknown", "citations": []}


def test_a_failed_refresh_keeps_the_last_ready_fields(world):
    """A later build that fails leaves the fields and `researched_at` as they were."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    ready = wait_ready(world, ACME)
    fakeweb.FAULTS.update({"search", "extract", "research"})
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    failed = wait_ready(world, ACME)
    assert failed["state"] == "failed"
    assert failed["fields"] == ready["fields"]
    assert failed["researched_at"] == ready["researched_at"]
    assert failed["spent_units"] == ready["spent_units"]


def test_a_slow_but_answering_provider_still_settles_within_ten_seconds(world):
    """A build whose calls each answer in about a fifth of a second ends inside §8's bound."""
    import time as clock  # noqa: PLC0415 - a local name for the bound this checks

    fakeweb.DELAY = 0.2
    started = clock.monotonic()
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    body = wait_ready(world, ACME, seconds=10)
    assert body["state"] == "ready"
    assert clock.monotonic() - started < 10


def test_fifty_concurrent_requests_are_all_answered(world, client):
    """Fifty requests in flight at once are all answered, and none is a 5xx."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    auth = world.headers["Authorization"]

    def send(index):
        target = ["read", "list", "create"][index % 3]
        if target == "read":
            return client.get(f"/dossiers/{ACME}", headers={"Authorization": auth})
        if target == "list":
            return client.get("/dossiers", headers={"Authorization": auth})
        return client.post("/dossiers", json={"domain": f"bulk-{index}.test"},
                           headers={"Authorization": auth, "Idempotency-Key": key()})

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        responses = list(pool.map(send, range(50)))
    assert all(response.status_code < 500 for response in responses)
    assert all(response.status_code in (200, 202) for response in responses)


# -- listing ---------------------------------------------------------------

def test_list_entry_and_order(world):
    """Dossiers are listed by domain ascending, each entry agreeing with its body."""
    for domain in ("windrush-bakery.test", ACME, "second-company.test"):
        world.post("/dossiers", json={"domain": domain}, headers={"Idempotency-Key": key()})
    for domain in ("windrush-bakery.test", ACME, "second-company.test"):
        wait_ready(world, domain)
    listed = world.get("/dossiers").json()
    assert listed["has_more"] is False
    assert [entry["domain"] for entry in listed["dossiers"]] == \
        sorted(["windrush-bakery.test", ACME, "second-company.test"])
    entry = next(entry for entry in listed["dossiers"] if entry["domain"] == ACME)
    body = wait_ready(world, ACME)
    assert entry["dossier_id"] == body["dossier_id"] and entry["state"] == body["state"]
    assert entry["legal_name"] == body["fields"]["legal_name"]["value"]
    assert entry["spent_units"] == body["spent_units"]
    assert entry["researched_at"] == body["researched_at"]


def test_list_q_and_paging(world):
    """`q` matches the domain or the legal name, ignoring case; limit and offset page."""
    for domain in ("windrush-bakery.test", ACME, "second-company.test"):
        world.post("/dossiers", json={"domain": domain}, headers={"Idempotency-Key": key()})
    for domain in ("windrush-bakery.test", ACME, "second-company.test"):
        wait_ready(world, domain)
    assert [entry["domain"] for entry in
            world.get("/dossiers", params={"q": "ACME"}).json()["dossiers"]] == [ACME]
    assert [entry["domain"] for entry in
            world.get("/dossiers", params={"q": "Acme Robotics"}).json()["dossiers"]] == [ACME]
    assert world.get("/dossiers", params={"q": "zz-none"}).json() == \
        {"dossiers": [], "has_more": False}
    assert len(world.get("/dossiers", params={"q": ""}).json()["dossiers"]) == 3
    first = world.get("/dossiers", params={"limit": 2}).json()
    assert len(first["dossiers"]) == 2 and first["has_more"] is True
    rest = world.get("/dossiers", params={"limit": 2, "offset": 2}).json()
    assert len(rest["dossiers"]) == 1 and rest["has_more"] is False


@pytest.mark.parametrize("params", [
    {"limit": "0"}, {"limit": "201"}, {"limit": "-1"}, {"limit": "ten"}, {"limit": "1e1"},
    {"limit": "4.0"}, {"limit": "+4"}, {"offset": "-1"}, {"offset": "1.0"}, {"offset": "x"},
])
def test_invalid_paging_is_422(world, params):
    """An integer query parameter is plain decimal digits inside §5's range."""
    response = world.get("/dossiers", params=params)
    assert (response.status_code, error_code(response)) == (422, "validation_failed")


def test_unknown_query_parameters_are_ignored(world):
    """An unknown query parameter changes nothing."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    assert len(world.get("/dossiers", params={"sort": "desc"}).json()["dossiers"]) == 1


@pytest.mark.parametrize("method,path", [("HEAD", "/health"), ("OPTIONS", "/dossiers"),
                                         ("DELETE", "/dossiers"), ("PUT", "/health")])
def test_other_methods_never_answer_5xx(client, method, path):
    """An unsupported method answers the §5 envelope, never a server error."""
    response = client.request(method, path)
    assert response.status_code < 500


# -- refresh ---------------------------------------------------------------

def test_refresh_starts_a_build(world):
    """A refresh answers 202 building and the dossier settles ready again."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    first = wait_ready(world, ACME)
    response = world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    assert response.status_code == 202
    assert response.json() == {"dossier_id": first["dossier_id"], "domain": ACME,
                               "state": "building"}
    assert wait_ready(world, ACME)["state"] == "ready"


def test_refresh_spends_again(world):
    """A refresh researches again and its spending counts in `spent_units`."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    first = wait_ready(world, ACME)
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    second = wait_ready(world, ACME)
    assert second["spent_units"] == 2 * first["spent_units"] > 0


def test_refresh_while_building_starts_no_second_build(world):
    """A refresh during a build answers the same 202 body and does not research again."""
    fakeweb.DELAY = 0.2
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    first = world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    again = world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    assert first.status_code == again.status_code == 202
    assert first.json() == again.json()
    body = wait_ready(world, ACME)
    assert body["state"] == "ready"
    assert fakeweb.CALLS.count("search") == 10


def test_refresh_of_an_unknown_domain_is_404(world):
    """A refresh needs a dossier to refresh."""
    response = world.post("/dossiers/nothing-here.test/refresh",
                          headers={"Idempotency-Key": key()})
    assert (response.status_code, error_code(response)) == (404, "not_found")


# -- idempotency -----------------------------------------------------------

def test_replay_is_200_with_the_original_body(world):
    """The same key and body is a replay: 200, body identical to the original response."""
    headers = {"Idempotency-Key": "one-key"}
    first = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert first.status_code == 202
    again = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert again.status_code == 200 and again.json() == first.json()


def test_replay_ignores_unknown_body_fields(world):
    """A body that differs only in unknown fields is the same body."""
    first = world.post("/dossiers", json={"domain": ACME},
                       headers={"Idempotency-Key": "one-key"})
    again = world.post("/dossiers", json={"domain": ACME, "extra": 1},
                       headers={"Idempotency-Key": "one-key"})
    assert again.status_code == 200 and again.json() == first.json()


def test_same_key_different_body_is_409(world):
    """The same key with a different body is a reuse, resolved before field validation."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": "one-key"})
    for body in ({"domain": "second-company.test"}, {"domain": "not a domain"}):
        response = world.post("/dossiers", json=body, headers={"Idempotency-Key": "one-key"})
        assert (response.status_code, error_code(response)) == (409, "idempotency_key_reuse")


def test_same_key_on_another_path_is_a_new_request(world):
    """The same key with the same body on a different path is not a replay."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": "one-key"})
    wait_ready(world, ACME)
    response = world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": "one-key"})
    assert response.status_code == 202


def test_keys_are_scoped_to_the_user(client, world):
    """Two users may use the same key string with no interaction."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": "shared"})
    bob = client.post("/auth/login", json={"email": BOB["email"],
                                           "password": BOB["password"]}).json()["token"]
    response = client.post("/dossiers", json={"domain": "second-company.test"},
                           headers={"Authorization": f"Bearer {bob}",
                                    "Idempotency-Key": "shared"})
    assert response.status_code == 202


def test_a_failed_request_frees_its_key(world):
    """A key reused after its original request failed with 4xx is a first use."""
    failed = world.post("/dossiers", json={"domain": "not a domain"},
                        headers={"Idempotency-Key": "one-key"})
    assert failed.status_code == 422
    again = world.post("/dossiers", json={"domain": ACME},
                       headers={"Idempotency-Key": "one-key"})
    assert again.status_code == 202


@pytest.mark.parametrize("header,status,code", [
    (None, 400, "missing_idempotency_key"),
    ("", 400, "missing_idempotency_key"),
    ("x" * 256, 422, "validation_failed"),
])
def test_idempotency_key_validation(world, header, status, code):
    """The header is required, and 1 to 255 characters."""
    headers = {} if header is None else {"Idempotency-Key": header}
    response = world.post("/dossiers", json={"domain": ACME}, headers=headers)
    assert (response.status_code, error_code(response)) == (status, code)


def test_concurrent_identical_requests_take_effect_once(client, world):
    """Exactly one concurrent identical request answers the normal response, the rest 200."""
    headers = {"Authorization": world.headers["Authorization"], "Idempotency-Key": "burst"}

    def send(_):
        return httpx.post(f"{world.base_url}/dossiers", json={"domain": ACME},
                          headers=headers, timeout=10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(send, range(10)))
    codes = sorted(response.status_code for response in responses)
    assert codes == [200] * 9 + [202]
    bodies = {response.json()["dossier_id"] for response in responses}
    assert len(bodies) == 1
    assert len(world.get("/dossiers").json()["dossiers"]) == 1


def test_concurrent_new_domains_create_one_dossier_each(client, world):
    """Ten requests for the same new domain make one dossier and one build."""
    domains = [f"company-{index}.test" for index in range(10)]

    def send(domain):
        return httpx.post(f"{world.base_url}/dossiers", json={"domain": domain},
                          headers={"Authorization": world.headers["Authorization"],
                                   "Idempotency-Key": key()}, timeout=10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(send, domains))
    assert all(response.status_code == 202 for response in responses)
    listed = world.get("/dossiers").json()["dossiers"]
    assert sorted(entry["domain"] for entry in listed) == sorted(domains)


def test_concurrent_requests_for_one_domain_make_one_dossier(client, world):
    """Whatever their keys, concurrent requests for one domain create one dossier."""
    def send(index):
        return httpx.post(f"{world.base_url}/dossiers", json={"domain": ACME},
                          headers={"Authorization": world.headers["Authorization"],
                                   "Idempotency-Key": key()}, timeout=10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(send, range(8)))
    assert sorted(response.status_code for response in responses) == [200] * 7 + [202]
    assert len(world.get("/dossiers").json()["dossiers"]) == 1


# -- no provider call from a read -----------------------------------------

def test_reads_make_no_provider_call(client, world):
    """Reading a dossier, listing, exporting and signing in make no provider call."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    fakeweb.CALLS.clear()
    world.get(f"/dossiers/{ACME}")
    world.get("/dossiers")
    client.get("/_test/export")
    client.post("/auth/signup", json={"email": "dee@example.com", "password": "correct horse",
                                      "display_name": "Dee"})
    client.post("/auth/login", json={"email": "alice@example.com",
                                     "password": "correct horse"})
    replicas = world.post("/dossiers", json={"domain": ACME},
                          headers={"Idempotency-Key": "replay"})
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": "replay"})
    assert replicas.status_code == 200
    assert fakeweb.CALLS == []


# -- export and import -----------------------------------------------------

def test_export_shape(client):
    """Export carries the track, the format version and an opaque state object."""
    snapshot = client.get("/_test/export").json()
    assert snapshot["track"] == "dossier" and snapshot["format_version"] == 1
    assert isinstance(snapshot["state"], dict)


def test_import_round_trip_keeps_everything(client, world):
    """An unchanged export restores accounts, tokens, dossiers and replays."""
    world.post("/dossiers", json={"domain": ACME},
               headers={"Idempotency-Key": "one-key"})
    body = wait_ready(world, ACME)
    snapshot = client.get("/_test/export").json()
    client.post("/_test/reset", json={"now": "2026-05-01T00:00:00+00:00", "users": []})
    assert client.post("/_test/import", json=snapshot).status_code == 204
    restored = world.get(f"/dossiers/{ACME}")
    assert restored.status_code == 200 and restored.json() == body
    replay = world.post("/dossiers", json={"domain": ACME},
                        headers={"Idempotency-Key": "one-key"})
    assert replay.status_code == 200 and replay.json()["dossier_id"] == body["dossier_id"]
    client.post("/_test/import", json=snapshot)
    assert world.get(f"/dossiers/{ACME}").json() == body


def test_import_does_not_change_the_clock(client, world):
    """Import replaces state but leaves the clock where it was."""
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    wait_ready(world, ACME)
    snapshot = client.get("/_test/export").json()
    client.post("/_test/clock", json={"now": "2026-06-01T00:00:00+00:00"})
    client.post("/_test/import", json=snapshot)
    world.post(f"/dossiers/{ACME}/refresh", headers={"Idempotency-Key": key()})
    assert wait_ready(world, ACME)["researched_at"] == "2026-06-01T00:00:00+00:00"


@pytest.mark.parametrize("body,status", [
    ({"track": "other", "format_version": 1, "state": {}}, 422),
    ({"track": "dossier", "format_version": 2, "state": {}}, 422),
    ({"track": "dossier", "format_version": 1}, 422),
    ({"track": "dossier", "format_version": 1, "state": []}, 422),
    ({"track": "dossier", "format_version": 1, "state": {"users": []}}, 422),
])
def test_import_validation(client, body, status):
    """A payload that is not this service's export is a validation failure."""
    response = client.post("/_test/import", json=body)
    assert response.status_code == status


def test_an_export_taken_mid_build_holds_the_previous_state(client, world):
    """An export during a first build holds no build outcome at all."""
    fakeweb.DELAY = 0.2
    world.post("/dossiers", json={"domain": ACME}, headers={"Idempotency-Key": key()})
    snapshot = client.get("/_test/export").json()
    [dossier] = snapshot["state"]["dossiers"]
    assert dossier["state"] == "failed" and dossier["researched_at"] is None
    assert all(field["value"] is None for field in dossier["fields"].values())
    client.post("/_test/import", json=snapshot)
    body = world.get(f"/dossiers/{ACME}").json()
    assert body["state"] == "failed" and body["researched_at"] is None
    assert all(field["value"] is None for field in body["fields"].values())
