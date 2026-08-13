#!/bin/bash
# Codex Deployment Master Script
# Handles the distinction between "Maintenance Mode" (Batch Build) and "Serving Mode" (Live API).

set -e

CODEX_DIR="/home/mujtaba/new_folder/codex"
cd "$CODEX_DIR"

# Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "============================================"
echo "  Codex Deployment Manager"
echo "============================================"
echo "1) Start Live Service (API + UI + Worker)"
echo "2) Maintenance Mode: Full Reset & Graph Build"
echo "3) Stop Everything"
echo "============================================"
read -p "Select an option [1-3]: " OPT

case $OPT in
    1)
        echo "🚀 Starting Live Service..."
        docker compose up -d
        echo "Service is running. Access UI at http://localhost:8501"
        ;;
    2)
        echo "🛠️  Entering Maintenance Mode..."
        # Start only the infrastructure needed for ingestion
        docker compose up -d db graph llm-server
        
        echo "Step 1: Running reset_reingest.sh..."
        # Execute script inside the worker container to ensure env consistency
        docker compose exec -T worker bash /app/infra/reset_and_reingest.sh
        
        echo "Step 2: Running run_graph_migration.sh..."
        docker compose exec -T worker bash /app/infra/run_graph_migration.sh
        
        echo "Maintenance complete. Starting API and UI..."
        docker compose up -d api ui worker
        ;;
    3)
        echo "🛑 Stopping all services..."
        docker compose down
        ;;
    *)
        echo "Invalid option."
        ;;
esac
