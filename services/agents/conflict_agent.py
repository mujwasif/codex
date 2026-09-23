"""
Conflict Detector Agent

Cross-checks clauses for contradictions or version mismatches.
Supports three workflows:
  1. Clause-vs-corpus:  expand retrieved chunks with similar clauses from other docs
  2. Document-vs-document: compare all chunks across 2-3 selected documents
  3. Document-vs-corpus: find which documents conflict with a given document

Uses Neo4j CONFLICTS_WITH when available, version mismatch detection,
and LLM-based pairwise conflict detection (structured JSON output).
"""

import json
import logging
import math
import re
from typing import Dict, Any, List, Optional, Tuple
from services.agents.tools.neo4j_tools import neo4j_query
from services.agents.tools.llm_tools import llm_generate, AGENT_MODEL, INGESTION_MODEL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Neo4j conflict lookup
# ---------------------------------------------------------------------------

def _check_neo4j_conflicts(chunk_ids: List[str]) -> List[Dict[str, Any]]:
    """Check Neo4j for pre-computed CONFLICTS_WITH relationships."""
    conflicts = []
    for cid in chunk_ids:
        result = neo4j_query(
            cypher="""
                MATCH (c:Clause {id: $id})-[:CONFLICTS_WITH]->(other:Clause)
                RETURN other.id AS other_id, other.clause_ref AS other_ref,
                       other.text_ref AS other_text
            """,
            params={"id": cid},
        )
        if result.success:
            for r in result.data:
                conflicts.append({
                    "clause_a_id": cid,
                    "clause_b_id": r["other_id"],
                    "ref_b": r["other_ref"],
                    "source": "neo4j",
                })
    return conflicts


# ---------------------------------------------------------------------------
# Version mismatch detection
# ---------------------------------------------------------------------------

