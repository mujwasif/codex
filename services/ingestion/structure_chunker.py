import json
import re
from typing import List, Dict, Optional
from services.ingestion.clause_detector import detect_clauses, get_clause_stats


from packages.shared.config import MIN_CLAUSE_TOKENS


def _batch_detect_clauses(
    sections_needing_llm: list,
    all_sections: list,
) -> dict:
    """Send all LLM-needy sections in ONE API call. Returns {index: [clauses]}."""
    from services.agents.tools.llm_tools import llm_generate, INGESTION_MODEL

    if not sections_needing_llm:
        return {}

    numbered_parts = []
    for idx, sec in sections_needing_llm:
        heading = " > ".join(sec.get("heading_hierarchy", []))
        content = sec.get("content", "")
        numbered_parts.append(f"[Section {idx + 1}] {heading}\n{content}")

    batch_text = "\n\n---\n\n".join(numbered_parts)

    prompt = f"""Split each numbered section below into individual rules/requirements.

RULES:
1. Each clause must be a single requirement or prohibition
2. Keep all original wording — do not paraphrase
3. If a section is already a single rule, return it as-is
4. Use the section heading to understand context

Return a JSON object mapping section numbers to clause arrays:
{{"1": ["rule a", "rule b"], "5": ["rule c"]}}

SECTIONS:
{batch_text}
"""

    result = llm_generate(
        model=INGESTION_MODEL,
        system_prompt="You are a policy document parser. Split each numbered section into rules. Output a JSON object mapping section numbers to arrays of clause strings in a ```json fenced block.",
        user_message=prompt,
        temperature=0.0,
        max_tokens=4096,
        timeout=180.0,
    )

    if not result.success:
        return {}

    content = result.data.strip()
    try:
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
        json_str = fenced[-1].strip() if fenced else content
        parsed = json.loads(json_str)
        out = {}
        for k, v in parsed.items():
            if isinstance(v, list):
                out[int(k) - 1] = [c for c in v if isinstance(c, str) and c.strip()]
        return out
    except (json.JSONDecodeError, ValueError, KeyError):
        return {}


def chunk_document_clauses(
    sections: List[Dict],
    max_clause_tokens: int = 50,
    overlap_tokens: int = 3,
    use_llm: bool = True
) -> List[Dict]:
    """
    Chunk document into clause-level units with overlapping.
    
    Each clause becomes a separate chunk with:
    - Precise clause_ref (e.g., "5.1 Policy > Network Access > Rule 3")
    - Full heading hierarchy context
    - Overlapping text from adjacent clauses for context continuity
    
    Args:
        sections: List of sections from doc_parser
        max_clause_tokens: Maximum tokens per clause (default 50)
        overlap_tokens: Number of tokens to overlap between clauses (default 3)
        use_llm: Whether to use LLM for clause detection (default True)
        
    Returns:
        List of clause-level chunks with metadata
    """
    chunks = []
    clause_counter = {}
    prev_tail = ""

    non_heading_sections = [s for s in sections if not s.get('is_heading', False)]

    # Pre-compute which sections need LLM (>300 chars)
    llm_batch = []
    if use_llm and non_heading_sections:
        for i, sec in enumerate(non_heading_sections):
            content = sec.get("content", "")
            if len(content.strip()) >= 300:
                llm_batch.append((i, sec))

    # ONE LLM call for all sections that need splitting
    llm_results = {}
    if llm_batch:
        llm_results = _batch_detect_clauses(llm_batch, non_heading_sections)

    for idx, section in enumerate(sections):
        if section.get('is_heading', False):
            continue

        section_path = section.get('section_path', '')
        heading_hierarchy = section.get('heading_hierarchy', [])
        content = section.get('content', '')
        page = section.get('page')
        is_table = section.get('is_table', False)

        if not content.strip():
            continue

        # Use batch result if available, else single-section fallback
        current_pos = non_heading_sections.index(section) if section in non_heading_sections else 0
        if current_pos in llm_results and llm_results[current_pos]:
            clauses = llm_results[current_pos]
        else:
            clauses = [content.strip()]

        if section_path not in clause_counter:
            clause_counter[section_path] = 0

        for i, clause_text in enumerate(clauses):
            clause_counter[section_path] += 1
            clause_num = clause_counter[section_path]

            if prev_tail and i > 0:
                clause_text = prev_tail + " " + clause_text

            words = clause_text.split()
            if len(words) > overlap_tokens:
                prev_tail = " ".join(words[-overlap_tokens:])
            else:
                prev_tail = clause_text

            hierarchy_str = ' > '.join(heading_hierarchy) if heading_hierarchy else 'Content'
            clause_ref = f"{section_path} {hierarchy_str} > Rule {clause_num}"

            token_count = len(clause_text.split())

            if token_count > max_clause_tokens:
                sub_clauses = _split_long_clause_with_overlap(
                    clause_text, max_clause_tokens, overlap_tokens
                )
                for sub_text in sub_clauses:
                    clause_counter[section_path] += 1
                    sub_num = clause_counter[section_path]
                    sub_ref = f"{section_path} {hierarchy_str} > Rule {sub_num}"

                    chunks.append({
                        'section_path': section_path,
                        'clause_ref': sub_ref,
                        'heading_hierarchy': heading_hierarchy,
                        'text': sub_text,
                        'token_count': len(sub_text.split()),
                        'page': page,
                        'clause_number': sub_num,
                        'is_table': is_table
                    })
            else:
                chunks.append({
                    'section_path': section_path,
                    'clause_ref': clause_ref,
                    'heading_hierarchy': heading_hierarchy,
                    'text': clause_text,
                    'token_count': token_count,
                    'page': page,
                    'clause_number': clause_num,
                    'is_table': is_table
                })

    return _merge_micro_chunks(chunks, max_clause_tokens, overlap_tokens)


