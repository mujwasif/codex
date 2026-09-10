#!/usr/bin/env python3
"""
Conflict Integration Tests — All Three Types

Tests conflict detection with realistic policy clause data across
three mock documents. Prints detailed results for visual inspection.

Documents:
  doc1 — IT Security Policy
    c1: "All passwords must be changed every 90 days." (ref: 5.1.3)
    c2: "All system access must use multi-factor authentication." (ref: 5.2.1)
    c3: "All employee data must be retained for 7 years." (ref: 8.1.2)

  doc2 — HR Onboarding Policy
    c4: "All passwords must be changed every 180 days." (ref: 3.2.1)  ← CONFLICTS with c1
    c5: "New hires receive badge access on their first day." (ref: 2.1.4)
    c6: "Employee data must be retained for 3 years." (ref: 7.3.1)   ← CONFLICTS with c3

  doc3 — Vendor Management Policy
    c7: "Vendor access must be reviewed quarterly." (ref: 4.1.2)
    c8: "Vendor data must be retained for 5 years." (ref: 6.2.3)     ← CONFLICTS with c3
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.agents.conflict_agent import (
    expand_chunks_for_conflicts,
    compare_document_chunks,
    detect_conflicting_documents,
)
from services.agents.tools.base import ToolResult

SIMILAR_PATH = "services.api.search.find_similar_clauses"
LLM_PATH = "services.agents.conflict_agent._llm_conflict_check"
NEO4J_PATH = "services.agents.conflict_agent._check_neo4j_conflicts"


# ════════════════════════════════════════════════════════════════
#  Test Data — Three documents with known conflicts
# ════════════════════════════════════════════════════════════════

def _c(doc_id, chunk_id, text, ref, title):
    """Shorthand to build a chunk dict."""
    return {
        "id": chunk_id,
        "document_id": doc_id,
        "text": text,
        "clause_ref": ref,
        "section_path": ref,
        "title": title,
        "similarity": 0.0,
    }

# Document 1: IT Security Policy
DOC1 = "doc-sec-001"
DOC1_TITLE = "IT Security Policy"
C1 = _c(DOC1, "c1", "All passwords must be changed every 90 days. This applies to all user accounts across all systems.", "5.1.3", DOC1_TITLE)
C2 = _c(DOC1, "c2", "All system access must use multi-factor authentication. No exceptions are permitted for any user role.", "5.2.1", DOC1_TITLE)
C3 = _c(DOC1, "c3", "All employee data including personal records and access logs must be retained for a minimum of 7 years.", "8.1.2", DOC1_TITLE)

# Document 2: HR Onboarding Policy
DOC2 = "doc-hr-002"
DOC2_TITLE = "HR Onboarding Policy"
C4 = _c(DOC2, "c4", "All passwords must be changed every 180 days. This applies to all employees including contractors.", "3.2.1", DOC2_TITLE)
C5 = _c(DOC2, "c5", "New hires receive badge access on their first day of employment. Setup is handled by facilities.", "2.1.4", DOC2_TITLE)
C6 = _c(DOC2, "c6", "Employee data must be retained for 3 years after departure. Records are then archived.", "7.3.1", DOC2_TITLE)

# Document 3: Vendor Management Policy
DOC3 = "doc-vnd-003"
DOC3_TITLE = "Vendor Management Policy"
C7 = _c(DOC3, "c7", "Vendor access must be reviewed quarterly. Reviews are conducted by the vendor management team.", "4.1.2", DOC3_TITLE)
C8 = _c(DOC3, "c8", "Vendor data including contracts and access logs must be retained for 5 years minimum.", "6.2.3", DOC3_TITLE)


def _print_conflict(i, c):
    """Pretty-print a single conflict record."""
    ca = c.get("clause_a", {})
    cb = c.get("clause_b", {})
    print(f"  CONFLICT #{i}:")
    print(f"    A: \"{ca.get('text', '')[:80]}...\"")
    print(f"       → {ca.get('document_title', '?')} / {ca.get('clause_ref', '?')}")
    print(f"    B: \"{cb.get('text', '')[:80]}...\"")
    print(f"       → {cb.get('document_title', '?')} / {cb.get('clause_ref', '?')}")
    print(f"    Similarity: {c.get('similarity', 0):.2f} | Source: {c.get('source', '?')}")
    if c.get("reason"):
        print(f"    Reason: {c['reason']}")
    print()


def _print_section(title):
    print()
    print("═" * 70)
    print(f"  {title}")
    print("═" * 70)


def _no_neo4j(*a, **kw):
    return []


def _fake_similar_by_text(text_to_chunks):
    """Build a mock for find_similar_clauses that maps input text to results."""
    def fake(clause_text, access_level, exclude_doc_id="", top_k=5, threshold=0.7):
        for key, chunks in text_to_chunks.items():
            if key.lower() in clause_text.lower():
                return [c for c in chunks if c.get("document_id") != exclude_doc_id][:top_k]
        return []
    return fake


def _fake_llm_by_pair(pair_results):
    """Build a mock for _llm_conflict_check that maps clause pair IDs to results."""
    def fake(text_a, text_b):
        for (t1_key, t2_key), result in pair_results.items():
            if t1_key.lower() in text_a.lower() and t2_key.lower() in text_b.lower():
                return result
            if t2_key.lower() in text_a.lower() and t1_key.lower() in text_b.lower():
                return result
        return {"conflict": False, "confidence": 0.5, "reason": "No known conflict", "subject": ""}
    return fake


# LLM results for known conflict pairs
PASSWORD_CONFLICT = {"conflict": True, "confidence": 0.94, "reason": "90-day vs 180-day password rotation for same subject", "subject": "password rotation"}
RETENTION_CONFLICT_7_3 = {"conflict": True, "confidence": 0.91, "reason": "7-year vs 3-year employee data retention", "subject": "employee data retention"}
RETENTION_CONFLICT_7_5 = {"conflict": True, "confidence": 0.88, "reason": "7-year vs 5-year data retention", "subject": "data retention"}
RETENTION_CONFLICT_3_5 = {"conflict": True, "confidence": 0.82, "reason": "3-year vs 5-year data retention", "subject": "data retention"}
NO_CONFLICT = {"conflict": False, "confidence": 0.9, "reason": "Different subjects", "subject": ""}

# Similar-chunks mapping: input text keyword → chunks from OTHER docs.
# When searching from doc1 (Security), return chunks from doc2/doc3.
# When searching from doc2 (HR), return chunks from doc1/doc3.
# The exclude_doc_id filter ensures same-doc chunks are removed.
SIMILAR_MAP_DOC1 = {
    "password": [C4],
    "retained for": [C6, C8],
    "employee data": [C6, C8],
    "vendor data": [],
    "multi-factor": [],
    "badge": [],
    "vendor access": [],
    "quarterly": [],
}

SIMILAR_MAP_DOC2 = {
    "password": [C1],
    "retained for": [C3, C8],
    "employee data": [C3, C8],
    "vendor data": [],
    "multi-factor": [],
    "badge": [],
    "vendor access": [],
    "quarterly": [],
}

SIMILAR_MAP_DOC3 = {
    "password": [C1, C4],
    "retained for": [C3, C6],
    "employee data": [C3, C6],
    "vendor data": [C3, C6],
    "multi-factor": [],
    "badge": [C5],
    "vendor access": [C7],
    "quarterly": [C7],
}

# Combined map that merges all three — the exclude_doc_id filter handles disambiguation
SIMILAR_MAP = {}
for key in set(list(SIMILAR_MAP_DOC1.keys()) + list(SIMILAR_MAP_DOC2.keys()) + list(SIMILAR_MAP_DOC3.keys())):
    SIMILAR_MAP[key] = SIMILAR_MAP_DOC1.get(key, []) + SIMILAR_MAP_DOC2.get(key, []) + SIMILAR_MAP_DOC3.get(key, [])

# LLM pair results: (keyword_a, keyword_b) → result
LLM_PAIRS = {
    ("90 days", "180 days"): PASSWORD_CONFLICT,
    ("90 days", "3 years"): NO_CONFLICT,
    ("90 days", "5 years"): NO_CONFLICT,
    ("7 years", "3 years"): RETENTION_CONFLICT_7_3,
    ("7 years", "5 years"): RETENTION_CONFLICT_7_5,
    ("3 years", "5 years"): RETENTION_CONFLICT_3_5,
    ("multi-factor", "badge"): NO_CONFLICT,
    ("multi-factor", "180 days"): NO_CONFLICT,
    ("badge", "quarterly"): NO_CONFLICT,
    ("quarterly", "badge"): NO_CONFLICT,
}


# ════════════════════════════════════════════════════════════════
#  Type 1: Clause-vs-Corpus
# ════════════════════════════════════════════════════════════════

class TestType1_ClauseVsCorpus(unittest.TestCase):

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_password_conflict_detection(self):
        """Password clause from Security vs similar from HR."""
        _print_section('TYPE 1: "Do these password policies conflict?"')
        source = [C1]
        expanded = expand_chunks_for_conflicts(source, access_level=3, max_similar_per_chunk=3, threshold=0.5)
        print(f"Source chunks: {len(source)} ({DOC1_TITLE})")
        print(f"Expanded chunks: {len(expanded)} (+{len(expanded) - len(source)} from other docs)")
        for i, c in enumerate(expanded):
            print(f"  [{i+1}] {c['title']} / {c['clause_ref']}: {c['text'][:60]}...")

        from services.agents.conflict_agent import detect_conflicts_in_chunks
        conflicts = detect_conflicts_in_chunks(expanded)
        print()
        for i, c in enumerate(conflicts, 1):
            _print_conflict(i, c)

        self.assertGreaterEqual(len(conflicts), 1)
        ref_a = conflicts[0]["clause_a"]["clause_ref"]
        ref_b = conflicts[0]["clause_b"]["clause_ref"]
        self.assertIn("5.1", ref_a)
        self.assertIn("3.2", ref_b)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_data_retention_conflict_detection(self):
        """Retention clause from Security vs HR and Vendor."""
        _print_section('TYPE 1: "Are there conflicting data retention rules?"')
        source = [C3]
        expanded = expand_chunks_for_conflicts(source, access_level=3, max_similar_per_chunk=3, threshold=0.5)
        print(f"Source chunks: {len(source)} ({DOC1_TITLE})")
        print(f"Expanded chunks: {len(expanded)} (+{len(expanded) - len(source)} from other docs)")
        for i, c in enumerate(expanded):
            print(f"  [{i+1}] {c['title']} / {c['clause_ref']}: {c['text'][:60]}...")

        from services.agents.conflict_agent import detect_conflicts_in_chunks
        conflicts = detect_conflicts_in_chunks(expanded)
        print()
        for i, c in enumerate(conflicts, 1):
            _print_conflict(i, c)

        self.assertGreaterEqual(len(conflicts), 2)
        texts = " ".join(c["clause_a"]["text"] + c["clause_b"]["text"] for c in conflicts)
        self.assertIn("7", texts)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_no_conflict_single_clause(self):
        """MFA clause — only one clause about this topic, no conflict."""
        _print_section('TYPE 1: "Is there a conflict about MFA requirements?"')
        source = [C2]
        expanded = expand_chunks_for_conflicts(source, access_level=3, max_similar_per_chunk=3, threshold=0.5)
        print(f"Source chunks: {len(source)} ({DOC1_TITLE})")
        print(f"Expanded chunks: {len(expanded)} (+{len(expanded) - len(source)} from other docs)")

        from services.agents.conflict_agent import detect_conflicts_in_chunks
        conflicts = detect_conflicts_in_chunks(expanded)
        print()
        if conflicts:
            for i, c in enumerate(conflicts, 1):
                _print_conflict(i, c)
        else:
            print("  No conflicts found.")
        print(f"\n✅ PASSED — {len(conflicts)} conflict(s) detected")
        self.assertEqual(len(conflicts), 0)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_no_conflict_different_subjects(self):
        """Vendor review vs badge access — different subjects, no conflict."""
        _print_section('TYPE 1: "Do vendor access and badge rules conflict?"')
        source = [C7]
        expanded = expand_chunks_for_conflicts(source, access_level=3, max_similar_per_chunk=3, threshold=0.5)
        print(f"Source chunks: {len(source)} ({DOC3_TITLE})")
        print(f"Expanded chunks: {len(expanded)} (+{len(expanded) - len(source)} from other docs)")
        for i, c in enumerate(expanded):
            print(f"  [{i+1}] {c['title']} / {c['clause_ref']}: {c['text'][:60]}...")

        from services.agents.conflict_agent import detect_conflicts_in_chunks
        conflicts = detect_conflicts_in_chunks(expanded)
        print()
        if conflicts:
            for i, c in enumerate(conflicts, 1):
                _print_conflict(i, c)
        else:
            print("  No conflicts found.")
        print(f"\n✅ PASSED — {len(conflicts)} conflict(s) detected")
        self.assertEqual(len(conflicts), 0)


# ════════════════════════════════════════════════════════════════
#  Type 2: Document-vs-Document
# ════════════════════════════════════════════════════════════════

class TestType2_DocumentVsDocument(unittest.TestCase):

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_security_vs_hr(self):
        """IT Security Policy vs HR Onboarding — 2 known conflicts."""
        _print_section("TYPE 2: IT Security Policy vs HR Onboarding Policy")
        chunks_by_doc = {
            DOC1: [C1, C2, C3],
            DOC2: [C4, C5, C6],
        }
        result = compare_document_chunks(chunks_by_doc, access_level=3, similarity_threshold=0.5, max_llm_calls=10)

        for pair in result["doc_pairs"]:
            a = pair["doc_a"]
            b = pair["doc_b"]
            print(f"  {a['title']} vs {b['title']}:")
            print(f"    Candidates: {pair['total_candidate_count']}")
            print(f"    Conflicts: {len(pair['conflicts'])}")
            for i, c in enumerate(pair["conflicts"], 1):
                _print_conflict(i, c)

        self.assertEqual(len(result["doc_pairs"]), 1)
        self.assertGreaterEqual(result["total_conflicts"], 2)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_security_vs_vendor(self):
        """IT Security Policy vs Vendor Management — 1 conflict (data retention)."""
        _print_section("TYPE 2: IT Security Policy vs Vendor Management Policy")
        chunks_by_doc = {
            DOC1: [C1, C2, C3],
            DOC3: [C7, C8],
        }
        result = compare_document_chunks(chunks_by_doc, access_level=3, similarity_threshold=0.5, max_llm_calls=10)

        for pair in result["doc_pairs"]:
            a = pair["doc_a"]
            b = pair["doc_b"]
            print(f"  {a['title']} vs {b['title']}:")
            print(f"    Candidates: {pair['total_candidate_count']}")
            print(f"    Conflicts: {len(pair['conflicts'])}")
            for i, c in enumerate(pair["conflicts"], 1):
                _print_conflict(i, c)

        self.assertEqual(len(result["doc_pairs"]), 1)
        self.assertGreaterEqual(result["total_conflicts"], 1)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_hr_vs_vendor(self):
        """HR Onboarding vs Vendor Management — 1 conflict (data retention)."""
        _print_section("TYPE 2: HR Onboarding Policy vs Vendor Management Policy")
        chunks_by_doc = {
            DOC2: [C4, C5, C6],
            DOC3: [C7, C8],
        }
        result = compare_document_chunks(chunks_by_doc, access_level=3, similarity_threshold=0.5, max_llm_calls=10)

        for pair in result["doc_pairs"]:
            a = pair["doc_a"]
            b = pair["doc_b"]
            print(f"  {a['title']} vs {b['title']}:")
            print(f"    Candidates: {pair['total_candidate_count']}")
            print(f"    Conflicts: {len(pair['conflicts'])}")
            for i, c in enumerate(pair["conflicts"], 1):
                _print_conflict(i, c)

        self.assertEqual(len(result["doc_pairs"]), 1)
        self.assertGreaterEqual(result["total_conflicts"], 1)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_three_way_comparison(self):
        """All three documents compared at once."""
        _print_section("TYPE 2: Three-way comparison (Security vs HR vs Vendor)")
        chunks_by_doc = {
            DOC1: [C1, C2, C3],
            DOC2: [C4, C5, C6],
            DOC3: [C7, C8],
        }
        result = compare_document_chunks(chunks_by_doc, access_level=3, similarity_threshold=0.5, max_llm_calls=15)

        print(f"  Total doc pairs: {len(result['doc_pairs'])}")
        print(f"  Total conflicts: {result['total_conflicts']}")
        print(f"  LLM calls used: {result['total_llm_calls']}")
        print(f"  Truncated: {result['truncated']}")
        print()
        for pair in result["doc_pairs"]:
            a = pair["doc_a"]
            b = pair["doc_b"]
            print(f"  {a['title']} vs {b['title']}: {len(pair['conflicts'])} conflict(s)")
            for i, c in enumerate(pair["conflicts"], 1):
                _print_conflict(i, c)

        self.assertEqual(len(result["doc_pairs"]), 3)
        self.assertGreaterEqual(result["total_conflicts"], 3)


# ════════════════════════════════════════════════════════════════
#  Type 2b: Document-vs-Corpus
# ════════════════════════════════════════════════════════════════

class TestType3_DocumentVsCorpus(unittest.TestCase):

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_security_policy_conflicts(self):
        """IT Security Policy — find all documents that conflict with it."""
        _print_section("TYPE 2b: Which documents conflict with IT Security Policy?")
        result = detect_conflicting_documents(
            target_doc_chunks=[C1, C2, C3],
            target_doc_id=DOC1,
            target_doc_title=DOC1_TITLE,
            access_level=3,
            similarity_threshold=0.5,
            max_llm_calls=15,
        )

        print(f"  Total conflicts: {result['total_conflicts']}")
        print(f"  LLM calls used: {result['total_llm_calls']}")
        print(f"  Truncated: {result['truncated']}")
        print()
        for group in result["doc_pairs"]:
            print(f"  Conflicts in: {group['doc_b']['title']} ({group['doc_b']['id']})")
            print(f"    Candidates: {group['total_candidate_count']}")
            for i, c in enumerate(group["conflicts"], 1):
                _print_conflict(i, c)

        self.assertGreaterEqual(len(result["doc_pairs"]), 1)
        all_conflict_texts = " ".join(
            c["clause_a"]["text"] + c["clause_b"]["text"]
            for g in result["doc_pairs"]
            for c in g["conflicts"]
        )
        self.assertIn("180", all_conflict_texts)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_hr_policy_conflicts(self):
        """HR Onboarding Policy — find all documents that conflict with it."""
        _print_section("TYPE 2b: Which documents conflict with HR Onboarding Policy?")
        result = detect_conflicting_documents(
            target_doc_chunks=[C4, C5, C6],
            target_doc_id=DOC2,
            target_doc_title=DOC2_TITLE,
            access_level=3,
            similarity_threshold=0.5,
            max_llm_calls=15,
        )

        print(f"  Total conflicts: {result['total_conflicts']}")
        print(f"  LLM calls used: {result['total_llm_calls']}")
        print()
        for group in result["doc_pairs"]:
            print(f"  Conflicts in: {group['doc_b']['title']} ({group['doc_b']['id']})")
            for i, c in enumerate(group["conflicts"], 1):
                _print_conflict(i, c)

        self.assertGreaterEqual(result["total_conflicts"], 1)

    @mock.patch(NEO4J_PATH, _no_neo4j)
    @mock.patch(LLM_PATH, _fake_llm_by_pair(LLM_PAIRS))
    @mock.patch(SIMILAR_PATH, _fake_similar_by_text(SIMILAR_MAP))
    def test_vendor_policy_conflicts(self):
        """Vendor Management Policy — find all documents that conflict with it."""
        _print_section("TYPE 2b: Which documents conflict with Vendor Management Policy?")
        result = detect_conflicting_documents(
            target_doc_chunks=[C7, C8],
            target_doc_id=DOC3,
            target_doc_title=DOC3_TITLE,
            access_level=3,
            similarity_threshold=0.5,
            max_llm_calls=15,
        )

        print(f"  Total conflicts: {result['total_conflicts']}")
        print(f"  LLM calls used: {result['total_llm_calls']}")
        print()
        for group in result["doc_pairs"]:
            print(f"  Conflicts in: {group['doc_b']['title']} ({group['doc_b']['id']})")
            for i, c in enumerate(group["conflicts"], 1):
                _print_conflict(i, c)

        self.assertGreaterEqual(result["total_conflicts"], 1)


# ════════════════════════════════════════════════════════════════
#  Live Database Tests (require PostgreSQL + ingested docs)
# ════════════════════════════════════════════════════════════════

class TestLiveDatabase(unittest.TestCase):

    def _db_available(self):
        try:
            from packages.shared.db import get_db_session
            from packages.shared.models import Document
            with get_db_session() as session:
                count = session.query(Document).filter(Document.ingestion_status == "ready").count()
                return count > 0
        except Exception:
            return False

    @unittest.skipUnless(
        os.environ.get("RUN_LIVE_TESTS"),
        "Set RUN_LIVE_TESTS=1 to run live database tests"
    )
    def test_live_similar_clauses(self):
        """Find similar clauses for a real chunk from the database."""
        _print_section("LIVE: find_similar_clauses() against real database")
        from packages.shared.db import get_db_session
        from packages.shared.models import Chunk, Document
        from services.api.search import find_similar_clauses

        with get_db_session() as session:
            chunk = (
                session.query(Chunk)
                .join(Document, Chunk.document_id == Document.id)
                .filter(Document.ingestion_status == "ready")
                .first()
            )
            if not chunk:
                self.skipTest("No chunks in database")

            doc = session.query(Document).filter(Document.id == chunk.document_id).first()
            source_title = doc.title if doc else "unknown"
            source_ref = chunk.clause_ref or "no-ref"

            print(f"  Source: {source_title} / {source_ref}")
            print(f"  Text: {chunk.text[:100]}...")
            print()

            similar = find_similar_clauses(
                clause_text=chunk.text,
                access_level=3,
                exclude_doc_id=str(chunk.document_id),
                top_k=5,
                threshold=0.5,
            )

            print(f"  Found {len(similar)} similar clauses:")
            for i, s in enumerate(similar, 1):
                print(f"    [{i}] {s.get('title', '?')} / {s.get('clause_ref', '?')}")
                print(f"        Similarity: {s.get('similarity', 0):.3f}")
                print(f"        Text: {s.get('text', '')[:80]}...")
                print()

            self.assertIsInstance(similar, list)
            if similar:
                self.assertLessEqual(len(similar), 5)
                for s in similar:
                    self.assertIn("similarity", s)
                    self.assertGreater(s["similarity"], 0.0)

    @unittest.skipUnless(
        os.environ.get("RUN_LIVE_TESTS"),
        "Set RUN_LIVE_TESTS=1 to run live database tests"
    )
    def test_live_document_conflicts(self):
        """Run document-vs-document conflict detection on real documents."""
        _print_section("LIVE: compare_document_chunks() against real database")
        from packages.shared.db import get_db_session
        from packages.shared.models import Document, Chunk
        from services.agents.conflict_agent import compare_document_chunks

        with get_db_session() as session:
            docs = (
                session.query(Document)
                .filter(Document.ingestion_status == "ready")
                .limit(2)
                .all()
            )
            if len(docs) < 2:
                self.skipTest("Need at least 2 ingested documents")

            doc_a, doc_b = docs[0], docs[1]
            print(f"  Doc A: {doc_a.title}")
            print(f"  Doc B: {doc_b.title}")
            print()

            chunks_a = (
                session.query(Chunk).filter(Chunk.document_id == doc_a.id)
                .order_by(Chunk.section_path).all()
            )
            chunks_b = (
                session.query(Chunk).filter(Chunk.document_id == doc_b.id)
                .order_by(Chunk.section_path).all()
            )
            print(f"  Chunks in A: {len(chunks_a)}")
            print(f"  Chunks in B: {len(chunks_b)}")
            print()

            title_a = doc_a.title
            title_b = doc_b.title
            chunks_by_doc = {
                str(doc_a.id): [
                    {"id": str(c.id), "text": c.text, "document_id": str(c.document_id),
                     "clause_ref": c.clause_ref, "section_path": c.section_path,
                     "title": title_a, "similarity": 0.0}
                    for c in chunks_a
                ],
                str(doc_b.id): [
                    {"id": str(c.id), "text": c.text, "document_id": str(c.document_id),
                     "clause_ref": c.clause_ref, "section_path": c.section_path,
                     "title": title_b, "similarity": 0.0}
                    for c in chunks_b
                ],
            }

        result = compare_document_chunks(
            chunks_by_doc, access_level=3,
            similarity_threshold=0.5, max_llm_calls=5,
        )

        print(f"  Doc pairs: {len(result['doc_pairs'])}")
        print(f"  Total conflicts: {result['total_conflicts']}")
        print(f"  Total candidates: {result['total_candidates']}")
        print(f"  LLM calls: {result['total_llm_calls']}")
        print(f"  Truncated: {result['truncated']}")
        print()

        for pair in result["doc_pairs"]:
            a = pair["doc_a"]
            b = pair["doc_b"]
            print(f"  {a['title']} vs {b['title']}:")
            print(f"    Candidates: {pair['total_candidate_count']}")
            print(f"    Conflicts: {len(pair['conflicts'])}")
            for i, c in enumerate(pair["conflicts"], 1):
                _print_conflict(i, c)

        self.assertIsInstance(result, dict)
        self.assertIn("doc_pairs", result)
        self.assertIn("total_conflicts", result)


# ════════════════════════════════════════════════════════════════
#  Runner
# ════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 70)
    print("  CONFLICT INTEGRATION TESTS")
    print("  Three conflict types × realistic policy clauses")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for cls in [
        TestType1_ClauseVsCorpus,
        TestType2_DocumentVsDocument,
        TestType3_DocumentVsCorpus,
        TestLiveDatabase,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print("  ALL TESTS PASSED")
    else:
        print(f"  FAILURES: {len(result.failures)}  ERRORS: {len(result.errors)}")
    print("=" * 70)

    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