def _detect_version_conflicts(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect version mismatches between clauses sharing the same clause_ref."""
    conflicts = []
    by_ref: Dict[str, List[Dict]] = {}

    for chunk in chunks:
        ref = chunk.get("clause_ref", "")
        if not ref:
            continue
        by_ref.setdefault(ref, []).append(chunk)

    for ref, ref_chunks in by_ref.items():
        if len(ref_chunks) < 2:
            continue
        doc_ids = {c.get("document_id") for c in ref_chunks}
        if len(doc_ids) < 2:
            continue
        for i in range(len(ref_chunks)):
            for j in range(i + 1, len(ref_chunks)):
                a, b = ref_chunks[i], ref_chunks[j]
                conflicts.append({
                    "clause_a_id": a.get("id", ""),
                    "clause_b_id": b.get("id", ""),
                    "clause_ref": ref,
                    "reason": f"Same clause_ref '{ref}' across documents",
                    "source": "version_check",
                    "conflict": True,
                })

    return conflicts


# ---------------------------------------------------------------------------
# LLM structured conflict check
# ---------------------------------------------------------------------------

def _clause_prompt_context(clause: Any, label: str) -> str:
    """Render clause text with its source context for semantic comparison."""
    if isinstance(clause, dict):
        text = str(clause.get("text", ""))[:1200]
        title = clause.get("title", "") or clause.get("document_title", "")
        ref = clause.get("clause_ref", "")
        section = clause.get("section_path", "")
        version = clause.get("version", "")
        metadata = "; ".join(
            part for part in (
                f"document={title}" if title else "",
                f"version={version}" if version else "",
                f"clause={ref}" if ref else "",
                f"section={section}" if section else "",
            ) if part
        )
        return f"{label} ({metadata}):\n{text}" if metadata else f"{label}:\n{text}"
    return f"{label}:\n{str(clause)[:1200]}"


def _llm_conflict_check(text_a: Any, text_b: Any) -> Dict[str, Any]:
    """
    Use LLM to detect if two clauses contradict. Returns structured result:

        {"conflict": bool, "confidence": float, "reason": str, "subject": str}

    On failure or malformed output, returns unchecked=False (not a conflict).
    """
    prompt = f"""Analyze these two policy clauses for a semantic conflict while preserving their source context.

You MUST follow this Chain-of-Thought process:
1. <thinking>
   - Identify the subject/topic of each clause.
   - Determine if both clauses address the same role, process, or asset.
   - Compare: threshold values, obligation levels (must vs should), time periods, scope.
   - Rules for different roles (e.g. Admin vs Employee) are complementary, NOT conflicting.
   - Similar wording alone is not a conflict.
   - If the available text is insufficient, plan to return conflict=false and explain what is missing.
   </thinking>
2. Final Output:
   Respond with the JSON object only.

{_clause_prompt_context(text_a, "CLAUSE A")}

{_clause_prompt_context(text_b, "CLAUSE B")}

Respond with JSON only:
{{"status":"confirmed_conflict|possible_conflict|no_conflict|complementary_scope|superseded", "subject":"topic", "scope_overlap":true, "difference_type":"threshold|modality|scope|time|negation|requirement", "source_requirement":"requirement from clause A (paraphrase OK)", "candidate_requirement":"requirement from clause B (paraphrase OK)", "confidence":0.0, "reason":"brief evidence-backed explanation", "missing_context":[]}}"""

    result = llm_generate(
        model=AGENT_MODEL,
        system_prompt=(
            "You are a policy conflict analyst. Find contradictions between policy clauses. When in doubt, flag it.\n"
            "Respond ONLY with a valid JSON object. No markdown fences, no extra text."
        ),
        user_message=prompt,
        temperature=0.0,
        max_tokens=4096,
        timeout=15.0,
        enable_thinking=True,
    )

    if not result.success:
        return {"conflict": False, "confidence": 0.0, "reason": "LLM call failed", "subject": ""}

    raw = result.data.strip()
    raw = re.sub(r'<thinking>.*?</thinking>', '', raw, flags=re.DOTALL).strip()
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        yes_match = re.search(r"\b(yes|no)\b", raw.lower())
        if yes_match:
            return {
                "conflict": yes_match.group(1) == "yes",
                "confidence": 0.7,
                "reason": "Parsed binary response",
                "subject": "",
            }
        return {"conflict": False, "confidence": 0.0, "reason": "Unparseable LLM output", "subject": ""}

    try:
        parsed = json.loads(json_match.group())
        confidence = float(parsed.get("confidence", 0.5))
        return {
            "conflict": bool(parsed.get("conflict", parsed.get("status") in {"confirmed_conflict", "possible_conflict", "superseded"})),
            "status": parsed.get("status"),
            "confidence": min(max(confidence, 0.0), 1.0),
            "reason": str(parsed.get("reason", "")),
            "subject": str(parsed.get("subject", "")),
            "scope_overlap": parsed.get("scope_overlap", True),
            "difference_type": parsed.get("difference_type", "requirement"),
            "source_requirement": parsed.get("source_requirement", ""),
            "candidate_requirement": parsed.get("candidate_requirement", ""),
            "missing_context": parsed.get("missing_context", []),
        }
    except (json.JSONDecodeError, ValueError):
        return {"conflict": False, "confidence": 0.0, "reason": "Malformed JSON from LLM", "subject": ""}


# ---------------------------------------------------------------------------
# Batch conflict check (single LLM call for all candidates)
# ---------------------------------------------------------------------------

def _batch_conflict_check(
    candidate_pairs: List[Dict[str, Any]],
    target_doc_title: str,
    question: str = "",
    conflict_type: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """
    Analyze all candidate pairs in a single LLM call.

    Pairs are grouped by candidate document so the LLM sees all clauses from
    the same source document together — making cross-clause contradictions
    within a document visible.

    Returns:
        List of conflict records in the same format as _build_conflict_record.
    """
    if not candidate_pairs:
        return []

    MAX_CANDIDATE_CHARS = 2000
    MAX_TARGET_CHARS = 2000
    MAX_PAIR_TOKENS = 12000

    from collections import defaultdict

    def _est_tokens(text: str) -> int:
        return (len(text) + 3) // 4

    prompt_overhead = 250
    running_tokens = prompt_overhead

    # --- Type 1: flat clause list, no pairs ---
    if conflict_type == "type_1":
        clause_list = candidate_pairs  # these are raw clauses, not pairs

        from collections import defaultdict as _dd
        clauses_by_doc = _dd(list)
        for idx, c in enumerate(clause_list):
            doc_id = c.get("document_id", "")
            clauses_by_doc[doc_id].append((idx, c))

        clause_lines = []
        for doc_id, doc_clauses in sorted(clauses_by_doc.items()):
            doc_title = doc_clauses[0][1].get("title", "Unknown")
            for idx, c in doc_clauses:
                text = str(c.get("text", ""))[:MAX_CANDIDATE_CHARS]
                clause_lines.append(f'[id:C{idx}] ({doc_title}) "{text}"')

        paired_block = "\n".join(clause_lines)

        logger.warning(
            "_batch_conflict_check: type=type_1 sending=%d clauses (~%d tokens)",
            len(clause_list), _est_tokens(paired_block) + prompt_overhead,
        )

        system_prompt = (
            "You are a policy conflict analyst.\n"
            "Your task: find pairs of clauses that contradict each other.\n\n"
            "RULES:\n"
            "1. A CONFLICT exists only when two clauses address the SAME rule but impose DIFFERENT requirements.\n"
            "2. Different numeric thresholds for the same rule = CONFLICT.\n"
            '   Example: "Passwords change every 90 days" vs "Passwords change every 180 days" = CONFLICT\n'
            "3. Obligation mismatch for the same rule = CONFLICT.\n"
            '   Example: "Must encrypt data" vs "Must not store unencrypted data" = CONFLICT\n'
            "4. One clause requires something the other forbids = CONFLICT.\n"
            '   Example: "Must retain logs for 6 months" vs "Logs deleted after 90 days" = CONFLICT\n'
            "5. Different populations for the same rule = NOT a conflict.\n"
            '   Example: "Admins must use MFA" vs "Employees must use MFA" = NOT a conflict\n'
            "6. Different topics = NOT a conflict.\n"
            '   Example: "Must encrypt data" vs "Must backup weekly" = NOT a conflict\n\n'
            "OUTPUT RULES:\n"
            "- Only use clause IDs that appear in the list (C0, C1, C2...)\n"
            "- Every reason MUST include:\n"
            "  1. A direct quote from BOTH clauses in quotes\n"
            "  2. The source document and clause reference for each: [Doc: {title}, Clause: {ref}]\n"
            '- Example reason: C0 says "Passwords change every 90 days" [Doc: Password Policy, Clause: 4.2] '
            'while C2 says "Passwords change every 180 days" [Doc: Security Policy, Clause: 3.1] — '
            "contradiction because the password rotation periods differ\n"
            "- Confidence: 0.9+ for clear contradictions, 0.7-0.89 for possible conflicts, below 0.7 for uncertain\n"
            "- If no conflicts found, return an empty list\n"
            "- Respond ONLY with a valid JSON object"
        )

        framing = f'The user is asking: "{question}"\n\n' if question else ""

        prompt = f'''{framing}If the user's question states a specific requirement, it is included as [id:C0] below.

Review every clause against every other clause. Find genuine contradictions.

CLAUSES:
{paired_block}
=== END ===

OUTPUT FORMAT:
{{
  "conflicts": [
    {{
      "clause_a_id": "C0",
      "clause_b_id": "C2",
      "reason": "C0 says \\"exact quote from C0\\" while C2 says \\"exact quote from C2\\" — contradiction because...",
      "confidence": 0.95
    }}
  ]
}}

If no conflicts: {{"conflicts": []}}'''

        result = llm_generate(
            model=AGENT_MODEL,
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=0.0,
            max_tokens=4096,
            timeout=60.0,
            enable_thinking=True,
        )

        if not result.success:
            logger.warning("Batch LLM failed (type_1) — %d clauses unprocessed", len(clause_list))
            return None

        raw = result.data.strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        first_brace = raw.find('{')
        if first_brace > 0:
            raw = raw[first_brace:]

        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return []

        try:
            parsed = json.loads(json_match.group())
        except (json.JSONDecodeError, ValueError):
            conflicts_list = re.findall(r'\{[^{}]*"clause_a_id"[^{}]*\}', raw)
            if not conflicts_list:
                return []
            parsed = {"conflicts": []}
            for obj_str in conflicts_list:
                try:
                    parsed["conflicts"].append(json.loads(obj_str))
                except (json.JSONDecodeError, ValueError):
                    continue

        raw_conflicts = parsed.get("conflicts", [])
        if not isinstance(raw_conflicts, list):
            return []

        logger.warning(
            "_batch_conflict_check: type=type_1 LLM returned %d raw conflicts from %d clauses",
            len(raw_conflicts), len(clause_list),
        )

        return raw_conflicts

    # --- Type 2/2b: pair-based evaluation ---
    pairs_by_cand_doc: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pair in candidate_pairs:
        cand_doc = pair["candidate"].get("document_id", "")
        pairs_by_cand_doc[cand_doc].append(pair)

    sections = []
    target_map: Dict[str, Dict[str, Any]] = {}
    seen_targets: Dict[str, str] = {}
    cand_map: Dict[str, Dict[str, Any]] = {}
    tc_idx = 0
    cc_idx = 0
    dropped_by_budget = 0

    for cand_doc_id, doc_pairs in sorted(pairs_by_cand_doc.items()):
        doc_title = doc_pairs[0]["candidate"].get("title", "") or doc_pairs[0]["candidate"].get("document_title", "Unknown")

        targets_in_doc: Dict[str, List[Dict]] = defaultdict(list)
        for p in doc_pairs:
            src_id = p["source"].get("id", "")
            targets_in_doc[src_id].append(p)

        target_sections = []
        for src_id, tpairs in targets_in_doc.items():
            source = tpairs[0]["source"]

            if src_id not in seen_targets:
                tc_idx += 1
                tc_label = f"T{tc_idx - 1}"
                seen_targets[src_id] = tc_label
                target_map[tc_label] = source
            else:
                tc_label = seen_targets[src_id]

            text = str(source.get("text", ""))[:MAX_TARGET_CHARS]
            ref = source.get("clause_ref", "")
            doc_title_src = source.get("title", "")
            ref_part = f" | {ref}" if ref else ""
            doc_part = f" | {doc_title_src}" if doc_title_src else ""
            tgt_line = f'TARGET {tc_label}{ref_part}{doc_part}\n"{text}"'

            tgt_tokens = _est_tokens(text) + 20
            cand_lines = []
            for p in sorted(tpairs, key=lambda x: x.get("similarity", 0), reverse=True):
                cand = p["candidate"]
                ctext = str(cand.get("text", ""))[:MAX_CANDIDATE_CHARS]
                pair_tokens = tgt_tokens + _est_tokens(ctext) + 50
                if running_tokens + pair_tokens > MAX_PAIR_TOKENS:
                    dropped_by_budget += 1
                    continue
                running_tokens += pair_tokens

                cc_idx += 1
                cc_label = f"C{cc_idx - 1}"
                cand_map[cc_label] = p

                cand_ref = cand.get("clause_ref", "")
                cand_doc_title = cand.get("title", "") or cand.get("document_title", "")
                cand_ref_part = f" | {cand_ref}" if cand_ref else ""
                cand_doc_part = f" | {cand_doc_title}" if cand_doc_title else ""
                cline = f'  MATCH {cc_label}{cand_ref_part}{cand_doc_part}\n  "{ctext}"'
                cand_lines.append(cline)

            if cand_lines:
                target_sections.append(f"{tgt_line}\n" + "\n".join(cand_lines))

        if target_sections:
            sections.append(
                f"=== Document: {doc_title} ({len(target_sections)} targets) ===\n"
                + "\n\n".join(target_sections)
            )

    if dropped_by_budget:
        logger.debug("Token budget: %d pairs dropped, %d kept, ~%d tokens used of %d", dropped_by_budget, cc_idx, running_tokens, MAX_PAIR_TOKENS)

    paired_block = "\n\n".join(sections)

    logger.warning(
        "_batch_conflict_check: type=%s sending=%d pairs (~%d tokens of %d budget), dropped_by_budget=%d",
        conflict_type, cc_idx, running_tokens, MAX_PAIR_TOKENS, dropped_by_budget,
    )

    question_context = f'\nUser question: "{question}"\n\n' if question else ""

    if conflict_type == "type_2b":
        system_prompt = (
            f"You are a policy conflict auditor. You are comparing TARGET clauses from '{target_doc_title}' against MATCH clauses from other documents.\n\n"
            "A CONFLICT exists ONLY when two clauses address the EXACT SAME requirement but impose contradictory rules.\n\n"
            "THINK STEP-BY-STEP for each TARGET:\n"
            "1. Read the TARGET clause. Identify the specific requirement it defines (what rule, what value, what scope).\n"
            "2. For each MATCH clause, identify the same: what requirement, what value, what scope.\n"
            '3. Ask yourself: "Are these two clauses trying to define the same thing but giving different answers?"\n'
            "   - Same thing + different answer = CONFLICT\n"
            "   - Different thing entirely = NOT a conflict\n"
            "   - Same thing + same answer = NOT a conflict\n"
            "4. If you found a conflict, extract the exact quotes.\n\n"
            "Do NOT report a conflict unless you can clearly explain WHY the two requirements contradict each other.\n\n"
            "RULES:\n"
            "1. Every conflict MUST include:\n"
            "   a. A direct word-for-word quote from BOTH clauses\n"
            "   b. The source document and clause reference for each clause\n"
            "2. Use the exact MATCH label (C0, C1...) as candidate_id.\n"
            "3. Include document titles in the reason field.\n"
            f'4. Example reason: TARGET says "Data retained for 7 years" [Doc: {target_doc_title}, Clause: {{ref}}] '
            'while MATCH says "Data deleted after 90 days" [Doc: {candidate_doc_title}, Clause: {ref}] — '
            "contradiction because retention periods conflict\n"
            "5. Confidence: 0.9+ for clear contradictions, 0.7-0.89 for possible conflicts, below 0.7 for uncertain\n"
            "6. If unsure, do NOT report it.\n"
            "7. Respond ONLY with valid JSON. No markdown fences, no code blocks."
        )
        prompt = f"""Think step-by-step. You are comparing TARGET clauses from "{target_doc_title}" against MATCH clauses from other documents.

For each TARGET, identify the specific requirement. For each MATCH, check if it defines the same requirement differently.

Think: "What specific requirement does this TARGET define?" Then for each MATCH: "Does it define the same requirement with a different rule?"

Only report pairs where the answer is clearly YES. Skip pairs about different topics or different populations.

{question_context}CLAUSES:
{paired_block}
=== END ===

Think through each pair before outputting JSON. Output ONLY:
{{
  "conflicts": [
    {{
      "target_id": "T0",
      "candidate_id": "C0",
      "reason": "concise explanation of why these contradict",
      "target_quote": "exact word-for-word from TARGET",
      "candidate_quote": "exact word-for-word from MATCH",
      "confidence": 0.92
    }}
  ]
}}
If no conflicts: {{"conflicts": []}}"""

        result = llm_generate(
            model=AGENT_MODEL,
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=0.0,
            max_tokens=4096,
            timeout=60.0,
            enable_thinking=False,
        )

    elif conflict_type == "type_2":
        system_prompt = (
            "You are a policy conflict analyst. Your job is to find contradictions between clauses from two policy documents.\n\n"
            "A CONFLICT exists ONLY when two clauses address the EXACT SAME requirement but impose contradictory rules.\n\n"
            "THINK STEP-BY-STEP for each TARGET:\n"
            "1. Read the TARGET clause. Identify the specific requirement it defines (what rule, what value, what scope).\n"
            "2. For each MATCH clause, identify the same: what requirement, what value, what scope.\n"
            '3. Ask yourself: "Are these two clauses trying to define the same thing but giving different answers?"\n'
            "   - Same thing + different answer = CONFLICT\n"
            "   - Different thing entirely = NOT a conflict\n"
            "   - Same thing + same answer = NOT a conflict\n"
            "4. If you found a conflict, extract the exact quotes.\n\n"
            "Do NOT report a conflict unless you can clearly explain WHY the two requirements contradict each other.\n\n"
            "RULES:\n"
            "1. Every conflict MUST include:\n"
            "   a. A direct word-for-word quote from BOTH clauses\n"
            "   b. The source document and clause reference for each clause\n"
            "2. Use the exact MATCH label (C0, C1...) as candidate_id.\n"
            "3. Include document titles in the reason field.\n"
            '4. Example reason: TARGET says "Data retained for 7 years" [Doc: Retention Policy, Clause: 2.1] '
            'while MATCH says "Data deleted after 90 days" [Doc: Security Policy, Clause: 3.4] — '
            "contradiction because retention periods conflict\n"
            "5. Confidence: 0.9+ for clear contradictions, 0.7-0.89 for possible conflicts, below 0.7 for uncertain\n"
            "6. If unsure, do NOT report it.\n"
            "7. Respond ONLY with valid JSON. No markdown fences, no code blocks."
        )
        prompt = f"""For each TARGET below, think through its MATCH clauses step by step.

Think: "What specific requirement does this TARGET define?" Then for each MATCH: "Does it define the same requirement with a different rule?"

Only report pairs where the answer is clearly YES. Skip pairs about different topics or different populations.

{question_context}CLAUSES:
{paired_block}
=== END ===

Think through each pair before outputting JSON. Output ONLY:
{{
  "conflicts": [
    {{
      "target_id": "T0",
      "candidate_id": "C0",
      "reason": "concise explanation of why these contradict",
      "target_quote": "exact word-for-word from TARGET",
      "candidate_quote": "exact word-for-word from MATCH",
      "confidence": 0.92
    }}
  ]
}}
If no conflicts: {{"conflicts": []}}."""

        result = llm_generate(
            model=AGENT_MODEL,
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=0.0,
            max_tokens=4096,
            timeout=60.0,
            enable_thinking=False,
        )

    else:
        system_prompt = (
            "You are a policy conflict analyst checking for contradictions between policy clauses.\n"
            "When in doubt, flag it. False negatives are worse than false positives.\n"
            "Respond ONLY with a valid JSON object. No markdown fences, no extra text."
        )

        framing = f'The user is asking: "{question}"\n' if question else ""

        prompt = f"""{framing}
The pairs below are grouped by candidate document. For each candidate document, review all its CANDIDATE clauses against the TARGET clauses.
A conflict exists when two clauses govern the same topic but impose contradictory requirements:
- Different numeric thresholds for the same rule (e.g. 90 days vs 180 days)
- One allows what the other prohibits (e.g. VPN allowed vs VPN banned)
- Different obligation levels for the same rule (must vs must not)
- Contradictory timeframes, scope, or procedures for the same process

Different populations (employees vs admins) for the same rule = NOT a conflict.

{question_context}PAIRS:
{paired_block}

Return a JSON object with "conflicts" — include every pair where you found a real contradiction.
{{
  "conflicts": [
    {{
      "target_id": "T0",
      "candidate_id": "C0",
      "reason": "evidence-backed explanation quoting both clauses",
      "confidence": 0.95
    }}
  ]
}}
If no conflicts, return {{"conflicts": []}}."""

        result = llm_generate(
            model=AGENT_MODEL,
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=0.0,
            max_tokens=4096,
            timeout=60.0,
            enable_thinking=False,
        )

    if not result or not result.success:
        logger.warning("Batch LLM failed — %d pairs unprocessed", len(candidate_pairs))
        return None

    raw = result.data.strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    first_brace = raw.find('{')
    if first_brace > 0:
        raw = raw[first_brace:]

    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        logger.warning("Batch LLM returned no JSON — %d pairs unprocessed", len(candidate_pairs))
        return []

    try:
        parsed = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError):
        conflicts_list = re.findall(r'\{[^{}]*"target_id"[^{}]*\}', raw)
        if not conflicts_list:
            logger.warning("Batch LLM returned unparseable JSON — %d pairs unprocessed", len(candidate_pairs))
            return []
        parsed = {"conflicts": []}
        for obj_str in conflicts_list:
            try:
                parsed["conflicts"].append(json.loads(obj_str))
            except (json.JSONDecodeError, ValueError):
                continue

    raw_conflicts = parsed.get("conflicts", [])
    if not isinstance(raw_conflicts, list):
        return []

    logger.warning(
        "_batch_conflict_check: type=%s LLM returned %d raw conflicts from %d pairs",
        conflict_type, len(raw_conflicts), len(candidate_pairs),
    )

    conflicts = []
    for rc in raw_conflicts:
        tid = rc.get("target_id", "")
        cid = rc.get("candidate_id", "")
        target_chunk = target_map.get(tid)
        cand_pair = cand_map.get(cid)
        if not target_chunk or not cand_pair:
            continue

        conflicts.append(_build_conflict_record(
            target_chunk,
            cand_pair["candidate"],
            cand_pair.get("similarity", 0.0),
            {"conflict": True, "reason": rc.get("reason", ""), "source": "batch_llm",
             "target_quote": rc.get("target_quote", ""),
             "candidate_quote": rc.get("candidate_quote", "")},
        ))

    logger.warning(
        "_batch_conflict_check: LLM returned %d conflicts from %d pairs (raw_conflicts=%d, unmatched=%d)",
        len(conflicts), len(candidate_pairs), len(raw_conflicts),
        len(raw_conflicts) - len(conflicts),
    )

    return conflicts


# ---------------------------------------------------------------------------
# Candidate pair generation (cross-document)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Core conflict evaluation
# ---------------------------------------------------------------------------

def _evaluate_pairs(
    pairs: List[Dict[str, Any]],
    access_level: int,
    max_llm_calls: int = 5,
    target_chunks: Optional[List[Dict[str, Any]]] = None,
    target_doc_title: str = "",
    question: str = "",
    conflict_type: str = "",
) -> Dict[str, Any]:
    """
    Evaluate candidate pairs for conflicts. Returns structured result.

    Deterministic checks (Neo4j, version) run first.
    Then a single batch LLM call evaluates all eligible pairs at once.
    """
    all_conflicts = []
    unchecked = 0
    llm_calls_used = 0
    failed_calls = 0
    filtered_by_similarity = 0
    filtered_by_neo4j = 0
    filtered_by_version = 0

    deterministic_pairs = []
    llm_eligible_pairs = []

    for pair in pairs:
        src = pair["source"]
        cand = pair["candidate"]
        src_id = src.get("id", "")
        cand_id = cand.get("id", "")

        neo4j_hits = _check_neo4j_conflicts([src_id])
        for hit in neo4j_hits:
            if hit.get("clause_b_id") == cand_id:
                all_conflicts.append(_build_conflict_record(src, cand, pair["similarity"], hit))
                filtered_by_neo4j += 1
                break

        src_doc = src.get("document_id", "")
        cand_doc = cand.get("document_id", "")
        if src_doc == cand_doc:
            src_ref = src.get("clause_ref", "")
            cand_ref = cand.get("clause_ref", "")
            if src_ref and cand_ref and src_ref == cand_ref:
                all_conflicts.append(_build_conflict_record(
                    src, cand, pair["similarity"],
                    {"conflict": True, "reason": f"Same clause_ref '{src_ref}' across versions", "source": "version_check"},
                ))
                filtered_by_version += 1
            continue

        if pair.get("similarity", 0.0) >= 0.77:
            logger.debug("Skipping near-identical pair: sim=%.3f", pair.get("similarity", 0.0))
            filtered_by_similarity += 1
            unchecked += 1
            continue

        llm_eligible_pairs.append(pair)

    logger.warning(
        "_evaluate_pairs: pairs=%d eligible=%d sim_filtered=%d neo4j=%d version=%d target_chunks=%d max_llm_calls=%d",
        len(pairs), len(llm_eligible_pairs),
        filtered_by_similarity,
        filtered_by_neo4j, filtered_by_version,
        len(target_chunks) if target_chunks else 0,
        max_llm_calls,
    )

    if llm_eligible_pairs and target_chunks:
        llm_eligible_pairs = llm_eligible_pairs[:max_llm_calls]

        # Group pairs by source clause (target)
        target_groups: Dict[str, List] = {}
        for pair in llm_eligible_pairs:
            src_id = pair["source"].get("id", "")
            target_groups.setdefault(src_id, []).append(pair)

        # Sort targets by best similarity first
        sorted_targets = sorted(
            target_groups.items(),
            key=lambda x: max(p.get("similarity", 0) for p in x[1]),
            reverse=True,
        )

        TARGETS_PER_BATCH = 5
        MAX_CANDIDATES_PER_TARGET = 5

        # Pack into batches of 5 targets with their top candidates
        batches = []
        for i in range(0, len(sorted_targets), TARGETS_PER_BATCH):
            batch_targets = sorted_targets[i:i + TARGETS_PER_BATCH]
            batch = []
            for src_id, target_pairs in batch_targets:
                batch.extend(target_pairs[:MAX_CANDIDATES_PER_TARGET])
            batches.append(batch)

        logger.warning(
            "_evaluate_pairs batching: %d pairs → %d targets → %d batches (~%d pairs each)",
            len(llm_eligible_pairs), len(sorted_targets), len(batches),
            len(llm_eligible_pairs) // max(1, len(batches)),
        )

        seen_pairs = set()
        for batch in batches:
            batch_conflicts = _batch_conflict_check(
                batch, target_doc_title, question=question,
                conflict_type=conflict_type,
            )
            llm_calls_used += 1
            if batch_conflicts is None:
                failed_calls += 1
            elif batch_conflicts:
                for c in batch_conflicts:
                    aid = c.get("clause_a", {}).get("id", "")
                    bid = c.get("clause_b", {}).get("id", "")
                    pair_key = tuple(sorted([aid, bid]))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        all_conflicts.append(c)
    elif llm_eligible_pairs:
        unchecked += len(llm_eligible_pairs)

    return {
        "conflicts": all_conflicts,
        "llm_calls_used": llm_calls_used,
        "unchecked": unchecked,
        "failed_calls": failed_calls,
    }


def _build_conflict_record(
    chunk_a: Dict, chunk_b: Dict, similarity: float, info: Dict
) -> Dict[str, Any]:
    """Build a full conflict record with clause metadata."""
    return {
        "clause_a": {
            "id": chunk_a.get("id", ""),
            "document_id": chunk_a.get("document_id", ""),
            "document_title": chunk_a.get("title", ""),
            "clause_ref": chunk_a.get("clause_ref", ""),
            "section_path": chunk_a.get("section_path", ""),
            "text": chunk_a.get("text", ""),
            "origin": chunk_a.get("origin"),
            "version": chunk_a.get("version"),
            "effective_date": chunk_a.get("effective_date"),
            "status": chunk_a.get("status"),
            "page": chunk_a.get("page"),
        },
        "clause_b": {
            "id": chunk_b.get("id", ""),
            "document_id": chunk_b.get("document_id", ""),
            "document_title": chunk_b.get("title", ""),
            "clause_ref": chunk_b.get("clause_ref", ""),
            "section_path": chunk_b.get("section_path", ""),
            "text": chunk_b.get("text", ""),
            "origin": chunk_b.get("origin"),
            "version": chunk_b.get("version"),
            "effective_date": chunk_b.get("effective_date"),
            "status": chunk_b.get("status"),
            "page": chunk_b.get("page"),
        },
        "similarity": similarity,
        "conflict": info.get("conflict", False),
        "reason": info.get("reason", ""),
        "source": info.get("source", "llm"),
    }


# ---------------------------------------------------------------------------
# Type 1: expand retrieved chunks with similar clauses from other docs
# ---------------------------------------------------------------------------

def expand_chunks_for_conflicts(
    chunks: List[Dict[str, Any]],
    access_level: int,
    max_similar_per_chunk: int = 3,
    threshold: float = 0.57,
) -> List[Dict[str, Any]]:
    """
    Expand retrieved chunks with similar clauses from other documents.

    For each input chunk, find up to max_similar_per_chunk similar clauses
    from OTHER documents. Returns deduplicated combined set.
    """
    from services.api.search import find_similar_clauses

    expanded = list(chunks)
    seen_ids = {c.get("id") for c in chunks}

    for chunk in chunks:
        src_doc = chunk.get("document_id", "")

        similar = find_similar_clauses(
            clause_text=chunk.get("text", ""),
            access_level=access_level,
            exclude_doc_id=src_doc,
            top_k=max_similar_per_chunk,
            threshold=threshold,
        )

        for sim_chunk in similar:
            sim_id = sim_chunk.get("id", "")
            if sim_id not in seen_ids:
                seen_ids.add(sim_id)
                expanded.append(sim_chunk)

    return expanded


# ---------------------------------------------------------------------------
# Type 2: document-vs-document comparison
# ---------------------------------------------------------------------------

def compare_document_chunks(
    chunks_by_doc: Dict[str, List[Dict[str, Any]]],
    access_level: int,
    similarity_threshold: float = 0.57,
    max_pairs: int = 100,
    max_llm_calls: int = 15,
    question: str = "",
    conflict_type: str = "type_2",
) -> Dict[str, Any]:
    """
    Compare all chunks across 2-3 documents for conflicts.

    Returns:
        {
            "doc_pairs": [{"doc_a": {...}, "doc_b": {...}, "conflicts": [...], ...}],
            "total_conflicts": int,
            "total_candidates": int,
            "total_llm_calls": int,
            "truncated": bool,
        }
    """
    doc_ids = list(chunks_by_doc.keys())
    doc_pairs_result = []
    total_conflicts = 0
    total_candidates = 0
    total_llm_calls = 0
    failed_calls = 0
    truncated = False

    llm_budget_remaining = max_llm_calls

    for i in range(len(doc_ids)):
        for j in range(i + 1, len(doc_ids)):
            doc_a_id, doc_b_id = doc_ids[i], doc_ids[j]
            chunks_a = chunks_by_doc.get(doc_a_id, [])
            chunks_b = chunks_by_doc.get(doc_b_id, [])

            title_a = chunks_a[0].get("title", "") if chunks_a else ""
            title_b = chunks_b[0].get("title", "") if chunks_b else ""

            pairs = _build_direct_pairs(chunks_a, chunks_b, similarity_threshold, max_pairs, access_level=access_level)
            total_candidates += len(pairs)

            if llm_budget_remaining <= 0:
                truncated = True
                doc_pairs_result.append({
                    "doc_a": {"id": doc_a_id, "title": title_a},
                    "doc_b": {"id": doc_b_id, "title": title_b},
                    "conflicts": [],
                    "unchecked_candidate_count": len(pairs),
                    "total_candidate_count": len(pairs),
                })
                continue

            # Take pairs within budget; dynamic token cap in _batch_conflict_check
            # handles the actual context limit
            initial_pairs = pairs[:min(len(pairs), llm_budget_remaining)]
            eval_result = _evaluate_pairs(
                initial_pairs, access_level, max_llm_calls=len(initial_pairs),
                target_chunks=chunks_a, target_doc_title=title_a, question=question,
                conflict_type=conflict_type,
            )
            calls_used = eval_result["llm_calls_used"]
            failed_calls += eval_result.get("failed_calls", 0)
            llm_budget_remaining -= calls_used
            total_llm_calls += calls_used

            if eval_result["unchecked"] > 0:
                truncated = True

            total_conflicts += len(eval_result["conflicts"])
            doc_pairs_result.append({
                "doc_a": {"id": doc_a_id, "title": title_a},
                "doc_b": {"id": doc_b_id, "title": title_b},
                "conflicts": eval_result["conflicts"],
                "unchecked_candidate_count": eval_result["unchecked"],
                "total_candidate_count": len(pairs),
            })

    return {
        "doc_pairs": doc_pairs_result,
        "total_conflicts": total_conflicts,
        "total_candidates": total_candidates,
        "total_llm_calls": total_llm_calls,
        "failed_calls": failed_calls,
        "truncated": truncated,
    }


def _build_direct_pairs(
    chunks_a: List[Dict],
    chunks_b: List[Dict],
    threshold: float,
    max_pairs: int,
    access_level: int = 3,
) -> List[Dict[str, Any]]:
    """Build all cross-document candidate pairs between two chunk lists."""
    from services.api.search import find_similar_in_document

    pairs = []
    target_doc_id = chunks_b[0].get("document_id", "") if chunks_b else ""

    for src in chunks_a:
        src_id = src.get("id", "")

        similar = find_similar_in_document(
            clause_text=src.get("text", ""),
            target_doc_id=target_doc_id,
            access_level=access_level,
            top_k=5,
            threshold=threshold,
        )

        for sim_chunk in similar:
            cand_id = sim_chunk.get("id", "")
            pairs.append({
                "source": src,
                "candidate": sim_chunk,
                "similarity": sim_chunk.get("similarity", 0.0),
            })

    pairs.sort(key=lambda x: x["similarity"], reverse=True)
    return pairs[:max_pairs]


# ---------------------------------------------------------------------------
# Type 2b: document-vs-corpus
# ---------------------------------------------------------------------------

def detect_conflicting_documents(
    target_doc_chunks: List[Dict[str, Any]],
    target_doc_id: str,
    target_doc_title: str,
    access_level: int,
    similarity_threshold: float = 0.57,
    max_pairs: int = 100,
    max_llm_calls: int = 15,
    question: str = "",
) -> Dict[str, Any]:
    """
    Find which documents conflict with the target document.

    Pair building: for each target clause, find_similar_clauses searches
    across ALL other docs (not just one). Group pairs by candidate document.
    Evaluation: _evaluate_pairs per candidate doc — one batch per doc,
    same structure as Type 2.
    """
    from services.api.search import find_similar_clauses

    # Step 1: build pairs via find_similar_clauses across all docs except target
    candidates_by_doc: Dict[str, List[Dict]] = {}

    for chunk in target_doc_chunks:
        similar = find_similar_clauses(
            clause_text=chunk.get("text", ""),
            access_level=access_level,
            exclude_doc_id=target_doc_id,
            top_k=5,
            threshold=similarity_threshold,
        )

        for sim_chunk in similar:
            sim_id = sim_chunk.get("id", "")
            doc_id = sim_chunk.get("document_id", "")
            candidates_by_doc.setdefault(doc_id, []).append({
                "source": chunk,
                "candidate": sim_chunk,
                "similarity": sim_chunk.get("similarity", 0.0),
            })

    if not candidates_by_doc:
        return {
            "doc_pairs": [], "total_conflicts": 0,
            "total_candidates": 0, "total_llm_calls": 0,
            "failed_calls": 0, "truncated": False,
        }

    # Step 2: evaluate per candidate doc — same structure as Type 2
    total_candidates = sum(len(pairs) for _, pairs in candidates_by_doc.items())
    total_conflicts = 0
    total_llm_calls = 0
    failed_calls = 0
    llm_budget = max_llm_calls
    doc_pairs_result = []

    for cand_doc_id, doc_pairs in candidates_by_doc.items():
        doc_pairs_sorted = sorted(doc_pairs, key=lambda x: x["similarity"], reverse=True)
        batch = doc_pairs_sorted[:llm_budget] if llm_budget > 0 else []

        cand_title = batch[0]["candidate"].get("title", "") if batch else ""

        if not batch or llm_budget <= 0:
            doc_pairs_result.append({
                "doc_a": {"id": target_doc_id, "title": target_doc_title},
                "doc_b": {"id": cand_doc_id, "title": cand_title},
                "conflicts": [],
                "unchecked_candidate_count": len(doc_pairs),
                "total_candidate_count": len(doc_pairs),
            })
            if llm_budget <= 0:
                continue
            continue

        # Extract target chunks referenced in this batch
        batch_target_ids = {p["source"].get("id", "") for p in batch}
        batch_targets = [c for c in target_doc_chunks if c.get("id", "") in batch_target_ids]
        if not batch_targets:
            batch_targets = target_doc_chunks[:10]

        eval_result = _evaluate_pairs(
            batch, access_level, max_llm_calls=len(batch),
            target_chunks=batch_targets, target_doc_title=target_doc_title,
            question=question, conflict_type="type_2b",
        )
        calls_used = eval_result["llm_calls_used"]
        failed_calls += eval_result.get("failed_calls", 0)
        llm_budget -= calls_used
        total_llm_calls += calls_used

        total_conflicts += len(eval_result["conflicts"])
        doc_pairs_result.append({
            "doc_a": {"id": target_doc_id, "title": target_doc_title},
            "doc_b": {"id": cand_doc_id, "title": cand_title},
            "conflicts": eval_result["conflicts"],
            "unchecked_candidate_count": eval_result.get("unchecked", 0),
            "total_candidate_count": len(doc_pairs),
        })

    truncated = failed_calls > 0 and (total_llm_calls - failed_calls) == 0

    return {
        "doc_pairs": doc_pairs_result,
        "total_conflicts": total_conflicts,
        "total_candidates": total_candidates,
        "total_llm_calls": total_llm_calls,
        "failed_calls": failed_calls,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Legacy entry point (used by orchestrator)
# ---------------------------------------------------------------------------

def detect_conflicts_in_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Detect conflicts across retrieved chunks (legacy single-flow interface).

    Strategy:
    1. Check Neo4j CONFLICTS_WITH relationships
    2. Detect version mismatches (same clause_ref across documents)
    3. Batch LLM conflict detection (single call)
    """
    all_conflicts = []

    chunk_ids = [c.get("id", "") for c in chunks if c.get("id")]
    neo4j_conflicts = _check_neo4j_conflicts(chunk_ids)
    all_conflicts.extend(neo4j_conflicts)

    version_conflicts = _detect_version_conflicts(chunks)
    all_conflicts.extend(version_conflicts)

    candidate_pairs = []
    for i, a in enumerate(chunks):
        for b in chunks[i + 1:]:
            if a.get("document_id") == b.get("document_id"):
                continue
            similarity = max(
                float(a.get("similarity", 0.0) or 0.0),
                float(b.get("similarity", 0.0) or 0.0),
            )
            candidate_pairs.append({"source": a, "candidate": b, "similarity": similarity})

    candidate_pairs.sort(key=lambda x: x["similarity"], reverse=True)
    top_pairs = candidate_pairs[:15]

    if top_pairs:
        batch_conflicts = _batch_conflict_check(top_pairs, "Corpus", conflict_type="type_1")
        if batch_conflicts:
            all_conflicts.extend(batch_conflicts)

    return all_conflicts


# ---------------------------------------------------------------------------
# Clause-vs-corpus semantic pipeline
# ---------------------------------------------------------------------------

CONFLICT_STATUSES = {
    "confirmed_conflict",
    "possible_conflict",
    "no_conflict",
    "complementary_scope",
    "insufficient_context",
    "superseded",
}


def _rule_attributes(text: str) -> Dict[str, Any]:
    """Extract conservative, auditable rule attributes from clause text."""
    value_matches = re.findall(r"\b(\d+(?:\.\d+)?)\s*(days?|weeks?|months?|years?|hours?|minutes?|%)\b", text.lower())
    values = [{"value": float(v) if "." in v else int(v), "unit": u} for v, u in value_matches]
    modal = "must not" if re.search(r"\b(must not|shall not|may not|prohibited|forbidden)\b", text, re.I) else None
    if not modal:
        modal_match = re.search(r"\b(must|shall|required|may|should|can)\b", text, re.I)
        modal = modal_match.group(1).lower() if modal_match else None
    roles = re.findall(
        r"\b(employees?|administrators?|admins?|managers?|contractors?|vendors?|customers?|users?|staff|"
        r"remote workers?|all personnel)\b", text, re.I,
    )
    normalized_roles = sorted({r.lower() for r in roles})
    return {
        "modal": modal,
        "values": values,
        "roles": normalized_roles,
        "negated": bool(re.search(r"\b(not|never|禁止|prohibited|forbidden)\b", text, re.I)),
        "dates": re.findall(r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}/\d{1,2}/20\d{2})\b", text),
    }


