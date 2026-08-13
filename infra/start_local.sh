#!/bin/bash
# Start Codex locally (no Docker)
# PostgreSQL, Neo4j, Qwen3-8B, Qwen3-4B, FastAPI, Streamlit, Ingestion Worker

set -e

CODEX_DIR="/home/mujtaba/new_folder/codex"
VENV_DIR="/home/mujtaba/new_folder/fastmcp/venv"
PG_BIN="/home/mujtaba/postgresql/bin"
PG_DATA="/home/mujtaba/pgdata"
NEO4J_HOME="/home/mujtaba/neo4j-community-5.26.0"
QWEN3_8B_MODEL="/home/mujtaba/models/Qwen3-8B-Q4_K_M.gguf"
QWEN3_MODEL="/home/mujtaba/models/Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf"

echo "============================================"
echo "     Starting Codex (Local)"
echo "============================================"

# Load environment variables from .env
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

# 4. Qwen3-8B (port 8080, 30 GPU layers)
echo "[4/8] Qwen3-8B (port 8080, 30 GPU layers)..."
/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$QWEN3_8B_MODEL" \
    --port 8080 \
    --n-gpu-layers 30 \
    --ctx-size 16384 \
    --flash-attn on \
    --reasoning-format deepseek \
    --threads 8 \
    --threads-batch 16 \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel 1 \
    --log-disable &
REASONER_PID=$!
echo "  PID: $REASONER_PID"
sleep 2

# 5. Qwen3-4B (port 8081, 10 GPU layers)
echo "[5/8] Qwen3-4B (port 8081, 10 GPU layers)..."
/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$QWEN3_MODEL" \
    --port 8081 \
    --n-gpu-layers 10 \
    --ctx-size 16384 \
    --flash-attn on \
    --reasoning off \
    --threads 4 \
    --threads-batch 8 \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel 1 \
    --log-disable &
Qwen_PID=$!
echo "  PID: $Qwen_PID"

# 6. FastAPI (port 8000)
echo "[6/8] FastAPI (port 8000)..."
source "$VENV_DIR/bin/activate"
cd "$CODEX_DIR"
SLACK_SIGNING_SECRET="${SLACK_SIGNING_SECRET}" \
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN}" \
CODEX_API_URL="http://localhost:8000" \
PYTHONPATH="/home/mujtaba/new_folder" nohup python3 -m uvicorn services.api.main:app \
    --host 0.0.0.0 --port 8000 > /tmp/fastapi.log 2>&1 &
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

# 8. Streamlit UI (port 8501)
echo "[8/8] Streamlit UI (port 8501)..."
source "$VENV_DIR/bin/activate"
cd "$CODEX_DIR"
PYTHONPATH="$CODEX_DIR" nohup "$VENV_DIR/bin/streamlit" run services/chat/ui.py \
    --server.address 0.0.0.0 \
    --server.port 8501 \
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
echo " Qwen3-8B      :8080  PID $REASONER_PID"
echo " Qwen3-4B      :8081  PID $Qwen_PID"
echo " FastAPI       :8000  PID $FASTAPI_PID"
echo " Streamlit UI  :8501  PID $UI_PID"
echo " Ingestion     :background  PID $INGESTION_PID"
echo ""
echo " Wait ~40s for models to load, then verify:"
echo "   curl http://localhost:8000/health"
echo "   http://localhost:8501"
echo ""
echo " Stop: bash infra/stop.sh"
echo "============================================"
