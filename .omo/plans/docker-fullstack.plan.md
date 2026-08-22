# Work Plan — Codex Docker: build + login + health (SIMPLE)

> v3 (2026-08-22): SIMPLE by owner request. Build + login + health. Nothing else.
> Execute via `/start-work` only.

## Goal
Fresh `docker compose up -d --build` gives 5 healthy services, a healthy `/health`, and a working admin login.

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

## Must not do
No compose edits, no new scripts, no new dependencies, no doc rewrites.
