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
import re
from typing import Dict, Any, List, Optional, Tuple
from services.agents.tools.neo4j_tools import neo4j_query
from services.agents.tools.llm_tools import llm_generate, QWEN3_8B_MODEL

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
        model=QWEN3_8B_MODEL,
        system_prompt=(
            "You are a policy conflict analyst. Find contradictions between policy clauses. When in doubt, flag it.\n"
            "Respond ONLY with a valid JSON object. No markdown fences, no extra text."
        ),
        user_message=prompt,
        temperature=0.0,
        max_tokens=1024,
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

    Pairs are grouped by target clause so the LLM sees each target alongside
    its matched candidates — no unnecessary targets, no unmatched candidates.

    Returns:
        List of conflict records in the same format as _build_conflict_record.
    """
    if not candidate_pairs:
        return []

    MAX_CANDIDATE_CHARS = 800
    MAX_TARGET_CHARS = 800
    MAX_PAIR_TOKENS = 12000

    from collections import defaultdict

    def _est_tokens(text: str) -> int:
        return (len(text) + 3) // 4

    prompt_overhead = 250
    running_tokens = prompt_overhead

    pairs_by_target: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pair in candidate_pairs:
        src_id = pair["source"].get("id", "")
        pairs_by_target[src_id].append(pair)

    sections = []
    target_map: Dict[str, Dict[str, Any]] = {}
    cand_map: Dict[str, Dict[str, Any]] = {}
    tc_idx = 0
    cc_idx = 0
    dropped_by_budget = 0

    for src_id, pairs in pairs_by_target.items():
        tc_idx += 1
        source = pairs[0]["source"]
        tc_label = f"T{tc_idx - 1}"
        target_map[tc_label] = source

        text = str(source.get("text", ""))[:MAX_TARGET_CHARS]
        ref = source.get("clause_ref", "")
        ref_part = f" [{ref}]" if ref else ""
        tgt_line = f'  TARGET [id:{tc_label}]{ref_part}: "{text}"'

        tgt_tokens = _est_tokens(text) + 20
        cand_lines = []
        for p in sorted(pairs, key=lambda x: x.get("similarity", 0), reverse=True):
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

            title = cand.get("title", "") or cand.get("document_title", "")
            crow = p.get("similarity", 0.0)
            cline = (
                f'    → CANDIDATE [id:{cc_label}, Doc: {title}, '
                f'Ref: {cand.get("clause_ref", "")}, sim={crow:.2f}]: "{ctext}"'
            )
            cand_lines.append(cline)

        sections.append(f"PAIR {tc_idx}:\n{tgt_line}\n" + "\n".join(cand_lines))

    if dropped_by_budget:
        logger.debug("Token budget: %d pairs dropped, %d kept, ~%d tokens used of %d", dropped_by_budget, cc_idx, running_tokens, MAX_PAIR_TOKENS)

    paired_block = "\n\n".join(sections)

    question_context = f'\nUser question: "{question}"\n\n' if question else ""

    if conflict_type == "type_2b":
        system_prompt = (
            f"You are a policy conflict analyst auditing the '{target_doc_title}' against the rest of the policy corpus.\n"
            "Your job is to find contradictions — clauses in other policies that clash with this document.\n"
            "When in doubt, flag it. False negatives are worse than false positives.\n"
            "Respond ONLY with a valid JSON object. No markdown fences, no extra text."
        )
        prompt = f"""You are checking whether any clauses in OTHER policies contradict clauses in the "{target_doc_title}".