def _semantic_result(result: Dict[str, Any], clause_a: Dict[str, Any], clause_b: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy and strict LLM responses into the new contract."""
    if not isinstance(result, dict):
        return {"status": "insufficient_context", "confidence": 0.0, "reason": "Invalid semantic result", "missing_context": ["result"]}
    status = result.get("status")
    if status is not None and status not in CONFLICT_STATUSES:
        return {"status": "insufficient_context", "confidence": 0.0, "reason": "Unsupported semantic status", "missing_context": ["status"]}
    if status is None:
        status = "confirmed_conflict" if result.get("conflict") else "no_conflict"
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if not 0.0 <= confidence <= 1.0:
        confidence = 0.0
        status = "insufficient_context"
    attrs_a = _rule_attributes(clause_a.get("text", ""))
    attrs_b = _rule_attributes(clause_b.get("text", ""))
    if status in {"confirmed_conflict", "possible_conflict"} and confidence < 0.60:
        status = "insufficient_context"
    normalized = {
        "status": status,
        "subject": str(result.get("subject", "")),
        "scope_overlap": bool(result.get("scope_overlap", True)),
        "difference_type": str(result.get("difference_type", "requirement")),
        "source_requirement": result.get("source_requirement", ""),
        "candidate_requirement": result.get("candidate_requirement", ""),
        "confidence": confidence,
        "reason": str(result.get("reason", "")),
        "missing_context": list(result.get("missing_context", []) or []),
        "source_attributes": result.get("source_attributes", attrs_a),
        "candidate_attributes": result.get("candidate_attributes", attrs_b),
    }
    if "status" in result and "scope_overlap" not in result:
        normalized.update(status="insufficient_context", missing_context=["scope_overlap"])
    return normalized


def _validate_semantic_result(result: Dict[str, Any], clause_a: Dict[str, Any], clause_b: Dict[str, Any]) -> Dict[str, Any]:
    """Apply deterministic safety checks after semantic comparison."""
    result = _semantic_result(result, clause_a, clause_b)
    status = result["status"]
    if status not in {"confirmed_conflict", "possible_conflict", "superseded"}:
        return result
    text_a, text_b = clause_a.get("text", "").lower(), clause_b.get("text", "").lower()
    attrs_a, attrs_b = result["source_attributes"], result["candidate_attributes"]
    roles_a, roles_b = set(attrs_a.get("roles", [])), set(attrs_b.get("roles", []))
    generic_roles = {"employees", "personnel", "staff", "users", "all personnel", "all employees"}
    specific_a = roles_a - generic_roles
    specific_b = roles_b - generic_roles
    if specific_a and specific_b and not (specific_a & specific_b):
        result.update(status="complementary_scope", scope_overlap=False, reason="The clauses apply to different roles or populations.")
        return result
    if not result.get("reason") or not result.get("scope_overlap"):
        result.update(status="insufficient_context", missing_context=["scope or explanation"])
        return result
    subject_words = {
        word for word in re.findall(r"[a-z0-9]+", result.get("subject", "").lower())
        if len(word) >= 4 and word not in {"same", "both", "rule", "policy", "requirement"}
    }
    words_a = set(re.findall(r"[a-z0-9]+", text_a))
    words_b = set(re.findall(r"[a-z0-9]+", text_b))
    def _subject_in(words: set) -> bool:
        return not subject_words or any(
            subject == word or subject.startswith(word[:5]) or word.startswith(subject[:5])
            for subject in subject_words for word in words
        )
    if not _subject_in(words_a) or not _subject_in(words_b):
        result.update(status="insufficient_context", missing_context=["subject not supported by both clauses"])
        return result
    for requirement, text in ((result.get("source_requirement"), text_a), (result.get("candidate_requirement"), text_b)):
        if requirement and isinstance(requirement, str):
            numbers = re.findall(r"\d+(?:\.\d+)?", requirement)
            if numbers and not all(number in text for number in numbers):
                result.update(status="insufficient_context", missing_context=["unsupported requirement value"])
                return result
    if status == "confirmed_conflict" and result["confidence"] < 0.85:
        result["status"] = "possible_conflict"
    return result


def _canonical_pair_key(clause_a: Dict[str, Any], clause_b: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (str(clause_a.get("document_id", "")), str(clause_a.get("id", "")),
            str(clause_b.get("document_id", "")), str(clause_b.get("id", "")))


def _build_semantic_conflict_record(clause_a: Dict[str, Any], clause_b: Dict[str, Any], similarity: float, result: Dict[str, Any]) -> Dict[str, Any]:
    record = _build_conflict_record(clause_a, clause_b, similarity, {**result, "source": "semantic_llm"})
    record.update({
        "status": result.get("status", "insufficient_context"),
        "confidence": result.get("confidence", 0.0),
        "subject": result.get("subject", ""),
        "difference_type": result.get("difference_type", "requirement"),
        "scope_overlap": result.get("scope_overlap", False),
        "missing_context": result.get("missing_context", []),
        "source_requirement": result.get("source_requirement", ""),
        "candidate_requirement": result.get("candidate_requirement", ""),
        "conflict": result.get("status") in {"confirmed_conflict", "possible_conflict", "superseded"},
    })
    for side in ("clause_a", "clause_b"):
        record[side]["origin"] = "query_source" if side == "clause_a" else "corpus_candidate"
        record[side]["version"] = (clause_a if side == "clause_a" else clause_b).get("version")
        record[side]["effective_date"] = (clause_a if side == "clause_a" else clause_b).get("effective_date")
        record[side]["status"] = (clause_a if side == "clause_a" else clause_b).get("status")
        record[side]["page"] = (clause_a if side == "clause_a" else clause_b).get("page")
    return record


def build_batches(clauses):
    """Split clauses into overlapping circular batches for full cross-comparison.
    
    Splits into groups of ~15 clauses, then creates circular batches:
    Each batch = group_i + group_(i+1), ensuring every clause pair
    is compared in at least one batch.
    
    Returns list of (global_start_offset, batch_clauses) tuples.
    """
    total = len(clauses)
    if total == 0:
        return []

    GROUP_SIZE = 15

    if total <= GROUP_SIZE:
        return [(0, clauses)]

    num_groups = math.ceil(total / GROUP_SIZE)
    groups = []
    for i in range(num_groups):
        start = i * GROUP_SIZE
        end = min(start + GROUP_SIZE, total)
        groups.append(clauses[start:end])

    batches = []
    for i in range(len(groups)):
        batch = groups[i] + groups[(i + 1) % len(groups)]
        global_start = i * GROUP_SIZE
        batches.append((global_start, batch))

    return batches


def analyze_clause_vs_corpus(
    source_chunks: List[Dict[str, Any]],  # ignored — kept for pipeline compat
    access_level: int,
    query_text: str = "",
    candidate_retrieval_limit: int = 200,
    **_kwargs,
) -> Dict[str, Any]:
    """Search entire corpus for most relevant clauses, batch into groups, analyze each.

    Uses LLM to generate topic-aware search queries, runs multi-search,
    shuffles clauses by document, splits into circular batches,
    runs _batch_conflict_check per batch, then deduplicates and validates.
    """
    from services.api.search import find_similar_clauses
    import json, re

    # Step 1: LLM generates topic-aware search queries (uses 4B for speed)
    search_queries = [query_text]
    try:
        topic_result = llm_generate(
            model=INGESTION_MODEL,
            system_prompt=(
                "You are a search query generator for a policy document system.\n"
                "Given a user question, generate 2 NEW search queries to find ALL related clauses.\n"
                "RULES:\n"
                "- Do NOT repeat the user's question or use the same words\n"
                "- Each query should use DIFFERENT vocabulary than the original\n"
                "- Think about what specific documents would contain this information\n"
                "- Think about related sub-topics (e.g. 'data retention' relates to 'log storage duration', 'record keeping period')\n"
                "Respond ONLY with a JSON object."
            ),
            user_message=(
                f'Question: "{query_text}"\n\n'
                '{"queries": ["search query 1", "search query 2"]}'
            ),
            temperature=0.0,
            max_tokens=200,
            timeout=15.0,
            enable_thinking=False,
        )
        if topic_result.success:
            raw = topic_result.data.strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                topic_data = json.loads(match.group())
                search_queries += topic_data.get("queries", [])
                logger.warning("Type 1 topic queries: %s", search_queries)
    except Exception as e:
        logger.warning("Type 1 topic extraction failed, using original query only: %s", e)

    # Step 2: Multi-search with original + LLM queries
    all_results = {}
    for q in search_queries:
        results = find_similar_clauses(
            clause_text=q,
            access_level=access_level,
            top_k=60,
            threshold=0.57,
        ) or []
        for r in results:
            rid = r.get("id", "")
            if rid and rid not in all_results:
                all_results[rid] = r

    clauses = list(all_results.values())
    clauses = [c for c in clauses if c.get("similarity", 0.0) < 0.77]

    # Prepend user query as a checkable clause (Scenario 2: clause vs query)
    query_clause = {
        "id": "__query__",
        "document_id": "__query__",
        "title": "User Query",
        "text": query_text,
        "clause_ref": "N/A",
        "similarity": 1.0,
    }
    clauses = [query_clause] + clauses

    if len(clauses) <= 1:
        return {
            "conflicts": [], "clauses_sent": [],
            "total_candidates": 0, "total_conflicts": 0,
            "total_llm_calls": 0, "failed_calls": 0, "truncated": False,
        }

    # Randomize clause order for balanced cross-doc batches
    import random
    random.shuffle(clauses[1:])

    # Split into overlapping batches
    batches = build_batches(clauses)
    logger.warning(
        "Type 1 analyze: %d clauses → %d batches",
        len(clauses), len(batches),
    )

    # Run LLM per batch, collect raw conflicts with a mapping logic
    all_raw_conflicts = []
    total_llm_calls = 0
    failed_calls = 0

    # Map global indices to their actual objects for easy lookup
    # clauses[0] is always the query_clause
    clause_map = {f"C{i}": c for i, c in enumerate(clauses)}

    for global_start, batch in batches:
        # Create a local mapping for this specific call:
        # Local ID 'C{j}' -> Global ID 'C{global_index}'
        # We need to know which global index each clause in the batch has.
        # Since build_batches for Type 1 now returns combined lists (e.g. A+B),
        # we must pass the actual objects and rebuild a local mapping inside _batch_conflict_check
        # OR handle it here.
        
        # To keep _batch_conflict_check generic, we wrap the batch to preserve global IDs
        wrapped_batch = []
        for c in batch:
            # Find the index of this clause in the original 'clauses' list
            try:
                idx = clauses.index(c)
                wrapped_batch.append({"_global_id": f"C{idx}", "text": c.get("text"), "document_id": c.get("document_id"), "title": c.get("title")})
            except ValueError:
                continue
        
        # Actually, _batch_conflict_check expects raw clauses and assigns C0, C1...
        # Let's just use the raw batch and map local -> global using the order in 'batch'
        raw = _batch_conflict_check(
            batch, query_text, question=query_text, conflict_type="type_1",
        )
        
        if raw is None:
            failed_calls += 1
            continue
        total_llm_calls += 1

        # Mapping local ID (C{j}) to global object
        # The LLM sees the list 'batch' as C0, C1, C2...
        for rc in raw:
            aid_local = rc.get("clause_a_id", "")
            bid_local = rc.get("clause_b_id", "")
            
            # Local to Global Mapping
            def map_id(local_id):
                if local_id.startswith("C") and local_id[1:].isdigit():
                    idx = int(local_id[1:])
                    if 0 <= idx < len(batch):
                        # The actual global ID is the index of batch[idx] in the original 'clauses' list
                        global_obj = batch[idx]
                        global_idx = clauses.index(global_obj)
                        return f"C{global_idx}"
                return local_id

            rc["clause_a_id"] = map_id(aid_local)
            rc["clause_b_id"] = map_id(bid_local)
            all_raw_conflicts.append(rc)

    # Deduplicate overlapping results (same pair may appear in 2 batches)
    seen_pairs = set()
    deduped = []
    for rc in all_raw_conflicts:
        pair_key = tuple(sorted([rc["clause_a_id"], rc["clause_b_id"]]))
        if pair_key not in seen_pairs:
            seen_pairs.add(pair_key)
            deduped.append(rc)

    # Validate: hallucination guard + same-doc guard
    valid_ids = {f"C{i}" for i in range(len(clauses))}
    clause_map = {f"C{i}": c for i, c in enumerate(clauses)}
    validated = []
    for rc in deduped:
        aid = rc.get("clause_a_id", "")
        bid = rc.get("clause_b_id", "")
        if aid not in valid_ids or bid not in valid_ids:
            logger.warning("Type1 guard: hallucinated ID filtered — aid=%s bid=%s", aid, bid)
            continue
        aid_doc = clause_map[aid].get("document_id", "")
        bid_doc = clause_map[bid].get("document_id", "")
        # Same-doc filter: skip if both clauses from same policy (but allow __query__)
        if aid_doc != "__query__" and bid_doc != "__query__" and aid_doc == bid_doc:
            logger.warning("Type1 guard: same-doc filtered — aid=%s bid=%s doc=%s", aid, bid, aid_doc)
            continue
        validated.append(_build_conflict_record(
            clause_map[aid], clause_map[bid], 0.0,
            {"conflict": True, "reason": rc.get("reason", ""), "source": "batch_llm"},
        ))

    return {
        "conflicts": validated,
        "clauses_sent": clauses,
        "total_candidates": len(clauses),
        "total_conflicts": len(validated),
        "total_llm_calls": total_llm_calls,
        "failed_calls": failed_calls,
        "truncated": False,
    }
