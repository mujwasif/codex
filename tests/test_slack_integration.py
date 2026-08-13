#!/usr/bin/env python3
"""
Slack integration tests: signature verification, url_verification,
event routing, RBAC mapping, answer formatting.
"""

import os
import sys
from unittest.mock import patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from integrations.slack import SlackIntegration


# ═══════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════

def make_integration(**kwargs):
    """Build a SlackIntegration with an empty user map and no network."""
    defaults = {
        "signing_secret": "test-secret",
        "bot_token": "xoxb-test",
        "api_url": "http://localhost:8000",
        "users_map_path": os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "integrations", "slack_users.json"
        ),
    }
    defaults.update(kwargs)
    return SlackIntegration(**defaults)


def block_texts(blocks):
    """Flatten all mrkdwn text (text + elements) from a Slack block list."""
    texts = []
    for b in blocks:
        if b.get("text"):
            texts.append(b["text"].get("text", ""))
        for el in b.get("elements", []) or []:
            texts.append(el.get("text", ""))
    return " ".join(texts)


# ═══════════════════════════════════════
#  User mapping
# ═══════════════════════════════════════

def test_resolve_mapped_user():
    """A mapped Slack user resolves to the configured Codex username."""
    intg = make_integration()
    assert intg.resolve_codex_username("U0BMPHDRXHB") == "admin"
    assert intg.resolve_codex_username("U0ABCDEFGH1") == "manager"


def test_resolve_unmapped_user_defaults_employee():
    """Unmapped Slack users default to 'employee' (level 1)."""
    intg = make_integration()
    assert intg.resolve_codex_username("U9999999999") == "employee"
    assert intg.resolve_codex_username("unknown") == "employee"


def test_user_map_missing_file_returns_empty():
    """A missing mapping file degrades to an empty map (all -> employee)."""
    intg = make_integration(users_map_path="/nonexistent/slack_users.json")
    assert intg.resolve_codex_username("U0123456789") == "employee"


# ═══════════════════════════════════════
#  Signature verification
# ═══════════════════════════════════════

