# vetter: project context for Claude Code

Drop this file at `_docs/context.md`. It is the seed for `_docs/plan.md`, which gets written through the scope interview described at the bottom. Read this before doing anything in the repo.

## What vetter is

An agent that vets astronomical transient alerts from ZTF, decides which candidates are real, classifies them, and produces a ranked shortlist of k candidates per night for spectroscopic follow up. Ground truth arrives later as TNS spectroscopic classifications, so every decision is verifiable after the fact. That property drives the eval design and makes RL possible.

Public repo. The purpose is a production grade agent platform with measured numbers for every claim: CI with statistical eval gates, OpenTelemetry tracing, drift monitoring, canary deploys with rollback, a sandbox with a threat model, and a GRPO trained triage policy.

## Constraints

- Compute: Apple M3 Max, 128 GB. No owned GPU. Rented GPU only if free (Kaggle) or trivially cheap.
- Budget: near zero. API spend capped at roughly $20 to $30 total. Everything else runs locally.
- Owner is a data scientist and AI lead, not a career SWE. The goal is to learn the deployment stack while keeping velocity: plain code, one idea per module, tweakable constants at the top of files, no clever abstractions. Explain non obvious choices in a comment or the PR description so the owner can follow them.

## Architecture decisions made so far

**Data.** ZTF public alert archive, nightly Avro tarballs, 10 nights from 2023 so TNS truth exists. Replay archived nights through a local Redpanda topic. Live broker consumption is a week 5 addition, not week 1.

**Ground truth.** TNS public API, matched by position within 2 arcsec, frozen at discovery date so the agent never sees the future. Taxonomy: SN Ia, SN II, SN Ibc, SLSN, TDE, AGN, CV, variable star, asteroid, bogus. "Rare" is a flag over TDE and SLSN.

**Public baseline.** Verify AstroAlertBench exists and is usable. If not, the baseline is the broker classifier probabilities that ship inside the alert (ALeRCE stamp and light curve classifiers).

**Agent shape.** Three roles. Triage: one cheap call per candidate, keep or drop plus coarse class. Vetter: tool using agent on triage survivors. Ranker: deterministic, orders by rarity times confidence into a shortlist of k. Only the vetter is an agent in the trajectory sense. Triage stays a single call so it can be RL trained later.

**Vetter tools.** `crossmatch(ra, dec)` over SIMBAD, NED, Gaia via astroquery. `fit_lightcurve(candidate_id)` in the sandbox. `inspect_stamps(candidate_id)` with a local vision model. `search_literature(query)` over ADS. `run_python(code)` in the sandbox. Five tools, each with a JSON schema, each with a span.

**Stack.** Python 3.12, uv, Pydantic, FastAPI worker, Postgres, Redpanda, OpenTelemetry to self hosted Langfuse, GitHub Actions, Docker with multi arch builds, kind locally. Models: local Qwen via mlx-lm as an OpenAI compatible endpoint for triage and vetter; Claude Haiku via the API for one frozen eval pass as the model under test, cached by prompt hash. Frontier model labeling (judge calibration, second rater labels, red team payloads) runs through Claude Code or the Codex CLI on a subscription against files in the repo, outputs frozen as fixtures with the prompt, model id, and date recorded. Sandbox: Docker with no network and resource caps from week 3, gVisor in a Lima VM in week 7. RL: MLX for SFT, RFT, GRPO locally; Kaggle GPU with TRL for one reproducible run.

**Names.** Repo, package, and CLI are all `vetter`. Not `marshal` (stdlib collision).

## Rules for agents working in this repo

1. No model call in tests. Tests use cached fixtures in `tests/conftest.py`.
2. Every model call goes through one client module with an OpenTelemetry span.
3. No number goes in `docs/RESULTS.md` without the command that produced it and the date. If it is not in RESULTS.md it does not go on the resume.
4. Infrastructure files are edited only with explicit approval from the owner: `.github/`, `Dockerfile`, `docker-compose.yml`, `infra/`, `helm/`, `terraform/`. The owner writes the first version of each by hand. Agents may propose diffs; they do not apply them.
5. Dependencies go in `pyproject.toml` via `uv add`. Never `pip install`. Do not add a dependency without asking.
6. Tests never hit the network. Parsing lives in pure functions tested with inline fixtures.
7. Commit after every meaningful decision.

## Commands

```
uv sync                       install
uv run pytest -q              whole suite
uv run pytest -k tns          tests matching a name
uv run ruff check .           lint
uv run ruff format .          format
```

## Repo layout (target)

```
vetter/
  src/vetter/
    agent/        triage.py, vetter.py, ranker.py, tools/
    ingest/       replay.py, broker.py, tns.py
    eval/         cases/, scorers.py, judge.py, gate.py, bootstrap.py
    rl/           env.py, train_grpo.py, train_rft.py, reward.py
    llm/          client.py (the only place a model is called)
  infra/          Dockerfile, compose, helm/, terraform/, k8s/
  sandbox/        gvisor/, threat_model.md, redteam/
  ui/             review app, mcp_server.py
  tests/
  docs/           RESULTS.md, one page per week
  _docs/          plan.md, process.md, task-template.md, team/
  release.yaml    model id, prompt hash, tool schema hash
```

