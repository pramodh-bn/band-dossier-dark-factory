# Dossier Factory

## Team and operating model

This repository was produced by a three-seat Band factory:

- **Lead — Codex-1** (`gpt-5.6-sol`, medium): decomposes each stage, defines
  acceptance criteria, freezes approved baselines, resolves specification
  interpretations, and owns the final evidence gate.
- **Implementer — OpenCode** (`deepseek/deepseek-flash-latest`, high): changes
  only the active stage, writes production and regression tests, commits every
  reviewable revision, and reports measured evidence.
- **Reviewer — Claude** (`claude-haiku-5-5`, high): works from clean pinned
  clones, writes independent adversarial probes, runs the official isolated
  harness, and never edits the implementation it approves.

The reusable mandates are in `mandates/`. A stage advances only through:

1. an exact copy of the approved preceding stage;
2. a separate baseline-copy commit by the Lead;
3. implementation and a clean commit by the Implementer;
4. independent review at the pinned SHA;
5. correction and re-review until explicit approval;
6. freezing the folder before the next specification is opened.

This sequencing kept Stages 1–3 byte-stable while later stages evolved.

## What the factory delivered

| Stage | Frozen revision | Official isolated evidence |
|---|---|---|
| 1 | `9e7dff2cadd2623057e1b9c5fbcf07f74fb42944` | 122/122 |
| 2 | `c5c814791f2ad983e02c726a4c9e24e8a7333d98` | 122/122 + 12/12 |
| 3 | `259e889bb772b599b43238d41ca95ef115e13fa5` | 122/122 + 12/12 + 9/9 |
| 4 | `9eba7969ca1af377eb60713f2c78227679641b35` | 122/122 + 12/12 + 9/9 + 10/10 |

The final Stage 4 revision passed two independent clean-clone isolated runs,
with zero failures, errors, skips, leaks or fabrications, and a final `--all`
run in which every folder claimed exactly its completed stage.

## Design choices and why

- **Evidence before confidence.** Calls share explicit per-build and batch
  budgets. Selection is deterministic, citations come only from rendered
  evidence, and candidate-page lineage resolves before filler pages use the
  remaining budget.
- **Temporal state is data, not recomputation.** Builds, page versions,
  candidate and selected learning times, histories and the global feed persist
  so `as_of` and `true_at` stay stable.
- **Crash safety without blocking readers.** Structural state uses atomic
  snapshot replacement and fsync. Per-call spend uses an append-and-fsync
  journal written before the call; replay is sequence-guarded. This preserves
  billed units across SIGKILL without rewriting all state under the global lock
  for every provider call.
- **Upgrades preserve recoverable truth.** Imports never call providers.
  Earlier exports are migrated using exactly what they contain; unavailable
  pre-export text or history is documented rather than invented.
- **Stage folders are deployable artifacts.** Each has its own Dockerfile,
  source, tests and `RUN.md`, independent of later stages.

## How bad work was caught and recovered

The shipped checks were a floor, not the approval bar. Independent review found
and forced corrections for:

- first-attempt throttles and seeded-ID collisions;
- missing rendered-page and research-only candidates under the unit cap;
- unread-page candidates with invented provenance;
- selected-versus-candidate learning times and empty page versions;
- corrective page-version ordering;
- canonical copies counted as independent origins;
- batch export/import ID collisions;
- missing executive/investor-only pages in the priority queue;
- whole-state fsync latency during 50-domain batches; and
- a fixed lineage reserve that preferred fillers to affordable originals.

Each rejection named a clean SHA, exact files, a deterministic reproduction and
acceptance criteria. The Implementer made a new commit; the Reviewer re-ran the
original failing probe plus regressions. No defect was waived merely because
the official harness passed.

## Measured verification and economy

The final Stage 4 implementation reported 394 local tests passing (one local
Playwright skip when browsers were absent). Independent checks covered exact
provider-log/spend parity, shared budgets up to 50 domains, concurrent
idempotency, repeated Docker SIGKILL, real cross-stage migrations, responsive
UI races, custom canonical chains, investors, and randomized temporal models.

The final 50-domain stress probe kept reads below 3.7 seconds against the
5-second request limit. A custom eight-copy lineage corpus resolved all eight
served originals and stayed within 79 units.

