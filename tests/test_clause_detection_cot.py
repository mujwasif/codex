#!/usr/bin/env python3
"""
Offline tests for the chain-of-thought clause detection parser.
No LLM server required - exercises _extract_json_array, _normalize_clause,
truncation repair, and multi-fence handling.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.ingestion.clause_detector import (
    _extract_json_array,
    _last_balanced_array,
    _normalize_clause,
    _repair_truncated_array,
    detect_clauses_llm,
)


def test_extract_fenced_json():
    content = (
        "I will identify each rule.\n"
        "The first rule says access requires approval.\n"
        "```json\n"
        '["Access requires approval.", "Remote access without approval is prohibited."]\n'
        "```"
    )
    extracted = _extract_json_array(content)
    assert json.loads(extracted) == [
        "Access requires approval.",
        "Remote access without approval is prohibited.",
    ]


def test_extract_last_fence_wins():
    content = (
        "```json\n"
        '["Draft list"]\n'
        "```\n"
        "Let me reconsider.\n"
        "```json\n"
        '["Final clause A", "Final clause B"]\n'
        "```"
    )
    extracted = _extract_json_array(content)
    assert json.loads(extracted) == ["Final clause A", "Final clause B"]


def test_extract_brackets_in_prose():
    content = (
        "Rule [5.1] covers network access. Rule 5.2 (noted in [5.1]) says use MFA.\n"
        "```json\n"
        '["Use MFA for all network access."]\n'
        "```"
    )
    extracted = _extract_json_array(content)
    assert json.loads(extracted) == ["Use MFA for all network access."]


def test_extract_truncated_array():
    content = 'The rules are as follows:\n```json\n["One clause.", "Two cla'
    repaired = _repair_truncated_array('["One clause.", "Two cla')
    assert repaired == ""


def test_extract_unfenced_array():
    content = 'I split this into three rules:\n["Rule A", "Rule B", "Rule C"]\nand that is all.'
    extracted = _extract_json_array(content)
    assert json.loads(extracted) == ["Rule A", "Rule B", "Rule C"]


def test_extract_invalid_returns_empty():
    content = "No array here, just prose without any JSON."
    assert _extract_json_array(content) == ""


def test_last_balanced_array():
    content = 'prose [not json] then ["a", "b [inner]"] trailing'
    assert _last_balanced_array(content) == '["a", "b [inner]"]'


def test_normalize_strips_rule_prefix():
    assert _normalize_clause("Rule 3: Passwords must be 12 characters.") == "Passwords must be 12 characters."
    assert _normalize_clause("3. Backups run nightly.") == "Backups run nightly."
    assert _normalize_clause("Rule 12 - Use encryption.") == "Use encryption."
    assert _normalize_clause("No prefix here.") == "No prefix here."


def test_detect_clauses_llm_offline_fallback():
    # No server running -> falls back to the original text as a single clause.
    clauses = detect_clauses_llm("A single requirement.", llama_url="http://localhost:1/v1/chat/completions")
    assert clauses == ["A single requirement."]


def main():
    tests = [
        test_extract_fenced_json,
        test_extract_last_fence_wins,
        test_extract_brackets_in_prose,
        test_extract_truncated_array,
        test_extract_unfenced_array,
        test_extract_invalid_returns_empty,
        test_last_balanced_array,
        test_normalize_strips_rule_prefix,
        test_detect_clauses_llm_offline_fallback,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {t.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        return 1
    print("\nAll CoT clause-detection tests PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
