#!/bin/bash
# Autonomous Reset & Re-ingest Codex Data
# Clears VRAM, starts the full-config Qwen server, truncates data, and runs fresh ingestion.

set -e

# --- Configuration ---
CODEX_DIR="/home/mujtaba/new_folder/codex"
VENV_DIR="/home/mujtaba/new_folder/fastmcp/venv"
PG_BIN="/home/mujtaba/postgresql/bin"
PG_DATA="/home/mujtaba/pgdata"
NEO4J_HOME="/home/mujtaba/neo4j-community-5.26.0"

export PATH="$PG_BIN:$PATH"

echo "============================================"
echo "    Codex Autonomous Reset & Re-ingestion"
echo "============================================"

# 0. Clear VRAM: kill ALL llama-server instances (full-config Qwen runs alone)
echo "[1/7] Clearing VRAM: Killing all llama-server instances..."
PIDS=$(lsof -t -i:8080,8081 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    kill -9 $PIDS 2>/dev/null
    echo "  Killed PIDS: $PIDS"
    sleep 2
else
    echo "  No LLM servers running."
fi

# 1. Ensure Core Servers are Running
echo "[2/7] Checking/Starting Core Servers..."

# --- PostgreSQL ---
if ! pg_isready -q; then
    echo "  PostgreSQL offline. Starting..."
    pg_ctl -D "$PG_DATA" -l "$PG_DATA/logfile" start || true
    sleep 2
else
    echo "  PostgreSQL is already running."
fi

# --- Neo4j ---
if ! curl -s http://localhost:7474 > /dev/null; then
    echo "  Neo4j offline. Starting..."
    "$NEO4J_HOME/bin/neo4j" start || true
    sleep 5
else
    echo "  Neo4j is already running."
fi

# --- Qwen2.5-3B (Port 8081, FULL GPU OFFLOAD for batch ingestion) ---
echo "  Starting Qwen2.5-3B (8081) in full configuration..."
cd "$CODEX_DIR"
bash infra/server_qwen3_graph.sh &
QWEN_PID=$!
echo "  Qwen PID: $QWEN_PID"
sleep 8
for i in {1..15}; do
    curl -s http://localhost:8081/health > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo "  Qwen ready after ~${i}s"
        break
    fi
    if [ $i -eq 15 ]; then
        echo "  ERROR: Qwen failed to start within timeout"
        kill $QWEN_PID 2>/dev/null
        exit 1
    fi
    sleep 2
done

# 2. Data Purge
echo "[3/7] Ensuring PostgreSQL schema is up-to-date..."
psql -U codex_admin -d codex_db -c "SELECT 1" > /dev/null 2>&1 || { echo "  ERROR: Cannot reach PostgreSQL. Aborting before destructive purge."; exit 1; }
psql -U codex_admin -d codex_db -c "ALTER TABLE chunks ALTER COLUMN clause_ref TYPE TEXT;" 2>/dev/null || true
psql -U codex_admin -d codex_db -c "ALTER TABLE citations ALTER COLUMN clause_ref TYPE TEXT;" 2>/dev/null || true

echo "  Truncating PostgreSQL tables..."
psql -U codex_admin -d codex_db -c "TRUNCATE TABLE documents, chunks, entities, queries, answers, citations, feedback, audit_log RESTART IDENTITY CASCADE;"
echo "  SQL data wiped."

echo "[4/7] Purging Neo4j knowledge graph..."
cypher-shell -u neo4j -p "${NEO4J_PASS:-securepassword123}" "MATCH (n) DETACH DELETE n" || echo "  Neo4j purge failed (check password)"
echo "  Graph wiped."

echo "[5/7] Resetting state files..."
rm -f "$CODEX_DIR/processed_chunks.txt" "$CODEX_DIR/processed_docs.txt"
echo "  State files deleted."

# 3. Fresh Ingestion
echo "[6/7] Running fresh ingestion from archive/..."
source "$VENV_DIR/bin/activate"
cd "$CODEX_DIR"
PYTHONPATH="$CODEX_DIR" python3 services/ingestion/ingest.py

# --- FastAPI (Port 8000) ---
# Start AFTER ingestion so the BM25 index is built from the freshly-ingested DB.
echo "  Starting FastAPI (8000)..."
if ! curl -s http://localhost:8000/health > /dev/null; then
    source "$VENV_DIR/bin/activate"
    cd "$CODEX_DIR"
    PYTHONPATH="/home/mujtaba/new_folder" nohup python3 -m uvicorn services.api.main:app \
        --host 0.0.0.0 --port 8000 > /tmp/fastapi.log 2>&1 &
    sleep 5
else
    echo "  FastAPI (8000) is already running."
fi

echo ""
echo "[7/7] FINAL VERIFICATION"
echo "============================================"
DOC_COUNT=$(psql -U codex_admin -d codex_db -t -c "SELECT count(*) FROM documents;")
CHUNK_COUNT=$(psql -U codex_admin -d codex_db -t -c "SELECT count(*) FROM chunks;")

echo " Documents ingested: ${DOC_COUNT//[[:space:]]/}"
echo " Chunks generated : ${CHUNK_COUNT//[[:space:]]/}"
echo "============================================"
echo "Reset and re-ingestion complete."
echo "Qwen (8081) left running in full config. Run run_graph_migration.sh next, or"
echo "kill it and start start_all.sh for the always-on (Qwen3-8B + Qwen3-4B) setup."
