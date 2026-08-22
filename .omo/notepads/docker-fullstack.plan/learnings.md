# Learnings — docker-fullstack.plan

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## T2-normalize-imports (2026-08-22)

### Final import inventory — 51 occurrences across 13 files

| File | Count | Notes |
|---|---|---|
| services/api/dependencies.py | 3 | top-level |
| services/api/main.py | 2 | top-level |
| services/api/routers/admin.py | 6 | 5 top-level + 1 function-local (`_delete_document_from_neo4j`, :313) |
| services/api/routers/auth.py | 5 | top-level |
| services/api/routers/conflicts.py | 4 | top-level |
| services/api/routers/documents.py | 4 | top-level |
| services/api/routers/health.py | 4 | top-level |
| services/api/routers/query.py | 5 | top-level |
| services/api/routers/users.py | 5 | top-level |
| services/api/search.py | 1 | was mixed-style; other 3 imports already canonical `packages.shared.*` |
| services/ingestion/ingest.py | 5 | top-level |
| services/ingestion/ingestion_agent.py | 5 | top-level |
| integrations/slack.py | 2 | function-local inside `_get_user()` try-block (:177-178) |

All were `from codex.` form — zero bare `import codex.` despite task example mentioning it. Pure prefix swap (`from codex.X` → `from X`), submodule paths preserved exactly.

### Edge cases found
- **Function-local imports invisible to ^-anchored grep**: slack.py:177,178 + admin.py:313 only surfaced via `\b(from|import) codex[.\s]` — reconciling both greps was mandatory (48 vs 51).
- **AGENTS.md testability invariant untouched**: slack.py's local imports are `packages.shared.db/models`, NOT `services.api.dependencies` — path spelling normalized, structure unchanged.
- **Dynamic imports clean**: `infra/check_dependencies.py` uses `importlib.import_module()` but only on third-party pip names; zero `"codex."` string literals repo-wide.
- **tests/ clean**: no codex.* imports anywhere; their `sys.path.insert` lines add repo root only (compatible with canonical layout).
- **Non-.py sweep**: AGENTS.md prose dual-module mention left alone per plan; stale `/home/mujtaba/...` PYTHONPATH values in AGENTS.md commands are host filesystem paths, not `codex.` module paths — left per "prose/history alone".
- **Canonical-layout confirmation**: main.py already had `from services.api import state` + `from packages.shared.config import CORS_ORIGINS`; conflicts.py already imported `from services.agents.conflict_agent` — services.* is the native layout, codex.* was the drift.

### Dockerfile changes
- `PYTHONPATH=/app:/ \` → `PYTHONPATH=/app \` (no filesystem-root on sys.path)
- Removed `RUN ln -sfn /app /codex` + its comment (no /codex alias); WORKDIR confirmed `/app`

### Gate commands that passed
```
grep -rnE '^(from|import) codex[.\s]' --include='*.py' .        # zero matches (exit 1)
grep -rnE '\b(from|import) codex[.\s]' --include='*.py' .       # zero matches (exit 1)
python3 -c "import services.api.main"                           # ModuleNotFoundError: No module named 'fastapi' (third-party only; .venv absent on host)
python3 -c "import services.ingestion.ingestion_agent"          # ModuleNotFoundError: No module named 'sentence_transformers' (third-party only)
# supplementary zero-dep proof:
python3 -c "import importlib.util as u; mods=[...16 canonical targets...]; missing=[m for m in mods if u.find_spec(m) is None]"  # ALL RESOLVE
grep -rnE "['\"]codex\." --include='*.py' .                     # zero matches (exit 1)
```
Both sanity imports fail ONLY on missing third-party deps — never on module-path errors. LSP server not installed (declined); inline per-edit diagnostics showed only pre-existing third-party-resolution/type errors, zero module-path errors.

---

## T3-fullstack-validation (2026-08-22)

### Clean-slate bring-up: ALL 5 SERVICES HEALTHY AT t=46s (gate ≤180s)

Sequence: `down -v` → `build --no-cache` → `up -d`. Gates: /health `database:healthy, neo4j:healthy`; admin login HTTP 200 + bearer JWT; postgres 60s log window silent + full boot log 0 FATAL; `\l` lists codex_db (owner codex_admin); pgvector extension present; users table seeded (admin/manager/employee).

### Fixes that landed this task (all live-evidence-driven)

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | compose PYTHONPATH overrides | api/ui/worker `environment:` carried `PYTHONPATH: /app:/`, overriding Dockerfile ENV at runtime | reduced to `/app` ×3 |
| 2 | api could NEVER be healthy | healthcheck `grep -q ok` but `/health` JSON (`{"status":"healthy",...}`) contains no "ok" substring — proven statically from schemas+router before boot | healthcheck now greps `'\"status\":\"healthy\"'` |
| 3 | ISC-1 demands "/health reports db+neo4j ok" but endpoint had zero neo4j awareness | `/health` checked db + LLM only | added bounded neo4j probe (`GraphDatabase.driver(...).verify_connectivity()`) + `neo4j` field on HealthResponse (minimal diff, recorded) |
| 4 | clean-slate seed crash (predicted statically) | `seed_users.py` queries `users` table but nothing calls `init_db()` before it on a fresh volume (only ingestion paths call it) | compose api command chain: insert `python -c 'from packages.shared.db import init_db; init_db()'` between check_dependencies and seed_users |
| 5 | cold build failed: `No matching distribution found for torch==2.5.1+cpu` | `+cpu` local-version wheels are NOT on PyPI; PyTorch CPU index has PRUNED old wheels — its `+cpu` builds now start at 2.6.0. Prior images only existed via layer cache | Dockerfile pip line gained `--extra-index-url https://download.pytorch.org/whl/cpu` AND requirements pin repaired to plain `torch==2.5.1` (same dep, not new; also unbreaks native cold installs sharing requirements.txt) |
| 6 | neo4j crash-loop `Failed to read config: Unrecognized setting. No declared setting with name: PASS` | neo4j docker entrypoint maps EVERY `NEO4J_*` env var to a config setting; `env_file: .env` leaked `NEO4J_PASS` → bogus setting `pass` → strict validation abort | removed `env_file: .env` from neo4j service only (auth flows via compose-level `${NEO4J_USER}/${NEO4J_PASS}` interpolation into NEO4J_AUTH); api/ui/worker keep env_file (our app WANTS those vars) |
| 7 | ISC-7 wants every service `healthy` but ui/worker had NO healthcheck (would show "running" forever) | missing definitions | ui: `curl :8501/_stcore/health` (Streamlit ≥1.26 returns literal "ok"); worker: liveness via singleton pidfile `$STATE_DIR/ingestion_worker.pid` + `os.kill(pid,0)` |

### Patterns that worked
- Static pre-flight against container reality (read router/schema/compose BEFORE building) caught bugs #2/#3/#4 without burning a build cycle.
- `docker compose config --quiet` validates YAML without printing resolved secrets.
- Runtime env proof without touching .env: `docker compose exec <svc> printenv KEY` for non-secret keys (SEED_DEFAULT_USERS/POSTGRES_DB/PYTHONPATH). Host-side `.env` reads were blocked by CC Safety Net — runtime printenv is the sanctioned path.
- `--since 60s` log window + full-boot scan together: quiet window proves no recurring loop; full scan proves clean first boot.
- Compose `$$` escaping: `$$VAR` inside command:/healthcheck stays literal for the container shell.
