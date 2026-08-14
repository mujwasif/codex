"""
Slack integration — signed inbound chat webhook.

Receives Slack Events API payloads at POST /v1/integrations/slack,
verifies the HMAC signature, routes app_mention / DM messages through the
Codex query pipeline, and posts the answer back as a threaded reply.

The bot is a trusted internal component: the inbound request is
authenticated by Slack's signed request (X-Slack-Signature), and the
mapping from Slack user -> Codex username is the authorization boundary.
JWTs are minted internally via create_access_token (same flow as /login),
so no passwords are stored in the mapping file.

Run / test notes:
    - Requires SLACK_SIGNING_SECRET, SLACK_BOT_TOKEN, CODEX_API_URL
    - Without a signing secret the signature check is skipped (dev mode)
"""

import json
import os
import re
import threading
from typing import Dict, List, Optional

import requests
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from slack_sdk.errors import SlackApiError

from packages.shared.auth import create_access_token, verify_token
from integrations.base import ChatIntegration

# Defaults so the module is importable in tests without env vars
DEFAULT_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
DEFAULT_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
DEFAULT_API_URL = os.getenv("CODEX_API_URL", "http://localhost:8000")
DEFAULT_USERS_MAP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "slack_users.json"
)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


class SlackIntegration(ChatIntegration):
    """Handles Slack Events API payloads and posts answers back via WebClient."""

    platform = "slack"

    def __init__(
        self,
        signing_secret: Optional[str] = None,
        bot_token: Optional[str] = None,
        api_url: Optional[str] = None,
        users_map_path: Optional[str] = None,
        user_store: Optional[dict] = None,
    ):
        self.signing_secret = signing_secret if signing_secret is not None else DEFAULT_SIGNING_SECRET
        self.bot_token = bot_token if bot_token is not None else DEFAULT_BOT_TOKEN
        self.api_url = (api_url or DEFAULT_API_URL).rstrip("/")
        self.users_map_path = users_map_path or DEFAULT_USERS_MAP_PATH
        # Optional injected user directory for tests / custom deployments.
        # Defaults to the shared MOCK_USERS store at call time.
        self.user_store = user_store

        self._verifier = SignatureVerifier(self.signing_secret) if self.signing_secret else None
        self._client = WebClient(token=self.bot_token) if self.bot_token else None
        self._user_map = self._load_user_map()
        # codex_username -> jwt token
        self._tokens: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ── Configuration ────────────────────────────────────────────────

    def _load_user_map(self) -> Dict[str, str]:
        """Load {slack_user_id: codex_username} from the JSON mapping file."""
        try:
            with open(self.users_map_path) as f:
                data = json.load(f)
                return {str(k).strip(): str(v).strip() for k, v in data.items() if k and v}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"⚠️ Slack user map load failed ({self.users_map_path}): {e}")
            return {}

    def resolve_codex_username(self, slack_user_id: str) -> str:
        """Map a Slack user ID to a Codex username. Defaults to 'employee'."""
        return self._user_map.get(str(slack_user_id).strip(), "employee")

    # ── Signature verification ───────────────────────────────────────

    def verify(self, raw_body: bytes, headers: Dict[str, str]) -> bool:
        """Verify Slack's X-Slack-Signature HMAC over the raw request body."""
        if self._verifier is None:
            # Dev mode — no signing secret configured
            return True
        if not raw_body or not headers:
            return False
        try:
            return self._verifier.is_valid_request(raw_body, headers)
        except Exception as e:
            print(f"⚠️ Slack signature verification error: {e}")
            return False

    # ── Entry point ──────────────────────────────────────────────────

    def handle(self, payload: dict, headers: Optional[dict] = None) -> dict:
        """Route an inbound Slack payload.

        Returns a dict for the HTTP response body. url_verification is
        answered synchronously (Slack requires a reply within 3s); event
        callbacks are acknowledged immediately and processed in a
        background thread.
        """
        ptype = payload.get("type")
        if ptype == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        if ptype == "event_callback":
            # Acknowledge fast, process async (Slack retries otherwise)
            event = payload.get("event", {}) or {}
            threading.Thread(
                target=self._process_event,
                args=(event,),
                daemon=True,
            ).start()

        return {"ok": True}

    # ── Event processing ─────────────────────────────────────────────

    def _process_event(self, event: dict):
        """Handle a single message event (runs in a background thread)."""
        # Ignore bot/echo messages and edit/delete subtypes
        if event.get("bot_id"):
            return
        subtype = event.get("subtype") or ""
        if subtype and subtype != "bot_message":
            return

        channel = event.get("channel")
        user = event.get("user")
        text = (event.get("text") or "").strip()
        if not channel or not user or not text:
            return

        question = _MENTION_RE.sub("", text).strip()
        if not question:
            return

        thread_ts = event.get("thread_ts") or event.get("ts")
        try:
            self._answer(user, channel, question, thread_ts)
        except Exception as e:
            print(f"⚠️ Slack processing error for {user}: {e}")
            self._safe_post(channel, thread_ts, self._error_blocks("Sorry, I hit an internal error."))

    def _answer(self, slack_user_id: str, channel: str, question: str, thread_ts: Optional[str]):
        """Resolve user, mint token, query Codex, post reply."""
        username = self.resolve_codex_username(slack_user_id)
        token = self._get_token(username)

        # Server handles conversational context from persisted history
        result = self._ask_codex(question, token)
        if result is None:
            self._safe_post(channel, thread_ts, self._error_blocks("The policy engine is unreachable right now."))
            return

        blocks = self._format_answer(result)
        self._safe_post(channel, thread_ts, blocks)

    # ── Codex API interaction ──────────────────────────────────────────

    def _get_user(self, username: str) -> Optional[dict]:
        """Resolve a user dict from the injected store or the PostgreSQL database."""
        if self.user_store is not None:
            return self.user_store.get(username)
        try:
            from codex.packages.shared.db import get_db_session
            from codex.packages.shared.models import User
            with get_db_session() as session:
                user = session.query(User).filter(User.username == username).first()
                if not user:
                    return None
                return {
                    "username": user.username,
                    "department": user.department,
                    "access_level": user.access_level,
                    "is_active": user.is_active,
                }
        except Exception as e:
            print(f"⚠️ Slack user store unavailable: {e}")
            return None

    def _get_token(self, username: str) -> str:
        """Return a cached JWT for the Codex username, minting one if needed.

        Reads the user's access_level from the user directory and mints the
        token with the same payload the /login endpoint would. Unknown users
        fall back to an employee-level token.
        """
        with self._lock:
            cached = self._tokens.get(username)
            if cached and verify_token(cached):
                return cached

        user = self._get_user(username)
        if not user:
            print(f"⚠️ Slack integration: unknown Codex user '{username}'")
            return self._mint_token("employee")
        return self._mint_token(username)

    def _mint_token(self, username: str) -> str:
        """Mint a JWT via create_access_token (mirrors /login token payload)."""
        user = self._get_user(username)
        if user is None and username != "employee":
            user = self._get_user("employee")
        level = user.get("access_level", 1) if user else 1
        token = create_access_token(data={"username": username, "access_level": level})
        with self._lock:
            self._tokens[username] = token
        return token

    def _ask_codex(self, question: str, token: str) -> Optional[dict]:
        """POST the question to the Codex /query endpoint."""
        try:
            response = requests.post(
                f"{self.api_url}/query",
                json={"question": question, "search_mode": "hybrid"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=120,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"⚠️ Codex query failed: {e}")
            return None

    # ── Slack reply ──────────────────────────────────────────────────

    def _safe_post(self, channel: str, thread_ts: Optional[str], blocks: List[dict]):
        """Post a reply via WebClient, swallowing Slack API errors."""
        if self._client is None:
            print(f"[dry-run slack] would post {len(blocks)} block(s) to {channel}")
            return
        try:
            kwargs = {"channel": channel, "blocks": blocks}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            self._client.chat_postMessage(**kwargs)
        except SlackApiError as e:
            print(f"⚠️ Slack postMessage failed: {e.response.get('error')}")

    def _verdict_emoji(self, verdict: str) -> str:
        return {
            "clear": ":white_check_mark:",
            "violation": ":red_circle:",
            "conditional": ":warning:",
            "conflict": ":twisted_rightwards_arrows:",
        }.get(verdict, ":grey_question:")

    def _verdict_color(self, verdict: str) -> str:
        return {
            "clear": "#2e7d32",
            "violation": "#c62828",
            "conditional": "#f9a825",
        }.get(verdict, "#546e7a")

    def _format_answer(self, result: dict) -> List[dict]:
        """Render an AnswerResponse as Slack blocks."""
        verdict = result.get("verdict", "abstained")
        confidence = result.get("confidence", 0.0)
        answer = result.get("answer", "No answer provided.")

        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": answer},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"{self._verdict_emoji(verdict)} Verdict: *{verdict.upper()}* "
                            f"| Confidence: *{confidence:.2f}*"
                        ),
                    }
                ],
            },
        ]

        citations = result.get("citations") or []
        if citations:
            # Split citations into blocks to avoid Slack's 3000-char limit per
            # block. The full quote is kept verbatim — no truncation.
            current_block_text = "*Citations:*\n"
            citation_blocks = []

            for i, cit in enumerate(citations, 1):
                ref = cit.get("clause_ref") or "N/A"
                doc = cit.get("title") or cit.get("document") or "N/A"
                score = cit.get("score") or 0.0
                quote = cit.get("quote") or ""

                item_text = f"{i}. {doc} — Clause `{ref}` (score {score:.3f})"
                if quote:
                    item_text += f"\n> {quote}"

                if len(current_block_text) + len(item_text) + 1 > 2500:
                    citation_blocks.append(
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": current_block_text.rstrip("\n")},
                        }
                    )
                    current_block_text = item_text + "\n"
                else:
                    current_block_text += f"{item_text}\n"

            if current_block_text.strip():
                citation_blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": current_block_text.rstrip("\n")},
                    }
                )

            blocks.extend(citation_blocks)

        next_steps = result.get("next_steps") or []
        if next_steps:
            steps = "\n".join(f"• {s}" for s in next_steps[:5])
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Next steps:*\n{steps}"},
                }
            )

        if verdict == "abstained":
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "> :mag: *Abstained* — no sufficient policy basis was found at your access "
                            "level. Try rewording, or ask an admin if the policy exists."
                        ),
                    },
                }
            )

        return blocks

    def _error_blocks(self, message: str) -> List[dict]:
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":warning: {message}"},
            }
        ]