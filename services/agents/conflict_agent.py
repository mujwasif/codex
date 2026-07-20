"""
Conflict Detector Agent

Cross-checks retrieved clauses for contradictions or version mismatches.
Uses the Neo4j CONFLICTS_WITH relationships when available,
and falls back to LLM-based conflict detection.
"""

from typing import Dict, Any, List
from services.agents.tools.neo4j_tools import neo4j_query
from services.agents.tools.llm_tools import llm_generate, PHI_MODEL


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
            params={"id": cid}
        )
        if result.success:
            for r in result.data:
                conflicts.append({
                    "clause_a": cid,
                    "clause_b": r["other_id"],
                    "ref_b": r["other_ref"],
                    "source": "neo4j",
                })
    return conflicts


def _llm_conflict_check(text_a: str, text_b: str) -> bool:
    """Use Phi-4-mini to detect if two clauses contradict."""
    prompt = f"""Do these two policy clauses contradict each other? Answer ONLY "yes" or "no".

CLAUSE A: {text_a[:400]}
CLAUSE B: {text_b[:400]}

Answer:"""

    result = llm_generate(
        model=PHI_MODEL,
        system_prompt="Answer only yes or no.",
        user_message=prompt,
        temperature=0.0,
        max_tokens=5,
        timeout=15.0
    )

    if result.success:
        return "yes" in result.data.lower()
    return False


def _detect_version_conflicts(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect version mismatches between clauses from same document."""
    conflicts = []
    by_doc: Dict[str, List[Dict]] = {}

    for chunk in chunks:
        doc_id = chunk.get("document_id", "")
        if doc_id not in by_doc:
            by_doc[doc_id] = []
        by_doc[doc_id].append(chunk)

    for doc_id, doc_chunks in by_doc.items():
        if len(doc_chunks) < 2:
            continue
        for i in range(len(doc_chunks)):
            for j in range(i + 1, len(doc_chunks)):
                a, b = doc_chunks[i], doc_chunks[j]
                ref_a = a.get("clause_ref", "")
                ref_b = b.get("clause_ref", "")
                if ref_a and ref_b and ref_a == ref_b:
                    conflicts.append({
                        "clause_a": a.get("id", ""),
                        "clause_b": b.get("id", ""),
                        "ref": ref_a,
                        "reason": f"Same clause_ref '{ref_a}' from different chunks",
                        "source": "version_check",
                    })

    return conflicts


def detect_conflicts_in_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Detect conflicts across retrieved chunks.

    Strategy:
    1. Check Neo4j CONFLICTS_WITH relationships
    2. Detect version mismatches (same clause_ref from different chunks)
    3. LLM-based pairwise conflict detection (top 5 pairs only)
    """
    all_conflicts = []

    # 1. Neo4j pre-computed conflicts
    chunk_ids = [c.get("id", "") for c in chunks if c.get("id")]
    neo4j_conflicts = _check_neo4j_conflicts(chunk_ids)
    all_conflicts.extend(neo4j_conflicts)

    # 2. Version mismatches
    version_conflicts = _detect_version_conflicts(chunks)
    all_conflicts.extend(version_conflicts)

    # 3. LLM pairwise (limit to top 5 pairs to avoid slowdown)
    llm_checked = 0
    for i in range(min(len(chunks), 5)):
        for j in range(i + 1, min(len(chunks), 6)):
            text_a = chunks[i].get("text", "")[:400]
            text_b = chunks[j].get("text", "")[:400]
            if len(text_a) < 50 or len(text_b) < 50:
                continue
            if _llm_conflict_check(text_a, text_b):
                all_conflicts.append({
                    "clause_a": chunks[i].get("id", ""),
                    "clause_b": chunks[j].get("id", ""),
                    "reason": "LLM detected contradiction",
                    "source": "llm",
                })
            llm_checked += 1
            if llm_checked >= 5:
                break
        if llm_checked >= 5:
            break

    return all_conflicts
