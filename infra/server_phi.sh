#!/bin/bash

# Phi-4-mini model for clause detection (faster than DeepSeek 8B)
MODEL_PATH="/home/mujtaba/models/Phi-4-mini-instruct-Q4_K_M.gguf"
PORT=8080
GPU_LAYERS=33  # All layers to GPU for speed

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
    --ctx-size 4096 \
    --parallel 1 \
    --log-disable
