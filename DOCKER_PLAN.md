# Docker Plan — Ubuntu Mapping for Codex

> **Goal:** Mirror `README.md`'s native Ubuntu flow (`install_codex.sh` → `start_codex.sh`) entirely inside Docker. One command brings up Postgres+pgvector, Neo4j, FastAPI, Streamlit UI, and the ingestion worker.

## README → Docker Mapping

| README Step (native) | Docker Equivalent |
|---|---|
| **1. Install System Requirements** — Python 3.10+, Postgres 16+pgvector, Neo4j 5.26+, curl, git | Install **Docker Engine + Compose plugin** only — all deps run in containers |
| **2. Clone & Setup** — `git clone` → `bash infra/install_codex.sh` → `cp .env.example .env` | `git clone` → `cp .env.example .env` → edit passwords/keys → `docker compose up -d --build` (replaces both `install_codex.sh` and `bootstrap_local.sh`) |
| **3. Launch** — `bash infra/start_codex.sh` (starts Postgres in `.data/postgres`, Neo4j, FastAPI :8000, Streamlit :8501, worker) | `docker compose up -d` does the same via 5 services with healthchecks + `depends_on` |
| **4. Access & Login** — `http://127.0.0.1:8501` / `:8000` with `admin/password123` etc. | Identical — ports `8000`/`8501`/`5432`/`7474`/`7687` published to host |

## Architecture (docker-compose.yml)

```
postgres (custom build) — postgres:16 + postgresql-16-pgvector — :5432 — healthcheck pg_isready
neo4j   — neo4j:5.26 — :7474/:7687 — healthcheck wget --spider :7474
api     — build . (python:3.10-slim) → uvicorn services.api.main — :8000 — depends_on postgres+neo4j healthy
ui      — build . → streamlit run services/chat/ui.py — :8501 — depends_on api
worker  — build . → python -m services.ingestion.ingestion_agent — depends_on api
volumes: postgres_data, neo4j_data, neo4j_logs | binds: ./archive:/app/archive:ro, ./logs:/app/logs
```

**Postgres pgvector:** Not via volume init script — via custom build `infra/docker/postgres/Dockerfile`:

```dockerfile
FROM postgres:16
RUN apt-get update && apt-get install -y postgresql-16-pgvector && rm -rf /var/lib/apt/lists/*
COPY init-pgvector.sh /docker-entrypoint-initdb.d/init-pgvector.sh
RUN chmod +x /docker-entrypoint-initdb.d/init-pgvector.sh
```
```bash
# infra/docker/postgres/init-pgvector.sh
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
```
> Do **not** create `infra/docker/init-pgvector.sh` — stale file, removed. Only `infra/docker/postgres/*` is used.

**App image:** `Dockerfile` (root) — `python:3.10-slim` + `build-essential gcc libpq-dev` + `pip install -r requirements.txt`, non-root `appuser` (UID 10001). `COPY . .` respects `.dockerignore`.

## Prerequisites (fresh Ubuntu/Debian)

```bash
# Docker Engine + Compose
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker  # re-login if needed
docker compose version  # verify v2+

# Project deps for compose file only
sudo apt-get install -y git curl
git clone <repository-url> codex && cd codex
```

## Quick Start (mirrors README §2-3)

```bash
cp .env.example .env
# Edit .env — MUST change before first up:
# POSTGRES_PASSWORD, NEO4J_PASS, NEO4J_AUTH=neo4j/<pass>, SECRET_KEY (openssl rand -hex 32)
# LLM_PROVIDER=api, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL / LLM_INGESTION_MODEL
# CORS_ORIGINS='["http://localhost:8501","http://127.0.0.1:8501"]' — must stay valid JSON
nano .env

# Build + start (replaces infra/install_codex.sh + infra/start_codex.sh)
docker compose up -d --build

# Wait for healthchecks (~15s) then verify
docker compose ps                    # all 5 should be healthy/running
docker compose logs -f               # watch api/worker if needed
curl http://127.0.0.1:8000/health    # {"status":"ok"} when ready — same as README verify step
```

