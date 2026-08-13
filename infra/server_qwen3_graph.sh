#!/bin/bash

# Qwen3-4B-Instruct High-Performance Graph Generation Server
# Optimized for prompt prefill speed and VRAM efficiency (batch migration use)
MODEL_PATH="/home/mujtaba/models/Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf"
PORT=8081
GPU_LAYERS=36          # All 36 layers on GPU
CTX_SIZE=32768         # 32k context (Qwen3-4B native) - enables 40-chunk batches
BATCH_SIZE=2048        # Evaluates large prompts in fewer passes
UBATCH_SIZE=512        # Physical micro-batch size for GPU execution

echo "========================================================"
echo "  Qwen3-4B Graph Generation Server (OPTIMIZED)"
echo "========================================================"
echo "Model:     $MODEL_PATH"
echo "Port:      $PORT"
echo "GPU:       $GPU_LAYERS layers (full offload)"
echo "Context:   $CTX_SIZE tokens"
echo "Batch:     $BATCH_SIZE (Logical) / $UBATCH_SIZE (Physical)"
echo "Features:  Flash Attention + q8_0 KV Cache + thinking OFF (prompt cache disabled)"
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
    --reasoning off \
    --parallel 1 \
    --threads 8 \
    --threads-batch 16 \
    --no-mmap \
    --cache-ram 0 \
    >> /tmp/qwen3_graph.log 2>&1
