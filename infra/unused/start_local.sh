#!/bin/bash
# Start Codex locally (no Docker)
# PostgreSQL, Neo4j, Qwen3-8B, Qwen3-4B, FastAPI, Streamlit, Ingestion Worker

set -e

CODEX_DIR="${CODEX_DIR:-/home/mujtaba/new_folder/codex}"
VENV_DIR="${VENV_DIR:-/home/mujtaba/new_folder/fastmcp/venv}"
PG_BIN="${PG_BIN:-/home/mujtaba/postgresql/bin}"
PG_DATA="${PG_DATA:-/home/mujtaba/pgdata}"
NEO4J_HOME="${NEO4J_HOME:-/home/mujtaba/neo4j-community-5.26.0}"
MODELS_DIR="${MODELS_DIR:-/home/mujtaba/models}"
QWEN3_8B_MODEL_PATH="${QWEN3_8B_MODEL_PATH:-${MODELS_DIR}/Qwen3-8B-Q4_K_M.gguf}"
QWEN3_4B_MODEL_PATH="${QWEN3_4B_MODEL_PATH:-${MODELS_DIR}/Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf}"
LLM_8B_PORT="${LLM_8B_PORT:-8080}"
LLM_4B_PORT="${LLM_4B_PORT:-8081}"
GPU_LAYERS_8B="${GPU_LAYERS_8B:-30}"
GPU_LAYERS_4B="${GPU_LAYERS_4B:-10}"
CTX_SIZE_8B="${CTX_SIZE_8B:-16384}"
CTX_SIZE_4B="${CTX_SIZE_4B:-16384}"
THREADS_8B="${THREADS_8B:-8}"
THREADS_BATCH_8B="${THREADS_BATCH_8B:-16}"
THREADS_4B="${THREADS_4B:-4}"
THREADS_BATCH_4B="${THREADS_BATCH_4B:-8}"
PARALLEL_8B="${PARALLEL_8B:-1}"
PARALLEL_4B="${PARALLEL_4B:-1}"
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"

echo "============================================"
echo "     Starting Codex (Local)"
echo "============================================"

# Load environment variables from .env (overrides defaults above)
if [ -f "$CODEX_DIR/.env" ]; then
    set -a; source "$CODEX_DIR/.env"; set +a
fi

# Stop Docker containers if running (prevent port conflicts)
if docker compose -f "$CODEX_DIR/docker-compose.yaml" ps -q 2>/dev/null | grep -q .; then
    echo "Stopping Docker containers first..."
    docker compose -f "$CODEX_DIR/docker-compose.yaml" down
fi

