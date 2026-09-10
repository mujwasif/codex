#!/bin/bash
# Start local LLM server (Qwen3-8B Q4_K_M) on port 8080
# This is the primary endpoint for Codex.

MODEL="/home/mujtaba/models/Qwen3-8B-Q4_K_M.gguf"
SERVER="/home/mujtaba/llama.cpp/build/bin/llama-server"
PORT=8080
CTX=20480
GPU_LAYERS=33

if ! [ -f "$MODEL" ]; then
    echo "Error: Model not found at $MODEL"
    exit 1
fi

if ! [ -f "$SERVER" ]; then
    echo "Error: llama-server not found at $SERVER"
    exit 1
fi

echo "Starting Qwen3-8B on http://localhost:$PORT (ctx=$CTX, gpu_layers=$GPU_LAYERS, flash_attn=on, kv_cache=q8)..."
exec "$SERVER" \
    -m "$MODEL" \
    --host 0.0.0.0 \
    --port $PORT \
    --n_gpu_layers $GPU_LAYERS \
    --ctx-size $CTX \
    --flash-attn on \
    --cache-type-k q8_0 \
    --cache-type-v q8_0
