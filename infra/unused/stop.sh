#!/bin/bash
# Stop Codex (Docker or Local — stops whichever is running)

set -e

CODEX_DIR="/home/mujtaba/new_folder/codex"

echo "============================================"
echo "     Stopping Codex"
echo "============================================"

# 1. Stop Docker containers
if docker compose -f "$CODEX_DIR/docker-compose.yaml" ps -q 2>/dev/null | grep -q .; then
    echo "Stopping Docker containers..."
    docker compose -f "$CODEX_DIR/docker-compose.yaml" down
    echo "  Docker stopped."
else
    echo "  No Docker containers running."
fi

# 2. Kill local processes on Codex ports
STOPPED=0
for port in 8000 8080 8081 8501 5050; do
    pid=$(lsof -t -i:"$port" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null || true
        echo "  Killed process on :$port (PID $pid)"
        STOPPED=1
    fi
done

# 3. Kill known background processes
for name in "uvicorn" "streamlit" "ingestion_agent" "pgadmin4"; do
    pkill -f "$name" 2>/dev/null || true
done

if [ "$STOPPED" -eq 0 ]; then
    echo "  No local processes running."
fi

echo ""
echo "============================================"
echo " All Codex services stopped"
echo "============================================"
