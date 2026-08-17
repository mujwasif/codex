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
import re
from typing import Dict, Any, List, Optional, Tuple
from services.agents.tools.neo4j_tools import neo4j_query
from services.agents.tools.llm_tools import llm_generate, QWEN3_8B_MODEL


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
A conflict exists ONLY if the same role, process, or asset is subject to two different, incompatible requirements.
Compare the subject, role, action, conditions, exceptions, thresholds, time periods, and obligation level.
Rules for different roles (e.g. Admin vs Employee) are complementary, NOT conflicting.
Similar wording alone is not a conflict. If the available text is insufficient, return conflict=false and explain what is missing.

{_clause_prompt_context(text_a, "CLAUSE A")}

{_clause_prompt_context(text_b, "CLAUSE B")}

Respond with JSON only:
{{"status":"confirmed_conflict|possible_conflict|no_conflict|complementary_scope|insufficient_context|superseded", "subject":"topic", "scope_overlap":true, "difference_type":"threshold|modality|scope|time|negation|requirement", "source_requirement":"requirement from clause A", "candidate_requirement":"requirement from clause B", "confidence":0.0, "reason":"brief evidence-backed explanation", "missing_context":[]}}"""

    result = llm_generate(
        model=QWEN3_8B_MODEL,
        system_prompt=(
            "You are a policy conflict analyst. "
            "Respond ONLY with a JSON object. No markdown fences, no extra text."
        ),
        user_message=prompt,
        temperature=0.0,
        max_tokens=120,
        timeout=15.0,
        enable_thinking=False,
    )

    if not result.success:
        return {"conflict": False, "confidence": 0.0, "reason": "LLM call failed", "subject": ""}

    raw = result.data.strip()
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
# Candidate pair generation (cross-document)
# ---------------------------------------------------------------------------

def _build_cross_doc_pairs(
    chunks: List[Dict[str, Any]],
    source_chunks: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Build cross-document candidate pairs from a list of chunks.

    If source_chunks is provided, only pairs where one chunk is from
    source_chunks and the other is from a different document are returned.
    Otherwise, all cross-document pairs from chunks are returned.
    """
    from services.api.search import find_similar_clauses

    pairs = []
    seen = set()
    anchor_set = source_chunks if source_chunks else chunks

    for src in anchor_set:
        src_id = src.get("id", "")
        src_doc = src.get("document_id", "")
        src_text = src.get("text", "")
        if not src_text or len(src_text) < 30:
            continue

        similar = find_similar_clauses(
            clause_text=src_text,
            access_level=3,
            exclude_doc_id=src_doc,
            top_k=5,
            threshold=0.5,
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
    return pairs


# ---------------------------------------------------------------------------
# Core conflict evaluation
# ---------------------------------------------------------------------------

def _evaluate_pairs(
    pairs: List[Dict[str, Any]],
    access_level: int,
    max_llm_calls: int = 5,
) -> Dict[str, Any]:
    """
    Evaluate candidate pairs for conflicts. Returns structured result.

    For each pair:
      - Neo4j check
      - Version check
      - LLM check (up to max_llm_calls)
    """
    all_conflicts = []
    llm_calls_used = 0
    unchecked = 0

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

        if llm_calls_used >= max_llm_calls:
            unchecked += 1
            continue

        text_a = src.get("text", "")[:1200]
        text_b = cand.get("text", "")[:1200]
        if len(text_a) < 50 or len(text_b) < 50:
            unchecked += 1
            continue

        llm_result = _llm_conflict_check(text_a, text_b)
        llm_calls_used += 1

        if llm_result.get("conflict"):
            all_conflicts.append(_build_conflict_record(
                src, cand, pair["similarity"],
                {**llm_result, "source": "llm"},
            ))

    return {
        "conflicts": all_conflicts,
        "llm_calls_used": llm_calls_used,
        "unchecked": unchecked,
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
    threshold: float = 0.7,
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
    similarity_threshold: float = 0.7,
    max_pairs: int = 100,
    max_llm_calls: int = 15,
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
    truncated = False

    llm_budget_remaining = max_llm_calls

    for i in range(len(doc_ids)):
        for j in range(i + 1, len(doc_ids)):
            doc_a_id, doc_b_id = doc_ids[i], doc_ids[j]
            chunks_a = chunks_by_doc.get(doc_a_id, [])
            chunks_b = chunks_by_doc.get(doc_b_id, [])

            title_a = chunks_a[0].get("title", "") if chunks_a else ""
            title_b = chunks_b[0].get("title", "") if chunks_b else ""

            pairs = _build_direct_pairs(chunks_a, chunks_b, similarity_threshold, max_pairs)
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

            eval_result = _evaluate_pairs(pairs, access_level, max_llm_calls=llm_budget_remaining)
            calls_used = eval_result["llm_calls_used"]
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
        "truncated": truncated,
    }


def _build_direct_pairs(
    chunks_a: List[Dict],
    chunks_b: List[Dict],
    threshold: float,
    max_pairs: int,
) -> List[Dict[str, Any]]:
    """Build all cross-document candidate pairs between two chunk lists."""
    from services.api.search import find_similar_clauses

    pairs = []
    seen = set()

    for src in chunks_a:
        src_id = src.get("id", "")
        src_text = src.get("text", "")
        if not src_text or len(src_text) < 30:
            continue

        similar = find_similar_clauses(
            clause_text=src_text,
            access_level=3,
            exclude_doc_id=src.get("document_id", ""),
            top_k=5,
            threshold=threshold,
        )

        for sim_chunk in similar:
            if sim_chunk.get("document_id") != chunks_b[0].get("document_id", ""):
                continue
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
    similarity_threshold: float = 0.5,
    max_pairs: int = 100,
    max_llm_calls: int = 15,
) -> Dict[str, Any]:
    """
    Find which documents conflict with the target document.

    For each chunk in the target document, find similar clauses across all
    OTHER documents, group by document, then evaluate for conflicts.

    Returns:
        {
            "conflicting_documents": [{
                "document_id": ..., "document_title": ...,
                "conflicts": [...], "unchecked_candidate_count": ...,
            }],
            "total_conflicts": int,
            "total_llm_calls": int,
            "truncated": bool,
        }
    """
    from services.api.search import find_similar_clauses

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

    conflicting_docs = []
    total_conflicts = 0
    total_llm_calls = 0
    llm_budget = max_llm_calls
    truncated = False

    doc_titles = {}
    for chunks_list in candidates_by_doc.values():
        for p in chunks_list:
            cand = p["candidate"]
            doc_titles[cand.get("document_id", "")] = cand.get("title", "")

    for doc_id, doc_pairs in candidates_by_doc.items():
        doc_pairs.sort(key=lambda x: x["similarity"], reverse=True)
        limited_pairs = doc_pairs[:max_pairs]

        if llm_budget <= 0:
            truncated = True
            conflicting_docs.append({
                "document_id": doc_id,
                "document_title": doc_titles.get(doc_id, ""),
                "conflicts": [],
                "unchecked_candidate_count": len(limited_pairs),
                "total_candidate_count": len(limited_pairs),
            })
            continue

        eval_result = _evaluate_pairs(limited_pairs, access_level, max_llm_calls=llm_budget)
        calls_used = eval_result["llm_calls_used"]
        llm_budget -= calls_used
        total_llm_calls += calls_used
        total_conflicts += len(eval_result["conflicts"])

        if eval_result["unchecked"] > 0:
            truncated = True

        conflicting_docs.append({
            "document_id": doc_id,
            "document_title": doc_titles.get(doc_id, ""),
            "conflicts": eval_result["conflicts"],
            "unchecked_candidate_count": eval_result["unchecked"],
            "total_candidate_count": len(limited_pairs),
        })

    conflicting_docs.sort(key=lambda x: len(x.get("conflicts", [])), reverse=True)

    return {
        "conflicting_documents": conflicting_docs,
        "total_conflicts": total_conflicts,
        "total_llm_calls": total_llm_calls,
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
    3. LLM-based pairwise conflict detection (top 5 pairs only)
    """
    all_conflicts = []

    chunk_ids = [c.get("id", "") for c in chunks if c.get("id")]
    neo4j_conflicts = _check_neo4j_conflicts(chunk_ids)
    all_conflicts.extend(neo4j_conflicts)

    version_conflicts = _detect_version_conflicts(chunks)
    all_conflicts.extend(version_conflicts)

    # Rank cross-document pairs by retrieval similarity instead of comparing
    # only the first few list positions. This preserves the call budget while
    # giving the semantic checker the most relevant candidate pairs.
    candidate_pairs = []
    for i, a in enumerate(chunks):
        for b in chunks[i + 1:]:
            if a.get("document_id") == b.get("document_id"):
                continue
            similarity = max(
                float(a.get("similarity", 0.0) or 0.0),
                float(b.get("similarity", 0.0) or 0.0),
            )
            candidate_pairs.append((similarity, a, b))

    llm_checked = 0
    for similarity, a, b in sorted(candidate_pairs, reverse=True, key=lambda item: item[0])[:5]:
        text_a = a.get("text", "")[:1200]
        text_b = b.get("text", "")[:1200]
        if len(text_a) < 50 or len(text_b) < 50:
            continue
        # Keep the legacy entry point compatible with callers that provide a
        # text-only checker; the dedicated Clause-vs-Corpus pipeline passes
        # full dictionaries and metadata.
        llm_result = _llm_conflict_check(a.get("text", ""), b.get("text", ""))
        llm_checked += 1
        if llm_result.get("conflict"):
            all_conflicts.append(_build_conflict_record(
                a, b, similarity, {**llm_result, "source": "llm"},
            ))

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
    if roles_a and roles_b and not (roles_a & roles_b or "all employees" in roles_a or "all employees" in roles_b):
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
    candidate_retrieval_limit: int = 20,
    minimum_conflict_targets: int = 3,
    maximum_llm_comparisons: int = 15,
    threshold: float = 0.5,
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

    pairs.sort(key=lambda pair: pair["similarity"], reverse=True)
    conflicts, checked, unchecked, llm_calls = [], 0, 0, 0
    for pair in pairs:
        if llm_calls >= maximum_llm_comparisons:
            unchecked += 1
            continue
        source, candidate = pair["source"], pair["candidate"]
        if len(source.get("text", "")) < 30 or len(candidate.get("text", "")) < 30:
            unchecked += 1
            continue
        result = _validate_semantic_result(_llm_conflict_check(source, candidate), source, candidate)
        llm_calls += 1
        checked += 1
        if result["status"] in {"confirmed_conflict", "possible_conflict", "superseded"}:
            conflicts.append(_build_semantic_conflict_record(source, candidate, pair["similarity"], result))

    unique = {}
    for conflict in conflicts:
        key = _canonical_pair_key(conflict["clause_a"], conflict["clause_b"])
        previous = unique.get(key)
        if previous is None or conflict.get("confidence", 0.0) > previous.get("confidence", 0.0):
            unique[key] = conflict
    conflicts = sorted(unique.values(), key=lambda item: (item.get("confidence", 0.0), item.get("scope_overlap", False), item.get("similarity", 0.0)), reverse=True)
    return {
        "source_clauses": sources,
        "corpus_candidates": [c for c in evidence if c.get("origin") == "corpus_candidate"],
        "evidence": evidence,
        "conflicts": conflicts,
        "total_candidates": len(pairs),
        "checked_candidates": checked,
        "unchecked_candidates": unchecked,
        "llm_calls": llm_calls,
        "truncated": unchecked > 0,
        "inconclusive": unchecked > 0,
        "minimum_conflict_targets": minimum_conflict_targets,
    }
