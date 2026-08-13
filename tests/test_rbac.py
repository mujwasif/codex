#!/usr/bin/env python3
"""
RBAC tests: access-level inference, enforcement semantics, and BM25 isolation.
"""

import os
import sys
from unittest.mock import patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from packages.shared.access_control import (
    infer_access_level,
    clamp_access_level,
    can_access,
    DEFAULT_ACCESS_LEVEL,
)


# ═══════════════════════════════════════
#  infer_access_level
# ═══════════════════════════════════════

def test_default_level_is_one():
    """An untagged, non-keyword document defaults to level 1."""
    assert infer_access_level("General Work Policy", None, "") == 1
    assert infer_access_level("Some Document.docx", [], "Nothing sensitive here.") == 1


def test_explicit_numeric_tag_wins():
    """A numeric level tag overrides all other signals."""
    assert infer_access_level("Anything.docx", ["level:3"], "salary info") == 3
    assert infer_access_level("Anything.docx", ["access_level=2"], "password reset") == 2
    assert infer_access_level("Anything.docx", ["access: 1"], "confidential text") == 1


def test_explicit_restricted_tag_wins():
    """Named sensitivity tags force their level regardless of title/content."""
    assert infer_access_level("Travel Policy.docx", ["restricted"], "") == 3
    assert infer_access_level("Travel Policy.docx", ["confidential"], "") == 3
    assert infer_access_level("General Policy.docx", ["manager"], "salary") == 2


def test_internal_tag_does_not_block_promotion():
    """The default 'internal' tag (level 1) must not prevent keyword promotion."""
    assert infer_access_level("HR Policy.docx", ["internal"], "") == 2
    assert infer_access_level("Salary Confidential Policy.docx", ["internal"], "") == 3


def test_title_level3_keywords():
    """Sensitive title words push to level 3."""
    for kw in ["confidential", "restricted", "salary", "compensation", "legal",
               "bonus", "termination", "classified", "executive"]:
        assert infer_access_level(f"2026 {kw.title()} Policy.docx", [], "") == 3, kw


def test_title_level2_keywords():
    """Managerial title words push to level 2."""
    for kw in ["manager", "management", "hr", "human-resource", "finance",
               "payroll", "budget", "forecast", "strategy", "board"]:
        assert infer_access_level(f"Annual {kw.title()} Report.docx", [], "") == 2, kw


def test_title_level2_precedes_content_level3():
    """Content is only checked after the title, so a level-2 title wins over content level-3."""
    assert infer_access_level("HR Handbook.docx", [], "Employee salary is confidential.") == 2


def test_content_level3():
    """Sensitive body keywords push to level 3 when title is neutral."""
    assert infer_access_level("Q4 Update.docx", [], "The board approved the bonus pool.") == 3
    assert infer_access_level("Memo.docx", [], "This document is confidential.") == 3


def test_content_level2():
    """Managerial body keywords push to level 2 when title is neutral."""
    assert infer_access_level("Weekly Report.docx", [], "The payroll run closes Friday.") == 2
    assert infer_access_level("Notes.docx", [], "Budget review scheduled for Q3.") == 2


def test_content_only_first_5000_chars():
    """Only the first SAMPLE_CHARS of the body are inspected."""
    body = "a" * 6000 + " salary increase approved"
    assert infer_access_level("Plain Title.docx", [], body) == 1


def test_lowercase_keywords_in_content():
    """Content keyword matching is case-insensitive."""
    assert infer_access_level("Doc.docx", [], "CONFIDENTIAL DATA") == 3


# ═══════════════════════════════════════
#  clamp_access_level / can_access
# ═══════════════════════════════════════

def test_clamp_bounds():
    """Values outside 1-3 are clamped; junk defaults to 1."""
    assert clamp_access_level(0) == 1
    assert clamp_access_level(99) == 3
    assert clamp_access_level("2") == 2
    assert clamp_access_level(None) == 1
    assert clamp_access_level("junk") == 1


def test_can_access_semantics():
    """Level N sees levels <= N."""
    assert can_access(1, 1) is True
    assert can_access(1, 2) is False
    assert can_access(1, 3) is False
    assert can_access(2, 1) is True
    assert can_access(2, 2) is True
    assert can_access(2, 3) is False
    assert can_access(3, 1) is True
    assert can_access(3, 2) is True
    assert can_access(3, 3) is True


def test_default_constant():
    assert DEFAULT_ACCESS_LEVEL == 1


# ═══════════════════════════════════════
#  BM25Index per-level isolation
# ═══════════════════════════════════════

class _FakeRow:
    def __init__(self, chunk_id, text, clause_ref, section_path, document_id, access_level, title):
        self.id = chunk_id
        self.text = text
        self.clause_ref = clause_ref
        self.section_path = section_path
        self.document_id = document_id
        self.access_level = access_level
        self.title = title


def _make_index(rows):
    """Build a BM25Index without touching the DB."""
    from services.ingestion.bm25_index import BM25Index

    class _FakeSession:
        def execute(self, sql, **kwargs):
            return rows

    class _FakeCtx:
        def __enter__(self):
            return _FakeSession()

        def __exit__(self, *a):
            return False

    with patch("services.ingestion.bm25_index.get_db_session", return_value=_FakeCtx()):
        return BM25Index()


