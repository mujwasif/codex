#!/usr/bin/env bash
# test_strat.sh — Validate LLM endpoint is live and responding correctly.
#
# Usage:
#   bash infra/test_strat.sh              # test localhost:8080 (default)
#   bash infra/test_strat.sh http://host:port/v1  # test custom endpoint
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

BASE_URL="${1:-http://127.0.0.1:8080}"
# Strip trailing /v1 if present — we append it ourselves
BASE_URL="${BASE_URL%/v1}"
ENDPOINT="${BASE_URL}/v1"

PASS=0
FAIL=0
TOTAL=0

pass() { ((PASS++)); ((TOTAL++)); printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { ((FAIL++)); ((TOTAL++)); printf "  \033[31m✗\033[0m %s\n" "$1"; }

echo "═══════════════════════════════════════════════════"
echo "  LLM Endpoint Test — $ENDPOINT"
echo "═══════════════════════════════════════════════════"

# ── 1. Health check ─────────────────────────────────────
echo ""
echo "[1/4] Health check..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health" 2>/dev/null || true)
if [[ "$HTTP_CODE" == "200" ]]; then
    pass "Health endpoint returned 200"
else
    fail "Health endpoint returned HTTP $HTTP_CODE (expected 200)"
fi

# ── 2. List models ──────────────────────────────────────
echo ""
echo "[2/4] List models..."
MODELS_RAW=$(curl -s "$ENDPOINT/models" 2>/dev/null || true)
if echo "$MODELS_RAW" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d.get('data',[]))>0" 2>/dev/null; then
    MODEL_ID=$(echo "$MODELS_RAW" | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])")
    pass "Models endpoint returned ≥1 model (using: $MODEL_ID)"
else
    fail "Models endpoint did not return valid model list"
    MODEL_ID=""
fi

# ── 3. Chat completion — simple ─────────────────────────
echo ""
echo "[3/4] Chat completion (simple)..."
CHAT_RAW=$(curl -s "$ENDPOINT/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "'"$MODEL_ID"'",
        "messages": [{"role":"user","content":"Say exactly: PING_OK"}],
        "temperature": 0.0,
        "max_tokens": 20
    }' 2>/dev/null || true)

if echo "$CHAT_RAW" | python3 -c "
import sys, json
r = json.load(sys.stdin)
c = r['choices'][0]['message'].get('content','') or r['choices'][0]['message'].get('reasoning_content','')
assert len(c) > 0, 'empty content'
" 2>/dev/null; then
    CONTENT=$(echo "$CHAT_RAW" | python3 -c "
import sys, json
r = json.load(sys.stdin)
m = r['choices'][0]['message']
print(m.get('content','') or m.get('reasoning_content',''))
")
    pass "Chat completion returned content: ${CONTENT:0:60}"
else
    fail "Chat completion failed or returned empty content"
fi

# ── 4. Chat completion — policy query ───────────────────
echo ""
echo "[4/4] Chat completion (policy query)..."
POLICY_RAW=$(curl -s "$ENDPOINT/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "'"$MODEL_ID"'",
        "messages": [
            {"role":"system","content":"You are a policy assistant. Answer concisely."},
            {"role":"user","content":"How often should passwords be changed according to policy?"}
        ],
        "temperature": 0.0,
        "max_tokens": 200
    }' 2>/dev/null || true)

if echo "$POLICY_RAW" | python3 -c "
import sys, json
r = json.load(sys.stdin)
c = r['choices'][0]['message'].get('content','') or r['choices'][0]['message'].get('reasoning_content','')
assert len(c) > 10, 'content too short for policy query'
" 2>/dev/null; then
    POLICY_CONTENT=$(echo "$POLICY_RAW" | python3 -c "
import sys, json
r = json.load(sys.stdin)
m = r['choices'][0]['message']
print(m.get('content','') or m.get('reasoning_content',''))
")
    pass "Policy query returned: ${POLICY_CONTENT:0:80}..."
else
    fail "Policy query failed or returned too-short content"
fi

# ── Summary ─────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
if [[ "$FAIL" -eq 0 ]]; then
    printf "  \033[32mAll %d tests passed\033[0m\n" "$TOTAL"
else
    printf "  \033[31m%d/%d tests failed\033[0m\n" "$FAIL" "$TOTAL"
fi
echo "═══════════════════════════════════════════════════"

exit "$FAIL"
