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
