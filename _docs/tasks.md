# Phase 0 and Phase 1 tasks

Issues are ordered by dependency. `OWNER` means Sam implements the issue by hand because it touches an owner-controlled file, the LLM client transport, or the Kafka consumer loop.



# Task 01 — Phase 0 — Download and inspect the first ZTF archive night

## Goal

Download one 2023 ZTF nightly Avro archive and establish the repeatable download and inspection contract used by the remaining nights.

## Acceptance criteria

- [ ] One chosen 2023 nightly archive is downloaded from the public ZTF alert archive without modifying its payloads.
- [ ] The archive source URL, observing date, byte size, and checksum are recorded in a committed manifest.
- [ ] A repeat run detects the verified local archive and does not download it again.
- [ ] The inspection reports the member count, readable Avro count, alert count, unique object count, and corrupt-member count.
- [ ] The minimum-night threshold `N` is set from the inspection, documented with its rationale, and used to classify the inspected night as eligible or skipped.
- [ ] <!-- EDIT: added, storage decision --> The night is extracted to one Parquet file containing the fields listed in `plan.md` plus the science, reference, and difference cutouts; the tarball is deleted after the Parquet checksum is recorded.
- [ ] Archive and Avro data files are excluded from Git; only manifests and small test fixtures are tracked.
- [ ] Network-free tests cover manifest parsing, checksum mismatch, an unreadable member, and the eligible/skip boundary using inline or tiny local fixtures.

## Out of scope

- Downloading all selected nights, applying the real/bogus cut, TNS matching, or loading Postgres.

## Constraints

- Preserve original Avro payload bytes in the Parquet row.
- Use `uv add` only after Sam approves any new dependency.
- Keep network access outside unit tests.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 02 — Phase 0 — Freeze and download the 2023 night set

## Goal

Select the ten eligible 2023 archive nights closest to six-week increments and download them reproducibly, using the seed and threshold decisions in the plan.

## Acceptance criteria

- [ ] The selection process chooses ten eligible 2023 nights closest to six-week increments and skips any night below `N` alerts in favor of the next eligible night.
- [ ] `data/nights.txt` contains the selected dates in processing order with no duplicates.
- [ ] The committed manifest records a source URL, byte size, and checksum for every selected archive.
- [ ] Every listed archive downloads successfully, passes checksum validation, and is extracted to Parquet per Task 01.
- [ ] Re-running selection with the same seed produces byte-identical `data/nights.txt` and manifest content.
- [ ] An interrupted or partial download is not accepted as a valid archive.
- [ ] Network-free tests cover nearest-date selection, ties, below-`N` skipping, duplicate prevention, and deterministic output.

## Out of scope

- Selecting extra nights for an undersized TNS truth set, parsing candidate records, or computing evaluation metrics.

## Constraints

- Treat `_docs/plan.md` as authoritative for the selection rule.
- Do not commit downloaded archives or Parquet files.
- Use the single project seed that will be recorded in `release.yaml`; this issue may accept it as an input but must not edit that file.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 03 — Phase 0 — Parse alerts and define the nightly candidate universe

## Goal

Convert archived alerts into validated internal records and deterministically identify every unique object detected on a night after the real/bogus cut.

## Acceptance criteria

- [ ] A pure parser converts a valid ZTF alert record into a typed record containing the object id, coordinates, observation date, candid, broker probabilities when present, and the original payload bytes or a lossless reference to them.
- [ ] <!-- EDIT: named the cut --> The real/bogus cut is `drb >= 0.5`, `isdiffpos` true, `nbad == 0`, read from `release.yaml`, and applied before object deduplication.
- [ ] Multiple detections of the same object on one night yield one candidate while retaining the object's eligible alert history through that night.
- [ ] Alert history excludes observations after the replayed night.
- [ ] Missing optional broker probabilities do not reject an otherwise valid alert.
- [ ] Malformed required fields and invalid coordinates produce explicit validation failures rather than silent drops.
- [ ] Network-free tests cover valid parsing, the cut boundary, duplicate objects, missing optional probabilities, invalid coordinates, and future-history exclusion.

## Out of scope

- Postgres persistence, TNS matching, replay publication, and model prompts.

## Constraints

- Parsing and candidate selection must be deterministic pure functions.
- Broker probabilities must remain distinguishable so later RL datasets can mask them.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 04 — Phase 0 — OWNER: Define local Postgres and Redpanda services

## Goal

Sam creates the first local Compose definition for one Redpanda broker and Postgres so later ingest and replay issues have stable service endpoints.

## Acceptance criteria

