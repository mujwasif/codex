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

# ══════════════════════════════════════════════════════════════
#  Helpers: kill existing processes before restart
# ══════════════════════════════════════════════════════════════

kill_by_port() {
    local port=$1 name=$2
    local pids
    pids=$(lsof -ti ":$port" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "  Stopping $name (PID: $pids)..."
        kill $pids 2>/dev/null || true
        sleep 1
        # Force-kill if still alive
        kill -0 $pids 2>/dev/null && kill -9 $pids 2>/dev/null || true
        sleep 1
    fi
}

kill_by_pattern() {
    local pattern=$1 name=$2
    local pids
    pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "  Stopping $name (PID: $pids)..."
        kill $pids 2>/dev/null || true
        sleep 1
        kill -0 $pids 2>/dev/null && kill -9 $pids 2>/dev/null || true
        sleep 1
    fi
}

# Wait for a port to become free. Returns 0 if free, 1 if still occupied.
wait_for_port_free() {
    local port=$1 max_wait=${2:-10}
    for i in $(seq 1 "$max_wait"); do
        if ! lsof -ti ":$port" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Wait for a URL to return HTTP 200. Returns 0 if healthy, 1 if not.
wait_for_healthy() {
    local url=$1 name=$2 max_wait=${3:-30}
    for i in $(seq 1 "$max_wait"); do
        if curl -fsS "$url" --max-time 3 >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "  ✗ $name not healthy after ${max_wait}s — $url" >&2
    return 1
}

# Aggressively free a port: kill by port, then force-kill if needed.
# Only targets processes on the specific port — never kills other services.
force_free_port() {
    local port=$1 name=$2
    kill_by_port "$port" "$name"
    if ! wait_for_port_free "$port" 5; then
        echo "  Force-killing remaining PIDs on port $port..."
        kill -9 $(lsof -ti ":$port") 2>/dev/null || true
        sleep 1
    fi
    if lsof -ti ":$port" >/dev/null 2>&1; then
        echo "  ✗ WARNING: Port $port still occupied after cleanup" >&2
        return 1
    fi
    return 0
}

# ══════════════════════════════════════════════════════════════
#  Pre-flight: kill ALL stale Codex processes
# ══════════════════════════════════════════════════════════════

echo ""
echo "Cleaning up stale processes..."

# Kill by process pattern (catches everything)
kill_by_pattern "services.ingestion.ingestion_agent" "Ingestion Worker"
kill_by_pattern "services.api.main:app" "Stale API"
kill_by_pattern "uvicorn services.api.main" "Stale uvicorn"
kill_by_pattern "services.chat.ui" "Stale UI"
kill_by_pattern "streamlit run services/chat" "Stale Streamlit"

# Kill by port
kill_by_port "$API_PORT" "FastAPI"
kill_by_port "$UI_PORT" "Streamlit UI"

# Wait for ports to fully release
echo "  Waiting for ports to release..."
wait_for_port_free "$API_PORT" 8 || true
wait_for_port_free "$UI_PORT" 5 || true

# Final aggressive cleanup if ports still held
if lsof -ti ":$API_PORT" >/dev/null 2>&1; then
    echo "  Force-killing PIDs on port $API_PORT..."
    kill -9 $(lsof -ti ":$API_PORT") 2>/dev/null || true
    sleep 2
fi
if lsof -ti ":$UI_PORT" >/dev/null 2>&1; then
    echo "  Force-killing PIDs on port $UI_PORT..."
    kill -9 $(lsof -ti ":$UI_PORT") 2>/dev/null || true
    sleep 2
fi

echo "  Cleanup done"
echo ""

# ══════════════════════════════════════════════════════════════
#  PIDs we spawn (for trap)
# ══════════════════════════════════════════════════════════════

API_PID=""
WORKER_PID=""
UI_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    [[ -n "$API_PID" ]]    && kill "$API_PID" 2>/dev/null || true
    [[ -n "$WORKER_PID" ]] && kill "$WORKER_PID" 2>/dev/null || true
    [[ -n "$UI_PID" ]]     && kill "$UI_PID" 2>/dev/null || true
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

# ── Step 3: Verify Funkash LLM endpoint ─────────────────────
FUNKASH_URL="${LLM_BASE_URL:-https://funkash.eu1.netbird.services/llm/v1}"
FUNKASH_MODEL="${LLM_MODEL:-gemma4:31b-cloud}"

echo "[3/6] Funkash LLM endpoint..."
if curl -fsS "$FUNKASH_URL/models" \
    ${LLM_API_KEY:+-H "Authorization: Bearer $LLM_API_KEY"} \
    --max-time 10 >/dev/null 2>&1; then
    echo "  ✓ Reachable at $FUNKASH_URL (model: $FUNKASH_MODEL)"
else
    echo "  ✗ FAILED — Funkash endpoint unreachable at $FUNKASH_URL" >&2
    echo "    Check LLM_BASE_URL and LLM_API_KEY in .env" >&2
    exit 1
fi

# ── Step 4: FastAPI (with retry) ──────────────────────────────
echo "[4/6] FastAPI..."

start_api() {
    "$PYTHON_BIN" -m uvicorn services.api.main:app --host "$API_HOST" --port "$API_PORT" \
        >"$LOG_DIR/fastapi.log" 2>&1 &
    API_PID=$!
    # Poll health, but also check if process is still alive
    for i in $(seq 1 50); do
        if curl -fsS "http://127.0.0.1:$API_PORT/health" --max-time 3 >/dev/null 2>&1; then
            return 0
        fi
        if ! kill -0 "$API_PID" 2>/dev/null; then
            echo "  ✗ FastAPI process died. Check $LOG_DIR/fastapi.log" >&2
            return 1
        fi
        sleep 2
    done
    echo "  ✗ FastAPI not healthy after 100s" >&2
    return 1
}

# Ensure port is free right before starting
force_free_port "$API_PORT" "FastAPI" || true

if ! start_api; then
    echo "  Retrying FastAPI (force-freeing port $API_PORT)..."
    force_free_port "$API_PORT" "FastAPI" || true
    sleep 2
    if ! start_api; then
        echo "  ✗ FAILED after retry — check $LOG_DIR/fastapi.log" >&2
        exit 1
    fi
fi
echo "  ✓ FastAPI ready on port $API_PORT (PID=$API_PID)"

# ── Step 5: Ingestion Worker ─────────────────────────────────
echo "[5/6] Ingestion Worker..."
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

force_free_port "$UI_PORT" "Streamlit" || true

"$PYTHON_BIN" -m streamlit run services/chat/ui.py --server.address "$API_HOST" \
    --server.port "$UI_PORT" --server.headless true >"$LOG_DIR/streamlit.log" 2>&1 &
UI_PID=$!

if wait_for_healthy "http://127.0.0.1:$UI_PORT/_stcore/health" "Streamlit UI" 30; then
    echo "  ✓ Streamlit UI ready on port $UI_PORT (PID=$UI_PID)"
else
    echo "  ⚠ Not reachable — check $LOG_DIR/streamlit.log" >&2
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════"
echo " All services started"
echo "═══════════════════════════════════════════"
echo " API:    http://127.0.0.1:$API_PORT"
echo " UI:     http://127.0.0.1:$UI_PORT"
echo " LLM:    $FUNKASH_URL (Funkash)"
echo " Logs:   $LOG_DIR"
echo "═══════════════════════════════════════════"

wait