For each TARGET clause from the "{target_doc_title}", compare it against its CANDIDATE clauses from other policies.
A conflict exists when two clauses govern the same topic but impose contradictory requirements:
- Different numeric thresholds for the same rule (e.g. 90 days vs 180 days)
- One allows what the other prohibits (e.g. VPN allowed vs VPN banned)
- Different obligation levels for the same rule (e.g. must vs must not)
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

    elif conflict_type == "type_2":
        system_prompt = (
            "You are a policy conflict analyst comparing two specific documents for contradictions.\n"
            "Your job is to find requirements in these documents that clash.\n"
            "When in doubt, flag it. False negatives are worse than false positives.\n"
            "Respond ONLY with a valid JSON object. No markdown fences, no extra text."
        )
        prompt = f"""You are comparing two policy documents for contradictions.

For each TARGET clause, compare it against its CANDIDATE clauses from the other document.
A conflict exists when two clauses govern the same topic but impose contradictory requirements:
- Different numeric thresholds for the same rule
- One allows what the other prohibits
- Different obligation levels for the same rule
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

    else:
        system_prompt = (
            "You are a policy conflict analyst checking for contradictions between policy clauses.\n"
            "When in doubt, flag it. False negatives are worse than false positives.\n"
            "Respond ONLY with a valid JSON object. No markdown fences, no extra text."
        )

        framing = f'The user is asking: "{question}"\n' if question else ""

        prompt = f"""{framing}
