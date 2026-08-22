# Issues — docker-fullstack.plan

Problems and gotchas encountered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

---

## T3-fullstack-validation (2026-08-22)

- **torch==2.5.1+cpu is uninstallable anywhere (Aug 2026)**: not on PyPI (local tag), and the PyTorch CPU index pruned ≤2.5.x wheels (+cpu now starts at 2.6.0). Any cached-layer-free build of the old pin fails. Fixed in requirements.txt + Dockerfile extra-index. Watch: if someone re-pins +cpu, minimum is 2.6.0+cpu.
- **neo4j image + env_file(.env) = boot loop**: the neo4j entrypoint treats every NEO4J_* var as a config key (NEO4J_PASS → setting "pass" → strict-validation abort). Never attach env_file to the neo4j service; inject auth via compose interpolation only.
- **api healthcheck grep pattern must match payload exactly**: bare `grep -q healthy` would false-positive on `"llama_server":"unhealthy"` when db is down; use the quoted `"status":"healthy"` form.
- **Fresh volume ⇒ no tables**: nothing in the api boot chain created schema before seeding; init_db() must run explicitly before seed_users.py on any clean-slate bring-up (compose command now does).
- **ui/worker needed invented healthchecks**: Streamlit exposes `/_stcore/health` ("ok"); worker liveness piggybacks on its own singleton pidfile ($STATE_DIR/ingestion_worker.pid) — if worker crashes, pidfile pid dies, os.kill(pid,0) raises, healthcheck goes red.
- **CC Safety Net blocks host reads of `.env`** (secret.basename.env rule) — even targeted `grep -c '^KEY=' .env`. Use `docker compose exec <svc> printenv KEY` post-boot for non-secret keys instead; never cat the file into artifacts.
- **`llama_server:"unhealthy"` in /health is EXPECTED here** — it's the hosted-LLM reachability probe and no LLM endpoint is reachable from this box; it does not gate ISC-1 (db+neo4j do). Smoke task T5 owns the DEGRADED-PASS semantics.

---

## T1-build-boot-probe (2026-08-22)

- **Worker restart-loop (exit 0) via stale pidfile + self-PID false positive**: `state/ingestion_worker.pid` is on a host **bind mount**, so `down -v` never clears it (bind mounts ≠ named volumes). A prior container's worker ran as PID 1 and wrote `1` into the lockfile. On reboot the new worker is *also* PID 1, so `_pid_is_worker(1)` read `/proc/1/cmdline` — its own process — matched `"ingestion_agent"`, concluded "Another ingestion worker is already running (pid 1)", and exited 0 forever. Compose kept restarting it: 4/5 healthy, worker flapping every ~5s.
  - Root cause chain: bind-mounted state survives teardown → stale PID value coincides with always-alive container PID 1 → liveness check can't distinguish self from another worker.
  - Fix: `_pid_is_worker()` returns False when `pid == os.getpid()` (a lock naming our own PID can never be a different live worker). Diff: `.omo/start-work/artifacts/09_fix_diff_ingestion_agent.patch`.
- **Profile-gated services are NOT orphans**: stale `codex-main-tests-1` (Exited 137) survived `docker compose down -v --remove-orphans` because compose v5 only removes containers whose service is *undefined*; `tests` is still defined (just profile-gated), so down skips it and it isn't an orphan either. Cleared with direct `docker rm codex-main-tests-1`. Expect this again after any run that starts the tests profile.
- **Pre-existing LSP noise in ingestion_agent.py** (not from T1): unresolved imports sentence_transformers/sqlalchemy (host venv lacks them) and `parse_document_structure` undefined at line ~197 — both present in HEAD baseline; unrelated to boot health inside containers.
