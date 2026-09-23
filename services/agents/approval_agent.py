"""
Approval-Matrix Agent

Resolves approval authority from the Neo4j knowledge graph:
- Which role(s) can approve a given process
- What amount threshold applies
- Whether the requester's role has sufficient authority

Matching strategy (replaces the old hardcoded keyword->name map, which
produced empty results because the graph's Process node names rarely contain
common business words like "purchase"/"travel"):

  1. Pull every (Role)-[:CAN_APPROVE]->(Process) pair from the graph once.
  2. Score each Process name against the user's actual question by token
     overlap + the extracted keyword hint.
  3. Resolve the top-scoring process(es) to their approving roles.

The result includes a clear structured decision (approvable / required_roles /
user_can_approve) so the UI can render it directly.
"""

import re
from collections import Counter
from typing import Dict, Any, List, Optional
from services.agents.tools.neo4j_tools import neo4j_query

# Stop words ignored when scoring process names against the question.
_STOPWORDS = {
    "a", "an", "the", "for", "of", "to", "and", "or", "in", "on", "is", "are",
    "can", "who", "what", "does", "do", "i", "we", "my", "our", "this", "that",
    "with", "by", "from", "over", "above", "more", "than", "need", "needs",
    "required", "approval", "approve", "authorize", "authorised", "authorized",
    "sign", "off", "signed", "per", "request", "requested", "when", "how",
}

# Keyword -> likely process name fragment hints. These are hints only; the
# final match is scored against real Process node names in the graph.
_PROCESS_HINTS = {
    "purchase": "purchase",
    "procurement": "procure",
    "leave": "leave",
    "vacation": "leave",
    "travel": "travel",
    "expense": "expense",
    "reimbursement": "reimburs",
    "access": "access",
    "permission": "access",
    "hire": "hire",
    "recruit": "recruit",
    "onboard": "onboard",
    "offboard": "offboard",
    "contract": "contract",
    "vendor": "vendor",
    "incident": "incident",
    "change": "change",
    "deployment": "deploy",
    "release": "release",
    "data": "data",
    "security": "security",
    "password": "password",
    "policy": "policy",
    "user": "user",
    "patch": "patch",
    "termination": "terminat",
    "supplier": "supplier",
}


def _extract_keywords(question: str) -> List[str]:
    """Return lowercased keyword hints mentioned in the question."""
    q = question.lower()
    hints = set()
    for kw, hint in _PROCESS_HINTS.items():
        if kw in q:
            hints.add(hint)
    # Also add any bare non-stopword tokens (>=3 chars) as weak hints.
    tokens = [t for t in re.split(r"\W+", q) if len(t) >= 3 and t not in _STOPWORDS]
    return list(hints | set(tokens))


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


def _tokenize(text: str) -> List[str]:
    return [
        t for t in re.split(r"\W+", text.lower())
        if t and len(t) >= 3 and t not in _STOPWORDS
    ]


def _score_process(process_name: str, keywords: List[str]) -> float:
    """Score a process name against the question keywords (0.0 - 1.0)."""
    name = process_name.lower()
    name_tokens = _tokenize(name)

    if not name_tokens:
        return 0.0

    score = 0.0
    for kw in keywords:
        # Exact substring in the name is a strong signal.
        if kw in name:
            score += 2.0
        elif any(kw in t for t in name_tokens):
            score += 1.5
        elif kw in name_tokens:
            score += 1.0

    if score > 0:
        # Normalize by number of name tokens so short generic names
        # ("Subject", "To", "Type of change") don't win spuriously.
        return score / (1.0 + 0.25 * len(name_tokens))
    return 0.0


def _all_approval_pairs() -> List[Dict[str, str]]:
    """Fetch every (Role)-[:CAN_APPROVE]->(Process) pair once."""
    result = neo4j_query(cypher="""
        MATCH (r:Role)-[:CAN_APPROVE]->(p:Process)
        RETURN r.name AS role, p.name AS process
    """)
    if not result.success:
        return []
    return result.data


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
            "matching_processes": ["..."],
            "user_can_approve": False,
            "amount": 10000.0,
            "approvable": True,
            "answer_text": "..."
        }
    """
    amount = _extract_amount_from_question(question)
    keywords = _extract_keywords(question)

    pairs = _all_approval_pairs()
    if not pairs:
        return {"error": "Neo4j approval graph could not be queried"}

    # Score every distinct approvable process.
    best_processes = set(p["process"] for p in pairs)
    ranked = sorted(
        best_processes,
        key=lambda name: _score_process(name, keywords),
        reverse=True,
    )
    ranked = [name for name in ranked if _score_process(name, keywords) > 0]

    # A process must clear a minimum match score to be treated as authoritative.
    # Otherwise an unrelated process (e.g. a generic "Policy" node) could win by
    # token overlap alone and produce a fabricated approval chain.
    MIN_SCORE = 1.0
    if not ranked or _score_process(ranked[0], keywords) < MIN_SCORE:
        return {
            "process": None,
            "matching_roles": [],
            "matching_processes": [],
            "user_can_approve": False,
            "amount": amount,
            "approvable": False,
            "answer_text": (
                "No approval process matching your question was found in the "
                "knowledge graph. The policy corpus does not define an approval "
                "chain for this action."
            ),
        }

    # Use the top matching process (and keep the top few for context).
    top_processes = ranked[:3]
    process = top_processes[0]

    # Roles are mapped to the winner ONLY — merging across the top-N caused
    # spurious roles (e.g. "Project Manager" from a neighbor process) to leak in.
    matching_roles = sorted({
        p["role"] for p in pairs if p["process"] == process
    })

    user_can_approve = user_role in matching_roles or user_role.title() in matching_roles

    # Build answer
    answer_parts = []
    if matching_roles:
        answer_parts.append(
            f"Approval authority for '{process}': {', '.join(matching_roles)} "
            f"[Source: Knowledge Graph — Process: {process}]"
        )
    else:
        answer_parts.append(
            f"No specific approval authority found for '{process}' in the knowledge graph."
        )

    if amount:
        answer_parts.append(f"Requested amount: ${amount:,.2f}")

    return {
        "process": process,
        "matching_roles": matching_roles,
        "matching_processes": top_processes,
        "user_can_approve": user_can_approve,
        "amount": amount,
        "approvable": bool(matching_roles),
        "answer_text": "; ".join(answer_parts),
    }