def _merge_micro_chunks(chunks: List[Dict], max_tokens: int, overlap: int) -> List[Dict]:
    """Merge chunks below MIN_CLAUSE_TOKENS into their neighbors."""
    if not chunks:
        return chunks

    merged = []
    buffer = None

    for chunk in chunks:
        token_count = chunk.get('token_count', 0)

        if token_count < MIN_CLAUSE_TOKENS:
            if buffer is None:
                buffer = dict(chunk)
            else:
                buffer['text'] = buffer['text'] + " " + chunk['text']
                buffer['token_count'] = len(buffer['text'].split())
        else:
            if buffer is not None:
                merged_text = buffer['text'] + " " + chunk['text']
                merged_tokens = len(merged_text.split())
                if merged_tokens <= max_tokens:
                    merged.append({
                        'section_path': chunk['section_path'],
                        'clause_ref': chunk['clause_ref'],
                        'heading_hierarchy': chunk['heading_hierarchy'],
                        'text': merged_text,
                        'token_count': merged_tokens,
                        'page': chunk['page'],
                        'clause_number': chunk.get('clause_number', 0),
                        'is_table': chunk.get('is_table', False),
                    })
                else:
                    sub_clauses = _split_long_clause_with_overlap(
                        merged_text, max_tokens, overlap
                    )
                    for sub_text in sub_clauses:
                        merged.append({
                            'section_path': chunk['section_path'],
                            'clause_ref': chunk['clause_ref'],
                            'heading_hierarchy': chunk['heading_hierarchy'],
                            'text': sub_text,
                            'token_count': len(sub_text.split()),
                            'page': chunk['page'],
                            'clause_number': chunk.get('clause_number', 0),
                            'is_table': chunk.get('is_table', False),
                        })
                buffer = None
            else:
                merged.append(chunk)

    if buffer is not None:
        if merged:
            prev = merged[-1]
            prev['text'] = prev['text'] + " " + buffer['text']
            prev['token_count'] = len(prev['text'].split())
        else:
            merged.append(buffer)

    return merged


def _split_long_clause_with_overlap(
    text: str,
    max_tokens: int,
    overlap_tokens: int
) -> List[str]:
    """
    Split a clause that exceeds max_tokens with overlapping.
    
    Each sub-clause includes tail of previous sub-clause for context continuity.
    """
    if not text or not text.strip():
        return []

    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    sub_clauses = []
    current = []
    current_tokens = 0
    prev_tail = ""

    for sentence in sentences:
        sentence_tokens = len(sentence.split())

        if prev_tail:
            sentence_with_overlap = prev_tail + " " + sentence
            sentence_with_overlap_tokens = len(sentence_with_overlap.split())
        else:
            sentence_with_overlap = sentence
            sentence_with_overlap_tokens = sentence_tokens

        if current_tokens + sentence_with_overlap_tokens > max_tokens and current:
            sub_clauses.append(' '.join(current))

            words = current[-1].split() if current else []
            if len(words) > overlap_tokens:
                prev_tail = " ".join(words[-overlap_tokens:])
            else:
                prev_tail = current[-1] if current else ""

            current = [sentence_with_overlap]
            current_tokens = sentence_with_overlap_tokens
        else:
            current.append(sentence_with_overlap)
            current_tokens += sentence_with_overlap_tokens

    if current:
        sub_clauses.append(' '.join(current))

    return sub_clauses


def get_chunk_stats(chunks: List[Dict]) -> Dict:
    """Get statistics about the chunks."""
    if not chunks:
        return {
            'total_chunks': 0,
            'avg_chunk_size': 0,
            'min_chunk_size': 0,
            'max_chunk_size': 0,
            'total_tokens': 0,
            'unique_sections': 0,
            'avg_tokens': 0,
            'min_tokens': 0,
            'max_tokens': 0
        }

    sizes = [len(c['text']) for c in chunks]
    tokens = [c.get('token_count', 0) for c in chunks]
    sections = set(c['section_path'] for c in chunks)

    return {
        'total_chunks': len(chunks),
        'avg_chunk_size': sum(sizes) / len(sizes),
        'min_chunk_size': min(sizes),
        'max_chunk_size': max(sizes),
        'total_tokens': sum(tokens),
        'unique_sections': len(sections),
        'avg_tokens': sum(tokens) / len(tokens),
        'min_tokens': min(tokens),
        'max_tokens': max(tokens)
    }
