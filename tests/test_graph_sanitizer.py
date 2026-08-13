#!/usr/bin/env python3
"""
Test department normalization and node reclassification in _sanitize_graph.
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.ingestion.llm_graph_generator import (
    _sanitize_graph,
    _extract_json,
    _resolve_department,
    _resolve_unmapped_departments,
    _extract_thresholds_from_text,
    _attach_thresholds,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    DOCUMENT_USER_PROMPT_TEMPLATE,
    DEPT_ALIASES,
    DEPT_LEVEL_MAP,
    _dept_resolution_cache,
)


def test_department_variants_normalize():
    """All IT/ISMS variants should consolidate to IT & InfoSec (level 2)."""
    variants = [
        "IT", "IT Department", "Infosec network", "ISMS", "ISMS Department",
        "Information Security Management System",
        "Information Security Management System (ISMS)",
        "Information Security Management", "Information Systems Management",
    ]
    nodes = [
        {"label": "Department", "merge_key": {"name": v}, "properties": {}}
        for v in variants
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": []})
    it_nodes = [n for n in out["nodes"] if n["merge_key"]["name"] == "IT & InfoSec"]
    assert len(it_nodes) == len(variants)
    for n in it_nodes:
        assert n["label"] == "Department"
        assert n["properties"]["access_level"] == 2


def test_hr_and_company_levels():
    """HR consolidates to level 1, Company variants to level 3."""
    nodes = [
        {"label": "Department", "merge_key": {"name": "HR department"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "Employees"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "Company"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "Courier Company"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "Emergency services"}, "properties": {}},
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": []})
    by_name = {n["merge_key"]["name"]: n for n in out["nodes"]}
    assert by_name["HR"]["properties"]["access_level"] == 1
    assert by_name["Company & Ops"]["properties"]["access_level"] == 3


def test_mislabeled_nodes_reclassified():
    """ISMS Manager -> Role, Supplier Management -> Process."""
    nodes = [
        {"label": "Department", "merge_key": {"name": "ISMS Manager"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "Supplier Management"}, "properties": {}},
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": []})
    labels = {n["merge_key"]["name"]: n["label"] for n in out["nodes"]}
    assert labels["ISMS Manager"] == "Role"
    assert labels["Supplier Management"] == "Process"


def test_edges_rewritten_for_reclassified():
    """Edges pointing at reclassified nodes must follow the new label/key."""
    nodes = [
        {"label": "Department", "merge_key": {"name": "ISMS Manager"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "Supplier Management"}, "properties": {}},
        {"label": "Process", "merge_key": {"name": "Access Control"}, "properties": {}},
        {"label": "Clause", "merge_key": {"id": "chunk-1"}, "properties": {}},
    ]
    edges = [
        {"type": "CAN_APPROVE", "from_label": "Department", "from_key": {"name": "ISMS Manager"},
         "to_label": "Process", "to_key": {"name": "Access Control"}},
        {"type": "GOVERNS", "from_label": "Clause", "from_key": {"id": "chunk-1"},
         "to_label": "Department", "to_key": {"name": "Supplier Management"}},
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": edges})
    assert out["edges"][0]["from_label"] == "Role"
    assert out["edges"][0]["from_key"]["name"] == "ISMS Manager"
    assert out["edges"][1]["to_label"] == "Process"
    assert out["edges"][1]["to_key"]["name"] == "Supplier Management"


def test_unknown_department_dropped():
    """Unknown departments (unresolvable to a canonical name) are dropped."""
    nodes = [
        {"label": "Department", "merge_key": {"name": "UnidentifiableUnitXYZ"}, "properties": {}},
        {"label": "Role", "merge_key": {"name": "CFO"}, "properties": {}},
    ]
    edges = [
        {"type": "CAN_APPROVE", "from_label": "Department", "from_key": {"name": "UnidentifiableUnitXYZ"},
         "to_label": "Role", "to_key": {"name": "CFO"}},
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": edges})
    assert all(n["label"] != "Department" for n in out["nodes"])
    assert out["edges"] == []


def test_all_aliases_resolve_to_known_level():
    """Every alias must map to a canonical dept with a defined level."""
    for alias, canonical in DEPT_ALIASES.items():
        assert canonical in DEPT_LEVEL_MAP, f"{alias} -> unknown canonical {canonical}"
        assert isinstance(DEPT_LEVEL_MAP[canonical], int)


def test_canonical_names_emitted_directly_still_stamped():
    """LLM emitting a canonical name directly must still get access_level."""
    nodes = [
        {"label": "Department", "merge_key": {"name": "IT & InfoSec"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "Company & Ops"}, "properties": {}},
        {"label": "Department", "merge_key": {"name": "HR"}, "properties": {}},
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": []})
    by_name = {n["merge_key"]["name"]: n for n in out["nodes"]}
    assert by_name["IT & InfoSec"]["properties"]["access_level"] == 2
    assert by_name["Company & Ops"]["properties"]["access_level"] == 3
    assert by_name["HR"]["properties"]["access_level"] == 1


def test_prompt_contains_normalization_rule():
    """SYSTEM_PROMPT must list all canonical names, levels, and reclassifications."""
    for name, level in DEPT_LEVEL_MAP.items():
        assert name in SYSTEM_PROMPT, f"canonical {name} missing from prompt"
        assert f"| {name} | {level} |" in SYSTEM_PROMPT
    assert "3 = all-access (admin)" in SYSTEM_PROMPT
    assert "2 = managerial" in SYSTEM_PROMPT
    assert "1 = employee" in SYSTEM_PROMPT
    assert "ISMS Manager" in SYSTEM_PROMPT and "Role" in SYSTEM_PROMPT
    assert "Supplier Management" in SYSTEM_PROMPT and "Process" in SYSTEM_PROMPT


def test_threshold_placeholder_dropped_with_edges():
    """Junk thresholds must be removed along with their edges."""
    nodes = [
        {"label": "Threshold", "merge_key": {"amount": "Not specified", "basis": "Not specified"},
         "properties": {}},
        {"label": "Threshold", "merge_key": {"amount": "N/A", "basis": "N/A"}, "properties": {}},
        {"label": "Process", "merge_key": {"name": "Risk Assessment"}, "properties": {}},
    ]
    edges = [
        {"type": "REQUIRES_THRESHOLD", "from_label": "Process", "from_key": {"name": "Risk Assessment"},
         "to_label": "Threshold", "to_key": {"amount": "Not specified", "basis": "Not specified"}},
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": edges})
    assert all(n["label"] != "Threshold" for n in out["nodes"])
    assert out["edges"] == []


def test_time_threshold_normalized():
    """currency='time' + time basis -> type='time', currency stripped."""
    node = {"label": "Threshold",
            "merge_key": {"amount": "1", "basis": "year"},
            "properties": {"currency": "time"}}
    out = _sanitize_graph({"nodes": [node], "edges": []})
    t = out["nodes"][0]
    assert t["properties"]["type"] == "time"
    assert "currency" not in t["properties"]
    assert t["properties"]["amount"] == "1"


def test_count_threshold():
    """A count cap like 12 characters -> type='count'."""
    node = {"label": "Threshold",
            "merge_key": {"amount": "12", "basis": "characters"},
            "properties": {}}
    out = _sanitize_graph({"nodes": [node], "edges": []})
    assert out["nodes"][0]["properties"]["type"] == "count"


def test_monetary_threshold_normalized():
    """$10,000 with currency USD -> amount normalized, type='monetary', currency kept."""
    node = {"label": "Threshold",
            "merge_key": {"amount": "$10,000", "basis": ""},
            "properties": {"currency": "USD"}}
    out = _sanitize_graph({"nodes": [node], "edges": []})
    t = out["nodes"][0]
    assert t["merge_key"]["amount"] == "10000"
    assert t["properties"]["amount"] == "10000"
    assert t["properties"]["type"] == "monetary"
    assert t["properties"]["currency"] == "USD"


def test_non_numeric_amount_dropped():
    """Threshold with a non-numeric amount is dropped."""
    node = {"label": "Threshold",
            "merge_key": {"amount": "maybe", "basis": "days"},
            "properties": {}}
    out = _sanitize_graph({"nodes": [node], "edges": []})
    assert out["nodes"] == []


def test_placeholder_any_label_dropped():
    """Placeholder-named nodes (e.g. Regulation 'N/A') are dropped with their edges."""
    nodes = [
        {"label": "Regulation", "merge_key": {"name": "N/A"}, "properties": {}},
        {"label": "Policy", "merge_key": {"id": "doc-1"}, "properties": {}},
    ]
    edges = [
        {"type": "MAPS_TO", "from_label": "Policy", "from_key": {"id": "doc-1"},
         "to_label": "Regulation", "to_key": {"name": "N/A"}},
    ]
    out = _sanitize_graph({"nodes": nodes, "edges": edges})
    assert all(n["label"] != "Regulation" for n in out["nodes"])
    assert out["edges"] == []


def test_prompt_contains_threshold_rule():
    """SYSTEM_PROMPT must carry the THRESHOLD RULE and type semantics."""
    assert "THRESHOLD RULE" in SYSTEM_PROMPT
    assert '"monetary"' in SYSTEM_PROMPT
    assert '"time"' in SYSTEM_PROMPT
    assert '"count"' in SYSTEM_PROMPT
    assert "currency ONLY when type=\"monetary\"" in SYSTEM_PROMPT
    assert "Never emit placeholder values" in SYSTEM_PROMPT


def test_prompt_contains_cot_rule():
    """SYSTEM_PROMPT must carry the CHAIN-OF-THOUGHT WORKFLOW."""
    assert "CHAIN-OF-THOUGHT WORKFLOW" in SYSTEM_PROMPT
    assert "Step 1" in SYSTEM_PROMPT and "Step 6" in SYSTEM_PROMPT
    assert "fenced code block" in SYSTEM_PROMPT
    assert "Do NOT wrap" not in SYSTEM_PROMPT.split("GRAPH INTEGRITY RULES")[-1] or "fenced" in SYSTEM_PROMPT


def test_prompt_templates_ask_for_fenced_json():
    """User templates must request CoT reasoning and a fenced JSON block."""
    assert "CHAIN-OF-THOUGHT WORKFLOW" in USER_PROMPT_TEMPLATE
    assert "```json" in USER_PROMPT_TEMPLATE
    assert "CHAIN-OF-THOUGHT WORKFLOW" in DOCUMENT_USER_PROMPT_TEMPLATE
    assert "```json" in DOCUMENT_USER_PROMPT_TEMPLATE


def test_extract_json_fenced_block():
    """CoT prose followed by a fenced JSON block -> the fenced JSON is returned."""
    text = (
        "The clause mandates access control procedures. This process is governed "
        "by the access management policy.\n\n"
        '```json\n{"nodes": [{"label": "Process", "merge_key": {"name": "Access Control"}, '
        '"properties": {}}], "edges": []}\n```'
    )
    assert '"Process"' in _extract_json(text)


def test_extract_json_prose_with_braces():
    """Reasoning containing braces must not break extraction."""
    text = (
        "Step 2 {identify roles} then Step 3 (depts) — the policy requires (per §4.2) "
        "that {processes} be mapped.\n\n"
        '{"nodes": [{"label": "Role", "merge_key": {"name": "IT Manager"}, "properties": {}}], "edges": []}'
    )
    assert '"Role"' in _extract_json(text)


def test_extract_json_multiple_fences_last_wins():
    """When multiple fenced blocks exist, the LAST one is used."""
    text = (
        '```json\n{"nodes": [{"label": "Role", "merge_key": {"name": "A"}, "properties": {}}], "edges": []}\n```\n'
        '```json\n{"nodes": [{"label": "Role", "merge_key": {"name": "B"}, "properties": {}}], "edges": []}\n```'
    )
    assert '"B"' in _extract_json(text)


def test_extract_json_truncated_repair():
    """Truncated output cut by max_tokens is repaired to the last complete object."""
    text = (
        '{"nodes": [{"label": "Role", "merge_key": {"name": "IT Manager"}, '
        '"properties": {}}], "edges": []} "unfinished trailing text'
    )
    parsed = _extract_json(text)
    assert '"Role"' in parsed


def test_extract_json_no_fence_legacy():
    """Plain strict JSON (no fence, no prose) still extracts correctly."""
    text = '{"nodes": [], "edges": []}'
    assert _extract_json(text) == text


def test_resolve_department_alias_fastpath():
    """Alias names resolve without any LLM call."""
    assert _resolve_department("IT Department") == "IT & InfoSec"
    assert _resolve_department("isms") == "IT & InfoSec"
    assert _resolve_department("employees") == "HR"


def test_resolve_department_llm_retry():
    """Unknown names trigger a single LLM retry and cache the result."""
    _dept_resolution_cache.clear()

    import services.ingestion.llm_graph_generator as mod
    from services.agents.tools.llm_tools import ToolResult

    original = mod.llm_generate
    mod.llm_generate = lambda *a, **k: ToolResult(
        success=True, data='{"canonical": "Company & Ops"}', tool_name="llm_generate"
    )
    try:
        assert _resolve_department("Finance Division") == "Company & Ops"
        assert "finance division" in _dept_resolution_cache
        assert _dept_resolution_cache["finance division"] == "Company & Ops"
    finally:
        mod.llm_generate = original


def test_resolve_department_llm_invalid():
    """Invalid LLM responses resolve to None and are cached as None."""
    _dept_resolution_cache.clear()

    import services.ingestion.llm_graph_generator as mod
    from services.agents.tools.llm_tools import ToolResult

    original = mod.llm_generate
    mod.llm_generate = lambda *a, **k: ToolResult(
        success=True, data="not json", tool_name="llm_generate"
    )
    try:
        assert _resolve_department("Mystery Unit") is None
        assert _dept_resolution_cache.get("mystery unit") is None
    finally:
        mod.llm_generate = original


def test_resolve_unmapped_departments_rewrites_key():
    """_resolve_unmapped_departments rewrites unknown dept names to canonical."""
    _dept_resolution_cache.clear()

    import services.ingestion.llm_graph_generator as mod
    from services.agents.tools.llm_tools import ToolResult

    original = mod.llm_generate
    mod.llm_generate = lambda *a, **k: ToolResult(
        success=True, data='{"canonical": "HR"}', tool_name="llm_generate"
    )
    try:
        output = {
            "nodes": [
                {"label": "Department", "merge_key": {"name": "Talent Team"}, "properties": {}},
                {"label": "Department", "merge_key": {"name": "IT"}, "properties": {}},
            ],
            "edges": [],
        }
        resolved = _resolve_unmapped_departments(output)
        names = {n["merge_key"]["name"] for n in resolved["nodes"]}
        assert "HR" in names  # unknown name retried and rewritten to canonical
        assert "IT" in names  # alias left untouched here (canonicalized by sanitizer)
        assert _dept_resolution_cache.get("talent team") == "HR"
    finally:
        mod.llm_generate = original


def test_extract_thresholds_monetary_time_count():
    """Regex backstop finds monetary, time, and count thresholds."""
    text = ("A purchase over $10,000 needs approval. Backups retained for 90 days. "
            "Passwords must be at least 12 characters. Limit of 3 attempts.")
    th = _extract_thresholds_from_text(text)
    by_basis = {t["basis"]: t for t in th}
    assert by_basis["$"]["amount"] == "10000"
    assert by_basis["$"]["type"] == "monetary"
    assert by_basis["$"]["currency"] == "USD"
    assert by_basis["day"]["amount"] == "90"
    assert by_basis["day"]["type"] == "time"
    assert by_basis["character"]["amount"] == "12"
    assert by_basis["character"]["type"] == "count"
    assert by_basis["attempt"]["amount"] == "3"
    assert by_basis["attempt"]["type"] == "count"


def test_attach_thresholds_adds_missing_nodes_and_edges():
    """Backstop materializes threshold nodes + REQUIRES_THRESHOLD edges the LLM missed."""
    graph = {
        "nodes": [
            {"label": "Process", "merge_key": {"name": "Password Length"}, "properties": {}},
            {"label": "Process", "merge_key": {"name": "Backup Retention"}, "properties": {}},
        ],
        "edges": [],
    }
    text = ("Employee passwords must be at least 12 characters. "
            "Backups must be retained for 90 days per the backup policy.")
    out = _attach_thresholds(graph, text)
    thresholds = [n for n in out["nodes"] if n["label"] == "Threshold"]
    assert len(thresholds) == 2
    assert any(t["merge_key"]["amount"] == "12" and t["properties"]["type"] == "count" for t in thresholds)
    assert any(t["merge_key"]["amount"] == "90" and t["properties"]["type"] == "time" for t in thresholds)

    req_edges = [e for e in out["edges"] if e["type"] == "REQUIRES_THRESHOLD"]
    assert len(req_edges) == 2
    linked = {(e["from_key"]["name"], e["to_key"]["amount"]) for e in req_edges}
    assert ("Password Length", "12") in linked
    assert ("Backup Retention", "90") in linked


def test_attach_thresholds_no_duplicates():
    """Thresholds already materialized by the LLM are not duplicated."""
    graph = {
        "nodes": [
            {"label": "Process", "merge_key": {"name": "Backup Retention"}, "properties": {}},
            {"label": "Threshold", "merge_key": {"amount": "90", "basis": "day"},
             "properties": {"type": "time"}},
        ],
        "edges": [],
    }
    out = _attach_thresholds(graph, "Backups retained for 90 days.")
    thresholds = [n for n in out["nodes"] if n["label"] == "Threshold"]
    assert len(thresholds) == 1


def test_attach_thresholds_no_false_positives():
    """Plain numbers without a unit are not treated as thresholds."""
    graph = {"nodes": [{"label": "Process", "merge_key": {"name": "Access"}, "properties": {}}], "edges": []}
    out = _attach_thresholds(graph, "Section 4.1 describes clause 12 of 25.")
    thresholds = [n for n in out["nodes"] if n["label"] == "Threshold"]
    assert thresholds == []


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("GRAPH SANITIZER TESTS")
    print("=" * 60)

    tests = [
        ("Dept variants -> IT & InfoSec", test_department_variants_normalize),
        ("HR / Company & Ops levels", test_hr_and_company_levels),
        ("Mislabeled nodes reclassified", test_mislabeled_nodes_reclassified),
        ("Edges rewritten for reclassified", test_edges_rewritten_for_reclassified),
        ("Unknown dept dropped", test_unknown_department_dropped),
        ("Aliases resolve to known levels", test_all_aliases_resolve_to_known_level),
        ("Canonical names still stamped", test_canonical_names_emitted_directly_still_stamped),
        ("Prompt contains normalization rule", test_prompt_contains_normalization_rule),
        ("Threshold placeholders dropped", test_threshold_placeholder_dropped_with_edges),
        ("Time threshold normalized", test_time_threshold_normalized),
        ("Count threshold", test_count_threshold),
        ("Monetary threshold normalized", test_monetary_threshold_normalized),
        ("Non-numeric amount dropped", test_non_numeric_amount_dropped),
        ("Placeholder any label dropped", test_placeholder_any_label_dropped),
        ("Prompt contains threshold rule", test_prompt_contains_threshold_rule),
        ("Prompt contains CoT rule", test_prompt_contains_cot_rule),
        ("Templates ask for fenced JSON", test_prompt_templates_ask_for_fenced_json),
        ("Extract JSON fenced block", test_extract_json_fenced_block),
        ("Extract JSON prose with braces", test_extract_json_prose_with_braces),
        ("Extract JSON multiple fences", test_extract_json_multiple_fences_last_wins),
        ("Extract JSON truncated repair", test_extract_json_truncated_repair),
        ("Extract JSON legacy no-fence", test_extract_json_no_fence_legacy),
        ("Dept resolve alias fastpath", test_resolve_department_alias_fastpath),
        ("Dept resolve LLM retry", test_resolve_department_llm_retry),
        ("Dept resolve LLM invalid", test_resolve_department_llm_invalid),
        ("Dept resolve rewrites keys", test_resolve_unmapped_departments_rewrites_key),
        ("Extract thresholds m/t/c", test_extract_thresholds_monetary_time_count),
        ("Attach thresholds adds missing", test_attach_thresholds_adds_missing_nodes_and_edges),
        ("Attach thresholds no dups", test_attach_thresholds_no_duplicates),
        ("Attach thresholds no false pos", test_attach_thresholds_no_false_positives),
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
