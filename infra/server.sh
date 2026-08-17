#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"
require_file "$LLAMA_CPP_BIN"
require_file "$QWEN3_8B_MODEL_PATH"
mkdir -p "$LOG_DIR"
exec "$LLAMA_CPP_BIN" -m "$QWEN3_8B_MODEL_PATH" --host 0.0.0.0 --port "$LLM_8B_PORT" \
    --n-gpu-layers "${GPU_LAYERS_8B:-30}" --ctx-size "${CTX_SIZE_8B:-16384}" \
    --flash-attn on --reasoning-format deepseek --threads "${THREADS_8B:-8}" \
    --threads-batch "${THREADS_BATCH_8B:-16}" --parallel "${PARALLEL_8B:-1}" \
    --log-disable
