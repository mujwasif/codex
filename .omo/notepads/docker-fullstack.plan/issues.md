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

---

## T11-ui-fix (2026-08-22)

- **UI login "Connection refused" to 127.0.0.1:8000 from inside the ui container**: `services/chat/ui.py:9` reads `CODEX_API_URL` (default `http://127.0.0.1:8000`); the ui container inherits the host-oriented value from `.env` via `env_file:`, but in-container 127.0.0.1 is its own loopback where nothing listens — the API is reachable in-network at `http://api:8000`.
  - Fix: one-line compose override `CODEX_API_URL: http://api:8000` added to the **ui service's** `environment:` block only (compose `environment:` beats `env_file:`). api/worker keep their host-facing defaults; `.env` untouched.
  - Verified: A) `printenv CODEX_API_URL` → `http://api:8000`; B) login POST to `http://api:8000/login` from inside ui → `bearer`; C) host `curl 127.0.0.1:8000/health` still healthy. Artifacts: `.omo/start-work/artifacts/11_uifix_{A_env_and_ps,B_login,C_host_health}.txt`.
  - Watch: any future service that calls the API *from inside* the network needs the same `http://api:8000` form; host-side callers keep `127.0.0.1:8000`.

---

## T12-dockerfile-chownfix (2026-08-22)

- **Remote-server build stall: 770s on the post-COPY `RUN chown -R appuser:appuser /app` step** (local OrbStack measured the same step at 0.1–0.2s — the report came from a remote box, likely vfs storage driver or very slow disk).
  - Root cause: two compounding costs. (1) `chown -R` is a per-file metadata storm over every source file in /app (~thousands of inodes), each an lchown syscall; (2) because COPY created the layer owned by root and chown mutates it, the storage driver must materialize a copy-up of the entire tree into a new layer — on vfs that means duplicating the whole rootfs, on slow disks it's brutal either way.
  - Fix applied (Dockerfile lines ~29-34 only): `COPY --chown=appuser:appuser . .` sets ownership at COPY time (metadata set during layer creation, no second pass), and the RUN narrows to `chown appuser:appuser /app/logs /app/state /app/.state /app/archive` — non-recursive, four empty runtime dirs only.
  - Verified locally: pip layer (6/8) stayed CACHED; steps 7/8 + 8/8 each DONE 0.2s; full `docker compose build api ui worker` wall time 3.657s. All 5 services healthy after `up -d` (postgres/neo4j untouched). Ownership inside api container: files UID/GID 10001, `id -u` → 10001.
  - Artifact: `.omo/start-work/artifacts/12_dockerfile_chownfix_20260822_112926.txt`.
  - Watch: any future Dockerfile change that adds files needing non-root ownership must rely on `COPY --chown` (or per-path chown), never reintroduce `-R` over /app.

---

## T13-api-host-port-8100 (2026-08-22)

- **Goal**: move the API's externally published port 8000 → 8100 while keeping the in-container port at 8000 (uvicorn bind, healthcheck, and ui `CODEX_API_URL: http://api:8000` all unchanged — T11 path intact).
- **Plan premise was wrong**: inherited wisdom claimed "no .env override" for API_PORT. Disproved empirically without reading `.env` (safety net): with the templated line `${API_PORT:-8100}:8000`, `docker compose config` still rendered published **8000**; `API_PORT=7777 docker compose config` rendered 7777. Shell-unset → .env value wins ⇒ `.env` contains `API_PORT=8000`. (Also: no compose override file exists.)
- **Consequence**: the prescribed one-line template change is a no-op while `.env` pins API_PORT=8000 — and editing `.env` is forbidden (and setting it to 8100 would leak into the container via `env_file:` and break uvicorn/healthcheck/ui anyway). Fix: pinned the published side on the same single line — `- "8100:8000"` with a trap-warning comment. Container side untouched.
- **Verified**: config render `{target: 8000, published: '8100'}`; all 5 services healthy after `up -d api ui worker`; host `GET :8100/health` → healthy; host `POST :8100/login` → access_token/bearer; host `:8000/health` → connection refused (exit 7); in-container ui→`http://api:8000/login` → bearer. Artifacts: `.omo/start-work/artifacts/13_apiport_{config_render,host_probes,container_probe_ps,diff}.txt`.
- **Watch**: host-side callers must now use `http://127.0.0.1:8100` (README examples and `services/chat/cli.py` default to 8000). To change the host port again, edit docker-compose.yml directly — do NOT set API_PORT in `.env` (it feeds the container side too and breaks the 8100→8000 mapping for any value ≠8000).

---

## T14-task2-ui-only-exposure (2026-08-22)

- **Owner decision "Option 1" applied**: only the Streamlit ui publishes a host port (8501). Deleted all three private-service `ports:` blocks — api (incl. the T13 `"8100:8000"` mapping + its 2 comment lines), postgres (`5432:5432`), neo4j (`7474:7474`, `7687:7687`). Container-side ports, `command:`, and healthchecks untouched; api `sh -c` payload quoting preserved byte-for-byte.
- **Surprise — edit 4 was pre-satisfied**: task brief said to ADD `CODEX_API_URL: http://api:8000` to the ui environment ("lost from the working tree"), but the line was already present in both HEAD and the working tree (first key of ui's `environment:` block; current diff vs HEAD showed no ui hunk before this task). No edit needed — likely restored by an earlier session. Recorded here so the verifier doesn't expect a 4th hunk.
- **Consequence for host callers**: api is no longer reachable from the host at :8100 or :8000 — only container-to-container via `http://api:8000`. This supersedes T13's watch note about using `http://127.0.0.1:8100`.

## T15-task2-live-verification (2026-08-22)

- **VERDICT: PASS** — UI-only exposure verified live after recreation (`up -d` recreated postgres/neo4j/api; ui/worker untouched). 5/5 healthy at t=0s poll. Host refusals: 8000/5432/7687/**8100** all curl exit 7 (old 8100 remap gone). Host reachability: :8501 → HTTP 200. In-container: api `/health` → `"status":"healthy"`; ui→`http://api:8000/login` → HTTP 200 token_type=bearer. Render: `docker compose config` exit 0, exactly ONE `published: "8501"` (ui service, line 294); zero ports keys on api/postgres/neo4j/worker. Artifacts: `.omo/start-work/artifacts/15_task2_live_{ps,host_probes,container_probes,config_render,ui_reach}.txt`. No tracked files modified by verifier.
