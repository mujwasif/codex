import re
import json
from typing import List, Optional
from services.agents.tools.llm_tools import llm_generate, QWEN3_4B_MODEL
from packages.shared.chunk_filter import is_low_info, PROMPT_LEAK_MARKERS

# Chain-of-thought workflow appended to the clause-detection prompt.
# The LLM reasons about rule boundaries first, then emits a JSON array inside a
# fenced block. _extract_json_array is tolerant of the reasoning prose.
_COT_RULE = (
    "\n\nCHAIN-OF-THOUGHT WORKFLOW (reason step-by-step first, then emit the JSON array):\n"
    "Step 1 - Read the section and list every distinct requirement, prohibition, or permission.\n"
    "Step 2 - Determine each rule's exact boundaries; keep its original wording verbatim - do not paraphrase.\n"
    "Step 3 - Verify no rule was missed, merged, or reworded. If the text is already a single rule, keep it as one clause.\n"
    "Step 4 - Output ONLY the final JSON array of strings wrapped in a ```json fenced code block. Do not add any text after the closing fence.\n"
)

def _is_valid_json_array(cand: str) -> bool:
    try:
        data = json.loads(cand)
    except json.JSONDecodeError:
        return False
    return isinstance(data, list) and len(data) > 0


def _array_has_prompt_leak(cand: str) -> bool:
    """True if a candidate array contains leaked prompt / CoT text.

    The LLM occasionally echoes its own instructions (e.g. "Each clause must
    be a single requirement or prohibition") or its chain-of-thought prose
    into the JSON array. Such candidates must be rejected, never accepted.
    """
    lowered = cand.lower()
    return any(marker in lowered for marker in PROMPT_LEAK_MARKERS)


def _repair_truncated_array(cand: str) -> str:
    """Recover an array truncated by max_tokens: cut at the last complete ']'."""
    for i in range(len(cand) - 1, -1, -1):
        if cand[i] == "]":
            repaired = cand[: i + 1]
            if _is_valid_json_array(repaired):
                return repaired
    return ""


def _last_balanced_array(text: str) -> str:
    """Return the last complete JSON array in text, ignoring prose with brackets."""
    n = len(text)
    for i in range(n - 1, -1, -1):
        if text[i] == "]":
            depth = 0
            in_str = False
            esc = False
            for j in range(i, -1, -1):
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                else:
                    if c == '"':
                        in_str = True
                    elif c == "]":
                        depth += 1
                    elif c == "[":
                        depth -= 1
                        if depth == 0:
                            return text[j:i + 1]
    return ""


def _extract_json_array(content: str) -> str:
    """
    Extract the JSON array from an LLM response that may contain CoT reasoning.

    Strategy (in order):
    1. The LAST ```json fenced block (preferred; CoT prose precedes it).
    2. The last balanced JSON array in the raw text (string/escape-aware).
    3. Truncation repair for output cut off by max_tokens.
    4. Legacy greedy regex fallback.
    """
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    fenced = [c.strip() for c in fenced if c.strip()]

    for cand in reversed(fenced):
        if _is_valid_json_array(cand) and not _array_has_prompt_leak(cand):
            return cand
        repaired = _repair_truncated_array(cand)
        if repaired and not _array_has_prompt_leak(repaired):
            return repaired

    balanced = _last_balanced_array(content)
    if balanced:
        if _is_valid_json_array(balanced) and not _array_has_prompt_leak(balanced):
            return balanced
        repaired = _repair_truncated_array(balanced)
        if repaired and not _array_has_prompt_leak(repaired):
            return repaired

    match = re.search(r'\[.*\]', content, re.DOTALL)
    if match and not _array_has_prompt_leak(match.group()):
        return match.group()
    return ""


_RULE_PREFIX_RE = re.compile(r'^\s*(?:rule\s*\d+|[0-9]+)\s*[:.)\-]\s*', re.IGNORECASE)


def _normalize_clause(clause: str) -> str:
    """Strip leading rule-number prefixes (e.g. 'Rule 3:', '3.') and trim whitespace."""
    cleaned = _RULE_PREFIX_RE.sub('', clause)
    return cleaned.strip()