def test_signature_valid():
    """A correctly signed request verifies True."""
    import time as t
    intg = make_integration()
    body = b'{"type":"url_verification"}'
    ts = str(int(t.time()))
    import hashlib
    import hmac
    sig = "v0=" + hmac.new(b"test-secret", f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    headers = {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": sig,
    }
    assert intg.verify(body, headers) is True


def test_signature_tampered_fails():
    """A body tampered after signing fails verification."""
    import time as t
    import hashlib
    import hmac
    intg = make_integration()
    ts = str(int(t.time()))
    body = b'{"type":"url_verification"}'
    sig = "v0=" + hmac.new(b"test-secret", f"v0:{ts}:".encode() + b'{"type":"other"}', hashlib.sha256).hexdigest()
    headers = {"x-slack-request-timestamp": ts, "x-slack-signature": sig}
    assert intg.verify(body, headers) is False


def test_signature_missing_headers_fails():
    """Missing signature headers fail verification."""
    intg = make_integration()
    assert intg.verify(b"{}", {}) is False


def test_signature_dev_mode_skips():
    """With no signing secret, verification is skipped (dev mode)."""
    intg = make_integration(signing_secret="")
    assert intg.verify(b"whatever", {}) is True


# ═══════════════════════════════════════
#  handle() routing
# ═══════════════════════════════════════

def test_url_verification_returns_challenge():
    """url_verification is answered synchronously with the challenge."""
    intg = make_integration()
    resp = intg.handle({"type": "url_verification", "challenge": "s3cr3t"})
    assert resp == {"challenge": "s3cr3t"}


def test_event_callback_acks_and_spawns_thread():
    """event_callback acknowledges immediately and processes async."""
    import time
    intg = make_integration()
    captured = {}

    def fake_process(event):
        captured["event"] = event

    with patch.object(intg, "_process_event", side_effect=fake_process):
        resp = intg.handle({"type": "event_callback", "event": {"x": 1}})
        assert resp == {"ok": True}
        # Let the background thread run
        time.sleep(0.2)
        assert captured.get("event") == {"x": 1}


# ═══════════════════════════════════════
#  Event filtering
# ═══════════════════════════════════════

def test_bot_messages_ignored():
    """Messages from bots (echo) are ignored."""
    intg = make_integration()
    with patch.object(intg, "_answer") as mock:
        intg._process_event({"type": "message", "bot_id": "B123", "text": "hi", "channel": "C", "user": "U"})
        mock.assert_not_called()


def test_edit_delete_subtypes_ignored():
    """message_changed / message_deleted subtypes are ignored."""
    intg = make_integration()
    with patch.object(intg, "_answer") as mock:
        intg._process_event({"type": "message", "subtype": "message_changed", "text": "x", "channel": "C", "user": "U"})
        intg._process_event({"type": "message", "subtype": "message_deleted", "text": "x", "channel": "C", "user": "U"})
        mock.assert_not_called()


def test_mention_stripped_from_text():
    """The app mention token is stripped before querying."""
    intg = make_integration()
    with patch.object(intg, "_answer") as mock:
        intg._process_event({
            "type": "message",
            "channel": "C123",
            "user": "U0123456789",
            "text": "<@U0BOT123> How often should passwords change?",
            "ts": "1400000000.000000",
        })
        mock.assert_called_once()
        user, channel, question, thread_ts = mock.call_args.args
        assert question == "How often should passwords change?"
        assert channel == "C123"
        assert thread_ts == "1400000000.000000"


def test_empty_message_after_mention_ignored():
    """A mention with no real question is ignored."""
    intg = make_integration()
    with patch.object(intg, "_answer") as mock:
        intg._process_event({"type": "message", "channel": "C", "user": "U", "text": "<@U0BOT123>"})
        mock.assert_not_called()


# ═══════════════════════════════════════
#  Token minting
# ═══════════════════════════════════════

def test_mint_token_caches_per_user():
    """Tokens are minted once and cached per Codex username."""
    intg = make_integration(user_store={"employee": {"username": "employee", "access_level": 1}})
    with patch("integrations.slack.create_access_token", return_value="jwt-abc") as mock_mint:
        t1 = intg._mint_token("employee")
        t2 = intg._mint_token("employee")
    assert t1 == "jwt-abc"
    assert t2 == "jwt-abc"
    assert mock_mint.call_count == 2  # _mint_token itself is idempotent via cache


def test_get_token_reads_access_level_from_mock_users():
    """get_token resolves the user's level from the user directory (like /login)."""
    fake_users = {
        "admin": {"username": "admin", "access_level": 3},
        "employee": {"username": "employee", "access_level": 1},
    }
    intg = make_integration(user_store=fake_users)
    with patch("integrations.slack.create_access_token") as mock_mint:
        mock_mint.side_effect = lambda data: f"jwt:{data['username']}:{data['access_level']}"
        assert intg._get_token("admin") == "jwt:admin:3"
        assert intg._get_token("unmapped-user") == "jwt:employee:1"


def test_get_token_remints_when_cached_token_expired():
    """An expired/invalid cached token is re-minted instead of reused."""
    fake_users = {
        "admin": {"username": "admin", "access_level": 3},
        "employee": {"username": "employee", "access_level": 1},
    }
    intg = make_integration(user_store=fake_users)
    minted = {"count": 0}
    with patch("integrations.slack.create_access_token") as mock_mint, \
         patch("integrations.slack.verify_token") as mock_verify:
        def fake_mint(data):
            minted["count"] += 1
            return f"jwt:{data['username']}:{data['access_level']}:{minted['count']}"
        mock_mint.side_effect = fake_mint
        # First call: nothing cached -> mint (verify_token not consulted)
        mock_verify.return_value = None
        t1 = intg._get_token("admin")
        assert t1 == "jwt:admin:3:1"
        # Second call: cached token fails verification -> re-mint
        t2 = intg._get_token("admin")
        assert t2 == "jwt:admin:3:2"
        assert mock_mint.call_count == 2
        assert mock_verify.call_count == 1


# ═══════════════════════════════════════
#  Answer formatting
# ═══════════════════════════════════════

def test_format_clear_verdict():
    """A clear answer includes verdict + confidence + citations blocks."""
    intg = make_integration()
    result = {
        "answer": "Passwords must change every 90 days.",
        "verdict": "clear",
        "confidence": 0.92,
        "citations": [{"document": "Password Policy", "clause_ref": "4.2", "score": 0.95}],
        "next_steps": [],
    }
    blocks = intg._format_answer(result)
    texts = block_texts(blocks)
    assert "Passwords must change every 90 days." in texts
    assert "CLEAR" in texts
    assert "0.92" in texts
    assert "Password Policy" in texts
    assert "Clause `4.2`" in texts


def test_format_abstained_has_fallback():
    """Abstained answers surface a guidance hint block."""
    intg = make_integration()
    blocks = intg._format_answer({"answer": "Insufficient policy basis.", "verdict": "abstained", "confidence": 0.1})
    texts = block_texts(blocks)
    assert "Abstained" in texts
    assert "no sufficient policy basis" in texts


def test_format_includes_next_steps():
    """next_steps are rendered as a list."""
    intg = make_integration()
    blocks = intg._format_answer({
        "answer": "Approval required.",
        "verdict": "conditional",
        "confidence": 0.7,
        "next_steps": ["Contact Finance", "Submit form F-12"],
    })
    texts = block_texts(blocks)
    assert "Contact Finance" in texts
    assert "Submit form F-12" in texts


def test_format_violation_emoji():
    """Violation verdict maps to the red circle emoji."""
    intg = make_integration()
    blocks = intg._format_answer({"answer": "x", "verdict": "violation", "confidence": 0.8})
    texts = block_texts(blocks)
    assert ":red_circle:" in texts


# ═══════════════════════════════════════
#  _ask_codex
# ═══════════════════════════════════════

def test_ask_codex_posts_to_api():
    """ask_codex posts to /query with the bearer token."""
    intg = make_integration()
    with patch("integrations.slack.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"answer": "yes"}
        result = intg._ask_codex("question?", "jwt-1")
    assert result == {"answer": "yes"}
    mock_post.assert_called_once()
    url = mock_post.call_args.args[0]
    assert url == "http://localhost:8000/query"
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer jwt-1"


def test_ask_codex_handles_failure():
    """A failed Codex call returns None (no crash)."""
    intg = make_integration()
    with patch("integrations.slack.requests.post", side_effect=Exception("down")):
        assert intg._ask_codex("q", "t") is None


def test_safe_post_dry_run_without_client():
    """With no bot token, replies are logged (dry-run) not sent."""
    intg = make_integration(bot_token="")
    intg._safe_post("C123", None, [{"type": "section"}])  # should not raise


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("SLACK INTEGRATION TESTS")
    print("=" * 60)

    tests = [
        ("Resolve mapped user", test_resolve_mapped_user),
        ("Unmapped user defaults employee", test_resolve_unmapped_user_defaults_employee),
        ("Missing user map file", test_user_map_missing_file_returns_empty),
        ("Signature valid", test_signature_valid),
        ("Signature tampered fails", test_signature_tampered_fails),
        ("Signature missing headers fails", test_signature_missing_headers_fails),
        ("Signature dev mode skips", test_signature_dev_mode_skips),
        ("url_verification challenge", test_url_verification_returns_challenge),
        ("event_callback acks + async", test_event_callback_acks_and_spawns_thread),
        ("Bot messages ignored", test_bot_messages_ignored),
        ("Edit/delete subtypes ignored", test_edit_delete_subtypes_ignored),
        ("Mention stripped", test_mention_stripped_from_text),
        ("Empty mention ignored", test_empty_message_after_mention_ignored),
        ("Token cached per user", test_mint_token_caches_per_user),
        ("Token reads access level", test_get_token_reads_access_level_from_mock_users),
        ("Token re-mints when cached token expired", test_get_token_remints_when_cached_token_expired),
        ("Format clear verdict", test_format_clear_verdict),
        ("Format abstained fallback", test_format_abstained_has_fallback),
        ("Format next steps", test_format_includes_next_steps),
        ("Format violation emoji", test_format_violation_emoji),
        ("ask_codex posts to API", test_ask_codex_posts_to_api),
        ("ask_codex failure returns None", test_ask_codex_handles_failure),
        ("safe_post dry run", test_safe_post_dry_run_without_client),
    ]

    results = []
    for name, fn in tests:
        try:
            fn()
            results.append((name, True))
            print(f"{name}: ✅ PASSED")
        except AssertionError as e:
            results.append((name, False))
            print(f"{name}: ❌ FAILED ({e})")

    all_passed = all(passed for _, passed in results)
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{name}: {status}")

    if all_passed:
        print("\n🎉 All tests PASSED!")
    else:
        print("\n⚠️ Some tests FAILED")

    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
