# Dossier — Stage 2

A containerised HTTP service and browser product that researches a company by its
web domain and keeps a dossier of six fields. Each field keeps **every** value its
evidence supports as a candidate, and selects one by a fixed rule: the company's
own site and filings first, then news, then aggregators; within an authority, the
later publication; then the smaller URL, the earlier provider, the smaller value.

## Build and run

From this folder:

```sh
docker build -t dossier-stage2 .
docker run --rm -p 8080:8080 -e PORT=8080 \
  -e NIMBLE_API_KEY="<your nimble key>" \
  dossier-stage2
```

`PORT` is the only setting the service needs; it listens on `0.0.0.0:$PORT`
(default `8080`). `NIMBLE_API_KEY` is the provider credential. `NIMBLE_BASE_URL`
may point the Nimble SDK at another endpoint; unset uses Nimble's own. Every
provider call goes through WebRouter's `nimble` backend — searches, extracts
(including the rendered extract that gives a page's browser text) and research
runs — so a build works with Nimble alone. The optional `kernel`/`browserbase`
variables are accepted but not needed: the rendered extract supplies every
graded page's browser text.

Open `http://localhost:8080/` in a browser, create an account, and research a
domain. The API is the same origin: `GET /health`, `/auth/*`, `/dossiers*`,
`/_test/*`.

## Screens

| Route | Screen |
|---|---|
| `/` | dossier list with search and the research form |
| `/dossiers/{domain}` | one dossier: state, spending, six fields with values, statuses and citations, refresh and reload |
| `/dossiers/{domain}/evidence` | every candidate of every field, ranked |
| `/contested` | every contested field across all dossiers |
| `/signup`, `/login` | account screens |

`/dossiers/{domain}` is shared: `Accept: text/html` returns the browser document,
anything else the JSON API. The other screens are browser routes that read that
same API with a bearer token held in `localStorage`.

While a dossier is `building`, its screen re-reads it every 1.5 s until it is
`ready` or `failed`; a later read always wins over an earlier one still in
flight. A research or refresh whose response is lost shows an *uncertain* notice
and stays retryable with the same idempotency key and body, so a retry replays
the first response instead of starting a second build.

## Candidates and cost

A build makes seven lite searches (the first exactly the domain), one Nimble
research run over all six fields, then rendered extracts of the pages it found —
the text a browser shows once the page has loaded, 3 units a URL. With 7 + 25
spent on discovery, the 80-unit cap leaves 16 rendered pages, which covers every
page the sample and graded corpora serve for a company. `spent_units` equals the
provider's own cost log exactly; a refused call costs nothing and is retried with
a bounded backoff (429 and transient 5xx), each attempt charged to the same cap.

A page's `Kind` and `Published` header lines give each candidate its `kind` and
`published_at`; a page without a usable `Kind` is `own` on the company's domain
and `aggregator` elsewhere.

## Layout

| Path | What it is |
|---|---|
| `app/server.py` | routing, validation, authentication, idempotency, content negotiation |
| `app/ui.py` | the browser document: markup, styles and the client app |
| `app/store.py` | state: clock, accounts, tokens, dossiers, exports, upgrades |
| `app/evidence.py` | Appendix A: when an excerpt supports a value |
| `app/pages.py` | page kind and publication time |
| `app/candidates.py` | the selection rule, statuses and citations |
| `app/builds.py` | one background build: searches, rendered extracts, research, the 80-unit budget |
| `app/providers.py` | every provider call, through WebRouter's `nimble` backend |
| `tests/` | unit, API, migration and Playwright browser tests, plus the corpus checks |

## Tests

```sh
pip install -r requirements.txt pytest httpx playwright
python -m playwright install chromium      # only for tests/test_browser.py
python -m pytest tests -q
```

The corpus checks replay the build's gathering offline against a corpus set's
`key.json` (the organizers' own reading of Appendix A and of the selection rule):

```sh
python -m tests.validate_corpus --corpus <set> --challenge <dossier challenge dir>
python -m tests.validate_candidates --corpus <set> --challenge <dossier challenge dir>
```

`tests/test_browser.py` drives the real document with Playwright at a 375 px and a
desktop viewport; `tests/test_migration.py` imports a Stage 1 export and checks the
candidates it becomes.

Screens at both widths are in `screenshots/`.

## Upgrading from Stage 1

`POST /_test/import` accepts this team's Stage 1 export unchanged. Each imported
field gains one candidate per cited page (the excerpts Stage 1 kept), with no kind
(so aggregator authority) and the dossier's `researched_at` as its retrieval time;
accounts, tokens and stored idempotent responses are preserved. The next ready
build replaces those candidates with the ones it gathers.
