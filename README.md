# Dossier — scored result

The Dossier buildathon submission from a three-seat Band factory. Each stage is
a complete, independently buildable service frozen at its independently
approved revision. Stage 4 is the highest completed service.

```text
stage-1/ … stage-4/  complete service at each specification stage
mandates/            reusable Lead, Implementer and Reviewer operating rules
FACTORY.md           factory design, evidence, costs and failure recovery
room.json            immutable full Band session export
dossier-runway.com.json
                     live-web runway.com result from Stage 4
```

## Running the completed service

```sh
cd stage-4
docker build -t dossier-stage4 .
docker run --rm -p 8080:8080 -e PORT=8080 -e NIMBLE_API_KEY="<key>" dossier-stage4
```

See each stage's `RUN.md` for its API walkthrough, design and tests.

## Checking it against the official harness

From the organizers' tree (`sf-buildathon-2026`):

```sh
python -m harness check --track dossier <this repository>
python -m harness run --track dossier --repo <this repository> --stage 4 --mode isolated
python -m harness run --track dossier --repo <this repository> --all --mode isolated
```

`--mode isolated` is the grading mode: the service runs on an internal network
with no route out, and provider calls go to the mock web through WebRouter.

## Presentation
[YouTube](https://youtu.be/I9qdLDhrcQE)
[Dossier Factory Room Replay](https://claude.ai/artifact/T91Dywbz189UgH4gvxKPKZ)