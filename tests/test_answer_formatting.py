#!/usr/bin/env python3
"""
Formatter tests: inline citation markers are stripped from the human-facing answer.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.api.formatters import (
    strip_inline_citations,
    normalize_numbered_lists,
    format_answer,
)


def test_simple_inline_citation():
    ans = "Passwords must not be stored in plain text [Doc: Password Policy, Clause: 9 Reference > Rule 5]."
    out = strip_inline_citations(ans)
    assert "[Doc:" not in out
    assert out == "Passwords must not be stored in plain text."


def test_mid_sentence_marker():
    ans = "Access must be restricted [Doc: Password Policy, Clause: 9 Reference > Rule 10] to authorized staff."
    out = strip_inline_citations(ans)
    assert "[Doc:" not in out
    assert out == "Access must be restricted to authorized staff."


def test_combined_clause_marker():
    ans = "Reset must be secure [Doc: Password Policy, Clause: 9 Reference > Rule 10 and Rule 9]."
    out = strip_inline_citations(ans)
    assert "[Doc:" not in out
    assert out == "Reset must be secure."


def test_doc_only_marker():
    ans = "See [Doc: Password Policy] for details."
    out = strip_inline_citations(ans)
    assert out == "See for details."


def test_clause_only_marker():
    ans = "See [Clause: 4.2]."
    out = strip_inline_citations(ans)
    assert out == "See."


def test_multiple_markers():
    ans = ("A [Doc: P, Clause: 1]. "
           "B [Doc: P, Clause: 2] and C [Doc: P, Clause: 3].")
    out = strip_inline_citations(ans)
    assert out == "A. B and C."


def test_no_marker_passthrough():
    ans = "Nothing to strip here."
    assert strip_inline_citations(ans) == "Nothing to strip here."


def test_empty():
    assert strip_inline_citations("") == ""
    assert strip_inline_citations(None) is None


def test_double_space_cleanup():
    ans = "One  claim  [Doc: P, Clause: 1]  two."
    out = strip_inline_citations(ans)
    assert "  " not in out
    assert out == "One claim two."


# ── normalize_numbered_lists ─────────────────────────────────────────

def test_run_on_numbered_list():
    text = ("The password change policies outline these: 1. **Maximum Lifetime**: "
            "change every 90 days. 2. Automatic Rotation: must be automated. "
            "3. Consistent Enforcement: apply across all systems.")
    out = normalize_numbered_lists(text)
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].startswith("The password change policies")
    assert lines[1].startswith("1. **Maximum Lifetime**:")
    assert lines[2].startswith("2. **Automatic Rotation**:")
    assert lines[3].startswith("3. **Consistent Enforcement**:")


def test_labels_force_bolded():
    """Labels get bold even if the model only bolded the first one."""
    text = "1. **Maximum**: a. 2. Plain: b."
    out = normalize_numbered_lists(text)
    assert "**Plain**" in out
    assert "**Maximum**" in out


def test_bullets_normalized():
    text = "- **First**: one - **Second**: two"
    out = normalize_numbered_lists(text)
    assert out.startswith("- **First**:") or "\n- **First**:" in out


def test_intro_and_outro_preserved():
    text = "These are the key requirements: 1. A: x. 2. B: y. No conflicts noted."
    out = normalize_numbered_lists(text)
    assert "These are the key requirements" in out
    assert "No conflicts noted." in out


def test_decimal_and_clause_ref_not_triggered():
    """Sentence-internal numbers/clause refs must not become list items."""
    text = "Refer to Clause 5.2 and see section 1.5 of the manual."
    assert normalize_numbered_lists(text) == text.strip()


def test_plain_prose_untouched():
    text = "Passwords must be changed regularly and never shared."
    assert normalize_numbered_lists(text) == text


def test_format_answer_composes_strip_then_normalize():
    raw = ("Passwords must change [Doc: P, Clause: 1]. "
           "Policy: 1. **Max**: 90 days. 2. Rotate: yes.")
    out = format_answer(raw)
    assert "[Doc:" not in out
    assert "**Max**" in out and "**Rotate**" in out


def test_format_answer_empty():
    assert format_answer("") == ""
    assert format_answer(None) is None


def main():
    print("\n" + "=" * 60)
    print("ANSWER FORMATTER TESTS")
    print("=" * 60)
    tests = [
        ("Simple inline citation", test_simple_inline_citation),
        ("Mid-sentence marker", test_mid_sentence_marker),
        ("Combined clause marker", test_combined_clause_marker),
        ("Doc-only marker", test_doc_only_marker),
        ("Clause-only marker", test_clause_only_marker),
        ("Multiple markers", test_multiple_markers),
        ("No-marker passthrough", test_no_marker_passthrough),
        ("Empty input", test_empty),
        ("Double-space cleanup", test_double_space_cleanup),
        ("Run-on numbered list", test_run_on_numbered_list),
        ("Labels force-bolded", test_labels_force_bolded),
        ("Bullets normalized", test_bullets_normalized),
        ("Intro and outro preserved", test_intro_and_outro_preserved),
        ("Decimal/clause ref not triggered", test_decimal_and_clause_ref_not_triggered),
        ("Plain prose untouched", test_plain_prose_untouched),
        ("format_answer composes", test_format_answer_composes_strip_then_normalize),
        ("format_answer empty", test_format_answer_empty),
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

    all_passed = all(p for _, p in results)
    print("=" * 60)
    if all_passed:
        print("🎉 All formatter tests PASSED!")
    else:
        print("⚠️ Some formatter tests FAILED")
    return all_passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