For each TARGET clause, compare it against its CANDIDATE clauses from other policy documents.
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
        model=QWEN3_8B_MODEL,
        system_prompt=system_prompt,
        user_message=prompt,
        temperature=0.0,
        max_tokens=4096,
        timeout=None,
        enable_thinking=False,
    )

    if not result.success:
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
            {"conflict": True, "reason": rc.get("reason", ""), "source": "batch_llm"},
        ))

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
            continue

        text_a = src.get("text", "")[:1200]
        text_b = cand.get("text", "")[:1200]
        if len(text_a) < 30 or len(text_b) < 30:
            logger.debug("Skipping pair: text too short (%d/%d chars)", len(text_a), len(text_b))
            unchecked += 1
            continue

        if pair.get("similarity", 0.0) >= 0.77:
            logger.debug("Skipping near-identical pair: sim=%.3f", pair.get("similarity", 0.0))
            unchecked += 1
            continue

        llm_eligible_pairs.append(pair)

    logger.warning(
        "_evaluate_pairs: pairs=%d eligible=%d target_chunks=%s len=%d max_llm_calls=%d",
        len(pairs), len(llm_eligible_pairs),
        type(target_chunks).__name__, len(target_chunks) if target_chunks else 0,
        max_llm_calls,
    )

    if llm_eligible_pairs and target_chunks:
        llm_eligible_pairs = llm_eligible_pairs[:max_llm_calls]
        llm_calls_used = 1
        batch_conflicts = _batch_conflict_check(
            llm_eligible_pairs, target_doc_title, question=question,
            conflict_type=conflict_type,
        )
        if batch_conflicts is None:
            failed_calls = 1
        else:
            all_conflicts.extend(batch_conflicts)
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
        src_text = chunk.get("text", "")
        src_doc = chunk.get("document_id", "")
        if not src_text or len(src_text) < 30:
            continue

        similar = find_similar_clauses(
            clause_text=src_text,
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
    seen = set()
    target_doc_id = chunks_b[0].get("document_id", "") if chunks_b else ""

    for src in chunks_a:
        src_id = src.get("id", "")
        src_text = src.get("text", "")
        if not src_text or len(src_text) < 30:
            continue

        similar = find_similar_in_document(
            clause_text=src_text,
            target_doc_id=target_doc_id,
            access_level=access_level,
            top_k=5,
            threshold=threshold,
        )

        for sim_chunk in similar:
            cand_id = sim_chunk.get("id", "")
            pair_key = tuple(sorted([src_id, cand_id]))
            if pair_key in seen:
                continue
            seen.add(pair_key)
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
    seen = set()

    for chunk in target_doc_chunks:
        src_text = chunk.get("text", "")
        if not src_text or len(src_text) < 30:
            continue

        similar = find_similar_clauses(
            clause_text=src_text,
            access_level=access_level,
            exclude_doc_id=target_doc_id,
            top_k=5,
            threshold=similarity_threshold,
        )

        for sim_chunk in similar:
            sim_id = sim_chunk.get("id", "")
            if sim_id in seen:
                continue
            seen.add(sim_id)
            doc_id = sim_chunk.get("document_id", "")
            candidates_by_doc.setdefault(doc_id, []).append({
                "source": chunk,
                "candidate": sim_chunk,
                "similarity": sim_chunk.get("similarity", 0.0),
            })

    if not candidates_by_doc:
        return {
            "conflicting_documents": [], "total_conflicts": 0,
            "total_candidates": 0, "total_llm_calls": 0,
            "truncated": False, "inconclusive": False,
            "evaluated_top_k": False, "evaluated_count": 0,
            "min_evaluated_similarity": 0.0, "genuine_failures": 0,
            "coverage_note": "No comparable cross-policy clauses were found.",
        }

    # Step 2: rank candidate docs by best pair similarity, cap at 10
    MAX_DOCS = 10
    ranked_docs = sorted(
        candidates_by_doc.items(),
        key=lambda item: max(p["similarity"] for p in item[1]),
        reverse=True,
    )[:MAX_DOCS]

    # Step 3: evaluate per candidate doc — same _evaluate_pairs call as Type 2
    total_candidates = sum(len(pairs) for _, pairs in ranked_docs)
    all_conflicts = []
    total_llm_calls = 0
    failed_calls = 0
    llm_budget = max_llm_calls
    docs_evaluated = 0
    min_eval_sim = 1.0
    conflicting_docs = []

    for cand_doc_id, doc_pairs in ranked_docs:
        doc_pairs_sorted = sorted(doc_pairs, key=lambda x: x["similarity"], reverse=True)
        batch = doc_pairs_sorted[:llm_budget] if llm_budget > 0 else []

        cand_title = batch[0]["candidate"].get("title", "") if batch else ""

        if not batch or llm_budget <= 0:
            conflicting_docs.append({
                "document_id": cand_doc_id,
                "document_title": cand_title,
                "conflicts": [],
                "unchecked_candidate_count": len(doc_pairs),
                "total_candidate_count": len(doc_pairs),
            })
            continue

        # Extract target chunks referenced in this batch
        batch_target_ids = {p["source"].get("id", "") for p in batch}
        batch_targets = [c for c in target_doc_chunks if c.get("id", "") in batch_target_ids]
        if not batch_targets:
            batch_targets = target_doc_chunks[:10]

        # Same _evaluate_pairs call as Type 2 (compare_document_chunks line 684)
        eval_result = _evaluate_pairs(
            batch, access_level, max_llm_calls=len(batch),
            target_chunks=batch_targets, target_doc_title=target_doc_title,
            question=question, conflict_type="type_2b",
        )
        calls_used = eval_result["llm_calls_used"]
        failed_calls += eval_result.get("failed_calls", 0)
        llm_budget -= calls_used
        total_llm_calls += calls_used
        docs_evaluated += 1

        if batch:
            min_eval_sim = min(min_eval_sim, min(p["similarity"] for p in batch))

        all_conflicts.extend(eval_result["conflicts"])
        conflicting_docs.append({
            "document_id": cand_doc_id,
            "document_title": cand_title,
            "conflicts": eval_result["conflicts"],
            "unchecked_candidate_count": 0,
            "total_candidate_count": len(doc_pairs),
        })

    conflicting_docs.sort(key=lambda x: len(x.get("conflicts", [])), reverse=True)

    total_conflicts = len(all_conflicts)
    genuinely_inconclusive = failed_calls > 0 and (total_llm_calls - failed_calls) == 0

    return {
        "conflicting_documents": conflicting_docs,
        "total_conflicts": total_conflicts,
        "total_candidates": total_candidates,
        "total_llm_calls": total_llm_calls,
        "truncated": genuinely_inconclusive,
        "inconclusive": genuinely_inconclusive,
        "evaluated_top_k": docs_evaluated > 0,
        "evaluated_count": total_llm_calls,
        "min_evaluated_similarity": min_eval_sim if min_eval_sim < 1.0 else 0.0,
        "genuine_failures": failed_calls,
        "coverage_note": (
            f"Evaluated clauses from {docs_evaluated} candidate document(s) against the target."
            if docs_evaluated > 0 else "No comparable cross-policy clauses were found."
        ),
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


def analyze_clause_vs_corpus(
    source_chunks: List[Dict[str, Any]],
    access_level: int,
    query_text: str = "",
    candidate_retrieval_limit: int = 30,
    minimum_conflict_targets: int = 3,
    maximum_llm_comparisons: int = 65,
    threshold: float = 0.57,
) -> Dict[str, Any]:
    """Compare query-source clauses with accessible corpus candidates."""
    from services.api.search import find_similar_clauses

    sources = []
    seen_sources = set()
    for chunk in source_chunks:
        cid = chunk.get("id")
        if cid and cid not in seen_sources:
            item = dict(chunk)
            item["origin"] = "query_source"
            sources.append(item)
            seen_sources.add(cid)

    pairs, evidence = [], list(sources)
    seen_candidates, seen_pairs = set(), set()
    for source in sources:
        text = source.get("text", "")
        if len(text.strip()) < 30:
            continue
        candidates = find_similar_clauses(
            clause_text=text,
            access_level=access_level,
            exclude_doc_id=source.get("document_id", ""),
            top_k=candidate_retrieval_limit,
            threshold=threshold,
        ) or []
        for candidate in candidates:
            if candidate.get("document_id") == source.get("document_id") or candidate.get("id") == source.get("id"):
                continue
            candidate = dict(candidate)
            candidate["origin"] = "corpus_candidate"
            key = _canonical_pair_key(source, candidate)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            candidate_id = candidate.get("id")
            if candidate_id not in seen_candidates:
                seen_candidates.add(candidate_id)
                evidence.append(candidate)
            pairs.append({"source": source, "candidate": candidate, "similarity": float(candidate.get("similarity", 0.0) or 0.0)})

    MIN_TEXT = 30
    evaluable = [
        p for p in pairs
        if len(p["source"].get("text", "")) >= MIN_TEXT
        and len(p["candidate"].get("text", "")) >= MIN_TEXT
    ]
    evaluable.sort(key=lambda pair: pair["similarity"], reverse=True)
    top_pairs = evaluable[:maximum_llm_comparisons]

    if top_pairs:
        label = f'Cross-document analysis (query: "{query_text}")' if query_text else "Cross-document analysis"
        batch_conflicts = _batch_conflict_check(
            top_pairs, label, question=query_text, conflict_type="type_1",
        )
        if batch_conflicts is None:
            conflicts, checked, failed_calls, llm_calls = [], 0, 1, 1
        else:
            conflicts = []
            for conflict in batch_conflicts:
                key = _canonical_pair_key(conflict["clause_a"], conflict["clause_b"])
                if key not in { _canonical_pair_key(c["clause_a"], c["clause_b"]) for c in conflicts }:
                    conflicts.append(conflict)
            conflicts = sorted(conflicts, key=lambda item: (item.get("confidence", 0.0), item.get("scope_overlap", False), item.get("similarity", 0.0)), reverse=True)
            llm_calls = 1
            checked = len(conflicts)
            failed_calls = 0
    else:
        conflicts, checked, failed_calls, llm_calls = [], 0, 0, 0
    evaluated_top_k = bool(top_pairs)
    successful_calls = llm_calls - failed_calls
    genuinely_inconclusive = failed_calls > 0 and successful_calls == 0
    return {
        "source_clauses": sources,
        "corpus_candidates": [c for c in evidence if c.get("origin") == "corpus_candidate"],
        "evidence": evidence,
        "conflicts": conflicts,
        "total_candidates": len(pairs),
        "checked_candidates": checked,
        "evaluated_count": llm_calls,
        "unchecked_candidates": 0,
        "failed_calls": failed_calls,
        "genuine_failures": failed_calls,
        "llm_calls": llm_calls,
        "evaluated_top_k": evaluated_top_k,
        "coverage_note": (
            f"Evaluated the {len(top_pairs)} most similar cross-policy clause pairs."
            if top_pairs else "No comparable cross-policy clauses were found."
        ),
        "truncated": genuinely_inconclusive,
        "inconclusive": genuinely_inconclusive,
        "minimum_conflict_targets": minimum_conflict_targets,
    }
