#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODEX_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${VENV_DIR:-$CODEX_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

[[ -n "$PYTHON_BIN" ]] || { echo "ERROR: python3 is required" >&2; exit 1; }
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Codex requires Python 3.10 or newer")
PY

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Creating Codex virtualenv: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
"$VENV_PYTHON" -m pip install -r "$CODEX_DIR/requirements.txt"
SITE_PACKAGES="$($VENV_PYTHON -c 'import site; print(site.getsitepackages()[0])')"
printf '%s\n%s\n' "$CODEX_DIR" "$(dirname "$CODEX_DIR")" > "$SITE_PACKAGES/codex_local.pth"
"$VENV_PYTHON" "$CODEX_DIR/infra/check_dependencies.py"

echo "Codex virtualenv ready: $VENV_DIR"
echo "Activate with: source $VENV_DIR/bin/activate"