# Kill existing processes on our ports
for port in 8080 8081 8000 8501; do
    pid=$(lsof -t -i:"$port" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        kill -9 "$pid" 2>/dev/null || true
        echo "  Freed port $port (PID $pid)"
    fi
done
sleep 1

# 1. PostgreSQL
echo "[1/8] PostgreSQL..."
export PATH="$PG_BIN:$PATH"
if pg_isready -q 2>/dev/null; then
    echo "  Already running."
else
    pg_ctl -D "$PG_DATA" -l "$PG_DATA/logfile" start
    echo "  Started."
fi
for i in $(seq 1 15); do
    if pg_isready -q 2>/dev/null; then
        echo "  Accepting connections."
        break
    fi
    echo "  Waiting for PostgreSQL... (${i}s)"
    sleep 1
done

# 2. Neo4j
echo "[2/8] Neo4j..."
if curl -s http://localhost:7474 > /dev/null 2>&1; then
    echo "  Already running."
else
    echo "  Starting Neo4j..."
    "$NEO4J_HOME/bin/neo4j" stop || true
    sleep 1
    "$NEO4J_HOME/bin/neo4j" start || true
    sleep 3
    echo "  Started."
fi

# 3. pgAdmin4
echo "[3/8] pgAdmin4 (port 5050)..."
if curl -s -o /dev/null http://127.0.0.1:5050/; then
    echo "  Already running."
else
    nohup "$VENV_DIR/bin/pgadmin4" > /tmp/pgadmin4.log 2>&1 &
    PGADMIN_PID=$!
    echo "  PID: $PGADMIN_PID"
    for i in $(seq 1 20); do
        if curl -s -o /dev/null http://127.0.0.1:5050/; then
            echo "  Ready on http://127.0.0.1:5050"
            break
        fi
        sleep 1
    done
fi

# 4. Qwen3-8B
echo "[4/8] Qwen3-8B (port $LLM_8B_PORT, $GPU_LAYERS_8B GPU layers)..."
/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$QWEN3_8B_MODEL_PATH" \
    --port "$LLM_8B_PORT" \
    --n-gpu-layers "$GPU_LAYERS_8B" \
    --ctx-size "$CTX_SIZE_8B" \
    --flash-attn on \
    --reasoning-format deepseek \
    --threads "$THREADS_8B" \
    --threads-batch "$THREADS_BATCH_8B" \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel "$PARALLEL_8B" \
    --log-disable &
REASONER_PID=$!
echo "  PID: $REASONER_PID"
sleep 2

# 5. Qwen3-4B
echo "[5/8] Qwen3-4B (port $LLM_4B_PORT, $GPU_LAYERS_4B GPU layers)..."
/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$QWEN3_4B_MODEL_PATH" \
    --port "$LLM_4B_PORT" \
    --n-gpu-layers "$GPU_LAYERS_4B" \
    --ctx-size "$CTX_SIZE_4B" \
    --flash-attn on \
    --reasoning off \
    --threads "$THREADS_4B" \
    --threads-batch "$THREADS_BATCH_4B" \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel "$PARALLEL_4B" \
    --log-disable &
Qwen_PID=$!
echo "  PID: $Qwen_PID"

# 6. FastAPI
echo "[6/8] FastAPI (port $API_PORT)..."
source "$VENV_DIR/bin/activate"
cd "$CODEX_DIR"
SLACK_SIGNING_SECRET="${SLACK_SIGNING_SECRET}" \
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN}" \
CODEX_API_URL="http://localhost:${API_PORT}" \
PYTHONPATH="${PYTHONPATH}" nohup python3 -m uvicorn services.api.main:app \
    --host "$API_HOST" --port "$API_PORT" > /tmp/fastapi.log 2>&1 &
FASTAPI_PID=$!
echo "  PID: $FASTAPI_PID"

# 7. Ingestion Agent
echo "[7/8] Ingestion Agent..."
source "$VENV_DIR/bin/activate"
cd "$CODEX_DIR"
PYTHONPATH="$CODEX_DIR" nohup python3 -m services.ingestion.ingestion_agent \
    > /tmp/ingestion_agent.log 2>&1 &
INGESTION_PID=$!
echo "  PID: $INGESTION_PID"

# 8. Streamlit UI
echo "[8/8] Streamlit UI (port $UI_PORT)..."
source "$VENV_DIR/bin/activate"
cd "$CODEX_DIR"
PYTHONPATH="$CODEX_DIR" nohup "$VENV_DIR/bin/streamlit" run services/chat/ui.py \
    --server.address "$API_HOST" \
    --server.port "$UI_PORT" \
    --server.headless true \
    > /tmp/streamlit.log 2>&1 &
UI_PID=$!
echo "  PID: $UI_PID"

echo ""
echo "============================================"
echo " All services started (Local)"
echo "============================================"
echo " PostgreSQL    :5432"
echo " Neo4j         :7474"
echo " Qwen3-8B      :$LLM_8B_PORT  PID $REASONER_PID"
echo " Qwen3-4B      :$LLM_4B_PORT  PID $Qwen_PID"
echo " FastAPI       :$API_PORT  PID $FASTAPI_PID"
echo " Streamlit UI  :8501  PID $UI_PID"
echo " Ingestion     :background  PID $INGESTION_PID"
echo ""
echo " Wait ~40s for models to load, then verify:"
echo "   curl http://localhost:8000/health"
echo "   http://localhost:8501"
echo ""
echo " Stop: bash infra/stop.sh"
echo "============================================"
