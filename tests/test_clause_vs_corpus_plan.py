#!/usr/bin/env python3
"""Focused tests for the structured Clause-vs-Corpus workflow."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.agents.conflict_agent import analyze_clause_vs_corpus


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


class ClauseVsCorpusPlanTests(unittest.TestCase):
    @mock.patch("services.agents.conflict_agent._llm_conflict_check")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_returns_all_validated_conflicts(self, find_similar, llm_check):
        find_similar.side_effect = lambda clause_text, **kwargs: {
            SOURCE["text"]: [CANDIDATES[0]],
            SOURCE_RETENTION["text"]: [CANDIDATES[1]],
            SOURCE_TRAINING["text"]: [CANDIDATES[2]],
        }.get(clause_text, [])

        def semantic(a, b):
            if b["id"] == "c1":
                return {"status": "confirmed_conflict", "subject": "password expiration", "scope_overlap": True,
                        "difference_type": "threshold", "source_requirement": "90 days",
                        "candidate_requirement": "60 days", "confidence": 0.95,
                        "reason": "Both clauses impose incompatible mandatory periods.", "missing_context": []}
            if b["id"] == "c2":
                return {"status": "confirmed_conflict", "subject": "record retention", "scope_overlap": True,
                        "difference_type": "threshold", "source_requirement": "7 years",
                        "candidate_requirement": "3 years", "confidence": 0.91,
                        "reason": "Both clauses impose incompatible retention periods.", "missing_context": []}
            return {"status": "confirmed_conflict", "subject": "training", "scope_overlap": True,
                    "difference_type": "time", "source_requirement": "90 days",
                    "candidate_requirement": "12 months", "confidence": 0.88,
                    "reason": "The requirements specify incompatible periods.", "missing_context": []}

        llm_check.side_effect = semantic
        result = analyze_clause_vs_corpus([SOURCE, SOURCE_RETENTION, SOURCE_TRAINING], access_level=1, maximum_llm_comparisons=15)

        self.assertEqual(len(result["conflicts"]), 3)
        self.assertEqual(result["total_candidates"], 3)
        self.assertEqual(result["checked_candidates"], 3)
        self.assertFalse(result["inconclusive"])
        self.assertEqual(result["source_clauses"][0]["origin"], "query_source")
        self.assertTrue(all(c["clause_b"]["origin"] == "corpus_candidate" for c in result["conflicts"]))

    @mock.patch("services.agents.conflict_agent._llm_conflict_check")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_different_roles_are_complementary(self, find_similar, llm_check):
        candidate = clause("d1", "c1", "Administrators must change passwords every 60 days.", "6.1", "Admin Policy")
        find_similar.return_value = [candidate]
        llm_check.return_value = {
            "status": "confirmed_conflict", "subject": "password expiration", "scope_overlap": True,
            "confidence": 0.95, "reason": "Different scopes", "source_requirement": "90 days",
            "candidate_requirement": "60 days",
        }
        result = analyze_clause_vs_corpus([SOURCE], access_level=1)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["checked_candidates"], 1)

    @mock.patch("services.agents.conflict_agent._llm_conflict_check")
    @mock.patch("services.api.search.find_similar_clauses")
    def test_budget_reports_incomplete_analysis(self, find_similar, llm_check):
        find_similar.return_value = CANDIDATES
        llm_check.return_value = {
            "status": "confirmed_conflict", "subject": "password", "scope_overlap": True,
            "confidence": 0.95, "reason": "Contradictory mandatory periods.",
            "source_requirement": "90 days", "candidate_requirement": "60 days",
        }
        result = analyze_clause_vs_corpus([SOURCE], access_level=1, maximum_llm_comparisons=1)
        self.assertEqual(result["checked_candidates"], 1)
        self.assertEqual(result["unchecked_candidates"], 2)
        self.assertTrue(result["inconclusive"])
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
