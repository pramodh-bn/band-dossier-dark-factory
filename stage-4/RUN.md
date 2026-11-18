# Dossier — Stage 4

A containerised HTTP service and browser product that researches a company by its
web domain. Each of the eight fields keeps every value its evidence supports as a
candidate, selects one by a fixed rule, and records the history of that selection:
when the value became true (`true_from`), when this service learned it
(`learned_at`), and every change in a feed. Every value also says which independent
origins stand behind it. Researchers submit companies in batches under a shared
budget, a dossier answers as it stood at an earlier time, and the whole state
survives an abrupt restart.

## Build and run

From this folder:

```sh
docker build -t dossier-stage4 .
docker run --rm -p 8080:8080 -e PORT=8080 \
  -e NIMBLE_API_KEY="<your nimble key>" \
  dossier-stage4
```

`PORT` is the only setting the service needs; it listens on `0.0.0.0:$PORT`
(default `8080`). `NIMBLE_API_KEY` is the provider credential; `NIMBLE_BASE_URL`
may point the Nimble SDK elsewhere. Every provider call goes through WebRouter's
`nimble` backend — searches, rendered extracts and research runs — so a build
works with Nimble alone.

State is written to `STATE_PATH` (default `/data/state.json`) atomically (temp
file, fsync, rename). Structural mutations take a snapshot; a provider call's
spend checkpoint is appended to `STATE_PATH.journal` and fsynced before the call,
so a kill cannot lose a billed unit and a reader does not wait on a whole-state
write. The image creates `/data`, so a `docker stop`/`docker start` or a
`docker kill` followed by `docker start` of the same container answers exactly as
before the stop. Mount a volume at `/data` to keep it across `docker rm`.

## The API

| Route | What it answers |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `POST /auth/signup`, `POST /auth/login` | accounts and bearer tokens |
| `POST /dossiers`, `GET /dossiers` | start a build, list dossiers |
| `GET /dossiers/{domain}` | the dossier now, or `?as_of=<RFC 3339>` as it stood then |
| `GET /dossiers/{domain}/values?true_at=<YYYY-MM-DD>[&as_of=…]` | what the evidence held on that date |
| `GET /dossiers/{domain}/history` | every field's selected values, oldest first |
| `GET /changes?since=&limit=` | the change feed, `seq` ascending |
| `POST /dossiers/{domain}/refresh` | start a new build |
| `POST /batches`, `GET /batches/{id}` | research several companies under one budget |
| `GET /_test/export`, `POST /_test/import` | move the whole state between processes |

`/dossiers/{domain}` is shared with the browser: `Accept: text/html` returns the
document, anything else the JSON API.

## Fields, candidates, origins and the two times

The six fields of stage 1, `executives` (the selected `{title, name}` of `cfo`,
`coo` and `cto` in that order) and `investors` (the member names, sorted, whose
evidence is `own`/`filing` or spans two origins). Each title and investor name is
ranked by the stage-2 rule (authority, recency, URL, provider, value).

