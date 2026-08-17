#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_command curl
require_command pg_isready
require_command pg_ctl
require_file "$PYTHON_BIN"
require_python

if [[ "$LLM_PROVIDER" == "api" ]]; then
    [[ -n "${LLM_API_KEY:-}" ]] || die "LLM_API_KEY is required when LLM_PROVIDER=api"
else
    require_file "$LLAMA_CPP_BIN"
    require_file "$QWEN3_8B_MODEL_PATH"
    require_file "$QWEN3_4B_MODEL_PATH"
fi

mkdir -p "$PG_DATA" "$LOG_DIR"
cd "$CODEX_DIR"
python_module_env

echo "Starting Codex from $CODEX_DIR"

if ! pg_isready -p "$PG_PORT" -q; then
    bash "$SCRIPT_DIR/bootstrap_local.sh"
fi

if [[ -n "$NEO4J_HOME" && -x "$NEO4J_HOME/bin/neo4j" ]]; then
    if ! curl -fsS "http://127.0.0.1:$NEO4J_HTTP_PORT" >/dev/null 2>&1; then
        "$NEO4J_HOME/bin/neo4j" start
    fi
else
    echo "Neo4j_HOME is not configured; expecting Neo4j at $NEO4J_URI" >&2
fi

if [[ "$LLM_PROVIDER" == "local" ]]; then
"$LLAMA_CPP_BIN" -m "$QWEN3_8B_MODEL_PATH" --host 0.0.0.0 --port "$LLM_8B_PORT" \
    --n-gpu-layers "${GPU_LAYERS_8B:-30}" --ctx-size "${CTX_SIZE_8B:-16384}" \
    --flash-attn on --reasoning-format deepseek --threads "${THREADS_8B:-8}" \
    --threads-batch "${THREADS_BATCH_8B:-16}" --parallel "${PARALLEL_8B:-1}" \
    --log-disable >"$LOG_DIR/llama_8b.log" 2>&1 &
REASONER_PID=$!

"$LLAMA_CPP_BIN" -m "$QWEN3_4B_MODEL_PATH" --host 0.0.0.0 --port "$LLM_4B_PORT" \
    --n-gpu-layers "${GPU_LAYERS_4B:-10}" --ctx-size "${CTX_SIZE_4B:-16384}" \
    --flash-attn on --reasoning off --threads "${THREADS_4B:-4}" \
    --threads-batch "${THREADS_BATCH_4B:-8}" --parallel "${PARALLEL_4B:-1}" \
    --log-disable >"$LOG_DIR/llama_4b.log" 2>&1 &
CLASSIFIER_PID=$!
else
    REASONER_PID=""
    CLASSIFIER_PID=""
fi

"$PYTHON_BIN" -m uvicorn services.api.main:app --host "$API_HOST" --port "$API_PORT" \
    >"$LOG_DIR/fastapi.log" 2>&1 &
API_PID=$!

"$PYTHON_BIN" -m services.ingestion.ingestion_agent \
    >"$LOG_DIR/ingestion_agent.log" 2>&1 &
WORKER_PID=$!

"$PYTHON_BIN" -m streamlit run services/chat/ui.py --server.address "$API_HOST" \
    --server.port "$UI_PORT" --server.headless true >"$LOG_DIR/streamlit.log" 2>&1 &
UI_PID=$!

trap 'kill "$REASONER_PID" "$CLASSIFIER_PID" "$API_PID" "$WORKER_PID" "$UI_PID" 2>/dev/null || true' INT TERM

echo "API: http://127.0.0.1:$API_PORT"
echo "UI:  http://127.0.0.1:$UI_PORT"
echo "Logs: $LOG_DIR"
echo "PIDs: LLM8=$REASONER_PID LLM4=$CLASSIFIER_PID API=$API_PID WORKER=$WORKER_PID UI=$UI_PID"
wait
