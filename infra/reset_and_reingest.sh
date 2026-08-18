#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

[[ "${1:-}" == "--reset" ]] || {
    echo "Destructive reset requires: $0 --reset" >&2
    exit 2
}
[[ "$LLM_PROVIDER" == "api" ]] || die "API-only ingestion requires LLM_PROVIDER=api"
require_command curl
require_command psql
require_command pg_isready
require_file "$PYTHON_BIN"
require_python

cd "$CODEX_DIR"
if ! pg_isready -p "$PG_PORT" -q; then
    bash "$SCRIPT_DIR/bootstrap_local.sh"
fi

if ! curl -fsS "http://127.0.0.1:$NEO4J_HTTP_PORT" >/dev/null 2>&1; then
    [[ -n "$NEO4J_HOME" && -x "$NEO4J_HOME/bin/neo4j" ]] || die "Neo4j is not running"
    "$NEO4J_HOME/bin/neo4j" start
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c \
    "TRUNCATE TABLE documents, chunks, entities, queries, answers, citations, feedback, audit_log RESTART IDENTITY CASCADE;"
if [[ -n "$NEO4J_HOME" && -x "$NEO4J_HOME/bin/cypher-shell" ]]; then
    "$NEO4J_HOME/bin/cypher-shell" -a "$NEO4J_URI" \
        -u "$NEO4J_USER" -p "$NEO4J_PASS" "MATCH (n) DETACH DELETE n;"
fi
rm -f "$STATE_DIR/processed_chunks.txt" "$MIGRATION_STATE_FILE"

"$PYTHON_BIN" services/ingestion/ingest.py
"$PYTHON_BIN" services/ingestion/migrate_to_neo4j.py

echo "Reset, API-based ingestion, and graph migration complete."
