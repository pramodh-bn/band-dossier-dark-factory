# Dossier — scored result

The Dossier buildathon submission. Each stage lives in its own folder and is a
complete, independently buildable service; `stage-1/` is the company-profile
service this repository currently holds.

```
stage-1/            the stage 1 service, its Dockerfile, RUN.md and tests
stage-1/app/        the service
stage-1/tests/      unit and HTTP tests, plus the Appendix A corpus-key check
```

## Running stage 1

```sh
cd stage-1
docker build -t dossier-stage1 .
docker run --rm -p 8080:8080 -e PORT=8080 -e NIMBLE_API_KEY="<key>" dossier-stage1
```

See `stage-1/RUN.md` for the API walkthrough, the layout and the tests.

## Checking it against the official harness

From the organizers' tree (`sf-buildathon-2026`), with the mock web the harness
starts beside the service:

```sh
python -m harness run --track dossier --repo <this repository> --stage 1 --mode isolated
```

`--mode isolated` is the grading mode: the service runs on an internal network
with no route out, and every provider call it makes goes to the mock web through
WebRouter.
