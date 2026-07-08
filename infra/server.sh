#!/bin/bash

# Absolute path to your model
MODEL_PATH="/home/mujtaba/models/DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf"
PORT=8080
GPU_LAYERS=33 # Optimized for RTX 4060 (8GB)

echo "--------------------------------------------------------"
echo "Starting Codex Reasoning Server (llama.cpp)..."
echo "Model: $MODEL_PATH"
echo "Port: $PORT"
echo "GPU Layers: $GPU_LAYERS"
echo "--------------------------------------------------------"

# Check if model exists before trying to launch
if [ ! -f "$MODEL_PATH" ]; then
    echo "❌ ERROR: Model file NOT found at $MODEL_PATH"
    echo "Please verify the filename in /home/mujtaba/models/"
    exit 1
fi

# Launch llama-server
# Using --ctx-size 8192 to handle multiple policy chunks
/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$MODEL_PATH" \
    --port $PORT \
    --n-gpu-layers $GPU_LAYERS \
    --ctx-size 8192 \
    --parallel 1 \
    --log-disable
