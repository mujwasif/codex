#!/bin/bash

# Start both llama.cpp servers in background
# Qwen3-8B (port 8080) — query reasoning
# Qwen3-4B (port 8081) — clause detection, classification, graph generation

echo "=== Starting Codex LLM Servers ==="
echo ""

# Start Qwen3-4B on port 8081
echo "Starting Qwen3-4B (port 8081)..."
./infra/server_qwen3.sh &
QWEN_PID=$!
echo "  PID: $QWEN_PID"

# Small delay to let Qwen start before Qwen3-8B (avoid simultaneous GPU init)
sleep 2

# Start Qwen3-8B on port 8080
echo "Starting Qwen3-8B (port 8080)..."
./infra/server.sh &
DS_PID=$!
echo "  PID: $DS_PID"

echo ""
echo "=== Both servers starting in background ==="
echo "Qwen3-8B (port 8080) — PID: $DS_PID"
echo "Qwen3-4B  (port 8081) — PID: $QWEN_PID"
echo ""
echo "Wait ~30s for models to load, then check:"
echo "  curl http://localhost:8080/health"
echo "  curl http://localhost:8081/health"
