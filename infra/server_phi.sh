#!/bin/bash

# Phi-4-mini model for clause detection & classification (runs on port 8081, ~0.5 GB GPU)
MODEL_PATH="/home/mujtaba/models/Phi-4-mini-instruct-Q4_K_M.gguf"
PORT=8081
GPU_LAYERS=8  # 20% on GPU (~0.5 GB), keeps total VRAM under 7 GB alongside Qwen3-8B

echo "--------------------------------------------------------"
echo "Starting Codex Clause Detection Server (Phi-4-mini)..."
echo "Model: $MODEL_PATH"
echo "Port: $PORT"
echo "GPU Layers: $GPU_LAYERS"
echo "--------------------------------------------------------"

# Check if model exists before trying to launch
if [ ! -f "$MODEL_PATH" ]; then
    echo "❌ ERROR: Model file NOT found at $MODEL_PATH"
    exit 1
fi

# Launch llama-server
/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$MODEL_PATH" \
    --port $PORT \
    --n-gpu-layers $GPU_LAYERS \
    --ctx-size 16384 \
    --flash-attn on \
    --threads 4 \
    --threads-batch 8 \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel 1 \
    --log-disable
