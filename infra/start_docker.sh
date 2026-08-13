#!/bin/bash
# Start Codex via Docker
# All 6 containers: PostgreSQL, Neo4j, LLM Server (8B+4B), FastAPI, Streamlit, Ingestion Worker

set -e

CODEX_DIR="/home/mujtaba/new_folder/codex"
cd "$CODEX_DIR"

echo "============================================"
echo "     Starting Codex (Docker)"
echo "============================================"

# Kill local processes if running (prevent port conflicts)
for port in 8080 8081 8000 8501; do
    pid=$(lsof -t -i:"$port" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null || true
        echo "  Killed local process on :$port (PID $pid)"
    fi
done
sleep 1

echo "Starting containers..."
docker compose up -d

echo ""
echo "Waiting for API to be ready..."
for i in $(seq 1 120); do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "  API ready on :8000"
        break
    fi
    if [ "$i" -eq 120 ]; then
        echo "  API did not start in time. Check: docker compose logs api"
    fi
    sleep 1
done

echo ""
docker compose ps

echo ""
echo "============================================"
echo " All services started (Docker)"
echo "============================================"
echo " UI:            http://localhost:8501"
echo " API:           http://localhost:8000"
echo " Neo4j:         http://localhost:7474"
echo ""
echo " Logs:  docker compose logs -f"
echo " Stop:  bash infra/stop.sh"
echo "============================================"
