#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"
require_command curl
require_command pg_isready
require_file "$PYTHON_BIN"
require_file "$NEO4J_HOME/bin/cypher-shell"
require_python

[[ "${1:-}" == "--reset" ]] || {
    echo "Graph deletion requires: $0 --reset" >&2
    exit 2
}

cd "$CODEX_DIR"
bash "$SCRIPT_DIR/server_qwen3_graph.sh" >"$LOG_DIR/qwen3_graph_start.log" 2>&1 &
QWEN_PID=$!
trap 'kill "$QWEN_PID" 2>/dev/null || true' EXIT

for i in $(seq 1 60); do
    if curl -fsS "$LLAMA_4B_URL/health" >/dev/null 2>&1; then break; fi
    [[ "$i" -eq 60 ]] && { echo "Qwen server did not become ready" >&2; exit 1; }
    sleep 2
done

"$NEO4J_HOME/bin/cypher-shell" -a "$NEO4J_URI" \
    -u "${NEO4J_USER:-neo4j}" -p "${NEO4J_PASS:?Set NEO4J_PASS in .env}" \
    "MATCH (n) DETACH DELETE n;"

"$PYTHON_BIN" services/ingestion/migrate_to_neo4j.py
kill "$QWEN_PID" 2>/dev/null || true
trap - EXIT

bash "$SCRIPT_DIR/server.sh" >"$LOG_DIR/llama_8b_restart.log" 2>&1 &
echo "Migration complete; Qwen3-8B is restarting on $LLAMA_8B_URL."
