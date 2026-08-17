#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"
require_file "$LLAMA_CPP_BIN"
require_file "$QWEN3_4B_MODEL_PATH"
mkdir -p "$LOG_DIR"
exec "$LLAMA_CPP_BIN" -m "$QWEN3_4B_MODEL_PATH" --host 0.0.0.0 --port "$LLM_4B_PORT" \
    --n-gpu-layers "${GPU_LAYERS_4B:-36}" --ctx-size "${CTX_SIZE_4B:-32768}" \
    --batch-size "${BATCH_SIZE_4B:-2048}" --ubatch-size "${UBATCH_SIZE_4B:-512}" \
    --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --reasoning off \
    --parallel "${PARALLEL_4B:-1}" --threads "${THREADS_4B:-8}" \
    --threads-batch "${THREADS_BATCH_4B:-16}" --no-mmap --cache-ram 0 \
    >>"$LOG_DIR/qwen3_graph.log" 2>&1
