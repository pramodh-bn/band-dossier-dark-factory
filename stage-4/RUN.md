# Dossier — Stage 3

A containerised HTTP service and browser product that researches a company by its
web domain. Each of the seven fields keeps every value its evidence supports as a
candidate, selects one by a fixed rule, and records the history of that selection:
when the value became true (`true_from`), when this service learned it
(`learned_at`), and every change in a feed. A dossier answers as it stood at an
earlier time, and the whole state survives an abrupt restart.

## Build and run

From this folder:

```sh
docker build -t dossier-stage3 .
docker run --rm -p 8080:8080 -e PORT=8080 \
  -e NIMBLE_API_KEY="<your nimble key>" \
  dossier-stage3
```

`PORT` is the only setting the service needs; it listens on `0.0.0.0:$PORT`
(default `8080`). `NIMBLE_API_KEY` is the provider credential; `NIMBLE_BASE_URL`
may point the Nimble SDK elsewhere. Every provider call goes through WebRouter's
`nimble` backend — searches, rendered extracts and research runs — so a build
works with Nimble alone.

State is written to `STATE_PATH` (default `/data/state.json`) after every
mutation, atomically (temp file, fsync, rename). The image creates `/data`, so a
`docker stop`/`docker start` or a `docker kill` followed by `docker start` of the
same container answers exactly as before the stop. Mount a volume at `/data` to
keep it across `docker rm`.

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
| `GET /_test/export`, `POST /_test/import` | move the whole state between processes |

`/dossiers/{domain}` is shared with the browser: `Accept: text/html` returns the
document, anything else the JSON API.

## Fields, candidates and the two times

The six fields of stage 1 plus `executives`, whose value lists the selected
`{title, name}` of `cfo`, `coo` and `cto` in that order. Each title is ranked on
its own by the stage-2 rule (authority, recency, URL, provider, value).

Every candidate carries `true_from` (the page's `Effective` date, else the UTC
date of its `Published` time, else null) and `learned_at` (the first build that
gathered that field, value, URL and provider). A selected value's `true_from` is
its candidate's; its `learned_at` is the build that made it selected.

A ready build appends one history entry per field whose selected value moved:
`initial` for the first value, `correction` when the new value's `true_from` is on
or before the old one, `change` otherwise. Every `change` and `correction` also
appears once in the feed, numbered in the §4 field order then `executives`.

## Cost

Ten lite searches (the first exactly the domain), one research run (25 units), then
rendered extracts (3 units a URL) in priority order — research-cited pages, the
company's own pages, pages whose search snippet carries field vocabulary, then the
rest — up to what the remaining 80-unit budget affords. The prefix is fixed before
anything is sent, so a build reads the same pages every time. `spent_units` equals
the provider's own cost log; refused calls cost nothing and are retried with a
bounded backoff.

## Layout

| Path | What it is |
|---|---|
| `app/server.py` | routing, validation, authentication, idempotency, content negotiation |
| `app/ui.py` | the browser document: markup, styles and the client app |
| `app/store.py` | state, builds, page versions, history, feed, persistence, upgrades |
| `app/evidence.py` | Appendix A, including the executives titles |
| `app/pages.py` | page kind, publication time and `true_from` |
| `app/candidates.py` | the selection rule, statuses, citations and the executives field |
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

`POST /_test/import` accepts this team's stage-1, stage-2 and stage-3 exports.

- **Stage 3** exports round-trip everything: builds, page versions, history, the
  feed and its sequence, so `?as_of=` and `/values` answer as they did.
- **Stage 2** exports keep the current candidates as one synthetic ready build at
  the dossier's `researched_at`, with one `initial` history entry per valued field
  and an empty feed. The stage-2 service did not export its earlier builds, so
  those are irrecoverable; the synthetic build is the strongest recoverable
  history. Its spending is the exported dossier's `spent_units`, so `?as_of=` at
  or after that build reports that total and an earlier `as_of` is 404.
- **Stage 1** exports become candidates from their citations, learned at their
  retrieval time, in the same synthetic build.

## Restarts and interrupted builds

The state file is written under the state lock after every mutation, so it always
holds a whole state: a dossier is never persisted as `building`, and a build's
history and feed entries are written together with its outcome or not at all.

A build interrupted by an abrupt stop therefore recovers to a defined
non-building state — the dossier's last committed state, or `failed` with unknown
fields when no build had completed — and the next refresh starts a fresh build.
Nothing is left stuck `building`, no partial or duplicate history or feed entry is
appended, and the last committed state stays readable.

Accounts, tokens, dossier ids, spending and completed idempotent responses survive
every import. An imported dossier has no `executives` candidates, so that field is
`unknown` until a build supplies it; the first build after an import compares its
values with the imported ones and records `change` entries where they moved.
