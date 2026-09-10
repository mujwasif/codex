#!/usr/bin/env python3
"""
Conflict detection tests.

Tests for the three conflict detection workflows:
  1. Clause-vs-corpus (expand_chunks_for_conflicts)
  2. Document-vs-document (compare_document_chunks)
  3. Document-vs-corpus (detect_conflicting_documents)

Also tests the LLM structured output parser, version conflict detection,
Neo4j conflict lookup, and the build_conflict_answer helper.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.agents.conflict_agent import (
    _detect_version_conflicts,
    _llm_conflict_check,
    _check_neo4j_conflicts,
    _build_conflict_record,
    _evaluate_pairs,
    detect_conflicts_in_chunks,
    expand_chunks_for_conflicts,
    compare_document_chunks,
    detect_conflicting_documents,
)
from services.agents.orchestrator import QueryContext, QueryIntent
from services.agents.tools.base import ToolResult

SIMILAR_MOCK_PATH = "services.api.search.find_similar_clauses"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(doc_id, chunk_id, text, clause_ref="", title=""):
    return {
        "id": chunk_id,
        "document_id": doc_id,
        "text": text,
        "clause_ref": clause_ref,
        "section_path": "",
        "title": title,
        "similarity": 0.0,
    }


def _fake_llm_success(*args, **kwargs):
    return ToolResult(success=True, data='{"conflict": true, "confidence": 0.92, "reason": "90 vs 180 days", "subject": "password"}', latency_ms=1, tool_name="llm_generate")


def _fake_llm_no_conflict(*args, **kwargs):
    return ToolResult(success=True, data='{"conflict": false, "confidence": 0.88, "reason": "different subjects", "subject": "access"}', latency_ms=1, tool_name="llm_generate")


def _fake_llm_binary(*args, **kwargs):
    return ToolResult(success=True, data='yes', latency_ms=1, tool_name="llm_generate")


def _fake_llm_malformed(*args, **kwargs):
    return ToolResult(success=True, data='sorry I cannot determine', latency_ms=1, tool_name="llm_generate")


def _fake_llm_failure(*args, **kwargs):
    return ToolResult(success=False, error="timeout", latency_ms=0, tool_name="llm_generate")


def _fail_neo4j(*args, **kwargs):
    return ToolResult(success=False, error="neo4j down", latency_ms=0, tool_name="neo4j_query")


def _no_similar(*args, **kwargs):
    return []


# ---------------------------------------------------------------------------
# Version conflict detection
# ---------------------------------------------------------------------------

class TestVersionConflicts(unittest.TestCase):

    def test_same_ref_different_docs(self):
        chunks = [
            _make_chunk("doc1", "c1", "Password must be changed every 90 days", "5.1"),
            _make_chunk("doc2", "c2", "Password must be changed every 180 days", "5.1"),
        ]
        conflicts = _detect_version_conflicts(chunks)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["clause_ref"], "5.1")
        self.assertTrue(conflicts[0]["conflict"])

    def test_same_ref_same_doc(self):
        chunks = [
            _make_chunk("doc1", "c1", "Text A", "5.1"),
            _make_chunk("doc1", "c2", "Text B", "5.1"),
        ]
        conflicts = _detect_version_conflicts(chunks)
        self.assertEqual(len(conflicts), 0)

    def test_different_refs(self):
        chunks = [
            _make_chunk("doc1", "c1", "Text A", "5.1"),
            _make_chunk("doc2", "c2", "Text B", "6.2"),
        ]
        conflicts = _detect_version_conflicts(chunks)
        self.assertEqual(len(conflicts), 0)

    def test_no_ref(self):
        chunks = [
            _make_chunk("doc1", "c1", "Some text"),
            _make_chunk("doc2", "c2", "Other text"),
        ]
        conflicts = _detect_version_conflicts(chunks)
        self.assertEqual(len(conflicts), 0)


# ---------------------------------------------------------------------------
# LLM structured output parsing
# ---------------------------------------------------------------------------

class TestLLMStructuredOutput(unittest.TestCase):

    def test_prompt_preserves_clause_context(self):
        captured = {}

        def fake_generate(*args, **kwargs):
            captured["prompt"] = kwargs["user_message"]
            return _fake_llm_no_conflict()

        a = _make_chunk("d1", "c1", "Password changes every 90 days.", "4.3.2", "Password Policy")
        a["section_path"] = "Authentication > Rotation"
        b = _make_chunk("d2", "c2", "Password changes every 60 days.", "6.1", "Access Policy")

        with mock.patch("services.agents.conflict_agent.llm_generate", fake_generate):
            _llm_conflict_check(a, b)

        self.assertIn("Password Policy", captured["prompt"])
        self.assertIn("4.3.2", captured["prompt"])
        self.assertIn("Authentication > Rotation", captured["prompt"])
        self.assertIn("Access Policy", captured["prompt"])

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_success)
    def test_structured_json_conflict(self):
        result = _llm_conflict_check("text A", "text B")
        self.assertTrue(result["conflict"])
        self.assertGreater(result["confidence"], 0.8)
        self.assertIn("90", result["reason"])

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_no_conflict)
    def test_structured_json_no_conflict(self):
        result = _llm_conflict_check("text A", "text B")
        self.assertFalse(result["conflict"])

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_binary)
    def test_binary_yes_fallback(self):
        result = _llm_conflict_check("text A", "text B")
        self.assertTrue(result["conflict"])

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_malformed)
    def test_malformed_output(self):
        result = _llm_conflict_check("text A", "text B")
        self.assertFalse(result["conflict"])
        self.assertEqual(result["confidence"], 0.0)

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_failure)
    def test_llm_failure(self):
        result = _llm_conflict_check("text A", "text B")
        self.assertFalse(result["conflict"])


# ---------------------------------------------------------------------------
# Neo4j conflict lookup
# ---------------------------------------------------------------------------

class TestNeo4jConflicts(unittest.TestCase):

    @mock.patch("services.agents.conflict_agent.neo4j_query", _fail_neo4j)
    def test_neo4j_failure_returns_empty(self):
        conflicts = _check_neo4j_conflicts(["c1"])
        self.assertEqual(conflicts, [])


# ---------------------------------------------------------------------------
# Conflict record building
# ---------------------------------------------------------------------------

class TestConflictRecord(unittest.TestCase):

    def test_build_record(self):
        a = _make_chunk("d1", "c1", "Password 90 days", "5.1", "Security Policy")
        b = _make_chunk("d2", "c2", "Password 180 days", "5.1", "HR Policy")
        record = _build_conflict_record(a, b, 0.85, {"conflict": True, "reason": "mismatch", "source": "llm"})
        self.assertEqual(record["clause_a"]["id"], "c1")
        self.assertEqual(record["clause_b"]["id"], "c2")
        self.assertEqual(record["clause_a"]["document_title"], "Security Policy")
        self.assertEqual(record["similarity"], 0.85)
        self.assertTrue(record["conflict"])
        self.assertEqual(record["source"], "llm")


# ---------------------------------------------------------------------------
# Version conflicts in detect_conflicts_in_chunks
# ---------------------------------------------------------------------------

class TestDetectConflictsInChunks(unittest.TestCase):

    def test_version_conflict_detected(self):
        chunks = [
            _make_chunk("d1", "c1", "Password must be changed every 90 days. " * 5, "5.1"),
            _make_chunk("d2", "c2", "Password must be changed every 180 days. " * 5, "5.1"),
        ]
        conflicts = detect_conflicts_in_chunks(chunks)
        version_conflicts = [c for c in conflicts if c["source"] == "version_check"]
        self.assertEqual(len(version_conflicts), 1)

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_no_conflict)
    @mock.patch(SIMILAR_MOCK_PATH, _no_similar)
    def test_cross_doc_no_conflict(self):
        chunks = [
            _make_chunk("d1", "c1", "Employee badge must be worn at all times in the office. " * 5),
            _make_chunk("d2", "c2", "Remote workers are not required to wear badges. " * 5),
        ]
        conflicts = detect_conflicts_in_chunks(chunks)
        llm_conflicts = [c for c in conflicts if c["source"] == "llm"]
        self.assertEqual(len(llm_conflicts), 0)


# ---------------------------------------------------------------------------
# expand_chunks_for_conflicts (Type 1)
# ---------------------------------------------------------------------------

class TestExpandChunksForConflicts(unittest.TestCase):

    @mock.patch(SIMILAR_MOCK_PATH)
    def test_expands_with_similar(self, mock_similar):
        mock_similar.return_value = [
            _make_chunk("d2", "sim1", "Similar clause text about password policy", "3.1", "Other Policy"),
        ]
        original = [_make_chunk("d1", "c1", "Original clause text about password policy rules", "5.1", "Main Policy")]
        expanded = expand_chunks_for_conflicts(original, access_level=1, max_similar_per_chunk=2, threshold=0.7)
        self.assertEqual(len(expanded), 2)
        ids = {c["id"] for c in expanded}
        self.assertIn("c1", ids)
        self.assertIn("sim1", ids)

    @mock.patch(SIMILAR_MOCK_PATH, _no_similar)
    def test_no_similar_returns_original(self):
        original = [_make_chunk("d1", "c1", "Some clause text that is long enough to pass the filter")]
        expanded = expand_chunks_for_conflicts(original, access_level=1)
        self.assertEqual(len(expanded), 1)

    @mock.patch(SIMILAR_MOCK_PATH)
    def test_deduplicates(self, mock_similar):
        mock_similar.return_value = [
            _make_chunk("d2", "sim1", "Similar text that is definitely long enough", "3.1", "Other"),
        ]
        original = [
            _make_chunk("d1", "c1", "Text A that is definitely long enough to pass"),
            _make_chunk("d1", "c2", "Text B that is also long enough to pass the filter"),
        ]
        expanded = expand_chunks_for_conflicts(original, access_level=1, max_similar_per_chunk=2)
        ids = [c["id"] for c in expanded]
        self.assertEqual(ids.count("sim1"), 1)

    def test_empty_input(self):
        expanded = expand_chunks_for_conflicts([], access_level=1)
        self.assertEqual(expanded, [])


# ---------------------------------------------------------------------------
# compare_document_chunks (Type 2)
# ---------------------------------------------------------------------------

class TestCompareDocumentChunks(unittest.TestCase):

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_success)
    @mock.patch(SIMILAR_MOCK_PATH)
    def test_two_doc_comparison(self, mock_similar):
        mock_similar.return_value = [
            _make_chunk("d2", "c2", "Password must be changed every 180 days", "5.1", "HR Policy"),
        ]
        chunks_by_doc = {
            "d1": [_make_chunk("d1", "c1", "Password must be changed every 90 days", "5.1", "Security Policy")],
            "d2": [_make_chunk("d2", "c2", "Password must be changed every 180 days", "5.1", "HR Policy")],
        }
        result = compare_document_chunks(chunks_by_doc, access_level=1)
        self.assertEqual(len(result["doc_pairs"]), 1)
        self.assertIsInstance(result["doc_pairs"][0]["conflicts"], list)

    def test_insufficient_docs(self):
        chunks_by_doc = {
            "d1": [_make_chunk("d1", "c1", "Some text")],
        }
        result = compare_document_chunks(chunks_by_doc, access_level=1)
        self.assertEqual(len(result["doc_pairs"]), 0)


# ---------------------------------------------------------------------------
# detect_conflicting_documents (Type 2b)
# ---------------------------------------------------------------------------

class TestDetectConflictingDocuments(unittest.TestCase):

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_success)
    @mock.patch(SIMILAR_MOCK_PATH)
    def test_finds_conflicting_doc(self, mock_similar):
        mock_similar.return_value = [
            _make_chunk("d2", "c2", "Password must be changed every 180 days", "5.1", "HR Policy"),
        ]
        target_chunks = [
            _make_chunk("d1", "c1", "Password must be changed every 90 days", "5.1", "Security Policy"),
        ]
        result = detect_conflicting_documents(
            target_doc_chunks=target_chunks,
            target_doc_id="d1",
            target_doc_title="Security Policy",
            access_level=1,
        )
        self.assertEqual(len(result["doc_pairs"]), 1)
        self.assertEqual(result["doc_pairs"][0]["doc_b"]["id"], "d2")

    @mock.patch(SIMILAR_MOCK_PATH, _no_similar)
    def test_no_similar_returns_empty(self):
        target_chunks = [
            _make_chunk("d1", "c1", "Some unique clause text"),
        ]
        result = detect_conflicting_documents(
            target_doc_chunks=target_chunks,
            target_doc_id="d1",
            target_doc_title="Policy",
            access_level=1,
        )
        self.assertEqual(result["total_conflicts"], 0)
        self.assertEqual(result["doc_pairs"], [])


# ---------------------------------------------------------------------------
# expand_chunks_for_conflicts LLM call budget
# ---------------------------------------------------------------------------

class TestLLMBudget(unittest.TestCase):

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_llm_success)
    @mock.patch(SIMILAR_MOCK_PATH)
    def test_max_llm_calls_respected(self, mock_similar):
        mock_similar.return_value = [
            _make_chunk("d2", f"sim{i}", f"Similar clause {i}", "5.1", "Other Policy")
            for i in range(10)
        ]
        original = [
            _make_chunk("d1", f"c{i}", f"Original clause {i} with enough text to pass length filter", "5.1", "Main Policy")
            for i in range(5)
        ]
        expanded = expand_chunks_for_conflicts(original, access_level=1, max_similar_per_chunk=3)
        self.assertLessEqual(len(expanded), 5 + 15)


# ---------------------------------------------------------------------------
# build_conflict_answer with new structured format
# ---------------------------------------------------------------------------

class TestBuildConflictAnswer(unittest.TestCase):

    def test_structured_conflict_answer(self):
        ctx = QueryContext(question="do policies conflict", user_id="u", raw_question="do policies conflict")
        ctx.conflicts = [
            {
                "clause_a": {"id": "c1", "document_id": "d1", "document_title": "Security Policy", "clause_ref": "5.1", "text": "Passwords must be changed every 90 days."},
                "clause_b": {"id": "c2", "document_id": "d2", "document_title": "HR Policy", "clause_ref": "3.2", "text": "Passwords must be changed every 180 days."},
                "similarity": 0.85,
                "conflict": True,
                "reason": "90-day vs 180-day password rotation",
                "source": "llm",
            }
        ]
        answer = ctx.build_conflict_answer()
        self.assertIn("conflict", answer.lower())
        self.assertIn("Security Policy", answer)
        self.assertIn("HR Policy", answer)
        self.assertIn("90-day vs 180-day", answer)
        self.assertIn("90 days", answer)
        self.assertIn("180 days", answer)

    def test_multiple_conflict_answer(self):
        ctx = QueryContext(question="do policies conflict", user_id="u", raw_question="do policies conflict")
        ctx.conflicts = [
            {
                "clause_a": {"id": "c1", "document_id": "d1", "document_title": "Security Policy", "clause_ref": "5.1", "text": "Passwords every 90 days."},
                "clause_b": {"id": "c2", "document_id": "d2", "document_title": "HR Policy", "clause_ref": "3.2", "text": "Passwords every 180 days."},
                "similarity": 0.85, "conflict": True, "reason": "90 vs 180 days", "source": "llm",
            },
            {
                "clause_a": {"id": "c3", "document_id": "d1", "document_title": "Security Policy", "clause_ref": "8.1", "text": "Retain 7 years."},
                "clause_b": {"id": "c4", "document_id": "d2", "document_title": "HR Policy", "clause_ref": "7.3", "text": "Retain 3 years."},
                "similarity": 0.80, "conflict": True, "reason": "7yr vs 3yr", "source": "llm",
            }
        ]
        answer = ctx.build_conflict_answer()
        self.assertIn("2 conflicts", answer)
        self.assertIn("alignment review", answer)

    def test_empty_conflict_answer(self):
        ctx = QueryContext(question="do policies conflict", user_id="u", raw_question="do policies conflict")
        self.assertEqual(ctx.build_conflict_answer(), "")


def main():
    print("\n" + "=" * 60)
    print("CONFLICT TYPE TESTS")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