- [ ] `docker-compose.yml` defines exactly one Redpanda service and one Postgres service with named persistent storage.
- [ ] Both services have health checks, bounded local ports, and restart cleanly without losing Postgres data.
- [ ] Redpanda exposes a client endpoint usable from the host and a service endpoint usable inside Compose.
- [ ] Configuration documents how to start, check, and stop the services without deleting persisted data.
- [ ] Secrets are supplied through ignored environment configuration and no credential is committed.
- [ ] Starting the stack from a clean machine state reaches healthy status for both services.

## Out of scope

- Kafka topic creation, producer or consumer code, application tables, and production hardening.

## Constraints

- OWNER: Sam writes `docker-compose.yml` by hand.
- Postgres is the durable store; Redpanda is transport only.
- Use one Redpanda container as decided in `_docs/plan.md`.

## Do not edit

- `.github/`
- `Dockerfile`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 05 — Phase 0 — Persist nights, alerts, candidates, and run state

## Goal

Create the Phase 0 Postgres schema and idempotent loaders for archive metadata, original alerts, nightly candidates, and processing runs.

## Acceptance criteria

- [ ] The schema stores night manifests, original alert payloads or stable lossless references, candidate coordinates, observation times, broker probabilities, and run status.
- [ ] Database constraints prevent duplicate alerts by stable alert identity and duplicate nightly candidates by night plus object id.
- [ ] Loading the same night twice leaves row counts and stored payload identity unchanged.
- [ ] A failed load rolls back without leaving a partially loaded night marked complete.
- [ ] A query returns the exact candidate universe for a requested night in deterministic order.
- [ ] <!-- EDIT: migration decision --> Migrations are plain numbered SQL files under `src/vetter/store/migrations/`, applied by a function that records applied versions in a table; they apply successfully to an empty Postgres database.
- [ ] Tests cover duplicate loads, rollback, required-field constraints, and deterministic retrieval without network access.

## Out of scope

- TNS records and matches, Kafka offsets, model outputs, and production database operations.

## Constraints

- Postgres is the source of durable processing state.
- `psycopg` is the approved driver. No migration framework.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 06 — Phase 0 — Acquire and freeze TNS spectroscopic truth

<!-- EDIT: rewritten for the CSV source. API, credentials, pagination, and rate limit criteria removed. -->

## Goal

Download the TNS daily public objects CSV into a dated, checksummed local snapshot and normalize it without treating unclassified objects as negatives.

## Acceptance criteria

- [ ] The fetch records the download URL, retrieval date, byte size, and checksum of the CSV in a committed manifest.
- [ ] The raw CSV is cached locally and excluded from Git; a repeat run rebuilds normalized records from it without network access.
- [ ] Normalized records preserve TNS object id, coordinates, discovery date, spectroscopic class when present, classification date when present, and source metadata.
- [ ] Records without a spectroscopic classification remain distinguishable from labeled positives.
- [ ] A malformed or truncated CSV produces an actionable error, not partial truth.
- [ ] Network-free tests use a frozen fixture for normalization, missing classification, malformed rows, and the classification date boundary.

## Out of scope

- Positional matching, taxonomy mapping, metric calculation, and model access to TNS data.

## Constraints

- Persist retrieval provenance so truth can be reconstructed later.
- TNS truth is evaluation data and must not enter replay-time model inputs.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 07 — Phase 0 — Match TNS truth to nightly candidates

<!-- EDIT: split. The extra night loop moved to Task 08. -->

## Goal

Match nightly candidates to TNS spectroscopic objects within 2 arcsec and map matches to the project taxonomy.

## Acceptance criteria

- [ ] Spherical separation is computed deterministically and only matches at or below 2 arcsec are accepted.
- [ ] Ambiguous multiple matches follow a documented deterministic rule and retain separation and source ids for audit.
- [ ] Spectroscopic classes map to SN Ia, SN II, SN Ibc, SLSN, TDE, AGN, CV, variable star, asteroid, or bogus, with TDE and SLSN also flagged rare.
- [ ] Unmatched candidates are stored as unlabeled and are never represented as negative examples.
- [ ] Matching results and TNS provenance are persisted in Postgres and can be joined to the nightly candidate universe.
- [ ] A report gives the labeled positive count per night and in total.
- [ ] Network-free tests cover the 2 arcsec boundary, no match, ambiguous matches, taxonomy mapping, and rare flags.

## Out of scope

- Extra night selection, precision/recall computation, Wilson intervals, and any use of labels in the online pipeline.

## Constraints

- Catalog or literature information dated after the replayed night must not become model-visible state.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 08 — Phase 0 — Extend the night set to 300 labeled positives