Provider-credit and model-token costs were not exposed to the Lead runtime, so
the factory does not invent a dollar figure. Measured wall-clock and test
results remain in the Band transcript and commit history.

## Known limits

- If evidence pages and research genuinely exhaust 80 units, later pages or
  originals cannot be fetched; evidence and lineage are prioritized
  deterministically and the service reports only what it obtained.
- An interrupted build may recover as `failed`, as allowed; prior fields and
  checkpointed spend survive, and refresh works normally.
- Older exports cannot recreate page text or histories they never stored.
- Completed batches are public-exported; a running batch exports its dossiers'
  settled state rather than its in-flight job.

## Reproducing the result

```sh
python -m harness check --track dossier <this-repository>
python -m harness run --track dossier --repo <this-repository> --stage 4 --mode isolated
python -m harness run --track dossier --repo <this-repository> --all --mode isolated
```

See `stage-4/RUN.md` for deeper service-specific commands.



# Dossier Dark Factory — Engineering Record

> **Evidence basis:** BAND full-room export `room.json` (exported 2026-11-19T00:25:14Z), the frozen stage implementations, and `stage-4/RUN.md`. This document is a retrospective reconstruction, **not** a contemporaneously authored factory log. Statements attributed to agents are recorded reports and review verdicts; test outcomes are the reports preserved in the transcript, not fresh tests run in preparing this document.

## Executive summary

**The product was Dossier; the experiment was the factory.** A three-seat BAND software factory built a four-stage, containerized company-research service. A Codex Lead delegated work and controlled stage transitions; an OpenCode Implementer built, tested and repaired the service; a Claude Reviewer independently inspected frozen commits, created adversarial probes and withheld approval until defects were closed.

The final independently approved revision was **`9eba7969ca1af377eb60713f2c78227679641b35`**. The Reviewer reported two fresh-clone isolated runs at that revision, each with **Stage 1 122/122, Stage 2 12/12, Stage 3 9/9 and Stage 4 10/10** (153/153), no failed tests, errors, skips, leaks or fabrications, and `claimed_stage: 4`. These are **reported independent review results**, not a claim that all later submission-environment checks were completed.

The distinctive outcome is the quality gate: **green tests were necessary, not sufficient**. Every stage was subject to independent review; several green implementations were rejected for specification-level defects and fixed before progression.

Factory execution cost: $46.78 USD (BAND-reported). This is the total cost shown by BAND for the four-stage Dossier factory run. Individual agent and stage-level cost breakdowns have not been independently verified.

## 1. Factory design and operating contract

| Seat | BAND identity | Responsibility | Authority |
|---|---|---|---|
| Lead | `@pramodh.nanjayya/lead-codex-1` (Codex) | Interpret current stage, define acceptance criteria, assign work, reconcile evidence, enforce stage boundaries | Authorize advancement only after independent approval and a clean committed revision |
| Implementer | `@pramodh.nanjayya/implementer-opencode` (OpenCode) | Implement complete stage, tests, Dockerfile and `RUN.md`; execute harness; fix defects; commit | Change the active stage only; supply reproducible evidence |
| Reviewer | `@pramodh.nanjayya/reviewer-claude` (Claude) | Independently inspect code, run isolated harness and adversarial tests, reproduce failures | Approve or request changes; do not edit implementation |

The user's opening mandate, sent **2026-11-17 20:47:57 UTC**, required:

- Start with Stage 1 and **do not read later specifications early**.
- Complete implementation, tests, review, fixes and Git commit before advancing.
- Copy each approved stage into the next stage's folder; **never alter a frozen earlier stage**.
- Deliver an independently buildable container and `RUN.md` per stage.
- Use the official harness in isolated WSL2/Docker mode; run checks sequentially.
- Keep credentials out of Git and logs; implement the specification, not merely visible tests.
- Operate without human debugging or steering; report blockers rather than wait for intervention.

The Lead established BAND tasks for all four stage gates and a final report, then sent distinct assignments to Implementer and Reviewer. The Reviewer prepared a risk-based checklist before reviewing the first implementation. Review was pinned to **exact commit SHAs**, frequently using fresh clones and isolated runs.

### The gate protocol

