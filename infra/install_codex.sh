#!/usr/bin/env bash
# Complete native Codex installer for Ubuntu/Debian.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODEX_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$CODEX_DIR/.env"
LLM_MODE="auto"
CUDA="false"

usage() {
    cat <<'EOF'
Usage: bash infra/install_codex.sh [--llm api|local|auto] [--cuda]

Options:
  --llm MODE   Select hosted API, local llama.cpp, or automatic mode.
  --cuda       Install the CUDA PyTorch variant and use CUDA local defaults.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --llm) LLM_MODE="${2:?Missing value for --llm}"; shift 2 ;;
        --cuda) CUDA="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

case "$LLM_MODE" in api|local|auto) ;; *) echo "Invalid LLM mode: $LLM_MODE" >&2; exit 2 ;; esac

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '\n==> %s\n' "$*"; }
need_command() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

[[ -f /etc/os-release ]] || die "Cannot detect operating system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" || "${ID_LIKE:-}" == *debian* ]] || \
    die "This installer supports Ubuntu/Debian Linux only"
[[ "$(uname -m)" == "x86_64" || "$(uname -m)" == "aarch64" ]] || \
    die "Unsupported architecture: $(uname -m)"
need_command sudo
need_command curl
need_command git
[[ "$(df -Pk "$CODEX_DIR" | awk 'NR==2 {print $4}')" -ge 10485760 ]] || \
    die "At least 10 GB of free disk space is required"

if [[ ! -f "$ENV_FILE" ]]; then
    cp "$CODEX_DIR/.env.example" "$ENV_FILE"
    echo "Created $ENV_FILE from .env.example"
else
    echo "Keeping existing $ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

set_env() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v value="$value" '
        BEGIN { replaced = 0 }
        $0 ~ "^" key "=" { print key "=" value; replaced = 1; next }
        { print }
        END { if (!replaced) print key "=" value }
    ' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
}

prompt_value() {
    local label="$1" default="${2:-}" value
    if [[ -n "$default" ]]; then
        read -r -p "$label [$default]: " value || true
        printf '%s' "${value:-$default}"
    else
        read -r -p "$label: " value || true
        printf '%s' "$value"
    fi
}

prompt_secret() {
    local label="$1" value
    read -r -s -p "$label: " value || true
    printf '\n' >&2
    printf '%s' "$value"
}

info "Installing operating-system packages"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl git gnupg build-essential cmake pkg-config \
    python3 python3-venv python3-dev python3-pip \
    postgresql postgresql-contrib libpq-dev

need_command python3
need_command psql
need_command pg_config
PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required")
PY

sudo systemctl enable --now postgresql

info "Installing pgvector"
if ! sudo -u postgres psql -d postgres -tAc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" 2>/dev/null | grep -q 1; then
    tmp_pgvector="$(mktemp -d)"
    trap 'rm -rf "$tmp_pgvector"' EXIT
    git clone --depth 1 https://github.com/pgvector/pgvector.git "$tmp_pgvector/pgvector"
    make -C "$tmp_pgvector/pgvector"
    sudo make -C "$tmp_pgvector/pgvector" install
fi
sudo -u postgres psql -d postgres -tAc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" 2>/dev/null | grep -q 1 || \
    die "pgvector installation could not be verified"

info "Configuring environment values"
existing_user="${POSTGRES_USER:-}"
existing_db="${POSTGRES_DB:-}"
existing_pg_password="${POSTGRES_PASSWORD:-}"
existing_neo_password="${NEO4J_PASS:-}"
db_user="$(prompt_value 'PostgreSQL user' "${existing_user:-codex_admin}")"
db_name="$(prompt_value 'PostgreSQL database' "${existing_db:-codex_db}")"
db_password="$existing_pg_password"
[[ -n "$db_password" && "$db_password" != change-this-password ]] || \
    db_password="$(prompt_secret 'PostgreSQL password')"
neo_password="$existing_neo_password"
[[ -n "$neo_password" && "$neo_password" != change-this-password ]] || \
    neo_password="$(prompt_secret 'Neo4j password')"
[[ -n "$db_password" ]] || die "PostgreSQL password cannot be empty"
[[ -n "$neo_password" ]] || die "Neo4j password cannot be empty"
secret_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

set_env LLM_PROVIDER "$LLM_MODE"
set_env POSTGRES_USER "$db_user"
set_env POSTGRES_PASSWORD "$db_password"
set_env POSTGRES_DB "$db_name"
set_env POSTGRES_HOST "127.0.0.1"
set_env POSTGRES_PORT "5432"
set_env DATABASE_URL "postgresql://${db_user}:${db_password}@127.0.0.1:5432/${db_name}"
set_env NEO4J_USER "neo4j"
set_env NEO4J_PASS "$neo_password"
set_env NEO4J_AUTH "neo4j/$neo_password"
set_env NEO4J_URI "bolt://127.0.0.1:7687"
set_env SECRET_KEY "$secret_key"
set_env CODEX_DIR "$CODEX_DIR"
set_env VENV_DIR "$CODEX_DIR/.venv"
set_env MODELS_DIR "$CODEX_DIR/models"
set_env ARCHIVE_DIR "$CODEX_DIR/archive"
set_env DATA_DIR "$CODEX_DIR/.data"
set_env LOG_DIR "$CODEX_DIR/logs"
set_env STATE_DIR "$CODEX_DIR/.state"

