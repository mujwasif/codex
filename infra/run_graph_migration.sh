#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"
[[ "$LLM_PROVIDER" == "api" ]] || die "API-only migration requires LLM_PROVIDER=api"
require_file "$PYTHON_BIN"
require_python
cd "$CODEX_DIR"

if [[ "${1:-}" == "--reset" ]]; then
    require_file "$NEO4J_HOME/bin/cypher-shell"
    "$NEO4J_HOME/bin/cypher-shell" -a "$NEO4J_URI" \
        -u "$NEO4J_USER" -p "$NEO4J_PASS" "MATCH (n) DETACH DELETE n;"
fi

"$PYTHON_BIN" services/ingestion/migrate_to_neo4j.py --incremental
echo "API-based Neo4j migration complete."
