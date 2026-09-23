#!/usr/bin/env python3
"""
Intent routing tests.

Verifies that:
  1. classify_intent() picks the right intent from the raw question, even when
     the LLM classifier is unavailable (monkeypatched to fail → keyword path).
  2. Both LLM output formats parse: "procedure,0.95" and "intent: procedure
     / confidence: high".
  3. build_conflict_answer / build_risk_answer produce prose for their intents.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.agents.orchestrator import (
    classify_intent,
    QueryContext,
    QueryIntent,
)


def _fail_llm(*args, **kwargs):
    """Fake llm_generate that reports failure (forces the keyword path)."""
    from services.agents.tools.base import ToolResult
    return ToolResult(success=False, error="server down", latency_ms=0, tool_name="llm_generate")


def _llm_returning(text):
    from services.agents.tools.base import ToolResult

    def fake(*args, **kwargs):
        return ToolResult(success=True, data=text, latency_ms=1, tool_name="llm_generate")
    return fake


class TestKeywordFallback(unittest.TestCase):
    @mock.patch("services.agents.orchestrator.llm_generate", _fail_llm)
    def test_approval(self):
        self.assertEqual(classify_intent("who can approve a purchase over 10000")[0], QueryIntent.APPROVAL)

    @mock.patch("services.agents.orchestrator.llm_generate", _fail_llm)
    def test_conflict(self):
        self.assertEqual(classify_intent("do these two policies contradict each other")[0], QueryIntent.CONFLICT)

    @mock.patch("services.agents.orchestrator.llm_generate", _fail_llm)
    def test_compliance(self):
        self.assertEqual(classify_intent("are we iso 27001 compliant")[0], QueryIntent.COMPLIANCE)

    @mock.patch("services.agents.orchestrator.llm_generate", _fail_llm)
    def test_procedure(self):
        self.assertEqual(classify_intent("how do i reset my password")[0], QueryIntent.PROCEDURE)

    @mock.patch("services.agents.orchestrator.llm_generate", _fail_llm)
    def test_general(self):
        self.assertEqual(classify_intent("tell me about the remote work policy")[0], QueryIntent.GENERAL)


class TestLLMFormats(unittest.TestCase):
    @mock.patch("services.agents.orchestrator.llm_generate", _llm_returning("procedure,0.95"))
    def test_comma_format(self):
        intent, conf = classify_intent("how do I onboard")
        self.assertEqual(intent, QueryIntent.PROCEDURE)
        self.assertAlmostEqual(conf, 0.95, places=3)

    @mock.patch("services.agents.orchestrator.llm_generate", _llm_returning("intent: compliance\nconfidence: high"))
    def test_keyword_value_format(self):
        intent, conf = classify_intent("are we gdpr compliant")
        self.assertEqual(intent, QueryIntent.COMPLIANCE)
        self.assertAlmostEqual(conf, 0.9, places=3)

    @mock.patch("services.agents.orchestrator.llm_generate", _llm_returning("intent: conflict\nconfidence: medium"))
    def test_medium_maps_to_0_7(self):
        intent, conf = classify_intent("does this contradict policy")
        self.assertEqual(intent, QueryIntent.CONFLICT)
        self.assertAlmostEqual(conf, 0.7, places=3)


class TestAnswerBuilders(unittest.TestCase):
    def test_conflict_answer(self):
        ctx = QueryContext(question="do policies conflict", user_id="u", raw_question="do policies conflict")
        ctx.conflicts = [
            {"clause_a": "c1", "clause_b": "c2", "reason": "LLM detected contradiction", "source": "llm"}
        ]
        answer = ctx.build_conflict_answer()
        self.assertIn("conflicting", answer)
        self.assertIn("contradiction", answer)

    def test_risk_answer(self):
        ctx = QueryContext(question="are we compliant", user_id="u", raw_question="are we compliant")
        ctx.risk_result = {
            "verdict": "conditional",
            "risk_level": "medium",
            "regulations": ["GDPR", "ISO 27001"],
            "obligations": {"c1": "mandatory"},
            "recommendations": ["Verify all conditions are met before proceeding"],
        }
        answer = ctx.build_risk_answer()
        self.assertIn("conditional", answer)
        self.assertIn("GDPR", answer)
        self.assertIn("mandatory", answer)

    def test_empty_builders(self):
        ctx = QueryContext(question="x", user_id="u", raw_question="x")
        self.assertEqual(ctx.build_conflict_answer(), "")
        self.assertEqual(ctx.build_risk_answer(), "")


def main():
    print("\n" + "=" * 60)
    print("INTENT ROUTING TESTS")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
