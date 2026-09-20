#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_command curl
require_file "$PYTHON_BIN"
require_python

[[ "$LLM_PROVIDER" == "api" ]] || die "API-only deployment requires LLM_PROVIDER=api"

# ── Local overrides (not Docker) ──────────────────────────────
PG_DATA="/home/mujtaba/pgdata"
PG_BIN="/home/mujtaba/postgresql/bin"
export PATH="$PG_BIN:$PATH"
export PG_DATA PG_BIN

mkdir -p "$PG_DATA" "$LOG_DIR"
cd "$CODEX_DIR"
python_module_env

# ── Helpers: kill existing processes before restart ────────────
kill_by_port() {
    local port=$1 name=$2
    local pids
    pids=$(lsof -ti ":$port" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "  Stopping existing $name (PID: $pids)..."
        kill $pids 2>/dev/null || true
        sleep 2
        kill -0 $pids 2>/dev/null && kill -9 $pids 2>/dev/null || true
        sleep 1
    fi
}

kill_by_pattern() {
    local pattern=$1 name=$2
    local pids
    pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "  Stopping existing $name (PID: $pids)..."
        kill $pids 2>/dev/null || true
        sleep 2
        kill -0 $pids 2>/dev/null && kill -9 $pids 2>/dev/null || true
        sleep 1
    fi
}

# ── PIDs we spawn (for trap) ──────────────────────────────────
LLM_PID=""
API_PID=""
WORKER_PID=""
UI_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    [[ -n "$API_PID" ]]    && kill "$API_PID" 2>/dev/null || true
    [[ -n "$WORKER_PID" ]] && kill "$WORKER_PID" 2>/dev/null || true
    [[ -n "$UI_PID" ]]     && kill "$UI_PID" 2>/dev/null || true
    [[ -n "$LLM_PID" ]]    && kill "$LLM_PID" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup INT TERM

echo ""
echo "═══════════════════════════════════════════"
echo " Starting Codex — $CODEX_DIR"
echo "═══════════════════════════════════════════"
echo ""

# ── Step 1: PostgreSQL ────────────────────────────────────────
echo "[1/6] PostgreSQL..."
if pg_isready -p "$PG_PORT" -q 2>/dev/null; then
    echo "  Stopping existing PostgreSQL..."
    pg_ctl -D "$PG_DATA" stop -m fast 2>/dev/null || true
    sleep 2
fi
if command -v pg_ctl >/dev/null 2>&1; then
    echo "  Starting PostgreSQL on port $PG_PORT..."
    pg_ctl -D "$PG_DATA" -l "$LOG_DIR/postgres.log" start 2>/dev/null || true
    for i in $(seq 1 15); do
        if pg_isready -p "$PG_PORT" -q 2>/dev/null; then
            echo "  ✓ Ready on port $PG_PORT"
            break
        fi
        sleep 1
    done
    if ! pg_isready -p "$PG_PORT" -q 2>/dev/null; then
        echo "  ✗ FAILED — not reachable on port $PG_PORT" >&2
        exit 1
    fi
else
    echo "  ✗ pg_ctl not found" >&2
    exit 1
fi

# ── Step 2: Neo4j ────────────────────────────────────────────
echo "[2/6] Neo4j..."
if [[ -n "$NEO4J_HOME" && -x "$NEO4J_HOME/bin/neo4j" ]]; then
    if curl -fsS "http://127.0.0.1:$NEO4J_HTTP_PORT" >/dev/null 2>&1; then
        echo "  Stopping existing Neo4j..."
        "$NEO4J_HOME/bin/neo4j" stop 2>/dev/null || true
        sleep 3
    fi
    echo "  Starting Neo4j..."
    "$NEO4J_HOME/bin/neo4j" start
    for i in $(seq 1 10); do
        if curl -fsS "http://127.0.0.1:$NEO4J_HTTP_PORT" >/dev/null 2>&1; then
            echo "  ✓ Ready on port $NEO4J_HTTP_PORT"
            break
        fi
        sleep 2
    done
    if ! curl -fsS "http://127.0.0.1:$NEO4J_HTTP_PORT" >/dev/null 2>&1; then
        echo "  ⚠ Not reachable — continuing without Neo4j" >&2
    fi
else
    echo "  ⚠ Not configured — expecting Neo4j at $NEO4J_URI" >&2
fi

# ── Step 3: Local LLM (Qwen3-4B Q5_K_M) ─────────────────────
LLM_SERVER_BIN="${LLM_SERVER_BIN:-/home/mujtaba/llama.cpp/build/bin/llama-server}"
LLM_MODEL_PATH="${LLM_MODEL_PATH:-/home/mujtaba/models/Qwen3-4B-Q5_K_M.gguf}"
LLM_PORT="${LLM_PORT:-8080}"
LLM_CTX="${LLM_CTX:-20480}"
LLM_GPU_LAYERS="${LLM_GPU_LAYERS:-99}"

echo "[3/6] LLM (Qwen3-4B Q5_K_M)..."
if curl -fsS "http://127.0.0.1:$LLM_PORT/health" >/dev/null 2>&1; then
    kill_by_port "$LLM_PORT" "LLM"
fi
if [[ -x "$LLM_SERVER_BIN" && -f "$LLM_MODEL_PATH" ]]; then
    echo "  Starting on port $LLM_PORT (ctx=$LLM_CTX, layers=$LLM_GPU_LAYERS)..."
    "$LLM_SERVER_BIN" \
        -m "$LLM_MODEL_PATH" \
        --host 0.0.0.0 \
        --port "$LLM_PORT" \
        --n_gpu_layers "$LLM_GPU_LAYERS" \
        --ctx-size "$LLM_CTX" \
        --flash-attn on \
        --parallel 4 \
        --cache-type-k q4_0 \
        --cache-type-v q4_0 \
        >"$LOG_DIR/llm_server.log" 2>&1 &
    LLM_PID=$!
    for i in $(seq 1 60); do
        if curl -fsS "http://127.0.0.1:$LLM_PORT/health" >/dev/null 2>&1; then
            echo "  ✓ Ready on port $LLM_PORT (PID=$LLM_PID)"
            break
        fi
        sleep 2
    done
    if ! curl -fsS "http://127.0.0.1:$LLM_PORT/health" >/dev/null 2>&1; then
        echo "  ✗ FAILED — check $LOG_DIR/llm_server.log" >&2
        exit 1
    fi
else
    echo "  ✗ Binary or model not found" >&2
    exit 1
fi

# ── Step 4: FastAPI ──────────────────────────────────────────
echo "[4/6] FastAPI..."
if curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
    kill_by_port "$API_PORT" "FastAPI"
fi
"$PYTHON_BIN" -m uvicorn services.api.main:app --host "$API_HOST" --port "$API_PORT" \
    >"$LOG_DIR/fastapi.log" 2>&1 &
API_PID=$!
for i in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
        echo "  ✓ Ready on port $API_PORT (PID=$API_PID)"
        break
    fi
    sleep 2
done
if ! curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
    echo "  ✗ FAILED — check $LOG_DIR/fastapi.log" >&2
    exit 1
fi

# ── Step 5: Ingestion Worker ─────────────────────────────────
echo "[5/6] Ingestion Worker..."
kill_by_pattern "services.ingestion.ingestion_agent" "Ingestion Worker"
"$PYTHON_BIN" -m services.ingestion.ingestion_agent \
    >"$LOG_DIR/ingestion_agent.log" 2>&1 &
WORKER_PID=$!
sleep 2
if kill -0 "$WORKER_PID" 2>/dev/null; then
    echo "  ✓ Running (PID=$WORKER_PID)"
else
    echo "  ⚠ Died on startup — check $LOG_DIR/ingestion_agent.log" >&2
fi

# ── Step 6: Streamlit UI ─────────────────────────────────────
echo "[6/6] Streamlit UI..."
if curl -fsS "http://127.0.0.1:$UI_PORT/_stcore/health" >/dev/null 2>&1; then
    kill_by_port "$UI_PORT" "Streamlit UI"
fi
"$PYTHON_BIN" -m streamlit run services/chat/ui.py --server.address "$API_HOST" \
    --server.port "$UI_PORT" --server.headless true >"$LOG_DIR/streamlit.log" 2>&1 &
UI_PID=$!
for i in $(seq 1 10); do
    if curl -fsS "http://127.0.0.1:$UI_PORT/_stcore/health" >/dev/null 2>&1; then
        echo "  ✓ Ready on port $UI_PORT (PID=$UI_PID)"
        break
    fi
    sleep 2
done
if ! curl -fsS "http://127.0.0.1:$UI_PORT/_stcore/health" >/dev/null 2>&1; then
    echo "  ⚠ Not reachable — check $LOG_DIR/streamlit.log" >&2
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════"
echo " All services started"
echo "═══════════════════════════════════════════"
echo " API:    http://127.0.0.1:$API_PORT"
echo " UI:     http://127.0.0.1:$UI_PORT"
echo " LLM:    $LLM_BASE_URL"
echo " Backup: http://127.0.0.1:$LLM_PORT"
echo " Logs:   $LOG_DIR"
echo "═══════════════════════════════════════════"

wait