<!-- EDIT: new issue, split out of the old Task 07. -->

## Goal

If the first ten nights contain fewer than 300 labeled positives, add eligible 2023 nights reproducibly until the target is met.

## Acceptance criteria

- [ ] Additional nights are selected randomly with the project seed from eligible 2023 nights not already in `data/nights.txt`, honoring the minimum alert threshold `N`.
- [ ] Each added night is downloaded, extracted, loaded, and matched using Tasks 01 through 07 unchanged.
- [ ] `data/nights.txt` is appended in selection order and the manifest is updated.
- [ ] The loop stops at the first night that brings the labeled positive total to at least 300.
- [ ] The final report gives the actual labeled positive count and never pads or resamples cases.
- [ ] Network-free tests cover seeded selection, exclusion of already selected nights, and the stopping rule.

## Out of scope

- Any change to matching, parsing, or download logic.

## Constraints

- Selection is deterministic for the seed.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 09 — Phase 0 — Pin and freeze AstroAlertBench

<!-- EDIT: narrowed to download and pin. Running the comparison needs a triage model and moves to phase 2. Fallback clause deleted. -->

## Goal

Download the AstroAlertBench dataset and code at fixed revisions, verify their contents, and freeze them as a local fixture.

## Acceptance criteria

- [ ] The Hugging Face dataset revision and GitHub commit are recorded in a handoff note for the owner `release.yaml` task.
- [ ] The dataset is verified to contain 1,500 alerts across five classes, 300 per class, and the verification is a network-free test against the frozen local copy.
- [ ] The paper's confidence calibration protocol is summarized in `docs/astroalertbench.md` with the exact metrics it reports.
- [ ] The frozen copy is excluded from Git; a manifest with checksums is committed.

## Out of scope

- Running any model on the benchmark. That is a phase 2 issue.

## Constraints

- Classifier labels are not ground truth and the note says so.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 10 — Phase 1 — OWNER: Define release configuration and the traced LLM client

## Goal

Sam defines the Phase 1 release contract and the only model-call path, with separate local Qwen endpoints for triage and vetter and an OpenTelemetry span for every request.

## Acceptance criteria

- [ ] `release.yaml` records the single seed, the real/bogus cut, separate triage and vetter provider:model values, prompt hashes, tool-schema hash, AstroAlertBench Hugging Face revision, and AstroAlertBench GitHub commit.
- [ ] Phase 1 defaults identify a small current Qwen instruct model for triage and a 30B-class Qwen MoE at 4-bit for vetter through `mlx-lm` OpenAI-compatible endpoints.
- [ ] `src/vetter/llm/client.py` is the only module that performs model HTTP calls and selects configuration by role.
- [ ] Each call emits an OpenTelemetry span with role, provider, model identity, prompt hash, latency, token counts when available, cache status, and success or error without recording secrets.
- [ ] Responses are validated into typed results and provider errors, timeouts, and invalid payloads become explicit client errors.
- [ ] A repository search confirms there are no model HTTP calls outside `src/vetter/llm/client.py`.

## Out of scope

- The cached response transport (Task 11), triage prompt design, vetter tools, final model selection, vLLM deployment, and production telemetry storage.

## Constraints

- OWNER: Sam writes `release.yaml` and `src/vetter/llm/client.py` by hand.
- Model identity must be captured per evaluation run.
- Never place secrets or full sensitive prompts in telemetry attributes.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `src/vetter/ingest/consumer.py`

# Task 11 — Phase 1 — OWNER: Cached response transport for tests

<!-- EDIT: new issue. Tasks 12 through 16 all assume this exists. -->

## Goal

Sam adds a transport to the LLM client that serves recorded responses from `tests/fixtures/responses/` keyed by prompt hash and model id, so no test ever calls a model.

## Acceptance criteria

- [ ] The client accepts a transport; the default is HTTP, the test transport reads fixtures.
- [ ] A fixture is keyed by a hash of the full request (role, model id, messages, tool schemas) and stores the response, model id, and recording date.
- [ ] A missing fixture fails the test with the hash and the request it would have sent, never by calling the network.
- [ ] A `record` mode writes a new fixture from a real call and is never used in CI.
- [ ] A pytest fixture in `tests/conftest.py` installs the test transport for every test.
- [ ] Spans still emit under the test transport with cache status set.

## Out of scope

- Any application use of the cache. Runtime caching is a phase 8 issue.

## Constraints

- OWNER: Sam writes this by hand; it lives inside `src/vetter/llm/`.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `src/vetter/ingest/consumer.py`