```text
Current-stage specification
        ↓
Lead defines acceptance criteria and delegates
        ↓
Implementer builds, tests, documents and commits
        ↓
Lead checks clean tree, freeze boundaries and harness evidence
        ↓
Reviewer independently tests the pinned revision
        ↓
CHANGES REQUESTED ──→ Implementer reproduces, fixes, commits ──→ re-review
        │
        └── APPROVED → freeze stage → copy to next folder → open next specification
```

This separation mattered in practice. The Lead rejected a Stage 1 handoff when `.gitignore` and `.dockerignore` were still uncommitted, even though the implementation passed its tests. The Reviewer also rejected multiple stages despite green harness results.

## 2. Four-stage delivery and frozen revisions

| Stage | Delivered capability | Independent approval revision | Isolated harness evidence at approval |
|---|---|---|---|
| 1 | Domain research, six-field dossier, citations, authentication, background builds, budget and export/import | `9e7dff2cadd2623057e1b9c5fbcf07f74fb42944` | 122/122; Reviewer approved 2026-11-17 22:53 UTC |
| 2 | Browser experience, evidence candidates and ranking, routes and migration | `c5c814791f2ad983e02c726a4c9e24e8a7333d98` | Stage 1 122/122 and Stage 2 12/12; approved 2026-11-18 01:39 UTC |
| 3 | Temporal values (`true_from`, `learned_at`), executives, history/change feed, durable restart and migrations | `259e889bb772b599b43238d41ca95ef115e13fa5` | 122/122 + 12/12 + 9/9; approved 2026-11-18, before Stage 4 began |
| 4 | Independent origins and corroboration, investors, shared-budget batches, interrupted-build recovery | `9eba7969ca1af377eb60713f2c78227679641b35` | 122/122 + 12/12 + 9/9 + 10/10, twice from a fresh clone; approved 2026-11-18 20:19 UTC |

The Lead reported copying Stage 3 byte-for-byte to the Stage 4 baseline at `72e3603ea11b1291c4a89febb788e2294197a40a` **before opening** the Stage 4 specification. At final approval the Reviewer verified no drift in `stage-1/`, `stage-2/` or `stage-3/` relative to their approved revisions.

## 3. The most instructive failures and recoveries

### Case A — Stage 1: tests passed; resilience and identity were wrong

At clean revision `987911bc7ac1d1859accf853889e94fe7f649355`, the official isolated harness passed **122/122**. The Reviewer nevertheless returned **CHANGES REQUESTED** for two reproduced defects:

1. **Provider refusal handling:** first-attempt HTTP 429 throttles were not retried, causing builds to fail or lose supported fields even though the provider could answer on a subsequent attempt.
2. **Seeded user-ID collision:** after reset, signup could reuse `u_1` and overwrite an existing seeded account.

The Implementer added bounded retry/backoff for retryable refusals, preserved the 80-unit budget and provider-log spend parity, and allocated user IDs without colliding with existing users. The Reviewer reran the original probes and approved `9e7dff2`. **Lesson:** a green sample suite does not establish failure-mode correctness or identity safety.

### Case B — Stage 2: candidate completeness versus budget allocation

Stage 2's official harness passed, but the Reviewer found a substantive evidence-selection problem. The implementation could leave useful gathered pages unread under a fixed research/render budget, losing candidates. An initial repair closed one failure but introduced **D1b**, a different loss of research evidence. The Reviewer also challenged a **D2 research-claim fallback** that could assert a value without adequate support.

The final repair prioritized evidence-bearing pages while retaining both research and rendered evidence, and removed unsupported fallback assertions. The Reviewer approved `c5c8147` after focused re-tests. **Lesson:** optimizing the number of pages or candidates is not enough; provenance and support must survive the optimization.

### Case C — Stage 3: temporal semantics resisted simple fixes

Stage 3 introduced two time dimensions: when a fact was true and when the service learned it. Independent probes found defects around temporal version selection, preservation of the build that selected a value, and the end of a superseded interval. The repair sequence included `3bf9048`, `320a0bd`, `895058d` and the final approved `259e889`.

One proposed correction introduced an invented cutoff; the Lead rejected it, and the Implementer removed it so the corrected value held forward as required. The Reviewer approved only after re-running temporal matrices, migrations, concurrency and real Docker **SIGKILL/restart** tests. **Lesson:** historical correctness needs an explicit reference model and adversarial time sequences, not only happy-path history snapshots.

