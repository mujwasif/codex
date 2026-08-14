#!/bin/bash

# Full graph migration: stops ALL servers, starts Qwen concurrently with PG/Neo4j setup,
# runs Qwen at stabilized max, migrates, restores

CODEX_DIR="/home/mujtaba/new_folder/codex"
VENV_DIR="/home/mujtaba/new_folder/fastmcp/venv"
PG_BIN="/home/mujtaba/postgresql/bin"
PG_DATA="/home/mujtaba/pgdata"
NEO4J_HOME="/home/mujtaba/neo4j-community-5.26.0"
NEO4J_PASS="securepassword123"

echo "========================================================"
echo "       Codex Graph Migration Pipeline"
echo "========================================================"

# Step 1: Kill ALL llama-server processes to clear VRAM
echo ""
echo "[1/10] Clearing VRAM: Killing all llama-server instances..."
PIDS=$(lsof -t -i:8080,8081 2>/dev/null)
if [ -n "$PIDS" ]; then
    kill -9 $PIDS 2>/dev/null
    echo "  Killed PIDS: $PIDS"
    sleep 2
else
    echo "  No LLM servers running."
fi

# Step 2: Verify GPU is idle before loading the full-offload model
echo ""
echo "[2/10] Verifying GPU baseline..."
GPU_MEM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n1)
echo "  GPU memory used: ${GPU_MEM:-unknown} MiB"
if [ -n "$GPU_MEM" ] && [ "$GPU_MEM" -gt 1024 ]; then
    echo "  WARNING: GPU memory is not idle (>1GiB used). Another process may be resident;"
    echo "           the full-offload Qwen could OOM during migration."
fi

# Step 3: Start Qwen immediately in background (loads while PG/Neo4j are set up)
echo ""
echo "[3/10] Starting Qwen3-4B (concurrent with PG/Neo4j setup)..."
cd "$CODEX_DIR"
bash infra/server_qwen3_graph.sh &
QWEN_PID=$!
trap 'kill $QWEN_PID 2>/dev/null' EXIT
echo "  Qwen PID: $QWEN_PID"

# Step 4: Ensure PostgreSQL is running
echo ""
echo "[4/10] Checking PostgreSQL status..."
if ! pg_isready -q 2>/dev/null; then
    echo "  PostgreSQL is not running. Attempting to start..."
    export PATH="$PG_BIN:$PATH"
    pg_ctl -D "$PG_DATA" -l "$PG_DATA/logfile" start
    sleep 2
    if ! pg_isready -q; then
        echo "  ERROR: PostgreSQL failed to start. Please start it manually."
        exit 1
    fi
    echo "  PostgreSQL started successfully."
else
    echo "  PostgreSQL is already running."
fi

# Step 5: Ensure Neo4j is running
echo ""
echo "[5/10] Checking Neo4j status..."

# Test Bolt directly — if it responds, Neo4j is truly ready
if "$NEO4J_HOME/bin/cypher-shell" -u neo4j -p "$NEO4J_PASS" "RETURN 1;" > /dev/null 2>&1; then
    echo "  Neo4j is already running."
else
    echo "  Starting Neo4j fresh..."
    "$NEO4J_HOME/bin/neo4j" stop || true
    sleep 2
    "$NEO4J_HOME/bin/neo4j" start || true
fi

# Wait for Neo4j Bolt port 7687 to be ready
echo "  Waiting for Neo4j Bolt port 7687..."
for i in $(seq 1 30); do
    if "$NEO4J_HOME/bin/cypher-shell" -u neo4j -p "$NEO4J_PASS" "RETURN 1;" > /dev/null 2>&1; then
        echo "  Neo4j Bolt ready after ~${i}s"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: Neo4j Bolt failed to start within timeout"
        exit 1
    fi
    sleep 2
done

# Step 6: CLEAR EXISTING GRAPH
echo ""
echo "[6/10] Wiping existing graph for clean LLM generation..."
"$NEO4J_HOME/bin/cypher-shell" -u neo4j -p "$NEO4J_PASS" "MATCH (n) DETACH DELETE n;"
echo "  Graph cleared."

# Step 7: Wait for Qwen to load (health polling, no fixed sleep)
echo ""
echo "[7/10] Waiting for Qwen to load..."
for i in $(seq 1 60); do
    RESP=$(curl -s -m 2 http://localhost:8081/health 2>/dev/null)
    if echo "$RESP" | grep -q '"ok"'; then
        echo "  Qwen ready after ~${i}x2s"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  ERROR: Qwen failed to start within timeout"
        exit 1
    fi
    sleep 2
done

# Step 8: Run migration
echo ""
echo "[8/10] Running LLM graph migration..."
source "$VENV_DIR/bin/activate"
PYTHONPATH="$CODEX_DIR" python3 services/ingestion/migrate_to_neo4j.py
MIGRATE_EXIT=$?
deactivate

if [ $MIGRATE_EXIT -ne 0 ]; then
    echo "  WARNING: Migration exited with code $MIGRATE_EXIT"
fi

# Step 9: Stop Qwen
echo ""
echo "[9/10] Stopping Qwen server..."
kill $QWEN_PID 2>/dev/null
sleep 1
echo "  Qwen stopped."

# Step 10: Restart Qwen3-8B reasoner
echo ""
echo "[10/10] Restarting Qwen3-8B..."
cd "$CODEX_DIR"
nohup bash infra/server.sh > /dev/null 2>&1 &
echo "  Qwen3-8B starting on port 8080 (wait ~30s)"

echo ""
echo "========================================================"
echo "  Migration complete!"
echo "  Qwen3-8B is restarting in background."
echo "========================================================"
