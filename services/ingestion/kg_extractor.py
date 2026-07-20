"""
LLM-based entity extraction for knowledge graph.
Uses Phi-4-mini to extract {role, department, process, threshold, regulation, obligation}
from each clause. Works with ANY document type.

Usage:
    from services.ingestion.kg_extractor import extract_entities_from_clause, detect_conflicts
"""

import re
import json
import requests

LLAMA_URL = "http://localhost:8080/v1/chat/completions"


def extract_entities_from_clause(clause_text: str) -> dict:
    """
    Use Phi-4-mini to extract structured entities from a clause.
    Works with ANY document type — not just corporate policies.

    Returns:
        {"role": "Manager", "department": "IT", "process": "Access Grant",
         "threshold": {"amount": 1000000, "currency": "NGN"},
         "regulation": "NDPA", "obligation": "mandatory"}
    """
    prompt = f"""Extract policy entities from this text. Return ONLY valid JSON.

RULES:
- role: any person/role mentioned with decision or approval authority
- department: any team, department, or organizational unit mentioned
- process: what action or thing this clause governs or requires
- threshold: monetary amount or numeric limit if mentioned, else null
- regulation: any law, standard, or regulation referenced (NDPA, ISO 27001, NIST, HIPAA, SOX, GDPR, etc.), else null
- obligation: "mandatory" if must/shall/required, "optional" if should/may/can

TEXT:
{clause_text}

JSON:"""

    payload = {
        "model": "Phi-4-mini-instruct-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": "You are a policy entity extractor. Output ONLY valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 200,
        "stream": False
    }

    try:
        response = requests.post(LLAMA_URL, json=payload, timeout=30)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()

        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass

    return {"role": None, "department": None, "process": None,
            "threshold": None, "regulation": None, "obligation": None}


def detect_conflicts(clause_a_text: str, clause_b_text: str) -> bool:
    """
    Use Phi-4-mini to detect if two clauses contradict each other.
    Returns True if conflict detected.
    """
    prompt = f"""Do these two policy clauses contradict each other? Answer ONLY "yes" or "no".

CLAUSE A: {clause_a_text[:300]}
CLAUSE B: {clause_b_text[:300]}

Answer:"""

    payload = {
        "model": "Phi-4-mini-instruct-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": "Answer only yes or no."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 5,
        "stream": False
    }

    try:
        response = requests.post(LLAMA_URL, json=payload, timeout=15)
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip().lower()
        return "yes" in answer
    except Exception:
        return False
