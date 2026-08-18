#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"
for port in "$API_PORT" "$UI_PORT"; do
    pids="$(lsof -t -i:"$port" 2>/dev/null || true)"
    [[ -z "$pids" ]] || kill $pids 2>/dev/null || true
done
echo "Codex native services stopped."
