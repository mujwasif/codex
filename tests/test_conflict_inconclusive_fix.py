#!/usr/bin/env python3
"""
Regression tests: a large candidate set (a many-clause policy vs corpus) must
NOT be reported as "inconclusive" just because the LLM budget is smaller than
the number of candidate pairs. Only a genuine LLM failure should flip
`inconclusive` (and thereby force an "abstained" verdict).
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.agents.conflict_agent import detect_conflicting_documents
from services.agents.tools.base import ToolResult

SIMILAR_MOCK_PATH = "services.api.search.find_similar_clauses"


def _make_chunk(doc_id, chunk_id, text, title=""):
    return {
        "id": chunk_id,
        "document_id": doc_id,
        "text": text,
        "clause_ref": "5.1",
        "section_path": "",
        "title": title,
        "similarity": 0.0,
    }


def _fake_batch_no_conflicts(*args, **kwargs):
    return ToolResult(
        success=True,
        data=json.dumps({"conflicts": []}),
        latency_ms=1,
        tool_name="llm_generate",
    )


class TestType2bLargeCandidateSet(unittest.TestCase):
    """Simulates 'Which other policies conflict with the Software Installation Policy?'."""

    @mock.patch("services.agents.conflict_agent.llm_generate", _fake_batch_no_conflicts)
    @mock.patch(SIMILAR_MOCK_PATH)
    def test_large_candidate_set_not_inconclusive(self, mock_similar):
        target_chunks = [
            _make_chunk("d1", f"s{i}", f"Some policy clause number {i} with enough text to pass the length filter for comparison.")
            for i in range(20)
        ]
        target_docs = {"d2": "Access Policy", "d3": "HR Policy", "d4": "Security Policy"}

        call_index = {"n": 0}

        def _similar(clause_text, **kwargs):
            call_index["n"] += 1
            i = call_index["n"]
            return [
                _make_chunk(doc, f"sim-{i}-{j}", f"Candidate {doc} clause {j} with sufficient text to be compared for conflicts.")
                for j, doc in enumerate(sorted(target_docs))
            ]

        mock_similar.side_effect = _similar

        result = detect_conflicting_documents(
            target_doc_chunks=target_chunks,
            target_doc_id="d1",
            target_doc_title="Software Installation Policy",
            access_level=3,
            similarity_threshold=0.5,
            max_pairs=100,
            max_llm_calls=15,
        )

        self.assertEqual(result["total_candidates"], 60)
        self.assertEqual(result["total_llm_calls"], 3)
        self.assertFalse(result["truncated"], "Large candidate tail must not be reported as truncated")
        self.assertEqual(result["failed_calls"], 0)

    @mock.patch("services.agents.conflict_agent.llm_generate")
    @mock.patch(SIMILAR_MOCK_PATH)
    def test_genuine_llm_failure_is_inconclusive(self, mock_similar, mock_llm):
        mock_llm.return_value = ToolResult(success=False, error="timeout", latency_ms=0, tool_name="llm_generate")
        target_chunks = [_make_chunk("d1", "s1", "Some policy clause with enough text to compare for conflicts.")]
        mock_similar.return_value = [
            _make_chunk("d2", "c1", "Candidate clause with enough text to compare for conflicts."),
        ]
        result = detect_conflicting_documents(
            target_doc_chunks=target_chunks,
            target_doc_id="d1",
            target_doc_title="Software Installation Policy",
            access_level=3,
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(result["failed_calls"], 1)


if __name__ == "__main__":
    unittest.main()
