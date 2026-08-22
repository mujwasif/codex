# Decisions — docker-fullstack.plan

Architectural choices and rationales discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## T1-build-boot-probe (2026-08-22)

- **Worker singleton lock: self-PID must never count as "another worker"** — `_pid_is_worker()` in `services/ingestion/ingestion_agent.py` now returns False when the lockfile PID equals `os.getpid()`. Rationale: PIDs are unique among live processes in a PID namespace, so a lock naming our own PID cannot belong to a different live worker; it is a stale leftover from a previous incarnation. This is what makes stale bind-mounted pidfiles self-healing instead of fatal.
- **Chose code fix over manual pidfile cleanup**: deleting `state/ingestion_worker.pid` by hand would green this run but re-break on every future stale boot. The self-PID guard fixes the class, not the instance; genuine two-worker contention (different PIDs) is still blocked by the `/proc/<pid>/cmdline` check.
- **Did NOT touch docker-compose.yml** (plan forbids it): the bind-mounted `state/` dir and `STATE_DIR=/app/state` stay as-is; the fix lives entirely in existing service code within the allowed scope (services/, packages/, infra/docker/*, Dockerfile).
