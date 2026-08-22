# Work Plan — Codex Docker: build + login + health + server deploy (UI-only)

> v4 (2026-08-22): SIMPLE. Two tasks: (1) build/login/health probes, (2) server deploy with only the UI port public.
> Execute via `/start-work` only.

## Goal
Fresh `docker compose up -d --build` gives 5 healthy services, a healthy `/health`, and a working admin login. On the server, ONLY the UI is reachable from outside; api/postgres/neo4j stay private on the compose network.

## Task 1 — Build, boot, probe (the only task)
- [x] Task 1 — Build, boot, probe: 5/5 services healthy, /health green, admin login returns access_token (verified 2026-08-22; fix: stale-pidfile self-PID guard in services/ingestion/ingestion_agent.py)

```bash
docker compose down -v
docker compose up -d --build
docker compose ps                # all 5 services "healthy" within 180s
curl -s http://localhost:8000/health    # status healthy, db + neo4j ok
curl -s -X POST http://localhost:8000/login \
     -d "username=admin&password=password123"   # returns access_token
```
Gate: all five checks pass. If something fails, fix it and rerun until green.

## Task 2 — Server deploy: UI-only exposure
Why: CONFIRMED 2026-08-22 by owner: another container already binds host 0.0.0.0:8000 (`address already in use` on endpoint codex-policy-api-1), and the owner wants only the UI public. Owner PICKED option "delete api ports block" over freeing 8000 or remapping to 8100. The API needs NO host port: the Streamlit UI calls the API server-side, verified at `services/chat/ui.py:9` via `CODEX_API_URL`.

Edits to `docker-compose.yml` (only these):
1. `api`: delete its whole `ports:` block (this alone kills the 8000 collision).
2. `postgres`: delete `ports:` ("5432:5432") — never expose a db on a public host.
3. `neo4j`: delete `ports:` ("7474", "7687").
4. `ui`: keep `${UI_PORT:-8501}:8501`; add env line `CODEX_API_URL: http://api:8000` (currently missing, so ui.py would default to 127.0.0.1 inside its own container and fail).

Server steps:
```bash
git pull && cp .env.example .env   # set strong POSTGRES_PASSWORD, NEO4J_PASS, SECRET_KEY; SEED_DEFAULT_USERS=true
docker compose down -v && docker compose up -d --build
docker compose ps                  # 5/5 healthy
docker compose exec ui printenv CODEX_API_URL   # must be http://api:8000 (the CONTAINER port uvicorn listens on, NEVER a host-mapped port like 8100)
```
Rule: container-to-container URLs use the in-container listen port only; host port mappings are invisible to inter-container traffic. A `connection refused` on `api:<port>` means that exact port has no listener inside the api container.
Troubleshoot anchor: if api dies right after "All Codex Python dependencies are installed..." with `SyntaxError: from`, the quoting in the api `command:` block was mangled: the `python -c` snippet must keep SINGLE quotes (`python -c 'from packages.shared.db import init_db; init_db()'`) inside the outer DOUBLE quotes of the sh -c payload, and `$$` before `{API_PORT}` must stay doubled. Verify with `git diff docker-compose.yml` and `docker compose config | grep -A6 'command'`.
Probes from laptop: open `http://SERVER_IP:8501`, login admin ok; `curl http://SERVER_IP:8000/health` must FAIL (connection refused); same for 5432/7687.
Gate: UI works from outside, everything else refuses connections from outside.
If direct API access is ever needed from a laptop: SSH tunnel `ssh -L 8000:localhost:8000 user@server` after binding api to `127.0.0.1:${API_PORT}:8000` instead of deleting ports (alternative variant, not default).

## Must not do
Compose edits limited to Task 2's four items; no new scripts, no new dependencies, no doc rewrites.

## Todos (v5 execution round — owner picked Option 1: delete api host binding)
- [x] 1. api: delete entire `ports:` block including the 8100 comment lines (kills the 8000 collision class permanently) — verified in git diff: hunk @@ -68,8 +63,6 @@ pure deletion
- [x] 2. postgres: delete `ports:` block ("5432:5432") — verified: hunk @@ -14,8 +14,6 @@
- [x] 3. neo4j: delete `ports:` block ("7474:7474", "7687:7687") — verified: hunk @@ -35,9 +33,6 @@
- [x] 4. ui: add `CODEX_API_URL: http://api:8000` to service environment — PRE-SATISFIED at docker-compose.yml:78 (present in HEAD; executor's "lost T11 fix" premise was stale, documented in issues.md T14)
- [x] F1. Final verification: PASS (verifier ses_fd7a14f58ffeYn2XupZRQzAmIE, evidence in artifacts/15_task2_live_*.txt, orchestrator re-read raw artifacts): config exit 0 with exactly one published port "8501"; host 8000/5432/7687/8100 all refused (exit 7); 5/5 healthy post-recreate; exec-in-ui login via http://api:8000 → bearer token; api command quoting intact
