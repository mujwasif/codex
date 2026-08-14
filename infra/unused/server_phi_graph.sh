#!/bin/bash

# Phi-4-mini High-Performance Graph Generation Server
# Optimized for prompt prefill speed and VRAM efficiency
MODEL_PATH="/home/mujtaba/models/Phi-4-mini-instruct-Q4_K_M.gguf"
PORT=8081
GPU_LAYERS=33          # All 33 layers on GPU
CTX_SIZE=16384         # 16k context window (reduces KV cache overhead)
BATCH_SIZE=2048        # Evaluates large prompts in 1 pass instead of 4+
UBATCH_SIZE=512        # Physical micro-batch size for GPU execution

echo "========================================================"
echo "  Phi-4-mini Graph Generation Server (OPTIMIZED)"
echo "========================================================"
echo "Model:     $MODEL_PATH"
echo "Port:      $PORT"
echo "GPU:       $GPU_LAYERS layers (full offload)"
echo "Context:   $CTX_SIZE tokens"
echo "Batch:     $BATCH_SIZE (Logical) / $UBATCH_SIZE (Physical)"
echo "Features:  Flash Attention + q8_0 KV Cache"
echo "========================================================"

if [ ! -f "$MODEL_PATH" ]; then
    echo "ERROR: Model not found at $MODEL_PATH"
    exit 1
fi

/home/mujtaba/llama.cpp/build/bin/llama-server \
    -m "$MODEL_PATH" \
    --port $PORT \
    --n-gpu-layers $GPU_LAYERS \
    --ctx-size $CTX_SIZE \
    --batch-size $BATCH_SIZE \
    --ubatch-size $UBATCH_SIZE \
    --flash-attn on \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --parallel 1 \
    --log-disable