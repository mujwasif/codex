#!/usr/bin/env python3
"""Focused tests for the structured Clause-vs-Corpus workflow."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.agents.conflict_agent import analyze_clause_vs_corpus
from services.agents.tools.base import ToolResult


def clause(doc, cid, text, ref, title):
    return {
        "id": cid,
        "document_id": doc,
        "text": text,
        "clause_ref": ref,
        "section_path": "Controls",
        "title": title,
        "similarity": 0.9,
        "version": "1.0",
        "effective_date": "2026-01-01",
        "status": "active",
        "page": 4,
    }


SOURCE = clause("source", "s1", "Employees must change passwords every 90 days.", "4.3.2", "Password Policy")
SOURCE_RETENTION = clause("source", "s2", "Employees must retain records for 7 years.", "8.1", "Records Policy")
SOURCE_TRAINING = clause("source", "s3", "Employees must complete security training every 90 days.", "2.1", "Training Policy")
CANDIDATES = [
    clause("d1", "c1", "Employees must change passwords every 60 days.", "6.1", "Access Policy"),
    clause("d2", "c2", "Employees must retain records for 3 years.", "8.1", "Records Policy"),
    clause("d3", "c3", "Employees must complete security training every 12 months.", "2.2", "Training Policy"),
]


def _make_batch_response(conflicts_data):
    """Build a mock ToolResult for _batch_conflict_check."""
    payload = {"conflicts": conflicts_data}
    return ToolResult(
        success=True,
        data=json.dumps(payload),
        latency_ms=1,
        tool_name="llm_generate",
    )


class ClauseVsCorpusPlanTests(unittest.TestCase):
    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_returns_all_validated_conflicts(self, find_similar, llm_gen):
        find_similar.side_effect = lambda clause_text, **kwargs: {
            SOURCE["text"]: [CANDIDATES[0]],
            SOURCE_RETENTION["text"]: [CANDIDATES[1]],
            SOURCE_TRAINING["text"]: [CANDIDATES[2]],
        }.get(clause_text, [])

        batch_conflicts = [
            {"target_id": "T0", "candidate_id": "C0", "reason": "Both clauses impose incompatible mandatory periods.", "confidence": 0.95},
            {"target_id": "T1", "candidate_id": "C1", "reason": "Both clauses impose incompatible retention periods.", "confidence": 0.91},
            {"target_id": "T2", "candidate_id": "C2", "reason": "The requirements specify incompatible periods.", "confidence": 0.88},
        ]
        llm_gen.return_value = _make_batch_response(batch_conflicts)

        result = analyze_clause_vs_corpus([SOURCE, SOURCE_RETENTION, SOURCE_TRAINING], access_level=1, maximum_llm_comparisons=15)

        self.assertEqual(len(result["conflicts"]), 3)
        self.assertEqual(result["total_candidates"], 3)
        self.assertFalse(result["inconclusive"])
        self.assertEqual(result["source_clauses"][0]["origin"], "query_source")
        self.assertTrue(all(c["clause_b"]["origin"] == "corpus_candidate" for c in result["conflicts"]))

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_different_roles_are_complementary(self, find_similar, llm_gen):
        candidate = clause("d1", "c1", "Administrators must change passwords every 60 days.", "6.1", "Admin Policy")
        find_similar.return_value = [candidate]

        llm_gen.return_value = _make_batch_response([])

        result = analyze_clause_vs_corpus([SOURCE], access_level=1)
        self.assertEqual(result["conflicts"], [])

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_budget_limited_is_not_inconclusive(self, find_similar, llm_gen):
        find_similar.return_value = CANDIDATES

        llm_gen.return_value = _make_batch_response([
            {"target_id": "T0", "candidate_id": "C0", "reason": "Contradictory mandatory periods.", "confidence": 0.95},
        ])

        result = analyze_clause_vs_corpus([SOURCE], access_level=1, maximum_llm_comparisons=1)
        self.assertEqual(result["unchecked_candidates"], 0)
        self.assertEqual(result["evaluated_count"], 1)
        self.assertFalse(result["inconclusive"])
        self.assertFalse(result["truncated"])
        self.assertTrue(result["evaluated_top_k"])

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_genuine_llm_failure_is_inconclusive(self, find_similar, llm_gen):
        find_similar.return_value = CANDIDATES

        llm_gen.return_value = ToolResult(
            success=False, error="timeout", latency_ms=0, tool_name="llm_generate",
        )

        result = analyze_clause_vs_corpus([SOURCE], access_level=1, maximum_llm_comparisons=3)
        self.assertTrue(result["inconclusive"])
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
