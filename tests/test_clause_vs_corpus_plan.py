#!/usr/bin/env python3
"""Tests for the query-driven Type 1 clause-vs-corpus workflow."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.agents.conflict_agent import analyze_clause_vs_corpus, build_batches
from services.agents.tools.base import ToolResult


def clause(doc, cid, text, ref, title):
    return {
        "id": cid,
        "document_id": doc,
        "text": text,
        "clause_ref": ref,
        "section_path": "Controls",
        "title": title,
        "similarity": 0.70,
        "version": "1.0",
        "effective_date": "2026-01-01",
        "status": "active",
        "page": 4,
    }


# 7 corpus clauses (will become C1-C7 after query clause C0 is prepended)
CLAUSES = [
    clause("doc1", "c1", "Employees must change passwords every 90 days.", "4.3", "Password Policy"),
    clause("doc2", "c2", "Credentials must be rotated quarterly.", "6.1", "Access Control"),
    clause("doc3", "c3", "Passwords must be changed every 60 days.", "3.2", "Security Baseline"),
    clause("doc4", "c4", "MFA is required for admin access.", "2.1", "Admin Policy"),
    clause("doc5", "c5", "Service accounts require 90-day rotation.", "5.1", "Infrastructure"),
    clause("doc1", "c6", "Password reuse is prohibited for 12 cycles.", "4.4", "Password Policy"),
    clause("doc6", "c7", "Backups must be encrypted at rest.", "7.1", "Backup Policy"),
]


def _make_batch_response(conflicts_data):
    payload = {"conflicts": conflicts_data}
    return ToolResult(
        success=True, data=json.dumps(payload),
        latency_ms=1, tool_name="llm_generate",
    )


class Type1ClauseVsCorpusTests(unittest.TestCase):
    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_clause_vs_clause_conflict(self, find_similar, llm_gen):
        find_similar.return_value = CLAUSES
        # 8 clauses → 3 batches of 3,3,2. Only batch 1 (offset 0) returns conflict.
        # Batch 1 local C1 vs C3 → global C1 vs C3
        llm_gen.return_value = _make_batch_response([
            {"clause_a_id": "C1", "clause_b_id": "C3",
             "reason": "C1 says '90 days' while C3 says '60 days'.", "confidence": 0.95},
        ])

        result = analyze_clause_vs_corpus([], access_level=1, query_text="password conflicts")
        self.assertGreaterEqual(len(result["conflicts"]), 1)
        # C0 = query clause, C1-C7 = corpus = 8 total
        self.assertEqual(result["total_candidates"], len(CLAUSES) + 1)
        self.assertGreaterEqual(result["total_llm_calls"], 1)

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_clause_vs_query_conflict(self, find_similar, llm_gen):
        find_similar.return_value = CLAUSES
        llm_gen.return_value = _make_batch_response([
            {"clause_a_id": "C0", "clause_b_id": "C7",
             "reason": "C0 states 'passwords must be encrypted' while C7 states 'backups must be encrypted'.", "confidence": 0.8},
        ])

        result = analyze_clause_vs_corpus(
            [], access_level=1,
            query_text="Passwords must be encrypted at rest.",
        )
        self.assertEqual(len(result["conflicts"]), 1)
        # C0 is query clause — should NOT be filtered by same-doc guard
        self.assertEqual(result["conflicts"][0]["clause_a"]["document_id"], "__query__")

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_hallucinated_id_filtered(self, find_similar, llm_gen):
        find_similar.return_value = CLAUSES
        llm_gen.return_value = _make_batch_response([
            {"clause_a_id": "C999", "clause_b_id": "C0",
             "reason": "Something.", "confidence": 0.9},
        ])

        result = analyze_clause_vs_corpus([], access_level=1, query_text="test")
        self.assertEqual(len(result["conflicts"]), 0)

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_same_doc_conflict_filtered(self, find_similar, llm_gen):
        find_similar.return_value = CLAUSES
        # C1 and C6 are both from doc1 — local C1 vs C5 in batch 1 → global C1 vs C5
        llm_gen.return_value = _make_batch_response([
            {"clause_a_id": "C1", "clause_b_id": "C5",
             "reason": "Both in Password Policy.", "confidence": 0.8},
        ])

        result = analyze_clause_vs_corpus([], access_level=1, query_text="test")
        self.assertEqual(len(result["conflicts"]), 0)

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_llm_failure(self, find_similar, llm_gen):
        find_similar.return_value = CLAUSES
        llm_gen.return_value = ToolResult(
            success=False, error="timeout", latency_ms=0, tool_name="llm_generate",
        )
        result = analyze_clause_vs_corpus([], access_level=1, query_text="test")
        # 3 batches, all fail
        self.assertEqual(result["total_llm_calls"], 0)
        self.assertEqual(result["failed_calls"], 3)

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_uses_query_text_for_search(self, find_similar, llm_gen):
        find_similar.return_value = CLAUSES
        llm_gen.return_value = _make_batch_response([])
        analyze_clause_vs_corpus([], access_level=1, query_text="VPN access requirements")
        find_similar.assert_called_once_with(
            clause_text="VPN access requirements",
            access_level=1,
            top_k=200,
            threshold=0.57,
        )

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_query_clause_prepended(self, find_similar, llm_gen):
        find_similar.return_value = CLAUSES
        llm_gen.return_value = _make_batch_response([])

        result = analyze_clause_vs_corpus(
            [], access_level=1,
            query_text="Passwords must rotate every 30 days.",
        )
        # C0 should be the query clause
        self.assertEqual(result["total_candidates"], len(CLAUSES) + 1)

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_077_filter(self, find_similar, llm_gen):
        # Mix of clauses within and above 0.77
        high_sim = clause("doc8", "c8", "High similarity clause.", "1.1", "Doc8")
        high_sim["similarity"] = 0.85
        low_sim = clause("doc9", "c9", "Low similarity clause.", "2.1", "Doc9")
        low_sim["similarity"] = 0.60
        find_similar.return_value = [high_sim, low_sim]
        llm_gen.return_value = _make_batch_response([])

        result = analyze_clause_vs_corpus([], access_level=1, query_text="test")
        # C0=query(1.0) + C1=low_sim(0.60) = 2 clauses. high_sim(0.85) filtered out
        self.assertEqual(result["total_candidates"], 2)


class BuildBatchesTests(unittest.TestCase):
    def test_86_clauses(self):
        batches = build_batches(list(range(86)))
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0][1]), 29)

    def test_50_clauses(self):
        batches = build_batches(list(range(50)))
        self.assertEqual(len(batches), 3)

    def test_20_clauses(self):
        batches = build_batches(list(range(20)))
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0][1]), 7)

    def test_5_clauses_always_3_batches(self):
        batches = build_batches(list(range(5)))
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0][1]), 2)
        self.assertEqual(len(batches[1][1]), 2)
        self.assertEqual(len(batches[2][1]), 2)

    def test_3_clauses_always_3_batches(self):
        batches = build_batches(list(range(3)))
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0][1]), 1)

    def test_1_clause(self):
        batches = build_batches([0])
        self.assertEqual(len(batches), 3)
        self.assertEqual(batches[0][1], [0])

    def test_overlap_exists(self):
        batches = build_batches(list(range(50)))
        batch1_end = set(batches[0][1])
        batch2_start = set(batches[1][1][:2])
        self.assertTrue(len(batch1_end & batch2_start) >= 1)

    def test_empty_clauses(self):
        batches = build_batches([])
        self.assertEqual(len(batches), 0)

    def test_100_clauses(self):
        batches = build_batches(list(range(100)))
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0][1]), 34)
        self.assertEqual(len(batches[1][1]), 34)
        self.assertEqual(len(batches[2][1]), 34)


if __name__ == "__main__":
    unittest.main()
