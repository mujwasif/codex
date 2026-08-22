#!/bin/sh
# T4 test-profile entrypoint (ISC-3, ISC-4).
#
# Orchestrates: isolated test-DB lifecycle + EVERY suite under tests/.
#   phase 0  bootstrap codex_test_db (drop/create/extension/init_db/seed)
#   phase 1  `python -m unittest discover -s tests -v`  (TestCase suites)
#   phase 2  functional runner for the bare-function main() suites
#            (unittest discovery cannot collect them — see
#            infra/docker/run_functional_suites.py header)
#   phase 3  teardown codex_test_db (always runs once phases 1–2 finished)
#
# Exit code = ORed failure of discovery / functional / teardown; the bootstrap
# failure short-circuits with its own code. docker compose --exit-code-from
# tests propagates this as the container exit code. Dev data on codex_db is
# never touched: DATABASE_URL points at codex_test_db for the whole run.
set -u

echo "== [T4] phase 0/3: bootstrap isolated test database (${TEST_DB_NAME:-codex_test_db}) =="
python infra/docker/test_db_lifecycle.py bootstrap
BOOT_RC=$?
if [ "$BOOT_RC" -ne 0 ]; then
    echo "== [T4] bootstrap FAILED rc=$BOOT_RC — aborting before suites (dev data untouched) =="
    exit "$BOOT_RC"
fi

RC=0

echo "== [T4] phase 1/3: unittest discovery suites =="
python -m unittest discover -s tests -v
DISCOVER_RC=$?
echo "== [T4] unittest discovery rc=$DISCOVER_RC =="
if [ "$DISCOVER_RC" -ne 0 ]; then RC=1; fi

echo "== [T4] phase 2/3: functional (bare-function) suites =="
python infra/docker/run_functional_suites.py
FUNC_RC=$?
echo "== [T4] functional rc=$FUNC_RC =="
if [ "$FUNC_RC" -ne 0 ]; then RC=1; fi

echo "== [T4] phase 3/3: teardown isolated test database =="
python infra/docker/test_db_lifecycle.py teardown
TEAR_RC=$?
if [ "$TEAR_RC" -ne 0 ]; then RC=1; fi

echo "== [T4] final exit code: $RC =="
exit "$RC"
