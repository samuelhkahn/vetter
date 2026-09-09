# Vetter plan

## Scope

Build a local-first ZTF alert vetting platform that replays archived alerts, triages candidates, vets survivors with tools, and ranks 25 nightly recommendations. Phase 0 establishes the data and truth set; phase 1 delivers one-night replay end to end with CI, tracing, triage, ranker, and an eval gate.

## Decisions

- **Shortlist:** `k = 25`; this fixes the operating point for ranking and the cascade rule.
- **Primary eval:** the candidate universe is every unique object with a detection on the night after the standard real/bogus cut; labels are TNS spectroscopic matches within 2 arcsec with their class, everything else is unlabeled, not negative. Precision at k is the fraction of the shortlist later confirmed by TNS; recall is the fraction of that night's TNS confirmed objects in the shortlist. The 300 target applies to labeled positives across the ten nights. Target at least 300; report the actual count and Wilson interval, never pad or resample, and add nights if needed.
- **AstroAlertBench eval:** use the confirmed 1,500-alert, five-class benchmark for model comparison and adopt its confidence calibration protocol; classifier labels are not ground truth.
- **Broker probabilities:** expose them to triage, but mask them during RL training so the policy cannot copy them.
- **Nights:** choose ten 2023 archive nights closest to six-week increments; add randomly selected archive nights if the TNS set is below 300.
- **Replay:** publish original Avro payloads with replay metadata to `ztf.alerts`; a worker consumes via a group, matching the phase 5 live-broker path.
- **Messaging:** run one Redpanda container in Docker Compose; Postgres is the durable store, not the queue.
- **Models:** `release.yaml` names separate provider:model values for triage and vetter; `llm/client.py` is the only call path and records model identity per eval.
- **Phase 1 defaults:** small current Qwen instruct for triage and a 30B-class Qwen MoE at 4-bit for vetter, both via `mlx-lm` OpenAI-compatible endpoints.
- **Model selection:** phase 2 chooses the cheapest model whose precision at 25 remains inside the best-tried model’s bootstrap interval; vLLM is a config-only serving swap on larger hardware.
- **Freeze rule** the agent sees only alert history with observation dates up to and including the replayed night, and no catalog or literature entry dated after it where a date is available.
- **seed** seed is one integer in release.yaml and the selected night list is commited as a file
- **wilson interval confidence** 95 percent, reported as point estimate, lower, upper, and n.
- **AstroAlertBench Leaderboard version** Hugging Face revision and GitHub commit recorded in release.yaml on first download.
- **Add to nights** skip a night with fewer than N alerts and take the next; N is set when the first tarball is inspected. Extra nights are selected with the seed below and the final night list is committed as data/nights.txt.
- **Real/bogus cut:** `drb >= 0.5`, `isdiffpos` true, `nbad == 0`. Adjust after inspecting the first night; record the final cut in `release.yaml`.
- **TNS source:** the TNS daily public objects CSV, not the API. One download, one checksum, no credentials.
- **Migrations:** plain numbered SQL files under `src/vetter/store/migrations/`, applied by a small function that records applied versions. No Alembic.
- **Archive storage:** tarballs are not kept. Each night is extracted to one Parquet file of the fields vetter uses plus the three cutouts, then the tarball is deleted.

## Out of scope

- Live broker consumption before phase 5, synthetic or padded eval cases, and telescope-action automation.
- Final model choice, RL training, full five-tool vetter, sandbox hardening, UI, Kubernetes, Terraform, canaries, and production deployment.