Access (same as README §4):
- **UI:** http://127.0.0.1:8501
- **API:** http://127.0.0.1:8000
- **Neo4j Browser:** http://127.0.0.1:7474

Default mock users (when `SEED_DEFAULT_USERS=true`, api runs `infra/seed_users.py` on boot):

| user | password | Level | Dept |
|------|----------|-------|------|
| admin | password123 | 3 | IT |
| manager | password123 | 2 | Finance |
| employee | password123 | 1 | HR |

> `api` entrypoint: `python infra/check_dependencies.py && python infra/seed_users.py && uvicorn ...` — no manual seeding needed.

## Configuration

All config via `.env` (mounted as compose environment). Key Docker-relevant vars:

| Var | Compose Default | Notes |
|-----|-----------------|-------|
| `POSTGRES_USER/PASSWORD/DB` | codex_admin / change-this-password / codex_db | Used by postgres + api/worker DATABASE_URL |
| `NEO4J_USER/NEO4J_PASS` | neo4j / change-this-password | Forms `NEO4J_AUTH` |
| `DATABASE_URL` (api/worker) | `postgresql://...@postgres:5432/...` | Note host is `postgres` (service name), not `127.0.0.1` |
| `NEO4J_URI` (api/worker) | `bolt://neo4j:7687` | Service name, not localhost |
| `LLM_PROVIDER` | `api` | Docker path is API-only; `local` requires extra services not in compose |
| `SECRET_KEY` | *(empty)* | Generate: `openssl rand -hex 32` |
| `CORS_ORIGINS` | `'["http://localhost:8501","http://127.0.0.1:8501"]'` | Must remain JSON array in shell quotes |

Compose interpolates `${VAR:-default}` — `.env` overrides defaults.

## Ingestion & Querying (mirrors README)

Worker **does not** watch `archive/` — only processes explicit uploads via UI/API (same as native). `archive` is mounted read-only for reference.

**Via UI (recommended):** Open `:8501` → login as `admin` → upload DOCX/PDF → wait → check status.

**Via API:**
```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/login \
  -d "username=admin&password=password123" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST http://127.0.0.1:8000/v1/ingest -H "Authorization: Bearer $TOKEN" -F "file=@/path/to/policy.docx"
curl http://127.0.0.1:8000/v1/ingestion/status -H "Authorization: Bearer $TOKEN"
curl -X POST http://127.0.0.1:8000/admin/refresh-index -H "Authorization: Bearer $TOKEN"  # required after ingestion
```

**Query:**
```bash
curl -X POST http://127.0.0.1:8000/query -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"Who can approve a purchase over $10,000?","search_mode":"hybrid"}'
```
Search modes: `hybrid` (RRF+rerank, default), `vector`, `bm25`.

**Incremental batch (alternative, inside container):**
```bash
docker compose exec api python services/ingestion/ingest.py
docker compose exec api python services/ingestion/migrate_to_neo4j.py --incremental
curl -X POST http://127.0.0.1:8000/admin/refresh-index -H "Authorization: Bearer $TOKEN"
```

## Verification

```bash
docker compose ps                                    # health checks pass
docker compose exec postgres psql -U codex_admin -d codex_db -c "SELECT * FROM pg_extension WHERE extname='vector';"
docker compose logs postgres | grep -i vector
docker compose logs neo4j | grep -i "Started"
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health -v  # check DB/Neo4j connectivity
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `CORS_ORIGINS` 500 on upload | Must be valid JSON with shell quotes: `'["http://127.0.0.1:8501"]'` — restart `docker compose restart api` after `.env` change |
| Login 500 Argon2 | `docker compose logs api` → `argon2-cffi` missing → `docker compose build --no-cache api && docker compose up -d` |
| Postgres not healthy | `docker compose logs postgres` ; `docker compose exec postgres pg_isready -U codex_admin` ; check `POSTGRES_*` in `.env` |
| Neo4j not healthy | `docker compose logs neo4j` ; `wget --spider http://localhost:7474/` inside container |
| Worker not processing | Only UI/API uploads queued in DB are processed — dropping files in `archive/` does nothing (same as native) |
| Stale search | Run `POST /admin/refresh-index` after every ingestion |
| No OCR | Scanned PDFs produce no text — use searchable PDFs |