if [[ "$LLM_MODE" == api || "$LLM_MODE" == auto ]]; then
    api_key="${LLM_API_KEY:-}"
    if [[ -z "$api_key" ]]; then
        read -r -s -p "Hosted LLM API key (leave empty for local fallback): " api_key || true
        printf '\n' >&2
    fi
    if [[ -n "$api_key" ]]; then
        LLM_API_KEY="$api_key"
        export LLM_API_KEY
        set_env LLM_API_KEY "$api_key"
    fi
fi

if [[ "$LLM_MODE" == local || ("$LLM_MODE" == auto && -z "${LLM_API_KEY:-}") ]]; then
    set_env LLM_PROVIDER "$LLM_MODE"
    llama_dir="${LLAMA_CPP_DIR:-$CODEX_DIR/.local/llama.cpp}"
    if [[ ! -x "$llama_dir/build/bin/llama-server" ]]; then
        info "Building llama.cpp"
        mkdir -p "$(dirname "$llama_dir")"
        [[ -d "$llama_dir/.git" ]] || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$llama_dir"
        cmake -S "$llama_dir" -B "$llama_dir/build" -DGGML_NATIVE=ON -DGGML_CUDA="$CUDA"
        cmake --build "$llama_dir/build" --config Release -j"$(nproc)"
    fi
    set_env LLAMA_CPP_DIR "$llama_dir"
    set_env LLAMA_CPP_BIN "$llama_dir/build/bin/llama-server"
    mkdir -p "$CODEX_DIR/models"
    for spec in \
        "QWEN3_8B_MODEL_PATH|Qwen3-8B-Q4_K_M.gguf|${QWEN3_8B_MODEL_URL:-}" \
        "QWEN3_4B_MODEL_PATH|Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf|${QWEN3_4B_MODEL_URL:-}"; do
        IFS='|' read -r key filename url <<< "$spec"
        path="$CODEX_DIR/models/$filename"
        if [[ ! -f "$path" ]]; then
            if [[ -z "$url" ]]; then
                read -r -p "Download URL for $filename (leave empty to abort): " url || true
            fi
            [[ -n "$url" ]] || die "Set ${key%_PATH}_URL in .env to download $filename"
            curl -fL --retry 3 "$url" -o "$path"
            set_env "${key%_PATH}_URL" "$url"
        fi
        set_env "$key" "$path"
    done
fi

info "Installing Python dependencies"
if [[ "$CUDA" == true ]]; then
    CODEX_CUDA=true bash "$SCRIPT_DIR/setup_venv.sh"
else
    bash "$SCRIPT_DIR/setup_venv.sh"
fi

info "Installing and configuring Neo4j"
if ! command -v neo4j >/dev/null 2>&1 && [[ ! -x "${NEO4J_HOME:-}/bin/neo4j" ]]; then
    curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key | \
        sudo gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
    echo 'deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable 5' | \
        sudo tee /etc/apt/sources.list.d/neo4j.list >/dev/null
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y neo4j
fi
if command -v neo4j >/dev/null 2>&1; then
    sudo neo4j-admin dbms set-initial-password "$neo_password" 2>/dev/null || true
    sudo systemctl enable neo4j
    sudo systemctl restart neo4j
fi

info "Initializing PostgreSQL and schema"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
source "$SCRIPT_DIR/common.sh"
bash "$SCRIPT_DIR/bootstrap_local.sh"

mkdir -p "$ARCHIVE_DIR" "$LOG_DIR" "$DATA_DIR" "$STATE_DIR"

info "Running final health checks"
"$PYTHON_BIN" "$CODEX_DIR/infra/check_dependencies.py"
"$PYTHON_BIN" -m pip check
psql "$DATABASE_URL" -tAc "SELECT 1" | grep -q 1 || die "PostgreSQL health check failed"
psql "$DATABASE_URL" -tAc "SELECT 1 FROM pg_extension WHERE extname='vector'" | grep -q 1 || die "pgvector health check failed"
psql "$DATABASE_URL" -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='chunks'" | grep -q 1 || die "Codex schema health check failed"
psql "$DATABASE_URL" -tAc "SELECT udt_name FROM information_schema.columns WHERE table_name='chunks' AND column_name='embedding'" | grep -q vector || die "chunks.embedding is not vector(1024)"
"$PYTHON_BIN" -c 'import services.api.main; print("FastAPI import: OK")'

for attempt in $(seq 1 30); do
    if "$PYTHON_BIN" - <<'PY'
from neo4j import GraphDatabase
from packages.shared.config import NEO4J_PASS, NEO4J_URI, NEO4J_USER
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
try:
    driver.verify_connectivity()
finally:
    driver.close()
print("Neo4j connectivity: OK")
PY
    then break; fi
    [[ "$attempt" -eq 30 ]] && die "Neo4j connectivity check failed"
    sleep 2
done

if [[ "$LLM_PROVIDER" == api ]]; then
    [[ -n "${LLM_API_KEY:-}" ]] || die "API mode selected but LLM_API_KEY is empty"
    curl -fsS -H "Authorization: Bearer $LLM_API_KEY" "${LLM_BASE_URL:-https://api.openai.com/v1}/models" >/dev/null || \
        die "Hosted LLM API health check failed"
else
    "$LLAMA_CPP_BIN" --version >/dev/null 2>&1 || die "llama-server could not be executed"
    curl -fsS "${LLAMA_8B_URL:-http://127.0.0.1:8080}/health" >/dev/null 2>&1 || \
        echo "WARNING: local llama.cpp is installed but not running yet"
fi

cat <<EOF

Codex installation completed successfully.

Activate the environment:
  source $CODEX_DIR/.venv/bin/activate

Validate dependencies:
  bash $CODEX_DIR/infra/check_dependencies.py

Start Codex:
  bash $CODEX_DIR/infra/start_codex.sh
EOF