### Case D — Stage 4: four failures behind green tests

At `9ad4483ef40697fe32c7d5b1487d23f784f1b4b9`, the official harness passed, but the Reviewer requested changes for:

- **D1 — incorrect origins:** a fixed extraction plan could exhaust the 80-unit budget before fetching canonical originals, making copies look like independent publishers.
- **D2 — batch persistence:** completed batches disappeared across export/import and batch IDs could collide.
- **D3 — incomplete triage:** the priority queue missed pages containing only executive or investor statements.
- **D4 — latency:** full-state fsync under a global lock caused a 50-domain batch read to take **5.33 seconds**, beyond the 5-second request target.

At `d857133`, D2–D4 were closed, but the Reviewer **again rejected D1**: a fixed reserve for canonical fetches still failed an eight-copy synthetic case. The Implementer replaced the reserve with an **adaptive two-phase plan**: read cited/own/triaged evidence first, resolve the lineages of candidate-bearing pages (up to three canonical hops), then spend the remaining budget on filler pages.

At `9eba796`, the independent synthetic `s4z` probe recorded **79 units spent = 79 provider-log units**, all eight originals fetched, correct origins `pub0`–`pub7`, and two filler pages. The Reviewer then approved. **Lesson:** a fixed heuristic can pass common fixtures yet violate an invariant; schedule mandatory provenance work before optional exploration.

## 4. Verification evidence

The final Reviewer report, pinned to `9eba796`, records:

| Verification | Recorded outcome |
|---|---|
| Official isolated harness | **Two fresh-clone runs**, each **153/153** across four stages; no failures/errors/skips/leaks/fabrications |
| Sample-corpus origin completeness | `s4p1`: **153 candidates**, zero missing and zero origin mismatches; `s4p3`: **96 fields**, zero origin differences |
| Canonical lineage | Eight-copy `s4z` and multi-hop `s4eta` probes passed; fourth-hop boundary exercised |
| Batch budgets/idempotency | 40 same-key concurrent requests yielded one creation and 39 replays; spend matched mock provider log; shared budgets never exceeded |
| Export/import and upgrades | Completed batch `b_1` replayed after import; next ID `b_2`; Stage 1/2/3 migration probes preserved applicable state without provider calls |
| Crash durability | Multiple same-container `docker kill`/`docker start` trials, interrupted refreshes and batches; spend reconciled and no dossier remained indefinitely `building` |
| Browser | Route/test-ID/Accept tests and 375/768/1280-pixel checks reported passing |
| Load/latency | 50-domain batch with pollers: p95 read ~0.03 s, max **3.40 s** in one final probe; another observed max **3.68 s**, below 5 s but with limited margin |
| Local tests | Implementer reported **394 passing** local tests near the final Stage 4 revision; the independent harness is the primary submission-level evidence |

**Scope of the claim:** these are results recorded in the BAND transcript by the Implementer and independent Reviewer. They should be reproduced by judges using the frozen repository and organizer harness. The Reviewer explicitly did **not** run host mode and did not claim exhaustive verification of every temporal corroboration sequence.

## 5. Technical architecture and economics

Each `stage-N/` folder is a complete, independently buildable HTTP service with its own Dockerfile, application, tests and `RUN.md`. The final implementation uses Python and WebRouter's `nimble` backend for provider calls. `stage-4/RUN.md` identifies the main modules:

- `server.py`: HTTP routing, validation, authentication and idempotency.
- `ui.py`: browser interface.
- `store.py`: persistent dossiers, builds, batches and migrations.
- `evidence.py`, `pages.py`, `candidates.py`: evidence parsing, page lineage and deterministic selection.
- `builds.py`, `providers.py`: background research orchestration, provider calls and budgeting.

A single dossier build is capped at **80 provider units**. The documented plan begins with ten lite searches and one 25-unit research run, then rendered extracts and canonical-origin lookups, all constrained by the budget. Stage 4 batches add a shared `budget_units` ceiling. Refused calls are accounted for according to provider billing rules, and spend is checked against the provider's cost log.

