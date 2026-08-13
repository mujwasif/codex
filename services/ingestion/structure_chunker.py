import re
from typing import List, Dict, Optional
from services.ingestion.clause_detector import detect_clauses, get_clause_stats


def chunk_document_clauses(
    sections: List[Dict],
    llama_url: Optional[str] = "http://localhost:8080/v1/chat/completions",
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
        llama_url: llama.cpp server URL for LLM clause detection
        max_clause_tokens: Maximum tokens per clause (default 50)
        overlap_tokens: Number of tokens to overlap between clauses (default 3)
        use_llm: Whether to use LLM for clause detection (default True)
        
    Returns:
        List of clause-level chunks with metadata
    """
    chunks = []
    clause_counter = {}
    prev_tail = ""

    for section in sections:
        if section.get('is_heading', False):
            continue

        section_path = section.get('section_path', '')
        heading_hierarchy = section.get('heading_hierarchy', [])
        content = section.get('content', '')
        page = section.get('page')
        is_table = section.get('is_table', False)

        if not content.strip():
            continue

        clauses = detect_clauses(content, llama_url, use_llm)

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

    return chunks


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
