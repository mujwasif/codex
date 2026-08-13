#!/bin/bash

# Absolute path to your model
MODEL_PATH="/home/mujtaba/models/Qwen3-8B-Q4_K_M.gguf"
PORT=8080
GPU_LAYERS=30 # 30/36 on GPU (~83%), keeps co-resident VRAM under 7GB with Qwen agent
# Thinking stays ON for QA answers; --reasoning-format deepseek moves thoughts to
# reasoning_content so message.content stays clean. Classifiers send
# chat_template_kwargs.enable_thinking=false per-request.

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
/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$MODEL_PATH" \
    --port $PORT \
    --n-gpu-layers $GPU_LAYERS \
    --ctx-size 16384 \
    --flash-attn on \
    --reasoning-format deepseek \
    --threads 8 \
    --threads-batch 16 \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel 1 \
    --log-disable
