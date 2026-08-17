#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

[[ "${1:-}" == "--reset" ]] || {
    echo "Destructive reset requires: $0 --reset" >&2
    exit 2
}
require_command curl
require_command psql
require_command pg_isready
require_file "$PYTHON_BIN"
require_python

export PATH="${PG_BIN:+$PG_BIN:}$PATH"
cd "$CODEX_DIR"

echo "Resetting Codex data using $CODEX_DIR"

if ! pg_isready -p "$PG_PORT" -q; then
    pg_ctl -D "$PG_DATA" -l "$LOG_DIR/postgres.log" start
fi

if [[ -n "$NEO4J_HOME" && -x "$NEO4J_HOME/bin/neo4j" ]]; then
    if ! curl -fsS "http://127.0.0.1:$NEO4J_HTTP_PORT" >/dev/null 2>&1; then
        "$NEO4J_HOME/bin/neo4j" start
    fi
fi

bash "$SCRIPT_DIR/server_qwen3_graph.sh" >"$LOG_DIR/qwen3_graph_start.log" 2>&1 &
QWEN_PID=$!
trap 'kill "$QWEN_PID" 2>/dev/null || true' EXIT

for i in $(seq 1 60); do
    if curl -fsS "$LLAMA_4B_URL/health" >/dev/null 2>&1; then break; fi
    [[ "$i" -eq 60 ]] && { echo "Qwen server did not become ready" >&2; exit 1; }
    sleep 2
done

psql "$DATABASE_URL" -c "SELECT 1" >/dev/null
psql "$DATABASE_URL" -c "TRUNCATE TABLE documents, chunks, entities, queries, answers, citations, feedback, audit_log RESTART IDENTITY CASCADE;"

if [[ -n "$NEO4J_HOME" ]]; then
    "$NEO4J_HOME/bin/cypher-shell" -a "$NEO4J_URI" \
        -u "${NEO4J_USER:-neo4j}" -p "${NEO4J_PASS:?Set NEO4J_PASS in .env}" \
        "MATCH (n) DETACH DELETE n;"
fi

rm -f "$STATE_DIR/processed_chunks.txt" "$MIGRATION_STATE_FILE"
"$PYTHON_BIN" services/ingestion/ingest.py

DOC_COUNT=$(psql "$DATABASE_URL" -t -c "SELECT count(*) FROM documents;")
CHUNK_COUNT=$(psql "$DATABASE_URL" -t -c "SELECT count(*) FROM chunks;")
echo "Documents ingested: ${DOC_COUNT//[[:space:]]/}"
echo "Chunks generated: ${CHUNK_COUNT//[[:space:]]/}"
echo "Reset and re-ingestion complete. Qwen graph server remains active."
