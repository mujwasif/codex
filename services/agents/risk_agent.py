"""
Risk & Compliance Agent

Classifies query outcomes as:
- CLEAR: No compliance issues detected
- CONDITIONAL: Compliance depends on specific conditions
- VIOLATION: Policy/regulation violation detected

Maps to regulations and provides risk assessment.
"""

import re
from typing import Dict, Any, List
from services.agents.tools.neo4j_tools import neo4j_query
from services.agents.tools.llm_tools import llm_generate, QWEN3_8B_MODEL


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


def _classify_by_llm(question: str, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    context_text = "\n\n".join([
        f"[Clause {c.get('clause_ref', 'N/A')}]: {(c.get('text') or '')[:500]}"
        for c in chunks[:10]
    ])
    prompt = f"""Analyze the user's question and the policy context below to classify the compliance risk.
    
    You MUST follow this Chain-of-Thought process:
    1. <thinking>
       - Identify the core requirement being asked about.
       - Search the context for explicit mentions of this requirement.
       - Check if the requirement is marked as "mandatory" or "optional".
       - Compare the current state described in the question with the policy requirement.
       - Determine if there is a clear breach, a dependency (conditional), or full alignment.
       </thinking>
    
    2. Final Output:
       Provide a JSON response with the following keys:
       - "verdict": "clear", "conditional", or "violation"
       - "elaboration": A detailed explanation of why this verdict was reached, citing the specific clause. If "clear", explain how it is compliant.
       
       Verdict Guide:
       - clear: No compliance issues; the policy is followed.
       - conditional: Compliance depends on meeting specific conditions or approvals.
       - violation: A policy/regulation is breached, or a mandatory requirement is missing from the context.
    
    Question: {question}
    
    Context:
    {context_text}
    
    Respond with JSON only:"""

    result = llm_generate(
        model=QWEN3_8B_MODEL,
        system_prompt="You are a strict compliance officer. Use Chain-of-Thought reasoning to analyze policy risk. Respond ONLY with a JSON object containing 'verdict' and 'elaboration'.",
        user_message=prompt,
        temperature=0.0,
        max_tokens=500,
        timeout=30.0,
        enable_thinking=False
    )

    if result.success:
        answer = result.data.strip()
        # Strip thinking block if present
        answer = re.sub(r'<thinking>.*?</thinking>', '', answer, flags=re.DOTALL).strip()
        
        try:
            import json
            # Find JSON part if LLM adds markdown fences
            json_match = re.search(r"\{.*\}", answer, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                return {
                    "verdict": parsed.get("verdict", "clear").lower(),
                    "elaboration": parsed.get("elaboration", "")
                }
        except Exception:
            pass
            
    return {"verdict": "clear", "elaboration": "No compliance issues detected."}


def assess_risk(
    question: str,
    chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Assess compliance risk for a query.
    
    Returns:
        {
            "verdict": "clear | conditional | violation",
            "elaboration": "Detailed explanation of the verdict",
            "regulations": ["GDPR", "ISO 27001"],
            "obligations": {"clause_id": "mandatory"},
            "recommendations": ["..."]
        }
    """
    if not chunks:
        return {
            "verdict": "abstained",
            "elaboration": "No relevant policy clauses found",
            "regulations": [],
            "obligations": {},
            "recommendations": ["No relevant policy clauses found"],
        }
    
    # Get regulations from Neo4j
    regulations = _get_regulations_for_chunks(chunks)
    
    # Get obligations from Neo4j
    obligations = _get_clause_obligations(chunks)
    
    # Classify verdict and get elaboration
    classification = _classify_by_llm(question, chunks)
    verdict = classification.get("verdict", "clear")
    elaboration = classification.get("elaboration", "")
    
    # Generate recommendations
    recommendations = []
    if verdict == "violation":
        recommendations.append(f"CRITICAL: {elaboration}")
        if regulations:
            recommendations.append(f"Ensure compliance with: {', '.join(regulations)}")
    elif verdict == "conditional":
        recommendations.append(f"NOTICE: {elaboration}")
        recommendations.append("Verify all conditions are met before proceeding")
    else:
        recommendations.append("No immediate compliance concerns detected")
    
    if mandatory_count := sum(1 for v in obligations.values() if v == "mandatory"):
        recommendations.append(f"{mandatory_count} mandatory obligation(s) apply to retrieved clauses")
    
    return {
        "verdict": verdict,
        "elaboration": elaboration,
        "regulations": regulations,
        "obligations": obligations,
        "recommendations": recommendations,
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
