# Dossier — Stage 1

A containerised HTTP service that researches a company by its web domain and keeps
a dossier of six fields, each with the value the web supports and the excerpts it
came from.

## Build and run

From this folder:

```sh
docker build -t dossier-stage1 .
docker run --rm -p 8080:8080 -e PORT=8080 \
  -e NIMBLE_API_KEY="<your nimble key>" \
  dossier-stage1
```

`PORT` is the only setting the service needs; it listens on `0.0.0.0:$PORT`
(default `8080`). `NIMBLE_API_KEY` is the provider credential. `NIMBLE_BASE_URL`
may point the Nimble SDK at another endpoint; leaving it unset uses Nimble's own.
`TAVILY_API_KEY` and `TAVILY_BASE_URL` are optional and unused: every provider call
goes through WebRouter's `nimble` backend, so a build works with Nimble alone.

Then:

```sh
curl -s localhost:8080/health
# {"status": "ok"}

curl -s -X POST localhost:8080/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}'
# {"user_id": "u_1", "display_name": "Ada", "token": "..."}

curl -s -X POST localhost:8080/dossiers \
  -H "Authorization: Bearer <token>" \
  -H 'Idempotency-Key: 2f9c1a' \
  -H 'Content-Type: application/json' \
  -d '{"domain": "acme-robotics.test"}'
# 202 {"dossier_id": "d_1", "domain": "acme-robotics.test", "state": "building"}

curl -s localhost:8080/dossiers/acme-robotics.test -H "Authorization: Bearer <token>"
```

A dossier is built in the background: the first `GET` after a build finishes shows
`state: "ready"` and the six fields.

## The control endpoints

`POST /_test/reset`, `POST /_test/clock`, `GET /_test/export` and
`POST /_test/import` are enabled in this image, take no authentication and are the
only way to seed users, move the clock, or move state between processes.

## Layout

| Path | What it is |
|---|---|
| `app/server.py` | routing, validation, authentication, idempotency |
| `app/store.py` | state: clock, accounts, tokens, dossiers, exports |
| `app/evidence.py` | Appendix A: when an excerpt supports a value |
| `app/builds.py` | one background build: searches, extracts, research, the 80-unit budget |
| `app/providers.py` | every provider call, through WebRouter's `nimble` backend |
| `app/domain.py` | domain normalisation and validation |
| `tests/` | unit and HTTP tests, and a corpus-key check for the Appendix A engine |

## Tests

The implementation's own tests need only Python and the service's dependencies:

```sh
pip install -r requirements.txt pytest httpx
python -m pytest tests -q
```

`tests/validate_corpus.py` checks the Appendix A engine against a corpus set that
ships a `key.json` (the organizers' own reading of Appendix A):

```sh
python -m tests.validate_corpus --corpus <corpus dir> --challenge <dossier challenge dir>
```