Persistence uses atomic state snapshots plus a write-ahead spend journal: billed units are checkpointed and fsynced before provider calls. After an interrupted build, recovery marks that build failed, preserves prior selected fields, and accounts for the units already incurred. This is **application provider-unit accounting**, not a measured dollar cost of the AI agents.

### AI-agent cost and elapsed time

The room's initial mandate was posted at **2026-11-17 20:47:57 UTC** and final Stage 4 approval was posted at **2026-11-18 20:19:47 UTC**, an elapsed **approximately 23 hours 32 minutes** between those events. This is **wall-clock span**, not active compute time or human-free execution time; the record includes pauses, sequential tests, re-reviews and later submission work. The export extends to 2026-11-19 00:24 UTC.

The provided room export does not establish a reliable consolidated total of Codex, OpenCode and Claude token consumption or USD model charges. **Agent dollar cost: not verified / not reported.** Do not substitute the service's 80-unit provider budget for agent inference costs.

## 6. Residual limitations and boundaries

The final Reviewer explicitly documented nonblocking risks:

- **Latency margin:** final 50-domain poller reads peaked around 3.4–3.7 seconds versus a 5-second target; provider behavior and workload may change that margin.
- **Snippet-limited triage:** a value stated only after a long preamble may be absent from the first ~300 characters of a lite snippet and fall into the lower-priority filler queue.
- **Genuine budget exhaustion:** if evidence gathering consumes the remaining budget, some canonical originals cannot be fetched; fallback attribution then follows the specified boundary behavior.
- **In-flight batch export:** a batch still running at export time is not included in the public completed-batch export.
- **Verification limits:** no host-mode harness run, no exhaustive `corroborated_since` reference-model sequence, and no general-purpose 50-way load test beyond the recorded batch/poller exercises.

The later **submission-preparation** phase is separate from the autonomous software-factory build. The Lead attempted to export the BAND transcript through desktop UI automation, encountered repeated permission/UI-snapshot interruptions, and recommended manual export. The user subsequently supplied the transcript. A live `runway.com` result and submission checker work were also handled outside the final autonomous implementation approval. Those actions should not be misrepresented as autonomous factory execution.

## 7. Reproduction and audit trail

From the official organizer checkout (`sf-buildathon-2026`), with Docker and a compatible Python environment available:

```sh
python -m harness check --track dossier /path/to/dossier-scored-result
python -m harness run --track dossier --repo /path/to/dossier-scored-result --stage 4 --mode isolated
python -m harness run --track dossier --repo /path/to/dossier-scored-result --all --mode isolated
```

On the original Windows workstation, the isolated harness was run from **WSL2**, using Linux paths such as `/mnt/d/Workspace/dark-factory-buildathon/dossier-scored-result`. On Windows, the checker may require `PYTHONUTF8=1` to decode the UTF-8 BAND export. See the official harness documentation for environment prerequisites.

Run the final service:

```sh
cd stage-4
docker build -t dossier-stage4 .
docker run --rm -p 8080:8080 -e PORT=8080 -e NIMBLE_API_KEY="<key>" dossier-stage4
```

**Primary audit artifacts:**

1. `room.json` — unchanged BAND full-session export; source of messages, timestamps, role handoffs, reviewer verdicts and reported tests.
2. Git history — commit-level implementation and repair evidence, especially the four approved revisions listed above.
3. `stage-1/` through `stage-4/` — independently buildable frozen services.
4. Each stage's `RUN.md` — architecture, API and reproduction details.
5. Organizer harness `runs/` reports — local generated test evidence, where retained; paths mentioned in the transcript may not be committed to the result repository.
6. Reviewer's synthetic probe scripts and corpora — referenced under the separate local `dossier-factory/review-probes` and `corpus-s4*` workspaces; **not assumed to be included in this submission repository**.

## 8. What the experiment demonstrated

The most important result was not raw code generation. It was a **repeatable engineering control loop** in which a Lead held the acceptance boundary, an Implementer supplied working revisions, and a separate Reviewer could disprove correctness with fresh tests even after the official harness was green. Immutable stage snapshots and exact commit pinning made those decisions auditable.

The evidence also identifies the limits of the experiment: test coverage is finite, some heuristics have known edge cases, and the transcript does not measure total inference cost. Within those boundaries, the final approved service and the documented rejections show a factory that **repaired specification-level failures rather than merely optimizing for visible test success**.

