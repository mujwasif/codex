#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_command initdb
require_command pg_ctl
require_command psql
require_command createdb
mkdir -p "$PG_DATA"

if [[ ! -f "$PG_DATA/PG_VERSION" ]]; then
    initdb -D "$PG_DATA" --auth-local=trust --auth-host=scram-sha-256
fi
if ! pg_isready -p "$PG_PORT" -q; then
    pg_ctl -D "$PG_DATA" -l "$LOG_DIR/postgres.log" start
fi

if ! psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$POSTGRES_USER'" | grep -q 1; then
    psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -d postgres -c "CREATE USER \"$POSTGRES_USER\" WITH PASSWORD '$POSTGRES_PASSWORD';"
else
    psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -d postgres -c "ALTER USER \"$POSTGRES_USER\" WITH PASSWORD '$POSTGRES_PASSWORD';"
fi
if ! psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" | grep -q 1; then
    createdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -O "$POSTGRES_USER" "$POSTGRES_DB"
fi
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$CODEX_DIR/infra/db/init.sql"
echo "PostgreSQL and pgvector are ready: $POSTGRES_DB"
