import re
import json
import requests
from typing import List, Optional

def detect_clauses_llm(text: str, llama_url: str = "http://localhost:8080/v1/chat/completions") -> List[str]:
    """
    Use DeepSeek-R1 to split section into individual clauses.
    
    Prompt ensures:
    1. Each clause is a single requirement/prohibition
    2. No content is lost
    3. Output is valid JSON array
    
    Args:
        text: Section content to split
        llama_url: llama.cpp server URL
        
    Returns:
        List of clause strings
    """
    prompt = f"""Split this policy section into individual rules/requirements.

RULES:
1. Each clause must be a single requirement or prohibition
2. Keep all original wording - do not paraphrase
3. If the text is already a single rule, return it as-is in a JSON array
4. Return ONLY a JSON array of strings, no other text

TEXT:
{text}

OUTPUT (JSON array):"""
    
    payload = {
        "model": "Phi-4-mini-instruct-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": "You are a policy document parser. Output ONLY valid JSON arrays."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
        "stream": False
    }
    
    try:
        response = requests.post(llama_url, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        content = result['choices'][0]['message']['content'].strip()
        
        # Parse JSON array from response
        # Handle cases where LLM wraps in ```json ... ```
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            clauses = json.loads(json_match.group())
            if isinstance(clauses, list) and len(clauses) > 0:
                # Validate each clause is a string
                validated = []
                for c in clauses:
                    if isinstance(c, str) and c.strip():
                        validated.append(c.strip())
                if validated:
                    return validated
    except json.JSONDecodeError as e:
        print(f"  LLM returned invalid JSON: {e}")
    except requests.exceptions.RequestException as e:
        print(f"  LLM connection failed: {e}")
    except Exception as e:
        print(f"  LLM clause detection failed: {e}")
    
    # Fallback: return original text as single clause
    return [text]


def detect_clauses(
    text: str, 
    llama_url: Optional[str] = "http://localhost:8080/v1/chat/completions",
    use_llm: bool = True
) -> List[str]:
    """
    Detect clauses using LLM only.
    
    Logic:
    1. Try LLM for clause detection
    2. If LLM fails → return original text as single clause
    
    Args:
        text: Section content to split
        llama_url: llama.cpp server URL
        use_llm: Whether to use LLM
        
    Returns:
        List of clause strings
    """
    if not text or not text.strip():
        return []
    
    # Step 1: Try LLM
    if use_llm and llama_url:
        llm_clauses = detect_clauses_llm(text, llama_url)
        if llm_clauses:
            return llm_clauses
    
    # Step 2: Return original text as single clause
    return [text]


def get_clause_stats(clauses: List[str]) -> dict:
    """
    Get statistics about detected clauses.
    
    Args:
        clauses: List of clause strings
        
    Returns:
        Dictionary with statistics
    """
    if not clauses:
        return {
            'total_clauses': 0,
            'avg_tokens': 0,
            'min_tokens': 0,
            'max_tokens': 0,
            'total_tokens': 0
        }
    
    token_counts = [len(c.split()) for c in clauses]
    
    return {
        'total_clauses': len(clauses),
        'avg_tokens': sum(token_counts) / len(token_counts),
        'min_tokens': min(token_counts),
        'max_tokens': max(token_counts),
        'total_tokens': sum(token_counts)
    }
