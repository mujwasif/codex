#!/bin/bash
# Send a signed fake Slack payload to the local Codex webhook.
#
# Requires real env values (or dev mode with empty SLACK_SIGNING_SECRET):
#   SLACK_SIGNING_SECRET, SLACK_BOT_TOKEN, CODEX_API_URL
#
# Usage:
#   source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
#   bash infra/server_slack_test.sh

CODEX_DIR="/home/mujtaba/new_folder/codex"
API_URL="${CODEX_API_URL:-http://localhost:8000}"
SIGNING_SECRET="${SLACK_SIGNING_SECRET:-}"
USER="${1:-U0123456789}"
CHANNEL="${2:-C0000000000}"
QUESTION="${3:-How often should passwords change?}"

python3 - "$USER" "$CHANNEL" "$QUESTION" "$SIGNING_SECRET" "$API_URL" <<'EOF'
import json
import sys
import hashlib
import hmac
import os
import time
from urllib.request import Request, urlopen, HTTPError

user, channel, question, secret, api_url = sys.argv[1:6]

payload = {
    "token": "test-token",
    "team_id": "T00000000",
    "api_app_id": "A00000000",
    "type": "event_callback",
    "event": {
        "type": "message",
        "channel": channel,
        "user": user,
        "text": question,
        "ts": "1400000000.000000",
        "channel_type": "channel",
    },
    "event_id": "Ev00000000",
    "event_time": int(time.time()),
}

body = json.dumps(payload)
headers = {"Content-Type": "application/json"}

if secret:
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    headers["X-Slack-Request-Timestamp"] = ts
    headers["X-Slack-Signature"] = sig
    print(f"Using signing secret: present (signature computed)")
else:
    print("Using signing secret: EMPTY (dev mode — signature not checked)")

req = Request(f"{api_url}/v1/integrations/slack", data=body.encode(), headers=headers, method="POST")
try:
    with urlopen(req, timeout=30) as resp:
        print(f"Status: {resp.status}")
        print(resp.read().decode())
except HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"Error: {e}")
EOF