Every page has an origin: its `First-Published-By` domain, else the origin reached
through at most three `Canonical` URLs (the URL's host where no provider serves the
page), else the company's domain for a lookalike `Kind: own` page, else its
`Registrant` (or its URL's host). A `Kind: own` page is only `own` when its
registrant is the company's domain. A build fetches each original it needs through
WebRouter, and a fetched original is used for lineage only.

Every candidate carries `origin`, `true_from` (the page's `Effective` date, else the
UTC date of its `Published` time, else null) and `learned_at`. A field carries
`origins` (the selected members' candidates' distinct origins, sorted),
`corroborated` (every member has two or more) and `corroborated_since` (the first
ready build after which the selected value or every member had two or more standing
origins).

Every candidate carries `true_from` (the page's `Effective` date, else the UTC
date of its `Published` time, else null) and `learned_at` (the first build that
gathered that field, value, URL and provider). A selected value's `true_from` is
its candidate's; its `learned_at` is the build that made it selected.

A ready build appends one history entry per field whose selected value moved:
`initial` for the first value, `correction` when the new value's `true_from` is on
or before the old one, `change` otherwise. Every `change` and `correction` also
appears once in the feed, numbered in the §4 field order then `executives` and
`investors`. An investors entry is typed by name: an added name whose own
`true_from` is on or before the earlier value's makes a `correction` when every
name in exactly one value qualifies; otherwise it is a `change`.

## Cost

Ten lite searches (the first exactly the domain), one research run (25 units), then
rendered extracts (3 units a URL) in priority order - research-cited pages, the
company's own pages, pages whose search snippet carries scalar, executive-title or
investor vocabulary, then the rest - up to what the remaining 80-unit budget
affords. A small reserve is kept back from the page queue for the one-unit lineage
extracts that resolve a copy's canonical original. The prefix is fixed before
anything is sent, so a build reads the same pages every time. `spent_units` equals
the provider's own cost log; refused calls cost nothing and are retried with a
bounded backoff.

A batch runs one build per new dossier under a shared `budget_units` (1 to 100000)
besides each build's 80-unit cap. A domain that already has a dossier is not
rebuilt and costs nothing. When the shared budget cannot cover a call, no build
makes it; a build that never started any call completes with unknown fields, and
every build's spend counts in its dossier and in the batch.

## Layout

| Path | What it is |
|---|---|
| `app/server.py` | routing, validation, authentication, idempotency, content negotiation |
| `app/ui.py` | the browser document: markup, styles and the client app |
| `app/store.py` | state, builds, batches, standing origins, persistence, upgrades |
| `app/evidence.py` | Appendix A, including the executives titles and investors |
| `app/pages.py` | page kind, lineage, origin resolution and `true_from` |
| `app/candidates.py` | the selection rule, statuses, origins and the list fields |
| `app/builds.py` | one background build: searches, research, rendered extracts, the budget |
| `app/providers.py` | every provider call, through WebRouter's `nimble` backend |
| `tests/` | unit, API, temporal, concurrency, restart, upgrade and Playwright tests |

## Tests

```sh
pip install -r requirements.txt pytest httpx playwright
python -m playwright install chromium      # only for tests/test_browser.py
python -m pytest tests -q
```

The corpus checks replay the build's gathering offline against a corpus set's
`key.json`:

```sh
python -m tests.validate_corpus --corpus <set> --challenge <dossier challenge dir>
python -m tests.validate_candidates --corpus <set> --challenge <dossier challenge dir>
```

## Upgrades

`POST /_test/import` accepts this team's stage-1, stage-2 and stage-3 exports and
this service's own exports. A public export carries every completed batch with its
id, owner, budget, spend and ordered domains; import restores the batch sequence,
so a completed key replays its original response, `GET /batches/{id}` still answers
and a new batch gets a fresh id. A batch still running is represented by its
dossiers' settled state, not as a batch.

- **Stage 3** exports round-trip everything: builds, page versions, history, the
  feed and its sequence, so `?as_of=` and `/values` answer as they did. Candidates
  whose export records no origin take their URL's host; standing origins and each
  field's `corroborated_since` are rebuilt from the build times.
- **Stage 2** exports keep the current candidates as one synthetic ready build at
  the dossier's `researched_at`, with one `initial` history entry per valued field
  and an empty feed. A page version's `true_from` is recovered from the candidate's
  `published_at` where the export records one.
- **Stage 1** exports become candidates from their citations, learned at their
  retrieval time, in the same synthetic build, with the citation URL's host as
  their origin.
- An old export has no `investors` candidates: that field is `unknown` in the
  imported state and every kept build, has no history entry, and the first ready
  build with a value adds a `change` (or `initial` when it is the dossier's first
  ready build). `executives` behaves the same for stage-1/2 exports.

## Restarts and interrupted builds

The state file is written atomically for structural mutations, and it holds a
running build's job; each spend checkpoint is appended to the journal and fsynced
before its provider call. A process
that is killed mid-build therefore reads back the dossier as it was before the
build, with the units the build had spent, and ends that one logical build
`failed` while loading — before `/health` first answers. Fields stay as they were,
the spend stays in the dossier and in its batch, and `POST /refresh` starts a new
build normally. Nothing is left stuck `building`; no partial or duplicate history
or feed entry is appended. Batches and their budgets survive the restart too.

Accounts, tokens, dossier ids, spending, batches and completed idempotent
responses survive every import. The clock is unchanged by export or import.
