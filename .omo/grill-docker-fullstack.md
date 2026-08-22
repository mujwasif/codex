# Grill: Codex — build, deploy, run, and test entirely inside Docker

> Session: 2026-08-22 · Workflow: ISA/Grill · Max questions: 12
> Note: harness restricts planner writes to `.omo/*.md`, so this checkpoint lives here instead of `~/.claude/LIFEOS/MEMORY/WORK/{slug}/grill.md`; content feeds the Scaffold handoff verbatim.

## Shape & Key Decisions
- Category: infra/tooling work on existing codex-main app (FastAPI + Streamlit + Postgres/pgvector + Neo4j + ingestion worker).
- Grounding found (codebase): root `Dockerfile` (python:3.10-slim, non-root appuser UID 10001, deps-layer cache, `/codex` symlink shim), `docker-compose.yml` (5 services: postgres custom build w/ pgvector init script, neo4j:5.26, api, ui, worker; healthchecks + `depends_on` conditions), `infra/docker/postgres/Dockerfile` + init-pgvector.sh, `.dockerignore`, `DOCKER_PLAN.md` (full runbook mirroring README native Ubuntu flow).
- Gap identified: NO test stage in Docker. All suites in `tests/` are documented native-only (`source .venv && python tests/test_*.py`). PROGRESS.md marks CI/CD ❌ NOT DONE.
- Compose assumes `LLM_PROVIDER=api` (hosted API); llama.cpp model servers deliberately NOT containerized per DOCKER_PLAN.md.

## Q&A Log
### Q1: Given build/deploy/run already exist on paper, what is the actual goal of "all inside Docker"?
**Recommended:** Validate the existing compose stack end-to-end, then add an in-Docker test stage
**Answer:** ✅ Validate + add tests (accepted recommendation)

## Shape updates after Q1
- Scope settled: (a) validate existing compose stack end-to-end on this machine, fixing whatever breaks; (b) add an in-Docker test stage running existing suites against/inside the compose network.
- Explicitly NOT chosen: rewrite-from-scratch; CI pipeline (may become Open Flag / Remaining Work).

### Q2: Where must this stack ultimately run; does plain `docker compose up -d` count as deploy?
**Recommended:** Compose = deploy (no registry/K8s/proxy/TLS this round)
**Answer:** ✅ Compose = deploy (accepted recommendation)

## Shape updates after Q2
- Deployment model settled: `docker compose up -d --build` is THE deploy story, identical on Mac dev box and Linux server. Registry push, K8s, reverse proxy, TLS all out of scope for this ISA (candidates for Remaining Work).
- Codebase facts gathered for Q3: tests are stdlib `unittest` style (7+ files use unittest/mock; no pytest, no fastapi TestClient); requirements.txt pins torch 2.5.1+cpu + sentence-transformers (heavy ~2GB image; embeddings/rerank in-container CPU), LLM is hosted-API only.

### Q3: What is the minimum bar where "tested inside Docker" is true?
**Recommended:** unittest suites in a `test` compose profile + black-box e2e smoke of the live stack
**Answer:** ✅ Suites + e2e smoke (accepted recommendation)

## Pre-mortem → draft ISCs (Step 6)
Failure modes imagined post-ship, converted to binary criteria:
- ISC-1: On a clean Linux host with only Docker+Compose+git, a fresh clone reaches `docker compose ps` all-healthy after `cp .env.example .env` + one `up -d --build` (kills "works only on my Mac").
- ISC-2: `docker compose build` succeeds from cold cache with zero unpinned bases/packages (kills dependency-drift rot).
- ISC-3: `docker compose --profile test run --rm tests` runs EVERY suite in `tests/`, exits nonzero on any error/skip-by-crash, and propagates the exit code (kills silent false-greens).
- ISC-4: DB-dependent suites (retrieval_quality, history) execute against compose Postgres/Neo4j in an isolated test database, and two consecutive runs produce identical outcomes (kills pollution/flakiness).
- ISC-5: Smoke proves the guardrail chain: health(deep) → login → ingest sample DOCX → status ready → query → assert ≥1 citation matching retrieved chunks OR verdict=abstained; any 5xx or unmatched citation FAILS (kills theater-passing smokes).
- ISC-6: Smoke degrades honestly without LLM key: absent/unreachable LLM ⇒ abstained-path assertions still validate fail-closed behavior and are reported as DEGRADED-PASS, never silent green (kills environmental reds/green-lies).

