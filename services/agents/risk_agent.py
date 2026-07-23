"""
Risk & Compliance Agent

Classifies query outcomes as:
- CLEAR: No compliance issues detected
- CONDITIONAL: Compliance depends on specific conditions
- VIOLATION: Policy/regulation violation detected

Maps to regulations and provides risk assessment.
"""

from typing import Dict, Any, List
from services.agents.tools.neo4j_tools import neo4j_query
from services.agents.tools.llm_tools import llm_generate, PHI_MODEL


def _get_regulations_for_chunks(chunks: List[Dict[str, Any]]) -> List[str]:
    """Query Neo4j for regulations that apply to the retrieved clauses."""
    regulations = []
    for chunk in chunks:
        doc_id = chunk.get("document_id", "")
        if not doc_id:
            continue
        result = neo4j_query(
            cypher="""
                MATCH (p:Policy {id: $doc_id})-[:MAPS_TO]->(reg:Regulation)
                RETURN DISTINCT reg.name AS name
            """,
            params={"doc_id": doc_id}
        )
        if result.success:
            regulations.extend([r["name"] for r in result.data])
    return list(set(regulations))


def _get_clause_obligations(chunks: List[Dict[str, Any]]) -> Dict[str, str]:
    """Query Neo4j for obligation levels on retrieved clauses."""
    obligations = {}
    for chunk in chunks:
        cid = chunk.get("id", "")
        if not cid:
            continue
        result = neo4j_query(
            cypher="""
                MATCH (c:Clause {id: $cid})
                RETURN c.obligation AS obligation
            """,
            params={"cid": cid}
        )
        if result.success and result.data and result.data[0].get("obligation"):
            obligations[cid] = result.data[0]["obligation"]
    return obligations


def _classify_by_llm(question: str, chunks: List[Dict[str, Any]]) -> str:
    context_text = "\n\n".join([
        f"[Clause {c.get('clause_ref', 'N/A')}]: {(c.get('text') or '')[:500]}"
        for c in chunks[:3]
    ])
    prompt = f"""Given the user's question and policy context below, classify compliance risk as one word: "clear", "conditional", or "violation".

- clear: No compliance issues, following policy
- conditional: Compliance depends on meeting conditions
- violation: Policy or regulation is being breached

Question: {question}

Context:
{context_text}

Answer with one word:"""

    result = llm_generate(
        model=PHI_MODEL,
        system_prompt="You are a compliance classifier. Output only one word: clear, conditional, or violation.",
        user_message=prompt,
        temperature=0.0,
        max_tokens=10,
        timeout=15.0
    )

    if result.success:
        verdict = (result.data or "").strip().lower()
        if verdict in ("clear", "conditional", "violation"):
            return verdict
    return "clear"


def assess_risk(
    question: str,
    chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Assess compliance risk for a query.

    Returns:
        {
            "verdict": "clear | conditional | violation",
            "regulations": ["GDPR", "ISO 27001"],
            "obligations": {"clause_id": "mandatory"},
            "risk_level": "low | medium | high",
            "recommendations": ["..."]
        }
    """
    if not chunks:
        return {
            "verdict": "abstained",
            "regulations": [],
            "obligations": {},
            "risk_level": "unknown",
            "recommendations": ["No relevant policy clauses found"],
        }

    # Get regulations from Neo4j
    regulations = _get_regulations_for_chunks(chunks)

    # Get obligations from Neo4j
    obligations = _get_clause_obligations(chunks)

    # Classify verdict
    verdict = _classify_by_llm(question, chunks)

    # Determine risk level
    mandatory_count = sum(1 for v in obligations.values() if v == "mandatory")
    if verdict == "violation":
        risk_level = "high"
    elif verdict == "conditional" or mandatory_count > 2:
        risk_level = "medium"
    else:
        risk_level = "low"

    # Generate recommendations
    recommendations = []
    if verdict == "violation":
        recommendations.append("Review the identified violation against applicable regulations")
        if regulations:
            recommendations.append(f"Ensure compliance with: {', '.join(regulations)}")
    elif verdict == "conditional":
        recommendations.append("Verify all conditions are met before proceeding")
        recommendations.append("Check approval requirements in the knowledge graph")
    else:
        recommendations.append("No immediate compliance concerns detected")

    if mandatory_count > 0:
        recommendations.append(f"{mandatory_count} mandatory obligation(s) apply to retrieved clauses")

    return {
        "verdict": verdict,
        "regulations": regulations,
        "obligations": obligations,
        "risk_level": risk_level,
        "recommendations": recommendations,
    }