## Build outline

Phases, not weeks. Each phase is done when its exit condition is met; move faster where you can.

| Phase | Focus | Done when |
|---|---|---|
| 0 | Decisions, 10 ZTF nights downloaded, TNS match script, baseline verified | 200 matched candidates in Postgres, baseline number written down |
| 1 | Day 1 skeleton (CI, 5 case smoke eval, OTel spans, release.yaml, Dockerfile). Replay simulator. Triage call. Ranker. | One night replays end to end, CI green with eval gate |
| 2 | Eval suite to 500 cases. Deterministic scorers. LLM judge with kappa against 50 human labels. Cached responses. Cost and latency per span. | Kappa exists, CI under 10 min, cost per candidate on a dashboard |
| 3 | Wilson lower bound gate. Paired bootstrap clustered by night. Vetter agent with five tools. Docker sandbox. Trajectory evals. Baseline comparison. | Gate has blocked a real regression. Vetter beats triage alone. |
| 4 | Tool spans, replay from trace, p50 and p95 per step, Slack alerting, audit log, online review queue feeding offline cases | Any run replayable. Alert has fired once. |
| 5 | Two services on kind. Helm. HPA. Sharded eval Job. Live broker consumption. PDB, probes, graceful shutdown. | Kill a pod mid night, nothing lost or duplicated |
| 6 | Terraform for the kind stack. Canary at 10 percent with rollback. Nightly drift CronJob. Load test. Rubin scale cost estimate. | Rollback has triggered once on a bad prompt |
| 7 | gVisor runtime in Lima. Egress allow list. Threat model. Red team with poisoned literature and catalog responses. | X of Y payloads blocked, before and after |
| 8 | Prompt caching, model routing (bandit), catalog cache, latency budget per step | p95 cut 2x, quality inside the bootstrap interval |
| 9 | RL: verifiers env, SFT then RFT then GRPO on triage, reward hack found and fixed | Trained policy beats prompted policy on held out nights |
| 10 | Review UI, MCP server, README as case study | A stranger can clone, run one night, see the dashboard |

## Today (phase 0, day 1)

1. Skeleton: `uv init --package`, pytest, ruff, smoke test green, first commit, public repo. Done by hand.
2. CI: `.github/workflows/ci.yml` written by hand from the GitHub Actions docs. Trigger on push and PR, checkout, uv install, sync, ruff, pytest. Watch it go green, push a failing test, watch it go red, revert. Branch protection on main.
3. Course files: `AGENTS.md`, `CLAUDE.md` with `@AGENTS.md`, `_docs/process.md`, `_docs/task-template.md`, `_docs/team/{pm,software-engineer,qa-engineer}.md`.
4. Scope interview: interview the owner one question at a time on the gaps in this document. Output `_docs/plan.md`. Gaps to probe: k for the shortlist, exact candidate count, which 10 nights, replay format, local model choice, Redpanda versus a Postgres queue for week 1.
5. Backlog: decompose `plan.md` into `_docs/tasks.md`, weeks 0 and 1 only, 12 to 15 session sized issues in the task template format. First issue is already done (skeleton). Create GitHub issues with `gh`.
6. One loop cycle on the ZTF download issue: PM grooms, engineer implements, QA checks. Owner reads the diff before closing.

Dockerfile is tomorrow, by hand.

## Vetter design principles

These govern how vetter makes decisions. The engineer checks new code against them.

1. Cascade, do not escalate by default. Cheap triage on every alert, the tool using vetter only on survivors, a deterministic ranker on top. Cost scales with survivors, not alerts. Each step uses the cheapest model that holds quality inside the bootstrap interval measured on the eval set.

2. Verifiable reward beats self assessment. TNS tells us months later whether a decision was right. Score against that. An LLM judge lives in the eval suite to grade rationale quality offline; it is never in the hot path and never the reason a candidate is promoted.

3. Escalate on measured uncertainty, not on vibes. When triage confidence falls inside a band set from eval data, the candidate goes to the vetter. When the vetter's tools disagree, sample again (self consistency) or route to a stronger model. When the ranker's top k is close, a human decides. The thresholds are numbers in `release.yaml`, not prose.

4. Deterministic where possible. If it can be a function, it is a function. The ranker, the TNS matcher, the light curve features, and every scorer are code with unit tests. Models are used where the input is unstructured and the output is a judgment.

5. Tools return typed results, not prose. Every tool has a JSON schema. A hallucinated host galaxy fails validation; a hallucinated paragraph does not. The vetter cites tool results by id in its rationale so a reviewer can check the claim.

6. Tool output is untrusted input. Catalog responses, paper abstracts, and broker fields are data an attacker can shape. They are never interpreted as instructions. This is the threat model in one line.

7. A human approves the expensive action. The shortlist is a recommendation. A person approves before a telescope moves, and the override rate is logged and reported. If the override rate is near zero, the human is rubber stamping and the label is the model's.

8. Cache by content hash. Model responses, catalog queries, and eval fixtures are keyed on a hash of their inputs. A run that changes nothing costs nothing.

9. Every number has a command. Precision at k, kappa, p95, cost per candidate: each is recorded in `docs/RESULTS.md` with the command and date that produced it. A number without a command does not exist.