# Task 12 — Phase 1 — Publish original alerts to the replay topic

## Goal

Replay one archived night by publishing each original Avro payload, plus separate replay metadata, to the `ztf.alerts` Redpanda topic in deterministic order.

## Acceptance criteria

- [ ] The publisher reads a night from its Parquet file and sends the original Avro payload bytes unchanged to `ztf.alerts`.
- [ ] Replay metadata includes run id, source night, source archive identity, sequence number, and original alert identity without mutating the Avro payload.
- [ ] The same seed and archive produce the same publication order and message keys.
- [ ] A replay can resume after interruption without silently omitting remaining alerts.
- [ ] Missing Parquet files, checksum failures, corrupt payloads, and broker unavailability produce explicit failures and a non-success run state.
- [ ] Publisher spans report the run id and counts attempted, published, skipped, and failed.
- [ ] Unit tests use a fake producer and contain no network calls; a local integration test verifies messages on `ztf.alerts` when Redpanda is available.

## Out of scope

- Consuming messages, live-broker ingestion, triage, and ranking.

## Constraints

- Preserve the archived Avro bytes exactly.
- Redpanda is not the durable source of processing truth.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 13 — Phase 1 — OWNER: Consume replay alerts durably

## Goal

Sam implements the Kafka-compatible consumer loop that processes `ztf.alerts` as a consumer group and records each alert durably before acknowledging it.

## Acceptance criteria

- [ ] `src/vetter/ingest/consumer.py` consumes `ztf.alerts` with an explicit consumer-group id and disables auto-commit.
- [ ] A message is acknowledged only after its alert and processing state are committed to Postgres.
- [ ] Re-delivery of the same alert is idempotent and does not duplicate alerts or nightly candidates.
- [ ] A failure between database commit and acknowledgement is recoverable by safe reprocessing.
- [ ] Invalid messages are recorded with an actionable error and do not cause an infinite tight retry loop.
- [ ] The loop supports graceful shutdown and leaves uncommitted work available for another consumer.
- [ ] Consumer spans include run id, topic, partition, offset, alert identity, result, and processing latency.
- [ ] Tests exercise duplicate delivery, handler failure, commit failure, invalid input, and shutdown using fakes without network access.

## Out of scope

- Live broker consumption, multiple-service scaling, dead-letter infrastructure, and triage logic.

## Constraints

