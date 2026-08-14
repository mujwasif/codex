#!/bin/bash

# Qwen3-4B-Instruct model for clause detection, classification & graph generation
# (always-on agent, runs on port 8081, 10/36 layers on GPU)
MODEL_PATH="/home/mujtaba/models/Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf"
PORT=8081
GPU_LAYERS=10

echo "--------------------------------------------------------"
echo "Starting Codex Clause Detection & Graph Server (Qwen3-4B)..."
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
    --reasoning off \
    --threads 4 \
    --threads-batch 8 \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel 1 \
    --cache-ram 0 \
    >> /tmp/qwen3_agent.log 2>&1