## Adopted defaults (veto at approval)
- Runner stays stdlib `unittest discover`; pytest NOT added (no new deps in runtime image; test service may pip-install pytest ad hoc only if needed).
- Test service reuses the app image (same build context); no second Dockerfile unless validation proves otherwise.
- Smoke lives at `infra/smoke.sh` (curl/python hybrid) + documented in README §Testing and DOCKER_PLAN.md.
- Sample fixture doc for smoke: smallest DOCX in `archive/` copied to a fixtures path, NOT ingested into the dev volume (uses isolated DB or cleanup step).

## Open Flags
- [ ] CI pipeline explicitly declined this round — Remaining Work candidate
- [ ] Slack webhook untestable in-Docker without public tunnel — out of scope note
- [ ] Whether hosted LLM_API_KEY exists for smoke's happy path — needs user at execution time; ISC-6 covers absence
- [ ] Registry/K8s/proxy/TLS — out of scope, future ISA

### Q4 (unsolicited evidence at approval gate): user pasted live crash from `docker compose up`
**Evidence:** api container dies at uvicorn import: `services/api/main.py:12 → from codex.services.ingestion.bm25_index import BM25Index → ModuleNotFoundError: No module named 'codex'`. Seeding step ran first ("SEED_DEFAULT_USERS is not true"), so this is THIS repo's compose command chain.
**Diagnosis fork:** (a) stale image built before the `/codex` symlink + `PYTHONPATH=/app:/` shims landed in Dockerfile, or (b) shim present but insufficient. Grep sizing: **48 `codex.*` imports across 12 files** (api routers, ingestion, main.py).
**Decision:** durable fix = normalize all 48 to repo-relative imports (`services.*`, `packages.*`) and DELETE the symlink/PYTHONPATH-`/` hack — kills the whole class (clone-name-independent, native flow unaffected; tests already import relatively). Immediate unblock during execution = `docker compose build --no-cache api`.
**Answer treated as:** approve-with-adjustment; scaffold proceeds with amended F1.

### Q5 (second live-evidence paste, "solve these too"): worker crash + postgres FATAL loop
**Evidence A:** `worker-1 … services/ingestion/ingestion_agent.py:29 → from codex.packages.shared.db import … → ModuleNotFoundError: No module named 'codex'` — confirms the import defect spans BOTH app processes; ISC-2 normalization scope (12 files incl. ingestion_agent.py) already covers it.
**Evidence B:** `postgres-1 FATAL: database "codex_admin" does not exist` repeating at exactly 10s intervals (= compose healthcheck `interval: 10s`) for 4+ minutes. A client is requesting dbname=`codex_admin` (libpq fallback when `-d` value is EMPTY ⇒ falls back to username). Prime suspect: postgres healthcheck `pg_isready -U $$POSTGRES_DB…` expanding `$POSTGRES_DB` empty inside the container, and/or named volume `postgres_data` initialized before `.env` was finalized (first-boot values are baked forever).
**Decisions:** (1) Validation bring-up MUST be clean-slate: `docker compose down -v` first — dev volumes hold nothing precious. (2) New ISC-8 pins the invariant: correct db exists, healthcheck probes IT, zero repeated FATALs in a 60s window. (3) Worker diagnosis commands recorded in plan (printenv, docker compose config) instead of guessing.

## Open Flags
- [ ] Has `docker compose up -d --build` ever succeeded on this Mac? — needs user
- [ ] Does "test" mean unit/integration suites in a container, black-box smoke against the live compose network, or CI gate? — needs user
- [ ] Target host: local dev only, or deployable artifact for a server? — needs user