- OWNER: Sam writes the Kafka consumer loop by hand.
- Postgres is authoritative for completed processing; Kafka offsets alone are insufficient.
- The structure must remain compatible with the phase 5 live-broker path.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`

# Task 14 — Phase 1 — Add typed single-call triage

## Goal

Triage every unique nightly candidate with one cheap model call and return a validated keep/drop decision, coarse class, confidence, and rationale for downstream ranking.

## Acceptance criteria

- [ ] Triage receives only alert history dated on or before the replayed night and never receives TNS truth or future catalog information.
- [ ] Each candidate causes at most one call through `src/vetter/llm/client.py`.
- [ ] Broker probabilities are included in the Phase 1 triage input when present and their presence is recorded so future RL exports can mask them.
- [ ] Output validates into a typed keep/drop decision, project taxonomy class, bounded confidence, and concise rationale.
- [ ] Missing broker probabilities, invalid model output, timeout, and client error each produce a documented deterministic handling result.
- [ ] Triage results store model identity, prompt hash, release identity, and candidate id in Postgres.
- [ ] Tests cover keep, drop, missing probabilities, future-data exclusion, invalid output, and client failure using the cached response transport.

## Out of scope

- Tool-using vetter behavior, RL training, confidence-band escalation, and direct provider calls.

## Constraints

- Triage remains a single call, not an agent loop.
- Do not bypass or modify the owner-written LLM client.
- Tests must not call a model or the network.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 15 — Phase 1 — Rank a deterministic nightly shortlist of 25

## Goal

Rank triage survivors deterministically by rarity and confidence and produce exactly the best available 25 recommendations for a night.

## Acceptance criteria

- [ ] Dropped candidates are excluded before ranking.
- [ ] The score is a documented deterministic function of rarity and confidence with no model or network call.
- [ ] TDE and SLSN receive the taxonomy's rare flag and the ranking behavior for rare versus non-rare candidates is covered by tests.
- [ ] Stable tie-breaking yields byte-identical ordering for identical inputs.
- [ ] The shortlist contains exactly 25 unique candidates when at least 25 survivors exist and all unique survivors when fewer exist.
- [ ] Each ranked row stores candidate id, rank, component values, final score, run id, and release identity for audit.
- [ ] Tests cover ties, duplicate inputs, fewer than 25 survivors, exactly 25, more than 25, and invalid confidence values.

## Out of scope

- Human approval, telescope actions, tool-using vetter escalation, and learned ranking.

## Constraints

- `k = 25` is fixed by `_docs/plan.md`.
- Ranking must be a pure deterministic function.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 16 — Phase 1 — `vetter` CLI

<!-- EDIT: new issue. Task 18 requires one command and nothing created it. -->

## Goal

Expose the pipeline as a Typer CLI so a night can be downloaded, replayed, and run from one entry point.

## Acceptance criteria

- [ ] `vetter download --night YYYY-MM-DD`, `vetter replay --night`, `vetter run --night`, and `vetter status --run-id` exist and are registered as the `vetter` script in `pyproject.toml`.
- [ ] Every command prints the run id and release identity it is operating under.
- [ ] Exit codes are nonzero on any failure and zero only on a terminal success state.
- [ ] `--help` documents each command in one sentence.
- [ ] Tests invoke commands through Typer's test runner with fakes and no network.

## Out of scope

- Any new pipeline behavior. The CLI only calls functions that already exist.

## Constraints

- Typer is the approved dependency.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 17 — Phase 1 — OWNER: Add the five-case smoke evaluation gate to CI

## Goal

Sam adds a fast, network-free five-case evaluation that scores shortlist quality and blocks CI when the frozen expected behavior regresses.

## Acceptance criteria

- [ ] Five committed cases exercise at least a confirmed rare object, a confirmed common transient, an unlabeled candidate, a bogus/drop case, and a ranking-boundary case.
- [ ] Cases use the cached response transport with recorded prompt hash, model id, creation date, and expected typed output.
- [ ] Scorers compute precision at 25 as confirmed shortlisted objects divided by shortlist size and recall as shortlisted confirmed objects divided by that night's confirmed candidate universe.
- [ ] Precision and recall reports include the point estimate, 95% Wilson lower and upper bounds, and actual `n`.
- [ ] Unmatched candidates remain unlabeled and are not counted as negatives.
- [ ] The gate has an explicit frozen threshold or expected-output contract and exits nonzero when a controlled regression is introduced.
- [ ] The gate reports actual numerator, denominator, and case count rather than implying a larger sample.
- [ ] `.github/workflows/ci.yml` runs the smoke evaluation without secrets, network calls, or a live model and preserves the existing lint, test, and Docker build checks.
- [ ] A local documented command reproduces the same pass/fail result as CI.

## Out of scope

- The full 300-positive evaluation, bootstrap intervals, Wilson gates on main, LLM judges, and live API comparisons.

## Constraints

- OWNER: Sam edits `.github/workflows/ci.yml` and writes the Wilson function by hand.
- No model call or network access is permitted in tests.
- Do not present five-case metrics as production evidence.

## Do not edit

- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`

# Task 18 — Phase 1 — Prove one-night replay end to end

## Goal

Run one selected archive night through publication, durable consumption, candidate construction, triage, and deterministic ranking, then preserve evidence that Phase 1's exit condition is met.

## Acceptance criteria

- [ ] `vetter run --night` starts from a verified night and produces a persisted shortlist for the same night.
- [ ] The run publishes original Avro payloads to `ztf.alerts`, consumes them through a group, and reaches a terminal success state in Postgres.
- [ ] Published, consumed, persisted, triaged, dropped, surviving, and ranked counts reconcile, with any invalid input accounted for explicitly.
- [ ] Re-running the same night and release does not duplicate durable alerts, candidates, triage results, or shortlist rows.
- [ ] The shortlist contains 25 unique ranked candidates when at least 25 survive, otherwise all survivors.
- [ ] A trace links the replay, consume, triage, and rank stages by run id and records the configured model identity.
- [ ] The five-case evaluation gate and the existing lint, format, unit-test, and Docker checks all pass at the final revision.
- [ ] `docs/RESULTS.md` records the end-to-end command, date, code revision, release identity, night, counts, runtime, and smoke-gate result.

## Out of scope

- Live broker traffic, the five-tool vetter, final model selection, full statistical evaluation, UI, Kubernetes, and production deployment.

## Constraints

- The replayed model input must obey the freeze rule.
- Use cached model output for automated verification; any manual local-model run must be separately identified and reproducible.
- Any number in `docs/RESULTS.md` must include its producing command and date.

## Do not edit

- `.github/`
- `Dockerfile`
- `docker-compose.yml`
- `release.yaml`
- `src/vetter/llm/client.py`
- `src/vetter/ingest/consumer.py`
