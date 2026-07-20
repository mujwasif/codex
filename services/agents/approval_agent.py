"""
Approval-Matrix Agent

Queries Neo4j knowledge graph to resolve:
- Which role can approve a given process
- What amount threshold applies
- Whether the requester's role has sufficient authority

Uses the Cypher query pattern:
  MATCH (role:Role {name:$role})-[:CAN_APPROVE]->(p:Process {name:$process})
  RETURN role.name, p.name
"""

import re
from typing import Dict, Any, List, Optional
from services.agents.tools.neo4j_tools import neo4j_query


def _extract_process_from_question(question: str) -> Optional[str]:
    """Try to extract a process/action from the question."""
    q = question.lower()

    process_keywords = {
        "purchase": "Purchase",
        "procurement": "Procurement",
        "leave": "Leave",
        "vacation": "Leave",
        "travel": "Travel",
        "travel approval": "Travel",
        "expense": "Expense",
        "reimbursement": "Reimbursement",
        "access": "Access Grant",
        "permission": "Access Grant",
        "hire": "Hiring",
        "recruit": "Hiring",
        "onboard": "Onboarding",
        "offboard": "Offboarding",
        "contract": "Contract",
        "vendor": "Vendor",
        "incident": "Incident Response",
        "change": "Change Management",
        "deployment": "Deployment",
        "release": "Release",
        "data": "Data Handling",
        "security": "Security",
    }

    for kw, process in process_keywords.items():
        if kw in q:
            return process

    return None


def _extract_amount_from_question(question: str) -> Optional[float]:
    """Try to extract a monetary amount from the question."""
    patterns = [
        r'\$\s*([\d,]+(?:\.\d{2})?)',
        r'([\d,]+(?:\.\d{2})?)\s*(?:dollars|usd)',
        r'amount\s+(?:of\s+)?\$?\s*([\d,]+(?:\.\d{2})?)',
        r'(?:over|above|exceed|more than)\s+\$?\s*([\d,]+(?:\.\d{2})?)',
    ]

    for pattern in patterns:
        m = re.search(pattern, question, re.I)
        if m:
            amt_str = m.group(1).replace(",", "")
            try:
                return float(amt_str)
            except ValueError:
                continue

    return None


def resolve_approval(
    question: str,
    chunks: List[Dict[str, Any]],
    user_role: str = "Employee",
) -> Dict[str, Any]:
    """
    Query Neo4j for approval authority.

    Returns:
        {
            "process": "Purchase",
            "matching_roles": ["Manager", "Director"],
            "max_amounts": {"Manager": 10000, "Director": 50000},
            "user_can_approve": False,
            "answer_text": "..."
        }
    """
    process = _extract_process_from_question(question)
    amount = _extract_amount_from_question(question)

    if not process:
        # Try to infer from chunks
        for chunk in chunks[:3]:
            text = (chunk.get("text", "") or "").lower()
            if "approval" in text or "approve" in text:
                process = "Approval"
                break

    if not process:
        return {"error": "Could not determine the process from the question"}

    # Find roles that can approve this process
    result = neo4j_query(
        cypher="""
            MATCH (r:Role)-[:CAN_APPROVE]->(p:Process)
            WHERE toLower(p.name) CONTAINS toLower($process)
            RETURN r.name AS role, p.name AS process
            ORDER BY r.name
        """,
        params={"process": process}
    )

    if not result.success:
        return {"error": f"Neo4j query failed: {result.error}"}

    records = result.data
    matching_roles = [r["role"] for r in records]
    matching_processes = list(set(r["process"] for r in records))

    # Check if user's role can approve
    user_can_approve = user_role in matching_roles or user_role.title() in matching_roles

    # Build answer
    answer_parts = []
    if matching_roles:
        answer_parts.append(
            f"Approval authority for '{process}': {', '.join(matching_roles)}"
        )
    else:
        answer_parts.append(f"No specific approval authority found for '{process}' in the knowledge graph.")

    if amount:
        answer_parts.append(f"Requested amount: ${amount:,.2f}")

    return {
        "process": process,
        "matching_roles": matching_roles,
        "matching_processes": matching_processes,
        "user_can_approve": user_can_approve,
        "amount": amount,
        "answer_text": "; ".join(answer_parts),
    }
