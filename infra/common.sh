#!/usr/bin/env bash
# Shared portable configuration for Codex local scripts.

set -u

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_DIR="${CODEX_DIR:-$SCRIPT_ROOT}"
export CODEX_DIR

if [[ -f "$CODEX_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$CODEX_DIR/.env"
    set +a
fi

CODEX_DIR="${CODEX_DIR:-$SCRIPT_ROOT}"
if [[ "$CODEX_DIR" != /* ]]; then CODEX_DIR="$SCRIPT_ROOT/${CODEX_DIR#./}"; fi
VENV_DIR="${VENV_DIR:-$CODEX_DIR/.venv}"
if [[ "$VENV_DIR" != /* ]]; then VENV_DIR="$CODEX_DIR/${VENV_DIR#./}"; fi
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"
PG_BIN="${PG_BIN:-}"
if [[ -z "$PG_BIN" ]]; then
    for candidate in "$HOME/postgresql/bin" "$CODEX_DIR/.local/postgresql/bin"; do
        if [[ -x "$candidate/pg_isready" && -x "$candidate/pg_ctl" ]]; then
            PG_BIN="$candidate"
            break
        fi
    done
fi
if [[ -n "$PG_BIN" ]]; then
    export PATH="$PG_BIN:$PATH"
fi
PG_DATA="${PG_DATA:-$CODEX_DIR/.data/postgres}"
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-codex_admin}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-password123}"
POSTGRES_DB="${POSTGRES_DB:-codex_db}"
PG_ADMIN_USER="${PG_ADMIN_USER:-postgres}"
DATABASE_URL="${DATABASE_URL:-postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB}"
NEO4J_HOME="${NEO4J_HOME:-}"
if [[ -z "$NEO4J_HOME" && -x "$HOME/neo4j-community-5.26.0/bin/neo4j" ]]; then
    NEO4J_HOME="$HOME/neo4j-community-5.26.0"
fi
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASS="${NEO4J_PASS:-}"
if [[ -z "$NEO4J_PASS" && "${NEO4J_AUTH:-}" == neo4j/* ]]; then
    NEO4J_PASS="${NEO4J_AUTH#neo4j/}"
fi
MODELS_DIR="${MODELS_DIR:-$CODEX_DIR/models}"
if [[ "$MODELS_DIR" != /* ]]; then MODELS_DIR="$CODEX_DIR/${MODELS_DIR#./}"; fi
if [[ ! -d "$MODELS_DIR" && -d "$HOME/models" ]]; then MODELS_DIR="$HOME/models"; fi
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-}"
LLAMA_CPP_BIN="${LLAMA_CPP_BIN:-}"
if [[ -z "$LLAMA_CPP_BIN" && -n "$LLAMA_CPP_DIR" ]]; then
    LLAMA_CPP_BIN="$LLAMA_CPP_DIR/build/bin/llama-server"
fi
if [[ -z "$LLAMA_CPP_BIN" ]]; then
    LLAMA_CPP_BIN="$(command -v llama-server 2>/dev/null || true)"
fi
if [[ -z "$LLAMA_CPP_BIN" ]]; then
    for candidate in \
        "$HOME/llama.cpp/build/bin/llama-server" \
        "$CODEX_DIR/llama.cpp/build/bin/llama-server" \
        "/usr/local/bin/llama-server"; do
        if [[ -x "$candidate" ]]; then
            LLAMA_CPP_BIN="$candidate"
            break
        fi
    done
fi
LOG_DIR="${LOG_DIR:-$CODEX_DIR/logs}"
STATE_DIR="${STATE_DIR:-$CODEX_DIR/.state}"

API_HOST="${API_HOST:-127.0.0.1}"
LLM_PROVIDER="${LLM_PROVIDER:-auto}"
LLM_API_KEY="${LLM_API_KEY:-${OPENAI_API_KEY:-}}"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.openai.com/v1}"
LLM_MODEL="${LLM_MODEL:-gpt-4o-mini}"
LLM_INGESTION_MODEL="${LLM_INGESTION_MODEL:-$LLM_MODEL}"
if [[ "$LLM_PROVIDER" == "auto" ]]; then
    if [[ -n "$LLM_API_KEY" ]]; then LLM_PROVIDER="api"; else LLM_PROVIDER="local"; fi
fi
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"
LLM_8B_PORT="${LLM_8B_PORT:-8080}"
LLM_4B_PORT="${LLM_4B_PORT:-8081}"
NEO4J_HTTP_PORT="${NEO4J_HTTP_PORT:-7474}"
NEO4J_BOLT_PORT="${NEO4J_BOLT_PORT:-7687}"
PG_PORT="${PG_PORT:-5432}"
NEO4J_URI="${NEO4J_URI:-bolt://127.0.0.1:$NEO4J_BOLT_PORT}"

QWEN3_8B_MODEL_PATH="${QWEN3_8B_MODEL_PATH:-${MODEL_8B_PATH:-$MODELS_DIR/${QWEN3_8B_MODEL:-Qwen3-8B-Q4_K_M.gguf}}}"
QWEN3_4B_MODEL_PATH="${QWEN3_4B_MODEL_PATH:-${MODEL_4B_PATH:-$MODELS_DIR/${QWEN3_4B_MODEL:-Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf}}}"
if [[ "$QWEN3_8B_MODEL_PATH" == /models/* && ! -f "$QWEN3_8B_MODEL_PATH" ]]; then
    QWEN3_8B_MODEL_PATH="$MODELS_DIR/${QWEN3_8B_MODEL_PATH#/models/}"
fi
if [[ "$QWEN3_4B_MODEL_PATH" == /models/* && ! -f "$QWEN3_4B_MODEL_PATH" ]]; then
    QWEN3_4B_MODEL_PATH="$MODELS_DIR/${QWEN3_4B_MODEL_PATH#/models/}"
fi
LLAMA_8B_URL="${LLAMA_8B_URL:-http://127.0.0.1:$LLM_8B_PORT}"
LLAMA_4B_URL="${LLAMA_4B_URL:-http://127.0.0.1:$LLM_4B_PORT}"
CODEX_API_URL="${CODEX_API_URL:-http://127.0.0.1:$API_PORT}"

export CODEX_DIR VENV_DIR PYTHON_BIN PG_BIN PG_DATA POSTGRES_HOST POSTGRES_PORT POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB PG_ADMIN_USER DATABASE_URL NEO4J_HOME NEO4J_USER NEO4J_PASS MODELS_DIR LLAMA_CPP_DIR LLAMA_CPP_BIN
export LOG_DIR STATE_DIR API_HOST LLM_PROVIDER LLM_API_KEY LLM_BASE_URL LLM_MODEL LLM_INGESTION_MODEL
export API_PORT UI_PORT LLM_8B_PORT LLM_4B_PORT
export NEO4J_HTTP_PORT NEO4J_BOLT_PORT NEO4J_URI PG_PORT QWEN3_8B_MODEL_PATH QWEN3_4B_MODEL_PATH
export LLAMA_8B_URL LLAMA_4B_URL CODEX_API_URL

mkdir -p "$LOG_DIR" "$STATE_DIR"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

require_file() {
    [[ -f "$1" ]] || die "Required file not found: $1"
}

require_python() {
    require_file "$PYTHON_BIN"
    "$PYTHON_BIN" "$CODEX_DIR/infra/check_dependencies.py" --quiet || \
        die "Codex Python dependencies are missing or incompatible. Run: bash infra/setup_venv.sh"
}

python_module_env() {
    export PYTHONPATH="$CODEX_DIR:$CODEX_DIR/..${PYTHONPATH:+:$PYTHONPATH}"
}