### Troubleshooting deltas — clean-slate validation run (2026-08-22, task T3)

Live-evidence findings from the first full `down -v && build --no-cache && up -d` cycle (all 5 services healthy at t=46s):

| Symptom | Root cause | Fix (landed) |
|---------|-----------|--------------|
| Cold build fails: `No matching distribution found for torch==2.5.1+cpu` | `+cpu` wheels are never on PyPI, and the PyTorch CPU index pruned ≤2.5.x (its `+cpu` builds now start at 2.6.0). Cached layers had been masking this | Dockerfile pip uses `--extra-index-url https://download.pytorch.org/whl/cpu`; requirements pin repaired to plain `torch==2.5.1`. If you re-pin a `+cpu` build, minimum is `2.6.0+cpu` |
| neo4j crash-loop: `Failed to read config: Unrecognized setting ... PASS` | The neo4j entrypoint maps every `NEO4J_*` env var to a config setting; `env_file: .env` leaks `NEO4J_PASS` into the container → bogus setting `pass` → strict-validation abort | `env_file` removed from the neo4j service only; auth is injected via compose-level `${NEO4J_USER}/${NEO4J_PASS}` interpolation into `NEO4J_AUTH`. Never re-add `env_file` to neo4j |
| api healthcheck perpetually failing while `/health` returns 200 | Old check grepped for `ok`, but the payload is `{"status":"healthy",...}` — no "ok" substring anywhere | Healthcheck greps `'\"status\":\"healthy\"'` (exact field match; bare `grep healthy` would false-positive on `"unhealthy"`) |
| Fresh volume ⇒ api exits before uvicorn (`relation "users" does not exist`) | Nothing created schema before seeding on a clean slate — only ingestion paths call `init_db()` | api command chain runs `python -c 'from packages.shared.db import init_db; init_db()'` before `seed_users.py` |
| ui/worker show `running` but never `healthy` | They had no healthcheck at all | ui probes Streamlit's `/_stcore/health`; worker liveness checks its singleton pidfile (`$STATE_DIR/ingestion_worker.pid`) via `os.kill(pid, 0)` |
| `/health` said nothing about Neo4j | Endpoint predates the compose topology | `/health` now includes a bounded `neo4j` connectivity probe (`verify_connectivity()`), so db+neo4j status is observable in one call |

Postgres invariant (ISC-8): the container env carries `POSTGRES_DB=codex_db`, the healthcheck resolves to `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` (probes THAT database), and a clean-slate boot produces zero `FATAL:` lines in postgres logs — if you ever see a `FATAL: database "…" does not exist` loop at ~10s cadence, it means stale named volumes baked with old values: run `docker compose down -v` and bring up again (dev data disposable).

Logs: `docker compose logs [postgres|neo4j|api|ui|worker]` — api/worker also write to `./logs/` bind.

## Maintenance

```bash
docker compose logs -f [service]          # tail
docker compose restart [service]
docker compose ps
docker compose down                       # stop, keep volumes
docker compose down -v                    # STOP + DELETE postgres_data/neo4j_data — destructive reset (mirrors --reset flows)
docker compose up -d --build              # rebuild after Dockerfile/requirements change
```

For full corpus reset (destructive, mirrors README `reset_and_reingest.sh --reset` + `run_graph_migration.sh --reset`):
```bash
docker compose down -v
docker compose up -d --build
# then re-upload via UI/API or run ingest inside api container
```

## Notes

- `api`/`ui`/`worker` share identical env — `worker` and `ui` depend on `api` (not directly on DB), ensuring DB migrations/seeding complete first.
- Data persistence via named volumes `postgres_data`, `neo4j_data`, `neo4j_logs` — survives `docker compose down` unless `-v` used.
- For production: set real `SECRET_KEY`, restrict `CORS_ORIGINS`, tune Postgres `shared_buffers`/`max_connections` and Neo4j `pagecache`, add backups for volumes.
- Ignore `__pycache__/`, `logs/`, `.env`, `*.pyc` — in `.gitignore`/`.dockerignore`.
