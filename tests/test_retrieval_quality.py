#!/usr/bin/env python3
"""
Retrieval-quality tests: low-information chunk filtering, title-aware
reranking, and the password-policy retrieval regression.

Covers the fix for "tell me some policies regarding password" abstaining:
  Phase 1 - is_low_info() excludes prompt leaks / table junk / bare refs
  Phase 2 - document titles are fed to the reranker so the source policy
            name can be matched
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from packages.shared.chunk_filter import is_low_info

PASSWORD_DOC = "UnderDefense MAXI - Password management policy.docx"


# ═══════════════════════════════════════
#  is_low_info unit tests
# ═══════════════════════════════════════

def test_prompt_leak_flagged():
    """Clauses that echo the LLM's own prompt are low-information."""
    assert is_low_info("", "Each clause must be a single requirement or prohibition")
    assert is_low_info("", "Each clause must be a single requirement or prohibition Keep all original wording - do not paraphrase")


def test_cot_prose_flagged():
    """Chain-of-thought workflow prose is flagged."""
    assert is_low_info("", "CHAIN-OF-THOUGHT WORKFLOW (reason step-by-step first, then emit the JSON array):")
    assert is_low_info("", "Step 1 - Read the section and list every distinct requirement, then emit the JSON array")


def test_table_junk_flagged():
    """Pure table/header junk (Issued/Reviewed/Approved/date) is flagged."""
    assert is_low_info("", "Issued Reviewed Approved")
    assert is_low_info("", "Date of Next Revision | <date>")
    assert is_low_info("", "Issued Updated Approved Granted 'FINAL' status")


def test_bare_reference_flagged():
    """A cross-reference that only names another document is flagged."""
    assert is_low_info("", "<Company> Password Management Policy")


def test_short_requirement_kept():
    """Real short clauses with a requirement verb are kept."""
    assert not is_low_info("", "Users must not reuse passwords.")
    assert not is_low_info("", "Each password must be at least 12 characters long.")
    assert not is_low_info("", "Keys must be backed up.")


def test_imperative_action_kept():
    """Imperative action clauses (no must/shall) are kept when substantive."""
    assert not is_low_info("", "Identify the authorized party or parties to approve the request.")
    assert not is_low_info("", "Set up security configurations on the network equipment;")


def test_empty_or_tiny_flagged():
    """Empty and single-token chunks are flagged."""
    assert is_low_info("", "")
    assert is_low_info("", "   ")
    assert is_low_info("", "TV")
    assert is_low_info("", "High;")


# ═══════════════════════════════════════
#  Retrieval integration tests
# ═══════════════════════════════════════

def test_password_query_surfaces_password_policy():
    """The broad password query returns substantive clauses from the
    Password management policy, not bare 'Related Documents' references."""
    from services.ingestion.bm25_index import BM25Index
    from services.api.search import search_policy

    bm25 = BM25Index()
    results = search_policy(
        "tell me some policies regarding password",
        3,
        search_mode="hybrid",
        bm25_index=bm25,
    )
    assert results, "expected non-empty results"

    titles = [c.get("title", "") for c in results]
    assert PASSWORD_DOC in titles, f"password policy not in top results: {titles}"

    # No bare cross-reference line should dominate the top 3
    for c in results:
        assert "Related Documents > Rule 1" not in c.get("clause_ref", "")
        assert "Each clause must be a single requirement" not in (c.get("text") or "")


def test_specific_password_query_returns_rotation_rule():
    """'How often should passwords change?' surfaces the 90-day rule."""
    from services.ingestion.bm25_index import BM25Index
    from services.api.search import search_policy

    bm25 = BM25Index()
    results = search_policy(
        "How often should passwords change?",
        3,
        search_mode="hybrid",
        bm25_index=bm25,
    )
    texts = " ".join((c.get("text") or "") for c in results).lower()
    assert "90 days" in texts, f"expected 90-day rotation rule, got: {texts[:200]}"


def test_bm25_index_excludes_prompt_leaks():
    """No prompt-leak text should survive into the BM25 index."""
    from services.ingestion.bm25_index import BM25Index

    bm25 = BM25Index()
    assert bm25.chunk_count > 0
    # Probe a search for the leaked prompt phrase: should return nothing
    hits = bm25.search("Each clause must be a single requirement or prohibition", 5, 3)
    for c in hits:
        assert "Each clause must be a single requirement" not in (c.get("text") or "")


def test_rerank_uses_document_title():
    """Rerank pairs include the document title (title-aware reranking)."""
    import services.api.search as S
    from unittest.mock import patch

    captured = {}

    def fake_predict(pairs):
        captured["pairs"] = pairs
        # Keep relative ordering stable; only capture the pairs.
        return [float(i) for i in range(len(pairs) - 1, -1, -1)]

    with patch.object(S.reranker, "predict", side_effect=fake_predict):
        S.search_policy(
            "tell me some policies regarding password",
            3,
            search_mode="hybrid",
        )
    assert captured.get("pairs"), "expected rerank pairs to be captured"
    assert any(PASSWORD_DOC in p[1] for p in captured["pairs"]), (
        "document title not present in rerank pairs"
    )


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("RETRIEVAL QUALITY TESTS")
    print("=" * 60)

    tests = [
        ("Prompt leak flagged", test_prompt_leak_flagged),
        ("CoT prose flagged", test_cot_prose_flagged),
        ("Table junk flagged", test_table_junk_flagged),
        ("Bare reference flagged", test_bare_reference_flagged),
        ("Short requirement kept", test_short_requirement_kept),
        ("Imperative action kept", test_imperative_action_kept),
        ("Empty/tiny flagged", test_empty_or_tiny_flagged),
        ("Password query surfaces policy", test_password_query_surfaces_password_policy),
        ("Specific password query returns rule", test_specific_password_query_returns_rotation_rule),
        ("BM25 excludes prompt leaks", test_bm25_index_excludes_prompt_leaks),
        ("Rerank uses document title", test_rerank_uses_document_title),
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