def test_bm25_isolation_level_one_sees_only_level_one():
    """A level-1 user must not see level-2/3 chunks in BM25 results."""
    rows = [
        _FakeRow("c1", "Employee badge renewal must be completed weekly", "1.1", "sec1", "d1", 1, "Employee Handbook"),
        _FakeRow("c2", "The payroll schedule must remain confidential", "2.1", "sec2", "d2", 2, "Finance Report"),
        _FakeRow("c3", "Executive bonus details are restricted data", "3.1", "sec3", "d3", 3, "Board Memo"),
    ]
    idx = _make_index(rows)
    results = idx.search("payroll bonus", top_k=20, access_level=1)
    ids = {r["id"] for r in results}
    assert ids == {"c1"} or ids <= {"c1"}


def test_bm25_isolation_level_two_sees_levels_one_and_two():
    rows = [
        _FakeRow("c1", "Employee badge renewal must be completed weekly", "1.1", "sec1", "d1", 1, "Employee Handbook"),
        _FakeRow("c2", "The payroll schedule must remain confidential", "2.1", "sec2", "d2", 2, "Finance Report"),
        _FakeRow("c3", "Executive bonus details are restricted data", "3.1", "sec3", "d3", 3, "Board Memo"),
    ]
    idx = _make_index(rows)
    results = idx.search("payroll", top_k=20, access_level=2)
    ids = {r["id"] for r in results}
    assert "c2" in ids
    assert "c3" not in ids


def test_bm25_isolation_level_three_sees_all():
    rows = [
        _FakeRow("c1", "Employee badge renewal must be completed weekly", "1.1", "sec1", "d1", 1, "Employee Handbook"),
        _FakeRow("c2", "The payroll schedule must remain confidential", "2.1", "sec2", "d2", 2, "Finance Report"),
        _FakeRow("c3", "Executive bonus details are restricted data", "3.1", "sec3", "d3", 3, "Board Memo"),
    ]
    idx = _make_index(rows)
    results = idx.search("payroll", top_k=20, access_level=3)
    assert any(r["id"] == "c2" for r in results)


def test_bm25_default_level_is_one():
    """When access_level is omitted, the caller defaults to level 1."""
    rows = [
        _FakeRow("c1", "Employee badge renewal must be completed weekly", "1.1", "sec1", "d1", 1, "Employee Handbook"),
        _FakeRow("c2", "The payroll schedule must remain confidential", "2.1", "sec2", "d2", 2, "Finance Report"),
    ]
    idx = _make_index(rows)
    results = idx.search("payroll", top_k=20)
    assert all(r["access_level"] == 1 for r in results)


def test_bm25_chunk_count_is_total():
    rows = [
        _FakeRow("c1", "Employee badge renewal must be completed weekly", "1.1", "sec1", "d1", 1, "Employee Handbook"),
        _FakeRow("c2", "The payroll schedule must remain confidential", "2.1", "sec2", "d2", 2, "Finance Report"),
        _FakeRow("c3", "Executive bonus details are restricted data", "3.1", "sec3", "d3", 3, "Board Memo"),
    ]
    idx = _make_index(rows)
    assert idx.chunk_count == 3


def test_bm25_result_meta_carries_access_level():
    rows = [
        _FakeRow("c1", "Employee badge renewal must be completed weekly", "1.1", "sec1", "d1", 1, "Employee Handbook"),
    ]
    idx = _make_index(rows)
    results = idx.search("badge", top_k=20, access_level=1)
    assert results[0]["access_level"] == 1
    assert results[0]["document_id"] == "d1"


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("RBAC TESTS")
    print("=" * 60)

    tests = [
        ("Default level is 1", test_default_level_is_one),
        ("Explicit numeric tag wins", test_explicit_numeric_tag_wins),
        ("Explicit restricted tag wins", test_explicit_restricted_tag_wins),
        ("Internal tag does not block promotion", test_internal_tag_does_not_block_promotion),
        ("Title level-3 keywords", test_title_level3_keywords),
        ("Title level-2 keywords", test_title_level2_keywords),
        ("Title level-2 precedes content level-3", test_title_level2_precedes_content_level3),
        ("Content level-3", test_content_level3),
        ("Content level-2", test_content_level2),
        ("Content only first 5000 chars", test_content_only_first_5000_chars),
        ("Lowercase keywords in content", test_lowercase_keywords_in_content),
        ("Clamp bounds", test_clamp_bounds),
        ("Can-access semantics", test_can_access_semantics),
        ("Default constant", test_default_constant),
        ("BM25 level-1 isolation", test_bm25_isolation_level_one_sees_only_level_one),
        ("BM25 level-2 isolation", test_bm25_isolation_level_two_sees_levels_one_and_two),
        ("BM25 level-3 sees all", test_bm25_isolation_level_three_sees_all),
        ("BM25 default level is 1", test_bm25_default_level_is_one),
        ("BM25 chunk count is total", test_bm25_chunk_count_is_total),
        ("BM25 meta carries access_level", test_bm25_result_meta_carries_access_level),
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
