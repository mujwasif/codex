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

[[ "$LLM_PROVIDER" == "api" ]] || die "API-only deployment requires LLM_PROVIDER=api"

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

# Start local LLM backup server on port 8080 if not already running
LLM_SERVER_BIN="${LLM_SERVER_BIN:-/home/mujtaba/llama.cpp/build/bin/llama-server}"
LLM_MODEL_PATH="${LLM_MODEL_PATH:-/home/mujtaba/models/Qwen3-8B-Q4_K_M.gguf}"
LLM_PORT="${LLM_PORT:-8080}"
LLM_CTX="${LLM_CTX:-20480}"
LLM_GPU_LAYERS="${LLM_GPU_LAYERS:-33}"
LLM_PID=""

if ! curl -fsS "http://127.0.0.1:$LLM_PORT/health" >/dev/null 2>&1; then
    if [[ -x "$LLM_SERVER_BIN" && -f "$LLM_MODEL_PATH" ]]; then
        echo "Starting local LLM backup on port $LLM_PORT..."
        "$LLM_SERVER_BIN" \
            -m "$LLM_MODEL_PATH" \
            --host 0.0.0.0 \
            --port "$LLM_PORT" \
            --n_gpu_layers "$LLM_GPU_LAYERS" \
            --ctx-size "$LLM_CTX" \
            --flash-attn on \
            --cache-type-k q8_0 \
            --cache-type-v q8_0 \
            >"$LOG_DIR/llm_server.log" 2>&1 &
        LLM_PID=$!
        for i in $(seq 1 60); do
            if curl -fsS "http://127.0.0.1:$LLM_PORT/health" >/dev/null 2>&1; then
                echo "Local LLM ready on port $LLM_PORT (PID=$LLM_PID)"
                break
            fi
            sleep 2
        done
    else
        echo "WARNING: Local LLM binary or model not found — using hosted API only" >&2
    fi
else
    echo "Local LLM already running on port $LLM_PORT"
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

trap 'kill "$API_PID" "$WORKER_PID" "$UI_PID" "$LLM_PID" 2>/dev/null || true' INT TERM

echo "API: http://127.0.0.1:$API_PORT"
echo "UI:  http://127.0.0.1:$UI_PORT"
echo "LLM primary: $LLM_BASE_URL"
echo "LLM backup:  http://127.0.0.1:$LLM_PORT"
echo "Logs: $LOG_DIR"
echo "PIDs: API=$API_PID WORKER=$WORKER_PID UI=$UI_PID LLM=${LLM_PID:-none}"
wait