def detect_clauses_llm(text: str, all_sections: list = None, current_index: int = 0) -> List[str]:
    """
    Use Qwen3-4B-Instruct to split a section into individual clauses.

    A chain-of-thought workflow makes the model reason about rule boundaries
    before emitting the JSON array. The parser tolerates CoT prose.

    Args:
        text: Section content to split
        all_sections: Full list of parsed sections (for document context)
        current_index: Index of the current section in all_sections

    Returns:
        List of clause strings
    """
    context_block = ""
    if all_sections and len(all_sections) > 1:
        # Sliding window: show ±10 sections around current (max 21 total)
        # Prevents token blowup for large docs with 100+ sections
        WINDOW = 5
        start = max(0, current_index - WINDOW)
        end = min(len(all_sections), current_index + WINDOW + 1)
        visible_sections = list(enumerate(all_sections))[start:end]
        total_visible = len(visible_sections)
        total_chars_budget = 30000
        chars_per_section = max(400, total_chars_budget // max(total_visible, 1))

        context_parts = []
        for i, sec in visible_sections:
            heading = " > ".join(sec.get("heading_hierarchy", []))
            sec_text = sec.get("content", "")
            if i == current_index:
                marker = ">>> [CURRENT — split this section into clauses]:"
                sec_text = text
            else:
                marker = ""
                sec_text = sec_text[:chars_per_section]
            label = f"[Section {i + 1}/{len(all_sections)}]"
            context_parts.append(f"{label} {heading}\n{marker}\n{sec_text}")
        context_block = "\n\n".join(context_parts)

    if context_block:
        prompt = f"""Split this policy section into individual rules/requirements.

FULL DOCUMENT CONTEXT (for reference — only split the CURRENT section marked with >>>):
{context_block}

RULES:
1. Each clause must be a single requirement or prohibition
2. Keep all original wording - do not paraphrase
3. If the text is already a single rule, return it as-is in a JSON array
4. Use surrounding sections only to resolve ambiguous pronouns or references
{_COT_RULE}"""
    else:
        prompt = f"""Split this policy section into individual rules/requirements.

RULES:
1. Each clause must be a single requirement or prohibition
2. Keep all original wording - do not paraphrase
3. If the text is already a single rule, return it as-is in a JSON array

TEXT:
{text}
{_COT_RULE}"""

    result = llm_generate(
        model=QWEN3_4B_MODEL,
        system_prompt="You are a policy document parser. Reason step-by-step about rule boundaries, then output a JSON array of clause strings wrapped in a ```json fenced block.",
        user_message=prompt,
        temperature=0.0,
        max_tokens=4096,
        timeout=180.0,
    )

    if not result.success:
        print(f"  LLM connection failed: {result.error}")
        return [text]

    content = result.data.strip()

    extracted = _extract_json_array(content)
    if not extracted:
        return [text]

    try:
        clauses = json.loads(extracted)
        if isinstance(clauses, list) and len(clauses) > 0:
            validated = []
            for c in clauses:
                if isinstance(c, str) and c.strip():
                    cleaned = _normalize_clause(c)
                    if cleaned and not is_low_info("", cleaned):
                        validated.append(cleaned)
            if validated:
                return validated
    except json.JSONDecodeError as e:
        print(f"  LLM returned invalid JSON: {e}")

    # Fallback: return original text as single clause
    return [text]


def detect_clauses(
    text: str, 
    use_llm: bool = True,
    all_sections: list = None,
    current_index: int = 0,
) -> List[str]:
    """
    Detect clauses using LLM.
    
    Logic:
    1. Try LLM for clause detection
    2. If LLM fails → return original text as single clause
    
    Args:
        text: Section content to split
        use_llm: Whether to use LLM
        all_sections: Full list of parsed sections (for document context)
        current_index: Index of the current section in all_sections
        
    Returns:
        List of clause strings
    """
    if not text or not text.strip():
        return []
    
    # Short-circuit: small sections are single rules — skip LLM
    # A section needs ~300+ chars to potentially contain multiple rules
    if len(text.strip()) < 300:
        return [text.strip()]
    
    # Step 1: Try LLM clause detection
    if use_llm:
        llm_clauses = detect_clauses_llm(text, all_sections=all_sections, current_index=current_index)
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
